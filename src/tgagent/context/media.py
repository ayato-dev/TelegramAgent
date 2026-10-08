import logging
import mimetypes
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from tgagent.agent.pricing import whisper_cost
from tgagent.context.pdf import extract_pdf
from tgagent.domain import MediaRef
from tgagent.storage.repos import MediaRepo, UsageRecord, UsageRepo
from tgagent.stt.groq import upload_filename

log = logging.getLogger(__name__)

TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
TEXT_TYPES = {
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/csv",
    "application/sql",
}
# Signed documents (attached CMS/PKCS#7 signature) usually carry a PDF inside.
PDF_TYPES = {
    "application/pdf",
    "application/pkcs7-signature",
    "application/x-pkcs7-signature",
    "application/pkcs7-mime",
    "application/x-pkcs7-mime",
}
SIGNATURE_SUFFIXES = (".sig", ".p7s", ".p7m")
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".log",
    ".sql",
}


class FileSource(Protocol):
    async def download(self, file_id: str) -> bytes: ...


class FileStore(Protocol):
    async def upload(self, filename: str, data: bytes, mime_type: str) -> str: ...


class SpeechToText(Protocol):
    async def transcribe(self, data: bytes, filename: str) -> str: ...


@dataclass(frozen=True, slots=True)
class MediaPart:
    body: str | None
    blocks: list[dict[str, Any]] = field(default_factory=list)


def _file_source(file_id: str) -> dict[str, str]:
    return {"type": "file", "file_id": file_id}


class UnreadableFileError(Exception):
    """The file's bytes are not what its name and type claim."""


def _is_pdf(mime: str, name: str) -> bool:
    lowered = name.lower()
    return mime in PDF_TYPES or lowered.endswith(".pdf") or lowered.endswith(SIGNATURE_SUFFIXES)


def _is_text(mime: str, name: str) -> bool:
    suffix = name[name.rfind(".") :].lower() if "." in name else ""
    return mime.startswith("text/") or mime in TEXT_TYPES or suffix in TEXT_EXTENSIONS


class MediaService:
    """Telegram media → Claude inputs: Files API uploads and Whisper transcripts, both cached."""

    def __init__(
        self,
        source: FileSource,
        store: FileStore,
        stt: SpeechToText,
        cache: MediaRepo,
        usage: UsageRepo,
        *,
        whisper_model: str,
        whisper_paid: bool,
    ) -> None:
        self._source = source
        self._store = store
        self._stt = stt
        self._cache = cache
        self._usage = usage
        self._whisper_model = whisper_model
        self._whisper_paid = whisper_paid

    async def describe(
        self, media: MediaRef, *, code_enabled: bool, user_id: int | None, chat_id: int
    ) -> MediaPart:
        match media.kind:
            case "photo":
                return await self._image(media, "photo.jpg", "image/jpeg")
            case "voice" | "video_note" | "audio" | "video":
                return MediaPart(await self.transcript(media, user_id=user_id, chat_id=chat_id))
            case "document":
                return await self._document(media, code_enabled)
            case _:
                return MediaPart(None)

    async def transcript(self, media: MediaRef, *, user_id: int | None, chat_id: int) -> str:
        cached = await self._cache.get(media.file_unique_id)
        if cached and cached.transcript is not None:
            return cached.transcript
        if (media.file_size or 0) > TELEGRAM_DOWNLOAD_LIMIT:
            return "[не удалось расшифровать: файл больше 20 МБ]"
        started = time.monotonic()
        try:
            data = await self._source.download(media.file_id)
            text = await self._stt.transcribe(data, upload_filename(media)) or "[речь не распознана]"
        except Exception:
            log.warning("transcription failed for %s", media.file_unique_id, exc_info=True)
            return "[не удалось расшифровать]"
        log.info(
            "whisper %s: %ss audio in %.2fs", media.file_unique_id, media.duration, time.monotonic() - started
        )
        await self._cache.save_transcript(media.file_unique_id, text)
        seconds = float(media.duration or 0)
        record = UsageRecord(user_id, chat_id, "stt", self._whisper_model, audio_seconds=seconds)
        cost = whisper_cost(seconds, self._whisper_model) if self._whisper_paid else Decimal(0)
        await self._usage.add(record.with_cost(cost))
        return text

    async def _document(self, media: MediaRef, code_enabled: bool) -> MediaPart:
        name = media.file_name or "file"
        mime = media.mime_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        if (media.file_size or 0) > TELEGRAM_DOWNLOAD_LIMIT:
            return MediaPart("[файл больше 20 МБ — открыть его не получится]")
        if mime in IMAGE_TYPES:
            return await self._image(media, name, mime)
        if _is_pdf(mime, name):
            try:
                file_id = await self._upload(media, name, "application/pdf", prepare=extract_pdf)
            except UnreadableFileError:
                return MediaPart("[не удалось прочитать файл: внутри не PDF]")
            if file_id is None:
                return MediaPart("[не удалось загрузить файл]")
            return MediaPart(None, [{"type": "document", "source": _file_source(file_id), "title": name}])
        if _is_text(mime, name):
            file_id = await self._upload(media, name, "text/plain")
            if file_id is None:
                return MediaPart("[не удалось загрузить файл]")
            return MediaPart(None, [{"type": "document", "source": _file_source(file_id), "title": name}])
        if not code_enabled:
            return MediaPart("[формат файла не поддерживается]")
        file_id = await self._upload(media, name, mime)
        if file_id is None:
            return MediaPart("[не удалось загрузить файл]")
        return MediaPart(None, [{"type": "container_upload", "file_id": file_id}])

    async def _image(self, media: MediaRef, name: str, mime: str) -> MediaPart:
        file_id = await self._upload(media, name, mime)
        if file_id is None:
            return MediaPart("[не удалось загрузить изображение]")
        return MediaPart(None, [{"type": "image", "source": _file_source(file_id)}])

    async def _upload(
        self,
        media: MediaRef,
        name: str,
        mime: str,
        *,
        prepare: Callable[[bytes], bytes | None] | None = None,
    ) -> str | None:
        """Files API id for the media (cached); ``prepare`` may convert the bytes or reject them."""
        cached = await self._cache.get(media.file_unique_id)
        if cached and cached.anthropic_file_id:
            return cached.anthropic_file_id
        try:
            data = await self._source.download(media.file_id)
        except Exception:
            log.warning("download failed for %s", media.file_unique_id, exc_info=True)
            return None
        if prepare is not None:
            prepared = prepare(data)
            if prepared is None:
                raise UnreadableFileError(media.file_unique_id)
            data = prepared
        try:
            file_id = await self._store.upload(name, data, mime)
        except Exception:
            log.warning("upload failed for %s", media.file_unique_id, exc_info=True)
            return None
        await self._cache.save_file(media.file_unique_id, file_id)
        return file_id

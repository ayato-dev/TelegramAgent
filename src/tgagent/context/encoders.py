"""Telegram media in the native input format of each non-Anthropic provider.

Claude gets Files API references from MediaService.describe. The others get, depending on the
model: inline bytes (Gemini), OpenAI file ids (Responses API), data URLs (Chat Completions)
or plain text — PDF text, inlined text files and Whisper transcripts — for what they cannot read.
"""

import base64
import logging
import re
from collections.abc import Callable
from html import escape
from typing import Any

from tgagent.context.media import (
    IMAGE_TYPES,
    TOO_BIG_TEXT,
    FileStore,
    MediaPart,
    MediaService,
    UnreadableFileError,
    document_type,
    is_pdf,
    is_text,
    too_big,
)
from tgagent.context.pdf import extract_pdf, pdf_text
from tgagent.domain import MediaRef
from tgagent.storage.repos import Content, MediaRepo

log = logging.getLogger(__name__)

AUDIO_KINDS = ("voice", "video_note", "audio", "video")
DEFAULT_AUDIO_TYPES = {
    "voice": "audio/ogg",
    "video_note": "video/mp4",
    "audio": "audio/mpeg",
    "video": "video/mp4",
}
# Gemini takes at most 20 MB inline per request; bigger recordings fall back to a transcript.
GEMINI_INLINE_LIMIT = 10 * 1024 * 1024
MAX_YOUTUBE_LINKS = 3
YOUTUBE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^\s<>\"]*&)?v=|shorts/|live/|embed/)|youtu\.be/)([\w-]{11})"
)

UNREADABLE = "[не удалось загрузить файл]"
NOT_PDF = "[не удалось прочитать файл: внутри не PDF]"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…[обрезано]"


def _document(name: str, text: str) -> str:
    return f'<document name="{escape(name)}">\n{text}\n</document>'


class InlineEncoder:
    """Shared dispatch; by default images are described, PDFs read as text, audio transcribed."""

    def __init__(self, media: MediaService, *, text_limit: int) -> None:
        self._media = media
        self._text_limit = text_limit

    async def describe(
        self, media: MediaRef, *, code_enabled: bool, user_id: int | None, chat_id: int
    ) -> MediaPart:
        if media.kind == "photo":
            return await self.image(media, "photo.jpg", "image/jpeg")
        if media.kind in AUDIO_KINDS:
            return await self.audio(media, user_id=user_id, chat_id=chat_id)
        if media.kind == "document":
            return await self._document(media)
        return MediaPart(None)

    async def _document(self, media: MediaRef) -> MediaPart:
        name, mime = document_type(media)
        if too_big(media):
            return MediaPart(TOO_BIG_TEXT)
        if mime in IMAGE_TYPES:
            return await self.image(media, name, mime)
        if is_pdf(mime, name):
            return await self.pdf(media, name)
        if is_text(mime, name):
            data = await self._media.fetch(media)
            if data is None:
                return MediaPart(UNREADABLE)
            return MediaPart(_document(name, _clip(data.decode(errors="replace"), self._text_limit)))
        return MediaPart("[формат файла не поддерживается этой моделью]")

    async def image(self, media: MediaRef, name: str, mime: str) -> MediaPart:
        return MediaPart("[изображение: эта модель не видит картинки]")

    async def audio(self, media: MediaRef, *, user_id: int | None, chat_id: int) -> MediaPart:
        return MediaPart(await self._media.transcript(media, user_id=user_id, chat_id=chat_id))

    async def pdf(self, media: MediaRef, name: str) -> MediaPart:
        data = await self._media.fetch(media)
        if data is None:
            return MediaPart(UNREADABLE)
        text = pdf_text(data, limit=self._text_limit)
        if text is None:
            return MediaPart(NOT_PDF)
        return MediaPart(_document(name, _clip(text, self._text_limit) or "[в PDF нет текста]"))


class ChatEncoder(InlineEncoder):
    """Chat Completions (DeepSeek, Groq): images as data URLs for vision models."""

    def __init__(self, media: MediaService, *, vision: bool, text_limit: int) -> None:
        super().__init__(media, text_limit=text_limit)
        self._vision = vision

    async def image(self, media: MediaRef, name: str, mime: str) -> MediaPart:
        if not self._vision:
            return await super().image(media, name, mime)
        data = await self._media.fetch(media)
        if data is None:
            return MediaPart(UNREADABLE)
        url = f"data:{mime};base64,{_b64(data)}"
        return MediaPart(None, [{"type": "image_url", "image_url": {"url": url}}])


class ResponsesEncoder(InlineEncoder):
    """OpenAI Responses: images and PDFs uploaded once to the Files API."""

    def __init__(self, media: MediaService, store: FileStore, cache: MediaRepo, *, text_limit: int) -> None:
        super().__init__(media, text_limit=text_limit)
        self._store = store
        self._cache = cache

    async def image(self, media: MediaRef, name: str, mime: str) -> MediaPart:
        file_id = await self._upload(media, name, mime)
        if file_id is None:
            return MediaPart(UNREADABLE)
        return MediaPart(None, [{"type": "input_image", "file_id": file_id}])

    async def pdf(self, media: MediaRef, name: str) -> MediaPart:
        try:
            file_id = await self._upload(media, name, "application/pdf", prepare=extract_pdf)
        except UnreadableFileError:
            return MediaPart(NOT_PDF)
        if file_id is None:
            return MediaPart(UNREADABLE)
        return MediaPart(None, [{"type": "input_file", "file_id": file_id}])

    async def _upload(
        self, media: MediaRef, name: str, mime: str, prepare: Callable[[bytes], bytes | None] | None = None
    ) -> str | None:
        cached = await self._cache.get(media.file_unique_id)
        if cached and cached.openai_file_id:
            return cached.openai_file_id
        data = await self._media.fetch(media)
        if data is None:
            return None
        if prepare is not None:
            prepared = prepare(data)
            if prepared is None:
                raise UnreadableFileError(media.file_unique_id)
            data = prepared
        try:
            file_id = await self._store.upload(name, data, mime)
        except Exception:
            log.warning("OpenAI upload failed for %s", media.file_unique_id, exc_info=True)
            return None
        await self._cache.save_openai_file(media.file_unique_id, file_id)
        return file_id


class GeminiEncoder(InlineEncoder):
    """Gemini: images, voice, video and PDFs inline, so the model hears and sees them itself."""

    async def image(self, media: MediaRef, name: str, mime: str) -> MediaPart:
        return await self._inline(media, mime)

    async def audio(self, media: MediaRef, *, user_id: int | None, chat_id: int) -> MediaPart:
        if (media.file_size or 0) > GEMINI_INLINE_LIMIT:
            return await super().audio(media, user_id=user_id, chat_id=chat_id)
        return await self._inline(media, media.mime_type or DEFAULT_AUDIO_TYPES[media.kind])

    async def pdf(self, media: MediaRef, name: str) -> MediaPart:
        data = await self._media.fetch(media)
        if data is None:
            return MediaPart(UNREADABLE)
        pdf = extract_pdf(data)
        if pdf is None:
            return MediaPart(NOT_PDF)
        return MediaPart(None, [_inline_part("application/pdf", pdf)])

    async def _inline(self, media: MediaRef, mime: str) -> MediaPart:
        data = await self._media.fetch(media)
        if data is None:
            return MediaPart(UNREADABLE)
        return MediaPart(None, [_inline_part(mime, data)])


def _inline_part(mime: str, data: bytes) -> dict[str, Any]:
    return {"inline_data": {"mime_type": mime, "data": _b64(data)}}


def youtube_parts(content: Content) -> list[dict[str, Any]]:
    """Gemini watches YouTube videos linked anywhere in the turn's text."""
    ids: list[str] = []
    for block in content:
        if block.get("type") != "text":
            continue
        for video_id in YOUTUBE.findall(block["text"]):
            if video_id not in ids:
                ids.append(video_id)
    return [
        {"file_data": {"file_uri": f"https://www.youtube.com/watch?v={video_id}"}}
        for video_id in ids[:MAX_YOUTUBE_LINKS]
    ]

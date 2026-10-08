from decimal import Decimal
from typing import Any, cast

from tgagent.context.media import MediaService
from tgagent.domain import MediaRef
from tgagent.storage.repos import MediaEntry, MediaRepo, UsageRecord, UsageRepo


class FakeSource:
    def __init__(self) -> None:
        self.downloads: list[str] = []

    async def download(self, file_id: str) -> bytes:
        self.downloads.append(file_id)
        return f"bytes:{file_id}".encode()


class FakeStore:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str]] = []

    async def upload(self, filename: str, data: bytes, mime_type: str) -> str:
        self.uploads.append((filename, mime_type))
        return f"file_{len(self.uploads)}"


class FakeStt:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def transcribe(self, data: bytes, filename: str) -> str:
        self.calls.append(filename)
        if self.fail:
            raise RuntimeError("groq down")
        return "расшифровка"


class FakeCache:
    def __init__(self) -> None:
        self.entries: dict[str, MediaEntry] = {}

    async def get(self, file_unique_id: str) -> MediaEntry | None:
        return self.entries.get(file_unique_id)

    async def save_file(self, file_unique_id: str, anthropic_file_id: str) -> None:
        old = self.entries.get(file_unique_id, MediaEntry(None, None))
        self.entries[file_unique_id] = MediaEntry(anthropic_file_id, old.transcript)

    async def save_transcript(self, file_unique_id: str, transcript: str) -> None:
        old = self.entries.get(file_unique_id, MediaEntry(None, None))
        self.entries[file_unique_id] = MediaEntry(old.anthropic_file_id, transcript)


class FakeUsage:
    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    async def add(self, record: UsageRecord) -> None:
        self.records.append(record)


class Harness:
    def __init__(self, *, stt_fails: bool = False) -> None:
        self.source = FakeSource()
        self.store = FakeStore()
        self.stt = FakeStt(stt_fails)
        self.cache = FakeCache()
        self.usage = FakeUsage()
        self.service = MediaService(
            self.source,
            self.store,
            self.stt,
            cast(MediaRepo, self.cache),
            cast(UsageRepo, self.usage),
            whisper_model="whisper-large-v3",
        )

    async def describe(self, media: MediaRef, *, code: bool = True) -> Any:
        return await self.service.describe(media, code_enabled=code, user_id=1, chat_id=2)


def ref(kind: str, uid: str = "u1", **extra: Any) -> MediaRef:
    return MediaRef(kind=kind, file_id=f"id-{uid}", file_unique_id=uid, **extra)  # type: ignore[arg-type]


async def test_photo_becomes_image_block_and_upload_is_cached() -> None:
    h = Harness()

    first = await h.describe(ref("photo"))
    second = await h.describe(ref("photo"))

    image = {"type": "image", "source": {"type": "file", "file_id": "file_1"}}
    assert first.blocks == [image]
    assert second.blocks == [image]
    assert h.store.uploads == [("photo.jpg", "image/jpeg")]


async def test_voice_is_transcribed_cached_and_billed() -> None:
    h = Harness()
    voice = ref("voice", duration=30, mime_type="audio/ogg")

    first = await h.describe(voice)
    second = await h.describe(voice)

    assert first.body == "расшифровка"
    assert second.body == "расшифровка"
    assert h.stt.calls == ["voice.ogg"]
    assert len(h.usage.records) == 1
    record = h.usage.records[0]
    assert (record.kind, record.audio_seconds, record.chat_id) == ("stt", 30, 2)
    assert record.cost_usd > Decimal(0)


async def test_oversized_audio_is_not_downloaded() -> None:
    h = Harness()

    part = await h.describe(ref("voice", file_size=25 * 1024 * 1024))

    assert part.body == "[не удалось расшифровать: файл больше 20 МБ]"
    assert h.source.downloads == []


async def test_transcription_failure_is_reported_in_body() -> None:
    h = Harness(stt_fails=True)

    part = await h.describe(ref("video_note", duration=5))

    assert part.body == "[не удалось расшифровать]"
    assert h.usage.records == []


async def test_pdf_document() -> None:
    h = Harness()

    part = await h.describe(ref("document", file_name="r.pdf", mime_type="application/pdf"))

    assert part.blocks == [
        {"type": "document", "source": {"type": "file", "file_id": "file_1"}, "title": "r.pdf"}
    ]


async def test_text_document_uploaded_as_plain_text() -> None:
    h = Harness()

    part = await h.describe(ref("document", file_name="data.csv", mime_type="text/csv"))

    assert part.blocks[0]["type"] == "document"
    assert h.store.uploads == [("data.csv", "text/plain")]


async def test_binary_document_goes_to_code_sandbox_when_enabled() -> None:
    h = Harness()
    xlsx = ref("document", file_name="t.xlsx", mime_type="application/vnd.ms-excel")

    enabled = await h.describe(xlsx)
    disabled = await Harness().describe(xlsx, code=False)

    assert enabled.blocks == [{"type": "container_upload", "file_id": "file_1"}]
    assert disabled.blocks == []
    assert disabled.body == "[формат файла не поддерживается]"


async def test_sticker_has_no_blocks() -> None:
    part = await Harness().describe(ref("sticker", emoji="😂"))

    assert (part.body, part.blocks) == (None, [])

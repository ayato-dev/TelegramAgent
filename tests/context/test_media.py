from decimal import Decimal
from typing import Any, cast

from tests.context.test_pdf import PDF, signed
from tgagent.context.media import MediaService
from tgagent.domain import MediaRef
from tgagent.storage.repos import MediaEntry, MediaRepo, UsageRecord, UsageRepo


class FakeSource:
    def __init__(self) -> None:
        self.downloads: list[str] = []
        self.contents: dict[str, bytes] = {}

    async def download(self, file_id: str) -> bytes:
        self.downloads.append(file_id)
        return self.contents.get(file_id, f"bytes:{file_id}".encode())


class FakeStore:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str]] = []
        self.data: list[bytes] = []

    async def upload(self, filename: str, data: bytes, mime_type: str) -> str:
        self.uploads.append((filename, mime_type))
        self.data.append(data)
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
    def __init__(self, *, stt_fails: bool = False, paid: bool = True) -> None:
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
            whisper_paid=paid,
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
    h.source.contents["id-u1"] = PDF

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


async def test_transcription_logs_duration(caplog: Any) -> None:
    h = Harness()

    with caplog.at_level("INFO", logger="tgagent.context.media"):
        await h.describe(ref("voice", duration=7))

    assert any(r.getMessage().startswith("whisper u1: 7s audio in") for r in caplog.records)


async def test_free_groq_tier_records_audio_but_no_cost() -> None:
    h = Harness(paid=False)

    await h.describe(ref("voice", duration=30))

    record = h.usage.records[0]
    assert record.audio_seconds == 30
    assert record.cost_usd == Decimal(0)


async def test_signed_pdf_is_unwrapped_before_upload() -> None:
    h = Harness()
    h.source.contents["id-u1"] = signed(PDF)

    part = await h.describe(ref("document", file_name="diploma.pdf", mime_type="application/pdf"))

    assert part.blocks[0]["type"] == "document"
    assert h.store.data == [PDF]


async def test_detached_signature_file_with_pdf_inside_is_read() -> None:
    h = Harness()
    h.source.contents["id-u1"] = signed(PDF)

    part = await h.describe(ref("document", file_name="doc.pdf.sig", mime_type="application/pkcs7-signature"))

    assert part.blocks[0]["type"] == "document"
    assert h.store.uploads == [("doc.pdf.sig", "application/pdf")]


async def test_pdf_without_pdf_inside_becomes_a_note() -> None:
    h = Harness()
    h.source.contents["id-u1"] = b"\x00garbage"

    part = await h.describe(ref("document", file_name="broken.pdf", mime_type="application/pdf"))

    assert part.blocks == []
    assert part.body == "[не удалось прочитать файл: внутри не PDF]"
    assert h.store.uploads == []

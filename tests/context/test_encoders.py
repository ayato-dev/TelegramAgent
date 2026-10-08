import base64
from typing import cast

from tests.context.test_media import FakeCache, FakeSource, FakeStore, FakeStt, FakeUsage, ref
from tests.context.test_pdf import signed, text_pdf
from tgagent.context.encoders import ChatEncoder, GeminiEncoder, ResponsesEncoder, youtube_parts
from tgagent.context.media import MediaPart, MediaService
from tgagent.domain import MediaRef
from tgagent.storage.repos import MediaRepo, UsageRepo


class Harness:
    def __init__(self, *, stt: bool = True) -> None:
        self.source = FakeSource()
        self.store = FakeStore()
        self.stt = FakeStt()
        self.cache = FakeCache()
        self.media = MediaService(
            self.source,
            None,
            self.stt if stt else None,
            cast(MediaRepo, self.cache),
            cast(UsageRepo, FakeUsage()),
            whisper_model="whisper-large-v3",
            whisper_paid=False,
        )

    def chat(self, *, vision: bool = True, text_limit: int = 1000) -> ChatEncoder:
        return ChatEncoder(self.media, vision=vision, text_limit=text_limit)

    def responses(self) -> ResponsesEncoder:
        return ResponsesEncoder(self.media, self.store, cast(MediaRepo, self.cache), text_limit=1000)

    def gemini(self) -> GeminiEncoder:
        return GeminiEncoder(self.media, text_limit=1000)


async def describe(encoder: ChatEncoder | ResponsesEncoder | GeminiEncoder, media: MediaRef) -> MediaPart:
    return await encoder.describe(media, code_enabled=True, user_id=1, chat_id=2)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


PDF_DOC = {"file_name": "invoice.pdf", "mime_type": "application/pdf"}


async def test_chat_encoder_sends_images_as_data_urls_when_the_model_sees() -> None:
    h = Harness()

    part = await describe(h.chat(), ref("photo"))

    url = "data:image/jpeg;base64," + b64(b"bytes:id-u1")
    assert part == MediaPart(None, [{"type": "image_url", "image_url": {"url": url}}])


async def test_chat_encoder_describes_images_to_a_blind_model() -> None:
    h = Harness()

    part = await describe(h.chat(vision=False), ref("photo"))

    assert part.blocks == []
    assert part.body == "[изображение: эта модель не видит картинки]"
    assert h.source.downloads == []


async def test_chat_encoder_reads_pdf_text_including_signed_documents() -> None:
    h = Harness()
    h.source.contents["id-u1"] = signed(text_pdf("Invoice 42"))

    part = await describe(h.chat(), ref("document", **PDF_DOC))

    assert part == MediaPart('<document name="invoice.pdf">\nInvoice 42\n</document>')


async def test_text_documents_are_inlined_and_clipped() -> None:
    h = Harness()
    h.source.contents["id-u1"] = ("строка\n" * 100).encode()

    part = await describe(
        h.chat(text_limit=20), ref("document", file_name="notes.txt", mime_type="text/plain")
    )

    assert part.body is not None
    assert part.body.startswith('<document name="notes.txt">\nстрока\nстрока\n')
    assert "[обрезано]" in part.body
    assert len(part.body) < 100


async def test_voice_is_transcribed_for_models_without_audio() -> None:
    h = Harness()

    part = await describe(h.chat(), ref("voice", duration=3))

    assert part == MediaPart("расшифровка")


async def test_voice_without_speech_to_text_gets_a_note() -> None:
    part = await describe(Harness(stt=False).chat(), ref("voice", duration=3))

    assert part.body == "[расшифровка голосовых недоступна]"


async def test_unsupported_and_oversized_documents_become_notes() -> None:
    h = Harness()

    binary = await describe(
        h.chat(), ref("document", file_name="t.xlsx", mime_type="application/vnd.ms-excel")
    )
    huge = await describe(h.chat(), ref("document", "u2", file_name="a.txt", file_size=30 * 1024 * 1024))

    assert binary.body == "[формат файла не поддерживается этой моделью]"
    assert huge.body == "[файл больше 20 МБ — открыть его не получится]"
    assert h.source.downloads == []


async def test_responses_encoder_uploads_images_and_pdfs_once() -> None:
    h = Harness()
    h.source.contents["id-u2"] = signed(text_pdf("x"))
    encoder = h.responses()

    first = await describe(encoder, ref("photo"))
    again = await describe(encoder, ref("photo"))
    pdf = await describe(encoder, ref("document", "u2", **PDF_DOC))

    assert first.blocks == again.blocks == [{"type": "input_image", "file_id": "file_1"}]
    assert pdf.blocks == [{"type": "input_file", "file_id": "file_2"}]
    assert h.store.uploads == [("photo.jpg", "image/jpeg"), ("invoice.pdf", "application/pdf")]
    assert h.store.data[1] == text_pdf("x")


async def test_gemini_encoder_sends_voice_images_and_pdfs_inline() -> None:
    h = Harness()
    h.source.contents["id-u3"] = signed(text_pdf("x"))
    encoder = h.gemini()

    voice = await describe(encoder, ref("voice", duration=4, mime_type="audio/ogg"))
    photo = await describe(encoder, ref("photo", "u2"))
    pdf = await describe(encoder, ref("document", "u3", **PDF_DOC))

    assert voice == MediaPart(
        None, [{"inline_data": {"mime_type": "audio/ogg", "data": b64(b"bytes:id-u1")}}]
    )
    assert photo.blocks == [{"inline_data": {"mime_type": "image/jpeg", "data": b64(b"bytes:id-u2")}}]
    assert pdf.blocks == [{"inline_data": {"mime_type": "application/pdf", "data": b64(text_pdf("x"))}}]
    assert h.stt.calls == []


async def test_gemini_encoder_transcribes_audio_too_big_to_inline() -> None:
    h = Harness()

    part = await describe(h.gemini(), ref("video", duration=600, file_size=15 * 1024 * 1024))

    assert part == MediaPart("расшифровка")


def test_youtube_links_become_video_parts() -> None:
    content = [
        {"type": "text", "text": "глянь https://youtu.be/dQw4w9WgXcQ?t=42 и https://example.com/watch?v=x"},
        {
            "type": "text",
            "text": "ещё https://www.youtube.com/watch?v=dQw4w9WgXcQ и youtube.com/shorts/aBcDeFgHiJk",
        },
    ]

    assert youtube_parts(content) == [
        {"file_data": {"file_uri": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}},
        {"file_data": {"file_uri": "https://www.youtube.com/watch?v=aBcDeFgHiJk"}},
    ]

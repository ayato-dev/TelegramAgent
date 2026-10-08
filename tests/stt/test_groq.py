from types import SimpleNamespace
from typing import Any, cast

import pytest
from groq import AsyncGroq

from tgagent.domain import MediaRef
from tgagent.stt.groq import GroqTranscriber, upload_filename


class FakeTranscriptions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **params: Any) -> SimpleNamespace:
        self.calls.append(params)
        return SimpleNamespace(text="  привет мир  ")


def ref(kind: str, **extra: Any) -> MediaRef:
    return MediaRef(kind=kind, file_id="f", file_unique_id="u", **extra)  # type: ignore[arg-type]


async def test_transcribe_sends_named_file_and_strips_text() -> None:
    fake = FakeTranscriptions()
    client = cast(AsyncGroq, SimpleNamespace(audio=SimpleNamespace(transcriptions=fake)))

    text = await GroqTranscriber(client, "whisper-large-v3").transcribe(b"OGG", "voice.ogg")

    assert text == "привет мир"
    assert fake.calls == [
        {
            "file": ("voice.ogg", b"OGG"),
            "model": "whisper-large-v3",
            "response_format": "json",
            "temperature": 0.0,
        }
    ]


@pytest.mark.parametrize(
    ("media", "expected"),
    [
        (ref("voice", mime_type="audio/ogg"), "voice.ogg"),
        (ref("video_note"), "video_note.mp4"),
        (ref("video", file_name="clip.MOV"), "video.mp4"),
        (ref("audio", file_name="song.m4a"), "song.m4a"),
        (ref("audio", file_name="track.oga", mime_type="audio/ogg"), "audio.ogg"),
        (ref("audio", mime_type="audio/mpeg"), "audio.mp3"),
        (ref("audio"), "audio.mp3"),
    ],
)
def test_upload_filename_uses_extensions_groq_accepts(media: MediaRef, expected: str) -> None:
    assert upload_filename(media) == expected

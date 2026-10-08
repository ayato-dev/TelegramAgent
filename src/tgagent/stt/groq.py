from pathlib import PurePath

from groq import AsyncGroq

from tgagent.domain import MediaRef

# Groq checks the extension, and Telegram names voice files *.oga, which it rejects.
SUPPORTED_EXTENSIONS = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".opus", ".wav", ".webm"}
_BY_MIME = {
    "audio/ogg": "audio.ogg",
    "audio/opus": "audio.opus",
    "audio/mpeg": "audio.mp3",
    "audio/mp4": "audio.m4a",
    "audio/x-m4a": "audio.m4a",
    "audio/wav": "audio.wav",
    "audio/x-wav": "audio.wav",
    "audio/flac": "audio.flac",
    "audio/webm": "audio.webm",
}


def upload_filename(media: MediaRef) -> str:
    match media.kind:
        case "voice":
            return "voice.ogg"
        case "video_note":
            return "video_note.mp4"
        case "video":
            return "video.mp4"
    if media.file_name and PurePath(media.file_name).suffix.lower() in SUPPORTED_EXTENSIONS:
        return media.file_name
    return _BY_MIME.get(media.mime_type or "", "audio.mp3")


class GroqTranscriber:
    def __init__(self, client: AsyncGroq, model: str) -> None:
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def transcribe(self, data: bytes, filename: str) -> str:
        result = await self._client.audio.transcriptions.create(
            file=(filename, data),
            model=self._model,
            response_format="json",
            temperature=0.0,
        )
        return result.text.strip()

"""Transport-neutral message types shared by storage, context building and handlers."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

MediaKind = Literal["photo", "voice", "video_note", "audio", "video", "document", "sticker", "animation"]


@dataclass(frozen=True, slots=True)
class MediaRef:
    kind: MediaKind
    file_id: str
    file_unique_id: str
    mime_type: str | None = None
    file_name: str | None = None
    file_size: int | None = None
    duration: int | None = None
    emoji: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "MediaRef":
        return cls(**data)


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    id: int
    text: str
    done: bool


@dataclass(frozen=True, slots=True)
class Checklist:
    title: str
    items: tuple[ChecklistItem, ...]

    def to_json(self) -> dict[str, Any]:
        return {"title": self.title, "items": [asdict(item) for item in self.items]}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Checklist":
        return cls(title=data["title"], items=tuple(ChecklistItem(**item) for item in data["items"]))


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    chat_id: int
    message_id: int
    thread_id: int | None
    sender_id: int | None
    sender_name: str
    date: datetime
    text: str | None
    media: MediaRef | None = None
    reply_to_message_id: int | None = None
    checklist: Checklist | None = None
    quote: str | None = None
    from_bot: bool = False
    forwarded_from: str | None = None

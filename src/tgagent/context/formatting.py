"""Plain-text rendering of Telegram messages for Claude's user turns."""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from tgagent.domain import Checklist, MediaRef, NormalizedMessage

_MEDIA_NAMES = {
    "photo": "фото",
    "voice": "голосовое",
    "video_note": "видеосообщение",
    "audio": "аудио",
    "video": "видео",
    "animation": "GIF",
    "document": "файл",
    "sticker": "стикер",
}


def format_time(moment: datetime, tz: ZoneInfo) -> str:
    return moment.astimezone(tz).isoformat(timespec="minutes")


def _duration(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}"


def media_label(media: MediaRef) -> str:
    parts = [_MEDIA_NAMES[media.kind]]
    if media.kind == "sticker" and media.emoji:
        parts.append(media.emoji)
    elif media.kind == "document" and media.file_name:
        parts.append(media.file_name)
    elif media.duration is not None:
        parts.append(_duration(media.duration))
    return f"[{' '.join(parts)}]"


def render_checklist(checklist: Checklist, message_id: int) -> str:
    lines = [f'<checklist message_id="{message_id}" title="{escape(checklist.title)}">']
    lines += [f"- [{'x' if item.done else ' '}] #{item.id} {item.text}" for item in checklist.items]
    lines.append("</checklist>")
    return "\n".join(lines)


def render_message(
    message: NormalizedMessage,
    tz: ZoneInfo,
    *,
    include_author: bool,
    body: str | None = None,
) -> str:
    """``<message ...>`` wrapper with attributes; ``body`` carries transcripts or file notes."""
    attrs = [f'id="{message.message_id}"']
    if include_author:
        attrs.append(f'author="{escape(message.sender_name)}"')
    attrs.append(f'time="{format_time(message.date, tz)}"')
    if message.reply_to_message_id is not None:
        attrs.append(f'reply_to="{message.reply_to_message_id}"')
    if message.forwarded_from:
        attrs.append(f'forwarded_from="{escape(message.forwarded_from)}"')

    lines: list[str] = []
    if message.quote:
        lines.append(f"<quote>{message.quote}</quote>")
    if message.media:
        lines.append(media_label(message.media))
    if body:
        lines.append(body)
    if message.checklist:
        lines.append(render_checklist(message.checklist, message.message_id))
    if message.text:
        lines.append(message.text)

    return f"<message {' '.join(attrs)}>\n" + "\n".join(lines) + "\n</message>"

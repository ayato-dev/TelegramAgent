from aiogram.types import (
    Message,
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginHiddenUser,
    MessageOriginUser,
)

from tgagent.context.rich_text import rich_to_text
from tgagent.domain import Checklist, ChecklistItem, MediaRef, NormalizedMessage


def display_name(first_name: str, last_name: str | None, username: str | None) -> str:
    name = f"{first_name} {last_name}" if last_name else first_name
    return f"{name} (@{username})" if username else name


def _sender_name(message: Message) -> str:
    if message.from_user:
        user = message.from_user
        return display_name(user.first_name, user.last_name, user.username)
    if message.sender_chat:
        return message.sender_chat.title or "канал"
    return "неизвестно"


def _forwarded_from(message: Message) -> str | None:
    match message.forward_origin:
        case MessageOriginUser(sender_user=user):
            return display_name(user.first_name, user.last_name, user.username)
        case MessageOriginHiddenUser(sender_user_name=name):
            return name
        case MessageOriginChat(sender_chat=chat) | MessageOriginChannel(chat=chat):
            return chat.title or "чат"
        case _:
            return None


def _media(message: Message) -> MediaRef | None:
    if message.photo:
        photo = message.photo[-1]
        return MediaRef("photo", photo.file_id, photo.file_unique_id, file_size=photo.file_size)
    if voice := message.voice:
        return MediaRef(
            "voice",
            voice.file_id,
            voice.file_unique_id,
            voice.mime_type,
            None,
            voice.file_size,
            voice.duration,
        )
    if note := message.video_note:
        return MediaRef(
            "video_note", note.file_id, note.file_unique_id, None, None, note.file_size, note.duration
        )
    if audio := message.audio:
        return MediaRef(
            "audio",
            audio.file_id,
            audio.file_unique_id,
            audio.mime_type,
            audio.file_name,
            audio.file_size,
            audio.duration,
        )
    if video := message.video:
        return MediaRef(
            "video",
            video.file_id,
            video.file_unique_id,
            video.mime_type,
            video.file_name,
            video.file_size,
            video.duration,
        )
    if animation := message.animation:
        return MediaRef("animation", animation.file_id, animation.file_unique_id, animation.mime_type)
    if document := message.document:
        return MediaRef(
            "document",
            document.file_id,
            document.file_unique_id,
            document.mime_type,
            document.file_name,
            document.file_size,
        )
    if sticker := message.sticker:
        return MediaRef("sticker", sticker.file_id, sticker.file_unique_id, emoji=sticker.emoji)
    return None


def _checklist(message: Message) -> Checklist | None:
    if not message.checklist:
        return None
    items = tuple(
        ChecklistItem(
            id=task.id,
            text=task.text,
            done=bool(task.completion_date) or task.completed_by_user is not None,
        )
        for task in message.checklist.tasks
    )
    return Checklist(title=message.checklist.title, items=items)


def _reply_to(message: Message) -> int | None:
    parent = message.reply_to_message
    if parent is None or parent.forum_topic_created is not None:
        return None
    return parent.message_id


def normalize(message: Message) -> NormalizedMessage:
    return NormalizedMessage(
        chat_id=message.chat.id,
        message_id=message.message_id,
        thread_id=message.message_thread_id if message.is_topic_message else None,
        sender_id=message.from_user.id if message.from_user else None,
        sender_name=_sender_name(message),
        date=message.date,
        text=message.text
        or message.caption
        or (rich_to_text(message.rich_message) if message.rich_message else None),
        media=_media(message),
        reply_to_message_id=_reply_to(message),
        checklist=_checklist(message),
        quote=message.quote.text if message.quote else None,
        from_bot=bool(message.from_user and message.from_user.is_bot),
        forwarded_from=_forwarded_from(message),
    )

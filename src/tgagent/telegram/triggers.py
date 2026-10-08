from aiogram.types import Message


def is_addressed(message: Message, bot_id: int, bot_username: str) -> bool:
    """A group message is for the bot when it @mentions it, replies to it, or targets its command."""
    parent = message.reply_to_message
    if (
        parent is not None
        and parent.forum_topic_created is None
        and parent.from_user is not None
        and parent.from_user.id == bot_id
    ):
        return True
    text = message.text or message.caption or ""
    handle = f"@{bot_username.lower()}"
    for entity in message.entities or message.caption_entities or []:
        match entity.type:
            case "mention" if entity.extract_from(text).lower() == handle:
                return True
            case "text_mention" if entity.user is not None and entity.user.id == bot_id:
                return True
            case "bot_command" if entity.extract_from(text).lower().endswith(handle):
                return True
    return False

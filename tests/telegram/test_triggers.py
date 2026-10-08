from datetime import UTC, datetime

import pytest
from aiogram.types import Chat, ForumTopicCreated, Message, MessageEntity, User

from tgagent.telegram.triggers import is_addressed

BOT = User(id=999, is_bot=True, first_name="Agent", username="agent_bot")
ANNA = User(id=1, is_bot=False, first_name="Аня")
GROUP = Chat(id=-100, type="supergroup")


def message(text: str | None = None, **fields: object) -> Message:
    base: dict[str, object] = {"message_id": 5, "date": datetime.now(UTC), "chat": GROUP, "from_user": ANNA}
    if text is not None:
        base["text"] = text
    base.update(fields)
    return Message(**base)  # type: ignore[arg-type]


def mention(text: str, at: str) -> list[MessageEntity]:
    offset = len(text[: text.index(at)].encode("utf-16-le")) // 2
    return [MessageEntity(type="mention", offset=offset, length=len(at))]


def addressed(msg: Message) -> bool:
    return is_addressed(msg, BOT.id, "agent_bot")


def test_plain_message_is_ignored() -> None:
    assert not addressed(message("всем привет"))


def test_mention_in_text_after_emoji() -> None:
    text = "😀 @Agent_Bot это правда?"
    assert addressed(message(text, entities=mention(text, "@Agent_Bot")))


def test_mention_of_another_bot_is_ignored() -> None:
    text = "@other_bot привет"
    assert not addressed(message(text, entities=mention(text, "@other_bot")))


def test_mention_in_photo_caption() -> None:
    caption = "@agent_bot что на фото?"
    assert addressed(message(caption=caption, caption_entities=mention(caption, "@agent_bot")))


def test_text_mention_of_bot() -> None:
    entity = MessageEntity(type="text_mention", offset=0, length=5, user=BOT)
    assert addressed(message("Агент, помоги", entities=[entity]))


def test_command_addressed_to_bot() -> None:
    entity = MessageEntity(type="bot_command", offset=0, length=14)
    assert addressed(message("/ask@agent_bot вопрос", entities=[entity]))


@pytest.mark.parametrize(("author", "expected"), [(BOT, True), (ANNA, False)])
def test_reply_to_bot_message(author: User, expected: bool) -> None:
    parent = message("ответ", from_user=author, message_id=4)
    assert addressed(message("а подробнее?", reply_to_message=parent)) is expected


def test_reply_to_topic_creation_is_not_a_reply_to_bot() -> None:
    created = message(
        from_user=BOT, message_id=2, forum_topic_created=ForumTopicCreated(name="t", icon_color=1)
    )
    assert not addressed(message("текст", reply_to_message=created))

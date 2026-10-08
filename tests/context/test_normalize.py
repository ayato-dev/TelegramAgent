from datetime import UTC, datetime

from aiogram.types import (
    Chat,
    ChecklistTask,
    ForumTopicCreated,
    Message,
    MessageOriginHiddenUser,
    PhotoSize,
    Sticker,
    TextQuote,
    User,
    Voice,
)
from aiogram.types import Checklist as TgChecklist

from tgagent.context.normalize import normalize
from tgagent.domain import Checklist, ChecklistItem, MediaRef

DATE = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
GROUP = Chat(id=-100, type="supergroup", title="Team")
IVAN = User(id=42, is_bot=False, first_name="Иван", last_name="Петров", username="ivan")


def message(**fields: object) -> Message:
    base: dict[str, object] = {"message_id": 10, "date": DATE, "chat": GROUP, "from_user": IVAN}
    base.update(fields)
    return Message(**base)  # type: ignore[arg-type]


def test_text_message() -> None:
    result = normalize(message(text="привет"))

    assert result.chat_id == -100
    assert result.message_id == 10
    assert result.sender_id == 42
    assert result.sender_name == "Иван Петров (@ivan)"
    assert result.text == "привет"
    assert result.media is None
    assert result.date == DATE


def test_photo_uses_largest_size_and_caption() -> None:
    sizes = [
        PhotoSize(file_id="small", file_unique_id="s", width=90, height=90, file_size=1000),
        PhotoSize(file_id="big", file_unique_id="b", width=1280, height=1280, file_size=90000),
    ]

    result = normalize(message(photo=sizes, caption="что это?"))

    assert result.text == "что это?"
    assert result.media == MediaRef(kind="photo", file_id="big", file_unique_id="b", file_size=90000)


def test_voice_message() -> None:
    voice = Voice(file_id="v", file_unique_id="vu", duration=42, mime_type="audio/ogg", file_size=5000)

    result = normalize(message(voice=voice))

    assert result.media == MediaRef(
        kind="voice", file_id="v", file_unique_id="vu", mime_type="audio/ogg", file_size=5000, duration=42
    )


def test_sticker_keeps_emoji() -> None:
    sticker = Sticker(
        file_id="st",
        file_unique_id="stu",
        type="regular",
        width=512,
        height=512,
        is_animated=False,
        is_video=False,
        emoji="😂",
    )

    assert normalize(message(sticker=sticker)).media == MediaRef(
        kind="sticker", file_id="st", file_unique_id="stu", emoji="😂"
    )


def test_reply_id_recorded() -> None:
    parent = message(message_id=5, text="parent")

    assert normalize(message(text="child", reply_to_message=parent)).reply_to_message_id == 5


def test_reply_to_topic_creation_service_message_is_not_a_reply() -> None:
    created = message(message_id=3, forum_topic_created=ForumTopicCreated(name="Topic", icon_color=0x6FB9F0))

    result = normalize(
        message(text="hi", reply_to_message=created, message_thread_id=3, is_topic_message=True)
    )

    assert result.reply_to_message_id is None
    assert result.thread_id == 3


def test_thread_ignored_for_non_topic_messages() -> None:
    assert normalize(message(text="x", message_thread_id=99)).thread_id is None


def test_checklist_with_done_tasks() -> None:
    checklist = TgChecklist(
        title="Дела",
        tasks=[
            ChecklistTask(id=1, text="Купить молоко"),
            ChecklistTask(id=2, text="Позвонить", completed_by_user=IVAN, completion_date=1_700_000_000),
        ],
    )

    result = normalize(message(checklist=checklist))

    assert result.checklist == Checklist(
        title="Дела",
        items=(ChecklistItem(1, "Купить молоко", done=False), ChecklistItem(2, "Позвонить", done=True)),
    )


def test_forward_origin_and_quote() -> None:
    origin = MessageOriginHiddenUser(date=DATE, sender_user_name="Аноним")
    quote = TextQuote(text="важная часть", position=0)

    result = normalize(message(text="смотри", forward_origin=origin, quote=quote))

    assert result.forwarded_from == "Аноним"
    assert result.quote == "важная часть"


def test_bot_sender_flagged() -> None:
    bot = User(id=1, is_bot=True, first_name="Helper", username="helper_bot")

    assert normalize(message(text="beep", from_user=bot)).from_bot

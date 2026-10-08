from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from tgagent.context.formatting import media_label, render_checklist, render_message
from tgagent.domain import Checklist, ChecklistItem, MediaRef, NormalizedMessage

MSK = ZoneInfo("Europe/Moscow")


def msg(**fields: object) -> NormalizedMessage:
    base: dict[str, object] = {
        "chat_id": -100,
        "message_id": 7,
        "thread_id": None,
        "sender_id": 42,
        "sender_name": 'Иван "Ваня"',
        "date": datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
        "text": "привет",
    }
    base.update(fields)
    return NormalizedMessage(**base)  # type: ignore[arg-type]


def test_message_with_author_and_local_time() -> None:
    rendered = render_message(msg(), MSK, include_author=True)

    assert (
        rendered
        == '<message id="7" author="Иван &quot;Ваня&quot;" time="2026-10-08T15:00+03:00">\nпривет\n</message>'
    )


def test_message_without_author() -> None:
    assert render_message(msg(), MSK, include_author=False) == (
        '<message id="7" time="2026-10-08T15:00+03:00">\nпривет\n</message>'
    )


def test_reply_forward_and_quote_attributes() -> None:
    rendered = render_message(
        msg(reply_to_message_id=3, forwarded_from="Канал", quote="цитата"), MSK, include_author=False
    )

    assert 'reply_to="3"' in rendered
    assert 'forwarded_from="Канал"' in rendered
    assert "<quote>цитата</quote>" in rendered


def test_media_body_and_extra_text() -> None:
    voice = MediaRef(kind="voice", file_id="f", file_unique_id="u", duration=75)

    rendered = render_message(msg(text=None, media=voice), MSK, include_author=False, body="расшифровка")

    assert "[голосовое 1:15]\nрасшифровка" in rendered


def test_media_labels() -> None:
    def ref(kind: str, **extra: object) -> MediaRef:
        return MediaRef(kind=kind, file_id="f", file_unique_id="u", **extra)  # type: ignore[arg-type]

    assert media_label(ref("photo")) == "[фото]"
    assert media_label(ref("video_note", duration=9)) == "[видеосообщение 0:09]"
    assert media_label(ref("sticker", emoji="😂")) == "[стикер 😂]"
    assert media_label(ref("document", file_name="report.pdf")) == "[файл report.pdf]"


def test_checklist_rendering() -> None:
    checklist = Checklist(
        title="Дела", items=(ChecklistItem(1, "Купить", done=False), ChecklistItem(2, "Позвонить", done=True))
    )

    assert render_checklist(checklist, message_id=55) == (
        '<checklist message_id="55" title="Дела">\n- [ ] #1 Купить\n- [x] #2 Позвонить\n</checklist>'
    )

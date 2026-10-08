import asyncio
from datetime import UTC, datetime

from aiogram.types import Chat, Message

from tgagent.agent.tools import AgentOptions
from tgagent.telegram.albums import AlbumCollector
from tgagent.telegram.keyboards import SETTINGS_PREFIX, settings_keyboard, settings_text


def buttons(options: AgentOptions) -> dict[str, tuple[str, str | None]]:
    markup = settings_keyboard(options)
    return {
        button.callback_data or "": (button.text, button.style)
        for row in markup.inline_keyboard
        for button in row
    }


def test_selected_effort_is_highlighted() -> None:
    result = buttons(AgentOptions(effort="high"))

    assert result[f"{SETTINGS_PREFIX}effort:high"][1] == "success"
    assert result[f"{SETTINGS_PREFIX}effort:low"][1] is None


def test_flags_show_state() -> None:
    result = buttons(AgentOptions(web=False, show_thinking=True))

    assert result[f"{SETTINGS_PREFIX}web"] == ("🌐 Веб-поиск: выкл", "danger")
    assert result[f"{SETTINGS_PREFIX}show_thinking"] == ("💭 Размышления: вкл", "success")
    assert all(len(data.encode()) <= 64 for data in result)


def test_settings_text_mentions_scope() -> None:
    assert "этого чата" in settings_text(AgentOptions(), group=True)
    assert "этого чата" not in settings_text(AgentOptions(), group=False)


def photo_message(message_id: int, group: str | None) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        media_group_id=group,
        text="x",
    )


async def test_album_collected_into_one_batch() -> None:
    collector = AlbumCollector(delay=0.05)

    results = await asyncio.gather(
        collector.collect(photo_message(1, "g")),
        collector.collect(photo_message(2, "g")),
        collector.collect(photo_message(3, None)),
    )

    batches = [r for r in results if r is not None]
    assert sorted(len(b) for b in batches) == [1, 2]
    album = next(b for b in batches if len(b) == 2)
    assert [m.message_id for m in album] == [1, 2]

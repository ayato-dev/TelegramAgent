from dataclasses import replace

from aiogram.types import InlineKeyboardMarkup

from tgagent.agent.models import CATALOG
from tgagent.agent.tools import AgentOptions
from tgagent.telegram.keyboards import (
    SETTINGS_PREFIX,
    model_badges,
    models_keyboard,
    settings_keyboard,
    settings_text,
)

HAIKU = CATALOG["anthropic:claude-haiku-5-5"]
OSS = replace(CATALOG["groq:openai/gpt-oss-120b"], free=True)
MODELS = (HAIKU, OSS)


def by_data(markup: InlineKeyboardMarkup) -> dict[str, tuple[str, str | None]]:
    return {
        button.callback_data or "": (button.text, button.style)
        for row in markup.inline_keyboard
        for button in row
    }


def buttons(options: AgentOptions) -> dict[str, tuple[str, str | None]]:
    return by_data(settings_keyboard(options, HAIKU, pickable=True))


def test_selected_effort_is_highlighted() -> None:
    result = buttons(AgentOptions(effort="high"))

    assert result[f"{SETTINGS_PREFIX}effort:high"][1] == "success"
    assert result[f"{SETTINGS_PREFIX}effort:low"][1] is None


def test_flags_show_state() -> None:
    result = buttons(AgentOptions(web=False, show_thinking=True))

    assert result[f"{SETTINGS_PREFIX}web"] == ("🌐 Веб-поиск: выкл", "danger")
    assert result[f"{SETTINGS_PREFIX}show_thinking"] == ("💭 Размышления: вкл", "success")
    assert all(len(data.encode()) <= 64 for data in result)


def test_settings_text_mentions_scope_and_model() -> None:
    text = settings_text(AgentOptions(), HAIKU)

    assert "в группах" in text
    assert "Claude Haiku 5.5" in text


def test_model_button_opens_the_picker_only_when_there_is_a_choice() -> None:
    assert buttons(AgentOptions())[f"{SETTINGS_PREFIX}models"][0].startswith("🤖 Claude Haiku 5.5")
    assert f"{SETTINGS_PREFIX}models" not in by_data(settings_keyboard(AgentOptions(), HAIKU, pickable=False))


def test_picker_lists_models_with_badges_and_marks_the_current_one() -> None:
    picker = by_data(models_keyboard(MODELS, OSS.key))

    assert picker[f"{SETTINGS_PREFIX}model:0"] == (f"Claude Haiku 5.5 {model_badges(HAIKU)}", None)
    assert picker[f"{SETTINGS_PREFIX}model:1"][1] == "success"
    assert f"{SETTINGS_PREFIX}back" in picker
    assert all(len(data.encode()) <= 64 for data in picker)


def test_badges_show_capabilities() -> None:
    assert model_badges(HAIKU) == "👁🌐🐍"
    assert model_badges(OSS) == "🌐🐍🆓"
    assert model_badges(CATALOG["gemini:gemini-3.1-flash-lite"]) == "👁🎙🌐🐍"
    assert "🎨" in model_badges(CATALOG["openai:gpt-6-luna"])


def test_style_button_shows_current_style() -> None:
    assert buttons(AgentOptions())[f"{SETTINGS_PREFIX}style"][0] == "🙂 Стиль: обычный"
    assert buttons(AgentOptions(style="troll"))[f"{SETTINGS_PREFIX}style"][0] == "😈 Стиль: дерзкий"

from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tgagent.agent.tools import AgentOptions

SETTINGS_PREFIX = "set:"
EFFORT_LABELS = {"low": "⚡ Быстро", "medium": "⚖️ Обычно", "high": "🧠 Глубоко"}
FLAG_LABELS = {"show_thinking": "💭 Размышления", "web": "🌐 Веб-поиск", "code": "🐍 Код"}

ButtonStyle = Literal["success", "danger"]


def _flag(name: str, enabled: bool) -> InlineKeyboardButton:
    style: ButtonStyle = "success" if enabled else "danger"
    return InlineKeyboardButton(
        text=f"{FLAG_LABELS[name]}: {'вкл' if enabled else 'выкл'}",
        callback_data=f"{SETTINGS_PREFIX}{name}",
        style=style,
    )


def settings_keyboard(options: AgentOptions) -> InlineKeyboardMarkup:
    efforts = [
        InlineKeyboardButton(
            text=label,
            callback_data=f"{SETTINGS_PREFIX}effort:{level}",
            style="success" if options.effort == level else None,
        )
        for level, label in EFFORT_LABELS.items()
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            efforts,
            [_flag("show_thinking", options.show_thinking)],
            [_flag("web", options.web), _flag("code", options.code)],
            [
                InlineKeyboardButton(
                    text="😈 Стиль: дерзкий" if options.style == "troll" else "🙂 Стиль: обычный",
                    callback_data=f"{SETTINGS_PREFIX}style",
                )
            ],
        ]
    )


def settings_text(options: AgentOptions, *, group: bool) -> str:
    scope = " этого чата" if group else ""
    return (
        f"⚙️ Настройки{scope}\n\n"
        f"Глубина: {EFFORT_LABELS[options.effort]} — сколько модель размышляет перед ответом.\n"
        "Размышления: показывать ход мыслей (в стриме и свёрнутым блоком в ответе).\n"
        "Веб-поиск и код: инструменты агента — поиск в интернете и Python-песочница.\n"
        "Стиль: обычный или дерзкий (с матом и подъёбами)."
    )

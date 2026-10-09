from collections.abc import Sequence
from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tgagent.agent.models import ModelSpec
from tgagent.agent.tools import AgentOptions

SETTINGS_PREFIX = "set:"
EFFORT_LABELS = {"low": "⚡ Быстро", "medium": "⚖️ Обычно", "high": "🧠 Глубоко"}
FLAG_LABELS = {"show_thinking": "💭 Размышления", "web": "🌐 Веб-поиск", "code": "🐍 Код"}
BADGES = (("vision", "👁"), ("native_audio", "🎙"), ("web", "🌐"), ("code", "🐍"), ("image_out", "🎨"))
BADGE_LEGEND = (
    "👁 видит картинки · 🎙 сама слушает голосовые и смотрит YouTube · 🌐 ищет в интернете · "
    "🐍 запускает код · 🎨 рисует картинки · 🆓 бесплатный тариф"
)

ButtonStyle = Literal["success", "danger"]


def _flag(name: str, enabled: bool) -> InlineKeyboardButton:
    style: ButtonStyle = "success" if enabled else "danger"
    return InlineKeyboardButton(
        text=f"{FLAG_LABELS[name]}: {'вкл' if enabled else 'выкл'}",
        callback_data=f"{SETTINGS_PREFIX}{name}",
        style=style,
    )


def model_badges(spec: ModelSpec) -> str:
    badges = "".join(icon for field, icon in BADGES if getattr(spec, field))
    return badges + ("🆓" if spec.free else "")


def models_keyboard(models: Sequence[ModelSpec], current: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{spec.label} {model_badges(spec)}".strip(),
                callback_data=f"{SETTINGS_PREFIX}model:{position}",
                style="success" if spec.key == current else None,
            )
        ]
        for position, spec in enumerate(models)
    ]
    rows.append([InlineKeyboardButton(text="‹ Назад", callback_data=f"{SETTINGS_PREFIX}back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def models_text(current: ModelSpec) -> str:
    return (
        f"🤖 Сейчас: {current.label}\n\n"
        "Выберите модель. В личке смена модели начинает новый разговор, "
        "ответы на старые сообщения продолжаются в их модели.\n\n"
        f"{BADGE_LEGEND}"
    )


def settings_keyboard(options: AgentOptions, model: ModelSpec, *, pickable: bool) -> InlineKeyboardMarkup:
    efforts = [
        InlineKeyboardButton(
            text=label,
            callback_data=f"{SETTINGS_PREFIX}effort:{level}",
            style="success" if options.effort == level else None,
        )
        for level, label in EFFORT_LABELS.items()
    ]
    picker = [
        [
            InlineKeyboardButton(
                text=f"🤖 {model.label} {model_badges(model)} ›", callback_data=f"{SETTINGS_PREFIX}models"
            )
        ]
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *(picker if pickable else []),
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


def settings_text(options: AgentOptions, model: ModelSpec) -> str:
    return (
        "⚙️ Твои настройки — действуют в личке, в группах и в гостевом режиме\n\n"
        f"Модель: {model.label} {model_badges(model)}\n"
        f"Глубина: {EFFORT_LABELS[options.effort]} — сколько модель размышляет перед ответом.\n"
        "Размышления: показывать ход мыслей (в стриме и свёрнутым блоком в ответе).\n"
        "Веб-поиск и код: инструменты агента — поиск в интернете и Python-песочница.\n"
        "Стиль: обычный или дерзкий (с матом и подъёбами)."
    )

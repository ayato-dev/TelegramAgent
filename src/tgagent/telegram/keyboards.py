from collections.abc import Sequence
from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tgagent.agent.models import ModelSpec
from tgagent.agent.tools import AgentOptions
from tgagent.i18n import DEFAULT_LANG, Lang, t

SETTINGS_PREFIX = "set:"
EFFORTS = ("low", "medium", "high")
BADGES = (("vision", "👁"), ("native_audio", "🎙"), ("web", "🌐"), ("code", "🐍"), ("image_out", "🎨"))

ButtonStyle = Literal["success", "danger"]


def _flag(name: str, enabled: bool, lang: Lang) -> InlineKeyboardButton:
    style: ButtonStyle = "success" if enabled else "danger"
    return InlineKeyboardButton(
        text=f"{t(lang, f'flag.{name}')}: {t(lang, 'on' if enabled else 'off')}",
        callback_data=f"{SETTINGS_PREFIX}{name}",
        style=style,
    )


def model_badges(spec: ModelSpec) -> str:
    badges = "".join(icon for field, icon in BADGES if getattr(spec, field))
    return badges + ("🆓" if spec.free else "")


def models_keyboard(
    models: Sequence[ModelSpec], current: str, lang: Lang = DEFAULT_LANG
) -> InlineKeyboardMarkup:
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
    rows.append([InlineKeyboardButton(text=t(lang, "back"), callback_data=f"{SETTINGS_PREFIX}back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def models_text(current: ModelSpec, lang: Lang = DEFAULT_LANG) -> str:
    return t(lang, "models.text", label=current.label, badges=t(lang, "badges"))


def settings_keyboard(
    options: AgentOptions, model: ModelSpec, *, pickable: bool, lang: Lang = DEFAULT_LANG
) -> InlineKeyboardMarkup:
    efforts = [
        InlineKeyboardButton(
            text=t(lang, f"effort.{level}"),
            callback_data=f"{SETTINGS_PREFIX}effort:{level}",
            style="success" if options.effort == level else None,
        )
        for level in EFFORTS
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
            [_flag("show_thinking", options.show_thinking, lang)],
            [_flag("web", options.web, lang), _flag("code", options.code, lang)],
            [
                InlineKeyboardButton(
                    text=t(lang, "style.troll" if options.style == "troll" else "style.normal"),
                    callback_data=f"{SETTINGS_PREFIX}style",
                )
            ],
        ]
    )


def settings_text(options: AgentOptions, model: ModelSpec, lang: Lang = DEFAULT_LANG) -> str:
    label = f"{model.label} {model_badges(model)}".strip()
    return t(lang, "settings.text", model=label, effort=t(lang, f"effort.{options.effort}"))

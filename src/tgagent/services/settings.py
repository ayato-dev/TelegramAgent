from collections.abc import Collection, Sequence
from dataclasses import replace
from typing import Any, get_args

from tgagent.agent.tools import AgentOptions, Style
from tgagent.config import Effort

EFFORTS: tuple[Effort, ...] = get_args(Effort)
STYLES: tuple[Style, ...] = get_args(Style)
FLAGS = ("show_thinking", "web", "code")


def options_from(
    saved: dict[str, Any], *, default_effort: Effort, models: Collection[str] = ()
) -> AgentOptions:
    options = AgentOptions(effort=default_effort)
    if saved.get("effort") in EFFORTS:
        options = replace(options, effort=saved["effort"])
    for flag in FLAGS:
        if isinstance(saved.get(flag), bool):
            options = replace(options, **{flag: saved[flag]})
    if saved.get("style") in STYLES:
        options = replace(options, style=saved["style"])
    if saved.get("model") in models:
        options = replace(options, model=saved["model"])
    return options


def toggle(options: AgentOptions, action: str, models: Sequence[str] = ()) -> dict[str, Any]:
    """Settings patch for a keyboard action: ``effort:<level>``, ``model:<position>`` or a flag to flip."""
    if action.startswith("model:"):
        position = action.removeprefix("model:")
        return {"model": models[int(position)]} if position.isdigit() and int(position) < len(models) else {}
    if action.startswith("effort:"):
        level = action.removeprefix("effort:")
        return {"effort": level} if level in EFFORTS else {}
    if action in FLAGS:
        return {action: not getattr(options, action)}
    if action == "style":
        return {"style": "normal" if options.style == "troll" else "troll"}
    return {}

from dataclasses import replace
from typing import Any, get_args

from tgagent.agent.tools import AgentOptions
from tgagent.config import Effort

EFFORTS: tuple[Effort, ...] = get_args(Effort)
FLAGS = ("show_thinking", "web", "code")


def options_from(saved: dict[str, Any], *, default_effort: Effort) -> AgentOptions:
    options = AgentOptions(effort=default_effort)
    if saved.get("effort") in EFFORTS:
        options = replace(options, effort=saved["effort"])
    for flag in FLAGS:
        if isinstance(saved.get(flag), bool):
            options = replace(options, **{flag: saved[flag]})
    return options


def toggle(options: AgentOptions, action: str) -> dict[str, Any]:
    """Settings patch for a keyboard action: ``effort:<level>`` or a flag name to flip."""
    if action.startswith("effort:"):
        level = action.removeprefix("effort:")
        return {"effort": level} if level in EFFORTS else {}
    if action in FLAGS:
        return {action: not getattr(options, action)}
    return {}

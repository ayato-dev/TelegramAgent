from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from tgagent.context.formatting import render_message
from tgagent.context.media import MediaService
from tgagent.domain import NormalizedMessage
from tgagent.storage.repos import Content


@dataclass(frozen=True, slots=True)
class TurnInput:
    message: NormalizedMessage
    context: Sequence[NormalizedMessage] = ()
    environment: str | None = None


class _Blocks:
    """Accumulates content blocks, merging adjacent text into one block."""

    def __init__(self) -> None:
        self.items: Content = []

    def text(self, value: str) -> None:
        if self.items and self.items[-1]["type"] == "text":
            self.items[-1]["text"] += "\n" + value
        else:
            self.items.append({"type": "text", "text": value})

    def extend(self, blocks: list[dict[str, Any]]) -> None:
        self.items.extend(blocks)


class ContentBuilder:
    """Renders a trigger message (plus the messages it replies to) into one Claude user turn."""

    def __init__(self, media: MediaService, tz: ZoneInfo) -> None:
        self._media = media
        self._tz = tz

    async def build(self, turn: TurnInput, *, include_author: bool, code_enabled: bool) -> Content:
        blocks = _Blocks()
        if turn.environment:
            blocks.text(turn.environment)
        if turn.context:
            blocks.text("<context>")
            for message in turn.context:
                await self._add(blocks, message, include_author=True, code_enabled=code_enabled)
            blocks.text("</context>")
        await self._add(blocks, turn.message, include_author=include_author, code_enabled=code_enabled)
        return blocks.items

    async def _add(
        self, blocks: _Blocks, message: NormalizedMessage, *, include_author: bool, code_enabled: bool
    ) -> None:
        part = None
        if message.media:
            part = await self._media.describe(
                message.media, code_enabled=code_enabled, user_id=message.sender_id, chat_id=message.chat_id
            )
        body = part.body if part else None
        blocks.text(render_message(message, self._tz, include_author=include_author, body=body))
        if part:
            blocks.extend(part.blocks)

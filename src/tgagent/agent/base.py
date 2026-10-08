"""What every model adapter provides to the turn service."""

from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol

from tgagent.agent.events import AgentEvent
from tgagent.agent.models import ModelSpec
from tgagent.agent.pricing import TurnUsage
from tgagent.agent.tools import AgentOptions, ToolContext
from tgagent.context.builder import TurnInput
from tgagent.storage.repos import Content, NodeRecord


class LLMRunner(Protocol):
    spec: ModelSpec

    async def encode_user(self, turn: TurnInput, *, include_author: bool, code_enabled: bool) -> Content:
        """The user turn in this provider's native content format, stored as the user node."""
        ...

    def run(
        self,
        path: Sequence[NodeRecord],
        options: AgentOptions,
        ctx: ToolContext,
        *,
        container_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """One agent turn over the root-to-leaf ``path``; the last event is a TurnResult."""
        ...

    async def complete(self, prompt: str, *, max_tokens: int) -> tuple[str, TurnUsage]:
        """A cheap single request without tools, e.g. for topic titles."""
        ...


def summarize_tool(payload: Any) -> str:
    """Short status line for a tool call: its query, URL or first line of code."""
    args = payload if isinstance(payload, dict) else {}
    for key in ("query", "url", "command", "path", "code"):
        value = args.get(key)
        if isinstance(value, str) and value:
            first_line = value.strip().splitlines()[0]
            return first_line[:80]
    return ""

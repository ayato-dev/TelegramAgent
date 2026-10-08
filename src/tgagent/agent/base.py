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


def as_text_block(block: dict[str, Any]) -> dict[str, Any]:
    """A client-side compaction summary is replayed as plain text to the model."""
    if block.get("type") == "compaction":
        return {
            "type": "text",
            "text": f"<conversation_summary>\n{block['content']}\n</conversation_summary>",
        }
    return block


def plain_text(content: Content) -> str:
    """Visible text of a stored node in any provider's format; tool traffic and thoughts are skipped."""
    parts: list[str] = []
    for block in content:
        kind = block.get("type")
        if kind == "text" or (kind is None and "text" in block and not block.get("thought")):
            parts.append(block["text"])
        elif kind == "compaction":
            parts.append(block["content"])
        elif kind == "message":
            parts += [part["text"] for part in block["content"] if part.get("type") == "output_text"]
        elif block.get("role") == "assistant" and isinstance(block.get("content"), str):
            parts.append(block["content"])
    return "\n".join(part for part in parts if part)


def summarize_tool(payload: Any) -> str:
    """Short status line for a tool call: its query, URL or first line of code."""
    args = payload if isinstance(payload, dict) else {}
    for key in ("query", "url", "command", "path", "code"):
        value = args.get(key)
        if isinstance(value, str) and value:
            first_line = value.strip().splitlines()[0]
            return first_line[:80]
    return ""

import logging
import time
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types.beta import BetaMessage

from tgagent.agent.events import (
    AgentEvent,
    ContainerInfo,
    FileProduced,
    TextDelta,
    ThinkingDelta,
    ToolStarted,
    TurnResult,
)
from tgagent.agent.pricing import TurnUsage
from tgagent.agent.prompt import SYSTEM_PROMPT
from tgagent.agent.tools import AgentOptions, ToolContext, ToolRegistry, server_tool_specs
from tgagent.storage.repos import Content, Role

log = logging.getLogger(__name__)

BETAS = ["compact-2026-01-12", "thinking-binding-controls-2026-08-01"]
MAX_PAUSE_CONTINUATIONS = 5
TRUNCATED = {"max_tokens", "model_context_window_exceeded", "refusal", "pause_turn"}


def _summarize_tool(name: str, payload: Any) -> str:
    args = payload if isinstance(payload, dict) else {}
    for key in ("query", "url", "command", "path", "code"):
        value = args.get(key)
        if isinstance(value, str) and value:
            first_line = value.strip().splitlines()[0]
            return first_line[:80]
    return ""


def _map_stream_event(event: Any) -> AgentEvent | None:
    match event.type:
        case "text":
            return TextDelta(event.text)
        case "thinking":
            return ThinkingDelta(event.thinking)
        case "content_block_start" if event.content_block.type == "compaction":
            return ToolStarted("compaction", "")
        case "content_block_stop" if event.content_block.type == "server_tool_use":
            block = event.content_block
            return ToolStarted(block.name, _summarize_tool(block.name, block.input))
    return None


def _visible_text(content: Content) -> str:
    return "".join(block["text"] for block in content if block.get("type") == "text")


def _drop_unanswered_tool_uses(content: Content) -> Content:
    answered = {block["tool_use_id"] for block in content if "tool_use_id" in block}
    return [
        block
        for block in content
        if not (
            block.get("type") == "tool_use"
            or (block.get("type") == "server_tool_use" and block.get("id") not in answered)
        )
    ]


def _output_file_ids(content: Content) -> list[str]:
    ids: list[str] = []
    for block in content:
        result = block.get("content")
        if not isinstance(result, dict):
            continue
        for item in result.get("content") or []:
            if (
                isinstance(item, dict)
                and item.get("file_id")
                and str(item.get("type", "")).endswith("_output")
            ):
                ids.append(item["file_id"])
    return ids


class AgentRunner:
    """Runs one agent turn: streams Claude, executes client tools, yields events, then a TurnResult."""

    def __init__(
        self,
        client: AsyncAnthropic,
        *,
        model: str,
        max_tokens: int,
        compaction_trigger: int,
        registry: ToolRegistry,
        web_supported: bool,
        web_max_uses: int,
        max_rounds: int = 8,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._compaction_trigger = compaction_trigger
        self._registry = registry
        self._web_supported = web_supported
        self._web_max_uses = web_max_uses
        self.max_rounds = max_rounds

    @property
    def model(self) -> str:
        return self._model

    def _params(
        self,
        messages: list[dict[str, Any]],
        options: AgentOptions,
        container_id: str | None,
        *,
        tools_allowed: bool,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "tools": [
                *server_tool_specs(
                    options, web_supported=self._web_supported, web_max_uses=self._web_max_uses
                ),
                *self._registry.specs,
            ],
            "thinking": {
                "type": "adaptive",
                "display": "summarized" if options.show_thinking else "omitted",
                "block_binding": {"prefix_mismatch_behavior": "drop_block"},
            },
            "output_config": {"effort": options.effort},
            "context_management": {
                "edits": [
                    {
                        "type": "compact_20260112",
                        "trigger": {"type": "input_tokens", "value": self._compaction_trigger},
                    }
                ]
            },
            "cache_control": {"type": "ephemeral"},
            "betas": BETAS,
        }
        if container_id:
            params["container"] = container_id
        if not tools_allowed:
            params["tool_choice"] = {"type": "none"}
        return params

    async def _download_files(self, content: Content) -> AsyncIterator[FileProduced]:
        for file_id in _output_file_ids(content):
            try:
                meta = await self._client.files.retrieve_metadata(file_id)
                response = await self._client.files.download(file_id)
                data = await response.read()
            except Exception:
                log.warning("could not download code execution output %s", file_id, exc_info=True)
                continue
            name = PurePosixPath(meta.filename or "").name or "file"
            yield FileProduced(name, meta.mime_type or "application/octet-stream", data)

    async def run(
        self,
        messages: list[dict[str, Any]],
        options: AgentOptions,
        ctx: ToolContext,
        *,
        container_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        history = list(messages)
        nodes: list[tuple[Role, Content]] = []
        usage = TurnUsage()
        texts: list[str] = []
        thinking: list[str] = []
        container: ContainerInfo | None = None
        pending: Content | None = None
        pauses = 0
        rounds = 0
        message: BetaMessage | None = None

        while True:
            tools_allowed = rounds < self.max_rounds
            request = history + ([{"role": "assistant", "content": pending}] if pending else [])
            if texts and pending is None:
                yield TextDelta("\n\n")
            params = self._params(request, options, container_id, tools_allowed=tools_allowed)
            started = time.monotonic()
            first_event: float | None = None
            async with self._client.beta.messages.stream(**params) as stream:
                headers = time.monotonic() - started
                async for raw in stream:
                    if first_event is None:
                        first_event = time.monotonic() - started
                    event = _map_stream_event(raw)
                    if isinstance(event, ThinkingDelta):
                        thinking.append(event.text)
                    if event is not None:
                        yield event
                message = await stream.get_final_message()
            log.info(
                "claude %s: headers=%.2fs first_event=%.2fs total=%.2fs stop=%s in=%d cache_read=%d out=%d",
                message.id,
                headers,
                first_event or 0.0,
                time.monotonic() - started,
                message.stop_reason,
                message.usage.input_tokens,
                message.usage.cache_read_input_tokens or 0,
                message.usage.output_tokens,
            )

            usage.add(message.usage)
            if message.container:
                container = ContainerInfo(message.container.id, message.container.expires_at)
                container_id = message.container.id
            content: Content = [block.to_dict(mode="json") for block in message.content]
            async for produced in self._download_files(content):
                yield produced
            if pending is not None:
                content = pending + content
                pending = None

            if message.stop_reason == "pause_turn" and pauses < MAX_PAUSE_CONTINUATIONS:
                pauses += 1
                pending = content
                continue

            if message.stop_reason in TRUNCATED or (message.stop_reason == "tool_use" and not tools_allowed):
                content = _drop_unanswered_tool_uses(content)
            if content:
                nodes.append(("assistant", content))
                history.append({"role": "assistant", "content": content})
                if text := _visible_text(content):
                    texts.append(text)

            if message.stop_reason != "tool_use" or not tools_allowed:
                break

            results: Content = []
            for block in message.content:
                if block.type != "tool_use":
                    continue
                yield ToolStarted(block.name, _summarize_tool(block.name, block.input))
                outcome = await self._registry.execute(block.name, dict(block.input), ctx)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": outcome.content,
                        "is_error": outcome.is_error,
                    }
                )
            nodes.append(("user", results))
            history.append({"role": "user", "content": results})
            rounds += 1

        yield TurnResult(
            nodes=nodes,
            text="\n\n".join(texts),
            thinking="".join(thinking),
            usage=usage,
            container=container,
            stop_reason=message.stop_reason or "end_turn",
            refused=message.stop_reason == "refusal",
        )

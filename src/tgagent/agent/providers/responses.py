"""OpenAI Responses API: web search, code interpreter, image generation, reasoning summaries.

Requests are stateless (``store=False``). Earlier turns are replayed as plain text; the current
turn's output items, including encrypted reasoning, are passed back while its tool loop runs.
"""

import base64
import json
import logging
import mimetypes
import time
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

from openai import AsyncOpenAI, omit
from openai.types.responses import Response

from tgagent.agent.base import as_text_block, summarize_tool
from tgagent.agent.events import AgentEvent, FileProduced, TextDelta, ThinkingDelta, ToolStarted, TurnResult
from tgagent.agent.models import ModelSpec
from tgagent.agent.pricing import TurnUsage
from tgagent.agent.prompt import system_prompt
from tgagent.agent.tools import AgentOptions, ToolContext, ToolRegistry
from tgagent.context.builder import ContentBuilder, TurnInput
from tgagent.storage.repos import Content, NodeRecord, Role

log = logging.getLogger(__name__)

IMAGE_TOOL = {
    "type": "image_generation",
    "model": "gpt-image-2",
    "size": "1024x1024",
    "quality": "medium",
    "output_format": "png",
}
# gpt-image-2 at medium quality, 1024x1024; containers are billed per 20-minute session.
IMAGE_PRICE = Decimal("0.04")
CONTAINER_PRICE = Decimal("0.03")
TOOL_ITEMS = {"web_search_call": "web_search", "code_interpreter_call": "code_interpreter"}


def function_tools(registry: ToolRegistry) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": spec["name"],
            "description": spec["description"],
            "parameters": spec["input_schema"],
            "strict": True,
        }
        for spec in registry.specs
    ]


def _user_content(content: Content) -> list[dict[str, Any]]:
    blocks = [as_text_block(block) for block in content]
    return [{"type": "input_text", "text": b["text"]} if b.get("type") == "text" else b for b in blocks]


def _text(content: Content) -> str:
    parts: list[str] = []
    for item in content:
        if item.get("type") == "text":
            parts.append(item["text"])
        elif item.get("type") == "message":
            parts += [part["text"] for part in item["content"] if part.get("type") == "output_text"]
    return "\n\n".join(part for part in parts if part)


def _is_tool_output(content: Content) -> bool:
    return bool(content) and all(item.get("type") == "function_call_output" for item in content)


def replay(path: Sequence[NodeRecord]) -> list[dict[str, Any]]:
    """Earlier turns as user content and assistant text; tool traffic stays in its own turn."""
    items: list[dict[str, Any]] = []
    for node in path:
        if node.role == "user" and not _is_tool_output(node.content):
            items.append({"role": "user", "content": _user_content(node.content)})
        elif node.role == "assistant" and (text := _text(node.content)):
            items.append({"role": "assistant", "content": text})
    return items


def _stored(item: dict[str, Any]) -> dict[str, Any]:
    """Generated images go to the chat, not into the database."""
    if item.get("type") == "image_generation_call":
        return {key: value for key, value in item.items() if key != "result"}
    return item


def _refused(output: list[dict[str, Any]]) -> bool:
    return any(
        part.get("type") == "refusal"
        for item in output
        if item.get("type") == "message"
        for part in item["content"]
    )


def _container_files(output: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        annotation
        for item in output
        if item.get("type") == "message"
        for part in item["content"]
        if part.get("type") == "output_text"
        for annotation in part.get("annotations", [])
        if annotation.get("type") == "container_file_citation"
    ]


def _map_event(event: Any) -> AgentEvent | None:
    match event.type:
        case "response.output_text.delta":
            return TextDelta(event.delta)
        case "response.reasoning_summary_text.delta":
            return ThinkingDelta(event.delta)
        case "response.output_item.added" if event.item.type == "image_generation_call":
            return ToolStarted("image_generation", "")
        case "response.output_item.added" if event.item.type == "code_interpreter_call":
            return ToolStarted("code_interpreter", summarize_tool({"code": event.item.code or ""}))
        case "response.output_item.done" if event.item.type == "web_search_call":
            action = event.item.action
            return ToolStarted("web_search", getattr(action, "query", None) or "")
    return None


class ResponsesRunner:
    """Runs one agent turn against an OpenAI model."""

    def __init__(
        self,
        client: AsyncOpenAI,
        spec: ModelSpec,
        *,
        builder: ContentBuilder,
        registry: ToolRegistry,
        max_rounds: int = 8,
    ) -> None:
        self.spec = spec
        self._client = client
        self._builder = builder
        self._registry = registry
        self.max_rounds = max_rounds

    async def encode_user(self, turn: TurnInput, *, include_author: bool, code_enabled: bool) -> Content:
        return await self._builder.build(turn, include_author=include_author, code_enabled=code_enabled)

    async def complete(
        self, prompt: str, *, max_tokens: int, system: str | None = None
    ) -> tuple[str, TurnUsage]:
        result = await self._client.responses.create(
            model=self.spec.model_id,
            input=prompt,
            max_output_tokens=max_tokens,
            reasoning={"effort": "low"},
            store=False,
            instructions=system or omit,
        )
        usage = TurnUsage()
        self._add_usage(usage, result)
        return result.output_text, usage

    def _tools(self, options: AgentOptions) -> list[dict[str, Any]]:
        tools = function_tools(self._registry)
        if options.web and self.spec.web:
            tools.append({"type": "web_search"})
        if options.code and self.spec.code:
            tools.append({"type": "code_interpreter", "container": {"type": "auto", "memory_limit": "1g"}})
        if self.spec.image_out:
            tools.append(IMAGE_TOOL)
        return tools

    def _params(
        self, history: list[dict[str, Any]], options: AgentOptions, *, tools_allowed: bool
    ) -> dict[str, Any]:
        reasoning: dict[str, Any] = {"effort": options.effort}
        if options.show_thinking:
            reasoning["summary"] = "auto"
        params: dict[str, Any] = {
            "model": self.spec.model_id,
            "instructions": system_prompt(options, self.spec),
            "input": list(history),
            "tools": self._tools(options),
            "reasoning": reasoning,
            "max_output_tokens": self.spec.max_output,
            "include": ["reasoning.encrypted_content"],
            "store": False,
            "stream": True,
        }
        if not tools_allowed:
            params["tool_choice"] = "none"
        return params

    @staticmethod
    def _add_usage(usage: TurnUsage, result: Response) -> None:
        if result.usage is None:
            return
        cached = result.usage.input_tokens_details.cached_tokens
        usage.add_iteration(result.usage.input_tokens - cached, result.usage.output_tokens, cached, 0)

    async def _files(self, output: list[dict[str, Any]]) -> AsyncIterator[FileProduced]:
        for item in output:
            if item.get("type") == "image_generation_call" and item.get("result"):
                yield FileProduced("image.png", "image/png", base64.b64decode(item["result"]))
        for citation in _container_files(output):
            try:
                content = await self._client.containers.files.content.retrieve(
                    citation["file_id"], container_id=citation["container_id"]
                )
            except Exception:
                log.warning("could not download container file %s", citation["file_id"], exc_info=True)
                continue
            name = citation["filename"]
            yield FileProduced(
                name, mimetypes.guess_type(name)[0] or "application/octet-stream", content.content
            )

    async def _request(
        self, params: dict[str, Any], usage: TurnUsage
    ) -> AsyncIterator[AgentEvent | Response]:
        started = time.monotonic()
        final: Response | None = None
        async with await self._client.responses.create(**params) as stream:
            async for event in stream:
                if event.type in ("response.completed", "response.incomplete"):
                    final = event.response
                elif event.type in ("response.failed", "error"):
                    error = getattr(getattr(event, "response", None), "error", None) or event
                    raise RuntimeError(f"OpenAI request failed: {getattr(error, 'message', error)}")
                elif (mapped := _map_event(event)) is not None:
                    yield mapped
        if final is None:
            raise RuntimeError("OpenAI stream ended without a response")
        self._add_usage(usage, final)
        log.info(
            "openai %s: total=%.2fs status=%s in=%d out=%d",
            final.id,
            time.monotonic() - started,
            final.status,
            final.usage.input_tokens if final.usage else 0,
            final.usage.output_tokens if final.usage else 0,
        )
        yield final

    async def run(
        self,
        path: Sequence[NodeRecord],
        options: AgentOptions,
        ctx: ToolContext,
        *,
        container_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        history = replay(path)
        nodes: list[tuple[Role, Content]] = []
        usage = TurnUsage()
        texts: list[str] = []
        thinking: list[str] = []
        containers: set[str] = set()
        rounds = 0
        final: Response | None = None

        while True:
            tools_allowed = rounds < self.max_rounds
            if texts:
                yield TextDelta("\n\n")
            async for event in self._request(
                self._params(history, options, tools_allowed=tools_allowed), usage
            ):
                if isinstance(event, Response):
                    final = event
                    continue
                if isinstance(event, ThinkingDelta):
                    thinking.append(event.text)
                yield event
            assert final is not None

            output = [item.model_dump(mode="json", exclude_none=True) for item in final.output]
            async for produced in self._files(output):
                yield produced
            usage.web_search_requests += sum(item.get("type") == "web_search_call" for item in output)
            usage.tool_cost += IMAGE_PRICE * sum(bool(item.get("result")) for item in output)
            for item in output:
                if item.get("type") == "code_interpreter_call" and item["container_id"] not in containers:
                    containers.add(item["container_id"])
                    usage.tool_cost += CONTAINER_PRICE

            calls = [item for item in output if item.get("type") == "function_call"]
            if final.status != "completed" or not tools_allowed:
                output = [item for item in output if item.get("type") != "function_call"]
                calls = []
            if output:
                nodes.append(("assistant", [_stored(item) for item in output]))
                history.extend(output)
                if text := _text(output):
                    texts.append(text)
            if not calls:
                break

            results: Content = []
            for call in calls:
                args = json.loads(call["arguments"] or "{}")
                yield ToolStarted(call["name"], summarize_tool(args))
                outcome = await self._registry.execute(call["name"], args, ctx)
                results.append(
                    {"type": "function_call_output", "call_id": call["call_id"], "output": outcome.content}
                )
            nodes.append(("user", results))
            history.extend(results)
            rounds += 1

        reason = final.incomplete_details.reason if final.incomplete_details else None
        refused = _refused(output)
        yield TurnResult(
            nodes=nodes,
            text="\n\n".join(texts),
            thinking="".join(thinking),
            usage=usage,
            container=None,
            stop_reason="refusal"
            if refused
            else "max_tokens"
            if reason == "max_output_tokens"
            else "end_turn",
            refused=refused,
        )

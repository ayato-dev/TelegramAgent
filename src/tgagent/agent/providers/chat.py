"""Chat Completions providers: DeepSeek and Groq (gpt-oss with built-in browser search and Python).

History is replayed message by message as the model produced it. DeepSeek requires the
``reasoning_content`` of every earlier assistant message once tools are present; Groq gets
none back to keep requests inside the free plan's token budget.
"""

import base64
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletionMessageParam

from tgagent.agent.base import as_text_block, summarize_tool
from tgagent.agent.events import AgentEvent, FileProduced, TextDelta, ThinkingDelta, ToolStarted, TurnResult
from tgagent.agent.models import ModelSpec
from tgagent.agent.pricing import TurnUsage
from tgagent.agent.prompt import system_prompt
from tgagent.agent.tools import AgentOptions, ToolContext, ToolOutcome, ToolRegistry
from tgagent.config import Effort
from tgagent.context.builder import ContentBuilder, TurnInput
from tgagent.storage.repos import Content, NodeRecord, Role

log = logging.getLogger(__name__)

# Groq gpt-oss browses in steps: search, then open and find inside pages.
BUILT_IN_NAMES = {
    "browser.search": "web_search",
    "browser.open": "web_fetch",
    "browser.find": "web_fetch",
    "python": "code_execution",
}


@dataclass(frozen=True, slots=True)
class ChatProfile:
    reasoning_field: str
    keep_reasoning: bool
    efforts: Mapping[Effort, str]
    # Extra request fields for complete(): as little thinking as the provider allows.
    quick: Mapping[str, Any]
    # gpt-oss marks browsed sources like 【2†L30-L34】, which mean nothing to the reader.
    strip_citations: bool = False


class CitationFilter:
    """Drops 【…】 source markers from streamed text, even when split across chunks."""

    def __init__(self) -> None:
        self._inside = False

    def __call__(self, text: str) -> str:
        kept: list[str] = []
        for char in text:
            if self._inside:
                self._inside = char != "】"
            elif char == "【":
                self._inside = True
            else:
                kept.append(char)
        return "".join(kept)


PROFILES = {
    "deepseek": ChatProfile(
        "reasoning_content",
        keep_reasoning=True,
        efforts={"low": "low", "medium": "high", "high": "max"},
        quick={"extra_body": {"thinking": {"type": "disabled"}}},
    ),
    "groq": ChatProfile(
        "reasoning",
        keep_reasoning=False,
        efforts={"low": "low", "medium": "medium", "high": "high"},
        quick={"reasoning_effort": "low"},
        strip_citations=True,
    ),
}


def function_tools(registry: ToolRegistry) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["input_schema"],
            },
        }
        for spec in registry.specs
    ]


def _user_message(content: Content) -> dict[str, Any]:
    parts = [as_text_block(block) for block in content]
    if all(part.get("type") == "text" for part in parts):
        return {"role": "user", "content": "\n".join(part["text"] for part in parts)}
    return {"role": "user", "content": parts}


def _merge_user(previous: dict[str, Any], message: dict[str, Any]) -> None:
    """Two user messages in a row (a summary and the next message) become one."""
    first, second = previous["content"], message["content"]
    if isinstance(first, str) and isinstance(second, str):
        previous["content"] = f"{first}\n{second}"
        return
    as_parts = [first] if isinstance(first, str) else first
    previous["content"] = [
        *({"type": "text", "text": p} if isinstance(p, str) else p for p in as_parts),
        *([{"type": "text", "text": second}] if isinstance(second, str) else second),
    ]


class ChatRunner:
    """Runs one agent turn against an OpenAI-compatible Chat Completions provider."""

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
        self._profile = PROFILES[spec.provider]
        self._client = client
        self._builder = builder
        self._registry = registry
        self.max_rounds = max_rounds

    async def encode_user(self, turn: TurnInput, *, include_author: bool, code_enabled: bool) -> Content:
        return await self._builder.build(turn, include_author=include_author, code_enabled=code_enabled)

    async def complete(
        self, prompt: str, *, max_tokens: int, system: str | None = None
    ) -> tuple[str, TurnUsage]:
        messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": prompt}]
        if system:
            messages.insert(0, {"role": "system", "content": system})
        response = await self._client.chat.completions.create(
            model=self.spec.model_id,
            messages=messages,
            max_tokens=max_tokens,
            **self._profile.quick,
        )
        usage = TurnUsage()
        self._add_usage(usage, response.usage)
        return response.choices[0].message.content or "", usage

    def _assistant(self, message: dict[str, Any], reasoning: str = "") -> dict[str, Any]:
        kept = {key: value for key, value in message.items() if key not in ("reasoning_content", "reasoning")}
        if self._profile.keep_reasoning:
            kept["reasoning_content"] = reasoning
        return kept

    def replay(self, path: Sequence[NodeRecord]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for node in path:
            if node.role == "user" and node.content and node.content[0].get("role") == "tool":
                messages.extend(node.content)
            elif node.role == "user":
                message = _user_message(node.content)
                if messages and messages[-1]["role"] == "user":
                    _merge_user(messages[-1], message)
                else:
                    messages.append(message)
            else:
                for item in node.content:
                    if item.get("role") == "assistant":
                        messages.append(self._assistant(item, item.get("reasoning_content") or ""))
                    elif item.get("type") == "text":
                        messages.append(self._assistant({"role": "assistant", "content": item["text"]}))
        return messages

    def _params(
        self, history: list[dict[str, Any]], options: AgentOptions, *, tools_allowed: bool
    ) -> dict[str, Any]:
        tools = function_tools(self._registry)
        if options.web and self.spec.web:
            tools.append({"type": "browser_search"})
        if options.code and self.spec.code:
            tools.append({"type": "code_interpreter"})
        params: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": [{"role": "system", "content": system_prompt(options, self.spec)}, *history],
            "max_tokens": self.spec.max_output,
            "reasoning_effort": self._profile.efforts[options.effort],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            params["tools"] = tools
            if not tools_allowed:
                params["tool_choice"] = "none"
        return params

    @staticmethod
    def _add_usage(usage: TurnUsage, reported: CompletionUsage | None) -> None:
        if reported is None:
            return
        details = reported.prompt_tokens_details
        cached = (
            (details.cached_tokens if details else None)
            or getattr(reported, "prompt_cache_hit_tokens", 0)
            or 0
        )
        usage.add_iteration(reported.prompt_tokens - cached, reported.completion_tokens, cached, 0)

    async def run(
        self,
        path: Sequence[NodeRecord],
        options: AgentOptions,
        ctx: ToolContext,
        *,
        container_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        history = self.replay(path)
        nodes: list[tuple[Role, Content]] = []
        usage = TurnUsage()
        texts: list[str] = []
        thinking: list[str] = []
        rounds = 0
        finish: str | None = None

        while True:
            tools_allowed = rounds < self.max_rounds
            if texts:
                yield TextDelta("\n\n")
            text: list[str] = []
            reasoning: list[str] = []
            calls: dict[int, dict[str, str]] = {}
            executed: dict[str, dict[str, Any]] = {}
            citations = CitationFilter() if self._profile.strip_citations else None
            reported: CompletionUsage | None = None
            started = time.monotonic()
            async with await self._client.chat.completions.create(
                **self._params(history, options, tools_allowed=tools_allowed)
            ) as stream:
                async for chunk in stream:
                    reported = chunk.usage or reported
                    for choice in chunk.choices:
                        delta = choice.delta
                        finish = choice.finish_reason or finish
                        if piece := getattr(delta, self._profile.reasoning_field, None):
                            reasoning.append(piece)
                            if options.show_thinking:
                                yield ThinkingDelta(piece)
                        if piece := (citations(delta.content or "") if citations else delta.content):
                            text.append(piece)
                            yield TextDelta(piece)
                        for fragment in delta.tool_calls or []:
                            entry = calls.setdefault(fragment.index, {"id": "", "name": "", "arguments": ""})
                            entry["id"] = fragment.id or entry["id"]
                            if fragment.function:
                                entry["name"] = fragment.function.name or entry["name"]
                                entry["arguments"] += fragment.function.arguments or ""
                        for tool in getattr(delta, "executed_tools", None) or []:
                            index = str(tool.get("index", len(executed)))
                            if index not in executed:
                                yield self._built_in(tool)
                            executed[index] = tool
            self._add_usage(usage, reported)
            usage.web_search_requests += sum(
                tool.get("name") == "browser.search" for tool in executed.values()
            )
            thinking += reasoning
            log.info(
                "%s %s: total=%.2fs finish=%s in=%d out=%d",
                self.spec.provider,
                self.spec.model_id,
                time.monotonic() - started,
                finish,
                reported.prompt_tokens if reported else 0,
                reported.completion_tokens if reported else 0,
            )
            for produced in self._charts(executed.values()):
                yield produced

            message: dict[str, Any] = {"role": "assistant", "content": "".join(text) or None}
            if self._profile.keep_reasoning:
                message["reasoning_content"] = "".join(reasoning)
            if finish == "length" or not tools_allowed:
                calls = {}
            if calls:
                message["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    }
                    for _, c in sorted(calls.items())
                ]
            if message["content"] or calls:
                nodes.append(("assistant", [message]))
                history.append(message)
                if message["content"]:
                    texts.append(message["content"])
            if not calls:
                break

            results: Content = []
            for entry in (c for _, c in sorted(calls.items())):
                try:
                    args = json.loads(entry["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = None
                yield ToolStarted(entry["name"], summarize_tool(args))
                outcome = (
                    await self._registry.execute(entry["name"], args, ctx)
                    if isinstance(args, dict)
                    else ToolOutcome("Ошибка: аргументы не являются корректным JSON", is_error=True)
                )
                results.append({"role": "tool", "tool_call_id": entry["id"], "content": outcome.content})
            nodes.append(("user", results))
            history.extend(results)
            rounds += 1

        refused = finish == "content_filter"
        yield TurnResult(
            nodes=nodes,
            text="\n\n".join(texts),
            thinking="".join(thinking),
            usage=usage,
            container=None,
            stop_reason="refusal" if refused else "max_tokens" if finish == "length" else "end_turn",
            refused=refused,
        )

    @staticmethod
    def _built_in(tool: dict[str, Any]) -> ToolStarted:
        name = str(tool.get("name") or tool.get("type") or "tool")
        try:
            args = json.loads(tool.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        return ToolStarted(BUILT_IN_NAMES.get(name, name), summarize_tool(args))

    @staticmethod
    def _charts(executed: Any) -> list[FileProduced]:
        return [
            FileProduced("chart.png", "image/png", base64.b64decode(result["png"]))
            for tool in executed
            for result in tool.get("code_results") or []
            if result.get("png")
        ]

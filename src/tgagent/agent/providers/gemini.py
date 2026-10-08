"""Google Gemini (generateContent): native audio/video/YouTube input, Google Search, URL context,
code execution and thought signatures.

Earlier turns are replayed as the user's parts and the model's visible text; the current turn
keeps every part as received (signatures included) while its function-calling loop runs.
"""

import base64
import logging
import mimetypes
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

from google import genai
from google.genai import types

from tgagent.agent.base import as_text_block, summarize_tool
from tgagent.agent.events import AgentEvent, FileProduced, TextDelta, ThinkingDelta, ToolStarted, TurnResult
from tgagent.agent.models import ModelSpec
from tgagent.agent.pricing import TurnUsage
from tgagent.agent.prompt import system_prompt
from tgagent.agent.tools import AgentOptions, ToolContext, ToolRegistry
from tgagent.context.builder import ContentBuilder, TurnInput
from tgagent.context.encoders import youtube_parts
from tgagent.storage.repos import Content, NodeRecord, Role

log = logging.getLogger(__name__)

# "medium" is left to the model's default level: not every Gemini model accepts MEDIUM.
THINKING_LEVELS = {"low": types.ThinkingLevel.LOW, "high": types.ThinkingLevel.HIGH}
REFUSALS = {
    types.FinishReason.SAFETY,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.SPII,
    types.FinishReason.RECITATION,
}


def _part(block: dict[str, Any]) -> dict[str, Any]:
    """Stored blocks are Gemini parts, except provider-neutral text and summaries."""
    block = as_text_block(block)
    return {"text": block["text"]} if block.get("type") == "text" else block


def _visible_text(parts: Content) -> str:
    return "".join(part["text"] for part in parts if "text" in part and not part.get("thought"))


def _is_tool_output(content: Content) -> bool:
    return bool(content) and all("function_response" in part for part in content)


def replay(path: Sequence[NodeRecord]) -> list[dict[str, Any]]:
    """Consecutive turns of one role (a summary and the next message) are merged into one."""
    contents: list[dict[str, Any]] = []
    for node in path:
        if node.role == "user" and not _is_tool_output(node.content):
            role, parts = "user", [_part(block) for block in node.content]
        elif node.role == "assistant" and (text := _visible_text([_part(block) for block in node.content])):
            role, parts = "model", [{"text": text}]
        else:
            continue
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})
    return contents


def _plain(part: dict[str, Any]) -> bool:
    return set(part) <= {"text", "thought"}


def _append(parts: list[dict[str, Any]], part: dict[str, Any]) -> None:
    """Join streamed text pieces; parts carrying signatures or tool data stay as received."""
    previous = parts[-1] if parts else None
    if previous and _plain(previous) and _plain(part) and previous.get("thought") == part.get("thought"):
        previous["text"] += part["text"]
    else:
        parts.append(dict(part))


def _map_part(part: types.Part) -> AgentEvent | None:
    if part.text:
        return ThinkingDelta(part.text) if part.thought else TextDelta(part.text)
    if part.executable_code is not None:
        return ToolStarted("code_execution", summarize_tool({"code": part.executable_code.code or ""}))
    if part.tool_call is not None and part.tool_call.tool_type == types.ToolType.GOOGLE_SEARCH_WEB:
        queries = (part.tool_call.args or {}).get("queries") or [""]
        return ToolStarted("web_search", summarize_tool({"query": queries[0]}))
    if part.tool_call is not None and part.tool_call.tool_type == types.ToolType.URL_CONTEXT:
        return ToolStarted("web_fetch", "")
    return None


class GeminiRunner:
    """Runs one agent turn against a Gemini model."""

    def __init__(
        self,
        client: genai.Client,
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
        content = await self._builder.build(turn, include_author=include_author, code_enabled=code_enabled)
        return content + youtube_parts(content)

    async def complete(self, prompt: str, *, max_tokens: int) -> tuple[str, TurnUsage]:
        response = await self._client.aio.models.generate_content(
            model=self.spec.model_id,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
            ),
        )
        usage = TurnUsage()
        self._add_usage(usage, response.usage_metadata)
        return response.text or "", usage

    def _tools(self, options: AgentOptions) -> list[types.Tool]:
        declarations = [
            types.FunctionDeclaration(
                name=spec["name"],
                description=spec["description"],
                parameters_json_schema=spec["input_schema"],
            )
            for spec in self._registry.specs
        ]
        tool = types.Tool(
            function_declarations=declarations or None,
            google_search=types.GoogleSearch() if options.web and self.spec.web else None,
            url_context=types.UrlContext() if options.web else None,
            code_execution=types.ToolCodeExecution() if options.code and self.spec.code else None,
        )
        return [tool] if tool.model_dump(exclude_none=True) else []

    def _config(self, options: AgentOptions, *, tools_allowed: bool) -> types.GenerateContentConfig:
        tools = self._tools(options)
        tool_config = None
        if tools:
            # Built-in tools next to function declarations need server-side invocations on.
            tool_config = types.ToolConfig(include_server_side_tool_invocations=True)
            if not tools_allowed:
                tool_config.function_calling_config = types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.NONE
                )
        return types.GenerateContentConfig(
            system_instruction=system_prompt(options, self.spec),
            max_output_tokens=self.spec.max_output,
            thinking_config=types.ThinkingConfig(
                thinking_level=THINKING_LEVELS.get(options.effort), include_thoughts=options.show_thinking
            ),
            tools=tools or None,
            tool_config=tool_config,
        )

    @staticmethod
    def _add_usage(usage: TurnUsage, meta: types.GenerateContentResponseUsageMetadata | None) -> None:
        if meta is None:
            return
        cached = meta.cached_content_token_count or 0
        prompt = (meta.prompt_token_count or 0) + (meta.tool_use_prompt_token_count or 0)
        output = (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)
        usage.add_iteration(prompt - cached, output, cached, 0)

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
        rounds = 0
        finish: types.FinishReason | None = None
        blocked = False

        while True:
            tools_allowed = rounds < self.max_rounds
            if texts:
                yield TextDelta("\n\n")
            parts: list[dict[str, Any]] = []
            meta: types.GenerateContentResponseUsageMetadata | None = None
            queries: list[str] = []
            started = time.monotonic()
            stream = await self._client.aio.models.generate_content_stream(
                model=self.spec.model_id,
                contents=[types.Content.model_validate(content) for content in history],
                config=self._config(options, tools_allowed=tools_allowed),
            )
            async for chunk in stream:
                meta = chunk.usage_metadata or meta
                if chunk.prompt_feedback and chunk.prompt_feedback.block_reason:
                    blocked = True
                for candidate in chunk.candidates or []:
                    finish = candidate.finish_reason or finish
                    if candidate.grounding_metadata and candidate.grounding_metadata.web_search_queries:
                        queries = candidate.grounding_metadata.web_search_queries
                    received = candidate.content.parts if candidate.content else None
                    for piece in received or []:
                        if (event := _map_part(piece)) is not None:
                            if isinstance(event, ThinkingDelta):
                                thinking.append(event.text)
                            yield event
                        _append(parts, piece.model_dump(mode="json", exclude_none=True))
            self._add_usage(usage, meta)
            usage.web_search_requests += len(queries)
            log.info(
                "gemini %s: total=%.2fs finish=%s in=%d out=%d",
                self.spec.model_id,
                time.monotonic() - started,
                finish,
                meta.prompt_token_count or 0 if meta else 0,
                meta.candidates_token_count or 0 if meta else 0,
            )

            for index, blob in enumerate(part["inline_data"] for part in parts if "inline_data" in part):
                data = base64.b64decode(blob["data"])
                mime = blob.get("mime_type", "image/png")
                name = "chart" + (f"-{index + 1}" if index else "") + (mimetypes.guess_extension(mime) or "")
                yield FileProduced(name, mime, data)

            calls = [part for part in parts if "function_call" in part]
            if finish != types.FinishReason.STOP or not tools_allowed:
                parts = [part for part in parts if "function_call" not in part]
                calls = []
            if parts:
                nodes.append(("assistant", parts))
                history.append({"role": "model", "parts": parts})
                if text := _visible_text(parts):
                    texts.append(text)
            if not calls:
                break

            results: Content = []
            for call_part in calls:
                call = call_part["function_call"]
                args = call.get("args") or {}
                yield ToolStarted(call["name"], summarize_tool(args))
                outcome = await self._registry.execute(call["name"], args, ctx)
                response = {
                    "id": call.get("id"),
                    "name": call["name"],
                    "response": {"result": outcome.content},
                }
                results.append({"function_response": {k: v for k, v in response.items() if v is not None}})
            nodes.append(("user", results))
            history.append({"role": "user", "parts": results})
            rounds += 1

        refused = blocked or finish in REFUSALS
        yield TurnResult(
            nodes=nodes,
            text="\n\n".join(texts),
            thinking="".join(thinking),
            usage=usage,
            container=None,
            stop_reason="refusal"
            if refused
            else "max_tokens"
            if finish == types.FinishReason.MAX_TOKENS
            else "end_turn",
            refused=refused,
        )

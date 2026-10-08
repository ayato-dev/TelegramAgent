import base64
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

from google import genai
from google.genai import types

from tgagent.agent.events import AgentEvent, FileProduced, TextDelta, ThinkingDelta, ToolStarted, TurnResult
from tgagent.agent.models import CATALOG, ModelSpec
from tgagent.agent.prompt import system_prompt
from tgagent.agent.providers.gemini import GeminiRunner
from tgagent.agent.tools import AgentOptions, ToolContext, ToolOutcome, ToolRegistry
from tgagent.context.builder import ContentBuilder, TurnInput
from tgagent.storage.repos import NodeRecord

CTX = ToolContext(chat_id=1, thread_id=None, user_id=1, chat_kind="private", message_id=10)
# As resolve_model returns it on the free tier: no Google Search.
FLASH = replace(CATALOG["gemini:gemini-3.8-flash"], context_trigger=100_000, max_output=16_000, web=False)
QUESTION = NodeRecord(9, 1, None, "user", [{"type": "text", "text": "hi"}], False)
SIG = base64.b64encode(b"signature").decode()


def chunk(
    *parts: dict[str, Any],
    finish: str | None = None,
    usage: dict[str, Any] | None = None,
    queries: list[str] | None = None,
    blocked: bool = False,
) -> types.GenerateContentResponse:
    candidate: dict[str, Any] = {"content": {"role": "model", "parts": list(parts)}, "finish_reason": finish}
    if queries:
        candidate["grounding_metadata"] = {"web_search_queries": queries}
    data: dict[str, Any] = {"candidates": [] if blocked else [candidate], "usage_metadata": usage}
    if blocked:
        data["prompt_feedback"] = {"block_reason": "SAFETY"}
    return types.GenerateContentResponse.model_validate(data)


class FakeModels:
    def __init__(self, script: list[Sequence[types.GenerateContentResponse]]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.completions: list[types.GenerateContentResponse] = []

    async def generate_content_stream(self, **params: Any) -> AsyncIterator[types.GenerateContentResponse]:
        self.calls.append(params)
        chunks = self._script.pop(0)

        async def iterate() -> AsyncIterator[types.GenerateContentResponse]:
            for item in chunks:
                yield item

        return iterate()

    async def generate_content(self, **params: Any) -> types.GenerateContentResponse:
        self.calls.append(params)
        return self.completions.pop(0)


class FakeGemini:
    def __init__(self, script: list[Sequence[types.GenerateContentResponse]]) -> None:
        self.models = FakeModels(script)
        self.aio = SimpleNamespace(models=self.models)

    def as_client(self) -> genai.Client:
        return cast(genai.Client, self)


class FakeBuilder:
    async def build(
        self, turn: TurnInput, *, include_author: bool, code_enabled: bool
    ) -> list[dict[str, Any]]:
        return [{"type": "text", "text": turn.message.text or ""}]


def runner(fake: FakeGemini, registry: ToolRegistry | None = None, spec: ModelSpec = FLASH) -> GeminiRunner:
    return GeminiRunner(
        fake.as_client(),
        spec,
        builder=cast(ContentBuilder, FakeBuilder()),
        registry=registry or ToolRegistry({}),
    )


async def collect(
    agent: GeminiRunner, options: AgentOptions | None = None, path: list[NodeRecord] | None = None
) -> list[AgentEvent]:
    return [event async for event in agent.run(path or [QUESTION], options or AgentOptions(), CTX)]


def result_of(events: list[AgentEvent]) -> TurnResult:
    last = events[-1]
    assert isinstance(last, TurnResult)
    return last


def config(fake: FakeGemini, index: int = 0) -> types.GenerateContentConfig:
    value = fake.models.calls[index]["config"]
    assert isinstance(value, types.GenerateContentConfig)
    return value


def contents(fake: FakeGemini, index: int = 0) -> list[dict[str, Any]]:
    return [
        content.model_dump(mode="json", exclude_none=True) for content in fake.models.calls[index]["contents"]
    ]


async def echo(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    return ToolOutcome("pong")


async def test_text_turn_streams_thoughts_and_text_and_keeps_signatures() -> None:
    fake = FakeGemini(
        [
            [
                chunk({"text": "hmm", "thought": True}),
                chunk({"text": "Hel"}),
                chunk({"text": "lo", "thought_signature": SIG}, finish="STOP"),
            ]
        ]
    )

    events = await collect(runner(fake), AgentOptions(show_thinking=True))

    assert events[:3] == [ThinkingDelta("hmm"), TextDelta("Hel"), TextDelta("lo")]
    result = result_of(events)
    assert result.nodes == [
        (
            "assistant",
            [{"text": "hmm", "thought": True}, {"text": "Hel"}, {"text": "lo", "thought_signature": SIG}],
        )
    ]
    assert (result.text, result.stop_reason, result.refused) == ("Hello", "end_turn", False)


async def test_adjacent_plain_text_chunks_are_merged() -> None:
    fake = FakeGemini([[chunk({"text": "Hel"}), chunk({"text": "lo"}, finish="STOP")]])

    result = result_of(await collect(runner(fake)))

    assert result.nodes == [("assistant", [{"text": "Hello"}])]


async def test_request_config_follows_design() -> None:
    fake = FakeGemini([[chunk({"text": "ok"}, finish="STOP")]])
    paid = replace(FLASH, web=True)
    options = AgentOptions(effort="high", show_thinking=True)

    await collect(runner(fake, ToolRegistry({"set_reminder": echo}), spec=paid), options)

    params = fake.models.calls[0]
    cfg = config(fake)
    assert params["model"] == "gemini-3.8-flash"
    assert cfg.system_instruction == system_prompt(options)
    assert cfg.max_output_tokens == 16_000
    assert cfg.thinking_config == types.ThinkingConfig(
        thinking_level=types.ThinkingLevel.HIGH, include_thoughts=True
    )
    assert cfg.tool_config is not None and cfg.tool_config.include_server_side_tool_invocations
    tool = cfg.tools[0] if cfg.tools else None
    assert isinstance(tool, types.Tool)
    assert [d.name for d in tool.function_declarations or []] == ["set_reminder"]
    assert None not in (tool.google_search, tool.url_context, tool.code_execution)
    assert contents(fake) == [{"role": "user", "parts": [{"text": "hi"}]}]


async def test_free_tier_has_no_google_search_and_medium_effort_uses_the_default() -> None:
    fake = FakeGemini([[chunk({"text": "ok"}, finish="STOP")]])

    await collect(runner(fake), AgentOptions(web=True, code=False))

    cfg = config(fake)
    tool = cfg.tools[0] if cfg.tools else None
    assert isinstance(tool, types.Tool)
    assert tool.google_search is None and tool.code_execution is None
    assert tool.url_context is not None
    assert cfg.thinking_config == types.ThinkingConfig(include_thoughts=False)


async def test_previous_turns_are_replayed_as_user_parts_and_model_text() -> None:
    call = {"function_call": {"id": "c1", "name": "echo", "args": {}}, "thought_signature": SIG}
    response = {"function_response": {"id": "c1", "name": "echo", "response": {"result": "pong"}}}
    voice = {"inline_data": {"mime_type": "audio/ogg", "data": base64.b64encode(b"ogg").decode()}}
    path = [
        NodeRecord(1, 1, None, "user", [{"type": "text", "text": "q1"}, voice], False),
        NodeRecord(2, 1, 1, "assistant", [{"text": "думаю", "thought": True}, call], False),
        NodeRecord(3, 1, 2, "user", [response], False),
        NodeRecord(4, 1, 3, "assistant", [{"text": "a1", "thought_signature": SIG}], False),
        NodeRecord(5, 1, 4, "assistant", [{"type": "text", "text": "stopped"}], False),
        NodeRecord(6, 1, 5, "user", [{"type": "text", "text": "q2"}], False),
    ]
    fake = FakeGemini([[chunk({"text": "ok"}, finish="STOP")]])

    await collect(runner(fake), path=path)

    assert contents(fake) == [
        {"role": "user", "parts": [{"text": "q1"}, voice]},
        {"role": "model", "parts": [{"text": "a1"}]},
        {"role": "model", "parts": [{"text": "stopped"}]},
        {"role": "user", "parts": [{"text": "q2"}]},
    ]


async def test_function_call_round_trip_returns_parts_with_signatures() -> None:
    call = {"function_call": {"id": "c1", "name": "echo", "args": {"x": "ping"}}, "thought_signature": SIG}
    fake = FakeGemini([[chunk(call, finish="STOP")], [chunk({"text": "done"}, finish="STOP")]])
    seen: list[dict[str, Any]] = []

    async def record(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        seen.append(args)
        return ToolOutcome("pong")

    events = await collect(runner(fake, ToolRegistry({"echo": record})))

    response = {"function_response": {"id": "c1", "name": "echo", "response": {"result": "pong"}}}
    assert seen == [{"x": "ping"}]
    assert ToolStarted("echo", "") in events
    assert result_of(events).nodes == [
        ("assistant", [call]),
        ("user", [response]),
        ("assistant", [{"text": "done"}]),
    ]
    assert contents(fake, 1)[-2:] == [
        {"role": "model", "parts": [call]},
        {"role": "user", "parts": [response]},
    ]


async def test_tool_rounds_are_capped_with_a_final_tool_free_answer() -> None:
    call = {"function_call": {"id": "c", "name": "echo", "args": {}}}
    fake = FakeGemini(
        [
            [chunk(call, finish="STOP")],
            [chunk(call, finish="STOP")],
            [chunk({"text": "final"}, finish="STOP")],
        ]
    )
    agent = runner(fake, ToolRegistry({"echo": echo}))
    agent.max_rounds = 2

    result = result_of(await collect(agent))

    last = config(fake, 2).tool_config
    assert last is not None and last.function_calling_config is not None
    assert last.function_calling_config.mode == types.FunctionCallingConfigMode.NONE
    assert result.nodes[-1] == ("assistant", [{"text": "final"}])


async def test_max_tokens_drops_dangling_calls() -> None:
    call = {"function_call": {"id": "c", "name": "echo", "args": {}}}
    fake = FakeGemini([[chunk({"text": "part"}, call, finish="MAX_TOKENS")]])

    result = result_of(await collect(runner(fake, ToolRegistry({"echo": echo}))))

    assert result.nodes == [("assistant", [{"text": "part"}])]
    assert result.stop_reason == "max_tokens"


async def test_safety_blocks_are_refusals() -> None:
    blocked = FakeGemini([[chunk(blocked=True)]])
    stopped = FakeGemini([[chunk(finish="SAFETY")]])

    assert result_of(await collect(runner(blocked))).refused
    assert result_of(await collect(runner(stopped))).refused


async def test_code_execution_is_reported_and_its_images_are_sent() -> None:
    code = {"executable_code": {"code": "plot()\nshow()", "language": "PYTHON"}}
    output = {"code_execution_result": {"outcome": "OUTCOME_OK", "output": "ok"}}
    image = {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(b"PNG").decode()}}
    fake = FakeGemini([[chunk(code), chunk(output), chunk(image, {"text": "chart"}, finish="STOP")]])

    events = await collect(runner(fake))

    assert ToolStarted("code_execution", "plot()") in events
    assert FileProduced("chart.png", "image/png", b"PNG") in events


async def test_usage_counts_thoughts_cache_and_search_queries() -> None:
    usage = {
        "prompt_token_count": 100,
        "cached_content_token_count": 30,
        "tool_use_prompt_token_count": 50,
        "candidates_token_count": 7,
        "thoughts_token_count": 3,
    }
    search = {"tool_call": {"id": "t1", "tool_type": "GOOGLE_SEARCH_WEB", "args": {"queries": ["курс евро"]}}}
    fake = FakeGemini(
        [[chunk(search), chunk({"text": "95"}, finish="STOP", usage=usage, queries=["курс евро", "eur rub"])]]
    )

    events = await collect(runner(fake))

    assert ToolStarted("web_search", "курс евро") in events
    turn = result_of(events).usage
    assert (turn.input_tokens, turn.cache_read_tokens, turn.output_tokens) == (120, 30, 10)
    assert turn.web_search_requests == 2
    assert turn.last_prompt_tokens == 150


async def test_youtube_links_are_attached_as_video() -> None:
    fake = FakeGemini([])
    message = SimpleNamespace(text="о чём https://youtu.be/dQw4w9WgXcQ ?")
    turn = cast(TurnInput, SimpleNamespace(message=message))

    content = await runner(fake).encode_user(turn, include_author=False, code_enabled=True)

    assert content[-1] == {"file_data": {"file_uri": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}}


async def test_complete_is_a_cheap_single_request() -> None:
    fake = FakeGemini([])
    fake.models.completions.append(
        chunk(
            {"text": "Рецепт борща"},
            finish="STOP",
            usage={"prompt_token_count": 10, "candidates_token_count": 4},
        )
    )

    text, usage = await runner(fake).complete("назови диалог", max_tokens=256)

    cfg = fake.models.calls[0]["config"]
    assert text == "Рецепт борща"
    assert (usage.input_tokens, usage.output_tokens) == (10, 4)
    assert cfg.max_output_tokens == 256
    assert cfg.thinking_config.thinking_level == types.ThinkingLevel.LOW

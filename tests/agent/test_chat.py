import base64
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from tgagent.agent.events import AgentEvent, FileProduced, TextDelta, ThinkingDelta, ToolStarted, TurnResult
from tgagent.agent.models import CATALOG, ModelSpec
from tgagent.agent.prompt import system_prompt
from tgagent.agent.providers.chat import ChatRunner
from tgagent.agent.tools import AgentOptions, ToolContext, ToolOutcome, ToolRegistry
from tgagent.context.builder import ContentBuilder
from tgagent.storage.repos import NodeRecord

CTX = ToolContext(chat_id=1, thread_id=None, user_id=1, chat_kind="private", message_id=10)
DEEPSEEK = replace(CATALOG["deepseek:deepseek-flash"], context_trigger=100_000, max_output=16_000)
OSS = replace(CATALOG["groq:openai/gpt-oss-120b"], context_trigger=5_000, max_output=2_048)
QUESTION = NodeRecord(9, 1, None, "user", [{"type": "text", "text": "hi"}], False)


def chunk(
    *,
    content: str | None = None,
    reasoning: str | None = None,
    reasoning_field: str = "reasoning_content",
    calls: list[dict[str, Any]] | None = None,
    executed: list[dict[str, Any]] | None = None,
    finish: str | None = None,
    usage: dict[str, Any] | None = None,
) -> ChatCompletionChunk:
    delta: dict[str, Any] = {"content": content}
    if reasoning is not None:
        delta[reasoning_field] = reasoning
    if calls:
        delta["tool_calls"] = calls
    if executed:
        delta["executed_tools"] = executed
    return ChatCompletionChunk.model_validate(
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            "usage": usage,
        }
    )


def call(index: int, call_id: str | None, name: str | None, arguments: str) -> dict[str, Any]:
    function: dict[str, Any] = {"arguments": arguments}
    if name:
        function["name"] = name
    item: dict[str, Any] = {"index": index, "type": "function", "function": function}
    if call_id:
        item["id"] = call_id
    return item


class FakeStream:
    def __init__(self, chunks: Sequence[ChatCompletionChunk]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def _iterate(self) -> AsyncIterator[ChatCompletionChunk]:
        for item in self._chunks:
            yield item

    def __aiter__(self) -> AsyncIterator[ChatCompletionChunk]:
        return self._iterate()


class FakeCompletions:
    def __init__(self, script: list[Sequence[ChatCompletionChunk]]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.completions: list[ChatCompletion] = []

    async def create(self, **params: Any) -> Any:
        self.calls.append(params)
        if not params.get("stream"):
            return self.completions.pop(0)
        return FakeStream(self._script.pop(0))


class FakeChatClient:
    def __init__(self, script: list[Sequence[ChatCompletionChunk]]) -> None:
        self.completions = FakeCompletions(script)
        self.chat = SimpleNamespace(completions=self.completions)

    def as_client(self) -> AsyncOpenAI:
        return cast(AsyncOpenAI, self)


def runner(
    fake: FakeChatClient, registry: ToolRegistry | None = None, spec: ModelSpec = DEEPSEEK
) -> ChatRunner:
    return ChatRunner(
        fake.as_client(), spec, builder=cast(ContentBuilder, None), registry=registry or ToolRegistry({})
    )


async def collect(
    agent: ChatRunner, options: AgentOptions | None = None, path: list[NodeRecord] | None = None
) -> list[AgentEvent]:
    return [event async for event in agent.run(path or [QUESTION], options or AgentOptions(), CTX)]


def result_of(events: list[AgentEvent]) -> TurnResult:
    last = events[-1]
    assert isinstance(last, TurnResult)
    return last


async def echo(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    return ToolOutcome("pong")


async def test_deepseek_turn_streams_reasoning_and_text_and_keeps_reasoning() -> None:
    fake = FakeChatClient(
        [
            [
                chunk(reasoning="hm"),
                chunk(reasoning="m"),
                chunk(content="Hel"),
                chunk(content="lo", finish="stop"),
            ]
        ]
    )

    events = await collect(runner(fake), AgentOptions(show_thinking=True))

    assert events[:4] == [ThinkingDelta("hm"), ThinkingDelta("m"), TextDelta("Hel"), TextDelta("lo")]
    result = result_of(events)
    assert result.nodes == [
        ("assistant", [{"role": "assistant", "content": "Hello", "reasoning_content": "hmm"}])
    ]
    assert (result.text, result.thinking, result.stop_reason) == ("Hello", "hmm", "end_turn")


async def test_hidden_thinking_is_not_streamed() -> None:
    fake = FakeChatClient([[chunk(reasoning="secret"), chunk(content="ok", finish="stop")]])

    events = await collect(runner(fake))

    assert not any(isinstance(event, ThinkingDelta) for event in events)


async def test_deepseek_request_parameters() -> None:
    fake = FakeChatClient([[chunk(content="ok", finish="stop")]])
    options = AgentOptions(effort="high")

    await collect(runner(fake, ToolRegistry({"set_reminder": echo})), options)

    params = fake.completions.calls[0]
    assert params["model"] == "deepseek-flash"
    assert params["messages"] == [
        {"role": "system", "content": system_prompt(options)},
        {"role": "user", "content": "hi"},
    ]
    assert (params["max_tokens"], params["stream"], params["reasoning_effort"]) == (16_000, True, "max")
    assert params["stream_options"] == {"include_usage": True}
    assert [tool["function"]["name"] for tool in params["tools"]] == ["set_reminder"]
    assert params["tools"][0]["function"]["parameters"]["required"] == ["when", "text", "mode"]
    assert "tool_choice" not in params


async def test_groq_gets_built_in_tools_and_its_own_effort_names() -> None:
    fake = FakeChatClient([[chunk(content="ok", finish="stop")]])

    await collect(runner(fake, spec=OSS), AgentOptions(effort="medium"))

    params = fake.completions.calls[0]
    assert params["reasoning_effort"] == "medium"
    assert params["max_tokens"] == 2_048
    assert [tool["type"] for tool in params["tools"]] == ["browser_search", "code_interpreter"]


async def test_images_go_in_user_parts() -> None:
    image = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="}}
    path = [NodeRecord(1, 1, None, "user", [{"type": "text", "text": "что это?"}, image], False)]
    fake = FakeChatClient([[chunk(content="кот", finish="stop")]])

    await collect(runner(fake), path=path)

    assert fake.completions.calls[0]["messages"][1] == {
        "role": "user",
        "content": [{"type": "text", "text": "что это?"}, image],
    }


async def test_history_is_replayed_with_reasoning_for_deepseek_only() -> None:
    assistant = {
        "role": "assistant",
        "content": None,
        "reasoning_content": "нужен инструмент",
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}],
    }
    tool = {"role": "tool", "tool_call_id": "c1", "content": "pong"}
    path = [
        NodeRecord(1, 1, None, "user", [{"type": "compaction", "content": "итоги"}], True),
        NodeRecord(2, 1, 1, "user", [{"type": "text", "text": "q1"}], False),
        NodeRecord(3, 1, 2, "assistant", [assistant], False),
        NodeRecord(4, 1, 3, "user", [tool], False),
        NodeRecord(5, 1, 4, "assistant", [{"type": "text", "text": "оборвано"}], False),
        NodeRecord(6, 1, 5, "user", [{"type": "text", "text": "q2"}], False),
    ]
    deepseek, groq = (
        FakeChatClient([[chunk(content="ok", finish="stop")]]),
        FakeChatClient([[chunk(content="ok", finish="stop")]]),
    )

    await collect(runner(deepseek), path=path)
    await collect(runner(groq, spec=OSS), path=path)

    summary = "<conversation_summary>\nитоги\n</conversation_summary>"
    assert deepseek.completions.calls[0]["messages"][1:] == [
        {"role": "user", "content": f"{summary}\nq1"},
        assistant,
        tool,
        {"role": "assistant", "content": "оборвано", "reasoning_content": ""},
        {"role": "user", "content": "q2"},
    ]
    groq_messages = groq.completions.calls[0]["messages"]
    assert {key for message in groq_messages for key in message} == {
        "role",
        "content",
        "tool_calls",
        "tool_call_id",
    }


async def test_tool_call_fragments_are_merged_and_round_trip() -> None:
    fake = FakeChatClient(
        [
            [
                chunk(reasoning="надо вызвать"),
                chunk(calls=[call(0, "c1", "echo", '{"x": ')]),
                chunk(calls=[call(0, None, None, '"ping"}')], finish="tool_calls"),
            ],
            [chunk(content="done", finish="stop")],
        ]
    )
    seen: list[dict[str, Any]] = []

    async def record(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        seen.append(args)
        return ToolOutcome("pong")

    events = await collect(runner(fake, ToolRegistry({"echo": record})))

    assistant = {
        "role": "assistant",
        "content": None,
        "reasoning_content": "надо вызвать",
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"x": "ping"}'}}
        ],
    }
    tool = {"role": "tool", "tool_call_id": "c1", "content": "pong"}
    assert seen == [{"x": "ping"}]
    assert ToolStarted("echo", "") in events
    assert result_of(events).nodes[:2] == [("assistant", [assistant]), ("user", [tool])]
    assert fake.completions.calls[1]["messages"][-2:] == [assistant, tool]


async def test_invalid_tool_arguments_are_reported_to_the_model() -> None:
    fake = FakeChatClient(
        [
            [chunk(calls=[call(0, "c1", "echo", "{oops")], finish="tool_calls")],
            [chunk(content="sorry", finish="stop")],
        ]
    )

    result = result_of(await collect(runner(fake, ToolRegistry({"echo": echo}))))

    tool = result.nodes[1][1][0]
    assert tool["tool_call_id"] == "c1"
    assert "JSON" in tool["content"]


async def test_tool_rounds_are_capped_with_a_final_tool_free_answer() -> None:
    loop = [chunk(calls=[call(0, "c", "list_reminders", "{}")], finish="tool_calls")]
    fake = FakeChatClient([loop, loop, [chunk(content="final", finish="stop")]])
    agent = runner(fake, ToolRegistry({"list_reminders": echo}))
    agent.max_rounds = 2

    result = result_of(await collect(agent))

    assert fake.completions.calls[2]["tool_choice"] == "none"
    assert result.text == "final"


async def test_truncated_answer_drops_unfinished_calls() -> None:
    fake = FakeChatClient(
        [[chunk(content="part"), chunk(calls=[call(0, "c", "echo", "{")], finish="length")]]
    )

    result = result_of(await collect(runner(fake, ToolRegistry({"echo": echo}))))

    assert result.nodes == [
        ("assistant", [{"role": "assistant", "content": "part", "reasoning_content": ""}])
    ]
    assert result.stop_reason == "max_tokens"


async def test_content_filter_is_a_refusal() -> None:
    fake = FakeChatClient([[chunk(finish="content_filter")]])

    assert result_of(await collect(runner(fake))).refused


async def test_groq_built_in_tools_are_reported_and_charts_sent() -> None:
    png = base64.b64encode(b"PNG").decode()
    search = {"index": 0, "type": "search", "arguments": '{"query": "курс евро"}'}
    python = {
        "index": 1,
        "type": "python",
        "arguments": '{"code": "plot()"}',
        "code_results": [{"text": "", "png": png}],
    }
    fake = FakeChatClient(
        [
            [
                chunk(reasoning="ищу", reasoning_field="reasoning", executed=[search]),
                chunk(executed=[search, python]),
                chunk(content="95", finish="stop"),
            ]
        ]
    )

    events = await collect(runner(fake, spec=OSS))

    started = [event for event in events if isinstance(event, ToolStarted)]
    assert started == [ToolStarted("web_search", "курс евро"), ToolStarted("code_execution", "plot()")]
    assert FileProduced("chart.png", "image/png", b"PNG") in events
    assert result_of(events).nodes == [("assistant", [{"role": "assistant", "content": "95"}])]


async def test_usage_reads_cache_hits_from_the_last_chunk() -> None:
    usage = {"prompt_tokens": 100, "completion_tokens": 9, "total_tokens": 109, "prompt_cache_hit_tokens": 60}
    fake = FakeChatClient([[chunk(content="ok"), chunk(finish="stop", usage=usage)]])

    turn = result_of(await collect(runner(fake))).usage

    assert (turn.input_tokens, turn.cache_read_tokens, turn.output_tokens) == (40, 60, 9)
    assert turn.last_prompt_tokens == 100


async def test_complete_switches_deepseek_thinking_off() -> None:
    fake = FakeChatClient([])
    fake.completions.completions.append(
        ChatCompletion.model_validate(
            {
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Рецепт борща"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }
        )
    )

    text, usage = await runner(fake).complete("назови диалог", max_tokens=256)

    params = fake.completions.calls[0]
    assert text == "Рецепт борща"
    assert (usage.input_tokens, usage.output_tokens) == (10, 4)
    assert params["messages"] == [{"role": "user", "content": "назови диалог"}]
    assert params["max_tokens"] == 256
    assert params["extra_body"] == {"thinking": {"type": "disabled"}}

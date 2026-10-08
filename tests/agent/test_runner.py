from typing import Any

import pytest

from tests.agent.fakes import FakeAnthropic, block_start, block_stop, final, text_event, thinking_event
from tgagent.agent.events import AgentEvent, FileProduced, TextDelta, ThinkingDelta, ToolStarted, TurnResult
from tgagent.agent.runner import AgentRunner
from tgagent.agent.tools import AgentOptions, ToolContext, ToolOutcome, ToolRegistry

CTX = ToolContext(chat_id=1, thread_id=None, user_id=1, chat_kind="private", message_id=10)
USER = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
TEXT = {"type": "text", "text": "Hello"}


def runner(fake: FakeAnthropic, registry: ToolRegistry | None = None) -> AgentRunner:
    return AgentRunner(
        fake.as_client(),
        model="claude-haiku-5-5",
        max_tokens=16_000,
        compaction_trigger=100_000,
        registry=registry or ToolRegistry({}),
        web_supported=True,
        web_max_uses=5,
    )


async def collect(
    agent: AgentRunner, options: AgentOptions | None = None, container: str | None = None
) -> list[AgentEvent]:
    return [event async for event in agent.run(USER, options or AgentOptions(), CTX, container_id=container)]


def result_of(events: list[AgentEvent]) -> TurnResult:
    last = events[-1]
    assert isinstance(last, TurnResult)
    return last


async def test_text_turn_streams_deltas_and_returns_node() -> None:
    fake = FakeAnthropic([([thinking_event("hmm"), text_event("Hel"), text_event("lo")], final([TEXT]))])

    events = await collect(runner(fake))

    assert events[:3] == [ThinkingDelta("hmm"), TextDelta("Hel"), TextDelta("lo")]
    result = result_of(events)
    assert result.nodes == [("assistant", [TEXT])]
    assert result.text == "Hello"
    assert not result.refused


async def test_request_parameters_follow_design() -> None:
    fake = FakeAnthropic([([], final([TEXT]))])

    await collect(runner(fake), AgentOptions(effort="high", show_thinking=True, web=False))

    params = fake.messages.calls[0]
    assert params["model"] == "claude-haiku-5-5"
    assert params["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert params["output_config"] == {"effort": "high"}
    assert params["context_management"] == {
        "edits": [{"type": "compact_20260112", "trigger": {"type": "input_tokens", "value": 100_000}}]
    }
    assert set(params["betas"]) == {"compact-2026-01-12", "thinking-binding-controls-2026-08-01"}
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "web_search" not in [tool["name"] for tool in params["tools"]]
    assert "container" not in params


async def test_client_tool_round_trip() -> None:
    tool_use = {"type": "tool_use", "id": "tu_1", "name": "echo", "input": {"x": "ping"}}
    fake = FakeAnthropic([([], final([tool_use], "tool_use")), ([text_event("done")], final([TEXT]))])
    seen: list[dict[str, Any]] = []

    async def echo(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        seen.append(args)
        return ToolOutcome("pong")

    events = await collect(runner(fake, ToolRegistry({"echo": echo})))

    assert seen == [{"x": "ping"}]
    assert ToolStarted("echo", "") in events
    tool_result = {"type": "tool_result", "tool_use_id": "tu_1", "content": "pong", "is_error": False}
    assert result_of(events).nodes == [
        ("assistant", [tool_use]),
        ("user", [tool_result]),
        ("assistant", [TEXT]),
    ]
    assert fake.messages.calls[1]["messages"][-1] == {"role": "user", "content": [tool_result]}


async def test_pause_turn_continuation_is_merged_into_one_node() -> None:
    search = {"type": "server_tool_use", "id": "srv_1", "name": "web_search", "input": {"query": "q"}}
    fake = FakeAnthropic([([], final([search], "pause_turn")), ([], final([TEXT]))])

    events = await collect(runner(fake))

    assert result_of(events).nodes == [("assistant", [search, TEXT])]
    assert fake.messages.calls[1]["messages"][-1] == {"role": "assistant", "content": [search]}


async def test_server_tool_activity_reported() -> None:
    events_in = [
        block_start(type="compaction"),
        block_stop(type="server_tool_use", name="web_search", input={"query": "курс евро"}),
    ]
    fake = FakeAnthropic([(events_in, final([TEXT]))])

    events = await collect(runner(fake))

    assert ToolStarted("compaction", "") in events
    assert ToolStarted("web_search", "курс евро") in events


async def test_refusal_is_flagged() -> None:
    fake = FakeAnthropic([([], final([], "refusal"))])

    assert result_of(await collect(runner(fake))).refused


async def test_max_tokens_drops_unanswered_tool_use() -> None:
    dangling = {"type": "tool_use", "id": "tu_9", "name": "echo", "input": {}}
    fake = FakeAnthropic([([], final([TEXT, dangling], "max_tokens"))])

    assert result_of(await collect(runner(fake))).nodes == [("assistant", [TEXT])]


async def test_code_execution_files_are_downloaded() -> None:
    result_block = {
        "type": "bash_code_execution_tool_result",
        "tool_use_id": "srv_2",
        "content": {
            "type": "bash_code_execution_result",
            "stdout": "",
            "stderr": "",
            "return_code": 0,
            "content": [{"type": "bash_code_execution_output", "file_id": "file_1"}],
        },
    }
    container = {"id": "cntr_1", "expires_at": "2026-10-08T13:00:00Z"}
    fake = FakeAnthropic([([], final([result_block, TEXT], container=container))])
    fake.files.files["file_1"] = ("../chart.png", "image/png", b"PNG")

    events = await collect(runner(fake), container="cntr_0")

    assert FileProduced("chart.png", "image/png", b"PNG") in events
    assert fake.messages.calls[0]["container"] == "cntr_0"
    result = result_of(events)
    assert result.container is not None
    assert result.container.id == "cntr_1"


async def test_tool_rounds_are_capped_with_a_final_tool_free_answer() -> None:
    tool_use = {"type": "tool_use", "id": "tu", "name": "echo", "input": {}}
    script = [([], final([tool_use], "tool_use")) for _ in range(2)]
    fake = FakeAnthropic([*script, ([], final([TEXT]))])

    async def echo(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        return ToolOutcome("again")

    agent = runner(fake, ToolRegistry({"echo": echo}))
    agent.max_rounds = 2

    result = result_of(await collect(agent))
    assert len(fake.messages.calls) == 3
    assert "tool_choice" not in fake.messages.calls[1]
    assert fake.messages.calls[2]["tool_choice"] == {"type": "none"}
    assert result.nodes[-1] == ("assistant", [TEXT])


@pytest.mark.parametrize("show", [True, False])
async def test_thinking_display_follows_setting(show: bool) -> None:
    fake = FakeAnthropic([([], final([TEXT]))])

    await collect(runner(fake), AgentOptions(show_thinking=show))

    assert fake.messages.calls[0]["thinking"]["display"] == ("summarized" if show else "omitted")

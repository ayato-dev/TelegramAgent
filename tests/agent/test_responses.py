import base64
from dataclasses import replace
from decimal import Decimal
from typing import Any, cast

from tests.agent.openai_fakes import (
    FakeOpenAI,
    item_added,
    item_done,
    message,
    response,
    summary_delta,
    text_delta,
)
from tgagent.agent.events import AgentEvent, FileProduced, TextDelta, ThinkingDelta, ToolStarted, TurnResult
from tgagent.agent.models import CATALOG
from tgagent.agent.prompt import system_prompt
from tgagent.agent.providers.responses import ResponsesRunner
from tgagent.agent.tools import AgentOptions, ToolContext, ToolOutcome, ToolRegistry
from tgagent.context.builder import ContentBuilder
from tgagent.storage.repos import NodeRecord

CTX = ToolContext(chat_id=1, thread_id=None, user_id=1, chat_kind="private", message_id=10)
LUNA = replace(CATALOG["openai:gpt-6-luna"], context_trigger=100_000, max_output=16_000)
QUESTION = NodeRecord(9, 1, None, "user", [{"type": "text", "text": "hi"}], False)
REASONING = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc"}
CALL = {
    "type": "function_call",
    "id": "fc_1",
    "call_id": "call_1",
    "name": "echo",
    "arguments": '{"x": "ping"}',
}


def runner(fake: FakeOpenAI, registry: ToolRegistry | None = None) -> ResponsesRunner:
    return ResponsesRunner(
        fake.as_client(), LUNA, builder=cast(ContentBuilder, None), registry=registry or ToolRegistry({})
    )


async def collect(
    agent: ResponsesRunner, options: AgentOptions | None = None, path: list[NodeRecord] | None = None
) -> list[AgentEvent]:
    return [event async for event in agent.run(path or [QUESTION], options or AgentOptions(), CTX)]


def result_of(events: list[AgentEvent]) -> TurnResult:
    last = events[-1]
    assert isinstance(last, TurnResult)
    return last


async def echo(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    return ToolOutcome("pong")


async def test_text_turn_streams_deltas_and_stores_output_items() -> None:
    fake = FakeOpenAI(
        [
            (
                [summary_delta("hmm"), text_delta("Hel"), text_delta("lo")],
                response([REASONING, message("Hello")]),
            )
        ]
    )

    events = await collect(runner(fake))

    assert events[:3] == [ThinkingDelta("hmm"), TextDelta("Hel"), TextDelta("lo")]
    result = result_of(events)
    assert result.nodes == [("assistant", [REASONING, message("Hello")])]
    assert (result.text, result.stop_reason, result.refused) == ("Hello", "end_turn", False)


async def test_request_parameters_follow_design() -> None:
    fake = FakeOpenAI([([], response([message("ok")]))])
    options = AgentOptions(effort="high", show_thinking=True)

    await collect(runner(fake, ToolRegistry({"set_reminder": echo})), options)

    params = fake.responses.calls[0]
    assert params["model"] == "gpt-6-luna"
    assert params["instructions"] == system_prompt(options, LUNA)
    assert (params["store"], params["stream"], params["max_output_tokens"]) == (False, True, 16_000)
    assert params["include"] == ["reasoning.encrypted_content"]
    assert params["reasoning"] == {"effort": "high", "summary": "auto"}
    assert params["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]
    tools = {tool.get("name", tool["type"]): tool for tool in params["tools"]}
    assert tools["set_reminder"]["type"] == "function"
    assert tools["set_reminder"]["strict"] is True
    assert tools["set_reminder"]["parameters"]["required"] == ["when", "text", "mode"]
    assert {"web_search", "code_interpreter", "image_generation"} <= set(tools)
    assert "tool_choice" not in params


async def test_disabled_web_and_code_and_hidden_thinking() -> None:
    fake = FakeOpenAI([([], response([message("ok")]))])

    await collect(runner(fake), AgentOptions(web=False, code=False))

    params = fake.responses.calls[0]
    assert [tool["type"] for tool in params["tools"]] == ["image_generation"]
    assert params["reasoning"] == {"effort": "medium"}


async def test_previous_turns_are_replayed_as_text() -> None:
    path = [
        NodeRecord(
            1,
            1,
            None,
            "user",
            [{"type": "text", "text": "q1"}, {"type": "input_image", "file_id": "f"}],
            False,
        ),
        NodeRecord(2, 1, 1, "assistant", [REASONING, CALL], False),
        NodeRecord(
            3, 1, 2, "user", [{"type": "function_call_output", "call_id": "call_1", "output": "pong"}], False
        ),
        NodeRecord(4, 1, 3, "assistant", [message("a1")], False),
        NodeRecord(5, 1, 4, "user", [{"type": "text", "text": "q2"}], False),
        NodeRecord(6, 1, 5, "assistant", [{"type": "text", "text": "stopped half"}], False),
        NodeRecord(7, 1, 6, "user", [{"type": "text", "text": "q3"}], False),
    ]
    fake = FakeOpenAI([([], response([message("ok")]))])

    await collect(runner(fake), path=path)

    assert fake.responses.calls[0]["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "q1"}, {"type": "input_image", "file_id": "f"}],
        },
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": [{"type": "input_text", "text": "q2"}]},
        {"role": "assistant", "content": "stopped half"},
        {"role": "user", "content": [{"type": "input_text", "text": "q3"}]},
    ]


async def test_function_call_round_trip() -> None:
    fake = FakeOpenAI(
        [([], response([REASONING, CALL])), ([text_delta("done")], response([message("done")]))]
    )
    seen: list[dict[str, Any]] = []

    async def record(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        seen.append(args)
        return ToolOutcome("pong")

    events = await collect(runner(fake, ToolRegistry({"echo": record})))

    output = {"type": "function_call_output", "call_id": "call_1", "output": "pong"}
    assert seen == [{"x": "ping"}]
    assert ToolStarted("echo", "") in events
    assert result_of(events).nodes == [
        ("assistant", [REASONING, CALL]),
        ("user", [output]),
        ("assistant", [message("done")]),
    ]
    assert fake.responses.calls[1]["input"][-3:] == [REASONING, CALL, output]


async def test_tool_rounds_are_capped_with_a_final_tool_free_answer() -> None:
    fake = FakeOpenAI([([], response([CALL])), ([], response([CALL])), ([], response([message("final")]))])
    agent = runner(fake, ToolRegistry({"echo": echo}))
    agent.max_rounds = 2

    result = result_of(await collect(agent))

    assert len(fake.responses.calls) == 3
    assert fake.responses.calls[2]["tool_choice"] == "none"
    assert result.nodes[-1] == ("assistant", [message("final")])


async def test_truncated_answer_drops_unanswered_calls() -> None:
    fake = FakeOpenAI(
        [([], response([message("part"), CALL], status="incomplete", reason="max_output_tokens"))]
    )

    result = result_of(await collect(runner(fake, ToolRegistry({"echo": echo}))))

    assert result.nodes == [("assistant", [message("part")])]
    assert result.stop_reason == "max_tokens"


async def test_refusal_is_flagged() -> None:
    refusal = {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "refusal", "refusal": "no"}],
    }
    fake = FakeOpenAI([([], response([refusal]))])

    assert result_of(await collect(runner(fake))).refused


async def test_generated_images_are_sent_but_not_stored() -> None:
    png = b"\x89PNG fake"
    image = {
        "type": "image_generation_call",
        "id": "ig_1",
        "status": "completed",
        "result": base64.b64encode(png).decode(),
    }
    pending = {**image, "status": "in_progress", "result": None}
    fake = FakeOpenAI([([item_added(pending)], response([image, message("Готово")]))])

    events = await collect(runner(fake))

    assert ToolStarted("image_generation", "") in events
    assert FileProduced("image.png", "image/png", png) in events
    result = result_of(events)
    stored = result.nodes[0][1][0]
    assert stored["type"] == "image_generation_call"
    assert "result" not in stored
    assert result.usage.tool_cost > Decimal(0)


async def test_code_interpreter_files_are_downloaded_and_containers_billed() -> None:
    annotated = message("chart ready")
    annotated["content"][0]["annotations"] = [
        {
            "type": "container_file_citation",
            "container_id": "cntr_1",
            "file_id": "cfile_1",
            "filename": "chart.png",
            "start_index": 0,
            "end_index": 5,
        }
    ]
    call = {
        "type": "code_interpreter_call",
        "id": "ci_1",
        "status": "completed",
        "container_id": "cntr_1",
        "code": "plot()",
    }
    fake = FakeOpenAI([([item_added(call)], response([call, annotated]))])
    fake.container_files.files[("cntr_1", "cfile_1")] = b"PNG"

    events = await collect(runner(fake))

    assert ToolStarted("code_interpreter", "plot()") in events
    assert FileProduced("chart.png", "image/png", b"PNG") in events
    assert result_of(events).usage.tool_cost == Decimal("0.03")


async def test_usage_counts_cached_tokens_and_searches() -> None:
    search = {
        "type": "web_search_call",
        "id": "ws_1",
        "status": "completed",
        "action": {"type": "search", "query": "курс евро"},
    }
    usage = {
        "input_tokens": 100,
        "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 0},
        "output_tokens": 20,
        "output_tokens_details": {"reasoning_tokens": 5},
        "total_tokens": 120,
    }
    fake = FakeOpenAI([([item_done(search)], response([search, message("95")], usage=usage))])

    events = await collect(runner(fake))

    assert ToolStarted("web_search", "курс евро") in events
    turn = result_of(events).usage
    assert (turn.input_tokens, turn.cache_read_tokens, turn.output_tokens) == (60, 40, 20)
    assert turn.web_search_requests == 1
    assert turn.last_prompt_tokens == 100


async def test_complete_is_a_cheap_single_request() -> None:
    fake = FakeOpenAI([])
    fake.responses.completions.append(response([message("Рецепт борща")]))

    text, usage = await runner(fake).complete("назови диалог", max_tokens=256)

    params = fake.responses.calls[0]
    assert text == "Рецепт борща"
    assert usage.output_tokens == 5
    assert (params["input"], params["max_output_tokens"], params["store"]) == ("назови диалог", 256, False)
    assert params["reasoning"] == {"effort": "low"}


async def test_client_side_summary_opens_the_replay() -> None:
    path = [
        NodeRecord(1, 1, None, "user", [{"type": "compaction", "content": "итоги"}], True),
        NodeRecord(2, 1, 1, "user", [{"type": "text", "text": "дальше"}], False),
    ]
    fake = FakeOpenAI([([], response([message("ok")]))])

    await collect(runner(fake), path=path)

    summary = "<conversation_summary>\nитоги\n</conversation_summary>"
    assert fake.responses.calls[0]["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": summary}]},
        {"role": "user", "content": [{"type": "input_text", "text": "дальше"}]},
    ]


async def test_complete_passes_a_system_prompt() -> None:
    fake = FakeOpenAI([])
    fake.responses.completions.append(response([message("ok")]))

    await runner(fake).complete("hi", max_tokens=256, system="be brief")

    assert fake.responses.calls[0]["instructions"] == "be brief"

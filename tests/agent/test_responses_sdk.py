"""The OpenAI runner against the real SDK (request validation, SSE parsing) over a mocked transport."""

import json
from dataclasses import replace
from typing import Any, cast

import httpx2
from openai import AsyncOpenAI

from tgagent.agent.events import TextDelta, ThinkingDelta, ToolStarted, TurnResult
from tgagent.agent.models import CATALOG
from tgagent.agent.providers.responses import ResponsesRunner
from tgagent.agent.tools import AgentOptions, ToolContext, ToolOutcome, ToolRegistry
from tgagent.context.builder import ContentBuilder
from tgagent.storage.repos import NodeRecord

CTX = ToolContext(chat_id=1, thread_id=None, user_id=1, chat_kind="private", message_id=1)
USAGE = {
    "input_tokens": 50,
    "input_tokens_details": {"cached_tokens": 10, "cache_write_tokens": 0},
    "output_tokens": 7,
    "output_tokens_details": {"reasoning_tokens": 3},
    "total_tokens": 57,
}
SEARCH = {
    "type": "web_search_call",
    "id": "ws_1",
    "status": "completed",
    "action": {"type": "search", "query": "курс евро"},
}
MESSAGE = {
    "type": "message",
    "id": "msg_1",
    "role": "assistant",
    "status": "completed",
    "content": [{"type": "output_text", "text": "Евро стоит 95 ₽", "annotations": []}],
}


def sse(*events: dict[str, Any]) -> bytes:
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps({**event, 'sequence_number': i})}\n\n"
        for i, event in enumerate(events)
    ).encode()


def response_json(status: str, output: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": "gpt-6-luna",
        "status": status,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": USAGE if status == "completed" else None,
    }


BODY = sse(
    {"type": "response.created", "response": response_json("in_progress", [])},
    {
        "type": "response.reasoning_summary_text.delta",
        "item_id": "rs_1",
        "output_index": 0,
        "summary_index": 0,
        "delta": "Ищу курс",
    },
    {"type": "response.output_item.done", "output_index": 1, "item": SEARCH},
    {
        "type": "response.output_text.delta",
        "item_id": "msg_1",
        "output_index": 2,
        "content_index": 0,
        "delta": "Евро ",
        "logprobs": [],
    },
    {
        "type": "response.output_text.delta",
        "item_id": "msg_1",
        "output_index": 2,
        "content_index": 0,
        "delta": "стоит 95 ₽",
        "logprobs": [],
    },
    {"type": "response.completed", "response": response_json("completed", [SEARCH, MESSAGE])},
)


async def test_runner_with_real_sdk_stream() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=BODY)

    async def noop(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        return ToolOutcome("ok")

    client = AsyncOpenAI(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), max_retries=0
    )
    spec = replace(CATALOG["openai:gpt-6-luna"], context_trigger=100_000, max_output=16_000)
    runner = ResponsesRunner(
        client, spec, builder=cast(ContentBuilder, None), registry=ToolRegistry({"set_reminder": noop})
    )
    path = [NodeRecord(1, 1, None, "user", [{"type": "text", "text": "курс евро?"}], False)]

    events = [e async for e in runner.run(path, AgentOptions(show_thinking=True), CTX)]

    assert ThinkingDelta("Ищу курс") in events
    assert ToolStarted("web_search", "курс евро") in events
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Евро ", "стоит 95 ₽"]
    result = events[-1]
    assert isinstance(result, TurnResult)
    assert result.text == "Евро стоит 95 ₽"
    assert result.usage.web_search_requests == 1
    assert (result.usage.input_tokens, result.usage.cache_read_tokens) == (40, 10)
    json.dumps(result.nodes)

    sent = requests[0]
    assert sent["url"].endswith("/responses")
    body = sent["body"]
    assert (body["model"], body["store"], body["stream"]) == ("gpt-6-luna", False, True)
    assert body["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "курс евро?"}]}]
    assert body["tools"][0]["name"] == "set_reminder"

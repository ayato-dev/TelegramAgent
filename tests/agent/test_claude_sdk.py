"""The runner against the real Anthropic SDK (SSE parsing, request validation) over a mocked transport."""

import json
from dataclasses import replace
from typing import Any, cast

import httpx2
from anthropic import AsyncAnthropic

from tgagent.agent.events import TextDelta, ThinkingDelta, ToolStarted, TurnResult
from tgagent.agent.models import CATALOG
from tgagent.agent.providers.claude import ClaudeRunner
from tgagent.agent.tools import AgentOptions, ToolContext, ToolRegistry
from tgagent.context.builder import ContentBuilder
from tgagent.storage.repos import NodeRecord

CTX = ToolContext(chat_id=1, thread_id=None, user_id=1, chat_kind="private", message_id=1)
USAGE = {
    "input_tokens": 12,
    "output_tokens": 7,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
}


def sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def message_start() -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": USAGE,
        },
    }


def message_end(stop_reason: str = "end_turn") -> list[dict[str, Any]]:
    return [
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 7},
        },
        {"type": "message_stop"},
    ]


BODY = sse(
    message_start(),
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "thinking", "thinking": "", "signature": ""},
    },
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Считаю"}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig=="}},
    {"type": "content_block_stop", "index": 0},
    {
        "type": "content_block_start",
        "index": 1,
        "content_block": {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {}},
    },
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '{"query": "курс евро"}'},
    },
    {"type": "content_block_stop", "index": 1},
    {
        "type": "content_block_start",
        "index": 2,
        "content_block": {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": []},
    },
    {"type": "content_block_stop", "index": 2},
    {"type": "content_block_start", "index": 3, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 3, "delta": {"type": "text_delta", "text": "Евро "}},
    {"type": "content_block_delta", "index": 3, "delta": {"type": "text_delta", "text": "стоит 95 ₽"}},
    {"type": "content_block_stop", "index": 3},
    *message_end(),
)


async def test_runner_with_real_sdk_stream() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append({"headers": dict(request.headers), "body": json.loads(request.content)})
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=BODY)

    client = AsyncAnthropic(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), max_retries=0
    )
    spec = replace(CATALOG["anthropic:claude-haiku-5-5"], context_trigger=100_000, max_output=16_000)
    runner = ClaudeRunner(
        client, spec, builder=cast(ContentBuilder, None), registry=ToolRegistry({}), web_max_uses=5
    )
    path = [NodeRecord(1, 1, None, "user", [{"type": "text", "text": "курс евро?"}], False)]

    events = [e async for e in runner.run(path, AgentOptions(show_thinking=True), CTX)]

    assert ThinkingDelta("Считаю") in events
    assert ToolStarted("web_search", "курс евро") in events
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Евро ", "стоит 95 ₽"]
    result = events[-1]
    assert isinstance(result, TurnResult)
    assert result.text == "Евро стоит 95 ₽"
    content = result.nodes[0][1]
    assert [block["type"] for block in content] == [
        "thinking",
        "server_tool_use",
        "web_search_tool_result",
        "text",
    ]
    assert content[0]["signature"] == "sig=="
    assert content[1]["input"] == {"query": "курс евро"}
    assert "parsed_output" not in content[3]
    json.dumps(content)

    sent = requests[0]
    beta_header = sent["headers"]["anthropic-beta"]
    assert "compact-2026-01-12" in beta_header
    assert "thinking-binding-controls-2026-08-01" in beta_header
    body = sent["body"]
    assert body["stream"] is True
    assert body["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert body["context_management"]["edits"][0]["trigger"]["value"] == 100_000
    assert body["cache_control"] == {"type": "ephemeral"}
    assert "betas" not in body

"""The chat runner against the real OpenAI SDK pointed at DeepSeek, over a mocked transport."""

import json
from dataclasses import replace
from typing import Any, cast

import httpx2
from openai import AsyncOpenAI

from tgagent.agent.events import TextDelta, ToolStarted, TurnResult
from tgagent.agent.models import CATALOG
from tgagent.agent.providers.chat import ChatRunner
from tgagent.agent.tools import AgentOptions, ToolContext, ToolOutcome, ToolRegistry
from tgagent.context.builder import ContentBuilder
from tgagent.storage.repos import NodeRecord

CTX = ToolContext(chat_id=1, thread_id=None, user_id=1, chat_kind="private", message_id=1)


def sse(*deltas: tuple[dict[str, Any], str | None], usage: dict[str, Any]) -> bytes:
    chunks = [
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "deepseek-flash",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            "usage": usage if i == len(deltas) - 1 else None,
        }
        for i, (delta, finish) in enumerate(deltas)
    ]
    return ("".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()


USAGE = {"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35, "prompt_cache_hit_tokens": 10}
CALL = {
    "index": 0,
    "id": "call_1",
    "type": "function",
    "function": {"name": "list_reminders", "arguments": "{}"},
}
FIRST = sse(({"reasoning_content": "проверю"}, None), ({"tool_calls": [CALL]}, "tool_calls"), usage=USAGE)
SECOND = sse(({"content": "Напоминаний "}, None), ({"content": "нет"}, "stop"), usage=USAGE)


async def test_runner_with_real_sdk_stream() -> None:
    requests: list[dict[str, Any]] = []
    bodies = [FIRST, SECOND]

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=bodies.pop(0))

    async def none(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        return ToolOutcome("пусто")

    client = AsyncOpenAI(
        api_key="test",
        base_url="https://api.deepseek.com",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        max_retries=0,
    )
    spec = replace(CATALOG["deepseek:deepseek-flash"], context_trigger=100_000, max_output=8_000)
    runner = ChatRunner(
        client, spec, builder=cast(ContentBuilder, None), registry=ToolRegistry({"list_reminders": none})
    )
    path = [NodeRecord(1, 1, None, "user", [{"type": "text", "text": "что запланировано?"}], False)]

    events = [e async for e in runner.run(path, AgentOptions(), CTX)]

    assert ToolStarted("list_reminders", "") in events
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Напоминаний ", "нет"]
    result = events[-1]
    assert isinstance(result, TurnResult)
    assert result.text == "Напоминаний нет"
    assert result.usage.cache_read_tokens == 20
    json.dumps(result.nodes)

    assert requests[0]["url"] == "https://api.deepseek.com/chat/completions"
    first, second = requests[0]["body"], requests[1]["body"]
    assert (first["reasoning_effort"], first["stream"]) == ("high", True)
    assert first["tools"][0]["function"]["name"] == "list_reminders"
    assistant, tool = second["messages"][-2:]
    assert assistant["reasoning_content"] == "проверю"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert tool == {"role": "tool", "tool_call_id": "call_1", "content": "пусто"}

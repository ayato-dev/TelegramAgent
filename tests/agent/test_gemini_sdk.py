"""The Gemini runner against the real SDK (request serialisation, SSE parsing) over a mocked transport."""

import base64
import json
from dataclasses import replace
from typing import Any, cast

import httpx2
from google import genai
from google.genai import types

from tgagent.agent.events import TextDelta, ToolStarted, TurnResult
from tgagent.agent.models import CATALOG
from tgagent.agent.providers.gemini import GeminiRunner
from tgagent.agent.tools import AgentOptions, ToolContext, ToolOutcome, ToolRegistry
from tgagent.context.builder import ContentBuilder
from tgagent.storage.repos import NodeRecord

CTX = ToolContext(chat_id=1, thread_id=None, user_id=1, chat_kind="private", message_id=1)
SIG = base64.b64encode(b"sig").decode()


def sse(*chunks: dict[str, Any]) -> bytes:
    return "".join(f"data: {json.dumps(chunk)}\r\n\r\n" for chunk in chunks).encode()


FIRST = sse(
    {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {"id": "c1", "name": "echo", "args": {"x": 1}},
                            "thoughtSignature": SIG,
                        }
                    ],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 5},
    }
)
SECOND = sse(
    {"candidates": [{"content": {"role": "model", "parts": [{"text": "Гото"}]}}]},
    {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "во"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 60, "cachedContentTokenCount": 20, "candidatesTokenCount": 3},
    },
)


async def test_runner_with_real_sdk_stream() -> None:
    requests: list[dict[str, Any]] = []
    bodies = [FIRST, SECOND]

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=bodies.pop(0))

    async def echo(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        return ToolOutcome("pong")

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    client = genai.Client(api_key="test", http_options=types.HttpOptions(httpx_async_client=http))
    spec = replace(
        CATALOG["gemini:gemini-3.1-flash-lite"], context_trigger=100_000, max_output=8_000, web=False
    )
    runner = GeminiRunner(
        client,
        spec,
        builder=cast(ContentBuilder, None),
        registry=ToolRegistry({"echo": echo, "set_reminder": echo}),
    )
    path = [NodeRecord(1, 1, None, "user", [{"type": "text", "text": "сделай"}], False)]

    events = [e async for e in runner.run(path, AgentOptions(), CTX)]

    assert ToolStarted("echo", "") in events
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Гото", "во"]
    result = events[-1]
    assert isinstance(result, TurnResult)
    assert result.text == "Готово"
    assert result.nodes[0][1] == [
        {"function_call": {"id": "c1", "name": "echo", "args": {"x": 1}}, "thought_signature": SIG}
    ]
    assert result.usage.cache_read_tokens == 20
    json.dumps(result.nodes)

    assert ":streamGenerateContent" in requests[0]["url"]
    first, second = requests[0]["body"], requests[1]["body"]
    assert first["contents"] == [{"role": "user", "parts": [{"text": "сделай"}]}]
    assert first["systemInstruction"]["parts"][0]["text"]
    declarations = [d for tool in first["tools"] for d in tool.get("functionDeclarations", [])]
    assert [d["name"] for d in declarations] == ["set_reminder"]
    assert first["toolConfig"] == {"includeServerSideToolInvocations": True}
    replayed_call, function_response = second["contents"][-2:]
    assert replayed_call["parts"][0]["thoughtSignature"] == SIG
    assert function_response["parts"][0]["functionResponse"]["response"] == {"result": "pong"}

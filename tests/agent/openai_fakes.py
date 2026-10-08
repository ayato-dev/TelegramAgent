from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.responses import Response


def response(
    output: list[dict[str, Any]],
    *,
    status: str = "completed",
    reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> Response:
    return Response.model_validate(
        {
            "id": "resp_1",
            "object": "response",
            "created_at": 0,
            "model": "gpt-6-luna",
            "status": status,
            "incomplete_details": {"reason": reason} if reason else None,
            "output": output,
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": usage
            or {
                "input_tokens": 10,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens": 5,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 15,
            },
        }
    )


def message(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def text_delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def summary_delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="response.reasoning_summary_text.delta", delta=text)


def item_added(item: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_item.added", item=response([item]).output[0])


def item_done(item: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_item.done", item=response([item]).output[0])


class FakeStream:
    def __init__(self, events: Sequence[Any]) -> None:
        self._events = events

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def _iterate(self) -> AsyncIterator[Any]:
        for event in self._events:
            yield event

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iterate()


class FakeResponses:
    def __init__(self, script: list[tuple[Sequence[Any], Response]]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.completions: list[Response] = []

    async def create(self, **params: Any) -> Any:
        self.calls.append(params)
        if not params.get("stream"):
            return self.completions.pop(0)
        events, final = self._script.pop(0)
        kind = "response.completed" if final.status == "completed" else "response.incomplete"
        return FakeStream([*events, SimpleNamespace(type=kind, response=final)])


class FakeContainerContent:
    def __init__(self) -> None:
        self.files: dict[tuple[str, str], bytes] = {}

    async def retrieve(self, file_id: str, *, container_id: str) -> SimpleNamespace:
        return SimpleNamespace(content=self.files[(container_id, file_id)])


class FakeOpenAI:
    def __init__(self, script: list[tuple[Sequence[Any], Response]]) -> None:
        self.responses = FakeResponses(script)
        self.container_files = FakeContainerContent()
        self.containers = SimpleNamespace(files=SimpleNamespace(content=self.container_files))

    def as_client(self) -> AsyncOpenAI:
        return cast(AsyncOpenAI, self)

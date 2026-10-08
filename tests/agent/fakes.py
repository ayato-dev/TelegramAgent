from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types.beta import BetaMessage


def final(
    content: list[dict[str, Any]],
    stop_reason: str = "end_turn",
    *,
    usage: dict[str, Any] | None = None,
    container: dict[str, Any] | None = None,
) -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage or {"input_tokens": 10, "output_tokens": 5},
            "container": container,
        }
    )


def text_event(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def thinking_event(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="thinking", thinking=text)


def block_stop(**block: Any) -> SimpleNamespace:
    return SimpleNamespace(type="content_block_stop", content_block=SimpleNamespace(**block))


def block_start(**block: Any) -> SimpleNamespace:
    return SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(**block))


class FakeStream:
    def __init__(self, events: Sequence[Any], message: BetaMessage) -> None:
        self._events = events
        self._message = message

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def _iterate(self) -> AsyncIterator[Any]:
        for event in self._events:
            yield event

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iterate()

    async def get_final_message(self) -> BetaMessage:
        return self._message


class FakeMessages:
    def __init__(self, script: list[tuple[Sequence[Any], BetaMessage]]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.created: list[BetaMessage] = []
        self.create_calls: list[dict[str, Any]] = []

    async def create(self, **params: Any) -> BetaMessage:
        self.create_calls.append(params)
        return self.created.pop(0)

    def stream(self, **params: Any) -> FakeStream:
        self.calls.append(params)
        events, message = self._script.pop(0)
        return FakeStream(events, message)


class FakeBinary:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class FakeFiles:
    def __init__(self) -> None:
        self.files: dict[str, tuple[str, str, bytes]] = {}

    async def download(self, file_id: str) -> FakeBinary:
        return FakeBinary(self.files[file_id][2])

    async def retrieve_metadata(self, file_id: str) -> SimpleNamespace:
        name, mime, _ = self.files[file_id]
        return SimpleNamespace(filename=name, mime_type=mime)


class FakeModels:
    def __init__(self) -> None:
        self.info = SimpleNamespace(capabilities=None)
        self.retrieved: list[str] = []

    async def retrieve(self, model: str) -> SimpleNamespace:
        self.retrieved.append(model)
        return self.info


class FakeAnthropic:
    def __init__(self, script: list[tuple[Sequence[Any], BetaMessage]]) -> None:
        self.messages = FakeMessages(script)
        self.beta = SimpleNamespace(messages=self.messages)
        self.files = FakeFiles()
        self.models = FakeModels()

    def as_client(self) -> AsyncAnthropic:
        return cast(AsyncAnthropic, self)

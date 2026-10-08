import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from tgagent.agent.events import AgentEvent, FileProduced, TextDelta, TurnResult
from tgagent.agent.pricing import TurnUsage
from tgagent.agent.runner import AgentRunner
from tgagent.agent.tools import AgentOptions, ToolContext
from tgagent.context.builder import ContentBuilder
from tgagent.context.media import MediaService
from tgagent.domain import NormalizedMessage
from tgagent.services.turns import TurnRequest, TurnService
from tgagent.storage.db import SessionFactory
from tgagent.storage.repos import ChatLogRepo, ConversationRepo, UsageRepo

pytestmark = pytest.mark.db

TEXT = {"type": "text", "text": "ответ"}


class ScriptedRunner:
    model = "claude-haiku-5-5"

    def __init__(self, results: list[TurnResult], *, hang: bool = False) -> None:
        self.results = results
        self.hang = hang
        self.received: list[list[dict[str, Any]]] = []

    async def run(
        self,
        messages: list[dict[str, Any]],
        options: AgentOptions,
        ctx: ToolContext,
        *,
        container_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        self.received.append(messages)
        yield TextDelta("част")
        if self.hang:
            await asyncio.Event().wait()
        yield self.results.pop(0)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []
        self.finished: list[str] = []
        self.failed: list[str] = []
        self.started = False
        self._next = 500

    async def start(self) -> None:
        self.started = True

    async def on_event(self, event: AgentEvent) -> None:
        self.events.append(event)

    async def finish(self, markdown: str, files: Sequence[FileProduced]) -> list[int]:
        self.finished.append(markdown)
        self._next += 1
        return [self._next]

    async def fail(self, text: str) -> None:
        self.failed.append(text)


class NoMedia:
    async def describe(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("no media in these tests")


def result(*nodes: tuple[str, list[dict[str, Any]]], refused: bool = False) -> TurnResult:
    return TurnResult(
        nodes=list(nodes),  # type: ignore[arg-type]
        text="ответ",
        thinking="",
        usage=TurnUsage(),
        container=None,
        stop_reason="refusal" if refused else "end_turn",
        refused=refused,
    )


def message(
    message_id: int, text: str, *, chat_id: int = 7, reply_to: int | None = None
) -> NormalizedMessage:
    return NormalizedMessage(
        chat_id=chat_id,
        message_id=message_id,
        thread_id=None,
        sender_id=1,
        sender_name="Аня",
        date=datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
        text=text,
        reply_to_message_id=reply_to,
    )


def service(sessions: SessionFactory, runner: ScriptedRunner) -> TurnService:
    builder = ContentBuilder(cast(MediaService, NoMedia()), ZoneInfo("UTC"))
    return TurnService(
        cast(AgentRunner, runner),
        builder,
        ConversationRepo(sessions),
        UsageRepo(sessions),
        ChatLogRepo(sessions),
        tz=ZoneInfo("UTC"),
    )


def private(trigger: NormalizedMessage) -> TurnRequest:
    return TurnRequest(kind="private", chat_id=7, thread_id=None, chat_title=None, user_id=1, trigger=trigger)


async def test_private_turn_persists_nodes_head_mapping_and_usage(sessions: SessionFactory) -> None:
    runner = ScriptedRunner([result(("assistant", [TEXT]))])
    sink = RecordingSink()

    await service(sessions, runner).run(private(message(1, "привет")), sink)

    repo = ConversationRepo(sessions)
    conversation = await repo.active(7, None)
    assert conversation is not None
    assert conversation.head_node_id is not None
    node = await repo.node_for_message(7, 501)
    assert node is not None
    assert node.id == conversation.head_node_id
    assert node.content == [TEXT]
    assert sink.started
    assert sink.finished == ["ответ"]
    assert TextDelta("част") in sink.events
    assert (await UsageRepo(sessions).totals(datetime(2000, 1, 1, tzinfo=UTC))).requests == 1
    first_turn = runner.received[0]
    assert first_turn[0]["role"] == "user"
    assert "<environment" in first_turn[0]["content"][0]["text"]


async def test_second_private_message_continues_from_head(sessions: SessionFactory) -> None:
    runner = ScriptedRunner([result(("assistant", [TEXT])), result(("assistant", [TEXT]))])
    turns = service(sessions, runner)

    await turns.run(private(message(1, "раз")), RecordingSink())
    await turns.run(private(message(2, "два")), RecordingSink())

    second = runner.received[1]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert "<environment" not in second[2]["content"][0]["text"]


async def test_group_reply_to_bot_continues_its_branch(sessions: SessionFactory) -> None:
    runner = ScriptedRunner([result(("assistant", [TEXT])), result(("assistant", [TEXT]))])
    turns = service(sessions, runner)
    group = TurnRequest(
        kind="group",
        chat_id=-100,
        thread_id=None,
        chat_title="Team",
        user_id=1,
        trigger=message(1, "@bot hi", chat_id=-100),
    )

    await turns.run(group, RecordingSink())
    follow_up = TurnRequest(
        kind="group",
        chat_id=-100,
        thread_id=None,
        chat_title="Team",
        user_id=2,
        trigger=message(5, "а подробнее?", chat_id=-100, reply_to=501),
    )
    await turns.run(follow_up, RecordingSink())

    assert [m["role"] for m in runner.received[1]] == ["user", "assistant", "user"]


async def test_unmapped_reply_starts_new_conversation_with_context(sessions: SessionFactory) -> None:
    runner = ScriptedRunner([result(("assistant", [TEXT]))])
    request = TurnRequest(
        kind="group",
        chat_id=-100,
        thread_id=None,
        chat_title="Team",
        user_id=1,
        trigger=message(9, "@bot это правда?", chat_id=-100, reply_to=8),
        reply_context=(message(8, "земля плоская", chat_id=-100),),
    )

    await service(sessions, runner).run(request, RecordingSink())

    text = runner.received[0][0]["content"][0]["text"]
    assert "<context>" in text
    assert "земля плоская" in text


async def test_stop_persists_only_partial_text(sessions: SessionFactory) -> None:
    runner = ScriptedRunner([], hang=True)
    sink = RecordingSink()
    task = asyncio.create_task(service(sessions, runner).run(private(message(1, "долгий вопрос")), sink))
    await asyncio.sleep(0.2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sink.finished == ["част\n\n_⏹ Остановлено_"]
    node = await ConversationRepo(sessions).node_for_message(7, 501)
    assert node is not None
    assert node.content == [{"type": "text", "text": "част"}]


async def test_refusal_keeps_head_and_reports(sessions: SessionFactory) -> None:
    runner = ScriptedRunner([result(("assistant", [TEXT])), result(refused=True)])
    turns = service(sessions, runner)
    await turns.run(private(message(1, "нормальный")), RecordingSink())
    repo = ConversationRepo(sessions)
    head_before = (await repo.active(7, None)).head_node_id  # type: ignore[union-attr]
    sink = RecordingSink()

    await turns.run(private(message(2, "плохой")), sink)

    assert sink.failed == ["Не могу помочь с этим запросом."]
    assert (await repo.active(7, None)).head_node_id == head_before  # type: ignore[union-attr]


async def test_private_reply_to_unknown_message_adds_context_to_ongoing_chat(
    sessions: SessionFactory,
) -> None:
    runner = ScriptedRunner([result(("assistant", [TEXT])), result(("assistant", [TEXT]))])
    turns = service(sessions, runner)
    await turns.run(private(message(1, "привет")), RecordingSink())
    forwarded = message(40, "пересланная новость")
    request = TurnRequest(
        kind="private",
        chat_id=7,
        thread_id=None,
        chat_title=None,
        user_id=1,
        trigger=message(41, "это правда?", reply_to=40),
        reply_context=(forwarded,),
    )

    await turns.run(request, RecordingSink())

    last_turn = runner.received[1][-1]["content"][0]["text"]
    assert [m["role"] for m in runner.received[1]] == ["user", "assistant", "user"]
    assert "<context>" in last_turn
    assert "пересланная новость" in last_turn


async def test_turn_logs_timing_breakdown(sessions: SessionFactory, caplog: pytest.LogCaptureFixture) -> None:
    runner = ScriptedRunner([result(("assistant", [TEXT]))])

    with caplog.at_level("INFO", logger="tgagent.services.turns"):
        await service(sessions, runner).run(private(message(1, "привет")), RecordingSink())

    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("turn 7 private"))
    assert "prepare=" in line
    assert "first_output=" in line
    assert "total=" in line

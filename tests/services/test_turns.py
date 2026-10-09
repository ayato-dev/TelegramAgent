import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from tgagent.agent.events import AgentEvent, FileProduced, TextDelta, TurnResult
from tgagent.agent.models import CATALOG, ModelSpec
from tgagent.agent.pricing import TurnUsage
from tgagent.agent.registry import ProviderRegistry
from tgagent.agent.tools import AgentOptions, ToolContext
from tgagent.context.builder import ContentBuilder, TurnInput
from tgagent.context.media import MediaService
from tgagent.domain import NormalizedMessage
from tgagent.i18n import t
from tgagent.services.turns import TurnRequest, TurnService
from tgagent.storage.db import SessionFactory
from tgagent.storage.models import UsageEvent
from tgagent.storage.repos import ChatLogRepo, Content, ConversationRepo, NodeRecord, UsageRepo

pytestmark = pytest.mark.db

TEXT = {"type": "text", "text": "ответ"}
HAIKU = CATALOG["anthropic:claude-haiku-5-5"]
OSS = CATALOG["groq:openai/gpt-oss-120b"]


class NoMedia:
    async def describe(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("no media in these tests")


class ScriptedRunner:
    def __init__(
        self,
        results: list[TurnResult],
        *,
        spec: ModelSpec = HAIKU,
        hang: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.spec = spec
        self.results = results
        self.hang = hang
        self.error = error
        self.received: list[list[dict[str, Any]]] = []
        self.prompts: list[str] = []
        self.summary: str | Exception = "краткое содержание"
        self._builder = ContentBuilder(cast(MediaService, NoMedia()), ZoneInfo("UTC"))

    async def encode_user(self, turn: TurnInput, *, include_author: bool, code_enabled: bool) -> Content:
        return await self._builder.build(turn, include_author=include_author, code_enabled=code_enabled)

    async def complete(self, prompt: str, *, max_tokens: int) -> tuple[str, TurnUsage]:
        self.prompts.append(prompt)
        if isinstance(self.summary, Exception):
            raise self.summary
        usage = TurnUsage()
        usage.add_iteration(100, 20, 0, 0)
        return self.summary, usage

    async def run(
        self,
        path: Sequence[NodeRecord],
        options: AgentOptions,
        ctx: ToolContext,
        *,
        container_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        self.received.append([{"role": node.role, "content": node.content} for node in path])
        if self.error is not None:
            raise self.error
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


def result(
    *nodes: tuple[str, list[dict[str, Any]]], refused: bool = False, prompt_tokens: int = 0
) -> TurnResult:
    usage = TurnUsage()
    usage.add_iteration(prompt_tokens, 5, 0, 0)
    return TurnResult(
        nodes=list(nodes),  # type: ignore[arg-type]
        text="ответ",
        thinking="",
        usage=usage,
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


def service(sessions: SessionFactory, *runners: ScriptedRunner) -> TurnService:
    by_key = {runner.spec.key: runner for runner in runners}
    registry = ProviderRegistry(
        [runner.spec for runner in runners],
        runners[0].spec.key,
        {runner.spec.provider: lambda spec: by_key[spec.key] for runner in runners},
    )
    return TurnService(
        registry,
        ConversationRepo(sessions),
        UsageRepo(sessions),
        ChatLogRepo(sessions),
        tz=ZoneInfo("UTC"),
    )


def private(trigger: NormalizedMessage, model: str | None = None) -> TurnRequest:
    return TurnRequest(
        kind="private",
        chat_id=7,
        thread_id=None,
        chat_title=None,
        user_id=1,
        trigger=trigger,
        options=AgentOptions(model=model),
        lang="ru",
    )


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


async def test_rejected_attachment_gets_a_clear_message(sessions: SessionFactory) -> None:
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.BadRequestError(
        "Unsupported document file format: application/pkcs7-signature",
        response=httpx2.Response(400, request=request),
        body=None,
    )
    sink = RecordingSink()

    await service(sessions, ScriptedRunner([], error=error)).run(private(message(1, "что в файле?")), sink)

    assert sink.failed == [t("ru", "error.bad_attachment")]


async def test_changing_the_model_starts_a_fresh_private_conversation(sessions: SessionFactory) -> None:
    haiku = ScriptedRunner([result(("assistant", [TEXT]))])
    oss = ScriptedRunner([result(("assistant", [TEXT]))], spec=OSS)
    turns = service(sessions, haiku, oss)
    await turns.run(private(message(1, "раз")), RecordingSink())

    await turns.run(private(message(2, "два"), model=OSS.key), RecordingSink())

    assert [m["role"] for m in oss.received[0]] == ["user"]
    assert "<environment" in oss.received[0][0]["content"][0]["text"]
    active = await ConversationRepo(sessions).active(7, None)
    assert active is not None
    assert active.model == OSS.key


async def test_reply_continues_in_the_conversations_own_model(sessions: SessionFactory) -> None:
    haiku = ScriptedRunner([result(("assistant", [TEXT])), result(("assistant", [TEXT]))])
    oss = ScriptedRunner([], spec=OSS)
    turns = service(sessions, haiku, oss)
    await turns.run(private(message(1, "раз")), RecordingSink())

    await turns.run(private(message(2, "а подробнее?", reply_to=501), model=OSS.key), RecordingSink())

    assert [m["role"] for m in haiku.received[1]] == ["user", "assistant", "user"]
    assert oss.received == []


async def test_empty_conversation_is_pinned_to_the_chosen_model(sessions: SessionFactory) -> None:
    repo = ConversationRepo(sessions)
    created = await repo.create(7, None, "private", title_pending=True)
    oss = ScriptedRunner([result(("assistant", [TEXT]))], spec=OSS)

    await service(sessions, ScriptedRunner([]), oss).run(
        private(message(1, "привет"), model=OSS.key), RecordingSink()
    )

    active = await repo.active(7, None)
    assert active is not None
    assert (active.id, active.model) == (created.id, OSS.key)


async def test_reply_into_a_conversation_of_a_removed_model_starts_over(sessions: SessionFactory) -> None:
    repo = ConversationRepo(sessions)
    old = await repo.create(7, None, "private", model="openai:gpt-6-luna")
    node = await repo.add_node(old.id, None, "assistant", [TEXT])
    await repo.map_messages(7, [300], node)
    await repo.deactivate(7, None)
    haiku = ScriptedRunner([result(("assistant", [TEXT]))])
    request = TurnRequest(
        kind="private",
        chat_id=7,
        thread_id=None,
        chat_title=None,
        user_id=1,
        trigger=message(5, "что тут?", reply_to=300),
        reply_context=(message(300, "старый ответ"),),
    )

    await service(sessions, haiku).run(request, RecordingSink())

    assert [m["role"] for m in haiku.received[0]] == ["user"]
    assert "старый ответ" in haiku.received[0][0]["content"][0]["text"]


async def test_usage_and_prompt_size_are_recorded_per_model(sessions: SessionFactory) -> None:
    oss = ScriptedRunner([result(("assistant", [TEXT]), prompt_tokens=1234)], spec=OSS)

    await service(sessions, oss).run(private(message(1, "привет")), RecordingSink())

    active = await ConversationRepo(sessions).active(7, None)
    assert active is not None
    assert active.last_prompt_tokens == 1234
    async with sessions() as session:
        models = list(await session.scalars(select(UsageEvent.model)))
    assert models == [OSS.key]


CLIENT_SIDE = replace(OSS, context_trigger=1_000)


async def test_long_client_side_conversation_is_summarised_first(sessions: SessionFactory) -> None:
    oss = ScriptedRunner(
        [
            result(("assistant", [TEXT]), prompt_tokens=5_000),
            result(("assistant", [TEXT]), prompt_tokens=600),
        ],
        spec=CLIENT_SIDE,
    )
    turns = service(sessions, oss)
    await turns.run(private(message(1, "раз")), RecordingSink())

    await turns.run(private(message(2, "два")), RecordingSink())

    assert "раз" in oss.prompts[0] and "ответ" in oss.prompts[0]
    second = oss.received[1]
    assert second[0] == {"role": "user", "content": [{"type": "compaction", "content": "краткое содержание"}]}
    assert [m["role"] for m in second] == ["user", "user"]
    assert "<environment" in second[1]["content"][0]["text"]
    active = await ConversationRepo(sessions).active(7, None)
    assert active is not None and active.last_prompt_tokens == 600
    async with sessions() as session:
        kinds = list(await session.scalars(select(UsageEvent.kind).order_by(UsageEvent.id)))
    assert kinds == ["chat", "compaction", "chat"]


async def test_short_conversations_and_server_compaction_are_left_alone(sessions: SessionFactory) -> None:
    oss = ScriptedRunner([result(("assistant", [TEXT]), prompt_tokens=900)] * 2, spec=CLIENT_SIDE)
    haiku = ScriptedRunner([result(("assistant", [TEXT]), prompt_tokens=500_000)] * 2)

    for runner, chat in ((oss, 7), (haiku, 8)):
        turns = service(sessions, runner)
        for message_id in (1, 2):
            request = replace(private(message(message_id, "текст", chat_id=chat)), chat_id=chat)
            await turns.run(request, RecordingSink())

    assert oss.prompts == [] and haiku.prompts == []


async def test_failed_summary_keeps_the_full_history(sessions: SessionFactory) -> None:
    oss = ScriptedRunner(
        [result(("assistant", [TEXT]), prompt_tokens=5_000), result(("assistant", [TEXT]))], spec=CLIENT_SIDE
    )
    oss.summary = RuntimeError("rate limited")
    turns = service(sessions, oss)
    await turns.run(private(message(1, "раз")), RecordingSink())

    await turns.run(private(message(2, "два")), RecordingSink())

    assert [m["role"] for m in oss.received[1]] == ["user", "assistant", "user"]


async def test_rate_limit_tells_when_to_retry(sessions: SessionFactory) -> None:
    import httpx2
    import openai

    response = httpx2.Response(
        429, request=httpx2.Request("POST", "https://api.groq.com"), headers={"retry-after": "20"}
    )
    error = openai.RateLimitError("Rate limit reached for tokens per minute", response=response, body=None)
    sink = RecordingSink()

    await service(sessions, ScriptedRunner([], spec=OSS, error=error)).run(
        private(message(1, "привет")), sink
    )

    assert len(sink.failed) == 1
    assert "20 с" in sink.failed[0]


async def test_typing_shows_while_the_history_is_being_summarised(sessions: SessionFactory) -> None:
    oss = ScriptedRunner(
        [result(("assistant", [TEXT]), prompt_tokens=5_000), result(("assistant", [TEXT]))], spec=CLIENT_SIDE
    )
    turns = service(sessions, oss)
    await turns.run(private(message(1, "раз")), RecordingSink())
    sink = RecordingSink()
    started_when_summarising: list[bool] = []
    summarise = oss.complete

    async def watched(prompt: str, *, max_tokens: int) -> tuple[str, TurnUsage]:
        started_when_summarising.append(sink.started)
        return await summarise(prompt, max_tokens=max_tokens)

    oss.complete = watched  # type: ignore[method-assign]

    await turns.run(private(message(2, "два")), sink)

    assert started_when_summarising == [True]


async def test_failure_while_preparing_the_turn_is_reported(sessions: SessionFactory) -> None:
    oss = ScriptedRunner([], spec=OSS)

    async def broken(*args: Any, **kwargs: Any) -> Content:
        raise RuntimeError("download failed")

    oss.encode_user = broken  # type: ignore[method-assign]
    sink = RecordingSink()

    await service(sessions, oss).run(private(message(1, "фото")), sink)

    assert sink.started
    assert sink.failed == [t("ru", "failure")]

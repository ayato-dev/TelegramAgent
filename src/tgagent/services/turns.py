import asyncio
import logging
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from zoneinfo import ZoneInfo

from tgagent.agent.events import FileProduced, TextDelta, TurnResult
from tgagent.agent.pricing import claude_cost
from tgagent.agent.runner import AgentRunner
from tgagent.agent.tools import AgentOptions, ToolContext
from tgagent.context.builder import ContentBuilder, TurnInput
from tgagent.context.tree import build_api_messages
from tgagent.domain import NormalizedMessage
from tgagent.storage.repos import (
    ChatLogRepo,
    ConversationKind,
    ConversationRecord,
    ConversationRepo,
    UsageRecord,
    UsageRepo,
)
from tgagent.telegram.render import compose_final
from tgagent.telegram.sinks import ResponseSink

log = logging.getLogger(__name__)

FAILURE_TEXT = "⚠️ Не удалось получить ответ. Попробуйте ещё раз чуть позже."
REFUSAL_TEXT = "Не могу помочь с этим запросом."
STOPPED_SUFFIX = "\n\n_⏹ Остановлено_"
KIND_LABELS = {
    "private": "личный чат",
    "group": "группа",
    "guest": "чужой чат (гостевой вызов)",
    "reminder": "личный чат",
}

type TitleCallback = Callable[[ConversationRecord, str, str], Coroutine[None, None, None]]


@dataclass(frozen=True, slots=True)
class TurnRequest:
    kind: ConversationKind
    chat_id: int
    thread_id: int | None
    chat_title: str | None
    user_id: int
    trigger: NormalizedMessage
    reply_context: Sequence[NormalizedMessage] = ()
    options: AgentOptions = field(default_factory=AgentOptions)
    title_pending: bool = False

    @property
    def include_author(self) -> bool:
        return self.kind in ("group", "guest")


class TurnService:
    """Runs one user turn end to end: resolve the branch, call the agent, stream, persist."""

    def __init__(
        self,
        runner: AgentRunner,
        builder: ContentBuilder,
        conversations: ConversationRepo,
        usage: UsageRepo,
        chat_log: ChatLogRepo,
        *,
        tz: ZoneInfo,
        bot_name: str = "бот",
        on_title: TitleCallback | None = None,
    ) -> None:
        self._runner = runner
        self._builder = builder
        self._conversations = conversations
        self._usage = usage
        self._chat_log = chat_log
        self._tz = tz
        self._bot_name = bot_name
        self._on_title = on_title
        self._background: set[asyncio.Task[None]] = set()

    async def _resolve(self, request: TurnRequest) -> tuple[ConversationRecord, int | None]:
        reply_to = request.trigger.reply_to_message_id
        if reply_to is not None:
            node = await self._conversations.node_for_message(request.chat_id, reply_to)
            if node is not None and (conversation := await self._conversations.get(node.conversation_id)):
                return conversation, node.id
        if request.kind == "private":
            active = await self._conversations.active(request.chat_id, request.thread_id)
            if active is not None:
                return active, active.head_node_id
            created = await self._conversations.create(
                request.chat_id, request.thread_id, "private", title_pending=request.title_pending
            )
            return created, None
        return await self._conversations.create(request.chat_id, request.thread_id, request.kind), None

    def _environment(self, request: TurnRequest) -> str:
        attrs = [f'chat="{KIND_LABELS[request.kind]}"']
        if request.chat_title:
            attrs.append(f'title="{escape(request.chat_title)}"')
        attrs.append(f'timezone="{self._tz.key}"')
        return f"<environment {' '.join(attrs)}/>"

    async def run(self, request: TurnRequest, sink: ResponseSink) -> None:
        conversation, parent_id = await self._resolve(request)
        is_new = parent_id is None
        turn = TurnInput(
            request.trigger,
            context=request.reply_context if is_new else (),
            environment=self._environment(request) if is_new else None,
        )
        content = await self._builder.build(
            turn, include_author=request.include_author, code_enabled=request.options.code
        )
        user_node = await self._conversations.add_node(conversation.id, parent_id, "user", content)
        messages = build_api_messages(await self._conversations.path(user_node))
        container = conversation.container_id
        if conversation.container_expires_at and conversation.container_expires_at <= datetime.now(UTC):
            container = None
        ctx = ToolContext(
            request.chat_id, request.thread_id, request.user_id, request.kind, request.trigger.message_id
        )

        await sink.start()
        partial: list[str] = []
        files: list[FileProduced] = []
        result: TurnResult | None = None
        try:
            async for event in self._runner.run(messages, request.options, ctx, container_id=container):
                if isinstance(event, TurnResult):
                    result = event
                elif isinstance(event, FileProduced):
                    files.append(event)
                else:
                    if isinstance(event, TextDelta):
                        partial.append(event.text)
                    await sink.on_event(event)
        except asyncio.CancelledError:
            await self._stopped(request, conversation, user_node, "".join(partial).strip(), files, sink)
            raise
        except Exception:
            log.exception("agent turn failed in chat %s", request.chat_id)
            await sink.fail(FAILURE_TEXT)
            return

        assert result is not None
        await self._record_usage(request, result)
        if result.refused or not result.nodes:
            await sink.fail(REFUSAL_TEXT)
            return

        ids = await self._conversations.add_nodes(conversation.id, user_node, result.nodes)
        markdown = compose_final(result.text, result.thinking if request.options.show_thinking else "")
        if not markdown.strip():
            markdown = "Готово." if files else "…"
        await self._deliver(request, conversation, ids[-1], markdown, result.text, files, sink)
        if result.container:
            await self._conversations.set_container(
                conversation.id, result.container.id, result.container.expires_at
            )
        if conversation.title_pending and self._on_title is not None:
            question = request.trigger.text or ""
            task = asyncio.create_task(self._on_title(conversation, question, result.text))
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    async def _deliver(
        self,
        request: TurnRequest,
        conversation: ConversationRecord,
        node_id: int,
        markdown: str,
        plain_text: str,
        files: Sequence[FileProduced],
        sink: ResponseSink,
    ) -> None:
        try:
            message_ids = await sink.finish(markdown, files)
        except Exception:
            log.exception("could not deliver answer to chat %s", request.chat_id)
            message_ids = []
        await self._conversations.map_messages(request.chat_id, message_ids, node_id)
        if request.kind in ("private", "reminder"):
            await self._conversations.set_head(conversation.id, node_id)
        if request.kind == "group" and message_ids:
            await self._chat_log.add(
                NormalizedMessage(
                    chat_id=request.chat_id,
                    message_id=message_ids[0],
                    thread_id=request.thread_id,
                    sender_id=None,
                    sender_name=self._bot_name,
                    date=datetime.now(UTC),
                    text=plain_text,
                    reply_to_message_id=request.trigger.message_id,
                    from_bot=True,
                )
            )

    async def _stopped(
        self,
        request: TurnRequest,
        conversation: ConversationRecord,
        user_node: int,
        partial: str,
        files: Sequence[FileProduced],
        sink: ResponseSink,
    ) -> None:
        """User pressed Stop: keep only the visible text so the history stays valid."""
        if not partial:
            await sink.fail("⏹ Остановлено")
            return
        node_id = await self._conversations.add_node(
            conversation.id, user_node, "assistant", [{"type": "text", "text": partial}]
        )
        await self._deliver(request, conversation, node_id, partial + STOPPED_SUFFIX, partial, files, sink)

    async def _record_usage(self, request: TurnRequest, result: TurnResult) -> None:
        usage = result.usage
        record = UsageRecord(
            user_id=request.user_id,
            chat_id=request.chat_id,
            kind="chat",
            model=self._runner.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            web_search_requests=usage.web_search_requests,
        )
        await self._usage.add(record.with_cost(claude_cost(self._runner.model, usage)))

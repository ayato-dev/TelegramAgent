import asyncio
import logging
import time
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from html import escape
from zoneinfo import ZoneInfo

import anthropic

from tgagent.agent.base import LLMRunner, plain_text
from tgagent.agent.events import FileProduced, TextDelta, TurnResult
from tgagent.agent.pricing import turn_cost
from tgagent.agent.registry import ProviderRegistry
from tgagent.agent.tools import AgentOptions, ToolContext
from tgagent.context.builder import TurnInput
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
ATTACHMENT_TEXT = (
    "⚠️ Не смог прочитать вложение: такой формат файла модель не принимает. Пришлите PDF, текст или картинку."
)
ATTACHMENT_ERROR_MARKERS = ("document", "image", "file format", "media type")
STOPPED_SUFFIX = "\n\n_⏹ Остановлено_"
SUMMARY_PROMPT = (
    "Below is a conversation between a user and you, an AI assistant in Telegram. Summarise it so the "
    "conversation can go on without the full history: keep facts, names, numbers, dates, decisions, the "
    "user's preferences, open questions and anything you promised to do. Write in the conversation's "
    "language as short bullet points.\n\n<conversation>\n{transcript}\n</conversation>"
)
SUMMARY_MAX_TOKENS = 2_000
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
    album: Sequence[NormalizedMessage] = ()
    options: AgentOptions = field(default_factory=AgentOptions)
    title_pending: bool = False

    @property
    def include_author(self) -> bool:
        return self.kind in ("group", "guest")


class TurnService:
    """Runs one user turn end to end: resolve the branch, call the agent, stream, persist."""

    def __init__(
        self,
        runners: ProviderRegistry,
        conversations: ConversationRepo,
        usage: UsageRepo,
        chat_log: ChatLogRepo,
        *,
        tz: ZoneInfo,
        bot_name: str = "бот",
        on_title: TitleCallback | None = None,
    ) -> None:
        self._runners = runners
        self._conversations = conversations
        self._usage = usage
        self._chat_log = chat_log
        self._tz = tz
        self._bot_name = bot_name
        self._on_title = on_title
        self._background: set[asyncio.Task[None]] = set()

    async def _resolve(self, request: TurnRequest) -> tuple[ConversationRecord, int | None, bool]:
        """Where the turn attaches: (conversation, parent node, whether a reply picked the branch).

        A conversation stays with the model it was started with: replies continue in it, while
        choosing another model starts a fresh private conversation.
        """
        chosen = request.options.model
        model = chosen if chosen and self._runners.supports(chosen) else self._runners.default_model
        reply_to = request.trigger.reply_to_message_id
        if reply_to is not None:
            node = await self._conversations.node_for_message(request.chat_id, reply_to)
            conversation = await self._conversations.get(node.conversation_id) if node else None
            if node is not None and conversation and self._runners.supports(conversation.model):
                return conversation, node.id, True
        if request.kind == "private":
            active = await self._conversations.active(request.chat_id, request.thread_id)
            if active is not None and active.model == model:
                return active, active.head_node_id, False
            if active is not None and active.head_node_id is None:
                await self._conversations.set_model(active.id, model)
                return replace(active, model=model), None, False
            if active is not None:
                await self._conversations.deactivate(request.chat_id, request.thread_id)
            created = await self._conversations.create(
                request.chat_id,
                request.thread_id,
                "private",
                title_pending=request.title_pending,
                model=model,
            )
            return created, None, False
        created = await self._conversations.create(
            request.chat_id, request.thread_id, request.kind, model=model
        )
        return created, None, False

    async def _compact(
        self, request: TurnRequest, conversation: ConversationRecord, parent_id: int | None, runner: LLMRunner
    ) -> int | None:
        """Client-side compaction for models without it on the server: once the last prompt outgrew
        the model's budget, the branch is summarised into a node the next turn continues from."""
        spec = runner.spec
        if (
            spec.compaction != "client"
            or parent_id is None
            or conversation.last_prompt_tokens <= spec.context_trigger
        ):
            return None
        path = await self._conversations.path(parent_id)
        transcript = "\n\n".join(
            f"{'User' if node.role == 'user' else 'Assistant'}: {text}"
            for node in path
            if (text := plain_text(node.content))
        )
        try:
            summary, usage = await runner.complete(
                SUMMARY_PROMPT.format(transcript=transcript),
                max_tokens=min(SUMMARY_MAX_TOKENS, spec.max_output),
            )
        except Exception:
            log.warning("could not compact conversation %s", conversation.id, exc_info=True)
            return None
        record = UsageRecord(
            request.user_id,
            request.chat_id,
            "compaction",
            spec.key,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
        )
        await self._usage.add(record.with_cost(turn_cost(spec, usage)))
        if not summary.strip():
            return None
        log.info(
            "compacted conversation %s at %d prompt tokens", conversation.id, conversation.last_prompt_tokens
        )
        return await self._conversations.add_node(
            conversation.id, parent_id, "user", [{"type": "compaction", "content": summary.strip()}]
        )

    def _environment(self, request: TurnRequest) -> str:
        attrs = [f'chat="{KIND_LABELS[request.kind]}"']
        if request.chat_title:
            attrs.append(f'title="{escape(request.chat_title)}"')
        attrs.append(f'timezone="{self._tz.key}"')
        return f"<environment {' '.join(attrs)}/>"

    async def run(self, request: TurnRequest, sink: ResponseSink) -> None:
        started = time.monotonic()
        conversation, parent_id, from_reply = await self._resolve(request)
        assert conversation.model is not None
        runner = self._runners.runner(conversation.model)
        compacted = await self._compact(request, conversation, parent_id, runner)
        turn = TurnInput(
            request.trigger,
            context=() if from_reply else request.reply_context,
            album=request.album,
            environment=self._environment(request) if parent_id is None or compacted else None,
        )
        parent_id = compacted or parent_id
        content = await runner.encode_user(
            turn, include_author=request.include_author, code_enabled=request.options.code
        )
        user_node = await self._conversations.add_node(conversation.id, parent_id, "user", content)
        path = await self._conversations.path(user_node)
        container = conversation.container_id
        if conversation.container_expires_at and conversation.container_expires_at <= datetime.now(UTC):
            container = None
        ctx = ToolContext(
            request.chat_id, request.thread_id, request.user_id, request.kind, request.trigger.message_id
        )

        await sink.start()
        prepared = time.monotonic() - started
        first_output: float | None = None
        partial: list[str] = []
        files: list[FileProduced] = []
        result: TurnResult | None = None
        try:
            async for event in runner.run(path, request.options, ctx, container_id=container):
                if first_output is None:
                    first_output = time.monotonic() - started
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
        except anthropic.BadRequestError as exc:
            log.exception("API rejected the turn in chat %s", request.chat_id)
            attachment = any(marker in str(exc).lower() for marker in ATTACHMENT_ERROR_MARKERS)
            await sink.fail(ATTACHMENT_TEXT if attachment else FAILURE_TEXT)
            return
        except Exception:
            log.exception("agent turn failed in chat %s", request.chat_id)
            await sink.fail(FAILURE_TEXT)
            return

        assert result is not None
        log.info(
            "turn %s %s: prepare=%.2fs first_output=%.2fs total=%.2fs",
            request.chat_id,
            request.kind,
            prepared,
            first_output or 0.0,
            time.monotonic() - started,
        )
        await self._record_usage(request, runner, result)
        await self._conversations.set_prompt_tokens(conversation.id, result.usage.last_prompt_tokens)
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

    async def _record_usage(self, request: TurnRequest, runner: LLMRunner, result: TurnResult) -> None:
        usage = result.usage
        record = UsageRecord(
            user_id=request.user_id,
            chat_id=request.chat_id,
            kind="chat",
            model=runner.spec.key,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            web_search_requests=usage.web_search_requests,
        )
        await self._usage.add(record.with_cost(turn_cost(runner.spec, usage)))

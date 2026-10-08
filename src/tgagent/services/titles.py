import logging
import re

from aiogram import Bot

from tgagent.agent.pricing import turn_cost
from tgagent.agent.registry import ProviderRegistry
from tgagent.storage.repos import ConversationRecord, ConversationRepo, UsageRecord, UsageRepo

log = logging.getLogger(__name__)

MAX_TITLE = 64
PROMPT = (
    "Придумай название для этого диалога: 2–5 слов на языке пользователя, без кавычек и точки в конце. "
    "Ответь только названием.\n\n<question>{question}</question>\n<answer>{answer}</answer>"
)


def clean_title(raw: str) -> str | None:
    lines = raw.strip().splitlines()
    if not lines:
        return None
    line = re.sub(r"^(title|название)\s*:\s*", "", lines[0].strip(), flags=re.IGNORECASE)
    line = line.strip(" \"'«»“”`*.")
    if len(line) > MAX_TITLE:
        line = line[:MAX_TITLE].rsplit(" ", 1)[0]
    return line or None


class TopicTitler:
    """Names a private-chat topic that the user created without an explicit name."""

    def __init__(
        self, runners: ProviderRegistry, bot: Bot, conversations: ConversationRepo, usage: UsageRepo
    ) -> None:
        self._runners = runners
        self._bot = bot
        self._conversations = conversations
        self._usage = usage

    async def __call__(self, conversation: ConversationRecord, question: str, answer: str) -> None:
        try:
            if conversation.thread_id is not None and (
                title := await self._generate(conversation, question, answer)
            ):
                await self._bot.edit_forum_topic(
                    chat_id=conversation.chat_id, message_thread_id=conversation.thread_id, name=title
                )
        except Exception:
            log.warning("could not title topic of conversation %s", conversation.id, exc_info=True)
        finally:
            await self._conversations.clear_title_pending(conversation.id)

    async def _generate(self, conversation: ConversationRecord, question: str, answer: str) -> str | None:
        runner = self._runners.runner(conversation.model or self._runners.default_model)
        prompt = PROMPT.format(question=question[:2000], answer=answer[:2000])
        text, usage = await runner.complete(prompt, max_tokens=1024)
        record = UsageRecord(
            None,
            conversation.chat_id,
            "title",
            runner.spec.key,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        await self._usage.add(record.with_cost(turn_cost(runner.spec, usage)))
        return clean_title(text)

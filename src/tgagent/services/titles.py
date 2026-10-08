import logging
import re

from aiogram import Bot
from anthropic import AsyncAnthropic

from tgagent.agent.pricing import TurnUsage, claude_cost
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
        self,
        client: AsyncAnthropic,
        bot: Bot,
        conversations: ConversationRepo,
        usage: UsageRepo,
        model: str,
    ) -> None:
        self._client = client
        self._bot = bot
        self._conversations = conversations
        self._usage = usage
        self._model = model

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
        response = await self._client.beta.messages.create(
            model=self._model,
            max_tokens=1024,
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
            messages=[
                {"role": "user", "content": PROMPT.format(question=question[:2000], answer=answer[:2000])}
            ],
        )
        usage = TurnUsage()
        usage.add(response.usage)
        record = UsageRecord(
            None,
            conversation.chat_id,
            "title",
            self._model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        await self._usage.add(record.with_cost(claude_cost(self._model, usage)))
        text = "".join(block.text for block in response.content if block.type == "text")
        return clean_title(text)

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

from tgagent.agent.models import CATALOG
from tgagent.agent.pricing import TurnUsage
from tgagent.agent.registry import ProviderRegistry
from tgagent.services.titles import TopicTitler, clean_title
from tgagent.services.usage_report import UsageReport
from tgagent.storage.repos import (
    ConversationRecord,
    ConversationRepo,
    UsageRecord,
    UsageRepo,
    UsageTotals,
    UserRepo,
)


def test_clean_title() -> None:
    assert clean_title("  «Курс евро на сегодня».\nлишнее") == "Курс евро на сегодня"
    assert clean_title("Title: Python decorators") == "Python decorators"
    assert clean_title("   ") is None
    assert len(clean_title("слово " * 40) or "") <= 64


class FakeTitleRunner:
    spec = CATALOG["groq:openai/gpt-oss-120b"]

    def __init__(self) -> None:
        self.prompts: list[tuple[str, int]] = []

    async def complete(
        self, prompt: str, *, max_tokens: int, system: str | None = None
    ) -> tuple[str, TurnUsage]:
        self.prompts.append((prompt, max_tokens))
        usage = TurnUsage()
        usage.add_iteration(100, 10, 0, 0)
        return '"Рецепт борща"', usage


class FakeRegistry:
    default_model = "anthropic:claude-haiku-5-5"

    def __init__(self) -> None:
        self.title_runner = FakeTitleRunner()
        self.asked: list[str] = []

    def runner(self, key: str) -> FakeTitleRunner:
        self.asked.append(key)
        return self.title_runner


class FakeTopicBot:
    def __init__(self) -> None:
        self.edits: list[dict[str, Any]] = []

    async def edit_forum_topic(self, **params: Any) -> bool:
        self.edits.append(params)
        return True


class FakeConversations:
    def __init__(self) -> None:
        self.cleared: list[int] = []

    async def clear_title_pending(self, conversation_id: int) -> None:
        self.cleared.append(conversation_id)


class FakeUsage:
    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    async def add(self, record: UsageRecord) -> None:
        self.records.append(record)


async def test_titler_uses_the_conversations_model_and_records_usage() -> None:
    registry = FakeRegistry()
    bot, conversations, usage = FakeTopicBot(), FakeConversations(), FakeUsage()
    titler = TopicTitler(
        cast(ProviderRegistry, registry),
        bot,  # type: ignore[arg-type]
        cast(ConversationRepo, conversations),
        cast(UsageRepo, usage),
    )
    conversation = ConversationRecord(3, 7, 11, "private", None, True, None, None, "groq:openai/gpt-oss-120b")

    await titler(conversation, "как сварить борщ?", "Возьмите свёклу…")

    assert bot.edits == [{"chat_id": 7, "message_thread_id": 11, "name": "Рецепт борща"}]
    assert conversations.cleared == [3]
    assert registry.asked == ["groq:openai/gpt-oss-120b"]
    assert "как сварить борщ?" in registry.title_runner.prompts[0][0]
    assert (usage.records[0].kind, usage.records[0].model) == ("title", "groq:openai/gpt-oss-120b")


class FakeTotals:
    async def totals(
        self, since: datetime, *, chat_id: int | None = None, user_id: int | None = None
    ) -> UsageTotals:
        return UsageTotals(4, 12_000, 3_000, 50_000, 1_000, 2, 90.0, Decimal("0.123456"))

    async def top_users(self, since: datetime, limit: int) -> list[tuple[int | None, Decimal]]:
        return [(1, Decimal("0.1"))]


class FakeUsers:
    async def names(self, user_ids: list[int]) -> dict[int, str]:
        return {1: "Аня (@anya)"}


async def test_usage_report_renders_periods_and_top_users() -> None:
    report = UsageReport(cast(UsageRepo, FakeTotals()), cast(UserRepo, FakeUsers()), ZoneInfo("UTC"))

    text = await report.render(chat_id=None, now=datetime(2026, 10, 8, 12, tzinfo=UTC), lang="ru")

    assert "| Сегодня | 4 |" in text
    assert "$0.1235" in text
    assert "Аня (@anya)" in text
    assert "1.5 мин" in text


async def test_group_usage_report_has_no_top_users() -> None:
    report = UsageReport(cast(UsageRepo, FakeTotals()), cast(UserRepo, FakeUsers()), ZoneInfo("UTC"))

    text = await report.render(chat_id=-100, now=datetime(2026, 10, 8, 12, tzinfo=UTC), lang="ru")

    assert "Аня" not in text


async def test_usage_report_in_english() -> None:
    report = UsageReport(cast(UsageRepo, FakeTotals()), cast(UserRepo, FakeUsers()), ZoneInfo("UTC"))

    text = await report.render(chat_id=None, now=datetime(2026, 10, 8, 12, tzinfo=UTC), lang="en")

    assert text.startswith("## Spending")
    assert "| Today | 4 |" in text

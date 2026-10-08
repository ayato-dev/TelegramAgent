from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import InstrumentedAttribute

from tgagent.storage.db import SessionFactory
from tgagent.storage.models import UsageEvent

UsageKind = Literal["chat", "title", "stt"]


@dataclass(frozen=True, slots=True)
class UsageRecord:
    user_id: int | None
    chat_id: int | None
    kind: UsageKind
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_search_requests: int = 0
    audio_seconds: float = 0.0
    cost_usd: Decimal = Decimal(0)

    def with_cost(self, cost: Decimal) -> "UsageRecord":
        return replace(self, cost_usd=cost)


@dataclass(frozen=True, slots=True)
class UsageTotals:
    requests: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    web_search_requests: int
    audio_seconds: float
    cost_usd: Decimal


class UsageRepo:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, record: UsageRecord) -> None:
        async with self._sessions.begin() as session:
            session.add(UsageEvent(**asdict(record)))

    async def totals(
        self, since: datetime, *, chat_id: int | None = None, user_id: int | None = None
    ) -> UsageTotals:
        e = UsageEvent
        stmt = select(
            func.count(),
            func.coalesce(func.sum(e.input_tokens), 0),
            func.coalesce(func.sum(e.output_tokens), 0),
            func.coalesce(func.sum(e.cache_read_tokens), 0),
            func.coalesce(func.sum(e.cache_write_tokens), 0),
            func.coalesce(func.sum(e.web_search_requests), 0),
            func.coalesce(func.sum(e.audio_seconds), 0.0),
            func.coalesce(func.sum(e.cost_usd), 0),
        ).where(*self._filters(since, chat_id, user_id))
        async with self._sessions() as session:
            row = (await session.execute(stmt)).one()
        return UsageTotals(
            requests=int(row[0]),
            input_tokens=int(row[1]),
            output_tokens=int(row[2]),
            cache_read_tokens=int(row[3]),
            cache_write_tokens=int(row[4]),
            web_search_requests=int(row[5]),
            audio_seconds=float(row[6]),
            cost_usd=Decimal(row[7]),
        )

    async def top_users(self, since: datetime, limit: int) -> list[tuple[int | None, Decimal]]:
        return await self._top(UsageEvent.user_id, since, limit)

    async def top_chats(self, since: datetime, limit: int) -> list[tuple[int | None, Decimal]]:
        return await self._top(UsageEvent.chat_id, since, limit)

    async def _top(
        self, column: InstrumentedAttribute[int | None], since: datetime, limit: int
    ) -> list[tuple[int | None, Decimal]]:
        cost = func.sum(UsageEvent.cost_usd)
        stmt = (
            select(column, cost)
            .where(UsageEvent.created_at >= since)
            .group_by(column)
            .order_by(cost.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = await session.execute(stmt)
        return [(key, Decimal(total)) for key, total in rows]

    @staticmethod
    def _filters(since: datetime, chat_id: int | None, user_id: int | None) -> list[ColumnElement[bool]]:
        filters = [UsageEvent.created_at >= since]
        if chat_id is not None:
            filters.append(UsageEvent.chat_id == chat_id)
        if user_id is not None:
            filters.append(UsageEvent.user_id == user_id)
        return filters

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from tgagent.storage.repos import ReminderRecord, ReminderRepo

log = logging.getLogger(__name__)

type FireCallback = Callable[[ReminderRecord], Awaitable[None]]


def utc_now() -> datetime:
    return datetime.now(UTC)


class ReminderScheduler:
    """Sleeps until the next reminder is due (or ``wake()``), then fires everything due."""

    def __init__(
        self,
        repo: ReminderRepo,
        fire: FireCallback,
        *,
        poll_interval: float = 60.0,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repo = repo
        self._fire = fire
        self._poll_interval = poll_interval
        self._clock = clock
        self._wake = asyncio.Event()

    def wake(self) -> None:
        self._wake.set()

    async def tick(self) -> int:
        """Fire everything due now; returns how many reminders fired."""
        due = await self._repo.claim_due(self._clock())
        await asyncio.gather(*(self._run_one(item) for item in due))
        return len(due)

    async def _run_one(self, item: ReminderRecord) -> None:
        try:
            await self._fire(item)
        except Exception:
            log.exception("reminder %s failed", item.id)
            await self._repo.finish(item.id, "failed")
        else:
            await self._repo.finish(item.id, "done")

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
                next_due = await self._repo.next_due()
            except Exception:
                log.exception("reminder loop iteration failed")
                next_due = None
            delay = self._poll_interval
            if next_due is not None:
                delay = min(delay, max((next_due - self._clock()).total_seconds(), 0.0))
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=delay)

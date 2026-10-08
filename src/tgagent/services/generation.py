import asyncio
from collections.abc import AsyncIterator, Hashable, Iterator
from contextlib import asynccontextmanager, contextmanager


class KeyedLocks:
    """One lock per key (chat/topic), dropped once nobody holds or waits for it."""

    def __init__(self) -> None:
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self._users: dict[Hashable, int] = {}

    @property
    def size(self) -> int:
        return len(self._locks)

    @asynccontextmanager
    async def hold(self, key: Hashable) -> AsyncIterator[None]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._users[key] = self._users.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._users[key] -= 1
            if not self._users[key]:
                del self._users[key], self._locks[key]


class GenerationRegistry:
    """Maps a streaming draft to its task so the Stop button can cancel it."""

    def __init__(self) -> None:
        self._tasks: dict[tuple[int, int], asyncio.Task[object]] = {}

    @contextmanager
    def track(self, chat_id: int, draft_id: int, task: asyncio.Task[object]) -> Iterator[None]:
        self._tasks[(chat_id, draft_id)] = task
        try:
            yield
        finally:
            self._tasks.pop((chat_id, draft_id), None)

    def cancel(self, chat_id: int, draft_id: int) -> bool:
        task = self._tasks.get((chat_id, draft_id))
        if task is None or task.done():
            return False
        task.cancel()
        return True

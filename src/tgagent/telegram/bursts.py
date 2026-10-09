import asyncio
from collections.abc import Callable, Sequence

from aiogram.types import Message


class BurstCollector:
    """Gathers what one person sends in quick succession into one turn.

    Telegram delivers an album, or a comment followed by the posts forwarded with it, as separate
    messages. The first message of a burst waits until nothing new has arrived for ``quiet``
    seconds (at most ``limit``); the rest join it and return ``None``. Relies on aiogram
    handling updates concurrently.
    """

    def __init__(self, quiet: float = 1.0, limit: float = 4.0) -> None:
        self._quiet = quiet
        self._limit = limit
        self._pending: dict[tuple[int, int | None, int], list[Message]] = {}

    async def collect(self, message: Message) -> list[Message] | None:
        sender = message.from_user.id if message.from_user else 0
        key = (message.chat.id, message.message_thread_id, sender)
        if key in self._pending:
            self._pending[key].append(message)
            return None
        batch = self._pending[key] = [message]
        try:
            waited = 0.0
            while waited < self._limit:
                size = len(batch)
                await asyncio.sleep(self._quiet)
                waited += self._quiet
                if len(batch) == size:
                    break
        finally:
            del self._pending[key]
        return sorted(batch, key=lambda m: m.message_id)


def pick_trigger(
    batch: Sequence[Message], addressed: Callable[[Message], bool]
) -> tuple[Message, list[Message]] | None:
    """The first message addressed to the bot and the rest of the burst, in order."""
    trigger = next((m for m in batch if addressed(m)), None)
    if trigger is None:
        return None
    return trigger, [m for m in batch if m is not trigger]

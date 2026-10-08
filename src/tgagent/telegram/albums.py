import asyncio

from aiogram.types import Message


class AlbumCollector:
    """Telegram delivers an album as separate messages; gather them into one turn.

    Relies on aiogram handling updates concurrently: the first message waits ``delay``
    while the rest of the album is appended, later ones return ``None``.
    """

    def __init__(self, delay: float = 1.0) -> None:
        self._delay = delay
        self._pending: dict[tuple[int, str], list[Message]] = {}

    async def collect(self, message: Message) -> list[Message] | None:
        if not message.media_group_id:
            return [message]
        key = (message.chat.id, message.media_group_id)
        if key in self._pending:
            self._pending[key].append(message)
            return None
        self._pending[key] = [message]
        await asyncio.sleep(self._delay)
        return sorted(self._pending.pop(key), key=lambda m: m.message_id)

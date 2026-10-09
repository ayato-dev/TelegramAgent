from dataclasses import dataclass

from tgagent.storage.db import SessionFactory, insert
from tgagent.storage.models import MediaCache


@dataclass(frozen=True, slots=True)
class MediaEntry:
    anthropic_file_id: str | None
    transcript: str | None
    openai_file_id: str | None = None


class MediaRepo:
    """Caches per-Telegram-file artefacts: provider file ids and voice transcripts."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def get(self, file_unique_id: str) -> MediaEntry | None:
        async with self._sessions() as session:
            row = await session.get(MediaCache, file_unique_id)
        return MediaEntry(row.anthropic_file_id, row.transcript, row.openai_file_id) if row else None

    async def save_file(self, file_unique_id: str, anthropic_file_id: str) -> None:
        await self._upsert(file_unique_id, anthropic_file_id=anthropic_file_id)

    async def save_openai_file(self, file_unique_id: str, openai_file_id: str) -> None:
        await self._upsert(file_unique_id, openai_file_id=openai_file_id)

    async def save_transcript(self, file_unique_id: str, transcript: str) -> None:
        await self._upsert(file_unique_id, transcript=transcript)

    async def _upsert(self, file_unique_id: str, **values: str) -> None:
        async with self._sessions.begin() as session:
            stmt = insert(session, MediaCache).values(file_unique_id=file_unique_id, **values)
            stmt = stmt.on_conflict_do_update(index_elements=[MediaCache.file_unique_id], set_=values)
            await session.execute(stmt)

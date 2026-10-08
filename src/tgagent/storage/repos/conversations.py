from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import literal, select, update
from sqlalchemy.dialects.postgresql import insert

from tgagent.storage.db import SessionFactory
from tgagent.storage.models import Conversation, Node, NodeMessage

Role = Literal["user", "assistant"]
ConversationKind = Literal["private", "group", "guest", "reminder"]
Content = list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class NodeRecord:
    id: int
    conversation_id: int
    parent_id: int | None
    role: Role
    content: Content
    has_compaction: bool


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    id: int
    chat_id: int
    thread_id: int | None
    kind: ConversationKind
    head_node_id: int | None
    title_pending: bool
    container_id: str | None
    container_expires_at: datetime | None
    model: str | None = None
    last_prompt_tokens: int = 0


def _conversation(row: Conversation) -> ConversationRecord:
    return ConversationRecord(
        id=row.id,
        chat_id=row.chat_id,
        thread_id=row.thread_id,
        kind=row.kind,  # type: ignore[arg-type]
        head_node_id=row.head_node_id,
        title_pending=row.title_pending,
        container_id=row.container_id,
        container_expires_at=row.container_expires_at,
        model=row.model,
        last_prompt_tokens=row.last_prompt_tokens,
    )


def contains_compaction(content: Content) -> bool:
    return any(block.get("type") == "compaction" for block in content)


class ConversationRepo:
    """Conversations are trees of nodes; a request context is the path from a node up to the root."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def create(
        self,
        chat_id: int,
        thread_id: int | None,
        kind: ConversationKind,
        *,
        title_pending: bool = False,
        model: str | None = None,
    ) -> ConversationRecord:
        row = Conversation(
            chat_id=chat_id, thread_id=thread_id, kind=kind, title_pending=title_pending, model=model
        )
        async with self._sessions.begin() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return _conversation(row)

    async def get(self, conversation_id: int) -> ConversationRecord | None:
        async with self._sessions() as session:
            row = await session.get(Conversation, conversation_id)
        return _conversation(row) if row else None

    async def active(self, chat_id: int, thread_id: int | None) -> ConversationRecord | None:
        thread_match = (
            Conversation.thread_id.is_(None) if thread_id is None else Conversation.thread_id == thread_id
        )
        stmt = (
            select(Conversation)
            .where(Conversation.chat_id == chat_id, thread_match, Conversation.is_active)
            .order_by(Conversation.id.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            row = await session.scalar(stmt)
        return _conversation(row) if row else None

    async def deactivate(self, chat_id: int, thread_id: int | None) -> None:
        thread_match = (
            Conversation.thread_id.is_(None) if thread_id is None else Conversation.thread_id == thread_id
        )
        async with self._sessions.begin() as session:
            await session.execute(
                update(Conversation)
                .where(Conversation.chat_id == chat_id, thread_match)
                .values(is_active=False)
            )

    async def set_head(self, conversation_id: int, node_id: int) -> None:
        await self._update(conversation_id, head_node_id=node_id)

    async def set_container(
        self, conversation_id: int, container_id: str, expires_at: datetime | None
    ) -> None:
        await self._update(conversation_id, container_id=container_id, container_expires_at=expires_at)

    async def set_model(self, conversation_id: int, model: str) -> None:
        await self._update(conversation_id, model=model)

    async def set_prompt_tokens(self, conversation_id: int, tokens: int) -> None:
        await self._update(conversation_id, last_prompt_tokens=tokens)

    async def clear_title_pending(self, conversation_id: int) -> None:
        await self._update(conversation_id, title_pending=False)

    async def _update(self, conversation_id: int, **values: object) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(Conversation).where(Conversation.id == conversation_id).values(**values)
            )

    async def add_node(
        self, conversation_id: int, parent_id: int | None, role: Role, content: Content
    ) -> int:
        return (await self.add_nodes(conversation_id, parent_id, [(role, content)]))[0]

    async def add_nodes(
        self, conversation_id: int, parent_id: int | None, items: Sequence[tuple[Role, Content]]
    ) -> list[int]:
        """Append a chain of nodes, each one the parent of the next."""
        ids: list[int] = []
        async with self._sessions.begin() as session:
            for role, content in items:
                node = Node(
                    conversation_id=conversation_id,
                    parent_id=parent_id,
                    role=role,
                    content=content,
                    has_compaction=contains_compaction(content),
                )
                session.add(node)
                await session.flush()
                ids.append(node.id)
                parent_id = node.id
        return ids

    async def path(self, node_id: int) -> list[NodeRecord]:
        """Nodes from the root (or the latest compaction point) down to ``node_id``."""
        nodes = Node.__table__
        columns = (
            nodes.c.id,
            nodes.c.conversation_id,
            nodes.c.parent_id,
            nodes.c.role,
            nodes.c.content,
            nodes.c.has_compaction,
        )
        chain = select(*columns, literal(0).label("depth")).where(nodes.c.id == node_id).cte(recursive=True)
        parents = nodes.alias()
        chain = chain.union_all(
            select(
                parents.c.id,
                parents.c.conversation_id,
                parents.c.parent_id,
                parents.c.role,
                parents.c.content,
                parents.c.has_compaction,
                chain.c.depth + 1,
            ).where(parents.c.id == chain.c.parent_id, chain.c.has_compaction.is_(False))
        )
        async with self._sessions() as session:
            rows = await session.execute(select(chain).order_by(chain.c.depth.desc()))
        return [
            NodeRecord(
                id=row.id,
                conversation_id=row.conversation_id,
                parent_id=row.parent_id,
                role=row.role,
                content=row.content,
                has_compaction=row.has_compaction,
            )
            for row in rows
        ]

    async def map_messages(self, chat_id: int, message_ids: Sequence[int], node_id: int) -> None:
        if not message_ids:
            return
        stmt = insert(NodeMessage).values(
            [{"chat_id": chat_id, "message_id": mid, "node_id": node_id} for mid in message_ids]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[NodeMessage.chat_id, NodeMessage.message_id], set_={"node_id": node_id}
        )
        async with self._sessions.begin() as session:
            await session.execute(stmt)

    async def node_for_message(self, chat_id: int, message_id: int) -> NodeRecord | None:
        stmt = (
            select(Node)
            .join(NodeMessage, NodeMessage.node_id == Node.id)
            .where(NodeMessage.chat_id == chat_id, NodeMessage.message_id == message_id)
        )
        async with self._sessions() as session:
            node = await session.scalar(stmt)
        if node is None:
            return None
        return NodeRecord(
            id=node.id,
            conversation_id=node.conversation_id,
            parent_id=node.parent_id,
            role=node.role,  # type: ignore[arg-type]
            content=node.content,
            has_compaction=node.has_compaction,
        )

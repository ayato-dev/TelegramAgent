from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tgagent.domain import MediaRef, NormalizedMessage
from tgagent.storage.db import SessionFactory
from tgagent.storage.repos import (
    ChatLogRepo,
    ChatRepo,
    ConversationRepo,
    LoggedMessage,
    MediaRepo,
    ReminderRepo,
    SecretaryLogRepo,
    UsageRecord,
    UsageRepo,
)

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def text_block(text: str) -> list[dict[str, object]]:
    return [{"type": "text", "text": text}]


def log_message(message_id: int, *, reply_to: int | None = None, minutes: int = 0) -> NormalizedMessage:
    return NormalizedMessage(
        chat_id=-100,
        message_id=message_id,
        thread_id=None,
        sender_id=42,
        sender_name="Иван",
        date=NOW + timedelta(minutes=minutes),
        text=f"msg {message_id}",
        reply_to_message_id=reply_to,
    )


async def test_path_follows_branch_from_root_to_leaf(sessions: SessionFactory) -> None:
    repo = ConversationRepo(sessions)
    conv = await repo.create(chat_id=1, thread_id=None, kind="private")
    root = await repo.add_node(conv.id, None, "user", text_block("q1"))
    answer = await repo.add_node(conv.id, root, "assistant", text_block("a1"))
    branch_a = await repo.add_node(conv.id, answer, "user", text_block("q2a"))
    branch_b = await repo.add_node(conv.id, answer, "user", text_block("q2b"))

    path_b = await repo.path(branch_b)

    assert [node.id for node in path_b] == [root, answer, branch_b]
    assert branch_a not in [node.id for node in path_b]


async def test_path_stops_at_node_with_compaction(sessions: SessionFactory) -> None:
    repo = ConversationRepo(sessions)
    conv = await repo.create(chat_id=1, thread_id=None, kind="private")
    old = await repo.add_node(conv.id, None, "user", text_block("old"))
    compacted = await repo.add_node(
        conv.id, old, "assistant", [{"type": "compaction", "content": "summary"}, *text_block("a")]
    )
    leaf = await repo.add_node(conv.id, compacted, "user", text_block("new"))

    path = await repo.path(leaf)

    assert [node.id for node in path] == [compacted, leaf]
    assert path[0].has_compaction


async def test_add_nodes_chains_parents(sessions: SessionFactory) -> None:
    repo = ConversationRepo(sessions)
    conv = await repo.create(chat_id=1, thread_id=None, kind="private")
    root = await repo.add_node(conv.id, None, "user", text_block("q"))

    ids = await repo.add_nodes(
        conv.id, root, [("assistant", text_block("tool")), ("user", text_block("result"))]
    )

    path = await repo.path(ids[-1])
    assert [node.id for node in path] == [root, *ids]


async def test_message_mapping_resolves_node(sessions: SessionFactory) -> None:
    repo = ConversationRepo(sessions)
    conv = await repo.create(chat_id=-100, thread_id=None, kind="group")
    node = await repo.add_node(conv.id, None, "assistant", text_block("hi"))

    await repo.map_messages(-100, [501, 502], node)

    found = await repo.node_for_message(-100, 502)
    assert found is not None
    assert found.id == node
    assert found.conversation_id == conv.id
    assert await repo.node_for_message(-100, 999) is None


async def test_active_conversation_lifecycle(sessions: SessionFactory) -> None:
    repo = ConversationRepo(sessions)
    first = await repo.create(chat_id=7, thread_id=3, kind="private")

    assert (await repo.active(7, 3)) == first
    assert await repo.active(7, None) is None

    await repo.deactivate(7, 3)
    assert await repo.active(7, 3) is None

    second = await repo.create(chat_id=7, thread_id=3, kind="private")
    await repo.set_head(second.id, 10)
    active = await repo.active(7, 3)
    assert active is not None
    assert active.id == second.id
    assert active.head_node_id == 10


async def test_conversation_model_pinning_and_prompt_size(sessions: SessionFactory) -> None:
    repo = ConversationRepo(sessions)
    unpinned = await repo.create(chat_id=1, thread_id=None, kind="private")
    pinned = await repo.create(chat_id=2, thread_id=None, kind="private", model="groq:openai/gpt-oss-120b")

    await repo.set_model(unpinned.id, "gemini:gemini-3.1-flash-lite")
    await repo.set_prompt_tokens(pinned.id, 4321)

    assert (unpinned.model, unpinned.last_prompt_tokens) == (None, 0)
    first, second = await repo.get(unpinned.id), await repo.get(pinned.id)
    assert first is not None and second is not None
    assert first.model == "gemini:gemini-3.1-flash-lite"
    assert (second.model, second.last_prompt_tokens) == ("groq:openai/gpt-oss-120b", 4321)


async def test_chat_log_chain_walks_reply_ancestors(sessions: SessionFactory) -> None:
    repo = ChatLogRepo(sessions)
    await repo.add(log_message(1))
    await repo.add(log_message(2, reply_to=1))
    await repo.add(log_message(3, reply_to=2))
    await repo.add(log_message(4, reply_to=3))

    chain = await repo.chain(-100, 4, depth=3)

    assert [m.message_id for m in chain] == [2, 3, 4]


async def test_chat_log_roundtrips_media(sessions: SessionFactory) -> None:
    repo = ChatLogRepo(sessions)
    media = MediaRef(kind="voice", file_id="F", file_unique_id="U", mime_type="audio/ogg", duration=12)
    message = NormalizedMessage(
        chat_id=-100,
        message_id=9,
        thread_id=None,
        sender_id=1,
        sender_name="A",
        date=NOW,
        text=None,
        media=media,
    )

    await repo.add(message)

    assert await repo.get(-100, 9) == message


async def test_chat_log_recent_is_chronological_and_limited(sessions: SessionFactory) -> None:
    repo = ChatLogRepo(sessions)
    for i in range(1, 6):
        await repo.add(log_message(i, minutes=i))

    recent = await repo.recent(-100, None, limit=3)

    assert [m.message_id for m in recent] == [3, 4, 5]


async def test_chat_log_purge_removes_old_messages(sessions: SessionFactory) -> None:
    repo = ChatLogRepo(sessions)
    await repo.add(log_message(1, minutes=-60 * 24 * 40))
    await repo.add(log_message(2))

    removed = await repo.purge_older_than(NOW - timedelta(days=30))

    assert removed == 1
    assert await repo.get(-100, 1) is None


async def test_chat_settings_patch_merges(sessions: SessionFactory) -> None:
    repo = ChatRepo(sessions)
    await repo.upsert(5, "private", None)

    await repo.update_settings(5, {"effort": "high"})
    merged = await repo.update_settings(5, {"web": False})

    assert merged == {"effort": "high", "web": False}
    assert await repo.get_settings(5) == merged


async def test_allowed_chats(sessions: SessionFactory) -> None:
    repo = ChatRepo(sessions)
    await repo.upsert(-1, "group", "A")
    await repo.upsert(-2, "group", "B")

    await repo.set_allowed(-1, True, added_by=42)
    await repo.set_allowed(-2, False, added_by=13)

    assert await repo.allowed_ids() == {-1}


async def test_media_cache_roundtrip(sessions: SessionFactory) -> None:
    repo = MediaRepo(sessions)

    await repo.save_file("U1", "file_abc")
    await repo.save_transcript("U1", "привет")

    entry = await repo.get("U1")
    assert entry is not None
    assert (entry.anthropic_file_id, entry.transcript) == ("file_abc", "привет")


async def test_media_cache_keeps_openai_file_ids(sessions: SessionFactory) -> None:
    repo = MediaRepo(sessions)

    await repo.save_file("U1", "file_abc")
    await repo.save_openai_file("U1", "file-oa1")

    entry = await repo.get("U1")
    assert entry is not None
    assert (entry.anthropic_file_id, entry.openai_file_id) == ("file_abc", "file-oa1")


async def test_claim_due_returns_only_due_pending_once(sessions: SessionFactory) -> None:
    repo = ReminderRepo(sessions)
    due = await repo.create(1, None, 1, NOW - timedelta(minutes=1), "due", "notify")
    await repo.create(1, None, 1, NOW + timedelta(hours=1), "future", "notify")
    cancelled = await repo.create(1, None, 1, NOW - timedelta(minutes=5), "cancelled", "notify")
    assert await repo.cancel(cancelled.id, chat_id=1)

    first = await repo.claim_due(NOW)
    second = await repo.claim_due(NOW)

    assert [r.id for r in first] == [due.id]
    assert second == []
    assert await repo.next_due() == NOW + timedelta(hours=1)


async def test_cancel_requires_same_chat(sessions: SessionFactory) -> None:
    repo = ReminderRepo(sessions)
    reminder = await repo.create(1, None, 1, NOW, "x", "notify")

    assert not await repo.cancel(reminder.id, chat_id=2)
    assert [r.id for r in await repo.pending(1)] == [reminder.id]


async def test_usage_totals_filter_by_chat(sessions: SessionFactory) -> None:
    repo = UsageRepo(sessions)
    base = UsageRecord(user_id=1, chat_id=10, kind="chat", model="m", input_tokens=100, output_tokens=10)
    await repo.add(base.with_cost(Decimal("0.5")))
    await repo.add(
        UsageRecord(user_id=2, chat_id=20, kind="stt", model="w", audio_seconds=12.0).with_cost(
            Decimal("0.25")
        )
    )

    everything = await repo.totals(NOW - timedelta(days=365))
    chat_10 = await repo.totals(NOW - timedelta(days=365), chat_id=10)

    assert everything.requests == 2
    assert everything.cost_usd == Decimal("0.75")
    assert chat_10.input_tokens == 100
    assert chat_10.cost_usd == Decimal("0.5")
    assert await repo.top_users(NOW - timedelta(days=365), limit=5) == [
        (1, Decimal("0.5")),
        (2, Decimal("0.25")),
    ]


async def test_times_come_back_aware_and_compare_correctly(sessions: SessionFactory) -> None:
    repo = ReminderRepo(sessions)
    moscow = datetime(2026, 10, 9, 15, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    await repo.create(1, None, 1, moscow, "x", "notify")

    due = await repo.next_due()
    early = await repo.claim_due(moscow - timedelta(seconds=1))
    claimed = await repo.claim_due(moscow)

    assert due == moscow and due is not None and due.tzinfo is not None
    assert early == []
    assert [r.due_at for r in claimed] == [moscow]


def secretary_message(
    message_id: int,
    sender: str = "person",
    *,
    minutes: int = 0,
    chat_id: int = 7,
    connection_id: str = "conn",
    reply_to: int | None = None,
) -> LoggedMessage:
    names = {"person": "Иван", "owner": "Виталий", "bot": "bot"}
    sender_ids = {"person": 7, "owner": 1, "bot": None}
    return LoggedMessage(
        connection_id=connection_id,
        chat_id=chat_id,
        message_id=message_id,
        sender=sender,  # type: ignore[arg-type]
        sender_id=sender_ids[sender],
        sender_name=names[sender],
        date=NOW + timedelta(minutes=minutes),
        text=f"msg {message_id}",
        reply_to_message_id=reply_to,
    )


async def test_secretary_log_keeps_each_connection_apart(sessions: SessionFactory) -> None:
    repo = SecretaryLogRepo(sessions)
    voice = MediaRef("voice", "f", "u", duration=3)
    await repo.add(secretary_message(1))
    await repo.add(secretary_message(2, "bot", minutes=1, reply_to=1))
    await repo.add(secretary_message(1, connection_id="other"))
    await repo.add(replace(secretary_message(3, minutes=2), media=voice))

    recent = await repo.recent("conn", 7, limit=2)

    assert [m.message_id for m in recent] == [2, 3]
    assert recent[0].reply_to_message_id == 1
    assert recent[1].media == voice


async def test_secretary_log_overwrites_a_message_seen_twice(sessions: SessionFactory) -> None:
    repo = SecretaryLogRepo(sessions)
    await repo.add(secretary_message(1))
    await repo.add(replace(secretary_message(1), text="edited"))

    assert [m.text for m in await repo.recent("conn", 7, limit=10)] == ["edited"]


async def test_secretary_replies_count_after_the_owner_writes(sessions: SessionFactory) -> None:
    repo = SecretaryLogRepo(sessions)
    for message in (
        secretary_message(1, "bot", minutes=0),
        secretary_message(2, "owner", minutes=1),
        secretary_message(3, "bot", minutes=2),
        secretary_message(4, "bot", minutes=3),
        secretary_message(5, "bot", minutes=4, chat_id=8),
    ):
        await repo.add(message)

    assert await repo.replies_since("conn", 7, NOW - timedelta(hours=1)) == 2
    assert await repo.replies_since("conn", 7, NOW + timedelta(minutes=3)) == 0
    assert await repo.replies_since("conn", 8, NOW - timedelta(hours=1)) == 1


async def test_secretary_log_knows_when_the_owner_last_wrote(sessions: SessionFactory) -> None:
    repo = SecretaryLogRepo(sessions)
    assert await repo.owner_last_seen(1) is None
    await repo.add(secretary_message(1, "owner", minutes=5))
    await repo.add(secretary_message(2, "owner", minutes=9, chat_id=8))
    await repo.add(secretary_message(3, "person", minutes=20))

    assert await repo.owner_last_seen(1) == NOW + timedelta(minutes=9)


async def test_secretary_log_purge_removes_old_messages(sessions: SessionFactory) -> None:
    repo = SecretaryLogRepo(sessions)
    await repo.add(secretary_message(1, minutes=-60 * 24 * 40))
    await repo.add(secretary_message(2))

    assert await repo.purge_older_than(NOW - timedelta(days=30)) == 1
    assert [m.message_id for m in await repo.recent("conn", 7, limit=10)] == [2]

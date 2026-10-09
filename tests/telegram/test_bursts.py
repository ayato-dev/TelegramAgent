import asyncio
from datetime import UTC, datetime

from aiogram.types import Chat, Message, User

from tgagent.telegram.bursts import BurstCollector, pick_trigger

ANNA = User(id=1, is_bot=False, first_name="Аня")
PETYA = User(id=2, is_bot=False, first_name="Петя")


def message(message_id: int, *, user: User = ANNA, text: str = "x", group: str | None = None) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        from_user=user,
        media_group_id=group,
        text=text,
    )


async def later(delay: float, collector: BurstCollector, item: Message) -> list[Message] | None:
    await asyncio.sleep(delay)
    return await collector.collect(item)


def batches(results: list[list[Message] | None]) -> list[list[int]]:
    return sorted([m.message_id for m in batch] for batch in results if batch is not None)


async def test_comment_and_forwarded_post_become_one_batch() -> None:
    collector = BurstCollector(quiet=0.1)

    results = await asyncio.gather(
        collector.collect(message(1, text="расскажи мне о нём")),
        later(0.05, collector, message(2, text="пересланный пост")),
    )

    assert batches(results) == [[1, 2]]


async def test_album_pieces_join_one_batch() -> None:
    collector = BurstCollector(quiet=0.1)

    results = await asyncio.gather(
        collector.collect(message(1, group="g")),
        later(0.02, collector, message(2, group="g")),
        later(0.04, collector, message(3, group="g")),
    )

    assert batches(results) == [[1, 2, 3]]


async def test_messages_after_a_pause_and_from_other_people_are_separate() -> None:
    collector = BurstCollector(quiet=0.05)

    results = await asyncio.gather(
        collector.collect(message(1)),
        later(0.02, collector, message(2, user=PETYA)),
        later(0.2, collector, message(3)),
    )

    assert batches(results) == [[1], [2], [3]]


async def test_a_steady_stream_is_cut_at_the_limit() -> None:
    collector = BurstCollector(quiet=0.05, limit=0.12)

    results = await asyncio.gather(*(later(0.03 * i, collector, message(i)) for i in range(1, 9)))

    assert len([batch for batch in results if batch]) >= 2
    assert sorted(m.message_id for batch in results if batch for m in batch) == list(range(1, 9))


async def test_cancelled_wait_does_not_swallow_later_messages() -> None:
    collector = BurstCollector(quiet=0.5)
    waiting = asyncio.create_task(collector.collect(message(1)))
    await asyncio.sleep(0.01)
    waiting.cancel()

    await asyncio.gather(waiting, return_exceptions=True)
    assert await asyncio.wait_for(collector.collect(message(3)), 1) is not None


def test_trigger_is_the_first_addressed_message() -> None:
    forwarded, mention, more = message(1, text="пост"), message(2, text="@bot что думаешь?"), message(3)

    picked = pick_trigger([forwarded, mention, more], lambda m: "@bot" in (m.text or ""))

    assert picked == (mention, [forwarded, more])
    assert pick_trigger([forwarded, more], lambda m: False) is None

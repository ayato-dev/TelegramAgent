import asyncio

from tgagent.services.generation import GenerationRegistry, KeyedLocks


async def test_keyed_locks_serialize_same_key_only() -> None:
    locks = KeyedLocks()
    order: list[str] = []

    async def work(key: str, name: str, delay: float) -> None:
        async with locks.hold(key):
            order.append(f"{name}+")
            await asyncio.sleep(delay)
            order.append(f"{name}-")

    await asyncio.gather(work("a", "1", 0.05), work("a", "2", 0.0), work("b", "3", 0.0))

    assert order.index("1-") < order.index("2+")
    assert order.index("3+") < order.index("1-")
    assert locks.size == 0


async def test_registry_cancels_registered_generation() -> None:
    registry = GenerationRegistry()
    task = asyncio.create_task(asyncio.sleep(10))

    with registry.track(5, 42, task):
        assert registry.cancel(5, 42)
        assert not registry.cancel(5, 43)
        await asyncio.sleep(0)
        assert task.cancelled()

    assert not registry.cancel(5, 42)

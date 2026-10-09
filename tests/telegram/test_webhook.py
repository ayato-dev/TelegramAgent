from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiohttp.test_utils import TestClient, TestServer

from tgagent.telegram.webhook import CRON_PATH, WEBHOOK_PATH, build_app, webhook_secret

SECRET = "s3cret"


def update(update_id: int, text: str = "hi") -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 0,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False, "first_name": "Аня"},
            "text": text,
        },
    }


class Harness:
    def __init__(self) -> None:
        self.seen: list[str] = []
        self.cron_runs = 0
        self.dispatcher = Dispatcher()

        @self.dispatcher.message()
        async def record(message: Message) -> None:
            self.seen.append(message.text or "")

    async def cron(self) -> dict[str, int]:
        self.cron_runs += 1
        return {"reminders": 2}


@pytest.fixture
async def harness() -> AsyncIterator[tuple[Harness, TestClient[Any, Any]]]:
    state = Harness()
    bot = Bot("123:abc")
    app = build_app(state.dispatcher, bot, secret=SECRET, inline=True, on_cron=state.cron)
    async with TestClient(TestServer(app)) as client:
        yield state, client
    await bot.session.close()


def headers(secret: str = SECRET) -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


async def test_health(harness: tuple[Harness, TestClient[Any, Any]]) -> None:
    _, client = harness

    response = await client.get("/health")

    assert response.status == 200


async def test_updates_need_the_secret(harness: tuple[Harness, TestClient[Any, Any]]) -> None:
    state, client = harness

    rejected = await client.post(WEBHOOK_PATH, json=update(1), headers=headers("wrong"))
    accepted = await client.post(WEBHOOK_PATH, json=update(2, "привет"), headers=headers())

    assert rejected.status == 401
    assert accepted.status == 200
    assert state.seen == ["привет"]


async def test_redelivered_update_is_handled_once(harness: tuple[Harness, TestClient[Any, Any]]) -> None:
    state, client = harness

    for _ in range(2):
        response = await client.post(WEBHOOK_PATH, json=update(7, "раз"), headers=headers())
        assert response.status == 200

    assert state.seen == ["раз"]


async def test_cron_runs_scheduled_work_with_the_secret(
    harness: tuple[Harness, TestClient[Any, Any]],
) -> None:
    state, client = harness

    denied = await client.post(CRON_PATH)
    by_header = await client.post(CRON_PATH, headers={"X-Cron-Secret": SECRET})
    by_query = await client.get(CRON_PATH, params={"secret": SECRET})

    assert denied.status == 401
    assert by_header.status == 200 and by_query.status == 200
    assert await by_header.json() == {"reminders": 2}
    assert state.cron_runs == 2


def test_default_secret_is_stable_and_telegram_safe() -> None:
    first, second = webhook_secret("123:abc", None), webhook_secret("123:abc", None)

    assert first == second
    assert first != webhook_secret("456:def", None)
    assert first.isalnum() and 16 <= len(first) <= 256
    assert webhook_secret("123:abc", "mine") == "mine"


async def test_run_webhook_registers_with_telegram_and_serves() -> None:
    import asyncio
    import socket
    from typing import cast

    import aiohttp

    from tgagent.telegram.webhook import run_webhook

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    registered: list[dict[str, Any]] = []

    class FakeTelegram:
        async def set_webhook(self, url: str, **params: Any) -> bool:
            registered.append({"url": url, **params})
            return True

    async def cron() -> dict[str, int]:
        return {}

    task = asyncio.create_task(
        run_webhook(
            Dispatcher(),
            cast(Bot, FakeTelegram()),
            public_url="https://bot.example.com/",
            secret=SECRET,
            port=port,
            inline=False,
            on_cron=cron,
        )
    )
    try:
        for _ in range(100):
            if registered:
                break
            await asyncio.sleep(0.02)
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{port}/health") as response,
        ):
            assert response.status == 200
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert registered[0]["url"] == "https://bot.example.com/webhook"
    assert registered[0]["secret_token"] == SECRET

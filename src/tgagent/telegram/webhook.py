"""Webhook mode for hosts that give the bot a public HTTPS address and may sleep between requests
(Cloud Run, Render, Railway, Fly and the like).

Besides Telegram's updates the server answers health checks and ``/cron``: an external scheduler
calls it to fire due reminders on platforms where nothing runs between requests.
"""

import asyncio
import contextlib
import hashlib
import hmac
import logging
import signal
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

log = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook"
CRON_PATH = "/cron"
HEALTH_PATH = "/health"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"

type CronJob = Callable[[], Awaitable[dict[str, int]]]


def webhook_secret(token: str, configured: str | None) -> str:
    """Telegram sends it back with every update; derived from the bot token unless set explicitly."""
    return configured or hashlib.sha256(f"tgagent-webhook:{token}".encode()).hexdigest()[:48]


def _same(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


class RecentUpdates:
    """Telegram redelivers an update it got no quick answer for; each one is handled once."""

    def __init__(self, size: int = 1000) -> None:
        self._size = size
        self._ids: OrderedDict[int, None] = OrderedDict()

    def seen(self, update_id: int) -> bool:
        if update_id in self._ids:
            return True
        self._ids[update_id] = None
        if len(self._ids) > self._size:
            self._ids.popitem(last=False)
        return False


def build_app(
    dispatcher: Dispatcher, bot: Bot, *, secret: str, inline: bool, on_cron: CronJob
) -> web.Application:
    """``inline`` handles each update inside its request: needed where the platform stops the CPU
    once the response is sent (request-billed Cloud Run); elsewhere updates are acknowledged at once."""
    recent = RecentUpdates()

    @web.middleware
    async def drop_redeliveries(
        request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
    ) -> web.StreamResponse:
        if request.path == WEBHOOK_PATH and _same(request.headers.get(SECRET_HEADER, ""), secret):
            with contextlib.suppress(ValueError):
                update_id = (await request.json()).get("update_id")
                if isinstance(update_id, int) and recent.seen(update_id):
                    return web.json_response({})
        return await handler(request)

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def cron(request: web.Request) -> web.Response:
        given = request.headers.get("X-Cron-Secret") or request.query.get("secret", "")
        if not _same(given, secret):
            return web.Response(status=401)
        return web.json_response(await on_cron())

    app = web.Application(middlewares=[drop_redeliveries])
    app.router.add_get(HEALTH_PATH, health)
    app.router.add_get(CRON_PATH, cron)
    app.router.add_post(CRON_PATH, cron)
    SimpleRequestHandler(dispatcher, bot, handle_in_background=not inline, secret_token=secret).register(
        app, path=WEBHOOK_PATH
    )
    return app


async def run_webhook(
    dispatcher: Dispatcher,
    bot: Bot,
    *,
    public_url: str,
    secret: str,
    port: int,
    inline: bool,
    on_cron: CronJob,
) -> None:
    """Serve until SIGTERM/SIGINT; Telegram is pointed at ``public_url`` + /webhook."""
    runner = web.AppRunner(build_app(dispatcher, bot, secret=secret, inline=inline, on_cron=on_cron))
    await runner.setup()
    try:
        await web.TCPSite(runner, "0.0.0.0", port).start()
        url = public_url.rstrip("/") + WEBHOOK_PATH
        await bot.set_webhook(
            url, secret_token=secret, allowed_updates=dispatcher.resolve_used_update_types()
        )
        log.info("webhook mode: listening on port %d, Telegram posts to %s", port, url)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        await stop.wait()
    finally:
        await runner.cleanup()

from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from tgagent.i18n import DEFAULT_LANG, Lang, t
from tgagent.storage.repos import UsageRepo, UsageTotals, UserRepo


def _money(value: Decimal) -> str:
    return f"${value:.4f}"


def _tokens(value: int) -> str:
    return f"{value / 1000:.1f}K" if value >= 1000 else str(value)


def _row(label: str, totals: UsageTotals, lang: Lang) -> str:
    voice = t(lang, "usage.minutes", minutes=f"{totals.audio_seconds / 60:.1f}")
    return (
        f"| {label} | {totals.requests} | {_tokens(totals.input_tokens)} / {_tokens(totals.output_tokens)} "
        f"| {_tokens(totals.cache_read_tokens)} | {totals.web_search_requests} "
        f"| {voice} | {_money(totals.cost_usd)} |"
    )


class UsageReport:
    def __init__(self, usage: UsageRepo, users: UserRepo, tz: ZoneInfo) -> None:
        self._usage = usage
        self._users = users
        self._tz = tz

    async def render(self, *, chat_id: int | None, now: datetime, lang: Lang = DEFAULT_LANG) -> str:
        local_midnight = datetime.combine(now.astimezone(self._tz).date(), time(), tzinfo=self._tz)
        periods = [
            (t(lang, "usage.today"), local_midnight),
            (t(lang, "usage.week"), now - timedelta(days=7)),
            (t(lang, "usage.month"), now - timedelta(days=30)),
        ]
        lines = [
            t(lang, "usage.title_chat" if chat_id is not None else "usage.title"),
            "",
            t(lang, "usage.header"),
            "|:--|--:|--:|--:|--:|--:|--:|",
        ]
        for label, since in periods:
            lines.append(_row(label, await self._usage.totals(since, chat_id=chat_id), lang))
        if chat_id is None:
            top = await self._usage.top_users(now - timedelta(days=30), limit=10)
            names = await self._users.names([uid for uid, _ in top if uid is not None])
            system = t(lang, "usage.system")
            if top:
                lines += ["", t(lang, "usage.top")]
                lines += [
                    f"- {names.get(uid, system) if uid is not None else system} — {_money(cost)}"
                    for uid, cost in top
                ]
        return "\n".join(lines)

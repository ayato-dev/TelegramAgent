from contextlib import AsyncExitStack
from typing import cast

import pytest

from tgagent.agent.providers.chat import ChatRunner
from tgagent.agent.providers.gemini import GeminiRunner
from tgagent.agent.tools import ToolRegistry
from tgagent.app import build_runners
from tgagent.config import Settings
from tgagent.context.media import MediaService
from tgagent.storage.repos import MediaRepo

KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "GROQ_API_KEY")


async def test_runners_exist_only_for_providers_with_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (*KEYS, "DEFAULT_MODEL", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:a")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/d")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    async with AsyncExitStack() as stack:
        runners = build_runners(
            settings, stack, None, cast(MediaService, None), cast(MediaRepo, None), ToolRegistry({})
        )

        assert {spec.provider for spec in runners.models} == {"gemini", "groq"}
        assert runners.default_model == "gemini:gemini-3.1-flash-lite"
        assert isinstance(runners.runner("groq:openai/gpt-oss-120b"), ChatRunner)
        assert isinstance(runners.runner("gemini:gemini-3.8-flash"), GeminiRunner)
        assert runners.runner("groq:openai/gpt-oss-120b").spec.free

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tgagent.agent.models import CATALOG, available_models, resolve_model
from tgagent.agent.pricing import TurnUsage, turn_cost
from tgagent.config import Settings

BASE = {"TELEGRAM_BOT_TOKEN": "1:a", "DATABASE_URL": "postgresql+asyncpg://u:p@h/d"}
KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "GROQ_API_KEY")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in (*KEYS, "DEFAULT_MODEL", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in BASE.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def make() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_some_llm_key_is_required(env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        make()


def test_default_model_follows_the_configured_provider(env: pytest.MonkeyPatch) -> None:
    env.setenv("GROQ_API_KEY", "g")

    assert make().default_model == "groq:openai/gpt-oss-120b"


def test_legacy_anthropic_model_is_respected(env: pytest.MonkeyPatch) -> None:
    env.setenv("ANTHROPIC_API_KEY", "a")
    env.setenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")

    assert make().default_model == "anthropic:claude-sonnet-5-5"


def test_empty_values_from_env_example_count_as_unset(env: pytest.MonkeyPatch) -> None:
    env.setenv("GROQ_API_KEY", "g")
    env.setenv("OPENAI_API_KEY", "")
    env.setenv("DEFAULT_MODEL", "")

    settings = make()

    assert settings.api_key("openai") is None
    assert settings.default_model == "groq:openai/gpt-oss-120b"


@pytest.mark.parametrize("value", ["openai:gpt-6-luna", "gpt-6-luna", "nope:model"])
def test_default_model_needs_a_known_provider_with_a_key(env: pytest.MonkeyPatch, value: str) -> None:
    env.setenv("ANTHROPIC_API_KEY", "a")
    env.setenv("DEFAULT_MODEL", value)

    with pytest.raises(ValidationError):
        make()


def test_available_models_only_for_configured_providers(env: pytest.MonkeyPatch) -> None:
    env.setenv("DEEPSEEK_API_KEY", "d")
    env.setenv("GEMINI_API_KEY", "g")

    providers = {spec.provider for spec in available_models(make())}

    assert providers == {"deepseek", "gemini"}


def test_catalog_capabilities(env: pytest.MonkeyPatch) -> None:
    env.setenv("ANTHROPIC_API_KEY", "a")
    settings = make()

    haiku = resolve_model("anthropic:claude-haiku-5-5", settings)
    gemini = resolve_model("gemini:gemini-3.1-flash-lite", settings)
    oss = resolve_model("groq:openai/gpt-oss-120b", settings)

    assert (haiku.compaction, haiku.vision, haiku.web) == ("server", True, True)
    assert (gemini.native_audio, gemini.youtube, gemini.web) == (True, True, False)
    assert (oss.vision, oss.web, oss.code) == (False, True, True)
    assert all(key.count(":") >= 1 for key in CATALOG)


def test_free_tiers_tighten_groq_and_unlock_nothing_for_gemini_search(env: pytest.MonkeyPatch) -> None:
    env.setenv("GROQ_API_KEY", "g")
    free = resolve_model("groq:openai/gpt-oss-120b", make())
    env.setenv("GROQ_PAID_TIER", "true")
    env.setenv("GEMINI_PAID_TIER", "true")
    env.setenv("GEMINI_API_KEY", "x")
    paid = resolve_model("groq:openai/gpt-oss-120b", make())
    gemini_paid = resolve_model("gemini:gemini-3.8-flash", make())

    assert free.free and free.context_trigger <= 6000 and free.max_output <= 4096
    assert not paid.free and paid.context_trigger > 6000
    assert gemini_paid.web


def test_unknown_model_gets_provider_defaults(env: pytest.MonkeyPatch) -> None:
    env.setenv("OPENAI_API_KEY", "o")

    spec = resolve_model("openai:gpt-7-preview", make())

    assert spec.model_id == "gpt-7-preview"
    assert spec.label == "gpt-7-preview"
    assert spec.provider == "openai"
    assert spec.pricing is None


def iteration(input_tokens: int, output_tokens: int, cache_read: int = 0) -> TurnUsage:
    usage = TurnUsage()
    usage.add_iteration(input_tokens, output_tokens, cache_read, 0)
    return usage


def test_turn_cost_free_tier_is_zero(env: pytest.MonkeyPatch) -> None:
    env.setenv("GROQ_API_KEY", "g")
    spec = resolve_model("groq:openai/gpt-oss-120b", make())

    assert turn_cost(spec, iteration(1_000_000, 1_000_000)) == Decimal(0)


def test_openai_long_context_doubles_input(env: pytest.MonkeyPatch) -> None:
    env.setenv("OPENAI_API_KEY", "o")
    spec = resolve_model("openai:gpt-6-luna", make())

    assert turn_cost(spec, iteration(100_000, 0)) == Decimal("0.01")
    assert turn_cost(spec, iteration(300_000, 0)) == Decimal("0.06")


def test_deepseek_off_peak_is_half_price(env: pytest.MonkeyPatch) -> None:
    env.setenv("DEEPSEEK_API_KEY", "d")
    spec = resolve_model("deepseek:deepseek-flash", make())
    usage = iteration(1_000_000, 1_000_000)
    peak = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # Wednesday 08:00 UTC
    night = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)

    assert turn_cost(spec, usage, now=peak) == Decimal("1.50")
    assert turn_cost(spec, usage, now=night) == Decimal("0.75")


def test_unknown_model_costs_nothing(env: pytest.MonkeyPatch) -> None:
    env.setenv("OPENAI_API_KEY", "o")

    assert turn_cost(resolve_model("openai:gpt-7-preview", make()), iteration(10, 10)) == Decimal(0)

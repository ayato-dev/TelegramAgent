import pytest
from pydantic import ValidationError

from tgagent.config import Settings

REQUIRED = {
    "TELEGRAM_BOT_TOKEN": "123:abc",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "GROQ_API_KEY": "gsk-test",
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def make() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_allowed_user_ids_parsed_from_comma_separated_list(env: pytest.MonkeyPatch) -> None:
    env.setenv("ALLOWED_USER_IDS", " 111, 222 ,333,")

    assert make().allowed_user_ids == frozenset({111, 222, 333})


def test_allowed_user_ids_empty_means_nobody(env: pytest.MonkeyPatch) -> None:
    env.setenv("ALLOWED_USER_IDS", "")

    assert make().allowed_user_ids == frozenset()


def test_non_numeric_user_id_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("ALLOWED_USER_IDS", "111,@durov")

    with pytest.raises(ValidationError):
        make()


def test_compaction_trigger_below_api_minimum_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("COMPACTION_TRIGGER_TOKENS", "40000")

    with pytest.raises(ValidationError):
        make()


def test_defaults_match_design(env: pytest.MonkeyPatch) -> None:
    settings = make()

    assert settings.default_model == "anthropic:claude-haiku-5-5"
    assert settings.compaction_trigger_tokens == 100_000
    assert settings.default_effort == "medium"
    assert settings.whisper_model == "whisper-large-v3"


def test_unknown_timezone_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("TIMEZONE", "Mars/Olympus")

    with pytest.raises(ValidationError):
        make()


def test_timezone_resolved_to_zoneinfo(env: pytest.MonkeyPatch) -> None:
    env.setenv("TIMEZONE", "Europe/Moscow")

    assert make().tz.key == "Europe/Moscow"


def test_webhook_mode_is_off_by_default_and_needs_https(env: pytest.MonkeyPatch) -> None:
    assert make().webhook_url is None
    assert make().port == 8080

    env.setenv("WEBHOOK_URL", "http://bot.example.com")
    with pytest.raises(ValidationError):
        make()

    env.setenv("WEBHOOK_URL", "https://bot.example.com")
    env.setenv("PORT", "9000")
    settings = make()
    assert (settings.webhook_url, settings.port) == ("https://bot.example.com", 9000)


def test_database_defaults_to_a_sqlite_file(env: pytest.MonkeyPatch) -> None:
    env.delenv("DATABASE_URL")

    assert make().database_url.startswith("sqlite+aiosqlite:///")

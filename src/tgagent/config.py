from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from tgagent.agent.models import FALLBACK_MODELS, PROVIDERS, Provider, parse_key

Effort = Literal["low", "medium", "high"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    telegram_bot_token: SecretStr
    database_url: str

    # Any subset of providers; at least one key is required.
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None

    allowed_user_ids: Annotated[frozenset[int], NoDecode] = frozenset()
    # Reply to people outside the whitelist; empty keeps the bot silent.
    access_denied_text: str = "Доступ к боту закрыт."

    # Deprecated: ANTHROPIC_MODEL=x is read as DEFAULT_MODEL=anthropic:x.
    anthropic_model: str | None = None
    # provider:model-id; defaults to the cheapest model of the first configured provider.
    default_model: str = Field("", validate_default=True)
    compaction_trigger_tokens: int = Field(100_000, ge=50_000)
    max_output_tokens: int = Field(16_000, ge=1_024)
    default_effort: Effort = "medium"
    web_search_max_uses: int = Field(5, ge=1, le=20)

    whisper_model: str = "whisper-large-v3"
    # Free plans cost nothing. Groq free also limits requests to 8K tokens, so the bot compacts
    # gpt-oss chats early; set true on a paid plan to lift that and count Groq in /usage.
    groq_paid_tier: bool = False
    # Google Search grounding needs a paid Gemini plan; free-tier usage is not counted in /usage.
    gemini_paid_tier: bool = False

    timezone: str = "Europe/Moscow"
    chat_log_retention_days: int = Field(30, ge=1)
    heartbeat_path: Path = Path("/tmp/tgagent-heartbeat")
    log_level: str = "INFO"

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(part) for part in value.split(",") if part.strip())
        return value

    @field_validator(*(f"{provider}_api_key" for provider in PROVIDERS), mode="before")
    @classmethod
    def _empty_key_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("default_model", mode="before")
    @classmethod
    def _fallback_model(cls, value: object, info: ValidationInfo) -> object:
        if value:
            return value
        if info.data.get("anthropic_model"):
            return f"anthropic:{info.data['anthropic_model']}"
        for provider in PROVIDERS:
            if info.data.get(f"{provider}_api_key") is not None:
                return f"{provider}:{FALLBACK_MODELS[provider]}"
        return ""

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def _default_model_has_key(self) -> Self:
        if all(self.api_key(provider) is None for provider in PROVIDERS):
            names = ", ".join(f"{provider.upper()}_API_KEY" for provider in PROVIDERS)
            raise ValueError(f"set at least one LLM API key: {names}")
        provider, _ = parse_key(self.default_model)
        if self.api_key(provider) is None:
            raise ValueError(f"DEFAULT_MODEL={self.default_model} needs {provider.upper()}_API_KEY")
        return self

    def api_key(self, provider: Provider) -> SecretStr | None:
        key: SecretStr | None = getattr(self, f"{provider}_api_key")
        return key

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

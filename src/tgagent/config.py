from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Effort = Literal["low", "medium", "high"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    telegram_bot_token: SecretStr
    anthropic_api_key: SecretStr
    groq_api_key: SecretStr
    database_url: str

    allowed_user_ids: Annotated[frozenset[int], NoDecode] = frozenset()

    anthropic_model: str = "claude-haiku-5-5"
    compaction_trigger_tokens: int = Field(100_000, ge=50_000)
    max_output_tokens: int = Field(16_000, ge=1_024)
    default_effort: Effort = "medium"
    web_search_max_uses: int = Field(5, ge=1, le=20)

    whisper_model: str = "whisper-large-v3"

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

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

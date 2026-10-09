"""Secretary mode settings from prompts/secretary.toml; anything the file leaves out keeps its default."""

import tomllib
from datetime import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


def _parse_hours(value: str) -> tuple[time, time]:
    start, _, end = value.partition("-")
    try:
        return time.fromisoformat(start.strip()), time.fromisoformat(end.strip())
    except ValueError as exc:
        raise ValueError('hours must look like "09:00-23:00"') from exc


class SecretaryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    online_minutes: int = Field(10, ge=0)
    reply_when_online: bool = False
    delay_seconds: float = Field(5, ge=0)
    max_replies: int = Field(3, ge=0)
    limit_hours: int = Field(24, ge=1)
    hours: str = ""
    voice: bool = True
    history: int = Field(30, ge=1, le=200)
    model: str = ""

    @field_validator("hours")
    @classmethod
    def _valid_hours(cls, value: str) -> str:
        if value.strip():
            _parse_hours(value)
        return value.strip()

    def answers_at(self, moment: time) -> bool:
        """Whether ``moment`` (local time) falls within ``hours``; a window may cross midnight."""
        if not self.hours:
            return True
        start, end = _parse_hours(self.hours)
        if start <= end:
            return start <= moment < end
        return moment >= start or moment < end


def load_secretary_config(path: Path) -> SecretaryConfig:
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError:
        return SecretaryConfig()
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    try:
        return SecretaryConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"{path}: {exc}") from exc

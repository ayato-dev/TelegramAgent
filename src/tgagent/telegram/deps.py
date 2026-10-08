from dataclasses import dataclass
from typing import Any

from aiogram.types import User

from tgagent.agent.models import ModelSpec
from tgagent.agent.tools import AgentOptions
from tgagent.config import Settings
from tgagent.services.generation import GenerationRegistry, KeyedLocks
from tgagent.services.settings import options_from
from tgagent.services.turns import TurnService
from tgagent.services.usage_report import UsageReport
from tgagent.storage.repos import ChatLogRepo, ChatRepo, ConversationRepo, UserRepo
from tgagent.telegram.access import AccessPolicy
from tgagent.telegram.albums import AlbumCollector


@dataclass(slots=True)
class Deps:
    """Everything handlers need; injected into aiogram handlers as ``deps``."""

    settings: Settings
    me: User
    policy: AccessPolicy
    turns: TurnService
    chats: ChatRepo
    users: UserRepo
    chat_log: ChatLogRepo
    conversations: ConversationRepo
    usage_report: UsageReport
    locks: KeyedLocks
    generations: GenerationRegistry
    albums: AlbumCollector
    # Models the bot can run, the default one included.
    models: tuple[ModelSpec, ...]

    @property
    def username(self) -> str:
        return self.me.username or ""

    @property
    def model_keys(self) -> list[str]:
        return [spec.key for spec in self.models]

    def options(self, saved: dict[str, Any]) -> AgentOptions:
        return options_from(saved, default_effort=self.settings.default_effort, models=self.model_keys)

    def model(self, options: AgentOptions) -> ModelSpec:
        key = options.model or self.settings.default_model
        return next(spec for spec in self.models if spec.key == key)

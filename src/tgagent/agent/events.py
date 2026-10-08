from dataclasses import dataclass
from datetime import datetime

from tgagent.agent.pricing import TurnUsage
from tgagent.storage.repos import Content, Role


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolStarted:
    name: str
    summary: str


@dataclass(frozen=True, slots=True)
class FileProduced:
    filename: str
    mime_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ContainerInfo:
    id: str
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class TurnResult:
    nodes: list[tuple[Role, Content]]
    text: str
    thinking: str
    usage: TurnUsage
    container: ContainerInfo | None
    stop_reason: str
    refused: bool


type AgentEvent = TextDelta | ThinkingDelta | ToolStarted | FileProduced | TurnResult

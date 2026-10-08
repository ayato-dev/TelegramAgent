import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from tgagent.config import Effort

log = logging.getLogger(__name__)

WEB_SEARCH = "web_search_20260209"
WEB_FETCH = "web_fetch_20260209"
CODE_EXECUTION = "code_execution_20260120"

ChatKind = Literal["private", "group", "guest", "reminder"]
Style = Literal["normal", "troll"]


@dataclass(frozen=True, slots=True)
class AgentOptions:
    effort: Effort = "medium"
    show_thinking: bool = False
    web: bool = True
    code: bool = True
    style: Style = "normal"


@dataclass(frozen=True, slots=True)
class ToolContext:
    chat_id: int
    thread_id: int | None
    user_id: int
    chat_kind: ChatKind
    message_id: int | None


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    content: str
    is_error: bool = False


type ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolOutcome]]


def server_tool_specs(
    options: AgentOptions, *, web_supported: bool, web_max_uses: int
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if options.web and web_supported:
        # Direct calls only: with programmatic calling enabled Haiku sometimes sends
        # {"params": {...}} on a direct call, which the API rejects as invalid_tool_input.
        direct = {"max_uses": web_max_uses, "allowed_callers": ["direct"]}
        specs.append({"type": WEB_SEARCH, "name": "web_search", **direct})
        specs.append({"type": WEB_FETCH, "name": "web_fetch", **direct})
    if options.code:
        specs.append({"type": CODE_EXECUTION, "name": "code_execution"})
    return specs


def _tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


CLIENT_TOOL_SPECS: list[dict[str, Any]] = [
    _tool(
        "set_reminder",
        "Ставит напоминание или отложенную задачу в текущем чате. Вызывай, когда просят напомнить "
        "о чём-то или сделать что-то позже («напомни завтра в 9», «через час проверь курс и напиши»). "
        "mode=notify — в назначенное время прислать text как напоминание; mode=task — в назначенное "
        "время ты сам выполнишь text как задание (с поиском и расчётами) и пришлёшь результат.",
        {
            "when": {
                "type": "string",
                "description": "Момент срабатывания, ISO 8601 со смещением: 2026-10-09T09:00:00+03:00",
            },
            "text": {"type": "string", "description": "Текст напоминания или формулировка задания"},
            "mode": {"type": "string", "enum": ["notify", "task"]},
        },
    ),
    _tool(
        "list_reminders",
        "Показывает активные напоминания и отложенные задачи текущего чата. Вызывай, когда спрашивают, "
        "что запланировано, или перед отменой напоминания, чтобы узнать его id.",
        {},
    ),
    _tool(
        "cancel_reminder",
        "Отменяет напоминание текущего чата по id (id бери из list_reminders).",
        {"reminder_id": {"type": "integer"}},
    ),
    _tool(
        "read_chat_history",
        "Только для групп: возвращает последние сообщения чата (до 200) с авторами и временем. "
        "Вызывай, когда вопрос касается обсуждения в чате: «что тут обсуждали?», «кто что предлагал?», "
        "«о чём договорились?».",
        {"limit": {"type": "integer", "description": "Сколько последних сообщений прочитать, 1–200"}},
    ),
    _tool(
        "create_poll",
        "Создаёт опрос Telegram в текущем чате. Вызывай, когда просят сделать опрос или голосование.",
        {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "От 2 до 12 вариантов"},
            "allows_multiple_answers": {"type": "boolean"},
        },
    ),
    _tool(
        "reply_to_checklist_task",
        "Отвечает на конкретную задачу чек-листа Telegram (ответ привязывается к пункту списка). "
        "Вызывай по одному разу на каждую выполненную задачу, когда выполняешь задачи из <checklist>.",
        {
            "checklist_message_id": {"type": "integer"},
            "task_id": {"type": "integer"},
            "text": {"type": "string", "description": "Короткий отчёт по задаче в Markdown"},
        },
    ),
]


class ToolRegistry:
    def __init__(self, handlers: Mapping[str, ToolHandler]) -> None:
        self._handlers = dict(handlers)

    @property
    def specs(self) -> list[dict[str, Any]]:
        return [spec for spec in CLIENT_TOOL_SPECS if spec["name"] in self._handlers]

    async def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolOutcome(f"Ошибка: инструмент {name} недоступен", is_error=True)
        try:
            return await handler(args, ctx)
        except Exception as exc:
            log.warning("tool %s failed", name, exc_info=True)
            return ToolOutcome(f"Ошибка: {exc}", is_error=True)

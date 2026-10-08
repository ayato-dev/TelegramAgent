from typing import Any

from tgagent.agent.tools import (
    CLIENT_TOOL_SPECS,
    AgentOptions,
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    server_tool_specs,
)

CTX = ToolContext(chat_id=1, thread_id=None, user_id=1, chat_kind="private", message_id=10)


def names(specs: list[dict[str, Any]]) -> list[str]:
    return [spec["name"] for spec in specs]


def test_all_server_tools_when_enabled() -> None:
    specs = server_tool_specs(AgentOptions(web=True, code=True), web_supported=True, web_max_uses=5)

    assert names(specs) == ["web_search", "web_fetch", "code_execution"]
    assert specs[0]["max_uses"] == 5


def test_web_tools_dropped_when_disabled_or_unsupported() -> None:
    disabled = server_tool_specs(AgentOptions(web=False, code=True), web_supported=True, web_max_uses=5)
    unsupported = server_tool_specs(AgentOptions(web=True, code=False), web_supported=False, web_max_uses=5)

    assert names(disabled) == ["code_execution"]
    assert unsupported == []


def test_client_tools_are_strict_with_closed_schemas() -> None:
    for spec in CLIENT_TOOL_SPECS:
        assert spec["strict"] is True
        schema = spec["input_schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


async def test_registry_runs_handler() -> None:
    async def handler(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        return ToolOutcome(f"{args['x']} in {ctx.chat_id}")

    registry = ToolRegistry({"echo": handler})

    assert await registry.execute("echo", {"x": "hi"}, CTX) == ToolOutcome("hi in 1")


async def test_registry_reports_unknown_tool_as_error() -> None:
    outcome = await ToolRegistry({}).execute("nope", {}, CTX)

    assert outcome.is_error


async def test_registry_turns_exceptions_into_error_results() -> None:
    async def broken(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        raise ValueError("bad date")

    outcome = await ToolRegistry({"broken": broken}).execute("broken", {}, CTX)

    assert outcome == ToolOutcome("Ошибка: bad date", is_error=True)

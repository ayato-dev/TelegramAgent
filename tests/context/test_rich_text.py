from datetime import UTC, datetime
from typing import Any

from aiogram.types import Chat, Message, RichMessage, User

from tgagent.context.normalize import normalize
from tgagent.context.rich_text import rich_to_text


def rich(*blocks: dict[str, Any]) -> RichMessage:
    return RichMessage.model_validate({"blocks": list(blocks)})


def test_paragraph_with_nested_formatting() -> None:
    message = rich({"type": "paragraph", "text": ["Курс евро — ", {"type": "bold", "text": "95 ₽"}, "."]})

    assert rich_to_text(message) == "Курс евро — 95 ₽."


def test_structure_is_kept_as_markdown() -> None:
    message = rich(
        {"type": "heading", "size": 2, "text": "Итог"},
        {
            "type": "list",
            "items": [
                {"label": "•", "blocks": [{"type": "paragraph", "text": "первый"}]},
                {
                    "label": "•",
                    "has_checkbox": True,
                    "is_checked": True,
                    "blocks": [{"type": "paragraph", "text": "сделано"}],
                },
            ],
        },
        {
            "type": "table",
            "cells": [
                [
                    {"align": "left", "valign": "top", "text": "Валюта", "is_header": True},
                    {"align": "left", "valign": "top", "text": "Курс"},
                ],
                [
                    {"align": "left", "valign": "top", "text": "EUR"},
                    {"align": "left", "valign": "top", "text": "95"},
                ],
            ],
        },
        {"type": "pre", "language": "python", "text": "print(1)"},
        {"type": "blockquote", "blocks": [{"type": "paragraph", "text": "цитата"}]},
        {"type": "mathematical_expression", "expression": "E = mc^2"},
    )

    assert rich_to_text(message) == (
        "## Итог\n\n"
        "- первый\n- [x] сделано\n\n"
        "| Валюта | Курс |\n| EUR | 95 |\n\n"
        "```python\nprint(1)\n```\n\n"
        "> цитата\n\n"
        "$$E = mc^2$$"
    )


def test_details_and_links() -> None:
    message = rich(
        {
            "type": "details",
            "summary": "Размышления",
            "blocks": [
                {"type": "paragraph", "text": [{"type": "url", "text": "сайт", "url": "https://a.b"}]}
            ],
        }
    )

    assert rich_to_text(message) == "Размышления\nсайт (https://a.b)"


def test_reply_to_rich_bot_message_is_not_empty() -> None:
    bot = User(id=9, is_bot=True, first_name="Agent", username="agent_bot")
    answer = Message(
        message_id=15239,
        date=datetime.now(UTC),
        chat=Chat(id=-100, type="supergroup"),
        from_user=bot,
        rich_message=rich({"type": "paragraph", "text": "Земля круглая."}),
    )

    assert normalize(answer).text == "Земля круглая."

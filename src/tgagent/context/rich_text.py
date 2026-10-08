"""Plain Markdown-ish text from a received Rich Message, so replies to the bot's own answers have context."""

from typing import Any

from aiogram.types import RichMessage

_MEDIA_LABELS = {
    "photo": "[фото]",
    "video": "[видео]",
    "animation": "[GIF]",
    "audio": "[аудио]",
    "voice_note": "[голосовое]",
    "document": "[файл]",
    "map": "[карта]",
    "collage": "[коллаж]",
    "slideshow": "[слайдшоу]",
}


def inline_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(inline_text(part) for part in value)
    kind = getattr(value, "type", None)
    if kind == "mathematical_expression":
        return f"${value.expression}$"
    if kind == "custom_emoji":
        return str(value.alternative_text)
    if kind == "button":
        return f"[{value.button.text}]"
    text = inline_text(getattr(value, "text", None))
    if kind == "url" and value.url != text:
        return f"{text} ({value.url})"
    return text


def _blocks(blocks: list[Any]) -> str:
    return "\n\n".join(rendered for block in blocks if (rendered := _block(block)))


def _caption(block: Any) -> str:
    caption = getattr(block, "caption", None)
    return inline_text(caption.text) if caption is not None and hasattr(caption, "text") else ""


def _block(block: Any) -> str:
    kind = getattr(block, "type", None)
    match kind:
        case "heading":
            return f"{'#' * min(max(block.size, 1), 6)} {inline_text(block.text)}"
        case "pre":
            return f"```{block.language or ''}\n{inline_text(block.text)}\n```"
        case "blockquote":
            return "\n".join(f"> {line}" if line else ">" for line in _blocks(block.blocks).split("\n"))
        case "expandable_blockquote" | "pullquote":
            return f"> {inline_text(block.text)}"
        case "details":
            return f"{inline_text(block.summary)}\n{_blocks(block.blocks)}"
        case "list":
            lines = []
            for item in block.items:
                mark = f"[{'x' if item.is_checked else ' '}] " if item.has_checkbox else ""
                lines.append(f"- {mark}{_blocks(item.blocks)}")
            return "\n".join(lines)
        case "table":
            return "\n".join(
                "| " + " | ".join(inline_text(cell.text) for cell in row) + " |" for row in block.cells
            )
        case "mathematical_expression":
            return f"$${block.expression}$$"
        case "divider":
            return "---"
        case "anchor" | "buttons":
            return ""
        case _ if kind in _MEDIA_LABELS:
            caption = _caption(block)
            return f"{_MEDIA_LABELS[kind]} {caption}".strip()
    if hasattr(block, "text"):
        return inline_text(block.text)
    if hasattr(block, "blocks"):
        return _blocks(block.blocks)
    return ""


def rich_to_text(message: RichMessage) -> str:
    return _blocks(message.blocks)

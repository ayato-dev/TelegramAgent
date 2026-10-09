from tests.telegram.fakes import FakeBot
from tgagent.telegram.render import compose_draft, compose_final, edit_markdown, send_markdown, split_markdown


def test_short_markdown_is_one_chunk() -> None:
    assert split_markdown("hello", limit=100) == ["hello"]


def test_split_on_paragraph_boundaries() -> None:
    paragraphs = ["a" * 40, "b" * 40, "c" * 40]

    chunks = split_markdown("\n\n".join(paragraphs), limit=90)

    assert chunks == ["a" * 40 + "\n\n" + "b" * 40, "c" * 40]


def test_code_fence_with_blank_lines_is_not_split_between_paragraphs() -> None:
    code = "```python\nx = 1\n\ny = 2\n```"
    md = "intro\n\n" + code + "\n\noutro"

    chunks = split_markdown(md, limit=len(code) + 2)

    assert code in chunks


def test_oversized_code_block_is_split_with_fences_reopened() -> None:
    body = "\n".join(f"line {i}" for i in range(40))
    md = f"```python\n{body}\n```"

    chunks = split_markdown(md, limit=120)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 120
        assert chunk.startswith("```python\n")
        assert chunk.endswith("```")


def test_draft_placeholder_status_and_thinking() -> None:
    assert compose_draft("", None, None, "ru") == "<tg-thinking>Думаю…</tg-thinking>"
    assert compose_draft("", None, None, "en") == "<tg-thinking>Thinking…</tg-thinking>"
    assert compose_draft("", None, "🔎 Ищу: курс") == "<tg-thinking>🔎 Ищу: курс</tg-thinking>"
    assert compose_draft("", "a < b", None) == "<tg-thinking>a &lt; b</tg-thinking>"
    assert compose_draft("Ответ", None, "🐍 Считаю") == "Ответ\n\n<tg-thinking>🐍 Считаю</tg-thinking>"


def test_final_includes_collapsed_thinking_when_present() -> None:
    assert compose_final("Ответ", "") == "Ответ"
    assert compose_final("Ответ", "x<y", "ru") == (
        "<details><summary>💭 Размышления</summary>\n\nx&lt;y\n\n</details>\n\nОтвет"
    )


async def test_send_markdown_uses_rich_messages() -> None:
    bot = FakeBot()

    sent = await send_markdown(bot.as_bot(), 5, "**hi**", thread_id=7, reply_to=3)

    assert len(sent) == 1
    params = bot.last("send_rich_message")
    assert params["markdown"] == "**hi**"
    assert params["message_thread_id"] == 7
    assert params["reply_parameters"].message_id == 3


async def test_rejected_rich_message_falls_back_to_entities() -> None:
    bot = FakeBot(fail={"send_rich_message"})

    sent = await send_markdown(bot.as_bot(), 5, "**жирный** текст")

    assert len(sent) == 1
    params = bot.last("send_message")
    assert params["text"] == "жирный текст"
    assert params["parse_mode"] is None
    assert params["entities"][0].type == "bold"


async def test_long_answer_split_into_several_messages_only_first_replies() -> None:
    bot = FakeBot()
    md = "\n\n".join(["x" * 20_000, "y" * 20_000])

    sent = await send_markdown(bot.as_bot(), 5, md, reply_to=9)

    assert len(sent) == 2
    calls = [p for name, p in bot.calls if name == "send_rich_message"]
    assert calls[0]["reply_parameters"].message_id == 9
    assert calls[1]["reply_parameters"] is None


async def test_edit_falls_back_to_plain_entities() -> None:
    bot = FakeBot(fail={"edit_rich"})

    assert await edit_markdown(bot.as_bot(), 5, 77, "*курсив*")

    params = bot.last("edit_text")
    assert params["text"] == "курсив"
    assert params["message_id"] == 77

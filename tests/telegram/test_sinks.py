import asyncio

from aiogram.types import InputRichMessageContent, InputTextMessageContent

from tests.telegram.fakes import FakeBot
from tgagent.agent.events import FileProduced, TextDelta, ToolStarted
from tgagent.telegram.sinks import DraftSink, EditSink, GuestSink, PlainSink


async def test_draft_sink_streams_coalesced_drafts_then_persists() -> None:
    bot = FakeBot()
    sink = DraftSink(bot.as_bot(), chat_id=5, thread_id=8, interval=0.02)

    await sink.start()
    for word in ["Пр", "ив", "ет", ", ", "мир"]:
        await sink.on_event(TextDelta(word))
    await asyncio.sleep(0.08)
    ids = await sink.finish("Привет, мир", [])

    drafts = [p for name, p in bot.calls if name == "send_rich_message_draft"]
    assert 1 <= len(drafts) < 6
    assert all(d["draft_id"] == sink.draft_id and d["can_stop"] for d in drafts)
    assert drafts[-1]["markdown"] == "Привет, мир"
    assert drafts[-1]["message_thread_id"] == 8
    assert bot.last("send_rich_message")["markdown"] == "Привет, мир"
    assert len(ids) == 1


async def test_draft_shows_tool_status() -> None:
    bot = FakeBot()
    sink = DraftSink(bot.as_bot(), chat_id=5, thread_id=None, interval=0.01)

    await sink.start()
    await sink.on_event(ToolStarted("web_search", "курс евро"))
    await asyncio.sleep(0.05)
    await sink.finish("ok", [])

    markdowns = [p["markdown"] for name, p in bot.calls if name == "send_rich_message_draft"]
    assert "<tg-thinking>🔎 Ищу: курс евро</tg-thinking>" in markdowns


async def test_draft_falls_back_to_plain_drafts() -> None:
    bot = FakeBot(fail={"send_rich_message_draft"})
    sink = DraftSink(bot.as_bot(), chat_id=5, thread_id=None, interval=0.01)

    await sink.start()
    await sink.on_event(TextDelta("текст"))
    await asyncio.sleep(0.05)
    await sink.finish("текст", [])

    plain = [p for name, p in bot.calls if name == "send_message_draft"]
    assert plain
    assert plain[-1]["text"] == "текст"
    assert plain[-1]["can_stop"]


async def test_draft_sink_failure_message() -> None:
    bot = FakeBot()
    sink = DraftSink(bot.as_bot(), chat_id=5, thread_id=None)

    await sink.start()
    await sink.fail("Ошибка")

    assert bot.last("send_message")["text"] == "Ошибка"


async def test_edit_sink_replies_with_placeholder_and_edits_final() -> None:
    bot = FakeBot()
    sink = EditSink(bot.as_bot(), chat_id=-100, thread_id=None, reply_to=55, interval=0.01)

    await sink.start()
    placeholder = bot.last("send_message")
    await sink.on_event(TextDelta("част"))
    await asyncio.sleep(0.05)
    ids = await sink.finish("Полный ответ", [])

    assert placeholder["reply_parameters"].message_id == 55
    assert bot.last("edit_rich")["markdown"] == "Полный ответ"
    assert ids == [bot.last("edit_rich")["message_id"]]


async def test_edit_sink_overflow_goes_to_follow_up_messages() -> None:
    bot = FakeBot()
    sink = EditSink(bot.as_bot(), chat_id=-100, thread_id=None, reply_to=55)
    final = "\n\n".join(["a" * 20_000, "b" * 20_000])

    await sink.start()
    ids = await sink.finish(final, [])

    assert len(ids) == 2
    assert bot.last("send_rich_message")["reply_parameters"].message_id == ids[0]


async def test_files_sent_as_photo_or_document() -> None:
    bot = FakeBot()
    sink = PlainSink(bot.as_bot(), chat_id=5, thread_id=None)
    files = [FileProduced("chart.png", "image/png", b"PNG"), FileProduced("data.csv", "text/csv", b"a,b")]

    await sink.finish("Готово", files)

    assert bot.last("send_photo")["filename"] == "chart.png"
    assert bot.last("send_document")["filename"] == "data.csv"


async def test_guest_sink_answers_once_with_rich_message() -> None:
    bot = FakeBot()
    sink = GuestSink(bot.as_bot(), guest_query_id="gq")

    await sink.start()
    await sink.on_event(TextDelta("x"))
    await sink.finish("**Ответ**", [])

    assert bot.names() == ["answer_guest_query"]
    content = bot.last("answer_guest_query")["result"].input_message_content
    assert isinstance(content, InputRichMessageContent)
    assert content.rich_message.markdown == "**Ответ**"


async def test_guest_sink_falls_back_to_text_content() -> None:
    bot = FakeBot(fail={"answer_guest_query"})
    sink = GuestSink(bot.as_bot(), guest_query_id="gq")
    calls: list[object] = []
    original = bot.answer_guest_query

    async def answer(guest_query_id: str, result: object) -> None:
        calls.append(result)
        if len(calls) == 1:
            await original(guest_query_id, result)

    bot.answer_guest_query = answer  # type: ignore[method-assign]

    await sink.finish("**Ответ**", [])

    content = calls[-1].input_message_content  # type: ignore[attr-defined]
    assert isinstance(content, InputTextMessageContent)
    assert content.message_text == "Ответ"

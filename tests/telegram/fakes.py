from datetime import UTC, datetime
from typing import Any, cast

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendRichMessage
from aiogram.types import Chat, InputRichMessage, Message


def bad_request(text: str = "Bad Request: can't parse rich message") -> TelegramBadRequest:
    method = SendRichMessage(chat_id=1, rich_message=InputRichMessage(markdown="x"))
    return TelegramBadRequest(method=method, message=text)


class FakeBot:
    """Records Bot API calls; methods listed in ``fail`` raise TelegramBadRequest."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail or set()
        self._next_id = 100

    def as_bot(self) -> Bot:
        return cast(Bot, self)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def last(self, name: str) -> dict[str, Any]:
        return [params for called, params in self.calls if called == name][-1]

    def _record(self, name: str, params: dict[str, Any]) -> None:
        self.calls.append((name, params))
        if name in self.fail:
            raise bad_request()

    def _message(self, chat_id: int) -> Message:
        self._next_id += 1
        return Message(
            message_id=self._next_id, date=datetime.now(UTC), chat=Chat(id=chat_id, type="private")
        )

    async def send_rich_message(self, chat_id: int, rich_message: InputRichMessage, **kw: Any) -> Message:
        self._record("send_rich_message", {"chat_id": chat_id, "markdown": rich_message.markdown, **kw})
        return self._message(chat_id)

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> Message:
        self._record("send_message", {"chat_id": chat_id, "text": text, **kw})
        return self._message(chat_id)

    async def send_rich_message_draft(
        self, chat_id: int, draft_id: int, rich_message: InputRichMessage, **kw: Any
    ) -> bool:
        self._record(
            "send_rich_message_draft", {"draft_id": draft_id, "markdown": rich_message.markdown, **kw}
        )
        return True

    async def send_message_draft(self, chat_id: int, draft_id: int, **kw: Any) -> bool:
        self._record("send_message_draft", {"draft_id": draft_id, **kw})
        return True

    async def edit_message_text(self, **kw: Any) -> bool:
        rich = kw.pop("rich_message", None)
        name = "edit_rich" if rich is not None else "edit_text"
        self._record(name, {"markdown": rich.markdown if rich else None, **kw})
        return True

    async def send_photo(self, chat_id: int, photo: Any, **kw: Any) -> Message:
        self._record("send_photo", {"chat_id": chat_id, "filename": photo.filename, **kw})
        return self._message(chat_id)

    async def send_document(self, chat_id: int, document: Any, **kw: Any) -> Message:
        self._record("send_document", {"chat_id": chat_id, "filename": document.filename, **kw})
        return self._message(chat_id)

    async def answer_guest_query(self, guest_query_id: str, result: Any) -> None:
        self._record("answer_guest_query", {"guest_query_id": guest_query_id, "result": result})

    async def send_poll(self, chat_id: int, question: str, options: list[Any], **kw: Any) -> Message:
        self._record("send_poll", {"chat_id": chat_id, "question": question, "options": options, **kw})
        return self._message(chat_id)

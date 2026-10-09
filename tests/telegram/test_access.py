from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.types import (
    BusinessConnection,
    CallbackQuery,
    Chat,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    MessageGenerationStopped,
    Update,
    User,
)

from tgagent.telegram.access import AccessMiddleware, AccessPolicy

OWNER = User(id=1, is_bot=False, first_name="Owner")
STRANGER = User(id=2, is_bot=False, first_name="Stranger")
NOW = datetime.now(UTC)


def msg(chat: Chat, user: User) -> Message:
    return Message(message_id=1, date=NOW, chat=chat, from_user=user, text="hi")


def connection(user: User) -> BusinessConnection:
    return BusinessConnection(id="c", user=user, user_chat_id=user.id, date=NOW, is_enabled=True)


PRIVATE_OWNER = Chat(id=1, type="private")
PRIVATE_STRANGER = Chat(id=2, type="private")
ALLOWED_GROUP = Chat(id=-10, type="supergroup")
OTHER_GROUP = Chat(id=-20, type="group")


class Recorder:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, event: Any, data: dict[str, Any]) -> str:
        self.called = True
        return "handled"


async def run(update: Update) -> tuple[bool, AccessPolicy]:
    policy = AccessPolicy(frozenset({1}), {-10})
    left: list[int] = []

    async def leave(chat_id: int) -> None:
        left.append(chat_id)

    middleware = AccessMiddleware(policy, leave)
    handler = Recorder()
    await middleware(handler, update, {})
    return handler.called, policy


@pytest.mark.parametrize(
    ("update", "allowed"),
    [
        (Update(update_id=1, message=msg(PRIVATE_OWNER, OWNER)), True),
        (Update(update_id=1, message=msg(PRIVATE_STRANGER, STRANGER)), False),
        (Update(update_id=1, message=msg(ALLOWED_GROUP, STRANGER)), True),
        (Update(update_id=1, message=msg(OTHER_GROUP, OWNER)), False),
        (Update(update_id=1, guest_message=msg(OTHER_GROUP, OWNER)), True),
        (Update(update_id=1, guest_message=msg(OTHER_GROUP, STRANGER)), False),
        (
            Update(
                update_id=1,
                callback_query=CallbackQuery(id="q", from_user=STRANGER, chat_instance="c", data="set:web"),
            ),
            False,
        ),
        (
            Update(
                update_id=1,
                stopped_message_generation=MessageGenerationStopped(chat=PRIVATE_OWNER, draft_id=3),
            ),
            True,
        ),
        (Update(update_id=1, business_connection=connection(OWNER)), True),
        (Update(update_id=1, business_connection=connection(STRANGER)), False),
        # Anyone writes to the owner's business chats; the secretary checks whose connection it is.
        (Update(update_id=1, business_message=msg(PRIVATE_STRANGER, STRANGER)), True),
        (
            Update(
                update_id=1,
                my_chat_member=ChatMemberUpdated(
                    chat=OTHER_GROUP,
                    from_user=STRANGER,
                    date=NOW,
                    old_chat_member=ChatMemberLeft(user=OWNER),
                    new_chat_member=ChatMemberMember(user=OWNER),
                ),
            ),
            True,
        ),
    ],
)
async def test_access_decisions(update: Update, allowed: bool) -> None:
    called, _ = await run(update)

    assert called is allowed


async def test_messages_from_unknown_group_trigger_leave_once() -> None:
    policy = AccessPolicy(frozenset({1}), set())
    left: list[int] = []

    async def leave(chat_id: int) -> None:
        left.append(chat_id)

    middleware = AccessMiddleware(policy, leave)
    for _ in range(2):
        await middleware(Recorder(), Update(update_id=1, message=msg(OTHER_GROUP, OWNER)), {})

    assert left == [-20]


def test_policy_tracks_chat_changes() -> None:
    policy = AccessPolicy(frozenset({1}), set())

    policy.allow_chat(-5)
    assert policy.chat_allowed(-5)
    policy.revoke_chat(-5)
    assert not policy.chat_allowed(-5)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def no_leave(chat_id: int) -> None:
    return None


def denying(clock: Clock, text: str = "Доступ закрыт") -> AccessMiddleware:
    return AccessMiddleware(
        AccessPolicy(frozenset({1}), set()), no_leave, denied_text=text, cooldown=10, clock=clock
    )


async def test_stranger_in_private_gets_denial_with_cooldown() -> None:
    from tests.telegram.fakes import FakeBot

    bot, clock = FakeBot(), Clock()
    middleware = denying(clock)
    update = Update(update_id=1, message=msg(PRIVATE_STRANGER, STRANGER))

    await middleware(Recorder(), update, {"bot": bot.as_bot()})
    clock.now += 5
    await middleware(Recorder(), update, {"bot": bot.as_bot()})
    clock.now += 6
    await middleware(Recorder(), update, {"bot": bot.as_bot()})

    replies = [p for name, p in bot.calls if name == "send_message"]
    assert [(p["chat_id"], p["text"]) for p in replies] == [(2, "Доступ закрыт"), (2, "Доступ закрыт")]


async def test_stranger_guest_call_and_button_get_denial() -> None:
    from tests.telegram.fakes import FakeBot

    bot = FakeBot()
    middleware = denying(Clock())
    guest = msg(OTHER_GROUP, STRANGER).model_copy(update={"guest_query_id": "gq"})
    query = CallbackQuery(id="q", from_user=STRANGER, chat_instance="c", data="set:web")

    await middleware(Recorder(), Update(update_id=1, guest_message=guest), {"bot": bot.as_bot()})
    await middleware(Recorder(), Update(update_id=2, callback_query=query), {"bot": bot.as_bot()})

    answer = bot.last("answer_guest_query")
    assert answer["guest_query_id"] == "gq"
    assert answer["result"].input_message_content.message_text == "Доступ закрыт"
    assert bot.last("answer_callback_query") == {"callback_query_id": "q", "text": "Доступ закрыт"}


async def test_empty_denial_text_keeps_silence() -> None:
    from tests.telegram.fakes import FakeBot

    bot = FakeBot()
    middleware = denying(Clock(), text="")

    await middleware(
        Recorder(), Update(update_id=1, message=msg(PRIVATE_STRANGER, STRANGER)), {"bot": bot.as_bot()}
    )

    assert bot.calls == []


async def test_bot_service_message_in_allowed_private_chat_is_not_denied() -> None:
    from aiogram.types import ForumTopicEdited

    from tests.telegram.fakes import FakeBot

    bot_user = User(id=999, is_bot=True, first_name="Agent")
    renamed = Message(
        message_id=7,
        date=NOW,
        chat=PRIVATE_OWNER,
        from_user=bot_user,
        message_thread_id=3,
        forum_topic_edited=ForumTopicEdited(name="Рецепт борща"),
    )
    bot, handler = FakeBot(), Recorder()

    await denying(Clock())(handler, Update(update_id=1, message=renamed), {"bot": bot.as_bot()})

    assert handler.called
    assert bot.calls == []


async def test_bots_never_get_denial_replies() -> None:
    from tests.telegram.fakes import FakeBot

    other_bot = User(id=777, is_bot=True, first_name="Other")
    bot = FakeBot()

    await denying(Clock())(
        Recorder(),
        Update(update_id=1, message=msg(Chat(id=777, type="private"), other_bot)),
        {"bot": bot.as_bot()},
    )

    assert bot.calls == []


async def test_default_denial_speaks_the_strangers_language() -> None:
    from tests.telegram.fakes import FakeBot

    bot = FakeBot()
    middleware = AccessMiddleware(AccessPolicy(frozenset({1}), set()), no_leave, denied_text=None)
    russian = User(id=3, is_bot=False, first_name="Ваня", language_code="ru")
    english = User(id=4, is_bot=False, first_name="John", language_code="en")

    for user in (russian, english):
        update = Update(update_id=user.id, message=msg(Chat(id=user.id, type="private"), user))
        await middleware(Recorder(), update, {"bot": bot.as_bot()})

    texts = [p["text"] for name, p in bot.calls if name == "send_message"]
    assert texts == ["Доступ к боту закрыт.", "This bot is private."]

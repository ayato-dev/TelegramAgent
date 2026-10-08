from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.types import (
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

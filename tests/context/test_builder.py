from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from tgagent.context.builder import ContentBuilder, TurnInput
from tgagent.context.media import MediaPart, MediaService
from tgagent.domain import MediaRef, NormalizedMessage

UTC_TZ = ZoneInfo("UTC")
IMAGE = {"type": "image", "source": {"type": "file", "file_id": "img"}}


@dataclass
class FakeMedia:
    calls: list[str] = field(default_factory=list)

    async def describe(
        self, media: MediaRef, *, code_enabled: bool, user_id: int | None, chat_id: int
    ) -> MediaPart:
        self.calls.append(media.kind)
        if media.kind == "photo":
            return MediaPart(None, [IMAGE])
        return MediaPart("расшифровка", [])


def msg(message_id: int, text: str | None, media: MediaRef | None = None) -> NormalizedMessage:
    return NormalizedMessage(
        chat_id=-1,
        message_id=message_id,
        thread_id=None,
        sender_id=5,
        sender_name="Петя",
        date=datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
        text=text,
        media=media,
    )


def builder() -> tuple[ContentBuilder, FakeMedia]:
    media = FakeMedia()
    return ContentBuilder(cast(MediaService, media), UTC_TZ), media


async def test_private_turn_is_single_text_block_without_author() -> None:
    build, _ = builder()

    content = await build.build(TurnInput(msg(1, "привет")), include_author=False, code_enabled=True)

    assert content == [
        {"type": "text", "text": '<message id="1" time="2026-10-08T12:00+00:00">\nпривет\n</message>'}
    ]


async def test_environment_and_context_wrap_the_trigger() -> None:
    build, _ = builder()
    turn = TurnInput(
        msg(3, "@bot это правда?"),
        context=(msg(1, "земля плоская"),),
        environment='<environment chat="group"/>',
    )

    content = await build.build(turn, include_author=True, code_enabled=True)

    assert len(content) == 1
    text = content[0]["text"]
    assert text.startswith('<environment chat="group"/>\n<context>\n<message id="1" author="Петя"')
    assert 'земля плоская\n</message>\n</context>\n<message id="3"' in text
    assert text.endswith("@bot это правда?\n</message>")


async def test_media_blocks_follow_their_message() -> None:
    build, media = builder()
    photo = MediaRef("photo", "f", "u")
    voice = MediaRef("voice", "v", "vu", duration=3)
    turn = TurnInput(msg(2, "что тут?"), context=(msg(1, None, photo),))
    turn_voice = TurnInput(msg(4, None, voice))

    content = await build.build(turn, include_author=True, code_enabled=True)
    voice_content = await build.build(turn_voice, include_author=False, code_enabled=True)

    kinds: list[Any] = [block["type"] for block in content]
    assert kinds == ["text", "image", "text"]
    assert content[0]["text"].endswith("[фото]\n</message>")
    assert "[голосовое 0:03]\nрасшифровка" in voice_content[0]["text"]
    assert media.calls == ["photo", "voice"]

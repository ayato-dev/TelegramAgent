from aiogram import F, Router
from aiogram.types import BusinessConnection, Message

from tgagent.telegram.deps import Deps
from tgagent.telegram.handlers.common import SUPPORTED_CONTENT

router = Router(name="business")


@router.business_connection()
async def on_business_connection(connection: BusinessConnection, deps: Deps) -> None:
    await deps.secretary.on_connection(connection)


@router.business_message(F.content_type.in_(SUPPORTED_CONTENT))
async def on_business_message(message: Message, deps: Deps) -> None:
    await deps.secretary.on_message(message)

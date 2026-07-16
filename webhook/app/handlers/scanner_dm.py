"""Owner commands accepted directly by the dedicated signal bot."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.handlers.dm import (
  handle_start as deliver_welcome,
  handle_trade_map as deliver_trade_map,
)

router = Router()


@router.message(Command("start"), F.chat.type == "private")
async def handle_start(msg: Message) -> None:
  await deliver_welcome(msg)


@router.message(Command("trade_map"), F.chat.type == "private")
async def handle_trade_map(msg: Message) -> None:
  await deliver_trade_map(msg)

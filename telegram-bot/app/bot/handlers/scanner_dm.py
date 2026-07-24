"""Owner commands accepted directly by the dedicated signal bot."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.handlers.dm import (
  handle_auto_close_all as deliver_auto_close_all,
  handle_auto_pause as deliver_auto_pause,
  handle_auto_resume as deliver_auto_resume,
  handle_auto_status as deliver_auto_status,
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


@router.message(Command("auto_status"), F.chat.type == "private")
async def handle_auto_status(msg: Message) -> None:
  await deliver_auto_status(msg)


@router.message(Command("auto_pause"), F.chat.type == "private")
async def handle_auto_pause(msg: Message) -> None:
  await deliver_auto_pause(msg)


@router.message(Command("auto_resume"), F.chat.type == "private")
async def handle_auto_resume(msg: Message) -> None:
  await deliver_auto_resume(msg)


@router.message(Command("auto_close_all"), F.chat.type == "private")
async def handle_auto_close_all(msg: Message) -> None:
  await deliver_auto_close_all(msg)

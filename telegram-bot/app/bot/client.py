"""Low-level Telegram client wiring shared by delivery modules."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import (
  BotCommand,
  BotCommandScopeChat,
  BotCommandScopeDefault,
  InlineKeyboardMarkup,
  Message,
)

from app.core.config import settings

log = logging.getLogger(__name__)

bot = Bot(
  token=settings.telegram_bot_token,
  default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
scanner_bot = Bot(
  token=settings.scanner_telegram_bot_token or settings.telegram_bot_token,
  default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
scanner_dp = Dispatcher()

OWNER_COMMANDS = [
  BotCommand(command="trade_open", description="[SYMBOL] — list open signals"),
  BotCommand(command="trade_active", description="[SYMBOL] [#id]"),
  BotCommand(command="trade_close", description="[SYMBOL] #id ±pips [%] | be"),
  BotCommand(command="trade_uncclose", description="[SYMBOL] #id"),
  BotCommand(command="trade_tp", description="[SYMBOL] #id TP +pips"),
  BotCommand(command="trade_sl", description="[SYMBOL] #id be|price"),
  BotCommand(command="trade_cancel", description="[SYMBOL] #id"),
  BotCommand(command="trade_delete", description="[SYMBOL] #id — remove a typo"),
  BotCommand(command="trade_reopen", description="[SYMBOL] #id [lo-hi]"),
  BotCommand(command="trade_tag", description="[SYMBOL] #id|id:DB_ID setup"),
  BotCommand(command="trade_untagged", description="[N] — setup backfill list"),
  BotCommand(command="trade_note", description="[SYMBOL] #id text"),
  BotCommand(command="trade_review", description="[SYMBOL] #id"),
  BotCommand(command="trade_map", description="[SYMBOL] — current market map"),
  BotCommand(command="auto_status", description="ApexVoid Algo status"),
  BotCommand(command="auto_pause", description="Pause ApexVoid Algo entries"),
  BotCommand(command="auto_resume", description="Resume ApexVoid Algo entries"),
  BotCommand(command="auto_close_all", description="Flatten all algo positions"),
  BotCommand(command="trade_stats", description="[SYMBOL] [today|week|month]"),
  BotCommand(command="trade_pips", description="[SYMBOL] [period]"),
  BotCommand(command="help", description="Trade command reference"),
]
SCANNER_PUBLIC_COMMANDS = [
  BotCommand(command="start", description="Welcome and public resources"),
]
SCANNER_OWNER_COMMANDS = [
  *SCANNER_PUBLIC_COMMANDS,
  BotCommand(command="trade_map", description="[SYMBOL] — current market map"),
  BotCommand(command="auto_status", description="ApexVoid Algo status"),
  BotCommand(command="auto_pause", description="Pause ApexVoid Algo entries"),
  BotCommand(command="auto_resume", description="Resume ApexVoid Algo entries"),
  BotCommand(command="auto_close_all", description="Flatten all algo positions"),
]

_MAX_SEND_ATTEMPTS = 3


async def setup_commands(target_bot: Bot) -> None:
  await target_bot.set_my_commands(
    [],
    scope=BotCommandScopeDefault(),
  )
  if settings.telegram_owner_id:
    await target_bot.set_my_commands(
      OWNER_COMMANDS,
      scope=BotCommandScopeChat(chat_id=settings.telegram_owner_id),
    )


async def setup_scanner_commands(target_bot: Bot) -> None:
  await target_bot.set_my_commands(
    SCANNER_PUBLIC_COMMANDS,
    scope=BotCommandScopeDefault(),
  )
  if settings.telegram_owner_id:
    await target_bot.set_my_commands(
      SCANNER_OWNER_COMMANDS,
      scope=BotCommandScopeChat(chat_id=settings.telegram_owner_id),
    )


async def send_with_retry(
  text: str,
  reply_to: int | None = None,
  chat_id: int | str | None = None,
  reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
  """Send a Telegram message with exponential-backoff retry on network errors."""
  return await _send_message_with_retry(
    bot,
    text,
    reply_to,
    chat_id,
    reply_markup,
  )


async def send_scanner_with_retry(
  text: str,
  reply_to: int | None = None,
  chat_id: int | str | None = None,
  reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
  """Send scanner/feed-analysis notifications with the scanner bot token."""
  return await _send_message_with_retry(
    scanner_bot,
    text,
    reply_to,
    chat_id,
    reply_markup,
  )


async def _send_message_with_retry(
  target_bot: Bot,
  text: str,
  reply_to: int | None,
  chat_id: int | str | None,
  reply_markup: InlineKeyboardMarkup | None,
) -> Message:
  for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
    try:
      return await target_bot.send_message(
        chat_id=chat_id or settings.telegram_channel_id,
        text=text,
        reply_to_message_id=reply_to,
        reply_markup=reply_markup,
      )
    except TelegramRetryAfter as e:
      log.warning(
        "Telegram rate-limited; waiting %ds (attempt %d/%d)",
        e.retry_after,
        attempt,
        _MAX_SEND_ATTEMPTS,
      )
      await asyncio.sleep(e.retry_after)
    except TelegramNetworkError as e:
      if attempt == _MAX_SEND_ATTEMPTS:
        raise
      wait = 2 ** attempt
      log.warning(
        "Telegram send failed (attempt %d/%d): %s — retrying in %ds",
        attempt,
        _MAX_SEND_ATTEMPTS,
        e,
        wait,
      )
      await asyncio.sleep(wait)
  raise RuntimeError(f"Telegram send failed after {_MAX_SEND_ATTEMPTS} attempts")


_send_with_retry = send_with_retry


async def send_sticker(
  sticker: str,
  channel_id: int,
  reply_to: int | None = None,
):
  return await bot.send_sticker(
    chat_id=channel_id,
    sticker=sticker,
    reply_to_message_id=reply_to,
  )


async def delete_message(chat_id: int | str, message_id: int) -> None:
  await bot.delete_message(int(chat_id), int(message_id))

"""Low-level Telegram client wiring shared by delivery modules."""

import asyncio
import logging
import time

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

from app.bot.telegram_actor import (
  PRIORITY_CARD,
  PRIORITY_LIFECYCLE,
  PRIORITY_PRICE,
  submit as submit_telegram,
)
from app.core.config import runtime_config

log = logging.getLogger(__name__)

bot = Bot(
  token=runtime_config.bootstrap.telegram.bot_token,
  default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
scanner_bot = Bot(
  token=(
    runtime_config.delivery.telegram.scanner_telegram_bot_token
    or runtime_config.bootstrap.telegram.bot_token
  ),
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
  BotCommand(
    command="trade_modify",
    description="[SYMBOL] #id entry/sl/tp — pending only",
  ),
  BotCommand(command="trade_reopen", description="[SYMBOL] #id [lo-hi]"),
  BotCommand(command="trade_tag", description="[SYMBOL] #id|id:DB_ID setup"),
  BotCommand(command="trade_untagged", description="[N] — setup backfill list"),
  BotCommand(command="trade_note", description="[SYMBOL] #id text"),
  BotCommand(command="trade_review", description="[SYMBOL] #id"),
  BotCommand(command="trade_map", description="[SYMBOL] — current market map"),
  BotCommand(command="algo_status", description="Algo bot status"),
  BotCommand(command="algo_pause", description="Pause Algo bot entries"),
  BotCommand(command="algo_resume", description="Resume Algo bot entries"),
  BotCommand(command="algo_close_all", description="Flatten all Algo bot positions"),
  BotCommand(command="trade_stats", description="[SYMBOL] today|week|month · auto vs manual"),
  BotCommand(command="trade_pips", description="[SYMBOL] [period]"),
  BotCommand(command="help", description="Trade command reference"),
]
SCANNER_PUBLIC_COMMANDS = [
  BotCommand(command="start", description="Welcome and public resources"),
]
SCANNER_OWNER_COMMANDS = [
  *SCANNER_PUBLIC_COMMANDS,
  BotCommand(command="trade_map", description="[SYMBOL] — current market map"),
  BotCommand(command="algo_status", description="Algo bot status"),
  BotCommand(command="algo_pause", description="Pause Algo bot entries"),
  BotCommand(command="algo_resume", description="Resume Algo bot entries"),
  BotCommand(command="algo_close_all", description="Flatten all Algo bot positions"),
]

_MAX_SEND_ATTEMPTS = 3
# Live incident 2026-08-07: Telegram issued a genuine flood-control ban
# (~39856s, ~11 hours) after repeated startup reconciliation passes burst
# Telegram with unthrottled edits. This unconditionally slept the full
# retry_after - freezing whatever task called send_with_retry for 11 real
# hours, not just failing the one send. A short per-second throttle is
# exactly what this retry loop is for; a multi-hour flood ban is not
# something worth blocking a task on. Above this cap, log and raise
# instead of sleeping, so the caller's own error handling (skip, log,
# move on) takes over rather than the whole task going dark.
_MAX_RETRY_AFTER_SLEEP_SECONDS = 30
# Root/PLAN PUBLISHED cards must exist before fill replies thread under them.
# Live 2026-08-12: scanner flood after startup reconciliation was 227–261s —
# a 120s cap still orphaned ORDER FILLED. Wait out typical floods; still
# refuse multi-hour bans.
_MAX_ROOT_CARD_RETRY_AFTER_SLEEP_SECONDS = 360
_SCANNER_FLOOD_UNTIL_KEY = "auto_trade:telegram_scanner_flood_until"


async def setup_commands(target_bot: Bot) -> None:
  await target_bot.set_my_commands(
    [],
    scope=BotCommandScopeDefault(),
  )
  if runtime_config.delivery.telegram.telegram_owner_id:
    await target_bot.set_my_commands(
      OWNER_COMMANDS,
      scope=BotCommandScopeChat(
        chat_id=runtime_config.delivery.telegram.telegram_owner_id
      ),
    )


async def setup_scanner_commands(target_bot: Bot) -> None:
  await target_bot.set_my_commands(
    SCANNER_PUBLIC_COMMANDS,
    scope=BotCommandScopeDefault(),
  )
  if runtime_config.delivery.telegram.telegram_owner_id:
    await target_bot.set_my_commands(
      SCANNER_OWNER_COMMANDS,
      scope=BotCommandScopeChat(
        chat_id=runtime_config.delivery.telegram.telegram_owner_id
      ),
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


async def note_scanner_flood(retry_after: float | int) -> None:
  """Record scanner-bot flood so other tasks can wait instead of hammering."""
  try:
    from app.persistence import redis_state

    client = redis_state.get_client()
    wait = max(1, int(retry_after))
    until = int(time.time()) + wait
    await client.set(_SCANNER_FLOOD_UNTIL_KEY, str(until), ex=wait + 30)
  except Exception:
    log.exception("note_scanner_flood failed retry_after=%s", retry_after)


async def wait_out_scanner_flood(
  *,
  max_wait_seconds: float = _MAX_ROOT_CARD_RETRY_AFTER_SLEEP_SECONDS,
) -> float:
  """Sleep until recorded scanner flood clears (capped). Returns seconds waited."""
  try:
    from app.persistence import redis_state

    client = redis_state.get_client()
    raw = await client.get(_SCANNER_FLOOD_UNTIL_KEY)
  except Exception:
    return 0.0
  if not raw:
    return 0.0
  try:
    until = float(raw.decode() if isinstance(raw, bytes) else raw)
  except (TypeError, ValueError):
    return 0.0
  wait = until - time.time()
  if wait <= 0:
    return 0.0
  wait = min(wait, float(max_wait_seconds))
  log.warning("waiting %.1fs for scanner Telegram flood to clear", wait)
  await asyncio.sleep(wait)
  return wait


async def send_scanner_with_retry(
  text: str,
  reply_to: int | None = None,
  chat_id: int | str | None = None,
  reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
  """Send scanner/feed-analysis notifications with the scanner bot token."""
  result = await submit_telegram(
    lambda: _send_message_with_retry(
      scanner_bot,
      text,
      reply_to,
      chat_id,
      reply_markup,
    ),
    priority=PRIORITY_CARD,
  )
  if result is None:
    raise RuntimeError("telegram actor dropped a non-droppable send")
  return result


async def send_scanner_root_card_with_retry(
  text: str,
  reply_to: int | None = None,
  chat_id: int | str | None = None,
  reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
  """Send the PLAN PUBLISHED / forming root card with a longer flood budget."""
  await wait_out_scanner_flood()
  result = await submit_telegram(
    lambda: _send_message_with_retry(
      scanner_bot,
      text,
      reply_to,
      chat_id,
      reply_markup,
      max_retry_after_sleep=_MAX_ROOT_CARD_RETRY_AFTER_SLEEP_SECONDS,
    ),
    priority=PRIORITY_LIFECYCLE,
  )
  if result is None:
    raise RuntimeError("telegram actor dropped a root-card send")
  return result


async def _send_message_with_retry(
  target_bot: Bot,
  text: str,
  reply_to: int | None,
  chat_id: int | str | None,
  reply_markup: InlineKeyboardMarkup | None,
  *,
  max_retry_after_sleep: int = _MAX_RETRY_AFTER_SLEEP_SECONDS,
) -> Message:
  for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
    try:
      return await target_bot.send_message(
        chat_id=(
          chat_id or runtime_config.delivery.telegram.telegram_channel_id
        ),
        text=text,
        reply_to_message_id=reply_to,
        reply_markup=reply_markup,
      )
    except TelegramRetryAfter as e:
      if target_bot is scanner_bot:
        await note_scanner_flood(e.retry_after)
        from app.bot.telegram_actor import note_flood
        note_flood(e.retry_after)
      if e.retry_after > max_retry_after_sleep:
        log.error(
          "Telegram flood-limited for %ds (exceeds %ds cap) - not "
          "blocking this task waiting it out; raising instead",
          e.retry_after,
          max_retry_after_sleep,
        )
        raise
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


async def edit_scanner_message_text(
  chat_id: int | str,
  message_id: int,
  text: str,
  reply_markup: InlineKeyboardMarkup | None = None,
  *,
  droppable: bool = False,
  priority: int = PRIORITY_CARD,
) -> Message | None:
  """Edit a message the scanner bot itself sent (forming cards)."""
  return await submit_telegram(
    lambda: scanner_bot.edit_message_text(
      chat_id=chat_id,
      message_id=int(message_id),
      text=text,
      reply_markup=reply_markup,
    ),
    priority=priority,
    droppable=droppable,
  )


async def edit_scanner_price_now(
  chat_id: int | str,
  message_id: int,
  text: str,
  reply_markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
  """Live Price-now edits — dropped while the actor is flood-paused."""
  return await edit_scanner_message_text(
    chat_id,
    message_id,
    text,
    reply_markup,
    droppable=True,
    priority=PRIORITY_PRICE,
  )


async def delete_scanner_message(chat_id: int | str, message_id: int) -> None:
  """Delete a message the scanner bot itself sent (forming cards).

  Telegram only lets the sending bot (or a channel admin) delete a message -
  forming cards go out via scanner_bot (send_scanner_with_retry), so this is
  a distinct function from delete_message (which uses the main `bot` and is
  for messages `bot` itself sent, eg. broadcast.py's signal posts).
  """
  await submit_telegram(
    lambda: scanner_bot.delete_message(int(chat_id), int(message_id)),
    priority=PRIORITY_CARD,
  )

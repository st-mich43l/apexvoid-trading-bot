"""Owner-opt-in end-of-day wipe of the ApexVoid bot's own DM with the owner.

Scope is deliberately narrow: only the main ApexVoid ``bot`` identity's
private chat with ``telegram_owner_id`` (see ``app.bot.client``). The
scanner/algo bot's own DM (autonomous root cards, and the owner's ``/1r``
personal-trade root card + its lifecycle) is a separate Telegram
conversation and is never touched here — that is the actual trade record.

Telegram's Bot API has no "list chat history" call, so per-message deletion
is only possible for message ids the bot itself already recorded. Outgoing
sends reach the owner DM through two different call paths that share no
single call site (``send_with_retry`` and direct aiogram convenience calls
like ``msg.answer(...)``), so recording happens at the lowest common layer
instead: a ``bot.session`` request middleware (every Telegram API call the
``bot`` object makes funnels through it) for outgoing messages, and a
``dp.message`` outer middleware for the owner's own incoming messages.

Gated end-to-end on ``delivery.telegram.owner_dm_daily_wipe_enabled``
(default off) — this is an irreversible action against the owner's own
chat history, so it must not start firing the moment this ships.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import runtime_config
from app.persistence import redis_state

log = logging.getLogger(__name__)

# Safety net for a missed sweep (restart mid-day, Redis hiccup): a day's
# journal self-expires well before it could ever be relevant again.
_JOURNAL_TTL_SECONDS = 3 * 24 * 3600


def _current_trade_date() -> str:
  """Same local-midnight boundary as ``store._current_trade_date()``."""
  tz = ZoneInfo(runtime_config.delivery.presentation.seq_reset_tz)
  return datetime.now(tz).date().isoformat()


def _journal_key(trade_date: str) -> str:
  return f"owner_dm:{trade_date}"


async def record_message_id(chat_id: object, message_id: object) -> None:
  """Journal one message id for today's owner-DM sweep. No-op unless enabled
  and ``chat_id`` matches the configured owner.
  """
  cfg = runtime_config.delivery.telegram
  if not cfg.owner_dm_daily_wipe_enabled:
    return
  owner_id = cfg.telegram_owner_id
  if not owner_id or chat_id is None or message_id is None:
    return
  try:
    if int(chat_id) != int(owner_id):
      return
    message_id = int(message_id)
  except (TypeError, ValueError):
    return
  client = redis_state.get_client()
  key = _journal_key(_current_trade_date())
  await client.sadd(key, message_id)
  await client.expire(key, _JOURNAL_TTL_SECONDS)


async def _pop_journal(trade_date: str) -> list[int]:
  client = redis_state.get_client()
  key = _journal_key(trade_date)
  raw_ids = await client.smembers(key)
  if raw_ids:
    await client.delete(key)
  return [int(value) for value in raw_ids]


async def sweep_owner_dm(trade_date: str) -> int:
  """Delete every journaled message for ``trade_date``. Returns count removed.

  Best-effort per message, mirroring ``broadcast.delete_posts`` - a message
  may already be gone (owner deleted it manually) or the delete may
  otherwise fail; one failure never aborts the rest of the sweep.
  """
  from app.bot.client import delete_message

  owner_id = runtime_config.delivery.telegram.telegram_owner_id
  ids = await _pop_journal(trade_date)
  if not owner_id or not ids:
    return 0
  deleted = 0
  for message_id in ids:
    try:
      await delete_message(owner_id, message_id)
      deleted += 1
    except Exception:
      log.warning(
        "could not delete owner DM message %s (trade_date=%s)",
        message_id, trade_date,
      )
  return deleted


async def owner_dm_daily_wipe_loop() -> None:
  """Sweep the just-completed trade day at each local midnight rollover.

  Deliberately does NOT use the "catch up immediately if already past the
  target hour" shape other daily loops in this codebase use (e.g.
  ``calendar_sync_loop``) - that shape fits a fixed daily hour, but here the
  boundary is midnight itself, so a restart at any point during the day
  would otherwise immediately sweep the CURRENT, still-accumulating day.
  Always targets the next upcoming midnight instead.
  """
  if not runtime_config.delivery.telegram.owner_dm_daily_wipe_enabled:
    log.info("Owner DM daily wipe disabled")
    return
  while True:
    try:
      tz = ZoneInfo(runtime_config.delivery.presentation.seq_reset_tz)
      now = datetime.now(tz)
      next_midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
      )
      delay = max(1.0, (next_midnight - now).total_seconds())
      await asyncio.sleep(delay)
      ended_date = (next_midnight - timedelta(days=1)).date().isoformat()
      count = await sweep_owner_dm(ended_date)
      log.info(
        "Owner DM daily wipe: removed %d message(s) for %s",
        count, ended_date,
      )
    except asyncio.CancelledError:
      raise
    except Exception:
      log.exception("Owner DM daily wipe failed")
      await asyncio.sleep(3600)


async def owner_dm_session_middleware(make_request, bot, method):
  """``bot.session`` request middleware - journals every outgoing message.

  Registered on the main ``bot`` only (never ``scanner_bot``). Runs for
  every Telegram API call the bot makes.

  ``make_request`` here is ``BaseSession.make_request`` itself (or an
  earlier-registered middleware wrapping it), which already returns the
  *unwrapped* Telegram result, not a ``Response`` envelope -
  ``AiohttpSession.make_request`` ends with ``return
  cast(TelegramType, response.result)``. So ``response`` is directly a
  ``Message`` for an ordinary send, a bare ``bool`` for e.g.
  ``DeleteMessage``/``AnswerCallbackQuery`` (no ``message_id`` - a no-op
  via ``getattr``'s default), or a ``list[Message]`` for
  ``SendMediaGroup`` (each item journaled so the whole album gets swept,
  not just silently dropped).
  """
  response = await make_request(bot, method)
  try:
    chat_id = getattr(method, "chat_id", None)
    items = response if isinstance(response, list) else [response]
    for item in items:
      message_id = getattr(item, "message_id", None)
      if message_id is not None:
        await record_message_id(chat_id, message_id)
  except Exception:
    log.exception("owner DM journal middleware failed")
  return response


async def owner_dm_incoming_middleware(handler, event, data):
  """``dp.message`` outer middleware - journals the owner's own messages."""
  try:
    cfg = runtime_config.delivery.telegram
    chat = getattr(event, "chat", None)
    from_user = getattr(event, "from_user", None)
    if (
      cfg.owner_dm_daily_wipe_enabled
      and cfg.telegram_owner_id
      and getattr(chat, "type", None) == "private"
      and from_user is not None
      and from_user.id == cfg.telegram_owner_id
    ):
      await record_message_id(chat.id, event.message_id)
  except Exception:
    log.exception("owner DM incoming journal middleware failed")
  return await handler(event, data)

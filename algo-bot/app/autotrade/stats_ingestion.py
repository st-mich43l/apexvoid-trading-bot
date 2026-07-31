"""Durable Redis-event ingestion for ``/trade_stats``.

Trade-result persistence used to be a side effect of the owner Telegram
delivery cursor. That coupled accounting to notification configuration and,
on the first deployment of the SQL tables, left older retained executor
events unrecorded. This module owns a separate cursor, backfills the retained
stream oldest-first at startup, then tails it continuously.
"""

from __future__ import annotations

import asyncio
import json
import logging

from app.core.config import settings
from app.persistence import redis_state
from app.persistence.store import record_auto_trade_event

log = logging.getLogger(__name__)

STATS_EVENT_CURSOR_KEY = "auto_trade:stats_event_cursor"


def _text(value: object) -> str:
  return value.decode() if isinstance(value, bytes) else str(value)


async def process_auto_trade_stats_entries(
  client,
  entries,
  *,
  cursor: str,
) -> str:
  """Persist a batch and advance only after each event is safely handled."""
  for entry_id, fields in entries:
    try:
      raw_payload = fields.get("payload")
      if raw_payload is None:
        raw_payload = fields.get(b"payload")
      event = json.loads(_text(raw_payload))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
      log.warning("Invalid auto-trade stats event %s: %s", entry_id, exc)
    else:
      await record_auto_trade_event(event)
    cursor = _text(entry_id)
    await client.set(STATS_EVENT_CURSOR_KEY, cursor)
  return cursor


async def backfill_retained_auto_trade_stats(client) -> str:
  """Catch the journal up to the retained stream tail before commands start."""
  stored = await client.get(STATS_EVENT_CURSOR_KEY)
  cursor = _text(stored) if stored else "0-0"
  start = f"({cursor}" if stored else "-"
  entries = await client.xrange(
    settings.auto_trade_event_stream,
    min=start,
    max="+",
  )
  if entries:
    cursor = await process_auto_trade_stats_entries(
      client,
      entries,
      cursor=cursor,
    )
  elif not stored:
    await client.set(STATS_EVENT_CURSOR_KEY, cursor)
  log.info(
    "Auto-trade stats backfill complete events=%s cursor=%s",
    len(entries),
    cursor,
  )
  return cursor


async def auto_trade_stats_ingestion_loop() -> None:
  """Tail executor events independently from Telegram delivery."""
  client = redis_state.get_client()
  cursor = await backfill_retained_auto_trade_stats(client)
  log.info("Auto-trade stats ingestion active from Redis cursor %s", cursor)
  while True:
    try:
      batches = await client.xread(
        {settings.auto_trade_event_stream: cursor},
        count=100,
        block=5000,
      )
      for _, entries in batches:
        cursor = await process_auto_trade_stats_entries(
          client,
          entries,
          cursor=cursor,
        )
    except asyncio.CancelledError:
      raise
    except Exception:
      stored = await client.get(STATS_EVENT_CURSOR_KEY)
      cursor = _text(stored) if stored else cursor
      log.exception(
        "Auto-trade stats ingestion failed at cursor %s; retrying",
        cursor,
      )
      await asyncio.sleep(5)

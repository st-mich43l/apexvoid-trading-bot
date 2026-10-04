from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramRetryAfter

from app.autotrade import delivery
from app.core.config import runtime_config
from app.persistence import redis_state


pytestmark = [pytest.mark.asyncio, pytest.mark.no_database]


async def test_owner_entries_advance_cursor_when_fill_stays_flooded(monkeypatch):
  client = redis_state.get_client()
  stream = runtime_config.runtime.redis_streams.events
  entry_id = await client.xadd(
    stream,
    {"payload": json.dumps({"type": "order_filled", "match_id": "x"})},
  )

  async def flood(*args, **kwargs):
    raise TelegramRetryAfter(
      method=SimpleNamespace(chat_id=123),
      message="Too Many Requests: retry after 20",
      retry_after=20,
    )

  sleeps: list[float] = []

  async def fake_sleep(seconds):
    sleeps.append(seconds)

  monkeypatch.setattr(delivery, "_deliver_auto_trade_event", flood)
  monkeypatch.setattr(delivery.asyncio, "sleep", fake_sleep)

  cursor = await delivery._process_owner_entries(
    client,
    [(entry_id, {"payload": json.dumps({"type": "order_filled", "match_id": "x"})})],
    cursor="0-0",
    chat_id=123,
  )

  assert cursor == entry_id
  assert sleeps == [5.0]


async def test_owner_loop_flood_backoff_is_short(monkeypatch):
  client = redis_state.get_client()
  stream = runtime_config.runtime.redis_streams.events
  await client.xadd(stream, {"payload": json.dumps({"type": "opened"})})
  await client.set(delivery._CURSOR_KEY, "0-0")

  async def flood_limited(*args, **kwargs):
    raise TelegramRetryAfter(
      method=SimpleNamespace(chat_id=123),
      message="Too Many Requests: retry after 39856",
      retry_after=39856,
    )

  monkeypatch.setattr(delivery, "_process_owner_entries", flood_limited)
  sleeps: list[float] = []

  async def fake_sleep(seconds):
    sleeps.append(seconds)
    if len(sleeps) >= 2:
      raise asyncio.CancelledError()

  monkeypatch.setattr(delivery.asyncio, "sleep", fake_sleep)

  with pytest.raises(asyncio.CancelledError):
    await delivery._auto_trade_owner_events_loop(chat_id=123)

  assert sleeps == [
    delivery._OWNER_LOOP_FLOOD_BACKOFF_SECONDS,
    delivery._OWNER_LOOP_FLOOD_BACKOFF_SECONDS,
  ]
  assert delivery._OWNER_LOOP_FLOOD_BACKOFF_SECONDS == 5

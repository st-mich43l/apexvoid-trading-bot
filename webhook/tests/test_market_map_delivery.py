from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app import market_map_delivery
from app.market_map import MapEntry, MarketMap, market_map_payload


def _map(lo: float = 4025.0, hi: float = 4028.0) -> MarketMap:
  return MarketMap(
    [MapEntry("buy", lo, hi, 4025, 4028, "zone", ["OB", "fresh"], 9)],
    4041,
    4047,
    4032,
    4062,
    "down",
    "M30",
  )


@pytest.mark.asyncio
async def test_session_map_sends_once_per_key_and_skips_unchanged_next_session(
  monkeypatch,
):
  meta = {}
  sent = AsyncMock()
  current = {"map": _map()}

  async def get_meta(key):
    return meta.get(key)

  async def set_meta(key, value):
    meta[key] = value

  async def get_map(symbol):
    assert symbol == "XAU"
    return current["map"]

  monkeypatch.setattr(market_map_delivery.settings, "map_session_send", True)
  monkeypatch.setattr(market_map_delivery.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(market_map_delivery.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(market_map_delivery.settings, "map_change_min", 1.0)
  monkeypatch.setattr(market_map_delivery, "get_meta", get_meta)
  monkeypatch.setattr(market_map_delivery, "set_meta", set_meta)
  monkeypatch.setattr(market_map_delivery, "get_current_market_map", get_map)
  monkeypatch.setattr(market_map_delivery, "send_with_retry", sent)

  london = datetime(2026, 7, 16, 7, 5, tzinfo=timezone.utc)
  ny = datetime(2026, 7, 16, 13, 5, tzinfo=timezone.utc)

  assert await market_map_delivery._market_map_session_tick(london)
  assert not await market_map_delivery._market_map_session_tick(london)
  assert not await market_map_delivery._market_map_session_tick(ny)
  assert sent.await_count == 1
  assert meta["last_map_session"] == "2026-07-16:NY"
  assert meta["last_market_map:XAU"] == market_map_payload(_map())


@pytest.mark.asyncio
async def test_session_map_resends_when_band_moves_by_threshold(monkeypatch):
  previous = _map()
  current = replace(
    previous,
    entries=[replace(previous.entries[0], lo=4026.0, hi=4029.0)],
  )
  meta = {
    "last_map_session": "2026-07-16:LONDON",
    "last_market_map:XAU": market_map_payload(previous),
  }
  sent = AsyncMock()

  async def get_meta(key):
    return meta.get(key)

  async def set_meta(key, value):
    meta[key] = value

  monkeypatch.setattr(market_map_delivery.settings, "map_session_send", True)
  monkeypatch.setattr(market_map_delivery.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(market_map_delivery.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(market_map_delivery.settings, "map_change_min", 1.0)
  monkeypatch.setattr(market_map_delivery, "get_meta", get_meta)
  monkeypatch.setattr(market_map_delivery, "set_meta", set_meta)
  monkeypatch.setattr(
    market_map_delivery,
    "get_current_market_map",
    AsyncMock(return_value=current),
  )
  monkeypatch.setattr(market_map_delivery, "send_with_retry", sent)

  fired = await market_map_delivery._market_map_session_tick(
    datetime(2026, 7, 16, 13, 5, tzinfo=timezone.utc)
  )

  assert fired
  sent.assert_awaited_once()
  assert sent.await_args.kwargs == {"chat_id": 42}


def test_session_open_key_uses_latest_configured_open(monkeypatch):
  monkeypatch.setattr(market_map_delivery.settings, "session_asia_start", 22)
  monkeypatch.setattr(market_map_delivery.settings, "session_london_start", 7)
  monkeypatch.setattr(market_map_delivery.settings, "session_ny_start", 13)

  assert market_map_delivery._session_open_key(
    datetime(2026, 7, 16, 1, tzinfo=timezone.utc)
  ) == "2026-07-15:ASIA"
  assert market_map_delivery._session_open_key(
    datetime(2026, 7, 16, 8, tzinfo=timezone.utc)
  ) == "2026-07-16:LONDON"

"""current_market_setups_text: the owner-facing surface separating current
actionable structure from stale structural memory (owner 2026-09-17).
"""

from __future__ import annotations

import json

import pytest

from app.autotrade.setups_report import current_market_setups_text
from app.autotrade.zone_watch import (
  GRADE_A,
  WATCHING_RETEST,
  ZONE_WATCH_INDEX_KEY,
  ZoneWatch,
  zone_watch_key,
)
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


def _zone(**overrides) -> ZoneWatch:
  base = dict(
    version=3,
    zone_id="zone-1",
    symbol="XAU",
    direction="BUY",
    low=4295.0,
    high=4300.0,
    width=5.0,
    source_timeframe="M5",
    structural_sources=("key_level",),
    confluence_tags=("key_level",),
    grade=GRADE_A,
    score=2.0,
    freshness=0,
    touch_count=0,
    discovered_at=1_000_000,
    last_confirmed_at=1_000_000,
    last_touch_at=None,
    invalidation_price=None,
    state=WATCHING_RETEST,
    market_map_id="mm-1",
    structure_signature="sig-1",
    updated_at=1_000_000,
  )
  base.update(overrides)
  return ZoneWatch(**base)


async def _seed_zone(record: ZoneWatch) -> None:
  # Bypasses discover_zone_watch's Lua CAS (real_redis-only) - a raw
  # SET + global-index SADD is exactly what list_active_zone_watches'
  # own lazy symbol-index migration expects to find.
  client = redis_state.get_client()
  await client.set(zone_watch_key(record.zone_id), json.dumps(record.to_dict()))
  await client.sadd(ZONE_WATCH_INDEX_KEY, record.zone_id)


async def _seed_spot(symbol: str, bid: float, ask: float) -> None:
  client = redis_state.get_client()
  await client.set(
    f"price:{symbol}:spot", json.dumps({"bid": bid, "ask": ask, "ts": 1}),
  )


async def _seed_bars(symbol: str, tf: str, closes: list[float]) -> None:
  client = redis_state.get_client()
  for index, close in enumerate(closes):
    payload = json.dumps({
      "t": index, "o": close, "h": close + 1, "l": close - 1, "c": close, "v": 1,
    })
    await client.zadd(f"bars:{symbol}:{tf}", {payload: index})


@pytest.mark.asyncio
async def test_no_current_setup_when_only_far_structural_memory_exists():
  await _seed_spot("XAU", 4360.0, 4360.5)
  await _seed_bars("XAU", "M5", [4358.0 + (i % 3) for i in range(30)])
  await _seed_zone(_zone(zone_id="far-buy", low=4280.0, high=4285.0, direction="BUY"))

  text = await current_market_setups_text("XAU")

  assert "NO CURRENT ACTIONABLE SETUP" in text
  assert "STRUCTURAL MEMORY" in text
  assert "4280.00-4285.00" in text


@pytest.mark.asyncio
async def test_nearby_zone_appears_as_active():
  await _seed_spot("XAU", 4360.0, 4360.5)
  await _seed_bars("XAU", "M5", [4358.0 + (i % 3) for i in range(30)])
  await _seed_zone(_zone(zone_id="near-sell", low=4361.0, high=4362.0, direction="SELL"))

  text = await current_market_setups_text("XAU")

  assert "NO CURRENT ACTIONABLE SETUP" not in text
  assert "ACTIVE" in text
  assert "4361.00-4362.00" in text


@pytest.mark.asyncio
async def test_no_spot_price_reports_unavailable_instead_of_guessing():
  text = await current_market_setups_text("EURUSD")
  assert "No live spot price" in text

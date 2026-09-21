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


def _map_entry(side, lo, hi, *, score=20.0, tier="major", tags=("OB", "supply"), inside=False):
  from app.analysis.market_map import MapEntry

  return MapEntry(
    side=side, lo=lo, hi=hi, label_lo=lo, label_hi=hi, tier=tier,
    tags=list(tags), score=score, contains_price=inside,
  )


def test_map_zone_lines_list_nearest_major_zones_and_skip_far_or_minor():
  from types import SimpleNamespace

  from app.autotrade.setups_report import map_zone_lines

  market_map = SimpleNamespace(entries=[
    _map_entry("sell", 4344.1, 4356.0, score=23.0, tags=("OB", "breaker", "supply", "FVG")),
    _map_entry("buy", 4298.3, 4304.3, score=10.0, tags=("demand",)),
    _map_entry("buy", 4290.0, 4298.3, score=10.0, tier="minor"),
    _map_entry("sell", 4500.0, 4510.0),
  ])
  lines = map_zone_lines(market_map, 4336.6, 5.0, [])

  assert lines[0].startswith("  SELL 4344.10-4356.00")
  assert "7.5 pts / 1.5 ATR" in lines[0]
  assert "OB,breaker,supply,FVG" in lines[0]
  assert "waiting for M5 reaction" in lines[0]
  assert any("4298.30-4304.30" in line for line in lines)
  assert not any("4290.00" in line for line in lines)  # minor tier
  assert not any("4500.00" in line for line in lines)  # beyond 8 ATR


def test_map_zone_lines_mark_zones_zonewatch_already_tracks():
  from types import SimpleNamespace

  from app.autotrade.setups_report import map_zone_lines

  market_map = SimpleNamespace(entries=[_map_entry("sell", 4344.0, 4356.0)])
  tracked = _zone(zone_id="z", direction="SELL", low=4350.0, high=4352.0)
  other_side = _zone(zone_id="y", direction="BUY", low=4350.0, high=4352.0)

  assert "tracked in ZoneWatch" in map_zone_lines(market_map, 4336.0, 2.0, [tracked])[0]
  assert "waiting for M5 reaction" in map_zone_lines(market_map, 4336.0, 2.0, [other_side])[0]


def test_map_zone_lines_price_inside_and_missing_map():
  from types import SimpleNamespace

  from app.autotrade.setups_report import map_zone_lines

  inside = SimpleNamespace(entries=[_map_entry("buy", 4330.0, 4340.0, inside=True)])
  assert "price inside" in map_zone_lines(inside, 4335.0, 2.0, [])[0]
  assert map_zone_lines(None, 4335.0, 2.0, []) == []
  assert map_zone_lines(SimpleNamespace(entries=[]), 4335.0, 2.0, []) == []


@pytest.mark.asyncio
async def test_report_includes_map_section_and_survives_map_failure(monkeypatch):
  from types import SimpleNamespace

  from app.analysis import market_map_delivery

  await _seed_spot("XAU", 4336.0, 4336.5)
  await _seed_bars("XAU", "M5", [4334.0 + (i % 3) for i in range(30)])

  async def _map(_symbol):
    return SimpleNamespace(bias="down", entries=[_map_entry("sell", 4344.0, 4356.0)])

  monkeypatch.setattr(market_map_delivery, "get_current_market_map", _map)
  text = await current_market_setups_text("XAU")
  assert "MARKET MAP ZONES" in text and "bias down" in text
  assert "SELL 4344.00-4356.00" in text

  async def _boom(_symbol):
    raise RuntimeError("map unavailable")

  monkeypatch.setattr(market_map_delivery, "get_current_market_map", _boom)
  text = await current_market_setups_text("XAU")
  assert "MARKET MAP ZONES" not in text
  assert "XAU setups" in text

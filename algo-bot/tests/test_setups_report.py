"""/algo_setups: the owner-facing list of Go's major zones near the live quote."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app.autotrade.setups_report as setups_report
from app.autotrade.setups_report import current_market_setups_text, map_zone_lines
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


async def _seed_spot(symbol: str, bid: float, ask: float) -> None:
  client = redis_state.get_client()
  await client.set(
    f"price:{symbol}:spot", json.dumps({"bid": bid, "ask": ask, "ts": 1}),
  )


def _map_entry(side, lo, hi, *, score=20.0, tier="major", tags=("OB", "supply"), inside=False):
  return SimpleNamespace(
    side=side, lo=lo, hi=hi, label_lo=lo, label_hi=hi, tier=tier,
    tags=list(tags), score=score, contains_price=inside,
  )


@pytest.mark.asyncio
async def test_no_spot_price_reports_unavailable_instead_of_guessing():
  text = await current_market_setups_text("EURUSD")
  assert "No live spot price" in text


def test_map_zone_lines_list_nearest_major_zones_and_skip_far_or_minor():
  market_map = SimpleNamespace(entries=[
    _map_entry("sell", 4344.1, 4356.0, score=23.0, tags=("OB", "breaker", "supply", "FVG")),
    _map_entry("buy", 4298.3, 4304.3, score=10.0, tags=("demand",)),
    _map_entry("buy", 4290.0, 4298.3, score=10.0, tier="minor"),
    _map_entry("sell", 4500.0, 4510.0),
  ])
  lines = map_zone_lines(market_map, 4336.6, 5.0)

  assert lines[0].startswith("  SELL 4344.10-4356.00")
  assert "7.5 pts / 1.5 ATR" in lines[0]
  assert "OB,breaker,supply,FVG" in lines[0]
  assert any("4298.30-4304.30" in line for line in lines)
  assert not any("4290.00" in line for line in lines)  # minor tier
  assert not any("4500.00" in line for line in lines)  # beyond 8 ATR


def test_map_zone_lines_price_inside_and_missing_map():
  inside = SimpleNamespace(entries=[_map_entry("buy", 4330.0, 4340.0, inside=True)])
  assert "price inside" in map_zone_lines(inside, 4335.0, 2.0)[0]
  assert map_zone_lines(None, 4335.0, 2.0) == []
  assert map_zone_lines(SimpleNamespace(entries=[]), 4335.0, 2.0) == []


@pytest.mark.asyncio
async def test_report_lists_map_zones_and_survives_map_failure(monkeypatch):
  await _seed_spot("XAU", 4336.0, 4336.5)

  async def _map(_symbol, _client):
    return SimpleNamespace(
      bias="down",
      entries=[_map_entry("sell", 4344.0, 4356.0)],
      atr_by_timeframe={"M5": 5.0},
    )

  monkeypatch.setattr(setups_report, "load_go_market_map", _map)
  text = await current_market_setups_text("XAU")
  assert "MARKET MAP ZONES" in text and "bias down" in text
  assert "SELL 4344.00-4356.00" in text

  async def _boom(_symbol, _client):
    raise RuntimeError("map unavailable")

  monkeypatch.setattr(setups_report, "load_go_market_map", _boom)
  text = await current_market_setups_text("XAU")
  assert "MARKET MAP ZONES" not in text
  assert "NO MAJOR ZONE NEAR PRICE" in text

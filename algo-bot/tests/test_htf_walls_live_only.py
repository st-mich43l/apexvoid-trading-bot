"""Opposing walls must still be walls (owner 2026-09-21).

A BUY was vetoed "entry inside opposing supply 4348.5-4356.4": a Sep-17 zone
touched 14 times that price had spent two days trading far above. ``_htf_zones``
fed every width-eligible M15 zone to the barrier guard without asking whether
price had since accepted through it.
"""

from __future__ import annotations

import pandas as pd
import pytest

import app.autotrade.worker as worker
from app.analysis.types import Leg, Zone
from app.core.config import runtime_config

pytestmark = pytest.mark.no_database


def _frame(closes: list[float]) -> pd.DataFrame:
  index = pd.date_range("2026-09-01", periods=len(closes), freq="15min", tz="UTC")
  rows = []
  previous = closes[0]
  for close in closes:
    rows.append({
      "open": previous,
      "high": max(previous, close) + 1.0,
      "low": min(previous, close) - 1.0,
      "close": close,
      "volume": 1.0,
    })
    previous = close
  return pd.DataFrame(rows, index=index)


def _patch_zones(monkeypatch, zones: list[Zone]) -> None:
  monkeypatch.setattr(worker, "displacement", lambda *a, **k: [Leg(0, 1, "down", 5.0)])
  monkeypatch.setattr(worker, "supply_demand", lambda *a, **k: list(zones))


def test_zone_price_accepted_through_is_not_an_opposing_wall(monkeypatch):
  # 60 bars: price rangebound near 100, then closes 12-20 above the "dead"
  # supply (110-113) and stays there; the "live" supply (130-133) is untouched.
  closes = [100.0 + (i % 3) for i in range(30)] + [126.0 + (i % 3) for i in range(30)]
  frame = _frame(closes)
  dead = Zone(110.0, 113.0, "supply", origin_index=5, created_ts=frame.index[5])
  live = Zone(130.0, 133.0, "supply", origin_index=5, created_ts=frame.index[5])
  _patch_zones(monkeypatch, [dead, live])

  walls = worker._htf_zones({worker._HTF_TIMEFRAME: frame}, runtime_config, symbol="XAU")

  assert [(round(z.low), round(z.high)) for z in walls] == [(130, 133)]


def test_zone_only_swept_and_reclaimed_stays_a_wall(monkeypatch):
  # One spike close above the zone that is reclaimed the next bar is a
  # liquidity sweep, not acceptance.
  closes = [100.0 + (i % 3) for i in range(40)]
  closes[20] = 122.0
  frame = _frame(closes)
  swept = Zone(110.0, 113.0, "supply", origin_index=5, created_ts=frame.index[5])
  _patch_zones(monkeypatch, [swept])

  walls = worker._htf_zones({worker._HTF_TIMEFRAME: frame}, runtime_config, symbol="XAU")

  assert [(round(z.low), round(z.high)) for z in walls] == [(110, 113)]


def test_demand_price_accepted_below_is_not_an_opposing_wall(monkeypatch):
  closes = [130.0 + (i % 3) for i in range(30)] + [104.0 + (i % 3) for i in range(30)]
  frame = _frame(closes)
  dead = Zone(120.0, 123.0, "demand", origin_index=5, created_ts=frame.index[5])
  live = Zone(90.0, 93.0, "demand", origin_index=5, created_ts=frame.index[5])
  _patch_zones(monkeypatch, [dead, live])

  walls = worker._htf_zones({worker._HTF_TIMEFRAME: frame}, runtime_config, symbol="XAU")

  assert [(round(z.low), round(z.high)) for z in walls] == [(90, 93)]

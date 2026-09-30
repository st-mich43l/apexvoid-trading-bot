"""Canonical scalp naming + shared M1/M5 context helper."""

from __future__ import annotations

import pandas as pd
import pytest

from app.scalping.models import ARCHETYPE_RANGE_SWEEP, STRATEGY_DISPLAY


pytestmark = pytest.mark.no_database


def _trending_ohlc(*, bars: int, direction: str, start: float = 2000.0) -> pd.DataFrame:
  rows = []
  index = []
  price = start
  step = 2.0 if direction == "up" else -2.0
  for i in range(bars):
    o = price
    c = price + step
    h = max(o, c) + 0.5
    low = min(o, c) - 0.5
    rows.append({"open": o, "high": h, "low": low, "close": c, "volume": 1.0})
    index.append(pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=i))
    price = c
  return pd.DataFrame(rows, index=index)


def test_strategy_display_maps_range_sweep_archetype():
  assert STRATEGY_DISPLAY[ARCHETYPE_RANGE_SWEEP] == "Range Sweep Scalp"

"""Scalp zone access — inside or momentum chase within budget."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.autotrade.execution_confirmation import (
  ZONE_ACCESS_MOMENTUM_CHASE,
  ZONE_ACCESS_RETEST_ONLY,
  scalp_zone_access,
)
from app.analysis.entry_location import EntryLocationDecision
from app.autotrade.zone_watch import ZoneWatch


pytestmark = pytest.mark.no_database


def test_sell_scalp_chase_within_budget_past_zone_low():
  # SELL zone 4100–4102; ask/bid already 1.0 below low → 10p chase.
  access = scalp_zone_access(
    "SELL",
    bid=4099.0,
    ask=4099.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=100.0,
  )
  assert access.status == "chase"
  assert access.executable is True
  assert access.chase_pips == pytest.approx(10.0)


def test_breakout_retest_sell_below_zone_waits_not_chases():
  """Live 2026-08-31 XAU: SELL retest must not market below the retest band."""
  access = scalp_zone_access(
    "SELL",
    bid=4429.62,
    ask=4429.72,
    zone_low=4430.76,
    zone_high=4433.36,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=40.0,
    zone_access_mode=ZONE_ACCESS_RETEST_ONLY,
  )
  assert access.status == "approach_wait"
  assert access.executable is False
  assert access.chase_pips == pytest.approx(11.4, abs=0.05)


def test_breakout_retest_sell_inside_zone_executable():
  access = scalp_zone_access(
    "SELL",
    bid=4431.0,
    ask=4431.1,
    zone_low=4430.76,
    zone_high=4433.36,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=40.0,
    zone_access_mode=ZONE_ACCESS_RETEST_ONLY,
  )
  assert access.status == "inside"
  assert access.executable is True


def test_breakout_retest_buy_above_zone_waits_not_chases():
  access = scalp_zone_access(
    "BUY",
    bid=4103.0,
    ask=4103.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=100.0,
    zone_access_mode=ZONE_ACCESS_RETEST_ONLY,
  )
  assert access.status == "approach_wait"
  assert access.executable is False


def test_momentum_chase_mode_unchanged_for_range_sweep():
  access = scalp_zone_access(
    "SELL",
    bid=4099.0,
    ask=4099.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=100.0,
    zone_access_mode=ZONE_ACCESS_MOMENTUM_CHASE,
  )
  assert access.status == "chase"
  assert access.executable is True


def test_sell_scalp_approach_above_zone_still_waits():
  # SELL supply: price still above the band (wrong side / not past) → wait.
  access = scalp_zone_access(
    "SELL",
    bid=4103.0,
    ask=4103.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=100.0,
  )
  assert access.status == "approach_wait"
  assert access.executable is False


def test_sell_scalp_chase_missed_beyond_100():
  access = scalp_zone_access(
    "SELL",
    bid=4089.0,  # 110 pips below low
    ask=4089.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=100.0,
  )
  assert access.status == "chase_missed"
  assert access.executable is False


  # Missing M1 trigger may still wait — but never die on quote_outside.


def _zone_record(*, direction: str = "SELL") -> ZoneWatch:
  return ZoneWatch(
    version=1,
    zone_id="z1",
    symbol="XAU",
    direction=direction,
    low=4100.0,
    high=4102.0,
    width=2.0,
    source_timeframe="M5",
    structural_sources=("FVG",),
    confluence_tags=(),
    grade="A",
    score=1.0,
    freshness=0,
    touch_count=0,
    discovered_at=1,
    last_confirmed_at=1,
    last_touch_at=None,
    invalidation_price=None,
    state="watching_retest",
    market_map_id="",
    structure_signature="sig",
    updated_at=1,
    technique_tags=("fvg",),
  )

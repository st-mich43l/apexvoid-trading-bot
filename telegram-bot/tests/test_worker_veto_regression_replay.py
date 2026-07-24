"""Replay tests for the post-23-Jul worker veto regression (fix/demo-eval-
worker-veto-regression). Written against current master FIRST - several of
these fail before the production fix lands, proving the regression rather
than just asserting the desired end state.

Baseline compared: 4be59b123604af38df08b56bc37dea02d1d1d59c (PR #87, 23 Jul).
Current master at investigation time: 7ba44e581e17d14566730b7ee9ddd6897e1d39ca.

Production evidence (apexvoid VPS, checked live before writing this file):
  auto_trade:gate_reject:XAU:entry_inside_opposing_zone = 440
  auto_trade:gate_reject:XAU:opposing_barrier = 51
  auto_trade:gate_reject:XAU:overlapping_zone_conflict = 22
  auto_trade:gate_reject:XAU:counter_bias_target_barrier = 39
  auto_trade:gate_reject:XAU:strategy_entry_moved = 12
Live logs showed "Mapped Zone Reaction"/"Range Edge Scalp" repeatedly
rejected with "entry X inside opposing round/reaction Y-Z" at prices that
are each strategy's own structural source, not a genuinely separate barrier.
"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from app.autotrade import worker
from app.autotrade.execution_policy import max_entry_drift_pips
from app.analysis.types import Level, Zone
from app.persistence import redis_state


# ---------------------------------------------------------------------------
# Root cause 1: a structural source vetoes the strategy trading it
# ---------------------------------------------------------------------------

def test_opposing_barrier_reason_excludes_a_key_level_that_is_the_candidates_own_source():
  """Key Level / Mapped Zone Reaction: the candidate's own key-level band is
  passed straight through into `levels` (htf_levels are symbol-wide, not
  filtered per-candidate) - before the fix, a strategy trading INTO its own
  level is indistinguishable from a strategy blocked BY an unrelated one.
  """
  own_level = [Level(price=4055.20, kind="round", touches=3, band=3.34)]
  entry = 4055.20  # dead center of the strategy's own thesis band

  reason = worker._opposing_barrier_reason(
    "BUY", entry, 1.2, [], own_level, 0.5,
    exclude_low=4051.86, exclude_high=4058.54,
  )

  assert reason is None


def test_opposing_barrier_reason_excludes_sells_own_supply_source():
  own_supply = [Zone(4047.24, 4050.56, "supply", touches=2)]
  entry = 4049.0  # inside the SELL's own supply thesis

  reason = worker._opposing_barrier_reason(
    "SELL", entry, 1.2, own_supply, [], 0.5,
    exclude_low=4047.24, exclude_high=4050.56,
  )

  assert reason is None


def test_opposing_barrier_reason_excludes_buys_own_demand_source():
  own_demand = [Zone(4040.0, 4043.0, "demand", touches=2)]
  entry = 4041.5

  reason = worker._opposing_barrier_reason(
    "BUY", entry, 1.2, own_demand, [], 0.5,
    exclude_low=4040.0, exclude_high=4043.0,
  )

  assert reason is None


def test_opposing_barrier_reason_still_vetoes_a_genuinely_separate_barrier():
  """Exclusion must not blanket-disable the guard: an unrelated opposing
  zone that does not overlap the candidate's own source still fires.
  """
  unrelated_supply = [Zone(4116.0, 4127.0, "supply", touches=8)]

  reason = worker._opposing_barrier_reason(
    "BUY", 4116.25, 1.2, unrelated_supply, [], 0.5,
    exclude_low=4000.0, exclude_high=4001.0,
  )

  assert reason is not None
  assert "inside opposing" in reason


def test_opposing_barrier_reason_without_exclusion_args_is_unchanged():
  """Byte-identical default behaviour for every existing caller that does
  not yet pass exclude_low/exclude_high.
  """
  supply = [Zone(4116.0, 4127.0, "supply", touches=8)]
  reason = worker._opposing_barrier_reason("BUY", 4116.25, 1.2, supply, [], 0.5)
  assert reason is not None
  assert "inside opposing" in reason


# ---------------------------------------------------------------------------
# Root cause 5: entry drift formula has no floor
# ---------------------------------------------------------------------------

def test_max_entry_drift_pips_has_a_configured_floor_even_with_tight_atr_and_room():
  """XAU M1 ATR can be small enough that atr_pips and room_cap both
  collapse the effective limit to 3-5 pips - well inside normal tick
  latency. The floor must not let the effective limit fall below it.
  """
  limit, measured = max_entry_drift_pips(
    strategy="Mapped Zone Reaction",
    atr=0.3,  # tiny ATR -> atr_pips collapses toward zero without a floor
    pip_size=0.1,
    remaining_target_room_pips=10.0,  # room * 0.45 = 4.5, also tiny
    cfg=_Cfg(
      auto_trade_max_entry_distance_pips=10.0,
      auto_trade_map_min_entry_drift_pips=10.0,
      auto_trade_map_max_entry_drift_atr=1.0,
      auto_trade_map_hard_entry_drift_pips=20.0,
    ),
  )

  assert limit >= 10.0, f"drift floor not enforced, got {limit} ({measured})"


class _Cfg:
  def __init__(self, **kwargs):
    for key, value in kwargs.items():
      setattr(self, key, value)


# ---------------------------------------------------------------------------
# Root cause 2: cooldown marker has no close-reason
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_zone_cooldown_ignores_unconfirmed_reason_by_default():
  """A marker with no confirmed stop_loss reason (the only kind the current
  broker integration can produce) must not block a same-direction re-entry
  once the reason/confidence-aware check lands.
  """
  client = redis_state.get_client()
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    (
      '{"entry_price": 4116.25, "stop_price": 4111.54, "closed_at": 1000,'
      ' "reason": "reconciliation_unknown", "confidence": "unconfirmed"}'
    ),
  )

  reason = await worker._zone_cooldown_reason(
    client, "XAU", "BUY", 4116.90, 2.0, 1.0,
  )

  assert reason is None


# ---------------------------------------------------------------------------
# Root cause 3: overlap veto deletes both directions unconditionally
# ---------------------------------------------------------------------------

def _frame_with_bullish_reclaim() -> pd.DataFrame:
  index = pd.date_range("2026-07-23", periods=6, freq="1min", tz="UTC")
  return pd.DataFrame({
    "open":  [4118.0, 4117.5, 4116.5, 4116.2, 4117.0, 4119.5],
    "high":  [4118.3, 4117.8, 4116.8, 4117.5, 4119.8, 4120.2],
    "low":   [4117.6, 4116.3, 4115.9, 4116.0, 4116.9, 4119.3],
    "close": [4117.8, 4116.5, 4116.1, 4117.3, 4119.6, 4120.0],
  }, index=index)


def test_overlap_resolves_to_buy_thesis_on_bullish_m1_reclaim():
  """A bullish reclaim through the demand zone's own reaction memory must
  let the BUY thesis survive an otherwise-symmetric BUY/SELL overlap,
  instead of the current unconditional double-reject.
  """
  from app.analysis.market_map import MapEntry, MarketMap
  from app.autotrade.worker import _resolve_overlap_thesis

  demand = MapEntry(
    side="buy", lo=4112.0, hi=4122.0, label_lo=4112, label_hi=4122,
    tier="major", tags=[], score=6.0,
  )
  supply = MapEntry(
    side="sell", lo=4116.0, hi=4127.0, label_lo=4116, label_hi=4127,
    tier="major", tags=[], score=5.0,
  )
  market_map = MarketMap(
    entries=[demand, supply], price=4118.0, eq=None, box_low=None,
    box_high=None, bias="up", bias_tf="M30",
  )

  outcome = _resolve_overlap_thesis(
    "BUY", 4118.0, market_map, _frame_with_bullish_reclaim(), atr=1.0, cfg=None,
  )

  assert outcome.outcome in ("allow", "allow_with_warning")
  assert outcome.hard_block is False

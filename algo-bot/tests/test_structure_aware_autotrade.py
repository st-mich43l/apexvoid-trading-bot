"""Structure-aware high-frequency autotrade regression fixtures."""

from dataclasses import replace
from types import SimpleNamespace

from tests.configuration.canonical_fixtures import (
  execution_cfg,
  scalp_ranges_cfg,
)

import pandas as pd
import pytest

from app.analysis.scalp_ranges import (
  RANGE_STATE_CONFIRMED,
  RANGE_STATE_NO_RANGE,
  RANGE_STATE_POST_IMPULSE,
  RANGE_STATE_PROVISIONAL,
  ScalpBarrier,
  _fallback_barrier,
  build_scalp_structure,
  build_scalp_structure_detailed,
)
from app.autotrade.execution_policy import (
  classify_tier,
  max_entry_drift_pips,
  risk_multiplier_for_tier,
)
from app.autotrade.multi_match import (
  dedupe_matches,
  same_thesis,
  select_primary,
  serialize_matches,
  deserialize_matches,
)
from app.autotrade.range_targets import select_range_target
from app.autotrade.strategy_match import (
  STRATEGY_MATCH_VERSION,
  StrategyMatch,
  strategy_match_id,
)


pytestmark = pytest.mark.no_database


def _cfg(**overrides):
  values = {
    "range_scalp_lookback": 36,
    "range_scalp_cluster_atr": 0.20,
    "range_scalp_cluster_min_abs": 0.0,
    "range_scalp_min_touches": 3,
    "range_scalp_min_wick_frac": 0.35,
    "range_scalp_entry_tol_atr": 0.15,
    "range_scalp_min_width_atr": 1.2,
    "range_scalp_max_width_atr": 6.0,
    "range_scalp_min_room_atr": 1.0,
    "range_scalp_break_closes": 2,
    "range_scalp_min_inside_closes": 3,
    "scalp_barrier_fallback_enabled": True,
    "scalp_barrier_fallback_min_confirmations": 1,
    "scalp_range_provisional_enabled": True,
    "scalp_post_impulse_range_enabled": True,
    "round_step": 5.0,
  }
  values.update(overrides)
  values.pop("pip_size", None)
  return scalp_ranges_cfg(**values)


def _range_df() -> pd.DataFrame:
  rows = [
    (105, 107, 103, 106, 100),
    (106, 110, 105, 106, 100),
    (105, 107, 103, 105, 100),
    (104, 105, 100, 104, 100),
    (104, 107, 103, 106, 100),
    (106, 110, 105, 106, 100),
    (105, 107, 103, 105, 100),
    (104, 105, 100, 104, 100),
    (104, 107, 103, 106, 100),
    (106, 110, 105, 106, 100),
    (104, 105, 100, 104, 100),
    (106, 111, 105, 106, 100),
  ]
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=pd.date_range("2026-07-17", periods=len(rows), freq="5min", tz="UTC"),
  ).astype(float)


def _resistance_only_df() -> pd.DataFrame:
  """Two clear resistances, weak/noisy support side (production shape)."""
  rows = []
  for _ in range(4):
    rows.extend([
      (106, 110, 105.2, 106.2, 100),
      (106, 110.2, 105.4, 105.8, 100),
      (105.5, 107, 104.8, 106.0, 100),
      (106, 109.8, 105.0, 106.5, 100),
    ])
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=pd.date_range("2026-07-17", periods=len(rows), freq="5min", tz="UTC"),
  ).astype(float)


def test_support_resistance_symmetry_on_clean_range():
  df = _range_df()
  atr = pd.Series([2.0] * len(df), index=df.index)
  barriers, scalp_range = build_scalp_structure(df, atr, [], [], None, _cfg())
  supports = [b for b in barriers if b.side == "support"]
  resistances = [b for b in barriers if b.side == "resistance"]
  assert supports and resistances
  assert scalp_range is not None
  assert scalp_range.state == RANGE_STATE_CONFIRMED


def test_two_resistances_zero_support_gets_fallback_or_reason():
  df = _resistance_only_df()
  atr = pd.Series([1.5] * len(df), index=df.index)
  detailed = build_scalp_structure_detailed(df, atr, [], [], None, _cfg())
  resistances = [b for b in detailed.barriers if b.side == "resistance"]
  supports = [b for b in detailed.barriers if b.side == "support"]
  assert len(resistances) >= 1
  if not supports:
    assert detailed.missing_side_reason in {
      "no_support_after_fallback",
      "range_geometry_rejected",
      "no_support_clustering",
    }
  else:
    assert any(b.fallback for b in supports) or detailed.scalp_range is not None


def test_missing_resistance_fallback_is_symmetric():
  source = _resistance_only_df()
  mirrored = source.copy()
  for column in ("open", "high", "low", "close"):
    mirrored[column] = 220.0 - source[column]
  mirrored["high"], mirrored["low"] = (
    220.0 - source["low"],
    220.0 - source["high"],
  )
  atr = pd.Series([1.5] * len(mirrored), index=mirrored.index)
  detailed = build_scalp_structure_detailed(
    mirrored, atr, [], [], None, _cfg(),
  )
  supports = [b for b in detailed.barriers if b.side == "support"]
  resistances = [b for b in detailed.barriers if b.side == "resistance"]
  assert supports
  if not resistances:
    assert detailed.missing_side_reason in {
      "no_resistance_after_fallback",
      "range_geometry_rejected",
      "no_resistance_clustering",
    }
  else:
    assert (
      any(barrier.fallback for barrier in resistances)
      or detailed.scalp_range is not None
    )


def test_fallback_barrier_ignores_a_stale_extreme_from_before_the_opposite_edge():
  """2026-08-03 incident (XAU live): the fallback used to search the single
  most extreme low/high across the *entire* lookback frame, with no regard
  for whether it happened before the opposing edge even started forming. A
  deep wick from ~2h earlier (an already-resolved prior swing) got paired
  with a live, currently-testing resistance from the last 15 minutes,
  producing an 18-point "range" instead of the ~8-point box actually
  forming. The fallback must only search bars at-or-after the opposing
  barrier's own first touch.
  """
  rows = [(101.0, 101.0, 90.0, 100.5, 100.0)]  # bar 0: ancient, single-touch wick
  for _ in range(14):  # bars 1-14: neutral filler, far from either level
    rows.append((101.0, 102.5, 100.5, 101.5, 100.0))
  rows.extend([  # bars 15-19: the live, currently-forming consolidation
    (100.0, 101.0, 97.8, 100.5, 100.0),   # support touch 1 (wick rejection)
    (100.5, 108.0, 100.0, 106.0, 100.0),  # resistance touch 1
    (106.0, 107.0, 97.9, 99.0, 100.0),    # support touch 2
    (99.0, 110.2, 98.5, 105.0, 100.0),    # resistance touch 2
    (105.0, 110.3, 99.0, 108.0, 100.0),   # resistance touch 3
  ])
  df = pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=pd.date_range("2026-08-03", periods=len(rows), freq="5min", tz="UTC"),
  ).astype(float)

  resistance = ScalpBarrier(
    side="resistance", level=110.0, low=109.5, high=110.5,
    touches=3, wick_rejections=2, accepted_closes=0, last_touch_index=19,
    tags=[], score=5.0, first_touch_index=15,
  )

  fallback = _fallback_barrier(
    "support", df, 0, df, 2.0, 0.3, 1.0, 103.0, [], [resistance], _cfg(),
  )

  assert fallback is not None
  # Must anchor on the recent ~98 low from the live consolidation, not the
  # ancient 90 wick from bar 0 (which predates the resistance entirely).
  assert fallback.level == pytest.approx(97.85, abs=0.1)
  assert fallback.level > 95.0


def test_adaptive_range_targets_ladder():
  assert select_range_target(48.8, targets=(70, 50, 40, 30, 20, 15), buffer_pips=3) == 40
  assert select_range_target(40.9, targets=(70, 50, 40, 30, 20, 15), buffer_pips=3) == 30
  assert select_range_target(26.0, targets=(70, 50, 40, 30, 20, 15), buffer_pips=3) == 20
  assert select_range_target(18.5, targets=(70, 50, 40, 30, 20, 15), buffer_pips=3) == 15
  assert select_range_target(10.0, targets=(70, 50, 40, 30, 20, 15), buffer_pips=3) is None


def test_strategy_aware_drift_caps_by_atr_and_room():
  limit, measured = max_entry_drift_pips(
    strategy="Range Edge Scalp",
    atr=2.0,
    pip_size=0.1,
    remaining_target_room_pips=40,
    cfg=execution_cfg(
      auto_trade_max_entry_distance_pips=10,
      auto_trade_range_max_entry_drift_atr=0.35,
    ),
  )
  assert limit <= 10
  assert measured["effective_pips"] == limit


def test_quality_tiers_and_risk_multipliers():
  assert classify_tier(confluence=3, strategy="Trend Pullback") == "A"
  assert classify_tier(
    confluence=2, strategy="Range Edge Scalp", one_sided=True,
  ) == "B"
  assert classify_tier(confluence=0, strategy="Fade Scalp") == "C"
  assert risk_multiplier_for_tier("B") == 1.0
  assert risk_multiplier_for_tier("A") == 1.0
  assert risk_multiplier_for_tier("C") == 1.0
  assert risk_multiplier_for_tier("A", post_impulse=True) == 0.5
  # Owner 2026-09-07: scalp books the same flat equity-table lot as any
  # other trade now (PR #486's equity_table sizing_mode default) - the
  # prior 1.5x here was a leftover from the old risk-percent sizing
  # formula and had started silently re-inflating every scalp position.
  assert risk_multiplier_for_tier("A", range_scalp=True) == 1.0
  assert risk_multiplier_for_tier("B", range_scalp=True) == 1.0
  assert risk_multiplier_for_tier("C", range_scalp=True) == 1.0


def test_evaluate_ignores_stale_tier_b_half_size_stamp():
  """ZoneWatch candidates can still hold pre-fix risk_multiplier=0.5."""
  from app.autotrade.execution_policy import evaluate_execution_policy

  stale = replace(
    _match("Trend Pullback", "BUY", 4270.0, 4272.0, confluence=2),
    tier="B",
    risk_multiplier=0.5,
    targets_pips=(30, 60, 90, 120, 200),
  )
  result = evaluate_execution_policy(
    stale,
    spot_price=4271.0,
    regime="trend",
    pip_size=0.1,
  )
  assert result.measured["stamped_risk_multiplier"] == 0.5
  assert result.measured["match_risk_multiplier"] == 1.0
  assert result.measured["effective_risk_multiplier"] == 1.0


def _match(
  strategy: str,
  direction: str,
  low: float,
  high: float,
  confluence: int = 3,
  *,
  family: str = "",
  event_ts: str = "100",
  targets: tuple[int, ...] = (30,),
):
  symbol = "XAU"
  tf = "M5"
  match_id = strategy_match_id(symbol, tf, event_ts, strategy, direction, low, high)
  return StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id=match_id,
    symbol=symbol,
    source_tf=tf,
    event_ts=event_ts,
    issued_at=1,
    expires_at=1000,
    strategy=strategy,
    strategy_mode="with_trend",
    direction=direction,
    key_level=(low + high) / 2,
    entry_low=low,
    entry_high=high,
    current_price=(low + high) / 2,
    confluence=confluence,
    reasons=(strategy,),
    atr=2.0,
    structure_swing=low if direction == "BUY" else high,
    targets_pips=targets,
    tier="A",
    family=family,
  )


def test_multi_match_dedupe_and_storage():
  a = _match("Trend Pullback", "BUY", 100, 101)
  duplicate = _match("Trend Pullback", "BUY", 100, 101, confluence=2)
  b = _match("Break & Retest", "BUY", 100.1, 101.1)
  c = _match("Fade Scalp", "SELL", 110, 111)
  kept, events = dedupe_matches([a, duplicate, b, c], atr=2.0)
  assert same_thesis(a, duplicate, atr=2.0)
  assert not same_thesis(a, b, atr=2.0)
  assert len(kept) == 3
  assert any(item["event"] == "replay_updated" for item in events)
  primary = select_primary(kept)
  assert primary is not None
  raw = serialize_matches(kept)
  restored = deserialize_matches(raw)
  assert len(restored) == 3


def test_multi_match_keeps_distinct_family_trigger_and_target_theses():
  base = _match(
    "Trend Pullback", "BUY", 100, 101, family="trend_pullback",
  )
  other_family = _match(
    "Break & Retest", "BUY", 100.1, 101.1, family="breakout_retest",
  )
  other_trigger = _match(
    "Trend Pullback",
    "BUY",
    100.1,
    101.1,
    family="trend_pullback",
    event_ts="101",
  )
  other_target = _match(
    "Trend Pullback",
    "BUY",
    100.1,
    101.1,
    family="trend_pullback",
    targets=(60,),
  )

  kept, _ = dedupe_matches(
    [base, other_family, other_trigger, other_target],
    atr=2.0,
  )

  assert len(kept) == 4


def test_narrow_wrapper_overlap_is_not_lost_to_absolute_price_floor():
  first_class = _match(
    "Supply Zone Reaction", "SELL", 4067.47, 4067.68, event_ts="100",
  )
  wrapper = _match(
    "Zone Reaction", "SELL", 4067.49, 4067.69, event_ts="100",
  )

  assert same_thesis(first_class, wrapper, atr=2.0)


def test_dedupe_keeps_fresh_geometry_and_recomputes_risk():
  old = replace(
    _match(
      "Supply Zone Reaction",
      "SELL",
      4067.47,
      4067.68,
      confluence=2,
      event_ts="100",
    ),
    tier="B",
    risk_multiplier=0.5,
    confirmation_bar_ts="100",
    structural_zone_id="supply-zone-1",
  )
  fresh = replace(
    _match(
      "Supply Zone Reaction",
      "SELL",
      4067.49,
      4067.69,
      confluence=2,
      event_ts="101",
    ),
    tier="B",
    risk_multiplier=0.5,
    confirmation_bar_ts="101",
    structural_zone_id="supply-zone-1",
  )

  kept, _ = dedupe_matches([old, fresh], atr=2.0)

  assert len(kept) == 1
  assert kept[0].strategy == "Supply Zone Reaction"
  assert kept[0].entry_low == fresh.entry_low
  assert kept[0].event_ts == fresh.event_ts
  assert kept[0].tier == "A"
  assert kept[0].risk_multiplier == 1.0


def test_same_match_replayed_five_times_never_inflates_confluence():
  original = _match(
    "Liquidity Sweep",
    "BUY",
    4100.0,
    4101.0,
    confluence=2,
    event_ts="100",
  )
  replays = [
    replace(
      original,
      event_ts=str(100 + index),
      confirmation_bar_ts=str(100 + index),
      current_price=4100.1 + index * 0.01,
    )
    for index in range(5)
  ]

  kept, events = dedupe_matches(replays, atr=2.0)

  assert len(kept) == 1
  assert kept[0].confluence == 2
  assert kept[0].event_ts == "104"
  assert kept[0].current_price == pytest.approx(4100.14)
  assert all(
    not tag.startswith("contributor:") for tag in kept[0].tags
  )
  assert sum(item["event"] == "replay_updated" for item in events) == 4


def test_staircase_downtrend_is_not_a_confirmed_range():
  rows = []
  price = 120.0
  for _ in range(20):
    rows.append((price, price + 1.0, price - 2.5, price - 2.0, 100))
    price -= 2.0
  df = pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=pd.date_range("2026-07-17", periods=len(rows), freq="5min", tz="UTC"),
  ).astype(float)
  atr = pd.Series([1.5] * len(df), index=df.index)
  detailed = build_scalp_structure_detailed(df, atr, [], [], None, _cfg())
  assert detailed.range_state in {
    RANGE_STATE_NO_RANGE,
    "broken_range",
    RANGE_STATE_PROVISIONAL,
    RANGE_STATE_POST_IMPULSE,
  }
  if detailed.scalp_range is not None:
    assert detailed.scalp_range.state != RANGE_STATE_CONFIRMED

"""M1 candlestick trigger (Codex Prompt P3, Part 2).

Each pattern is tested in isolation (m1_trigger_patterns restricted to just
that one pattern) so a match doesn't depend on ALL_PATTERNS check ordering -
this proves each detector's own qualifying logic, not just "something fired".
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.analysis.m1_trigger import (
  ALL_PATTERNS,
  evaluate_m1_trigger,
  evaluate_m1_trigger_window,
)


pytestmark = pytest.mark.no_database


ZONE_LOW = 4088.0
ZONE_HIGH = 4090.0
KEY_LEVEL = 4089.0


def _cfg(pattern: str, **overrides) -> SimpleNamespace:
  """Canonical-shaped ``runtime_config`` view for the M1 trigger tests.

  Production reads ``cfg.analysis.triggers.m1.<field>``. Overrides accept
  either the canonical trailing field name (``wick_fraction``,
  ``strong_close_pct``, ``patterns``) or the retired legacy attribute name
  (``m1_trigger_wick_fraction`` etc.) for backwards compatibility.
  """
  legacy_to_canonical = {
    "m1_trigger_patterns": "patterns",
    "m1_trigger_wick_fraction": "wick_fraction",
    "m1_trigger_strong_close_pct": "strong_close_pct",
  }
  values: dict[str, object] = {
    "patterns": pattern,
    "wick_fraction": 0.5,
    "strong_close_pct": 0.2,
  }
  for key, value in overrides.items():
    values[legacy_to_canonical.get(key, key)] = value
  return SimpleNamespace(
    analysis=SimpleNamespace(
      triggers=SimpleNamespace(
        m1=SimpleNamespace(**values),
      ),
    ),
  )


def _bars(rows: list[dict], *, prior: dict | None = None) -> pd.DataFrame:
  data = ([prior] if prior else []) + rows
  index = pd.date_range("2026-07-22 14:45", periods=len(data), freq="1min", tz="UTC")
  return pd.DataFrame(data, index=index)


@pytest.mark.parametrize("pattern", ALL_PATTERNS)
def test_all_six_patterns_are_enabled_by_default(pattern):
  # Sanity check for the config default before testing each in isolation.
  from app.core.config import runtime_config
  from tests.configuration.canonical_fixtures import leaf

  patterns = leaf(runtime_config, "m1_trigger_patterns")
  assert pattern in patterns


def test_wick_rejection_fires_and_non_qualifying_bar_does_not():
  qualifying = _bars([{
    "open": 4089.5, "high": 4090.5, "low": 4087.0, "close": 4090.3, "volume": 100.0,
  }])
  result = evaluate_m1_trigger(
    qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("wick_rejection"),
  )
  assert result is not None
  assert result.pattern == "wick_rejection"
  assert result.wick_extreme == 4087.0

  non_qualifying = _bars([{
    # Never touches the zone at all.
    "open": 4200.0, "high": 4200.5, "low": 4199.5, "close": 4200.2, "volume": 100.0,
  }])
  assert evaluate_m1_trigger(
    non_qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("wick_rejection"),
  ) is None


def test_wick_rejection_fires_for_sell_direction():
  qualifying = _bars([{
    "open": 4088.5, "high": 4091.0, "low": 4087.5, "close": 4087.7, "volume": 100.0,
  }])
  result = evaluate_m1_trigger(
    qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="SELL", cfg=_cfg("wick_rejection"),
  )
  assert result is not None
  assert result.pattern == "wick_rejection"
  assert result.wick_extreme == 4091.0


def test_body_close_fires_and_non_qualifying_bar_does_not():
  qualifying = _bars([{
    "open": 4088.5, "high": 4089.8, "low": 4088.3, "close": 4089.5, "volume": 100.0,
  }])
  result = evaluate_m1_trigger(
    qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("body_close"),
  )
  assert result is not None
  assert result.pattern == "body_close"

  non_qualifying = _bars([{
    "open": 4088.5, "high": 4088.9, "low": 4088.3, "close": 4088.6, "volume": 100.0,
  }])
  assert evaluate_m1_trigger(
    non_qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("body_close"),
  ) is None


def test_strong_close_fires_and_non_qualifying_bar_does_not():
  qualifying = _bars([{
    "open": 4088.5, "high": 4090.2, "low": 4087.0, "close": 4089.6, "volume": 100.0,
  }])
  result = evaluate_m1_trigger(
    qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("strong_close"),
  )
  assert result is not None
  assert result.pattern == "strong_close"

  non_qualifying = _bars([{
    "open": 4088.0, "high": 4089.0, "low": 4087.0, "close": 4088.3, "volume": 100.0,
  }])
  assert evaluate_m1_trigger(
    non_qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("strong_close"),
  ) is None


def test_pin_bar_fires_and_non_qualifying_bar_does_not():
  qualifying = _bars([{
    "open": 4089.0, "high": 4089.1, "low": 4087.0, "close": 4089.05, "volume": 100.0,
  }])
  result = evaluate_m1_trigger(
    qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("pin_bar"),
  )
  assert result is not None
  assert result.pattern == "pin_bar"

  non_qualifying = _bars([{
    # Fat body, no dominant wick.
    "open": 4088.0, "high": 4089.5, "low": 4087.5, "close": 4089.3, "volume": 100.0,
  }])
  assert evaluate_m1_trigger(
    non_qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("pin_bar"),
  ) is None


def test_engulfing_fires_and_non_qualifying_bar_does_not():
  prior = {"open": 4089.0, "high": 4089.2, "low": 4088.4, "close": 4088.5, "volume": 100.0}
  qualifying = _bars(
    [{"open": 4088.3, "high": 4089.5, "low": 4088.2, "close": 4089.3, "volume": 100.0}],
    prior=prior,
  )
  result = evaluate_m1_trigger(
    qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("engulfing"),
  )
  assert result is not None
  assert result.pattern == "engulfing"

  non_qualifying = _bars(
    [{"open": 4088.6, "high": 4088.8, "low": 4088.5, "close": 4088.7, "volume": 100.0}],
    prior=prior,
  )
  assert evaluate_m1_trigger(
    non_qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("engulfing"),
  ) is None

  # No prior bar at all: engulfing can never fire.
  no_prior = _bars(
    [{"open": 4088.3, "high": 4089.5, "low": 4088.2, "close": 4089.3, "volume": 100.0}],
  )
  assert evaluate_m1_trigger(
    no_prior, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("engulfing"),
  ) is None


def test_hammer_fires_and_non_qualifying_bar_does_not():
  qualifying = _bars([{
    "open": 4088.5, "high": 4088.7, "low": 4087.0, "close": 4088.6, "volume": 100.0,
  }])
  result = evaluate_m1_trigger(
    qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("hammer"),
  )
  assert result is not None
  assert result.pattern == "hammer"

  non_qualifying = _bars([{
    # Long upper wick instead of lower - a shooting star shape, not a hammer.
    "open": 4088.5, "high": 4090.5, "low": 4088.4, "close": 4088.6, "volume": 100.0,
  }])
  assert evaluate_m1_trigger(
    non_qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("hammer"),
  ) is None


def test_shooting_star_shape_fires_hammer_pattern_for_sell():
  qualifying = _bars([{
    "open": 4089.5, "high": 4091.0, "low": 4089.3, "close": 4089.4, "volume": 100.0,
  }])
  result = evaluate_m1_trigger(
    qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="SELL", cfg=_cfg("hammer"),
  )
  assert result is not None
  assert result.pattern == "hammer"
  assert result.wick_extreme == 4091.0


def test_disabled_pattern_never_fires_even_if_bar_qualifies():
  # This bar qualifies for hammer (touches zone, small body, dominant lower
  # wick) but its close (4088.6) never exceeds zone_high (4090.0), so it
  # cannot also qualify for wick_rejection - a genuinely disjoint case.
  qualifying_for_hammer_only = _bars([{
    "open": 4088.5, "high": 4088.7, "low": 4087.0, "close": 4088.6, "volume": 100.0,
  }])
  assert evaluate_m1_trigger(
    qualifying_for_hammer_only, zone_low=ZONE_LOW, zone_high=ZONE_HIGH,
    key_level=KEY_LEVEL, direction="BUY", cfg=_cfg("wick_rejection"),
  ) is None


def test_no_bars_or_empty_frame_returns_none():
  empty = pd.DataFrame(columns=["open", "high", "low", "close"])
  assert evaluate_m1_trigger(
    empty, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("wick_rejection"),
  ) is None
  assert evaluate_m1_trigger(
    None, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("wick_rejection"),
  ) is None


@pytest.mark.parametrize(
  ("pattern", "prior", "bar"),
  (
    (
      "wick_rejection",
      None,
      {"open": 4089.5, "high": 4090.5, "low": 4087.0, "close": 4090.3},
    ),
    (
      "body_close",
      None,
      {"open": 4088.5, "high": 4089.8, "low": 4088.3, "close": 4089.5},
    ),
    (
      "strong_close",
      None,
      {"open": 4088.5, "high": 4090.2, "low": 4087.0, "close": 4089.6},
    ),
    (
      "pin_bar",
      None,
      {"open": 4089.0, "high": 4089.1, "low": 4087.0, "close": 4089.05},
    ),
    (
      "engulfing",
      {"open": 4089.0, "high": 4089.2, "low": 4088.4, "close": 4088.5},
      {"open": 4088.3, "high": 4089.5, "low": 4088.2, "close": 4089.3},
    ),
    (
      "hammer",
      None,
      {"open": 4088.5, "high": 4088.7, "low": 4087.0, "close": 4088.6},
    ),
  ),
)
def test_every_pattern_has_one_common_zone_intersection_gate(pattern, prior, bar):
  def shifted(row):
    return {
      key: value + 100.0 if key in {"open", "high", "low", "close"} else value
      for key, value in {**row, "volume": 100.0}.items()
    }

  outside = _bars(
    [shifted(bar)],
    prior=None if prior is None else shifted(prior),
  )

  assert evaluate_m1_trigger(
    outside,
    zone_low=ZONE_LOW,
    zone_high=ZONE_HIGH,
    key_level=KEY_LEVEL,
    direction="BUY",
    cfg=_cfg(pattern),
  ) is None


def test_windowed_evaluator_recovers_earlier_trigger_before_later_doji():
  bars = _bars([
    {
      "open": 4089.5,
      "high": 4090.5,
      "low": 4087.0,
      "close": 4090.3,
      "volume": 100.0,
    },
    {
      "open": 4089.0,
      "high": 4089.05,
      "low": 4088.95,
      "close": 4089.0,
      "volume": 100.0,
    },
  ])
  earliest = int(bars.index[0].timestamp())

  result = evaluate_m1_trigger_window(
    bars,
    zone_low=ZONE_LOW,
    zone_high=ZONE_HIGH,
    key_level=KEY_LEVEL,
    direction="BUY",
    earliest_bar_ts=earliest,
    after_bar_ts=None,
    cfg=_cfg("wick_rejection"),
  )

  assert result is not None
  assert result.pattern == "wick_rejection"
  assert int(result.bar_ts) == earliest


def test_windowed_evaluator_never_uses_explicitly_open_bar():
  bars = _bars([
    {
      "open": 4089.5,
      "high": 4090.5,
      "low": 4087.0,
      "close": 4090.3,
      "volume": 100.0,
      "closed": False,
    },
  ])

  assert evaluate_m1_trigger_window(
    bars,
    zone_low=ZONE_LOW,
    zone_high=ZONE_HIGH,
    key_level=KEY_LEVEL,
    direction="BUY",
    earliest_bar_ts=int(bars.index[0].timestamp()),
    after_bar_ts=None,
    cfg=_cfg("wick_rejection"),
  ) is None


def test_windowed_evaluator_ignores_pre_episode_and_consumed_bars():
  bars = _bars([
    {
      "open": 4089.5,
      "high": 4090.5,
      "low": 4087.0,
      "close": 4090.3,
      "volume": 100.0,
    },
    {
      "open": 4089.0,
      "high": 4089.05,
      "low": 4088.95,
      "close": 4089.0,
      "volume": 100.0,
    },
  ])
  first_ts = int(bars.index[0].timestamp())

  assert evaluate_m1_trigger_window(
    bars,
    zone_low=ZONE_LOW,
    zone_high=ZONE_HIGH,
    key_level=KEY_LEVEL,
    direction="BUY",
    earliest_bar_ts=first_ts + 60,
    after_bar_ts=None,
    cfg=_cfg("wick_rejection"),
  ) is None
  assert evaluate_m1_trigger_window(
    bars,
    zone_low=ZONE_LOW,
    zone_high=ZONE_HIGH,
    key_level=KEY_LEVEL,
    direction="BUY",
    earliest_bar_ts=first_ts,
    after_bar_ts=first_ts,
    cfg=_cfg("wick_rejection"),
  ) is None


def test_windowed_engulfing_does_not_use_prior_bar_across_episode_boundary():
  prior = {
    "open": 4089.0,
    "high": 4089.2,
    "low": 4088.4,
    "close": 4088.5,
    "volume": 100.0,
  }
  current = {
    "open": 4088.3,
    "high": 4089.5,
    "low": 4088.2,
    "close": 4089.3,
    "volume": 100.0,
  }
  bars = _bars([current], prior=prior)
  episode_start = int(bars.index[1].timestamp())

  assert evaluate_m1_trigger_window(
    bars,
    zone_low=ZONE_LOW,
    zone_high=ZONE_HIGH,
    key_level=KEY_LEVEL,
    direction="BUY",
    earliest_bar_ts=episode_start,
    after_bar_ts=None,
    cfg=_cfg("engulfing"),
  ) is None


def test_strong_close_buy_near_high_but_below_zone_mid_does_not_qualify():
  """Near own high is insufficient when close stays below zone midpoint."""
  bars = _bars([{
    "open": 4088.0, "high": 4089.0, "low": 4087.0, "close": 4088.85, "volume": 100.0,
  }])
  assert evaluate_m1_trigger(
    bars, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("strong_close"),
  ) is None


def test_strong_close_sell_near_low_but_above_zone_mid_does_not_qualify():
  bars = _bars([{
    "open": 4089.5, "high": 4090.5, "low": 4089.0, "close": 4089.2, "volume": 100.0,
  }])
  assert evaluate_m1_trigger(
    bars, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="SELL", cfg=_cfg("strong_close"),
  ) is None


def test_strong_close_buy_reclaim_at_or_above_zone_mid_qualifies():
  bars = _bars([{
    "open": 4088.5, "high": 4090.2, "low": 4087.0, "close": 4089.6, "volume": 100.0,
  }])
  result = evaluate_m1_trigger(
    bars, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("strong_close"),
  )
  assert result is not None
  assert result.pattern == "strong_close"


def test_strong_close_sell_reclaim_at_or_below_zone_mid_qualifies():
  bars = _bars([{
    "open": 4089.0, "high": 4090.0, "low": 4088.0, "close": 4088.3, "volume": 100.0,
  }])
  result = evaluate_m1_trigger(
    bars, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="SELL", cfg=_cfg("strong_close"),
  )
  assert result is not None
  assert result.pattern == "strong_close"


def test_wick_rejection_closes_away_from_zone():
  buy_bar = _bars([{
    "open": 4089.5, "high": 4090.5, "low": 4087.0, "close": 4090.3, "volume": 100.0,
  }])
  buy = evaluate_m1_trigger(
    buy_bar, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("wick_rejection"),
  )
  assert buy is not None
  assert float(buy_bar.iloc[-1]["close"]) > ZONE_HIGH

  sell_bar = _bars([{
    "open": 4088.5, "high": 4091.0, "low": 4087.5, "close": 4087.7, "volume": 100.0,
  }])
  sell = evaluate_m1_trigger(
    sell_bar, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="SELL", cfg=_cfg("wick_rejection"),
  )
  assert sell is not None
  assert float(sell_bar.iloc[-1]["close"]) < ZONE_LOW


def test_strong_close_buy_through_zone_low_does_not_qualify():
  """Bar intersects zone wick but closes below invalidating edge."""
  bars = _bars([{
    "open": 4088.5, "high": 4089.0, "low": 4087.0, "close": 4087.5, "volume": 100.0,
  }])
  assert evaluate_m1_trigger(
    bars, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("strong_close"),
  ) is None


def test_strong_close_sell_through_zone_high_does_not_qualify():
  bars = _bars([{
    "open": 4089.0, "high": 4091.0, "low": 4088.5, "close": 4090.5, "volume": 100.0,
  }])
  assert evaluate_m1_trigger(
    bars, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="SELL", cfg=_cfg("strong_close"),
  ) is None


def test_evaluate_m1_trigger_ignores_open_bar():
  bars = _bars([{
    "open": 4089.5,
    "high": 4090.5,
    "low": 4087.0,
    "close": 4090.3,
    "volume": 100.0,
    "closed": False,
  }])
  assert evaluate_m1_trigger(
    bars,
    zone_low=ZONE_LOW,
    zone_high=ZONE_HIGH,
    key_level=KEY_LEVEL,
    direction="BUY",
    cfg=_cfg("wick_rejection"),
  ) is None


# --- Candle Confirmation V2 compatibility (§30) ---------------------------
# The existing first-match `.pattern` decision is untouched; `.evidence` is
# a purely additive field that is None unless the caller supplies `atr`.


def test_evidence_field_has_no_atr_normalized_data_without_atr():
  # Fraction-based labels (wick/close-location) don't require ATR, so
  # `.evidence` can still be populated with atr defaulted to 0 - but every
  # ATR-normalized reading on it stays None/unset rather than dividing by
  # a missing ATR.
  qualifying = _bars([{
    "open": 4089.5, "high": 4090.5, "low": 4087.0, "close": 4090.3, "volume": 100.0,
  }])
  result = evaluate_m1_trigger(
    qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("wick_rejection"),
  )
  assert result is not None
  assert result.pattern == "wick_rejection"
  assert result.evidence is not None
  assert result.evidence.rejection is not None
  assert result.evidence.rejection.sweep_penetration_atr is None
  assert result.evidence.rejection.reclaim_depth_atr is None


def test_evidence_field_is_populated_when_atr_supplied_and_never_changes_pattern():
  qualifying = _bars([{
    "open": 4089.5, "high": 4090.5, "low": 4087.0, "close": 4090.3, "volume": 100.0,
  }])
  result = evaluate_m1_trigger(
    qualifying, zone_low=ZONE_LOW, zone_high=ZONE_HIGH, key_level=KEY_LEVEL,
    direction="BUY", cfg=_cfg("wick_rejection"), atr=1.0,
  )
  assert result is not None
  assert result.pattern == "wick_rejection"
  assert result.evidence is not None
  assert "wick_rejection" in result.evidence.all_patterns
  assert 0.0 <= result.evidence.final_score <= 1.0


def test_windowed_evaluator_carries_evidence_through():
  bars = _bars([{
    "open": 4089.5, "high": 4090.5, "low": 4087.0, "close": 4090.3, "volume": 100.0,
  }])
  result = evaluate_m1_trigger_window(
    bars,
    zone_low=ZONE_LOW,
    zone_high=ZONE_HIGH,
    key_level=KEY_LEVEL,
    direction="BUY",
    earliest_bar_ts=int(bars.index[0].value // 1_000_000_000),
    after_bar_ts=None,
    cfg=_cfg("wick_rejection"),
    atr=1.0,
  )
  assert result is not None
  assert result.evidence is not None

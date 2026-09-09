"""MAD-2 observe-only phase × session expectancy (not in CI allowlist)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.scalping.mad_replay import (
  mad_expectancy_report,
  replay_lab_event_with_mad,
  replay_mad_fixture,
  resolve_event_mad_phase,
)
from app.scalping.replay_lab import LabEvent


pytestmark = pytest.mark.no_database

FIXTURE = Path(__file__).parent / "fixtures" / "mad_lab_events.jsonl"


def test_resolve_phase_from_stamp_only():
  stamped = LabEvent.from_dict({
    "timestamp": 1,
    "direction": "BUY",
    "price": 4050.5,
    "atr": 5.0,
    "range_low": 4048.0,
    "range_high": 4060.0,
    "strategy": "liquidity_sweep_reversal",
    "liquidity_level": 4050.0,
    "bar": {"open": 4050.2, "high": 4051.0, "low": 4049.5, "close": 4050.6},
    "bars_after": [],
    "measured": {"mad_phase": "manip"},
  })
  assert resolve_event_mad_phase(stamped) == "manip"

  nested = LabEvent.from_dict({
    **{
      "timestamp": 2,
      "direction": "BUY",
      "price": 4050.5,
      "atr": 5.0,
      "range_low": 4048.0,
      "range_high": 4060.0,
      "strategy": "liquidity_sweep_reversal",
      "liquidity_level": 4050.0,
      "bar": {"open": 4050.2, "high": 4051.0, "low": 4049.5, "close": 4050.6},
      "bars_after": [],
      "measured": {"mad": {"phase": "expand"}},
    },
  })
  assert resolve_event_mad_phase(nested) == "expand"

  missing = LabEvent.from_dict({
    "timestamp": 3,
    "direction": "BUY",
    "price": 4050.5,
    "atr": 5.0,
    "range_low": 4048.0,
    "range_high": 4060.0,
    "strategy": "liquidity_sweep_reversal",
    "liquidity_level": 4050.0,
    "bar": {"open": 4050.2, "high": 4051.0, "low": 4049.5, "close": 4050.6},
    "bars_after": [],
  })
  assert resolve_event_mad_phase(missing) == "unclear"


def test_mad_counterfactual_impulse_blocked_on_accum_kept_on_expand():
  accum_impulse = LabEvent.from_dict({
    "timestamp": 10,
    "direction": "BUY",
    "price": 4057.0,
    "atr": 5.0,
    "range_low": 4048.0,
    "range_high": 4070.0,
    "strategy": "impulse_pullback_continuation",
    "impulse_origin": 4050.0,
    "impulse_extreme": 4060.0,
    "barrier": 4070.0,
    "bar": {"open": 4056.5, "high": 4057.5, "low": 4056.0, "close": 4057.0},
    "continuation_trigger": True,
    "target_min_price": 1.0,
    "stop_price": 4054.0,
    "target_price": 4062.0,
    "session": "asia",
    "measured": {"mad_phase": "accum"},
    "bars_after": [
      {"open": 4057.0, "high": 4062.5, "low": 4056.8, "close": 4062.0},
    ],
  })
  row = replay_lab_event_with_mad(accum_impulse)
  assert row["gate_allowed"] is True
  assert row["mad_would_block"] is True
  assert row["mad_filtered"] is True
  assert row["mad_kept"] is False

  expand_impulse = LabEvent.from_dict({
    **{
      "timestamp": 11,
      "direction": "BUY",
      "price": 4057.0,
      "atr": 5.0,
      "range_low": 4048.0,
      "range_high": 4070.0,
      "strategy": "impulse_pullback_continuation",
      "impulse_origin": 4050.0,
      "impulse_extreme": 4060.0,
      "barrier": 4070.0,
      "bar": {"open": 4056.5, "high": 4057.5, "low": 4056.0, "close": 4057.0},
      "continuation_trigger": True,
      "target_min_price": 1.0,
      "stop_price": 4054.0,
      "target_price": 4062.0,
      "session": "london",
      "measured": {"mad_phase": "expand"},
      "bars_after": [
        {"open": 4057.0, "high": 4062.5, "low": 4056.8, "close": 4062.0},
      ],
    },
  })
  kept = replay_lab_event_with_mad(expand_impulse)
  assert kept["mad_would_block"] is False
  assert kept["mad_kept"] is True


def test_mad_counterfactual_range_blocked_on_expand():
  event = LabEvent.from_dict({
    "timestamp": 20,
    "direction": "BUY",
    "price": 4050.5,
    "atr": 5.0,
    "range_low": 4048.0,
    "range_high": 4060.0,
    "strategy": "liquidity_sweep_reversal",
    "liquidity_level": 4050.0,
    "barrier": 4058.0,
    "bar": {"open": 4050.2, "high": 4051.0, "low": 4049.5, "close": 4050.6},
    "target_min_price": 1.0,
    "stop_price": 4048.0,
    "target_price": 4054.0,
    "session": "london",
    "measured": {"mad_phase": "expand"},
    "bars_after": [
      {"open": 4050.6, "high": 4054.2, "low": 4050.4, "close": 4054.0},
    ],
  })
  row = replay_lab_event_with_mad(event)
  assert row["mad_would_block"] is True
  assert row["mad_gate_reason"] == "mad_gate_reversal_avoid_expand"


def test_mad_fixture_report_has_phase_session_table_and_discipline():
  report = replay_mad_fixture(FIXTURE)
  assert report["version"] == "mad-2"
  assert report["discipline"]["mode"] == "observe_only_counterfactual"
  assert report["discipline"]["live_publish"] == "unchanged"
  assert report["discipline"]["rule"] == "never_tune_thresholds_on_holdout"
  assert report["summary"]["n_events"] == 5
  # Impulse on accum filtered; range on expand filtered; unclear stays neutral.
  assert report["summary"]["n_mad_filtered"] >= 2
  assert "range_family" in report["strategy_baselines"]
  assert "impulse_family" in report["strategy_baselines"]
  assert any(
    row["phase"] == "accum" and row["session"] == "asia"
    for row in report["by_phase_session_strategy"]
  )
  assert "holdout" in report["splits"]
  assert "development" in report["splits"]


def test_mad_expectancy_report_empty_safe():
  report = mad_expectancy_report([])
  assert report["summary"]["n_events"] == 0
  assert report["by_phase_session_strategy"] == []
  assert report["by_affinity_confidence"] == []
  assert report["alignment_comparison"]["aligned"]["trade_count"] == 0


def _manip_snapshot(*, sweep_side: str, confidence: float) -> dict:
  return {
    "phase": "manip",
    "range_quality_atr": None,
    "price_vs_asia": "above" if sweep_side == "high" else "below",
    "sweep_side": sweep_side,
    "reclaim": True,
    "reason_code": "asia_sweep_reclaim",
    "measured": {},
    "asia": None,
    "manipulation_direction": "SELL" if sweep_side == "high" else "BUY",
    "expansion_direction": None,
    "confidence": confidence,
    "mad_version": 2,
  }


def test_v2_affinity_bucket_aligned_vs_opposed():
  # High-sweep manipulation is SELL-aligned. A SELL trade sees it aligned;
  # a BUY trade (wrong direction for this manipulation) sees it opposed.
  aligned_event = LabEvent.from_dict({
    "timestamp": 30,
    "direction": "SELL",
    "price": 4050.5,
    "atr": 5.0,
    "range_low": 4048.0,
    "range_high": 4060.0,
    "strategy": "liquidity_sweep_reversal",
    "liquidity_level": 4050.0,
    "barrier": 4040.0,
    "bar": {"open": 4050.2, "high": 4051.0, "low": 4049.5, "close": 4050.6},
    "target_min_price": 1.0,
    "measured": {"mad": _manip_snapshot(sweep_side="high", confidence=0.9)},
    "bars_after": [],
  })
  opposed_event = LabEvent.from_dict({
    "timestamp": 31,
    "direction": "BUY",
    "price": 4050.5,
    "atr": 5.0,
    "range_low": 4048.0,
    "range_high": 4060.0,
    "strategy": "liquidity_sweep_reversal",
    "liquidity_level": 4050.0,
    "barrier": 4060.0,
    "bar": {"open": 4050.2, "high": 4051.0, "low": 4049.5, "close": 4050.6},
    "target_min_price": 1.0,
    "measured": {"mad": _manip_snapshot(sweep_side="high", confidence=0.9)},
    "bars_after": [],
  })
  aligned_row = replay_lab_event_with_mad(aligned_event)
  opposed_row = replay_lab_event_with_mad(opposed_event)
  assert aligned_row["mad_affinity_bucket"] == "aligned"
  assert aligned_row["mad_affinity"] > 0.0
  assert opposed_row["mad_affinity_bucket"] == "opposed"
  assert opposed_row["mad_affinity"] == 0.0
  assert aligned_row["mad_confidence_bucket"] == "high"


def test_v2_confidence_bucket_scales_low_to_high():
  low_event = LabEvent.from_dict({
    "timestamp": 40,
    "direction": "SELL",
    "price": 4050.5,
    "atr": 5.0,
    "range_low": 4048.0,
    "range_high": 4060.0,
    "strategy": "liquidity_sweep_reversal",
    "liquidity_level": 4050.0,
    "bar": {"open": 4050.2, "high": 4051.0, "low": 4049.5, "close": 4050.6},
    "measured": {"mad": _manip_snapshot(sweep_side="high", confidence=0.1)},
    "bars_after": [],
  })
  row = replay_lab_event_with_mad(low_event)
  assert row["mad_confidence_bucket"] == "low"


def test_alignment_comparison_and_affinity_confidence_table_from_fixture():
  report = replay_mad_fixture(FIXTURE)
  comparison = report["alignment_comparison"]
  assert set(comparison) == {"aligned", "neutral", "opposed"}
  for bucket_metrics in comparison.values():
    assert "trade_count" in bucket_metrics
    assert "expectancy_r" in bucket_metrics
    assert "profit_factor" in bucket_metrics
    assert "median_r" in bucket_metrics
  # Legacy fixture rows only stamp a bare phase (no direction) -> neutral.
  assert comparison["neutral"]["trade_count"] >= 1
  table = report["by_affinity_confidence"]
  assert isinstance(table, list)
  for row in table:
    assert row["affinity_bucket"] in {"aligned", "neutral", "opposed"}
    assert row["confidence_bucket"] in {"low", "medium", "high"}

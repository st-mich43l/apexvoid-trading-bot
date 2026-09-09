"""Candle Confirmation V2 — orchestrator (§16-§18/§49)."""

from __future__ import annotations

import pytest

from app.analysis.candle_evidence import (
  CANDLE_CONFIRMATION_VERSION,
  CandleEvidence,
  evaluate_all_candle_evidence,
)


def _hammer_bar():
  return {"open": 100.0, "high": 100.2, "low": 98.0, "close": 100.1}


def test_hammer_bar_produces_deduplicated_patterns_and_bounded_score():
  ev = evaluate_all_candle_evidence(
    [_hammer_bar()], direction="BUY", atr=1.0, level=99.0,
    zone_low=98.5, zone_high=100.5,
  )
  assert ev is not None
  assert isinstance(ev, CandleEvidence)
  assert ev.version == CANDLE_CONFIRMATION_VERSION == 2
  assert "hammer" in ev.all_patterns
  assert "pin_bar" in ev.all_patterns
  assert "wick_rejection" in ev.all_patterns
  # each label appears exactly once — no triple-counting a single geometry
  assert len(ev.all_patterns) == len(set(ev.all_patterns))
  assert 0.0 <= ev.base_score <= 1.0
  assert 0.0 <= ev.final_score <= 1.0
  assert ev.final_score >= ev.base_score
  assert ev.primary_pattern in ev.all_patterns


def test_final_score_never_exceeds_one_even_with_synergy():
  ev = evaluate_all_candle_evidence(
    [_hammer_bar()], direction="BUY", atr=1.0, level=99.0,
    zone_low=98.5, zone_high=100.5,
  )
  assert ev is not None
  assert ev.final_score <= 1.0
  assert ev.synergy_bonus <= 0.12 + 1e-9


def test_base_score_is_active_weight_normalized_when_only_rejection_fires():
  # A tiny-body wick bar whose BODY DIRECTION is bearish (open > close):
  # this clears the rejection wick threshold on its lower wick, but
  # displacement's strong_close/body_close/displacement_candle labels all
  # gate on the candle being directionally BULLISH for a BUY check, so
  # displacement never fires even though the wick alone would satisfy its
  # ATR-based range floor.
  # level == close so the (direction-independent) reclaim check in
  # BOTH families reads exactly 0 (not > 0) and stays inert either way.
  bar = {"open": 100.02, "high": 100.05, "low": 98.0, "close": 100.0}
  ev = evaluate_all_candle_evidence(
    [bar], direction="BUY", atr=1.0, level=100.0, zone_low=98.5, zone_high=100.5,
  )
  assert ev is not None
  assert ev.rejection is not None
  assert ev.displacement is None
  assert ev.sequence is None
  assert ev.base_score == pytest.approx(ev.rejection.score, rel=1e-9)


def test_returns_none_when_no_family_produces_evidence():
  # Flat-bodied (doji) bar, well outside the rejection zone, with the
  # level set beyond the close so displacement's level-independent
  # strong_reclaim label can't fire either — no family has anything to say.
  bar = {"open": 100.0, "high": 100.05, "low": 99.95, "close": 100.0}
  ev = evaluate_all_candle_evidence(
    [bar], direction="BUY", atr=1.0, level=200.0, zone_low=500.0, zone_high=510.0,
  )
  assert ev is None


def test_returns_none_for_invalid_direction_or_empty_bars():
  assert evaluate_all_candle_evidence(
    [_hammer_bar()], direction="SIDEWAYS", atr=1.0, level=99.0,
  ) is None
  assert evaluate_all_candle_evidence(
    [], direction="BUY", atr=1.0, level=99.0,
  ) is None


def test_sequence_evidence_merges_across_window_sizes():
  bars = [
    {"open": 101.0, "high": 101.05, "low": 99.95, "close": 100.0},
    {"open": 100.0, "high": 100.2, "low": 99.9, "close": 100.05},
    {"open": 100.05, "high": 100.85, "low": 100.0, "close": 100.8},
  ]
  ev = evaluate_all_candle_evidence(bars, direction="BUY", atr=1.0, level=99.0)
  assert ev is not None
  assert ev.sequence is not None
  assert "morning_star" in ev.sequence.patterns


def test_to_dict_serializes_every_top_level_field():
  ev = evaluate_all_candle_evidence(
    [_hammer_bar()], direction="BUY", atr=1.0, level=99.0,
    zone_low=98.5, zone_high=100.5,
  )
  assert ev is not None
  payload = ev.to_dict()
  for key in (
    "version", "direction", "rejection", "displacement", "sequence",
    "indecision", "base_score", "synergy_bonus", "final_score",
    "primary_pattern", "all_patterns",
  ):
    assert key in payload
  assert payload["rejection"]["patterns"]
  assert isinstance(payload["all_patterns"], list)


def test_causal_only_last_bar_is_the_one_under_evaluation():
  # Only the LAST bar's geometry should drive rejection/displacement —
  # prepending an unrelated earlier bar must not change which bar is judged.
  hammer = _hammer_bar()
  unrelated_earlier_bar = {"open": 50.0, "high": 50.5, "low": 49.5, "close": 50.1}
  ev_single = evaluate_all_candle_evidence(
    [hammer], direction="BUY", atr=1.0, level=99.0, zone_low=98.5, zone_high=100.5,
  )
  ev_with_history = evaluate_all_candle_evidence(
    [unrelated_earlier_bar, hammer], direction="BUY", atr=1.0, level=99.0,
    zone_low=98.5, zone_high=100.5,
  )
  assert ev_single is not None and ev_with_history is not None
  assert ev_single.rejection.score == pytest.approx(ev_with_history.rejection.score, rel=1e-9)

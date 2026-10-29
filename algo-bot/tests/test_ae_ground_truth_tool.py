"""The labels that score the Analysis Engine must themselves be right: constructed candles with a known answer."""

from __future__ import annotations

import pytest

from tools import ae_ground_truth as gt

pytestmark = pytest.mark.no_database

STEP = 900


def series(rows):
  """rows: (open, high, low, close); a flat 14-bar warm-up of range 1.0 makes ATR(14) exactly 1.0."""
  warm = [(100.0, 100.5, 99.5, 100.0)] * 15
  candles = [[1_000_000 + i * STEP, o, h, l, c, 1.0] for i, (o, h, l, c) in enumerate(warm + list(rows))]
  return gt.Series(candles)


def test_atr_of_a_constant_range_is_that_range():
  s = series([])
  assert s.atr[-1] == pytest.approx(1.0)


def test_a_five_bar_extreme_that_reverses_two_atr_is_a_swing_high():
  flat = [(100.0, 100.5, 99.5, 100.0)] * 5
  peak = [(100.0, 103.0, 100.0, 102.0)]
  fall = [(101.0, 101.0, 99.0, 99.5)] * 6
  s = series(flat + peak + fall)
  highs = [x for x in gt.oracle_swings(s) if x["kind"] == "high"]
  assert [x["i"] for x in highs] == [20]
  assert highs[0]["price"] == 103.0 and highs[0]["reversal_atr"] >= 1.5


def test_a_tied_extreme_is_not_a_swing():
  flat = [(100.0, 100.5, 99.5, 100.0)] * 5
  twin = [(100.0, 103.0, 100.0, 102.0), (102.0, 103.0, 100.0, 101.0)]
  s = series(flat + twin + [(101.0, 101.0, 99.0, 99.5)] * 6)
  assert [x for x in gt.oracle_swings(s) if x["kind"] == "high"] == []


def test_fair_value_gap_both_sides_and_its_liveness():
  bull = [(100, 100.5, 99.5, 100), (100, 101.5, 100, 101.4), (101.4, 103.0, 101.2, 102.8)]  # low[i] 101.2 > high[i-2] 100.5
  s = series(bull + [(102.8, 103, 102.5, 102.9)] * 2)
  gap = next(g for g in gt.oracle_gaps(s) if g["i"] == 17)
  assert gap["side"] == "demand" and gap["live"] and (gap["low"], gap["high"]) == (100.5, 101.2)
  closed_through = series(bull + [(102.8, 102.9, 99.0, 100.0)])
  assert not next(g for g in gt.oracle_gaps(closed_through) if g["i"] == 17)["live"]
  bear = [(100, 100.5, 99.5, 100), (100, 100, 98.5, 98.6), (98.6, 98.8, 97.0, 97.2)]
  gap = gt.oracle_gaps(series(bear + [(97.2, 97.5, 97.0, 97.3)]))
  assert gap and gap[0]["side"] == "supply" and (gap[0]["low"], gap[0]["high"]) == (98.8, 99.5)


def test_impulse_origin_is_the_last_opposite_candle_before_a_two_atr_run():
  rows = [(100.0, 100.4, 99.6, 99.8)] + [(99.8, 101.5, 99.8, 101.4), (101.4, 102.9, 101.4, 102.8)]  # 100 -> 102.8 = 3 ATR
  s = series(rows + [(102.8, 103.0, 102.6, 102.9)] * 2)
  origins = gt.oracle_impulses(s)
  assert origins and origins[0]["side"] == "demand" and origins[0]["i"] == 15 and origins[0]["live"]


def test_unreclaimed_breach_forgives_a_reclaimed_sweep_and_not_a_departure():
  base = [(100, 100.5, 99.5, 100)] * 3
  sweep = series(base + [(100, 100, 96.5, 97.0), (97.0, 100.6, 96.8, 100.5)] + [(100.5, 101, 100, 100.6)] * 6)
  left = series(base + [(100, 100, 96.5, 97.0)] + [(97.0, 97.5, 96.0, 96.5)] * 6)
  assert not gt.unreclaimed_breach(sweep, 16, "demand", 99.0, 1.0, 1.0)
  assert gt.unreclaimed_breach(left, 16, "demand", 99.0, 1.0, 1.0)
  assert not gt.unreclaimed_breach(left, 16, "demand", 99.0, 5.0, 1.0), "a margin the price never reached"


def test_the_committed_results_state_their_definitions_and_cover_every_symbol():
  import json
  from pathlib import Path

  results = json.loads((Path(gt.REPO) / "algo-bot/tools/data/ae-ground-truth-results.json").read_text())
  assert set(results["definitions"]) == {"swing", "fvg", "impulse_origin", "reaction"}
  assert len(results["captures"]) == 5 and results["checkpoints_frames_scored"] > 100

"""The F-7 investigation tooling must itself be right: constructed inputs with a known answer.

The conclusions of `git show f02d62f:docs/architecture/f7-opposing-barrier-ground-truth.md` rest on four small pieces of logic: the causal
triple-barrier race, the bar a production timestamp maps to, the union length behind the gap measure and the classification of where
two books first part ways. Each is checked here against hand-computed cases.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "analysis-engine" / "test" / "f7barrier"
sys.path.insert(0, str(TOOLS))

import analyze  # noqa: E402
import anatomy  # noqa: E402
import extract_events  # noqa: E402
import simulate  # noqa: E402

pytestmark = pytest.mark.no_database


def bars(*rows):
  return [(i * 300, row) for i, row in enumerate(rows)]  # (open, high, low, close)


def test_a_clean_long_wins_one_r_and_stays_alive_for_two_r():
  out = simulate.race("BUY", 100.0, 1.0, bars((100, 100.4, 99.8, 100.2), (100.2, 101.2, 100.1, 101.0), (101.0, 101.5, 100.9, 101.4)), (1.0, 2.0))
  assert out[1.0] == ("win", 1.0)
  assert out[2.0][0] == "censored"


def test_a_bar_touching_both_stop_and_target_counts_as_the_stop():
  out = simulate.race("BUY", 100.0, 1.0, bars((100, 101.5, 98.9, 100.0)), (1.0, 2.0))
  assert out[1.0] == ("loss", -1.0) and out[2.0] == ("loss", -1.0)


def test_the_short_side_is_the_mirror_of_the_long_side():
  out = simulate.race("SELL", 100.0, 1.0, bars((100, 100.2, 98.8, 99.0), (99.0, 99.1, 97.9, 98.0)), (1.0, 2.0))
  assert out[1.0] == ("win", 1.0) and out[2.0] == ("win", 2.0)


def test_an_unresolved_race_is_censored_at_the_last_close_in_r():
  out = simulate.race("BUY", 100.0, 2.0, bars((100, 100.5, 99.5, 100.0), (100.0, 100.6, 99.6, 100.5)), (1.0,))
  assert out[1.0][0] == "censored" and out[1.0][1] == pytest.approx(0.25)


def test_the_stop_is_checked_before_the_target_on_the_same_bar_for_each_target_independently():
  # +1R is reached on bar 1 (a win at 1R) but the same bar's low then never hits the stop; +2R is reached on bar 2.
  out = simulate.race("BUY", 100.0, 1.0, bars((100, 101.1, 99.9, 101.0), (101.0, 102.2, 100.9, 102.0)), (1.0, 2.0))
  assert out[1.0] == ("win", 1.0) and out[2.0] == ("win", 2.0)


def test_a_timestamp_maps_to_the_last_closed_m5_bar():
  # the bar that opened at T closes at T+300: at T+300 itself it is the last closed bar, one second earlier it is not
  assert extract_events.bar_open(1_791_459_900) == 1_791_459_600
  assert extract_events.bar_open(1_791_459_899) == 1_791_459_300


def test_union_length_counts_overlapping_spans_once():
  assert anatomy.union_length([(0.0, 2.0), (1.0, 3.0), (5.0, 6.0)]) == pytest.approx(4.0)
  assert anatomy.union_length([]) == 0.0


def decision(allowed, reason, barrier=None):
  return {"allowed": allowed, "reason": reason, "barrier": barrier}


def test_divergence_names_an_absent_python_barrier():
  go_barrier = {"side": "sell", "low": 10.0, "high": 11.0, "tier": "zone", "score": 1.0, "touches": 2}
  r = {"symbol": "XAU", "bar": 1, "c": decision(False, "opposing_entry_overlap", go_barrier), "a": decision(True, "no_opposing_barrier")}
  rows = {("XAU", 1): {"barriers_a": [{"side": "sell", "low": 50.0, "high": 51.0}]}}
  assert analyze.divergence(r, {"planned_entry": 10.5}, "a", rows) == "barrier_absent_in_python_book"


def test_divergence_names_a_narrower_python_band_that_leaves_the_entry_outside():
  go_barrier = {"side": "sell", "low": 10.0, "high": 12.0, "tier": "zone", "score": 1.0, "touches": 2}
  r = {"symbol": "XAU", "bar": 1, "c": decision(False, "opposing_entry_overlap", go_barrier), "b": decision(True, "opposing_barrier_full_ladder_fits")}
  rows = {("XAU", 1): {"barriers_b": [{"side": "sell", "low": 11.5, "high": 12.5}]}}
  assert analyze.divergence(r, {"planned_entry": 10.5}, "b", rows) == "python_band_narrower_entry_outside"


def test_divergence_names_the_weak_tier_rule():
  go_barrier = {"side": "buy", "low": 10.0, "high": 11.0, "tier": "zone", "score": 1.0, "touches": 0}
  r = {"symbol": "XAU", "bar": 1, "c": decision(False, "opposing_entry_contained", go_barrier), "a": decision(True, "weak_opposing_level_ignored")}
  assert analyze.divergence(r, {"planned_entry": 10.5}, "a", {}) == "python_tier_level_weak_ignored"


def test_divergence_agreement_and_reverse_cases():
  go_barrier = {"side": "buy", "low": 10.0, "high": 11.0, "tier": "zone", "score": 1.0, "touches": 0}
  both = {"symbol": "XAU", "bar": 1, "c": decision(False, "x", go_barrier), "a": decision(False, "x", go_barrier)}
  assert analyze.divergence(both, {"planned_entry": 10.5}, "a", {}) == "agree_blocked"
  neither = {"symbol": "XAU", "bar": 1, "c": decision(True, "ok"), "a": decision(True, "ok")}
  assert analyze.divergence(neither, {"planned_entry": 10.5}, "a", {}) == "agree_allowed"
  only_python = {"symbol": "XAU", "bar": 1, "c": decision(True, "ok"), "a": decision(False, "x", go_barrier)}
  assert analyze.divergence(only_python, {"planned_entry": 10.5}, "a", {}) == "python_blocks_go_allows"


def test_outcome_stats_clusters_by_episode_and_reports_the_race_rate():
  sim = {}
  rs = []
  for i, outcome in enumerate(["win", "win", "loss", "win"]):
    rs.append({"match_id": f"m{i}", "ts": 1000 + i, "symbol": "XAU", "direction": "BUY"})
    sim[f"m{i}{1000 + i}"] = {"r1": (outcome, 1.0 if outcome == "win" else -1.0)}
  out = analyze.outcome_stats(rs, sim)
  assert out["events"] == 4 and out["episodes"] == 1
  assert out["win1"] == pytest.approx(0.75)
  assert out["expectancy_r"] == pytest.approx(0.5)

"""The AE quality audit's matrix and tool are evidence, so they are tested.

* The reviewer's matrix covers exactly the strategies the Go engine registers, uses only the
  documented vocabulary, and never proposes a rebuild or retirement without flagging that it needs the
  owner's approval.
* The committed results file was produced from that matrix (it cannot go stale silently).
* Nothing is observe-only (owner rule 2026-10-14): the production config contains no observe-only strategy or timeframe.
* The R arithmetic: a full stop is -1R whatever ``realized_pips`` says (the executor writes 0.0
  there), and the exit-path bounds follow the 1R/2R 50/50 ladder.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
import yaml

from tools import ae_quality_eval as ev

pytestmark = pytest.mark.no_database

REPO = Path(ev.REPO)
MATRIX = yaml.safe_load((REPO / "docs/strategies/ae-strategy-matrix.yml").read_text())["strategies"]
RECOMMENDATIONS = {"RETAIN", "RECONSTRUCT", "RETIRE"}
VOCABULARY = {
  "technical": {"PASS", "PASS_WITH_DEFECTS", "FAIL"},
  "execution": {"COMPATIBLE", "PARTIAL", "INCOMPATIBLE"},
  "performance": {"POSITIVE", "NEUTRAL", "NEGATIVE", "UNPROVEN", "CONFLICTING"},
  "evidence": {"STRONG", "MODERATE", "WEAK", "INSUFFICIENT"},
}


def _registered() -> set[str]:
  document = yaml.safe_load((REPO / "config/analysis.yml").read_text())
  return set(document["analysis"]["strategies"])


def test_matrix_covers_exactly_the_registered_strategies() -> None:
  assert set(MATRIX) == _registered()
  assert len(MATRIX) == 17


@pytest.mark.parametrize("strategy_id", sorted(MATRIX))
def test_matrix_row_is_well_formed(strategy_id: str) -> None:
  row = MATRIX[strategy_id]
  assert row["recommendation"] in RECOMMENDATIONS
  for key, allowed in VOCABULARY.items():
    assert row["verdict"][key] in allowed, (strategy_id, key)
  assert row["scope"].strip() and row["rationale"].strip()
  assert row["technical_thesis"].strip() and row["validation_conditions"].strip()
  assert row["quality_formula"]["class"] in {"stars_over_3", "constant", "continuous"}
  if row["recommendation"] in {"RECONSTRUCT", "RETIRE"}:
    assert row["owner_approval_required"] is True, "a rebuild or retirement is the owner's decision"
  if row["recommendation"] == "RETIRE":
    assert row["verdict"]["technical"] == "FAIL", "retirement needs a technical failure, not a losing streak"


def test_committed_results_match_the_matrix() -> None:
  results = json.loads((REPO / "docs/strategies/ae-quality-replay-results.json").read_text())
  merged = results["strategy_matrix"]
  assert set(merged) == set(MATRIX)
  for strategy_id, row in MATRIX.items():
    assert merged[strategy_id]["final_recommendation"] == row["recommendation"], strategy_id
    assert merged[strategy_id]["confirmed_defects"] == row["confirmed_defects"], strategy_id
    assert merged[strategy_id]["independent_review_status"] == "pending"
  assert results["replay"]["range_edge"]["invariant_violations"] == {}
  assert all(not v["invariant_violations"] for v in results["replay"].values())


def test_nothing_is_observe_only_in_the_production_config() -> None:
  for symbol, contained in ev.containment().items():
    assert contained["observe_only_strategies"] == [], symbol
    assert contained["observe_only_structure_timeframes"] == [], symbol


def test_a_full_stop_is_minus_one_r_whatever_realized_pips_says() -> None:
  stopped = {"exit_path": "full_stop", "realized_pips": "0.0", "risk_pips": "20", "stop_pips": "20"}
  assert ev.corrected_r(stopped) == -1.0
  target = {"exit_path": "tp2_full", "realized_pips": "30", "risk_pips": "20", "stop_pips": "20"}
  assert ev.corrected_r(target) == pytest.approx(1.5)
  assert ev.corrected_r({"exit_path": "tp2_full", "realized_pips": "", "risk_pips": "20", "stop_pips": "20"}) is None


def _row(path: str) -> dict:
  return {"exit_path": path, "symbol": "XAU", "closed_at": "1790000000"}


def test_exit_path_bounds_follow_the_one_two_ladder() -> None:
  rows = [_row("full_stop")] * 2 + [_row("tp1_be"), _row("tp2_full"), _row("tp1_stop"), _row("manual_close")]
  bounds = ev.exit_path_bounds(rows)
  assert bounds["trades"] == 5, "manual_close is excluded"
  assert bounds["mean_r_low"] == pytest.approx((-2 + 0.5 + 1.5 + 0.0) / 5)
  assert bounds["mean_r_high"] == pytest.approx((-2 + 0.5 + 1.5 + 0.5) / 5)
  assert bounds["full_stop_rate"] == pytest.approx(0.4)
  assert ev.exit_path_bounds([_row("manual_close")]) == {}


def test_cluster_interval_is_seeded_and_needs_three_clusters() -> None:
  items = [(f"c{i % 6}", float(i % 3) - 1) for i in range(30)]
  assert ev.cluster_ci(items, random.Random(1)) == ev.cluster_ci(items, random.Random(1))
  assert ev.cluster_ci([("a", 1.0), ("a", 2.0), ("b", 0.0)], random.Random(1)) is None

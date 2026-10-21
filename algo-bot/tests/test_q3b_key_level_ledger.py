"""tools/q3b_key_level_ledger.py: the join between the Python decisions and the Go replays must attribute divergence honestly."""

import json

import pytest

from tools import q3b_key_level_ledger as ledger

pytestmark = pytest.mark.no_database

BAR = 1_790_000_000 - (1_790_000_000 % 300)


def _decision(**over):
  base = {"direction": "BUY", "entry_low": 4300.0, "entry_high": 4304.0, "confluence": 3, "key_level": 4302.0, "structural_kind": "round", "confirmation_type": "wick_rejection",
          "touch_bar": BAR, "confirmation_bar": BAR, "source_touches": 3, "role": "ambiguous"}
  base.update(over)
  return base


def _golden(tmp_path, decisions, bars=None, key="XAU", capture="replay-xau-production-capture-20260921.json"):
  path = tmp_path / "oracle.json"
  path.write_text(json.dumps({"symbols": {key: {"capture": capture, "bars": bars or [BAR, BAR + 300], "decisions": {str(k): v for k, v in decisions.items()}}}}))
  return str(path)


def _record(i, bar, stars, capture="XAU Sep", **over):
  base = {"capture": capture, "symbol": "XAU", "strategy": "key_level", "id": f"opp_{i}", "direction": "BUY", "eval_time": bar + 300, "entry_low": 4300.0, "entry_high": 4304.0,
          "invalidation": 4296.0, "stars": stars, "factors": {}, "evidence": [], "structural_id": "keylevel:round:4302",
          "sim": {"ladder_cost": {"result": "stop", "r": -1.0, "mfe": 0.2, "mae": 1.0}}}
  base.update(over)
  return base


def test_a_decision_the_current_engine_still_publishes_with_the_same_stars_has_no_divergence(tmp_path):
  golden = _golden(tmp_path, {BAR: _decision()})
  frozen, current = [_record(1, BAR, 3)], [_record(1, BAR, 3)]
  rows, python_only, current_only, disagreements, _ = ledger.build([golden], frozen, {}, current, {})
  assert [r["earliest_divergent_stage"] for r in rows] == ["none"]
  assert python_only == [] and current_only == [] and disagreements == []


def test_the_confluence_floor_is_attributed_to_acceptance_not_detection(tmp_path):
  golden = _golden(tmp_path, {BAR: _decision(confluence=2)})
  rows, *_ = ledger.build([golden], [_record(1, BAR, 2)], {}, [], {})
  assert rows[0]["earliest_divergent_stage"] == "acceptance"
  assert rows[0]["go_current"] == {"decision": "not_published"}


def test_a_changed_star_count_is_a_quality_divergence_and_a_changed_band_a_geometry_one(tmp_path):
  golden = _golden(tmp_path, {BAR: _decision(), BAR + 300: _decision()})
  frozen = [_record(1, BAR, 3), _record(2, BAR + 300, 3, entry_low=4301.0)]
  current = [_record(1, BAR, 2)]
  rows, *_ = ledger.build([golden], frozen, {}, current, {})
  assert [r["earliest_divergent_stage"] for r in rows] == ["quality", "geometry"]


def test_a_go_opportunity_the_python_never_fired_is_a_detection_divergence(tmp_path):
  golden = _golden(tmp_path, {})
  rows, *_ = ledger.build([golden], [_record(1, BAR, 3)], {}, [], {})
  assert rows[0]["earliest_divergent_stage"] == "detection" and rows[0]["python"]["decision"] == "silent"


def test_a_python_decision_nothing_explains_is_reported_but_a_standing_thesis_covers_the_next_bar(tmp_path):
  golden = _golden(tmp_path, {BAR: _decision(), BAR + 300: _decision()})
  rows, python_only, *_ = ledger.build([golden], [_record(1, BAR, 3)], {}, [], {})
  assert len(rows) == 1 and python_only == [], "the second bar repeats the identity published on the first"
  rows, python_only, *_ = ledger.build([golden], [], {}, [], {})
  assert len(python_only) == 2


def test_only_the_capture_that_owns_a_bar_is_compared_with_its_python_decisions(tmp_path):
  golden = _golden(tmp_path, {BAR: _decision()})
  other_capture = _record(1, BAR, 3, capture="XAU Oct M5")
  rows, python_only, *_ = ledger.build([golden], [other_capture], {}, [], {})
  assert rows == [] and len(python_only) == 1


def test_bars_evaluated_by_two_captures_must_agree_or_the_disagreement_is_reported(tmp_path):
  first = tmp_path / "a.json"
  second = tmp_path / "b.json"
  first.write_text(json.dumps({"symbols": {"XAU": {"capture": "x", "bars": [BAR], "decisions": {str(BAR): _decision()}}}}))
  second.write_text(json.dumps({"symbols": {"XAU_1013": {"capture": "y", "bars": [BAR], "decisions": {}}}}))
  rows, evaluated, disagreements = ledger.python_theses(ledger.load_golden([str(first), str(second)]))
  assert len(disagreements) == 1 and disagreements[0]["later_capture"] == "XAU_1013"
  assert evaluated[("XAU", BAR)] == "XAU"


def test_the_outcome_carries_the_random_control_only_for_filled_trades():
  filled = ledger.outcome({"id": "a", "sim": {"ladder_cost": {"result": "stop", "r": -1.0}}}, {"a": {"mean_r": -0.1}})
  unfilled = ledger.outcome({"id": "b", "sim": {"ladder_cost": {"result": "unfilled"}}}, {"b": {"mean_r": -0.1}})
  assert filled["difference_r"] == pytest.approx(-0.9)
  assert unfilled["difference_r"] is None

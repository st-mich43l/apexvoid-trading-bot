"""The Q5.3 evaluation tools must be right on constructed inputs with a known answer: the windows, the dimension audit, the lineage join and the sample size."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from tools import q53_lineage as lineage
from tools import q53_quality_forward as forward

pytestmark = pytest.mark.no_database


def utc(y, m, d, h=0):
  return int(dt.datetime(y, m, d, h, tzinfo=dt.timezone.utc).timestamp())


def test_a_bar_belongs_to_exactly_one_window_and_the_forward_window_opens_on_monday_the_19th():
  assert forward.window_of(utc(2026, 10, 6, 23)) == "development"
  assert forward.window_of(utc(2026, 10, 7)) == "out_of_sample"
  assert forward.window_of(utc(2026, 10, 18, 23)) == "out_of_sample"
  assert forward.window_of(utc(2026, 10, 19)) == "forward"
  rows = [{"t": utc(2026, 10, d)} for d in (2, 8, 20)]
  parts = forward.windows(rows)
  assert [len(parts[w]) for w in ("development", "out_of_sample", "forward", "all")] == [1, 1, 1, 3]


def test_saturation_reports_how_much_of_a_dimension_is_one_value():
  out = forward.saturation([1.0] * 8 + [0.0, 0.5])
  assert out["n"] == 10 and out["at_1"] == 0.8 and out["at_0"] == 0.1 and out["modal_share"] == 0.8 and out["distinct"] == 3
  assert forward.saturation([]) == {"n": 0}


def test_room_read_on_the_executable_risk_uses_the_same_ramp_and_treats_missing_as_zero():
  assert forward.q_room(1.0) == 0.0 and forward.q_room(3.0) == 1.0 and forward.q_room(2.0) == pytest.approx(0.5)
  assert forward.q_room(None) == 0.0 and forward.q_room(float("nan")) == 0.0 and forward.q_room(0.4) == 0.0


def test_the_scoring_rule_is_read_off_the_envelope_never_assumed():
  assert lineage.quality_format({"quality": {"components": {"confluence": 1.0, "touch_quality": 0.4}}}) == "legacy"
  assert lineage.quality_format({"quality": {"components": {"score_room": 0.5}}}) == "model_v1"
  assert lineage.quality_format({"quality": {"components": {"score_room": 0.5, "quality_model_version": 2}}}) == "model_v2"
  assert lineage.quality_format({}) == "legacy"


def envelope(opp_id, *, strategy="key_level", symbol="EURUSD", produced, components, fingerprint="f1"):
  return {"id": opp_id, "strategy": strategy, "symbol": symbol, "direction": "BUY", "created_at": produced - 300, "produced_at": produced,
          "entry": {"low": 1.1000, "high": 1.1002}, "invalidation": {"price": 1.0996}, "targets": [{"price": {"price": 1.1030}}],
          "quality": {"overall": 0.6, "components": components}, "technical_context": {"confluence": {"selected_stars": 2}}, "config_fingerprint": fingerprint}


def test_the_join_keeps_one_group_per_opportunity_and_separates_the_scoring_rules_and_the_result_methods(tmp_path):
  after, before = utc(2026, 10, 22, 8), utc(2026, 10, 21, 8)
  opps = [
    envelope("opp_a", produced=after, components={"score_room": 0.5, "quality_model_version": 1}),
    envelope("opp_b", produced=before, components={"confluence": 1.0}),
    envelope("opp_c", strategy="range_edge", produced=before, components={"confluence": 1.0}),
  ]
  results = [
    {"group_id": "v8:go_opp_a", "symbol": "EURUSD", "closed_at": after + 3600, "exit_path": "full_stop", "stop_pips": 15.0, "net_r": -1.0, "result_method": "fills_v1", "realized_pips": -15.0},
    {"group_id": "v8:go_opp_b", "symbol": "EURUSD", "closed_at": before + 3600, "exit_path": "tp1_be", "stop_pips": 15.0, "booked_tp_count": 1, "realized_pips": None},
    {"group_id": "v8:go_opp_c", "symbol": "EURUSD", "closed_at": before + 7200, "exit_path": "full_stop", "stop_pips": 15.0, "realized_pips": 0.0},
    {"group_id": "v8:go_opp_missing", "symbol": "EURUSD", "closed_at": before, "exit_path": "full_stop", "stop_pips": 15.0},
  ]
  paths = {}
  for name, rows in (("opps", opps), ("results", results)):
    paths[name] = tmp_path / f"{name}.jsonl"
    paths[name].write_text("\n".join(json.dumps(r) for r in rows) + "\n")
  out = tmp_path / "lineage.json"
  import sys
  argv, sys.argv = sys.argv, ["q53_lineage", "--results", str(paths["results"]), "--opps", str(paths["opps"]), "--out", str(out)]
  try:
    lineage.main()
  finally:
    sys.argv = argv
  report = json.loads(out.read_text())
  assert report["closed_groups_with_a_result"] == 3 and report["closed_groups_without_envelope"] == 1
  assert report["by_quality_format"] == {"model_v1": 1, "legacy": 2}
  assert report["by_result_method"]["fills_v1"] == 1
  assert report["format_agrees_with_publish_time"] == {"v1_scored_before_boundary": 0, "legacy_scored_after_boundary": 0}
  assert report["duplicate_opportunity_ids"] == 0
  assert report["current_strategies"] == {"groups": 2, "purged_groups": 1}      # range_edge is purged
  assert report["model_v1_closed_groups"]["n"] == 1 and report["model_v1_closed_groups"]["measured_fills_v1"] == 1
  # technical risk 4 pips (1.1002 -> 1.0996 is 6 pips from the proximal edge 1.1002), stop 15
  needed = report["sample_needed"]
  import math
  assert needed["per_side_for_0.25r"] == math.ceil(2 * (2.8 * needed["sd_of_r"] / 0.25) ** 2)
  widening = report["technical_vs_executable_risk"]
  assert widening == {} or all(v["widening_median"] > 1 for v in widening.values())

"""Q5.5 replay-versus-production tool: the simulator port, the exact matching, the paired comparison and the decomposition."""

from __future__ import annotations

import glob
import json
import os

import pytest

from tools import q55_replay_parity as q55

pytestmark = pytest.mark.no_database

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURES = sorted(glob.glob(os.path.join(HERE, "..", "..", "analysis-engine", "testdata", "replay-*production-capture-*.json")))


# ---- the simulator reproduces the Go replay ------------------------------------------------------------------------------------

def test_the_python_simulator_reproduces_the_go_replay_on_real_captures():
  bars = q55.load_bars(CAPTURES)
  cases = json.load(open(os.path.join(HERE, "fixtures", "q55_go_simulator_cases.json")))
  assert len(cases) >= 10
  seen = set()
  for case in cases:
    record = case["record"]
    series, index = bars[record["symbol"]]
    result, r = q55.simulate(record, series, index, q55.SPREAD[record["symbol"]])
    assert (result, round(r, 6)) == (case["go"]["result"], round(case["go"]["r"], 6)), record["id"]
    seen.add((record["direction"], result))
  assert {"stop", "stop_be", "target", "unfilled", "not_executable"} <= {result for _, result in seen}
  assert {"BUY", "SELL"} <= {direction for direction, _ in seen}


def _bars(rows):
  """rows of (open, high, low, close), five minutes apart from t=0."""
  series = [[i * 300, o, h, l, c, 0] for i, (o, h, l, c) in enumerate(rows)]
  return series, {row[0]: i for i, row in enumerate(series)}


def _record(direction="BUY", symbol="EURUSD", **kw):
  base = {"symbol": symbol, "strategy": "key_level", "direction": direction, "eval_time": 300, "expires_at": 0, "entry_low": 1.0996,
          "entry_high": 1.1000, "invalidation": 1.0988, "floor_pips": 12, "cap_pips": 0}
  if direction == "SELL":
    base.update(entry_low=1.1000, entry_high=1.1004, invalidation=1.1012)
  base.update(kw)
  return base


def test_a_buy_fills_at_the_proximal_edge_and_runs_the_ladder_with_a_breakeven_stop():
  # bar 0 is the evaluated bar; bar 1 retraces to the edge; bar 2 reaches 1R (+12 pips); bar 3 falls back to entry
  series, index = _bars([(1.1005, 1.1006, 1.1004, 1.1005), (1.1004, 1.1005, 1.0999, 1.1000), (1.1000, 1.1013, 1.1000, 1.1012), (1.1012, 1.1012, 1.0999, 1.1000)])
  result, r = q55.simulate(_record(), series, index, 0.0)
  assert result == "stop_be" and r == pytest.approx(0.5)  # half closed at +1R, the rest stopped at entry


def test_a_sell_is_the_mirror_of_a_buy():
  series, index = _bars([(1.0995, 1.0996, 1.0994, 1.0995), (1.0996, 1.1001, 1.0995, 1.1000), (1.1000, 1.1000, 1.0987, 1.0988), (1.0988, 1.1001, 1.0988, 1.1000)])
  result, r = q55.simulate(_record("SELL"), series, index, 0.0)
  assert result == "stop_be" and r == pytest.approx(0.5)


def test_the_stop_is_widened_to_the_floor_and_a_stop_in_the_fill_bar_is_a_full_loss():
  series, index = _bars([(1.1005, 1.1006, 1.1004, 1.1005), (1.1004, 1.1005, 1.0987, 1.0990)])
  # Go's invalidation is 12 pips below the edge here; with a 15-pip floor the stop is 1.0985 and the fill bar (low 1.0987) does not reach it
  assert q55.simulate(_record(floor_pips=15), series, index, 0.0)[0] != "stop"
  assert q55.simulate(_record(floor_pips=12), series, index, 0.0) == ("stop", -1.0)


def test_a_limit_that_is_never_reached_is_unfilled_and_a_cap_makes_it_not_executable():
  series, index = _bars([(1.1010, 1.1011, 1.1009, 1.1010)] * 6)
  assert q55.simulate(_record(), series, index, 0.0) == ("unfilled", 0.0)
  series, index = _bars([(1.1005, 1.1006, 1.1004, 1.1005), (1.1004, 1.1005, 1.0999, 1.1000)])
  assert q55.simulate(_record(cap_pips=10), series, index, 0.0) == ("not_executable", 0.0)


def test_the_production_fill_and_stop_replace_the_replay_assumptions():
  series, index = _bars([(1.1005, 1.1006, 1.1004, 1.1005), (1.1004, 1.1005, 1.0999, 1.1000), (1.1000, 1.1013, 1.0996, 1.1012), (1.1012, 1.1026, 1.1012, 1.1025)])
  record = _record()
  # deeper fill (1.0996, 4 pips below the edge) with a 10-pip stop: +1R is 1.1006 and +2R is 1.1016, both reached
  result, r = q55.simulate(record, series, index, 0.0, fill_idx=1, fill_price=1.0996, risk=0.0010)
  assert result == "target" and r == pytest.approx(1.5)


# ---- matching and pairing -------------------------------------------------------------------------------------------------------

def _envelope(**kw):
  base = {"id": "opp_a", "symbol": "EURUSD", "strategy": "key_level", "direction": "BUY", "created_at": 1000, "entry": {"low": 1.0996, "high": 1.1000},
          "invalidation": {"price": 1.0988}, "targets": [{"price": {"price": 1.1008}}]}
  base.update(kw)
  return base


def _replay(**kw):
  base = {"id": "opp_a", "symbol": "EURUSD", "strategy": "key_level", "direction": "BUY", "created_at": 1000, "entry_low": 1.0996, "entry_high": 1.1000,
          "invalidation": 1.0988, "targets": [1.1008], "exec_risk_pips": 12.0, "tech_risk_pips": 8.0, "floor_pips": 12,
          "sim": {"ladder_cost": {"result": "stop", "r": -1.0}}}
  base.update(kw)
  return base


def _group(**kw):
  base = {"group_id": "v8:go_opp_a", "symbol": "EURUSD", "direction": "BUY", "closed_at": 1_790_000_000, "result_pips": 0.0, "stop_pips": 12.0,
          "booked_tp_count": 0, "exit_path": "full_stop", "realized_pips": -12.0, "risk_pips": 12.0, "weighted_risk_pips": 12.0, "net_r": -1.0,
          "result_method": "fills_v1", "fills": [{"entry": 1.0999, "vol": 1_000_000, "at": 1_789_990_000}], "env": {"payload": _envelope()}}
  base.update(kw)
  return base


def test_an_opportunity_matches_only_when_its_geometry_is_the_same_to_half_a_pip():
  pairs, unmatched = q55.build_pairs([_group()], [_replay()])
  assert len(pairs) == 1 and pairs[0]["same_id"] and not unmatched
  _, differs = q55.build_pairs([_group()], [_replay(entry_low=1.0990)])
  assert differs == {"replay_opportunity_differs_in_geometry": 1}
  _, none = q55.build_pairs([_group()], [_replay(created_at=999)])
  assert none == {"no_replay_opportunity_with_the_same_key": 1}
  _, purged = q55.build_pairs([_group(env={"payload": _envelope(strategy="range_edge")})], [_replay()])
  assert purged == {"purged_strategy": 1}


@pytest.mark.parametrize("direction,fill,deeper", [("BUY", 1.0998, 2.0), ("SELL", 1.1002, 2.0), ("BUY", 1.1001, -1.0)])
def test_fill_depth_is_signed_against_the_proximal_edge_for_both_sides(direction, fill, deeper):
  if direction == "SELL":
    envelope = _envelope(direction="SELL", entry={"low": 1.1000, "high": 1.1004}, invalidation={"price": 1.1012}, targets=[{"price": {"price": 1.0992}}])
    replay = _replay(direction="SELL", entry_low=1.1000, entry_high=1.1004, invalidation=1.1012, targets=[1.0992])
  else:
    envelope, replay = _envelope(), _replay()
  g = _group(direction=direction, fills=[{"entry": fill, "vol": 1, "at": 5}], env={"payload": envelope})
  pairs, _ = q55.build_pairs([g], [replay])
  assert pairs[0]["deeper_pips"] == pytest.approx(deeper, abs=1e-6)


def test_the_fill_is_volume_weighted_across_legs():
  g = _group(fills=[{"entry": 1.0998, "vol": 3, "at": 5}, {"entry": 1.0994, "vol": 1, "at": 9}])
  pairs, _ = q55.build_pairs([g], [_replay()])
  assert pairs[0]["fill"] == pytest.approx(1.0997) and pairs[0]["first_fill_at"] == 5


def _pair(r, replay_r, *, method="fill_record_risk", closed="2026-10-20", symbol="EURUSD", result="stop", day=0):
  import datetime as dt
  t = int(dt.datetime.strptime(closed, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) + day * 86400
  return {"group": "FX" if symbol != "XAU" else "XAU", "symbol": symbol, "strategy": "key_level", "closed_at": t, "production_r": r, "replay_r": replay_r,
          "replay_result": result, "method": method, "exit_path": "full_stop", "deeper_pips": 1.0, "band_pips": 4.0, "production_stop_pips": 12.0,
          "replay_stop_pips": 12.0, "technical_risk_pips": 6.0, "floor_pips": 12, "same_id": True}


def test_the_paired_difference_uses_only_pairs_the_replay_filled():
  pairs = [_pair(-1.0, -1.0, day=i) for i in range(6)] + [_pair(1.0, 0.5, day=7 + i) for i in range(4)] + [_pair(-1.0, 0.0, result="unfilled", day=20)]
  out = q55.paired_block(pairs)
  assert out["matched"] == 11 and out["paired"] == 10
  assert out["difference_mean"] == pytest.approx(0.2) and out["win_loss_agreement"] == 1.0


def test_measured_estimated_and_assumed_results_are_never_merged_and_the_cutoff_is_applied():
  pairs = ([_pair(-1.0, -1.0, day=i) for i in range(10)]
           + [_pair(-1.0, -1.0, method="legacy_loss_unbooked", closed="2026-10-01", day=i) for i in range(10)]
           + [_pair(0.5, 0.5, method="reconstructed_ladder_v1", closed="2026-10-01", day=i) for i in range(10)]
           + [_pair(1.0, 1.0, closed="2026-10-01", day=i) for i in range(10)])  # measured, but before full stops were booked
  report = q55.parity_report(pairs, {})
  assert report["primary"]["n"] == 10  # measured methods closed on or after the first reliable close
  fx = report["by_group"]["FX"]
  assert fx["paired_primary"]["paired"] == 10
  assert fx["other_methods"]["legacy_loss_unbooked_ASSUMED_MINUS_ONE"]["paired"] == 10
  assert fx["other_methods"]["reconstructed_ladder"]["paired"] == 10
  assert fx["other_methods"]["all_dates_measured_only_BIASED_UPWARD"]["paired"] == 20


# ---- decomposition and funnel ------------------------------------------------------------------------------------------------------

def _obs(r, rand, *, t=1_790_000_000, symbol="EURUSD", htf=1.0, widening=2.0, day=0):
  return {"group": "FX", "symbol": symbol, "strategy": "key_level", "t": t + day * 86400, "r": r, "r_nocost": r + 0.07, "rand": rand, "diff": r - rand,
          "cluster": (symbol, day), "facts": {"htf_aligned": htf}, "widening": widening}


def test_the_decomposition_separates_the_cost_model_from_the_strategy_against_random():
  obs = [_obs(-0.2, -0.1, day=i) for i in range(10)] + [_obs(0.1, 0.0, htf=0.0, day=10 + i) for i in range(10)]
  out = q55.decomposition(obs)["FX"]
  assert out["all"]["n"] == 20 and out["cost_model_effect"]["mean"] == pytest.approx(-0.07)
  assert out["by_htf_alignment"]["aligned"]["above_random"] == pytest.approx(-0.1)
  assert out["by_htf_alignment"]["not_aligned"]["above_random"] == pytest.approx(0.1)
  assert q55.session(1_790_000_000 - 1_790_000_000 % 86400 + 3 * 3600) == "asia 00-07"


def test_the_funnel_reports_the_first_rejection_and_counts_a_published_plan_once():
  rows = [{"state": "rejected", "reason_code": "fx_opposite_position_not_allowed", "correlation_id": "a"},
          {"state": "invalidated", "reason_code": "x", "correlation_id": "a"},
          {"state": "rejected", "reason_code": "opposing_entry_overlap", "correlation_id": "b"},
          {"state": "candidate_published", "reason_code": "", "correlation_id": "c"}, {"state": "rejected", "reason_code": "later", "correlation_id": "c"}]
  out = q55.funnel(rows)
  assert out["opportunities"] == 3 and sorted(out["final_outcome"]) == [["fx_opposite_position_not_allowed", 1], ["opposing_entry_overlap", 1], ["published", 1]]


def test_a_group_with_no_fill_or_no_envelope_is_counted_as_unmatchable_not_guessed():
  pairs, unmatched = q55.build_pairs([_group(fills=[]), _group(env=None), _group(fills=None)], [_replay()])
  assert pairs == [] and unmatched == {"no_envelope_or_fill": 3}

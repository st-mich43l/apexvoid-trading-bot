"""Q5.4 evaluation tool: the removed-against-kept comparison, the buckets, the admission rule and the group geometry."""

from __future__ import annotations

import pytest

from tools import q54_executable_room as q54

pytestmark = pytest.mark.no_database


def _obs(r, *, diff=0.0, room_r=None, first=1.0, widening=1.0, day=0, symbol="EURUSD", t=0):
  return {"r": r, "diff": diff, "room_r": room_r, "first_target_r": first, "widening": widening, "symbol": symbol, "t": t + day * 86400,
          "cluster": (symbol, day)}


def test_buckets_have_half_open_edges_and_no_barrier_is_not_a_bucket():
  assert q54.bucket(0.499, q54.ROOM_BUCKETS) == "<0.5" and q54.bucket(0.5, q54.ROOM_BUCKETS) == "0.5-1"
  assert q54.bucket(2.0, q54.ROOM_BUCKETS) == ">2" and q54.bucket(None, q54.ROOM_BUCKETS) is None
  assert q54.bucket(1.0, q54.WIDENING_BUCKETS) == "1.0 (floor not binding)" and q54.bucket(2.5, q54.WIDENING_BUCKETS) == ">2.5"


def test_the_first_decision_of_a_thesis_is_the_one_kept():
  rows = [{"id": "a", "bar": 20, "allowed": False}, {"id": "a", "bar": 10, "allowed": True}, {"id": "b", "bar": 5, "allowed": True}]
  first = q54.first_decisions(rows)
  assert first["a"]["allowed"] is True and set(first) == {"a", "b"}


def test_admission_needs_a_confirmed_reaction_an_allowed_room_decision_and_two_stars():
  decisions = {
    "ok": {"admissible": True, "allowed": True}, "blocked": {"admissible": True, "allowed": False},
    "resting": {"admissible": False, "allowed": True}, "one_star": {"admissible": True, "allowed": True},
  }
  records = [{"id": "ok", "stars": 2}, {"id": "blocked", "stars": 3}, {"id": "resting", "stars": 3}, {"id": "one_star", "stars": 1}, {"id": "undecided", "stars": 3}]
  assert [r["id"] for r in q54.admitted(records, decisions)] == ["ok"]


def test_a_rule_reports_what_it_removes_and_refuses_a_side_too_small_to_read():
  items = [_obs(1.0, room_r=0.5, day=i) for i in range(30)] + [_obs(-1.0, room_r=2.0, day=i) for i in range(30)]
  out = q54.evaluate_rule(items, lambda o: o["room_r"] < 1)
  assert out["evaluated"] and out["admission_rate"] == 0.5 and out["removed"] == 30
  assert out["removed_winners"] == 30 and out["removed_losers"] == 0 and out["removed_sum_r"] == 30.0
  assert out["removed_minus_kept_r"]["estimate"] == 2.0  # the filter would remove the winners
  assert not q54.evaluate_rule(items[:30], lambda o: o["room_r"] < 1)["evaluated"]  # nothing kept


def test_drawdown_is_chronological():
  items = [_obs(-1, t=3), _obs(2, t=1), _obs(-2, t=2), _obs(1, t=4)]
  assert q54.max_drawdown(items) == -3.0  # +2, then -2 and -1


def test_the_summary_leaves_out_the_random_control_when_there_is_none():
  assert "above_random" not in q54.summary([{"r": 1.0, "cluster": ("X", 0), "t": 0}])
  assert q54.summary([_obs(1.0)])["above_random"] == 0.0


def _group(direction, *, entry, stop, target, fills=None, volume=1_000_000, symbol="EURUSD", executable=12.0):
  return {"symbol": symbol, "direction": direction, "stop_pips": executable,
          "fills": fills or [{"entry": entry, "vol": volume}],
          "env": {"payload": {"strategy": "key_level", "invalidation": {"price": stop}, "targets": [{"price": {"price": target}}]}}}


@pytest.mark.parametrize("direction,stop,target", [("BUY", 1.0995, 1.1008), ("SELL", 1.1005, 1.0992)])
def test_group_geometry_is_symmetric_for_buy_and_sell(direction, stop, target):
  g = q54.realized_geometry(_group(direction, entry=1.1, stop=stop, target=target))
  assert g["technical_risk_pips"] == pytest.approx(5.0) and g["first_target_pips"] == pytest.approx(8.0)
  assert g["widening"] == pytest.approx(2.4) and g["first_target_r"] == pytest.approx(8 / 12) and g["technical_rr"] == pytest.approx(1.6)


def test_group_entry_is_volume_weighted():
  fills = [{"entry": 4186.0, "vol": 8}, {"entry": 4184.0, "vol": 2}]
  g = q54.realized_geometry(_group("BUY", entry=0, stop=4183.0, target=4200.0, fills=fills, symbol="XAU", executable=40.0))
  assert g["entry"] == pytest.approx(4185.6) and g["technical_risk_pips"] == pytest.approx(26.0)


@pytest.mark.parametrize("bad", [{"stop": 1.1005}, {"target": 1.0990}, {"fills": [{"entry": 1.1, "vol": 0}]}, {"executable": 0}])
def test_a_group_that_cannot_be_measured_is_skipped_not_invented(bad):
  kwargs = {"entry": 1.1, "stop": 1.0995, "target": 1.1008}
  kwargs.update(bad)
  assert q54.realized_geometry(_group("BUY", **kwargs)) is None


def test_purged_strategies_are_left_out_of_the_realized_report():
  g = _group("BUY", entry=1.1, stop=1.0995, target=1.1008)
  g["env"]["payload"]["strategy"] = "range_edge"
  assert q54.realized_report([g])["excluded_purged_strategy"] == 1

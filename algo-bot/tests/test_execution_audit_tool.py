"""tools/execution_audit.py measures from records and never invents a missing one."""

from __future__ import annotations

import argparse
import gzip
import json

import pytest

from tools import execution_audit as audit

pytestmark = pytest.mark.no_database

PLAN_ID = "v8:go_opp_aaa"
OPP = "opp_aaa"


def _plan(plan_id=PLAN_ID, *, direction="BUY", entry=None, stop="4100.00", invalidation="4100.30", strategy="Key Level"):
  return {
    "version": 8, "plan_id": plan_id, "symbol": "XAU", "created_at": 1_000,
    "analysis": {"strategy": strategy, "direction": direction},
    "source_structure": {"invalidation_price": invalidation},
    "entry": entry or {"type": "single_limit", "order_price": "4110.00", "legs": []},
    "stop": {"price": stop, "source": "go_invalidation"},
    "targets": [{"target_id": "TP1", "price": "4120.00"}],
  }


def _write(tmp_path, plans, fills, opps="opportunity_id,symbol,strategy,state,created_at,expires_at,terminal_at,x\n", log=""):
  plans_path = tmp_path / "plans.jsonl"
  plans_path.write_text("\n".join(
    json.dumps({"id": "1000000-0", "f": {"payload": json.dumps(plan)}}) for plan in plans
  ))
  fills_path = tmp_path / "fills.csv"
  fills_path.write_text("position_id,group_id,symbol,setup_type,direction,entry_price,stop_pips,volume,filled_at\n" + fills)
  opps_path = tmp_path / "opps.csv"
  opps_path.write_text(opps)
  log_path = tmp_path / "algo.log.gz"
  with gzip.open(log_path, "wt") as handle:
    handle.write(log)
  return argparse.Namespace(
    plans=str(plans_path), fills=str(fills_path), results="", opps=str(opps_path), algo_log=str(log_path),
  )


def test_slippage_is_positive_when_the_fill_is_worse_for_the_trade(tmp_path):
  fills = f"1,{PLAN_ID},XAU,Key Level,BUY,4110.30,40,1000,1010\n"
  report = audit.run(_write(tmp_path, [_plan()], fills))
  stats = report["plan_vs_fill"]["slippage_pips_positive_is_worse_nearest_declared_price"]["XAU|single_limit"]
  assert stats["n"] == 1
  assert stats["mean"] == pytest.approx(3.0)  # BUY filled 0.30 above its 4110.00 limit
  sell = _plan(direction="SELL", entry={"type": "single_limit", "order_price": "4110.00", "legs": []})
  report = audit.run(_write(tmp_path, [sell], f"1,{PLAN_ID},XAU,Key Level,SELL,4110.30,40,1000,1010\n"))
  stats = report["plan_vs_fill"]["slippage_pips_positive_is_worse_nearest_declared_price"]["XAU|single_limit"]
  assert stats["mean"] == pytest.approx(-3.0)  # SELL filled 0.30 above its limit is better


def test_more_fills_than_declared_legs_is_reported(tmp_path):
  fills = (
    f"1,{PLAN_ID},XAU,Key Level,BUY,4110.00,40,1000,1010\n"
    f"2,{PLAN_ID},XAU,Key Level,BUY,4110.00,40,500,1011\n"
  )
  report = audit.run(_write(tmp_path, [_plan()], fills))
  assert report["plan_vs_fill"]["fills_beyond_declared_legs"] == [
    {"plan_id": PLAN_ID, "declared": 1, "fills": 2}
  ]


def test_a_declared_risk_leg_is_counted_as_a_declared_leg(tmp_path):
  entry = {
    "type": "market_with_limit_scale", "zone_low": "4108", "zone_high": "4110",
    "legs": [{"leg_id": "L1", "price": "4110.00"}, {"leg_id": "L2", "price": "4108.00"}],
    "risk_leg": {"price": "4101.50", "lots": "0.05"},
  }
  fills = "".join(f"{n},{PLAN_ID},XAU,Key Level,BUY,4109.00,40,1000,{1010 + n}\n" for n in range(3))
  report = audit.run(_write(tmp_path, [_plan(entry=entry)], fills))
  assert report["plan_vs_fill"]["fills_beyond_declared_legs"] == []
  assert report["declared_entry_shapes"]["Key Level"] == {"XAU|market_with_limit_scale|legs=2|risk_leg=yes": 1}


def test_stop_distance_beyond_go_invalidation_is_measured(tmp_path):
  report = audit.run(_write(tmp_path, [_plan(stop="4100.00", invalidation="4102.00")], ""))
  stats = report["stop_geometry"]["stop_distance_beyond_go_invalidation_pips"]["Key Level|XAU"]
  assert stats["mean"] == pytest.approx(20.0)
  assert report["stop_geometry"]["planned_risk_pips_nearest_leg_to_stop"]["Key Level|XAU"]["mean"] == pytest.approx(100.0)


def test_funnel_and_latency_come_only_from_logged_events(tmp_path):
  log = "\n".join([
    f"2026-10-09 10:00:00,000 [INFO] app.autotrade.go_opportunity_policy: Go opportunity adapted opportunity={OPP} match=go_{OPP}",
    f"2026-10-09 10:01:00,000 [INFO] app.autotrade.worker: v8 execution confirmation symbol=XAU setup_id=go_{OPP} phase=trigger_ready",
    f"2026-10-09 10:01:01,500 [INFO] app.autotrade.worker: v8 execution confirmation symbol=XAU setup_id=go_{OPP} phase=published",
    "2026-10-09 10:05:00,000 [INFO] app.autotrade.worker: v8 build rejected symbol=XAU setup_id=go_opp_bbb reason=opposing_entry_overlap message=x terminal=invalidated",
  ])
  opps = (
    "opportunity_id,symbol,strategy,state,created_at,expires_at,terminal_at,x\n"
    f"{OPP},XAU,key_level,active,1,2,3,\nopp_bbb,XAU,supply,invalidated,1,2,3,\n"
  )
  fills = f"1,{PLAN_ID},XAU,Key Level,BUY,4110.00,40,1000,1010\n"
  report = audit.run(_write(tmp_path, [_plan()], fills, opps=opps, log=log))
  assert report["latency"]["adapted_to_entry_trigger_s"]["median"] == pytest.approx(60.0)
  assert report["latency"]["trigger_to_plan_published_s"]["median"] == pytest.approx(1.5)
  # plan stream id 1000000-0 is 1000 s; the fill is at 1010 s.
  assert report["latency"]["plan_published_to_first_fill_s"]["median"] == pytest.approx(10.0)
  by = report["funnel"]["by_strategy"]
  assert by["Key Level"]["plan_filled"] == 1
  assert by["Supply Demand"]["terminally_rejected"] == 1
  assert report["funnel"]["rejection_reasons_by_strategy"]["Supply Demand"] == {"opposing_entry_overlap": 1}


def test_nothing_is_reported_for_a_plan_that_never_filled(tmp_path):
  report = audit.run(_write(tmp_path, [_plan()], ""))
  assert report["plan_vs_fill"]["slippage_pips_positive_is_worse_nearest_declared_price"] == {}
  assert report["latency"]["plan_published_to_first_fill_s"] == {"n": 0}

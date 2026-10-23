"""Q5.5: the immutable plan-time evidence (app.autotrade.plan_snapshot + store.record_plan_snapshot)."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.autotrade.plan_snapshot import build_plan_snapshot
from app.autotrade.structural_target_room import ZoneOpposingEntry, assess_executable_room, evaluate_structural_target_room
from app.persistence import store


def _plan(plan_id="v8:go_opp_aa", symbol="EURUSD", stop="1.09880"):
  return SimpleNamespace(
    plan_id=plan_id, thesis_id="th1", symbol=symbol, stop=SimpleNamespace(price=Decimal(stop)),
    entry=SimpleNamespace(max_spread_ticks=20, max_slippage_ticks=10),
    targets=(SimpleNamespace(target_id="TP1", price=Decimal("1.10120"), close_ratio=Decimal("0.5")),
             SimpleNamespace(target_id="TP2", price=Decimal("1.10240"), close_ratio=Decimal("0.5"))),
    provenance=SimpleNamespace(config_fingerprint="", analysis_engine_version="v2"),
    to_dict=lambda: {"plan_id": plan_id, "stop": {"price": Decimal(stop)}},
  )


def _match(**kw):
  base = dict(match_id="go_opp_aa", strategy="key_level", direction="BUY", issued_at=100, quality_overall=0.6,
              quality_components={"score_room": 0.25, "quality_model_version": 1}, entry_low=1.0996, entry_high=1.1,
              go_invalidation_price=1.0995, targets_pips=(8, 16))
  base.update(kw)
  return SimpleNamespace(**base)


def _gate(**kw):
  base = {"planned_base_stop_pips": "5", "planned_final_stop_pips": "12", "planned_execution_route": "single_limit",
          "planned_entry_price": "1.10000", "stop_adjustment": "envelope_minimum", "not_an_admission_field": "x"}
  base.update(kw)
  return base


def _room(room_pips=18, pip=0.0001):
  entry = 1.1
  barrier = ZoneOpposingEntry("sell", entry + room_pips * pip, entry + (room_pips + 10) * pip, tier="zone", score=0.8, touches=1,
                              members=({"zone_id": "zone:M15:supply:1", "timeframe": "M15"},))
  decision = evaluate_structural_target_room(
    direction="BUY", planned_entry_price=entry, candidate_entry_low=entry, candidate_entry_high=entry, configured_target_pips=(40,),
    actionable_entries=[barrier], atr=0.0010, pip_size=pip, barrier_buffer_atr=0.0, execution_cost_pips=1.0, executable_entry_price=entry)
  return decision, assess_executable_room(decision, executable_stop_pips=12, technical_stop_pips=5, target_pips=[12, 24])


def _snapshot(**kw):
  decision, room = _room()
  args = dict(plan=_plan(), match=_match(), opportunity_id="opp_aa", admitted_at=1000, gate_measured=_gate(), target_room=decision,
              executable_room=room, spot=SimpleNamespace(bid=1.0999, ask=1.1001, ts=999), executable_quote=1.1001,
              execution_cost_pips=1.0, pip_size=0.0001)
  args.update(kw)
  return build_plan_snapshot(**args)


@pytest.mark.no_database
def test_the_snapshot_carries_the_technical_and_the_executable_geometry():
  s = _snapshot()
  assert (s["plan_id"], s["opportunity_id"], s["symbol"], s["strategy"], s["direction"]) == ("v8:go_opp_aa", "opp_aa", "EURUSD", "key_level", "BUY")
  assert (s["opportunity_created_at"], s["admitted_at"]) == (100, 1000)
  assert (s["technical_entry_low"], s["technical_entry_high"], s["technical_invalidation"], s["technical_stop_pips"]) == (1.0996, 1.1, 1.0995, 5.0)
  assert (s["entry_route"], s["planned_entry_price"], s["final_stop_price"], s["final_stop_pips"]) == ("single_limit", 1.1, 1.0988, 12.0)
  assert s["stop_widening_ratio"] == 2.4 and s["stop_adjustment"] == "envelope_minimum"
  assert s["technical_targets_pips"] == [8, 16]
  assert [t["price"] for t in s["targets"]] == ["1.10120", "1.10240"] and s["targets"][0]["close_ratio"] == "0.5"
  assert (s["execution_cost_pips"], s["max_spread_ticks"], s["spot_spread_pips"], s["executable_quote"]) == (1.0, 20, 2.0, 1.1001)


@pytest.mark.no_database
def test_the_barrier_and_the_room_in_executable_r_are_recorded():
  s = _snapshot()
  assert s["barrier_present"] is True and s["barrier_side"] == "sell" and s["barrier_tier"] == "zone"
  assert s["barrier_low"] == pytest.approx(1.1018) and s["barrier_high"] == pytest.approx(1.1028)
  assert s["barrier_members"] == [{"zone_id": "zone:M15:supply:1", "timeframe": "M15"}]
  assert s["usable_room_pips"] == pytest.approx(18.0) and s["room_r"] == pytest.approx(1.5) and s["room_classification"] == "ladder_exceeds_room"


@pytest.mark.no_database
def test_scoring_lineage_is_recorded_and_never_invented():
  assert _snapshot()["quality_model_version"] == 1 and _snapshot()["quality_overall"] == 0.6
  legacy = _snapshot(match=_match(quality_components={"zone_departure": 0.4}))
  assert legacy["quality_model_version"] is None  # a pre-Q5.3 score names no rule; the date is not a lineage
  assert _snapshot()["config_fingerprint"] is None  # the Python side builds plans without one: Go's is joined through opportunity_id


@pytest.mark.no_database
def test_unavailable_measurements_are_none_not_zero():
  s = _snapshot(target_room=None, executable_room=None, spot=None, executable_quote=None, execution_cost_pips=None,
                gate_measured={"planned_execution_route": "market"})
  assert s["barrier_present"] is None and s["room_r"] is None and s["room_classification"] is None
  assert s["spot_spread_pips"] is None and s["executable_quote"] is None and s["execution_cost_pips"] is None
  assert s["stop_widening_ratio"] is None and s["final_stop_pips"] is None and s["planned_entry_price"] is None


@pytest.mark.no_database
def test_no_barrier_is_recorded_as_no_barrier():
  decision = evaluate_structural_target_room(
    direction="BUY", planned_entry_price=1.1, candidate_entry_low=1.1, candidate_entry_high=1.1, configured_target_pips=(20,),
    actionable_entries=[], atr=0.001, pip_size=0.0001, barrier_buffer_atr=0.5, execution_cost_pips=1.0, executable_entry_price=1.1)
  s = _snapshot(target_room=decision, executable_room=assess_executable_room(decision, executable_stop_pips=12, technical_stop_pips=5, target_pips=[12, 24]))
  assert s["barrier_present"] is False and s["barrier_low"] is None and s["room_classification"] == "no_opposing_barrier"


@pytest.mark.no_database
def test_jpy_and_xau_pip_precision_and_the_volume_weighted_entry_reference():
  jpy = _snapshot(plan=_plan(symbol="USDJPY", stop="149.820"), spot=SimpleNamespace(bid=150.000, ask=150.008, ts=1), pip_size=0.01,
                  gate_measured=_gate(planned_base_stop_pips="5", planned_final_stop_pips="18", planned_entry_price="150.000"))
  assert jpy["spot_spread_pips"] == 0.8 and jpy["stop_widening_ratio"] == 3.6 and jpy["final_stop_price"] == 149.82
  xau = _snapshot(plan=_plan(symbol="XAU", stop="4182.10"), spot=SimpleNamespace(bid=4354.1, ask=4354.3, ts=1), pip_size=0.1,
                  gate_measured=_gate(planned_base_stop_pips="31", planned_final_stop_pips="40", planned_entry_price="4185.80",
                                      planned_leg_entry_prices=["4186.10", "4184.60"], planned_stop_entry_price="4185.80"))
  assert xau["spot_spread_pips"] == 2.0 and xau["stop_widening_ratio"] == pytest.approx(1.2903, abs=1e-4)
  assert xau["admission"]["planned_leg_entry_prices"] == ["4186.10", "4184.60"]  # the legs the weighted reference was built from


@pytest.mark.no_database
def test_the_row_is_json_safe_and_keeps_only_admission_fields():
  s = _snapshot()
  json.dumps(s)  # Decimal and non-finite values are already converted
  assert "not_an_admission_field" not in s["admission"] and s["admission"]["planned_execution_route"] == "single_limit"
  assert s["plan"]["stop"]["price"] == "1.09880"


# ---- persistence --------------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_snapshot_is_written_once_and_a_redelivery_changes_nothing(sql):
  first = _snapshot()
  assert await store.record_plan_snapshot(first) is True
  # the same plan again (event redelivery, a second publish, a restart replaying the step) with different numbers: the first row wins
  again = _snapshot(gate_measured=_gate(planned_final_stop_pips="30"), admitted_at=2000)
  assert await store.record_plan_snapshot(again) is False
  assert await sql.val("SELECT count(*) FROM auto_trade_plan_snapshots") == 1
  row = await sql.row("SELECT final_stop_pips, admitted_at, room_r, barrier_members, plan FROM auto_trade_plan_snapshots")
  assert row["final_stop_pips"] == 12.0 and row["admitted_at"] == 1000 and row["room_r"] == pytest.approx(1.5)
  assert json.loads(row["barrier_members"]) == [{"zone_id": "zone:M15:supply:1", "timeframe": "M15"}]
  assert json.loads(row["plan"])["plan_id"] == "v8:go_opp_aa"


@pytest.mark.asyncio
async def test_unavailable_measurements_are_sql_null(sql):
  await store.record_plan_snapshot(_snapshot(target_room=None, executable_room=None, spot=None, gate_measured={}))
  row = await sql.row("SELECT barrier_present, room_r, spot_spread_pips, final_stop_pips, barrier_members, quality_model_version FROM auto_trade_plan_snapshots")
  assert row["barrier_present"] is None and row["room_r"] is None and row["spot_spread_pips"] is None
  assert row["final_stop_pips"] is None and row["barrier_members"] is None and row["quality_model_version"] == 1


@pytest.mark.asyncio
async def test_the_snapshot_joins_the_opportunity_to_the_fill_and_the_result(sql):
  await store.record_plan_snapshot(_snapshot())
  gid = "v8:go_opp_aa"
  await store.record_auto_trade_event({"type": "order_filled", "timestamp": 10, "position_id": 7001, "group_id": gid, "candidate_id": gid,
                                       "symbol": "EURUSD", "setup": "Key Level", "direction": "BUY", "price": 1.1, "stop_loss": 1.0988,
                                       "volume": 1000000})
  await store.record_auto_trade_event({"type": "position_closed", "timestamp": 50, "position_id": 7001, "group_id": gid, "candidate_id": gid,
                                       "price": 1.0988, "target_pips": 0, "group_realized_pips": -12, "message": "PLAN CLOSED · no TP archived",
                                       "reason_code": "stop_loss_or_take_profit", "volume_weighted_pips": -12.0, "risk_pips": 12.0,
                                       "volume_weighted_risk_pips": 12.0})
  joined = await sql.row(
    """
    SELECT s.opportunity_id, s.final_stop_pips, s.room_r, f.entry_price, r.net_r, r.result_method
    FROM auto_trade_plan_snapshots s
    JOIN auto_trade_fills f ON f.group_id = s.plan_id
    JOIN auto_trade_results r ON r.group_id = s.plan_id
    """
  )
  assert joined["opportunity_id"] == "opp_aa" and joined["final_stop_pips"] == 12.0 and joined["entry_price"] == 1.1
  assert joined["net_r"] == pytest.approx(-1.0) and joined["result_method"] == "fills_v1"


@pytest.mark.asyncio
async def test_the_snapshot_survives_a_restart_and_a_schema_bootstrap(sql):
  await store.record_plan_snapshot(_snapshot())
  await store.close_pool()  # the process restarts
  await store.init_db()  # and bootstraps again: CREATE IF NOT EXISTS, nothing is dropped or rewritten
  assert await sql.val("SELECT count(*) FROM auto_trade_plan_snapshots WHERE plan_id = 'v8:go_opp_aa'") == 1
  assert await store.record_plan_snapshot(_snapshot()) is False


@pytest.mark.asyncio
async def test_distinct_plans_of_one_opportunity_family_are_distinct_rows(sql):
  await store.record_plan_snapshot(_snapshot())
  await store.record_plan_snapshot(_snapshot(plan=_plan(plan_id="v8:go_opp_bb"), match=_match(match_id="go_opp_bb"), opportunity_id="opp_bb"))
  assert await sql.val("SELECT count(*) FROM auto_trade_plan_snapshots") == 2

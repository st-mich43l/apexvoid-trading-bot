"""Same-direction group and cumulative-risk exposure (instrument-owned, atomic).

Incident: production 2026-10-12 USDJPY, four same-direction range-scalp SELLs admitted (replayed here as Range Box Scalp, the surviving range scalp)
between 06:51 and 09:01 against one rising resistance. See
docs/execution.md (exposure).
"""

from __future__ import annotations

import asyncio
import json
import random
from types import SimpleNamespace

import fakeredis
import pytest

from app.autotrade import group_exposure as ge
from app.autotrade.group_exposure import (
  EXPOSURE_UNKNOWN,
  SAME_DIRECTION_GROUP_LIMIT,
  SAME_DIRECTION_RISK_BUDGET,
  SAME_THESIS_ACTIVE,
  SYMBOL_RISK_BUDGET,
  GroupExposure,
  GroupExposureUnavailable,
  IncomingGroup,
  admit_group,
  evaluate_group_exposure,
  lots_for_equity,
  planned_lots,
  release_group_slot,
  reservation_key,
  runtime_group_risk,
  stop_distance_pips,
)
from app.runtime.instruments import (
  EffectiveInstrumentError,
  SameDirectionLimits,
  SameDirectionPolicy,
  _same_direction_policy,
)

pytestmark = pytest.mark.no_database

NOW = 1_791_800_000
# USDJPY: 0.01 pip, 6.27 USD per pip per lot, 10,000,000 broker volume per lot.
USDJPY = SimpleNamespace(pip_size=0.01, pip_value_per_lot=6.27, volume_units_per_lot=10_000_000)
XAU = SimpleNamespace(pip_size=0.1, pip_value_per_lot=10.0, volume_units_per_lot=10_000)


def _policy(max_groups=1, cum=None, symbol_risk=None, **kwargs) -> SameDirectionPolicy:
  return SameDirectionPolicy(
    symbol="USDJPY",
    limits=SameDirectionLimits(max_groups=max_groups, max_cumulative_risk=cum, max_symbol_risk=symbol_risk),
    **kwargs,
  )


def _client():
  return fakeredis.FakeAsyncRedis(decode_responses=True)


def _incoming(setup="go_opp_new", direction="SELL", risk=25.0, thesis="thesis-new",
              structural="range:resistance:1", strategy="Range Box Scalp", symbol="USDJPY"):
  return IncomingGroup(
    setup_id=setup, plan_id=f"v8:{setup}", symbol=symbol, direction=direction, strategy=strategy,
    thesis_id=thesis, structural_id=structural, risk_amount=risk,
  )


async def _seed_snapshot(client, symbol="USDJPY", groups=(), *, now=NOW, age=5, ready=True):
  await client.set(f"auto_trade:executor_snapshot:{symbol}", json.dumps({
    "symbol": symbol, "ready": ready, "group_ids": list(groups), "updated_at": now - age,
    "account_equity": 2005.63,
  }))


async def _seed_runtime(client, *states):
  ids = [state["PlanId"] for state in states]
  await client.set("execution:trade_plan_runtime_ids", ",".join(ids))
  for state in states:
    await client.set(f"execution:plan_runtime:{state['PlanId']}", json.dumps(state))


def _state(setup="a", symbol="USDJPY", direction="SELL", *, stage="FullyOpen", group_stage="fully_open",
           fill=158.40, stop=158.58, lots=0.11, volume_units=10_000_000, thesis="t-a", booked=-1,
           remaining=None, legs=None, **extra):
  volume = int(lots * volume_units)
  state = {
    "PlanId": f"v8:{setup}", "SetupId": setup, "ThesisId": thesis, "Symbol": symbol, "Direction": direction,
    "Stage": stage, "GroupStage": group_stage, "CurrentStop": stop, "GroupAbsoluteStop": stop,
    "HighestBookedTargetIndex": booked, "TotalIntendedVolume": volume, "TotalFilledVolume": volume,
    "RemainingVolume": volume if remaining is None else remaining, "GroupWeightedFillPrice": fill,
    "EntryFillPrice": fill, "IntendedEntryPrice": fill,
  }
  if legs is not None:
    state["Legs"] = legs
  state.update(extra)
  return state


# ---------------------------------------------------------------------------
# Sizing mirror
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  ("equity", "fx", "expected"),
  [
    (199.99, False, 0.0),
    (200, False, 0.02),
    (500, False, 0.04),
    (600, False, 0.10),
    (1000, False, 0.10),
    (1300, False, 0.12),
    (2000, False, 0.15),
    (2005.63, True, 0.15),
    (2005.63, False, 0.15),
    (2500, True, 0.18),
    (3000, True, 0.20),
    (3500, True, 0.23),
    (3500, False, 0.26),
    (5000, True, 0.30),
    (9000, False, 0.30),
  ],
)
def test_the_equity_table_mirrors_the_executor(equity, fx, expected):
  assert lots_for_equity(equity, fx=fx) == pytest.approx(expected)


def test_planned_lots_apply_the_risk_multiplier_like_the_executor():
  # equity table lots x multiplier, a multiplier above 1 under $2000 is 1.5.
  assert planned_lots(symbol="USDJPY", equity=2005.63, sizing_mode="equity_table", risk_multiplier=0.6,
                      risk_percent=1, stop_pips=18, pip_value_per_lot=6.27) == pytest.approx(0.09)
  assert planned_lots(symbol="XAU", equity=1500, sizing_mode="equity_table", risk_multiplier=2.0,
                      risk_percent=1, stop_pips=40, pip_value_per_lot=10) == pytest.approx(0.18)
  assert planned_lots(symbol="USDJPY", equity=None, sizing_mode="equity_table", risk_multiplier=1,
                      risk_percent=1, stop_pips=18, pip_value_per_lot=6.27) is None
  assert planned_lots(symbol="USDJPY", equity=2000, sizing_mode="risk", risk_multiplier=1,
                      risk_percent=1.0, stop_pips=20, pip_value_per_lot=6.27) == pytest.approx(2000 * 0.01 / (20 * 6.27))


def test_stop_distance_uses_the_entry_that_loses_most():
  assert stop_distance_pips("SELL", [158.30, 158.40], 158.60, 0.01) == pytest.approx(30.0)
  assert stop_distance_pips("BUY", [158.30, 158.40], 158.10, 0.01) == pytest.approx(30.0)
  assert stop_distance_pips("SELL", [158.70], 158.60, 0.01) == 0.0


# ---------------------------------------------------------------------------
# Remaining risk from the runtime state
# ---------------------------------------------------------------------------


def _risk(state):
  return runtime_group_risk(state, pip_size=0.01, pip_value_per_lot=6.27, volume_units_per_lot=10_000_000)


def test_open_group_risk_is_lots_times_stop_distance_times_pip_value():
  risk, lots = _risk(_state(fill=158.40, stop=158.58, lots=0.11))
  assert lots == pytest.approx(0.11)
  assert risk == pytest.approx(0.11 * 18 * 6.27)


def test_risk_is_remaining_volume_after_a_partial_close_not_the_original():
  risk, lots = _risk(_state(fill=158.40, stop=158.58, lots=0.20, remaining=1_000_000, booked=0))
  assert lots == pytest.approx(0.10)
  assert risk == pytest.approx(0.10 * 18 * 6.27)


def test_a_stop_moved_to_breakeven_leaves_no_loss_but_the_group_still_exists():
  risk, lots = _risk(_state(fill=158.40, stop=158.40, lots=0.11, booked=0))
  assert risk == pytest.approx(0.0)
  assert lots == pytest.approx(0.11)
  risk, _ = _risk(_state(fill=158.40, stop=158.30, lots=0.11, booked=1))
  assert risk == pytest.approx(0.0)  # stop beyond entry in profit: no loss at the stop


def test_unfilled_legs_count_at_their_intended_price_and_closed_legs_not_at_all():
  legs = [
    {"LegId": "L1", "Stage": "filled", "IntendedVolume": 800_000, "FilledVolume": 800_000,
     "RemainingVolume": 800_000, "FillPrice": 158.40, "IntendedPrice": 158.40},
    {"LegId": "L2", "Stage": "pending", "IntendedVolume": 200_000, "FilledVolume": 0,
     "RemainingVolume": 0, "IntendedPrice": 158.50},
    {"LegId": "L3", "Stage": "cancelled", "IntendedVolume": 500_000, "FilledVolume": 0,
     "RemainingVolume": 0, "IntendedPrice": 158.55},
  ]
  risk, lots = _risk(_state(stop=158.58, legs=legs))
  assert lots == pytest.approx(0.10)
  assert risk == pytest.approx(0.08 * 18 * 6.27 + 0.02 * 8 * 6.27)


def test_a_state_without_a_stop_or_volume_is_unknown_never_zero():
  assert _risk(_state(stop=0, **{"GroupAbsoluteStop": 0})) == (None, None)
  pending = _state(stage="Received", group_stage="received", lots=0)
  pending["TotalIntendedVolume"] = 0
  pending["TotalFilledVolume"] = 0
  pending["RemainingVolume"] = 0
  assert _risk(pending) == (None, None)


# ---------------------------------------------------------------------------
# Pure decision
# ---------------------------------------------------------------------------


def _group(setup, direction="SELL", risk=25.0, thesis=None, structural=None, source="runtime", booked=None):
  return GroupExposure(
    plan_id=f"v8:{setup}", setup_id=setup, symbol="USDJPY", direction=direction, source=source,
    thesis_id=thesis, structural_id=structural, risk_amount=risk, risk_basis="runtime" if risk is not None else "unknown",
    highest_booked_target_index=booked,
  )


def test_the_first_group_is_admitted():
  assert evaluate_group_exposure(_policy(), _incoming(), []).allowed


def test_a_second_same_direction_group_is_blocked_by_a_one_group_cap():
  decision = evaluate_group_exposure(_policy(), _incoming(), [_group("a")])
  assert not decision.allowed and decision.reason_code == SAME_DIRECTION_GROUP_LIMIT
  assert decision.measured["same_direction_groups"] == 1
  assert decision.measured["existing_plan_ids"] == ["v8:a"]


def test_two_groups_fit_a_two_group_cap_and_a_third_does_not():
  policy = _policy(max_groups=2)
  assert evaluate_group_exposure(policy, _incoming(), [_group("a")]).allowed
  assert not evaluate_group_exposure(policy, _incoming(), [_group("a"), _group("b")]).allowed


def test_the_cumulative_risk_budget_counts_real_money():
  policy = _policy(max_groups=3, cum=60.0)
  assert evaluate_group_exposure(policy, _incoming(risk=30), [_group("a", risk=25)]).allowed
  decision = evaluate_group_exposure(policy, _incoming(risk=36), [_group("a", risk=25)])
  assert not decision.allowed and decision.reason_code == SAME_DIRECTION_RISK_BUDGET


def test_the_symbol_budget_includes_the_opposite_direction_the_direction_budget_does_not():
  policy = _policy(max_groups=None, cum=100.0, symbol_risk=70.0)
  opposite = _group("o", direction="BUY", risk=50.0)
  assert evaluate_group_exposure(policy, _incoming(risk=60), [opposite]).reason_code == SYMBOL_RISK_BUDGET
  assert evaluate_group_exposure(policy, _incoming(risk=20), [opposite]).allowed


def test_an_opposite_group_does_not_consume_the_same_direction_count():
  assert evaluate_group_exposure(_policy(), _incoming(), [_group("o", direction="BUY")]).allowed


def test_the_same_thesis_or_structure_is_a_duplicate_even_with_room():
  policy = _policy(max_groups=5)
  by_thesis = evaluate_group_exposure(policy, _incoming(thesis="t"), [_group("a", thesis="t")])
  assert by_thesis.reason_code == SAME_THESIS_ACTIVE
  by_structure = evaluate_group_exposure(policy, _incoming(structural="s"), [_group("a", structural="s")])
  assert by_structure.reason_code == SAME_THESIS_ACTIVE
  off = _policy(max_groups=5, duplicate_thesis_block=False)
  assert evaluate_group_exposure(off, _incoming(thesis="t"), [_group("a", thesis="t")]).allowed


def test_a_group_with_unknown_risk_fails_closed_under_a_money_limit_only():
  unknown = _group("a", risk=None)
  assert evaluate_group_exposure(_policy(max_groups=3, cum=100.0), _incoming(), [unknown]).reason_code == EXPOSURE_UNKNOWN
  # A count-only policy does not need its risk, but it still counts the group.
  assert evaluate_group_exposure(_policy(max_groups=3), _incoming(), [unknown]).allowed
  assert not evaluate_group_exposure(_policy(max_groups=1), _incoming(), [unknown]).allowed


def test_an_unknown_incoming_risk_fails_closed_under_a_money_limit():
  decision = evaluate_group_exposure(_policy(max_groups=3, cum=100.0), _incoming(risk=None), [])
  assert decision.reason_code == EXPOSURE_UNKNOWN


def test_unknown_reasons_always_refuse():
  decision = evaluate_group_exposure(_policy(), _incoming(), [], unknown_reasons=["executor_snapshot_stale:400s"])
  assert decision.reason_code == EXPOSURE_UNKNOWN


def test_a_booked_tp_does_not_reopen_the_slot_unless_the_policy_says_so():
  risk_free = _group("a", risk=0.0, booked=1)
  assert not evaluate_group_exposure(_policy(), _incoming(), [risk_free]).allowed
  lenient = _policy(risk_free_groups_count=False)
  assert evaluate_group_exposure(lenient, _incoming(), [risk_free]).allowed
  # but a group that still risks money always counts
  assert not evaluate_group_exposure(lenient, _incoming(), [_group("a", risk=10.0, booked=1)]).allowed


def test_strategy_overrides_apply_to_that_strategy_only():
  policy = _policy(max_groups=1, strategy_overrides=(("key_level", SameDirectionLimits(max_groups=2)),))
  existing = [_group("a")]
  assert not evaluate_group_exposure(policy, _incoming(strategy="Range Box Scalp"), existing).allowed
  assert evaluate_group_exposure(policy, _incoming(strategy="Key Level"), existing).allowed


def test_the_incoming_setup_never_blocks_itself():
  assert evaluate_group_exposure(_policy(), _incoming(setup="a"), [_group("a")]).allowed


# ---------------------------------------------------------------------------
# Loading the live picture
# ---------------------------------------------------------------------------


async def _load(client, policy=None, symbol="USDJPY", units=USDJPY, now=NOW):
  return await ge.load_symbol_exposure(client, symbol=symbol, units=units, policy=policy or _policy(), now=now)


@pytest.mark.asyncio
async def test_an_open_group_is_loaded_with_real_risk():
  client = _client()
  await _seed_snapshot(client, groups=["v8:a"])
  await _seed_runtime(client, _state("a"))
  exposure = await _load(client)
  assert not exposure.unknown_reasons
  assert [(g.setup_id, g.direction) for g in exposure.groups] == [("a", "SELL")]
  assert exposure.groups[0].risk_amount == pytest.approx(0.11 * 18 * 6.27)


@pytest.mark.asyncio
async def test_pending_submitted_and_partially_open_groups_all_count():
  client = _client()
  await _seed_snapshot(client, groups=["v8:p", "v8:s", "v8:h"])
  pending = _state("p", stage="Received", group_stage="received")
  submitted = _state("s", stage="Submitted", group_stage="submitted")
  partial = _state("h", stage="PartiallyOpen", group_stage="partially_open")
  await _seed_runtime(client, pending, submitted, partial)
  exposure = await _load(client)
  assert {g.setup_id for g in exposure.groups} == {"p", "s", "h"}


@pytest.mark.asyncio
async def test_a_multi_leg_group_is_one_group():
  client = _client()
  legs = [
    {"LegId": "L1", "Stage": "filled", "IntendedVolume": 800_000, "FilledVolume": 800_000, "RemainingVolume": 800_000,
     "FillPrice": 158.40, "IntendedPrice": 158.40},
    {"LegId": "L2", "Stage": "filled", "IntendedVolume": 200_000, "FilledVolume": 200_000, "RemainingVolume": 200_000,
     "FillPrice": 158.41, "IntendedPrice": 158.41},
  ]
  await _seed_snapshot(client, groups=["v8:a"])
  await _seed_runtime(client, _state("a", legs=legs))
  exposure = await _load(client)
  assert len(exposure.groups) == 1


@pytest.mark.asyncio
async def test_closed_and_other_symbol_groups_are_ignored_and_aliases_are_normalised():
  client = _client()
  await _seed_snapshot(client, symbol="XAU", groups=["v8:x"])
  closed = _state("c", symbol="XAU", group_stage="closed", stage="Closed")
  other = _state("o", symbol="EURUSD")
  xau = _state("x", symbol="XAUUSD", fill=4100.0, stop=4110.0, lots=0.10, volume_units=10_000)
  await _seed_runtime(client, closed, other, xau)
  exposure = await _load(client, symbol="XAU", units=XAU)
  assert [(g.setup_id, g.symbol) for g in exposure.groups] == [("x", "XAU")]


@pytest.mark.asyncio
async def test_a_plan_listed_twice_is_one_group():
  client = _client()
  await _seed_snapshot(client, groups=["v8:a"])
  state = _state("a")
  await client.set("execution:trade_plan_runtime_ids", "v8:a,v8:a")
  await client.set("execution:plan_runtime:v8:a", json.dumps(state))
  exposure = await _load(client)
  assert len(exposure.groups) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "stale", "unreadable", "not_ready"])
async def test_an_executor_snapshot_that_cannot_be_trusted_is_unknown_exposure(failure):
  client = _client()
  if failure == "stale":
    await _seed_snapshot(client, age=900)
  elif failure == "unreadable":
    await client.set("auto_trade:executor_snapshot:USDJPY", "{not json")
  elif failure == "not_ready":
    await _seed_snapshot(client, ready=False)
  exposure = await _load(client)
  assert exposure.unknown_reasons
  decision = evaluate_group_exposure(_policy(), _incoming(), exposure.groups, unknown_reasons=exposure.unknown_reasons)
  assert decision.reason_code == EXPOSURE_UNKNOWN


@pytest.mark.asyncio
async def test_a_broker_group_with_no_runtime_state_is_live_exposure_of_unknown_size():
  client = _client()
  await _seed_snapshot(client, groups=["v8:ghost"])
  exposure = await _load(client)
  assert [g.source for g in exposure.groups] == ["broker_snapshot"]
  assert any(reason.startswith("broker_group_without_runtime") for reason in exposure.unknown_reasons)
  # the ghost counts on both sides: an opposite incoming group is not free either
  assert not evaluate_group_exposure(_policy(), _incoming(direction="BUY"), exposure.groups).allowed


@pytest.mark.asyncio
async def test_an_unreadable_runtime_state_of_a_snapshot_group_fails_closed():
  client = _client()
  await _seed_snapshot(client, groups=["v8:a"])
  await client.set("execution:trade_plan_runtime_ids", "v8:a")
  await client.set("execution:plan_runtime:v8:a", "{broken")
  exposure = await _load(client)
  assert any(reason.startswith("runtime_unreadable") for reason in exposure.unknown_reasons)


# ---------------------------------------------------------------------------
# Atomic admission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_first_group_reserves_a_slot_and_the_second_is_refused():
  client = _client()
  await _seed_snapshot(client)
  first = await admit_group(client, policy=_policy(), incoming=_incoming("one"), units=USDJPY, now=NOW)
  second = await admit_group(client, policy=_policy(), incoming=_incoming("two", thesis="t2", structural="s2"),
                             units=USDJPY, now=NOW + 5)
  assert first.allowed
  assert not second.allowed and second.reason_code == SAME_DIRECTION_GROUP_LIMIT
  assert await client.hlen(reservation_key("USDJPY", "SELL")) == 1


@pytest.mark.asyncio
async def test_a_retried_admission_of_the_same_setup_is_idempotent():
  client = _client()
  await _seed_snapshot(client)
  for _ in range(3):
    decision = await admit_group(client, policy=_policy(), incoming=_incoming("one"), units=USDJPY, now=NOW)
    assert decision.allowed
  assert await client.hlen(reservation_key("USDJPY", "SELL")) == 1


@pytest.mark.asyncio
async def test_concurrent_admissions_cannot_both_claim_the_last_slot():
  client = _client()
  await _seed_snapshot(client)

  async def attempt(index):
    return await admit_group(
      client, policy=_policy(max_groups=2), incoming=_incoming(f"c{index}", thesis=f"t{index}", structural=f"s{index}"),
      units=USDJPY, now=NOW,
    )

  decisions = await asyncio.gather(*(attempt(i) for i in range(25)))
  assert sum(1 for d in decisions if d.allowed) == 2
  assert {d.reason_code for d in decisions if not d.allowed} == {SAME_DIRECTION_GROUP_LIMIT}
  assert await client.hlen(reservation_key("USDJPY", "SELL")) == 2


@pytest.mark.asyncio
async def test_concurrent_admissions_respect_the_money_budget():
  client = _client()
  await _seed_snapshot(client)
  policy = _policy(max_groups=None, cum=70.0)

  async def attempt(index):
    return await admit_group(
      client, policy=policy, incoming=_incoming(f"c{index}", risk=30.0, thesis=f"t{index}", structural=f"s{index}"),
      units=USDJPY, now=NOW,
    )

  decisions = await asyncio.gather(*(attempt(i) for i in range(12)))
  assert sum(1 for d in decisions if d.allowed) == 2  # 2 x 30 <= 70 < 3 x 30


@pytest.mark.asyncio
async def test_a_reservation_is_not_counted_twice_once_the_plan_is_in_the_runtime():
  client = _client()
  await _seed_snapshot(client)
  first = await admit_group(client, policy=_policy(max_groups=2), incoming=_incoming("one"), units=USDJPY, now=NOW)
  assert first.allowed
  # the executor picks the plan up: it is in the runtime and in the broker snapshot
  await _seed_snapshot(client, groups=["v8:one"], now=NOW + 20)
  await _seed_runtime(client, _state("one", thesis="thesis-new"))
  second = await admit_group(
    client, policy=_policy(max_groups=2), incoming=_incoming("two", thesis="t2", structural="s2"), units=USDJPY, now=NOW + 20,
  )
  assert second.allowed, second  # one live group + the new one = 2 <= 2, not 3
  assert second.measured["same_direction_groups"] == 1
  # the superseded reservation is not counted, and is removed only once it is old
  assert await client.hlen(reservation_key("USDJPY", "SELL")) == 2
  await _seed_snapshot(client, groups=["v8:one"], now=NOW + 130)
  await admit_group(client, policy=_policy(max_groups=2), incoming=_incoming("three", thesis="t3", structural="s3"), units=USDJPY, now=NOW + 130)
  assert "one" not in await client.hkeys(reservation_key("USDJPY", "SELL"))


@pytest.mark.asyncio
async def test_publication_failure_releases_the_slot():
  client = _client()
  await _seed_snapshot(client)
  assert (await admit_group(client, policy=_policy(), incoming=_incoming("one"), units=USDJPY, now=NOW)).allowed
  await release_group_slot(client, symbol="USDJPY", direction="SELL", setup_id="one")
  assert (await admit_group(client, policy=_policy(), incoming=_incoming("two", thesis="t2", structural="s2"),
                            units=USDJPY, now=NOW + 1)).allowed


@pytest.mark.asyncio
async def test_a_crashed_worker_cannot_hold_a_slot_past_the_ttl():
  client = _client()
  await _seed_snapshot(client, now=NOW)
  policy = _policy(reservation_ttl_seconds=600)
  assert (await admit_group(client, policy=policy, incoming=_incoming("one"), units=USDJPY, now=NOW)).allowed
  # published and merely slow: still held inside the ttl
  await client.set("execution:plan:v8:one", "{}")
  await _seed_snapshot(client, now=NOW + 300)
  inside = await admit_group(client, policy=policy, incoming=_incoming("two", thesis="t2", structural="s2"), units=USDJPY, now=NOW + 300)
  assert not inside.allowed
  await _seed_snapshot(client, now=NOW + 601)
  after = await admit_group(client, policy=policy, incoming=_incoming("two", thesis="t2", structural="s2"), units=USDJPY, now=NOW + 601)
  assert after.allowed


@pytest.mark.asyncio
async def test_a_plan_that_never_reached_the_stream_frees_its_slot_after_the_grace():
  client = _client()
  await _seed_snapshot(client, now=NOW)
  assert (await admit_group(client, policy=_policy(), incoming=_incoming("one"), units=USDJPY, now=NOW)).allowed
  await _seed_snapshot(client, now=NOW + 60)
  early = await admit_group(client, policy=_policy(), incoming=_incoming("two", thesis="t2", structural="s2"), units=USDJPY, now=NOW + 60)
  assert not early.allowed
  await _seed_snapshot(client, now=NOW + 120)
  late = await admit_group(client, policy=_policy(), incoming=_incoming("two", thesis="t2", structural="s2"), units=USDJPY, now=NOW + 120)
  assert late.allowed


@pytest.mark.asyncio
async def test_a_plan_the_executor_rejected_frees_its_slot_immediately():
  client = _client()
  await _seed_snapshot(client, now=NOW)
  assert (await admit_group(client, policy=_policy(), incoming=_incoming("one"), units=USDJPY, now=NOW)).allowed
  await client.set("execution:plan:v8:one", "{}")
  await client.set("execution:plan_ack:v8:one", json.dumps({"state": "rejected", "plan_id": "v8:one"}))
  await _seed_snapshot(client, now=NOW + 10)
  after = await admit_group(client, policy=_policy(), incoming=_incoming("two", thesis="t2", structural="s2"), units=USDJPY, now=NOW + 10)
  assert after.allowed


@pytest.mark.asyncio
async def test_a_restart_reconstructs_exposure_from_redis_not_from_memory():
  client = _client()
  await _seed_snapshot(client, groups=["v8:a"])
  await _seed_runtime(client, _state("a"))
  # a fresh process has no memory of "a": the cap still holds
  decision = await admit_group(client, policy=_policy(), incoming=_incoming("two"), units=USDJPY, now=NOW)
  assert not decision.allowed and decision.reason_code == SAME_DIRECTION_GROUP_LIMIT


@pytest.mark.asyncio
async def test_symbols_and_directions_are_independent_books():
  client = _client()
  await _seed_snapshot(client, "USDJPY")
  await _seed_snapshot(client, "EURUSD")
  assert (await admit_group(client, policy=_policy(), incoming=_incoming("a"), units=USDJPY, now=NOW)).allowed
  eur = SameDirectionPolicy(symbol="EURUSD", limits=SameDirectionLimits(max_groups=1))
  assert (await admit_group(client, policy=eur, incoming=_incoming("b", symbol="EURUSD", thesis="tb", structural="sb"),
                            units=USDJPY, now=NOW)).allowed
  # same symbol, opposite direction keeps its own (existing) policy: this one does not count it
  assert (await admit_group(client, policy=_policy(), incoming=_incoming("c", direction="BUY", thesis="tc", structural="sc"),
                            units=USDJPY, now=NOW)).allowed


@pytest.mark.asyncio
async def test_an_unknown_executor_picture_refuses_without_reserving():
  client = _client()  # no executor snapshot at all
  decision = await admit_group(client, policy=_policy(), incoming=_incoming("a"), units=USDJPY, now=NOW)
  assert not decision.allowed and decision.reason_code == EXPOSURE_UNKNOWN
  assert await client.hlen(reservation_key("USDJPY", "SELL")) == 0


@pytest.mark.asyncio
async def test_redis_that_cannot_run_the_script_refuses_admission():
  class Broken:
    async def get(self, key):
      return json.dumps({"ready": True, "group_ids": [], "updated_at": NOW})

    async def hgetall(self, key):
      return {}

    async def eval(self, *args):
      raise ConnectionError("redis down")

  with pytest.raises(GroupExposureUnavailable):
    await admit_group(Broken(), policy=_policy(), incoming=_incoming("a"), units=USDJPY, now=NOW)


# ---------------------------------------------------------------------------
# The script and the pure decision agree
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_script_and_the_pure_decision_agree_on_random_books():
  rng = random.Random(7)
  checked = 0
  for case in range(120):
    client = _client()
    groups = []
    reservations = []
    for index in range(rng.randint(0, 4)):
      direction = rng.choice(["BUY", "SELL"])
      risk = rng.choice([None, 0.0, 10.0, 20.0, 35.0])
      state = _state(f"g{index}", direction=direction, thesis=f"gt{index % 2}", fill=158.40 if direction == "SELL" else 158.20,
                     stop=158.58 if direction == "SELL" else 158.02, lots=rng.choice([0.05, 0.11, 0.2]))
      if rng.random() < 0.5:
        groups.append(state)
      else:
        reservations.append((f"r{index}", direction, risk, f"rt{index % 2}"))
    snapshot_ids = [state["PlanId"] for state in groups]
    await _seed_snapshot(client, groups=snapshot_ids)
    if groups:
      await _seed_runtime(client, *groups)
    for setup, direction, risk, thesis in reservations:
      stub = _incoming(setup, direction=direction, risk=risk, thesis=thesis, structural=f"rs-{setup}")
      value = ge._encode(NOW + 600, NOW, risk, stub)
      await client.hset(reservation_key("USDJPY", direction), setup, value)
      await client.set(f"execution:plan:v8:{setup}", "{}")
    policy = _policy(
      max_groups=rng.choice([None, 1, 2, 3]), cum=rng.choice([None, 40.0, 80.0]), symbol_risk=rng.choice([None, 60.0, 120.0]),
    )
    if policy.limits.max_groups is None and policy.limits.max_cumulative_risk is None and policy.limits.max_symbol_risk is None:
      continue
    incoming = _incoming("new", direction=rng.choice(["BUY", "SELL"]), risk=rng.choice([None, 10.0, 30.0]),
                         thesis=rng.choice(["tnew", "gt0", "gt1", "rt0", "rt1"]), structural=rng.choice(["snew", "rs-r0", "rs-r1"]))

    exposure = await ge.load_symbol_exposure(client, symbol="USDJPY", units=USDJPY, policy=policy, now=NOW)
    pure_groups = list(exposure.groups) + [
      GroupExposure(plan_id=f"v8:{setup}", setup_id=setup, symbol="USDJPY", direction=direction, source="reservation",
                    thesis_id=thesis, structural_id=f"rs-{setup}", risk_amount=risk, risk_basis="reservation")
      for setup, direction, risk, thesis in reservations
    ]
    pure = evaluate_group_exposure(policy, incoming, pure_groups, unknown_reasons=exposure.unknown_reasons)
    live = await admit_group(client, policy=policy, incoming=incoming, units=USDJPY, now=NOW)
    assert live.allowed == pure.allowed, (case, pure, live)
    if not pure.allowed:
      assert live.reason_code == pure.reason_code, (case, pure, live)
    checked += 1
  assert checked > 60


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_no_declaration_means_the_legacy_behaviour():
  assert _same_direction_policy("USDJPY", {"opposite_position": {"allowed": False}}) is None
  assert _same_direction_policy("USDJPY", None) is None


def test_a_declared_policy_is_parsed_strictly():
  policy = _same_direction_policy("USDJPY", {"same_direction": {
    "max_groups": 1, "max_cumulative_risk": 45.0,
    "strategy_overrides": {"Key Level": {"max_groups": 2}},
  }})
  assert policy.limits == SameDirectionLimits(max_groups=1, max_cumulative_risk=45.0, max_symbol_risk=None)
  assert policy.limits_for("Key Level").max_groups == 2
  assert policy.limits_for("Range Box Scalp").max_groups == 1
  assert policy.risk_free_groups_count and policy.duplicate_thesis_block


@pytest.mark.parametrize(
  "bad",
  [
    {"max_groups": 1, "reservation_ttl_seconds": 120},  # shorter than the dead-plan grace plus a margin
    {},
    {"max_groups": 0},
    {"max_groups": True},
    {"max_groups": 1.5},
    {"max_cumulative_risk": -1},
    {"max_groups": 1, "unexpected": 1},
    {"max_groups": 1, "reservation_ttl_seconds": 1},
    {"max_groups": 1, "risk_free_groups_count": "yes"},
    {"max_groups": 1, "strategy_overrides": {"x": {}}},
    {"max_groups": 1, "strategy_overrides": []},
  ],
)
def test_an_invalid_declaration_is_a_configuration_error_not_a_silent_default(bad):
  with pytest.raises(EffectiveInstrumentError):
    _same_direction_policy("USDJPY", {"same_direction": bad})


@pytest.mark.real_redis
@pytest.mark.asyncio
async def test_the_script_is_atomic_on_a_real_redis_under_real_concurrency():
  """fakeredis runs the script on one thread; this proves it on the real engine."""
  import os
  import time

  from redis.asyncio import Redis

  configured = os.getenv("REAL_REDIS_URL")
  if not configured:
    pytest.fail("REAL_REDIS_URL is required")
  client = Redis.from_url(f"{configured.rsplit('/', 1)[0]}/13", decode_responses=True)
  await client.flushdb()
  try:
    await _seed_snapshot(client, now=int(time.time()))

    async def attempt(index):
      return await admit_group(
        client, policy=_policy(max_groups=3), incoming=_incoming(f"c{index}", thesis=f"t{index}", structural=f"s{index}"),
        units=USDJPY,
      )

    for _round in range(5):
      await client.delete(reservation_key("USDJPY", "SELL"))
      decisions = await asyncio.gather(*(attempt(i) for i in range(60)))
      assert sum(1 for d in decisions if d.allowed) == 3
      assert await client.hlen(reservation_key("USDJPY", "SELL")) == 3
    assert 0 < await client.ttl(reservation_key("USDJPY", "SELL")) <= 660
  finally:
    await client.flushdb()
    await client.aclose()


# ---------------------------------------------------------------------------
# Defects found by the independent review (each reproduced before it was fixed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_worker_with_a_stale_runtime_read_cannot_take_a_slot_another_worker_just_filled():
  """W1 read the runtime before plan P was visible; W2's admission must not delete P's reservation."""
  client = _client()
  await _seed_snapshot(client)
  assert (await admit_group(client, policy=_policy(), incoming=_incoming("p"), units=USDJPY, now=NOW)).allowed
  stale = await ge.load_symbol_exposure(client, symbol="USDJPY", units=USDJPY, policy=_policy(), now=NOW)
  assert stale.groups == []  # W1's picture: P is not in the runtime yet
  await _seed_snapshot(client, groups=["v8:p"], now=NOW + 2)
  await _seed_runtime(client, _state("p", thesis="thesis-new"))
  fresh = await admit_group(client, policy=_policy(), incoming=_incoming("q", thesis="tq", structural="sq"), units=USDJPY, now=NOW + 3)
  assert not fresh.allowed and fresh.reason_code == SAME_DIRECTION_GROUP_LIMIT
  late = await ge.reserve_group_slot(
    client, policy=_policy(), incoming=_incoming("r", thesis="tr", structural="sr"), exposure=stale, now=NOW + 4,
  )
  assert not late.allowed, "the stale worker found the slot free: two groups under max_groups=1"


@pytest.mark.asyncio
async def test_a_plan_the_executor_has_accepted_but_not_sized_keeps_its_planned_risk():
  """A Received plan has no volume in the runtime: the risk planned at admission stands for it."""
  client = _client()
  await _seed_snapshot(client, now=NOW)
  policy = _policy(max_groups=None, cum=60.0)
  assert (await admit_group(client, policy=policy, incoming=_incoming("p", risk=25.0), units=USDJPY, now=NOW)).allowed
  unsized = _state("p", stage="Received", group_stage="received", lots=0)
  for key in ("TotalIntendedVolume", "TotalFilledVolume", "RemainingVolume"):
    unsized[key] = 0
  await _seed_snapshot(client, groups=["v8:p"], now=NOW + 40)
  await _seed_runtime(client, unsized)
  second = await admit_group(client, policy=policy, incoming=_incoming("q", risk=30.0, thesis="tq", structural="sq"), units=USDJPY, now=NOW + 40)
  assert second.allowed, second  # 25 + 30 <= 60: not exposure_unknown
  over = await admit_group(client, policy=policy, incoming=_incoming("r", risk=30.0, thesis="tr", structural="sr"), units=USDJPY, now=NOW + 41)
  assert not over.allowed and over.reason_code == SAME_DIRECTION_RISK_BUDGET
  assert "p" in await client.hkeys(reservation_key("USDJPY", "SELL")), "its reservation is the only record of its risk"


@pytest.mark.asyncio
async def test_a_sticky_recovery_required_group_is_not_a_broker_group_the_runtime_cannot_explain():
  client = _client()
  await _seed_snapshot(client, groups=["v8:rec"])
  await _seed_runtime(client, _state("rec", group_stage="recovery_required", remaining=0))  # the positions are gone
  exposure = await _load(client)
  assert exposure.groups == [] and not exposure.unknown_reasons
  assert (await admit_group(client, policy=_policy(), incoming=_incoming("a"), units=USDJPY, now=NOW)).allowed


@pytest.mark.asyncio
async def test_the_structure_of_a_live_group_comes_from_its_plan_and_blocks_a_duplicate():
  client = _client()
  await _seed_snapshot(client, groups=["v8:a"])
  await _seed_runtime(client, _state("a", thesis="other"))
  await client.set("execution:plan:v8:a", json.dumps({"source_structure": {"structure_id": "range:resistance:1791782100"}}))
  decision = await admit_group(
    client, policy=_policy(max_groups=5), incoming=_incoming("b", thesis="t2", structural="range:resistance:1791782100"), units=USDJPY, now=NOW,
  )
  assert not decision.allowed and decision.reason_code == SAME_THESIS_ACTIVE


def test_a_strategy_override_changes_only_the_limits_it_declares():
  policy = _policy(max_groups=1, cum=40.0, strategy_overrides=(("key_level", SameDirectionLimits(max_groups=2)),))
  merged = policy.limits_for("Key Level")
  assert merged == SameDirectionLimits(max_groups=2, max_cumulative_risk=40.0, max_symbol_risk=None)
  assert policy.limits_for("Range Box Scalp") == policy.limits


@pytest.mark.asyncio
async def test_a_plan_that_already_closed_frees_its_slot():
  """It stopped out before any admission scan saw it live: its ack says so."""
  client = _client()
  await _seed_snapshot(client, now=NOW)
  assert (await admit_group(client, policy=_policy(), incoming=_incoming("a"), units=USDJPY, now=NOW)).allowed
  await client.set("execution:plan:v8:a", "{}")
  await client.set("execution:plan_ack:v8:a", json.dumps({"state": "completed"}))
  await _seed_snapshot(client, now=NOW + 300)
  after = await admit_group(client, policy=_policy(), incoming=_incoming("b", thesis="t2", structural="s2"), units=USDJPY, now=NOW + 300)
  assert after.allowed


def test_a_leg_closed_in_part_counts_its_remaining_volume_not_the_filled_volume():
  legs = [{"LegId": "L1", "Stage": "managing", "IntendedVolume": 2_000_000, "FilledVolume": 2_000_000,
           "RemainingVolume": 1_000_000, "FillPrice": 158.40, "IntendedPrice": 158.40}]
  risk, lots = _risk(_state(stop=158.58, legs=legs))
  assert lots == pytest.approx(0.10)
  assert risk == pytest.approx(0.10 * 18 * 6.27)


@pytest.mark.asyncio
async def test_two_admissions_of_one_thesis_are_one_group_even_with_room():
  client = _client()
  await _seed_snapshot(client)
  policy = _policy(max_groups=5)
  assert (await admit_group(client, policy=policy, incoming=_incoming("a", thesis="same", structural="s1"), units=USDJPY, now=NOW)).allowed
  second = await admit_group(client, policy=policy, incoming=_incoming("b", thesis="same", structural="s2"), units=USDJPY, now=NOW + 1)
  assert not second.allowed and second.reason_code == SAME_THESIS_ACTIVE
  third = await admit_group(client, policy=policy, incoming=_incoming("c", thesis="t3", structural="s1"), units=USDJPY, now=NOW + 2)
  assert third.reason_code == SAME_THESIS_ACTIVE


@pytest.mark.asyncio
async def test_a_strategy_override_still_carries_the_instruments_money_cap():
  client = _client()
  await _seed_snapshot(client)
  policy = _policy(max_groups=1, cum=30.0, strategy_overrides=(("key_level", SameDirectionLimits(max_groups=3)),))
  assert (await admit_group(client, policy=policy, incoming=_incoming("a", strategy="Key Level", risk=20.0), units=USDJPY, now=NOW)).allowed
  over = await admit_group(client, policy=policy, incoming=_incoming("b", strategy="Key Level", risk=20.0, thesis="t2", structural="s2"), units=USDJPY, now=NOW + 1)
  assert not over.allowed and over.reason_code == SAME_DIRECTION_RISK_BUDGET


@pytest.mark.asyncio
async def test_a_plan_that_stays_unsized_past_the_ttl_keeps_its_planned_risk():
  client = _client()
  await _seed_snapshot(client, now=NOW)
  policy = _policy(max_groups=None, cum=60.0, reservation_ttl_seconds=600)
  assert (await admit_group(client, policy=policy, incoming=_incoming("p", risk=25.0), units=USDJPY, now=NOW)).allowed
  unsized = _state("p", stage="Received", group_stage="received", lots=0)
  for key in ("TotalIntendedVolume", "TotalFilledVolume", "RemainingVolume"):
    unsized[key] = 0
  await _seed_runtime(client, unsized)
  for at in (NOW + 300, NOW + 800, NOW + 1500, NOW + 2300):  # every scan inside the (refreshed) ttl
    await _seed_snapshot(client, groups=["v8:p"], now=at)
    decision = await admit_group(client, policy=policy, incoming=_incoming(f"q{at}", risk=20.0, thesis=f"t{at}", structural=f"s{at}"), units=USDJPY, now=at)
    assert decision.reason_code != EXPOSURE_UNKNOWN, (at, decision)
    await release_group_slot(client, symbol="USDJPY", direction="SELL", setup_id=f"q{at}")


@pytest.mark.asyncio
async def test_a_recovery_required_group_with_open_orders_is_live_exposure_of_unknown_size():
  client = _client()
  await _seed_snapshot(client, groups=["v8:rec"])
  state = _state("rec", group_stage="recovery_required")
  state["PendingOrderIds"] = [51329162]
  await _seed_runtime(client, state)
  exposure = await _load(client)
  assert any(reason.startswith("recovery_required_with_open_orders") for reason in exposure.unknown_reasons)

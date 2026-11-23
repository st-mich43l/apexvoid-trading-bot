"""X4: a risk-free XAU group stops counting toward the same-direction limit (``risk_free_groups_count: false`` on the XAU pack).

Run from ``algo-bot``:  ``python -m unittest tests.test_x4_risk_free_groups``  (the Redis-backed cases need ``fakeredis`` and ``lupa``).
"Risk-free" is the executor's own point: ``TradePlanRuntime.HasBlockingSameDirectionLivePlan`` stops blocking at ``HighestBookedTargetIndex >= 1`` (the index is -1 with nothing
booked, 0 once TP1 is booked, 1 once TP2 is), so a group is risk-free here once TP2 is booked and its live stop leaves at most the break-even buffer of remaining risk. Python never
admits a plan the executor would then reject. A missing risk or booking keeps counting the group.
"""
from __future__ import annotations

import datetime
import json
import os
import time
import unittest

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.autotrade import group_exposure as ge  # noqa: E402
from app.core import instrument_geometry  # noqa: E402

try:
  import fakeredis.aioredis as fake_aioredis  # noqa: F401
  import lupa  # noqa: F401
  HAVE_FAKEREDIS = True
except ImportError:  # pragma: no cover
  HAVE_FAKEREDIS = False

XAU_POLICY = instrument_geometry.same_direction_policy("XAU")
FX_POLICY = instrument_geometry.same_direction_policy("EURUSD")
XAU_UNITS = instrument_geometry.instrument_runtime("XAU").units
# be_buffer_ticks 6 x tick 0.01 = 0.06 price = 0.6 pip; per lot at $10 a pip that is $6.
ALLOWANCE_PER_LOT = 6.0


def group(n, *, risk, booked, lots=0.04, allowance=None, thesis=None, structural=None, stage="partially_open", **extra):
  return ge.GroupExposure(
    plan_id=f"v8:plan{n}", setup_id=f"plan{n}", symbol="XAU", direction="BUY", source="runtime", thesis_id=thesis, structural_id=structural, stage=stage,
    risk_amount=risk, risk_basis="runtime" if risk is not None else "unknown", highest_booked_target_index=booked, remaining_lots=lots,
    risk_free_allowance=ALLOWANCE_PER_LOT * lots if allowance is None else allowance, **extra,
  )


def incoming(n=99, *, risk=40.0, thesis="t-new", structural="s-new"):
  return ge.IncomingGroup(setup_id=f"plan{n}", plan_id=f"v8:plan{n}", symbol="XAU", direction="BUY", strategy="Supply Demand", thesis_id=thesis, structural_id=structural, risk_amount=risk)


class ShippedConfigTests(unittest.TestCase):
  def test_only_the_xau_pack_lets_a_risk_free_group_go(self):
    self.assertFalse(XAU_POLICY.risk_free_groups_count)
    self.assertEqual(XAU_POLICY.limits.max_groups, 1)
    for pair in ("EURUSD", "GBPUSD", "USDJPY", "GBPJPY"):
      with self.subTest(pair=pair):
        policy = instrument_geometry.same_direction_policy(pair)
        self.assertTrue(policy.risk_free_groups_count)
        self.assertEqual(policy.limits.max_groups, 1)

  def test_the_duplicate_thesis_block_is_kept_on_xau(self):
    self.assertTrue(XAU_POLICY.duplicate_thesis_block)


class RuleTests(unittest.TestCase):
  def test_a_group_after_tp2_with_its_stop_at_break_even_does_not_block(self):
    decision = ge.evaluate_group_exposure(XAU_POLICY, incoming(), [group(1, risk=0.0, booked=1)])
    self.assertTrue(decision.allowed, decision)
    self.assertEqual(decision.measured["skipped_risk_free_plan_ids"], ["v8:plan1"])
    self.assertEqual(decision.measured["same_direction_groups"], 0)

  def test_a_group_after_tp1_only_still_blocks_because_the_executor_would_reject_the_plan(self):
    # (In production Python's own before-TP2 gate refuses first; this is the exposure rule on its own.)
    decision = ge.evaluate_group_exposure(XAU_POLICY, incoming(), [group(1, risk=0.0, booked=0)])
    self.assertFalse(decision.allowed)
    self.assertEqual(decision.reason_code, ge.SAME_DIRECTION_GROUP_LIMIT)
    self.assertEqual(decision.measured["skipped_risk_free_plan_ids"], [])

  def test_a_group_after_tp2_whose_stop_is_still_far_blocks(self):
    decision = ge.evaluate_group_exposure(XAU_POLICY, incoming(), [group(1, risk=18.0, booked=1)])
    self.assertFalse(decision.allowed)
    self.assertEqual(decision.reason_code, ge.SAME_DIRECTION_GROUP_LIMIT)

  def test_a_group_with_nothing_booked_blocks(self):
    for booked in (None, -1):
      with self.subTest(booked=booked):
        self.assertFalse(ge.evaluate_group_exposure(XAU_POLICY, incoming(), [group(1, risk=0.0, booked=None)]).allowed)

  def test_unknown_risk_blocks_whatever_is_booked(self):
    decision = ge.evaluate_group_exposure(XAU_POLICY, incoming(), [group(1, risk=None, booked=1)])
    self.assertFalse(decision.allowed)

  def test_the_break_even_buffer_is_the_most_remaining_risk_a_risk_free_group_may_carry(self):
    lots = 0.04
    at_buffer = group(1, risk=ALLOWANCE_PER_LOT * lots, booked=1, lots=lots)
    above = group(1, risk=ALLOWANCE_PER_LOT * lots + 0.01, booked=1, lots=lots)
    self.assertTrue(ge.is_risk_free(at_buffer))
    self.assertFalse(ge.is_risk_free(above))
    self.assertTrue(ge.evaluate_group_exposure(XAU_POLICY, incoming(), [at_buffer]).allowed)
    self.assertFalse(ge.evaluate_group_exposure(XAU_POLICY, incoming(), [above]).allowed)

  def test_a_group_with_no_allowance_must_be_at_zero(self):
    self.assertTrue(ge.is_risk_free(group(1, risk=0.0, booked=1, allowance=0.0)))
    self.assertFalse(ge.is_risk_free(group(1, risk=0.05, booked=1, allowance=0.0)))

  def test_the_same_thesis_still_blocks_a_duplicate_even_when_the_group_is_risk_free(self):
    owner = group(1, risk=0.0, booked=1, thesis="t-1", structural="s-1")
    same_thesis = ge.evaluate_group_exposure(XAU_POLICY, incoming(thesis="t-1"), [owner])
    self.assertFalse(same_thesis.allowed)
    self.assertEqual(same_thesis.reason_code, ge.SAME_THESIS_ACTIVE)
    same_structure = ge.evaluate_group_exposure(XAU_POLICY, incoming(structural="s-1"), [owner])
    self.assertFalse(same_structure.allowed)

  def test_a_second_live_group_still_blocks_next_to_a_risk_free_one(self):
    decision = ge.evaluate_group_exposure(XAU_POLICY, incoming(), [group(1, risk=0.0, booked=1), group(2, risk=30.0, booked=None)])
    self.assertFalse(decision.allowed)
    self.assertEqual(decision.measured["skipped_risk_free_plan_ids"], ["v8:plan1"])

  def test_fx_is_unchanged_a_risk_free_group_still_counts(self):
    fx_group = ge.GroupExposure(
      plan_id="v8:fx1", setup_id="fx1", symbol="EURUSD", direction="BUY", source="runtime", stage="partially_open", risk_amount=0.0, risk_basis="runtime",
      highest_booked_target_index=3, remaining_lots=0.1, risk_free_allowance=5.0,
    )
    fx_in = ge.IncomingGroup(setup_id="fx2", plan_id="v8:fx2", symbol="EURUSD", direction="BUY", strategy="Key Level", thesis_id="a", structural_id="b", risk_amount=10.0)
    decision = ge.evaluate_group_exposure(FX_POLICY, fx_in, [fx_group])
    self.assertFalse(decision.allowed)
    self.assertEqual(decision.reason_code, ge.SAME_DIRECTION_GROUP_LIMIT)
    self.assertEqual(decision.measured["skipped_risk_free_plan_ids"], [])

  def test_open_risk_after_admission_never_exceeds_one_groups_planned_risk_plus_the_break_even_buffers(self):
    live = [group(1, risk=0.0, booked=1, lots=0.04), group(2, risk=ALLOWANCE_PER_LOT * 0.03, booked=1, lots=0.03)]
    new = incoming(risk=40.0)
    self.assertTrue(ge.evaluate_group_exposure(XAU_POLICY, new, live).allowed)
    total_open_risk = sum(g.risk_amount for g in live) + new.risk_amount
    self.assertLessEqual(total_open_risk, new.risk_amount + sum(g.risk_free_allowance for g in live) + 1e-9)

  def test_the_recorded_inputs_name_every_blocking_group(self):
    decision = ge.evaluate_group_exposure(XAU_POLICY, incoming(), [group(1, risk=12.5, booked=0, stop_price=3890.0, entry_reference=3900.0)])
    row = decision.measured["existing_groups"][0]
    self.assertEqual(
      {k: row[k] for k in ("plan_id", "highest_booked_target_index", "risk_amount", "stop_price", "entry_reference", "counted")},
      {"plan_id": "v8:plan1", "highest_booked_target_index": 0, "risk_amount": 12.5, "stop_price": 3890.0, "entry_reference": 3900.0, "counted": True},
    )
    self.assertEqual(decision.measured["existing_plan_ids"], ["v8:plan1"])


def runtime_state(*, booked, stop, legs, direction="BUY", plan_id="v8:plan1", symbol="XAU"):
  remaining = sum(leg["RemainingVolume"] for leg in legs)
  filled = [leg for leg in legs if leg["FilledVolume"] > 0 and leg["RemainingVolume"] > 0]
  weighted = sum(leg["FillPrice"] * leg["RemainingVolume"] for leg in filled) / remaining if remaining else None
  return {
    "PlanId": plan_id, "ThesisId": "thesis-1", "SetupId": plan_id[3:], "Symbol": symbol, "Direction": direction, "EntryType": "market_with_limit_scale", "Stage": "PartiallyClosed",
    "GroupStage": "partially_open", "PositionId": 1, "PendingOrderIds": [], "EntryFillPrice": weighted, "GroupWeightedFillPrice": weighted, "RemainingVolume": remaining,
    "CurrentStop": stop, "NextTargetIndex": booked + 1, "BreakEvenApplied": booked >= 0, "HighestBookedTargetIndex": booked, "Legs": legs, "TotalIntendedVolume": 1000,
    "TotalFilledVolume": 1000, "GroupAbsoluteStop": stop, "TerminalReason": None,
  }


def leg(leg_id, *, stage, price, remaining, filled=None):
  filled = remaining if filled is None else filled
  return {"LegId": leg_id, "Stage": stage, "IntendedPrice": price, "FillPrice": price, "IntendedVolume": filled, "FilledVolume": filled, "RemainingVolume": remaining}


@unittest.skipUnless(HAVE_FAKEREDIS, "fakeredis and lupa are not installed")
class ProjectionTests(unittest.IsolatedAsyncioTestCase):
  """The live projection: from the executor runtime state to a GroupExposure, through the real loader and the atomic reservation."""

  async def client_with(self, state):
    client = fake_aioredis.FakeRedis(decode_responses=True)
    await client.set("auto_trade:executor_snapshot:XAU", json.dumps({"account_equity": 5000.0, "updated_at": time.time(), "group_ids": [state["PlanId"]], "ready": True}))
    await client.set("execution:trade_plan_runtime_ids", state["PlanId"])
    await client.set(f"execution:plan_runtime:{state['PlanId']}", json.dumps(state))
    return client

  async def load(self, state):
    client = await self.client_with(state)
    exposure = await ge.load_symbol_exposure(client, symbol="XAU", units=XAU_UNITS, policy=XAU_POLICY)
    self.assertEqual(exposure.unknown_reasons, [])
    return client, exposure, exposure.groups[0]

  async def test_after_tp2_and_the_break_even_move_the_group_reports_both_facts(self):
    # L1 and L2 are drained shallow-first by TP1/TP2; only the deep leg L3 (0.04 lot at 3994.0) is left, its stop at fill + 6 ticks.
    state = runtime_state(booked=1, stop=3994.06, legs=[
      leg("L1", stage="closed", price=3996.0, remaining=0, filled=200), leg("L2", stage="closed", price=3995.0, remaining=0, filled=200),
      leg("L3", stage="filled", price=3994.0, remaining=400),
    ])
    _, _, g = await self.load(state)
    self.assertEqual(g.highest_booked_target_index, 1)
    self.assertEqual(g.risk_amount, 0.0)  # the stop is above the fill: nothing left to lose at it
    self.assertEqual((g.stop_price, g.entry_reference), (3994.06, 3994.0))
    self.assertAlmostEqual(g.risk_free_allowance, 0.6 * 0.04 * 10, places=6)  # 0.6 pip x 0.04 lot x $10 per pip-lot
    self.assertTrue(ge.is_risk_free(g))

  async def test_after_tp1_only_the_group_is_not_risk_free(self):
    state = runtime_state(booked=0, stop=3994.06, legs=[leg("L1", stage="closed", price=3996.0, remaining=0, filled=200), leg("L2", stage="filled", price=3995.0, remaining=200), leg("L3", stage="filled", price=3994.0, remaining=600)])
    _, _, g = await self.load(state)
    self.assertEqual(g.highest_booked_target_index, 0)
    self.assertFalse(ge.is_risk_free(g))

  async def test_a_group_whose_stop_has_not_moved_carries_its_real_risk(self):
    state = runtime_state(booked=1, stop=3980.0, legs=[leg("L3", stage="filled", price=3994.0, remaining=400)])
    _, _, g = await self.load(state)
    self.assertGreater(g.risk_amount, g.risk_free_allowance)
    self.assertFalse(ge.is_risk_free(g))

  async def test_an_unreadable_stop_keeps_the_group_counting(self):
    state = runtime_state(booked=1, stop=0, legs=[leg("L3", stage="filled", price=3994.0, remaining=400)])
    state["GroupAbsoluteStop"] = 0
    _, _, g = await self.load(state)
    self.assertIsNone(g.risk_amount)
    self.assertFalse(ge.is_risk_free(g))

  async def test_the_live_reservation_skips_a_risk_free_group_and_records_the_inputs_of_a_blocker(self):
    free = runtime_state(booked=1, stop=3994.06, legs=[leg("L3", stage="filled", price=3994.0, remaining=400)])
    client, exposure, _ = await self.load(free)
    decision = await ge.reserve_group_slot(client, policy=XAU_POLICY, incoming=incoming(), exposure=exposure)
    self.assertTrue(decision.allowed, decision)
    self.assertEqual(decision.measured["skipped_risk_free_plan_ids"], ["v8:plan1"])

    blocking = runtime_state(booked=0, stop=3994.06, legs=[leg("L3", stage="filled", price=3994.0, remaining=400)])
    client, exposure, _ = await self.load(blocking)
    decision = await ge.reserve_group_slot(client, policy=XAU_POLICY, incoming=incoming(), exposure=exposure)
    self.assertFalse(decision.allowed)
    self.assertEqual(decision.reason_code, ge.SAME_DIRECTION_GROUP_LIMIT)
    row = decision.measured["existing_groups"][0]  # what PR-X0 could not read: who blocked, and why
    self.assertEqual((row["plan_id"], row["highest_booked_target_index"], row["stop_price"], row["entry_reference"], row["counted"]), ("v8:plan1", 0, 3994.06, 3994.0, True))
    self.assertEqual(decision.measured["existing_plan_ids"], ["v8:plan1"])


if HAVE_FAKEREDIS:
  from app.autotrade import worker
  from app.autotrade.go_opportunity_policy import GoOpportunityPolicy
  from app.autotrade.worker import AutoTradeSpot, _publish_trade_plan_v8
  from tests.test_tl_live2_single_price import XAU_M5_BUY, match_for, with_execution


@unittest.skipUnless(HAVE_FAKEREDIS, "fakeredis and lupa are not installed")
class WorkerAdmissionTests(unittest.IsolatedAsyncioTestCase):
  """The real admission path (``worker._publish_trade_plan_v8``) for an XAU BUY next to a live XAU BUY group."""

  def setUp(self):
    self._saved = (worker._news_guard_hit, worker.datetime, worker.record_plan_snapshot)

    async def no_news(symbol, now):
      return None

    async def no_snapshot(*args, **kwargs):
      return None

    worker._news_guard_hit = no_news
    worker.record_plan_snapshot = no_snapshot

  def tearDown(self):
    worker._news_guard_hit, worker.datetime, worker.record_plan_snapshot = self._saved

  async def publish_next_to(self, state):
    symbol, factory, price = XAU_M5_BUY
    client = fake_aioredis.FakeRedis(decode_responses=True)
    raw = with_execution(factory(), price)
    match = match_for(raw)
    created = raw["payload"]["created_at"]
    clock = created + 40

    class PinnedDatetime:
      @staticmethod
      def now(tz=None):
        return datetime.datetime.fromtimestamp(clock, tz)

    worker.datetime = PinnedDatetime
    await GoOpportunityPolicy._store_match(client, match, created + 30)
    await GoOpportunityPolicy._advance_setup(client, match)
    await client.set(f"analysis:zone_book:{symbol}", json.dumps({"symbol": symbol, "generated_at": clock, "barriers": []}))
    for pair in ("XAU", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY"):
      groups = [state["PlanId"]] if pair == "XAU" else []
      await client.set(f"auto_trade:executor_snapshot:{pair}", json.dumps({"account_equity": 10000.0, "updated_at": time.time(), "group_ids": groups, "ready": True}))
    await client.set("execution:trade_plan_runtime_ids", state["PlanId"])
    await client.set(f"execution:plan_runtime:{state['PlanId']}", json.dumps(state))
    spot = AutoTradeSpot(price=3902.9, ts=clock, fresh=True, bid=3902.8, ask=3903.0)
    plan_id = await _publish_trade_plan_v8(client, symbol, spot, match, regime=None, frames=None)
    outcome = None
    for key in await client.keys("auto_trade:route_outcome:*"):
      outcome = json.loads(await client.get(key))
    return plan_id, outcome, client

  async def test_a_new_xau_buy_is_published_next_to_a_group_that_booked_tp2_and_sits_at_break_even(self):
    state = runtime_state(booked=1, stop=3880.06, legs=[leg("L3", stage="filled", price=3880.0, remaining=400)])
    plan_id, _, client = await self.publish_next_to(state)
    self.assertIsNotNone(plan_id)
    self.assertEqual(await client.hget("auto_trade:metrics:XAU", "same_direction_risk_free_skipped"), "1")

  async def test_a_group_that_only_booked_tp1_is_refused_by_the_existing_before_tp2_gate(self):
    state = runtime_state(booked=0, stop=3880.06, legs=[leg("L3", stage="filled", price=3880.0, remaining=400)])
    plan_id, outcome, client = await self.publish_next_to(state)
    self.assertIsNone(plan_id)
    self.assertEqual(outcome["reason_code"], "same_direction_active_before_tp2")  # Python's own stacking gate, which the executor's rule mirrors
    self.assertIsNone(await client.hget("auto_trade:metrics:XAU", "same_direction_risk_free_skipped"))

  async def test_a_tp2_group_whose_stop_has_not_moved_still_blocks_and_the_refusal_records_who_blocked(self):
    state = runtime_state(booked=1, stop=3860.0, legs=[leg("L3", stage="filled", price=3880.0, remaining=400)])
    plan_id, outcome, client = await self.publish_next_to(state)
    self.assertIsNone(plan_id)
    self.assertEqual(outcome["reason_code"], ge.SAME_DIRECTION_GROUP_LIMIT)
    rows = outcome["measured"]["existing_groups"]  # what PR-X0 could not read: who blocked, and on which facts
    self.assertEqual([(r["plan_id"], r["highest_booked_target_index"], r["stop_price"], r["entry_reference"], r["counted"]) for r in rows], [("v8:plan1", 1, 3860.0, 3880.0, True)])
    self.assertGreater(rows[0]["risk_amount"], rows[0]["risk_free_allowance"])
    self.assertIsNone(await client.hget("auto_trade:metrics:XAU", "same_direction_risk_free_skipped"))


if __name__ == "__main__":
  unittest.main()

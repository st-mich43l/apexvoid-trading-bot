"""Active exposure gates: same-direction stacking + TradePlan PascalCase Redis payloads."""

from __future__ import annotations

import json

import pytest

from app.autotrade.active_exposure import (
  ActiveExposure,
  evaluate_entry_against_exposure,
  load_active_exposures,
)

pytestmark = pytest.mark.no_database


def test_same_direction_evaluator_does_not_decide_opposite_exposure():
  """Opposite exposure is owned by evaluate_opposite_exposure (instrument
  policy); the same-direction evaluator must neither block nor bypass it."""
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4051.93,
    exposures=[
      ActiveExposure(
        direction="SELL",
        entry_price=4054.20,
        source="v8_plan",
        plan_id="sell-near",
      )
    ],
  )
  assert decision.block is False
  assert decision.reason_code is None
  assert decision.same_direction_stack is False


def test_same_direction_blocks_non_scalp_before_tp2_booked():
  """Owner: same-dir add waits until first position books TP2."""
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4050.0,
    exposures=[
      ActiveExposure(
        direction="BUY",
        entry_price=4048.0,
        source="v8_plan",
        plan_id="buy-open",
        highest_booked_target_index=0,  # TP1 only
      )
    ],
  )
  assert decision.block is True
  assert decision.same_direction_stack is False
  assert decision.reason_code == "same_direction_active_before_tp2"


def test_same_direction_blocks_when_booked_index_unknown():
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4050.0,
    exposures=[
      ActiveExposure(
        direction="BUY",
        entry_price=4048.0,
        source="v8_plan",
        plan_id="buy-open",
        highest_booked_target_index=None,
      )
    ],
  )
  assert decision.block is True
  assert decision.reason_code == "same_direction_active_before_tp2"


def test_same_direction_blocks_non_tier_a_after_tp2_booked():
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4050.0,
    exposures=[
      ActiveExposure(
        direction="BUY",
        entry_price=4048.0,
        source="v8_plan",
        plan_id="buy-open",
        highest_booked_target_index=1,
      )
    ],
    candidate_tier="B",
  )
  assert decision.block is True
  assert decision.same_direction_stack is False
  assert decision.reason_code == "same_direction_stack_requires_tier_a"


def test_same_direction_stacks_at_60_after_tp2_booked():
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4050.0,
    exposures=[
      ActiveExposure(
        direction="BUY",
        entry_price=4048.0,
        source="v8_plan",
        plan_id="buy-open",
        highest_booked_target_index=1,  # TP2 booked
      )
    ],
    candidate_tier="A",
  )
  assert decision.block is False
  assert decision.same_direction_stack is True
  assert decision.reason_code == "same_direction_stack"
  assert decision.measured is not None
  assert decision.measured["same_direction_tp2_booked"] is True
  assert "Tier A" in decision.message


def test_same_direction_stack_flag_when_allowed_for_scalp():
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4050.0,
    exposures=[
      ActiveExposure(
        direction="BUY",
        entry_price=4048.0,
        source="v8_plan",
        plan_id="buy-open",
        highest_booked_target_index=None,
      )
    ],
    allow_same_direction_stack=True,
  )
  assert decision.block is False
  assert decision.same_direction_stack is True
  assert decision.reason_code == "same_direction_stack"


@pytest.mark.asyncio
async def test_recovery_required_fully_open_is_not_live_exposure():
  """Live 2026-08-26: unknown_leg_close left FullyOpen and locked HFS concurrent."""
  class FakeRedis:
    async def get(self, key: str):
      if key == "execution:trade_plan_runtime_ids":
        return b"v8:a80bf164b1f8e2d4de2dd710ebe0e9db"
      if key == "execution:plan_runtime:v8:a80bf164b1f8e2d4de2dd710ebe0e9db":
        return json.dumps({
          "PlanId": "v8:a80bf164b1f8e2d4de2dd710ebe0e9db",
          "SetupId": "a80bf164b1f8e2d4de2dd710ebe0e9db",
          "Symbol": "XAU",
          "Direction": "BUY",
          "Stage": "FullyOpen",
          "GroupStage": "recovery_required",
          "TerminalReason": "unknown_leg_close",
          "GroupWeightedFillPrice": 4642.018,
          "TotalFilledVolume": 1500,
          "RemainingVolume": 300,
          "HighestBookedTargetIndex": -1,
        }).encode()
      return None

    async def smembers(self, key: str):
      return set()

  exposures = await load_active_exposures(FakeRedis(), symbol="XAU")
  assert exposures == []


@pytest.mark.asyncio
async def test_unknown_leg_close_fully_open_excluded_even_without_group_stage():
  class FakeRedis:
    async def get(self, key: str):
      if key == "execution:trade_plan_runtime_ids":
        return b"v8:ghost"
      if key == "execution:plan_runtime:v8:ghost":
        return json.dumps({
          "PlanId": "v8:ghost",
          "SetupId": "ghost",
          "Symbol": "XAU",
          "Direction": "BUY",
          "Stage": "FullyOpen",
          "GroupStage": "fully_open",
          "TerminalReason": "unknown_leg_close",
          "GroupWeightedFillPrice": 4642.0,
          "TotalFilledVolume": 100,
          "RemainingVolume": 100,
        }).encode()
      return None

    async def smembers(self, key: str):
      return set()

  exposures = await load_active_exposures(FakeRedis(), symbol="XAU")
  assert exposures == []


def test_is_non_live_trade_plan_helpers():
  from app.autotrade.active_exposure import _is_non_live_trade_plan

  assert _is_non_live_trade_plan(
    stage="FullyOpen",
    group_stage="recovery_required",
    terminal_reason="unknown_leg_close",
  )
  assert not _is_non_live_trade_plan(
    stage="FullyOpen",
    group_stage="fully_open",
    terminal_reason=None,
  )


@pytest.mark.asyncio
async def test_load_trade_plan_exposures_reads_pascal_case_runtime_json():
  class FakeRedis:
    async def get(self, key: str):
      if key == "execution:trade_plan_runtime_ids":
        return b"plan-sell-1"
      if key == "execution:plan_runtime:plan-sell-1":
        return json.dumps({
          "PlanId": "plan-sell-1",
          "SetupId": "setup-1",
          "Symbol": "XAU",
          "Direction": "SELL",
          "Stage": "FullyOpen",
          "GroupStage": "fully_open",
          "GroupWeightedFillPrice": 4054.2,
          "TotalFilledVolume": 200,
          "RemainingVolume": 200,
          "HighestBookedTargetIndex": 1,
        }).encode()
      return None

    async def smembers(self, key: str):
      return set()

  exposures = await load_active_exposures(FakeRedis())
  assert len(exposures) == 1
  assert exposures[0].direction == "SELL"
  assert exposures[0].entry_price == pytest.approx(4054.2)
  assert exposures[0].source == "v8_plan"
  assert exposures[0].plan_id == "plan-sell-1"
  assert exposures[0].symbol == "XAU"
  assert exposures[0].highest_booked_target_index == 1


@pytest.mark.asyncio
async def test_load_pending_trade_plan_counts_as_exposure():
  class FakeRedis:
    async def get(self, key: str):
      if key == "execution:trade_plan_runtime_ids":
        return b"v8:kl-dac0"
      if key == "execution:plan_runtime:v8:kl-dac0":
        return json.dumps({
          "PlanId": "v8:kl-dac0",
          "SetupId": "setup-dac0",
          "Symbol": "GBPJPY",
          "Direction": "SELL",
          "Stage": "Received",
          "GroupStage": "received",
          "IntendedEntryPrice": 215.91,
          "TotalFilledVolume": 0,
          "RemainingVolume": 0,
          "HighestBookedTargetIndex": -1,
        }).encode()
      return None

    async def smembers(self, key: str):
      return set()

  exposures = await load_active_exposures(FakeRedis())
  assert len(exposures) == 1
  assert exposures[0].direction == "SELL"
  assert exposures[0].entry_price == pytest.approx(215.91)
  assert exposures[0].symbol == "GBPJPY"
  decision = evaluate_entry_against_exposure(
    direction="SELL",
    entry_price=215.92,
    exposures=exposures,
    candidate_symbol="GBPJPY",
  )
  assert decision.block is True
  assert decision.reason_code == "same_direction_active_before_tp2"


def _gbpjpy_sell() -> ActiveExposure:
  return ActiveExposure(
    direction="SELL",
    entry_price=215.91,
    source="v8_plan",
    symbol="GBPJPY",
    plan_id="v8:kl-dac0",
    highest_booked_target_index=None,
  )


def _xau_sell() -> ActiveExposure:
  return ActiveExposure(
    direction="SELL",
    entry_price=4414.11,
    source="v8_plan",
    symbol="XAU",
    plan_id="v8:vip-4414",
    highest_booked_target_index=None,
  )


def test_gbpjpy_sell_does_not_block_eurusd_sell():
  """Live 2026-08-17 13:19 UTC: EURUSD Key Level SELL rejected @ 215.91."""
  decision = evaluate_entry_against_exposure(
    direction="SELL",
    entry_price=1.1640,
    exposures=[_gbpjpy_sell()],
    candidate_symbol="EURUSD",
  )
  assert decision.block is False
  assert decision.reason_code is None


def test_xau_pending_sell_does_not_block_gbpjpy_sell():
  """Live 2026-08-17 14:04 UTC: GBPJPY rejected because VIP gold @ 4414.11."""
  decision = evaluate_entry_against_exposure(
    direction="SELL",
    entry_price=215.91,
    exposures=[_xau_sell(), _gbpjpy_sell()],
    candidate_symbol="GBPJPY",
  )
  assert decision.block is True
  assert decision.reason_code == "same_direction_active_before_tp2"
  assert decision.measured is not None
  assert decision.measured["active_symbol"] == "GBPJPY"
  assert decision.measured["active_entry_price"] == pytest.approx(215.91)


def test_xauusd_alias_still_blocks_xau_same_direction():
  decision = evaluate_entry_against_exposure(
    direction="SELL",
    entry_price=4396.10,
    exposures=[
      ActiveExposure(
        direction="SELL",
        entry_price=4414.11,
        source="v8_plan",
        symbol="XAUUSD",
        plan_id="v8:xauusd-sell",
      )
    ],
    candidate_symbol="XAU",
  )
  assert decision.block is True
  assert decision.reason_code == "same_direction_active_before_tp2"


def test_missing_symbol_exposure_does_not_lock_other_instruments():
  """V6 rows without a string symbol must not become a global SELL lock."""
  decision = evaluate_entry_against_exposure(
    direction="SELL",
    entry_price=1.1640,
    exposures=[
      ActiveExposure(
        direction="SELL",
        entry_price=215.91,
        source="v6_position",
        symbol=None,
        position_id=40539792,
      )
    ],
    candidate_symbol="EURUSD",
  )
  assert decision.block is False


@pytest.mark.asyncio
async def test_load_active_exposures_filters_by_symbol():
  class FakeRedis:
    async def get(self, key: str):
      if key == "execution:trade_plan_runtime_ids":
        return b"v8:gbp,v8:xau"
      if key == "execution:plan_runtime:v8:gbp":
        return json.dumps({
          "PlanId": "v8:gbp",
          "Symbol": "GBPJPY",
          "Direction": "SELL",
          "Stage": "FullyOpen",
          "GroupStage": "fully_open",
          "GroupWeightedFillPrice": 215.91,
          "TotalFilledVolume": 120,
          "RemainingVolume": 120,
          "HighestBookedTargetIndex": -1,
        }).encode()
      if key == "execution:plan_runtime:v8:xau":
        return json.dumps({
          "PlanId": "v8:xau",
          "Symbol": "XAU",
          "Direction": "SELL",
          "Stage": "FullyOpen",
          "GroupStage": "fully_open",
          "GroupWeightedFillPrice": 4414.11,
          "TotalFilledVolume": 120,
          "RemainingVolume": 120,
          "HighestBookedTargetIndex": -1,
        }).encode()
      return None

    async def smembers(self, key: str):
      return set()

  all_exposures = await load_active_exposures(FakeRedis())
  assert {item.symbol for item in all_exposures} == {"GBPJPY", "XAU"}
  eurusd = await load_active_exposures(FakeRedis(), symbol="EURUSD")
  assert eurusd == []
  gbp = await load_active_exposures(FakeRedis(), symbol="GBPJPY")
  assert len(gbp) == 1
  assert gbp[0].plan_id == "v8:gbp"


@pytest.mark.asyncio
async def test_load_active_exposures_batches_position_and_plan_payloads():
  class CountingRedis:
    def __init__(self):
      self.get_calls: list[str] = []
      self.mget_calls: list[list[str]] = []
      self.values = {
        "auto_trade:position:47": json.dumps({
          "PositionId": 47,
          "Symbol": "XAU",
          "Direction": "BUY",
          "EntryPrice": 4100.0,
          "RemainingVolume": 100,
        }).encode(),
        "auto_trade:position:48": json.dumps({
          "PositionId": 48,
          "Symbol": "EURUSD",
          "Direction": "SELL",
          "EntryPrice": 1.17,
          "RemainingVolume": 100,
        }).encode(),
        "execution:plan_runtime:v8:gbp": json.dumps({
          "PlanId": "v8:gbp",
          "Symbol": "GBPJPY",
          "Direction": "SELL",
          "Stage": "Submitted",
          "GroupStage": "submitted",
          "IntendedEntryPrice": 215.9,
        }).encode(),
        "execution:plan_runtime:v8:jpy": json.dumps({
          "PlanId": "v8:jpy",
          "Symbol": "USDJPY",
          "Direction": "BUY",
          "Stage": "FullyOpen",
          "GroupStage": "fully_open",
          "GroupWeightedFillPrice": 148.2,
          "TotalFilledVolume": 100,
          "RemainingVolume": 100,
        }).encode(),
      }

    async def smembers(self, key: str):
      assert key == "auto_trade:positions"
      return {b"47", b"48"}

    async def get(self, key: str):
      self.get_calls.append(key)
      if key == "execution:trade_plan_runtime_ids":
        return b"v8:gbp,v8:jpy"
      return self.values.get(key)

    async def mget(self, keys: list[str]):
      self.mget_calls.append(list(keys))
      return [self.values.get(key) for key in keys]

  client = CountingRedis()
  exposures = await load_active_exposures(client)

  assert {item.symbol for item in exposures} == {
    "XAU", "EURUSD", "GBPJPY", "USDJPY",
  }
  assert client.get_calls == ["execution:trade_plan_runtime_ids"]
  assert len(client.mget_calls) == 2
  assert {len(keys) for keys in client.mget_calls} == {2}

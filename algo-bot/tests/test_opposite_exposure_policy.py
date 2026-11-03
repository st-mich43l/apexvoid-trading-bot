"""Instrument-owned opposite-direction exposure policy (FX never, XAU >= 150 pips,
or >= 80 pips for an incoming scalp-lane trade).

The decision comes only from ``exposure.opposite_position`` in
config/instruments.yml plus the incoming trade's lane (the catalog's ``m1_scalp``
trait). Strategy names and families are never inputs.
"""

from __future__ import annotations

import inspect
import json

import pytest

from app.autotrade import active_exposure
from app.autotrade.active_exposure import (
  FX_OPPOSITE_NOT_ALLOWED,
  XAU_OPPOSITE_SEPARATION_SATISFIED,
  XAU_OPPOSITE_TOO_CLOSE,
  XAU_SCALP_OPPOSITE_TOO_CLOSE,
  ActiveExposure,
  evaluate_opposite_exposure,
  load_active_exposures,
)
from app.core import instrument_geometry
from app.runtime.instruments import (
  EffectiveInstrumentError,
  OppositePositionPolicy,
  _opposite_position_policy,
)

pytestmark = pytest.mark.no_database

FX_PAIRS = ("EURUSD", "GBPUSD", "GBPJPY", "USDJPY")
STRATEGIES = (
  "Range Box Scalp",
  "Supply", "Demand", "FVG", "Breakout",
)

MEASURED_FIELDS = {
  "symbol", "incoming_direction", "incoming_entry", "existing_direction",
  "existing_entry", "distance_price", "distance_pips",
  "minimum_separation_pips", "existing_plan_id", "existing_group_id",
  "existing_position_id", "policy",
}


def _opposite(direction: str) -> str:
  return "SELL" if direction == "BUY" else "BUY"


def _active(symbol: str, direction: str, entry: float, **kw) -> ActiveExposure:
  return ActiveExposure(
    direction=direction,
    entry_price=entry,
    source=kw.pop("source", "v8_plan"),
    symbol=symbol,
    plan_id=kw.pop("plan_id", f"plan-{symbol}-{direction}-{entry}"),
    group_id=kw.pop("group_id", "group-1"),
    **kw,
  )


XAU_POLICY = OppositePositionPolicy("XAU", True, 150.0, 0.1, 80.0)


# --- config-owned policy ------------------------------------------------------


@pytest.mark.parametrize("symbol", FX_PAIRS)
def test_fx_policy_from_yaml_never_allows_opposite(symbol):
  policy = instrument_geometry.opposite_position_policy(symbol)
  assert policy.allowed is False
  assert policy.minimum_separation_pips is None
  assert policy.scalp_minimum_separation_pips is None


@pytest.mark.parametrize("symbol", ("XAU", "XAUUSD"))
def test_xau_policy_from_yaml_is_150_pips_and_80_for_scalps_on_canonical_pip_size(symbol):
  policy = instrument_geometry.opposite_position_policy(symbol)
  assert policy.allowed is True
  assert policy.minimum_separation_pips == 150.0
  assert policy.scalp_minimum_separation_pips == 80.0
  assert policy.pip_size == pytest.approx(0.1)


def test_every_live_instrument_declares_a_policy():
  from app.core.config import runtime_config
  from app.runtime.instruments import live_instruments

  for symbol in live_instruments(runtime_config):
    assert isinstance(
      instrument_geometry.opposite_position_policy(symbol),
      OppositePositionPolicy,
    )


@pytest.mark.parametrize(
  "exposure",
  [
    None,
    {},
    {"opposite_position": {}},
    {"opposite_position": {"allowed": "yes"}},
    {"opposite_position": {"allowed": True}},
    {"opposite_position": {"allowed": True, "minimum_separation_pips": 0}},
    {"opposite_position": {"allowed": True, "minimum_separation_pips": -5}},
    {"opposite_position": {"allowed": False, "minimum_separation_pips": 150}},
    # scalp separation: required when allowed, positive, never above the standard one,
    # and forbidden when opposite positions are blocked (FX)
    {"opposite_position": {"allowed": True, "minimum_separation_pips": 150}},
    {"opposite_position": {"allowed": True, "minimum_separation_pips": 150, "scalp_minimum_separation_pips": 0}},
    {"opposite_position": {"allowed": True, "minimum_separation_pips": 150, "scalp_minimum_separation_pips": -1}},
    {"opposite_position": {"allowed": True, "minimum_separation_pips": 150, "scalp_minimum_separation_pips": 151}},
    {"opposite_position": {"allowed": True, "minimum_separation_pips": 150, "scalp_minimum_separation_pips": "80"}},
    {"opposite_position": {"allowed": False, "scalp_minimum_separation_pips": 80}},
  ],
)
def test_missing_or_invalid_policy_fails_closed(exposure):
  with pytest.raises(EffectiveInstrumentError):
    _opposite_position_policy("NEWFX", exposure, 0.0001)


def test_scalp_separation_equal_to_the_standard_one_is_valid():
  policy = _opposite_position_policy(
    "NEWXAU",
    {"opposite_position": {"allowed": True, "minimum_separation_pips": 150, "scalp_minimum_separation_pips": 150}},
    0.1,
  )
  assert (policy.minimum_separation_pips, policy.scalp_minimum_separation_pips) == (150.0, 150.0)


def test_removed_price_based_leaves_are_gone():
  from pathlib import Path

  root = Path(__file__).resolve().parents[2] / "config"
  for path in root.rglob("*.yml"):
    assert "opposing_minimum_separation_price" not in path.read_text(), path
  assert not hasattr(instrument_geometry, "opposing_minimum_separation_price")


# --- FX: always blocked, distance irrelevant -----------------------------------


@pytest.mark.parametrize("symbol", FX_PAIRS)
@pytest.mark.parametrize("incoming", ("BUY", "SELL"))
@pytest.mark.parametrize("pips_away", (0.0, 0.1, 1, 25, 150, 150.1, 5000))
def test_fx_opposite_blocked_at_any_distance(symbol, incoming, pips_away):
  policy = instrument_geometry.opposite_position_policy(symbol)
  base = 1.2500 if not symbol.endswith("JPY") else 190.00
  step = pips_away * policy.pip_size
  decision = evaluate_opposite_exposure(
    symbol,
    incoming,
    base + step,
    [_active(symbol, _opposite(incoming), base)],
    policy,
  )
  assert decision.allowed is False
  assert decision.reason_code == FX_OPPOSITE_NOT_ALLOWED
  assert MEASURED_FIELDS <= set(decision.measured)
  assert decision.measured["policy"] == "blocked"
  assert decision.measured["existing_direction"] == _opposite(incoming)


@pytest.mark.parametrize("symbol", FX_PAIRS)
def test_fx_same_direction_is_not_an_opposite_conflict(symbol):
  policy = instrument_geometry.opposite_position_policy(symbol)
  decision = evaluate_opposite_exposure(
    symbol, "BUY", 1.25, [_active(symbol, "BUY", 1.25)], policy
  )
  assert decision.allowed is True
  assert decision.reason_code is None


def test_fx_opposite_on_other_symbol_does_not_block():
  policy = instrument_geometry.opposite_position_policy("EURUSD")
  decision = evaluate_opposite_exposure(
    "EURUSD", "BUY", 1.10, [_active("GBPUSD", "SELL", 1.10)], policy
  )
  assert decision.allowed is True


# --- XAU: >= 150 pips against EVERY opposite group -----------------------------


@pytest.mark.parametrize(
  ("incoming", "existing", "entry", "allowed"),
  [
    # active BUY @ 4300, incoming SELL above it
    ("SELL", "BUY", 4314.00, False),
    ("SELL", "BUY", 4314.90, False),
    ("SELL", "BUY", 4314.99, False),   # 149.9 pips
    ("SELL", "BUY", 4315.00, True),    # 150.0 pips: inclusive boundary
    ("SELL", "BUY", 4315.01, True),    # 150.1 pips
    ("SELL", "BUY", 4290.00, False),   # other side of the active, 100 pips
    ("SELL", "BUY", 4285.00, True),
    # active SELL @ 4300, incoming BUY
    ("BUY", "SELL", 4285.01, False),   # 149.9 pips
    ("BUY", "SELL", 4285.00, True),
    ("BUY", "SELL", 4284.99, True),
    ("BUY", "SELL", 4315.00, True),
    ("BUY", "SELL", 4314.00, False),
  ],
)
def test_xau_separation_boundary(incoming, existing, entry, allowed):
  decision = evaluate_opposite_exposure(
    "XAU", incoming, entry, [_active("XAU", existing, 4300.00)], XAU_POLICY
  )
  assert decision.allowed is allowed
  assert decision.reason_code == (
    XAU_OPPOSITE_SEPARATION_SATISFIED if allowed else XAU_OPPOSITE_TOO_CLOSE
  )
  assert MEASURED_FIELDS <= set(decision.measured)
  assert decision.measured["minimum_separation_pips"] == 150.0


def test_xau_scenarios_from_the_mission_spec():
  buy = [_active("XAU", "BUY", 4300.0)]
  sell = [_active("XAU", "SELL", 4300.0)]
  assert not evaluate_opposite_exposure("XAU", "SELL", 4314.0, buy, XAU_POLICY).allowed
  assert evaluate_opposite_exposure("XAU", "SELL", 4315.0, buy, XAU_POLICY).allowed
  assert evaluate_opposite_exposure("XAU", "BUY", 4315.0, sell, XAU_POLICY).allowed


def test_xau_distance_reported_in_pips_from_pip_size():
  decision = evaluate_opposite_exposure(
    "XAU", "SELL", 4314.0, [_active("XAU", "BUY", 4300.0)], XAU_POLICY
  )
  assert decision.measured["distance_price"] == pytest.approx(14.0)
  assert decision.measured["distance_pips"] == pytest.approx(140.0)


@pytest.mark.parametrize(
  ("active_symbol", "incoming_symbol"),
  [("XAUUSD", "XAU"), ("XAU", "XAUUSD"), ("GOLD", "XAU"), ("XAU", "GOLD")],
)
def test_xau_aliases_share_one_book(active_symbol, incoming_symbol):
  blocked = evaluate_opposite_exposure(
    incoming_symbol, "SELL", 4314.0,
    [_active(active_symbol, "BUY", 4300.0)], XAU_POLICY,
  )
  assert blocked.allowed is False
  allowed = evaluate_opposite_exposure(
    incoming_symbol, "SELL", 4315.0,
    [_active(active_symbol, "BUY", 4300.0)], XAU_POLICY,
  )
  assert allowed.allowed is True


def test_xau_must_clear_every_opposite_group():
  groups = [
    _active("XAU", "BUY", 4300.0, plan_id="far", group_id="g-far"),
    _active("XAU", "BUY", 4400.0, plan_id="near", group_id="g-near"),
  ]
  # 4420 is 1200 pips from the first group but only 200 from... 4400 -> 200
  # pips; tighten to 4410 (100 pips from 4400) so the nearer group blocks.
  blocked = evaluate_opposite_exposure("XAU", "SELL", 4410.0, groups, XAU_POLICY)
  assert blocked.allowed is False
  assert blocked.reason_code == XAU_OPPOSITE_TOO_CLOSE
  assert blocked.measured["existing_plan_id"] == "near"
  assert blocked.measured["existing_group_id"] == "g-near"
  cleared = evaluate_opposite_exposure("XAU", "SELL", 4415.0, groups, XAU_POLICY)
  assert cleared.allowed is True


def test_xau_same_direction_never_triggers_opposite_rule():
  decision = evaluate_opposite_exposure(
    "XAU", "BUY", 4300.5, [_active("XAU", "BUY", 4300.0)], XAU_POLICY
  )
  assert decision.allowed is True
  assert decision.reason_code is None


# --- exposure lanes: scalp 80 / standard 150 / FX never -------------------------


@pytest.mark.parametrize(
  ("pips", "scalp_allowed"),
  [(79.9, False), (80.0, True), (149.0, True), (151.0, True)],
)
def test_xau_scalp_lane_needs_80_pips_inclusive(pips, scalp_allowed):
  # active SELL @ 4300.00, incoming BUY below it by `pips` (pip 0.1 = 0.1 price)
  entry = round(4300.0 - pips * 0.1, 2)
  decision = evaluate_opposite_exposure(
    "XAU", "BUY", entry, [_active("XAU", "SELL", 4300.0)], XAU_POLICY, lane="scalp"
  )
  assert decision.allowed is scalp_allowed
  assert decision.reason_code == (
    XAU_OPPOSITE_SEPARATION_SATISFIED if scalp_allowed else XAU_SCALP_OPPOSITE_TOO_CLOSE
  )
  assert decision.measured["lane"] == "scalp"
  assert decision.measured["separation_applied_pips"] == 80.0
  assert decision.measured["minimum_separation_pips"] == 150.0
  assert decision.measured["scalp_minimum_separation_pips"] == 80.0


@pytest.mark.parametrize(("pips", "allowed"), [(149.0, False), (149.9, False), (150.0, True)])
@pytest.mark.parametrize("lane", ("standard", None, "STANDARD", "unknown"))
def test_xau_standard_lane_keeps_150_pips(pips, allowed, lane):
  entry = round(4300.0 - pips * 0.1, 2)
  kwargs = {} if lane is None else {"lane": lane}
  decision = evaluate_opposite_exposure(
    "XAU", "BUY", entry, [_active("XAU", "SELL", 4300.0)], XAU_POLICY, **kwargs
  )
  assert decision.allowed is allowed
  assert decision.reason_code == (
    XAU_OPPOSITE_SEPARATION_SATISFIED if allowed else XAU_OPPOSITE_TOO_CLOSE
  )
  assert decision.measured["lane"] == "standard"
  assert decision.measured["separation_applied_pips"] == 150.0


@pytest.mark.parametrize("symbol", FX_PAIRS)
@pytest.mark.parametrize("lane", ("scalp", "standard"))
@pytest.mark.parametrize("pips_away", (0.0, 80.0, 500.0, 5000.0))
def test_fx_blocks_opposite_in_every_lane_at_any_distance(symbol, lane, pips_away):
  policy = instrument_geometry.opposite_position_policy(symbol)
  entry = 1.0 + pips_away * policy.pip_size
  decision = evaluate_opposite_exposure(
    symbol, "BUY", entry, [_active(symbol, "SELL", 1.0)], policy, lane=lane
  )
  assert decision.allowed is False
  assert decision.reason_code == FX_OPPOSITE_NOT_ALLOWED


def test_incoming_lane_decides_not_the_lane_of_the_existing_groups():
  """An opposite scalp group does not relax the rule for an incoming standard trade."""
  existing = [_active("XAU", "SELL", 4300.0, plan_id="scalp-plan-on-the-book")]
  standard = evaluate_opposite_exposure("XAU", "BUY", 4300.0 - 10.0, existing, XAU_POLICY)
  scalp = evaluate_opposite_exposure("XAU", "BUY", 4300.0 - 10.0, existing, XAU_POLICY, lane="scalp")
  assert standard.allowed is False and standard.reason_code == XAU_OPPOSITE_TOO_CLOSE
  assert scalp.allowed is True


def test_scalp_lane_must_clear_every_opposite_group_at_80():
  groups = [_active("XAU", "SELL", 4300.0, plan_id="far"), _active("XAU", "SELL", 4292.0, plan_id="near")]
  decision = evaluate_opposite_exposure("XAU", "BUY", 4290.0, groups, XAU_POLICY, lane="scalp")
  assert decision.allowed is False
  assert decision.measured["existing_plan_id"] == "near"


def test_policy_without_a_scalp_separation_falls_back_to_the_standard_minimum():
  policy = OppositePositionPolicy("XAU", True, 150.0, 0.1)
  decision = evaluate_opposite_exposure(
    "XAU", "BUY", 4300.0 - 10.0, [_active("XAU", "SELL", 4300.0)], policy, lane="scalp"
  )
  assert decision.allowed is False
  assert decision.measured["separation_applied_pips"] == 150.0


def test_27_oct_fade_cell_is_allowed_for_an_impulse_pullback_scalp():
  """Production 27 Oct: a Fade BUY at 4008.30 was refused 144.0 pips from an active SELL
  at 4022.70 (needed 150). Fade no longer exists; the same geometry as an Impulse
  Pullback Scalp (catalog lane scalp) clears the 80-pip scalp separation, a standard
  trade is still refused."""
  from app.autotrade.strategy_catalog import exposure_lane

  groups = [_active("XAU", "SELL", 4022.70)]
  lane = exposure_lane("Impulse Pullback Scalp")
  assert lane == "scalp"
  scalp = evaluate_opposite_exposure("XAU", "BUY", 4008.30, groups, XAU_POLICY, lane=lane)
  assert scalp.allowed is True
  assert scalp.measured["distance_pips"] == pytest.approx(144.0)
  refused = evaluate_opposite_exposure("XAU", "BUY", 4008.30, groups, XAU_POLICY, lane="standard")
  assert refused.allowed is False and refused.reason_code == XAU_OPPOSITE_TOO_CLOSE


def test_six_short_groups_at_144_pips_do_not_block_a_scalp():
  groups = [_active("XAU", "SELL", 4022.70, plan_id=f"short-{i}") for i in range(6)]
  decision = evaluate_opposite_exposure("XAU", "BUY", 4008.30, groups, XAU_POLICY, lane="scalp")
  assert decision.allowed is True


def test_exposure_lane_comes_from_the_catalog_trait_only():
  from app.autotrade.strategy_catalog import STRATEGY_PROFILES, exposure_lane

  for profile in STRATEGY_PROFILES:
    assert exposure_lane(profile.name) == ("scalp" if profile.m1_scalp else "standard")
  assert exposure_lane("Impulse Pullback Scalp") == "scalp"
  assert exposure_lane("Key Level") == "standard"
  assert exposure_lane("Round Number") == "standard"
  assert exposure_lane("Not A Strategy") == "standard"
  assert exposure_lane("") == "standard"
  assert exposure_lane(None) == "standard"


# --- strategy independence ------------------------------------------------------


def test_evaluator_signature_has_no_strategy_inputs():
  params = set(inspect.signature(evaluate_opposite_exposure).parameters)
  # ``lane`` is the catalog's scalp/standard exposure lane, not a strategy name.
  assert params == {
    "symbol", "incoming_direction", "incoming_entry_reference",
    "active_exposures", "instrument_policy", "lane",
  }


def test_no_strategy_bypass_symbols_remain_in_exposure_module():
  source = inspect.getsource(active_exposure)
  for retired in (
    "ignore_opposing_active",
    "opposing_active_too_close_ignored_scalp",
    "scalp_ignores_opposing_active",
    "min_price_separation",
  ):
    assert retired not in source


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("symbol", FX_PAIRS)
def test_fx_block_is_identical_for_every_strategy(strategy, symbol):
  """Strategy never reaches the evaluator, so the verdict cannot vary."""
  policy = instrument_geometry.opposite_position_policy(symbol)
  decision = evaluate_opposite_exposure(
    symbol, "BUY", 1.0, [_active(symbol, "SELL", 1.0)], policy
  )
  assert (strategy, decision.allowed, decision.reason_code) == (
    strategy, False, FX_OPPOSITE_NOT_ALLOWED
  )


# --- active-exposure states (pending race, terminal, broker-side recovery) ------


class _FakeRedis:
  def __init__(self, plans: dict[str, dict]):
    self._plans = plans

  async def get(self, key: str):
    if key == "execution:trade_plan_runtime_ids":
      return ",".join(self._plans).encode()
    plan_id = key.removeprefix("execution:plan_runtime:")
    if plan_id in self._plans:
      return json.dumps(self._plans[plan_id]).encode()
    return None


def _plan_state(symbol, direction, stage, group_stage, **extra) -> dict:
  return {
    "PlanId": f"v8:{symbol}-{direction}-{stage}",
    "SetupId": f"setup-{symbol}-{direction}",
    "Symbol": symbol,
    "Direction": direction,
    "Stage": stage,
    "GroupStage": group_stage,
    "TotalFilledVolume": extra.pop("TotalFilledVolume", 0),
    "RemainingVolume": extra.pop("RemainingVolume", 0),
    **extra,
  }


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("stage", "group_stage", "filled"),
  [
    ("Received", "received", 0),
    ("Submitting", "submitting", 0),
    ("Submitted", "submitted", 0),
    ("PartiallyOpen", "partially_open", 100),
    ("FullyOpen", "fully_open", 100),
    ("FullyOpen", "managing", 100),
    ("FullyOpen", "partially_closed", 60),
  ],
)
async def test_every_live_plan_stage_blocks_fx_opposite(stage, group_stage, filled):
  """Pending-plan race: a plan that has not filled yet still occupies the book."""
  redis = _FakeRedis({
    "p1": _plan_state(
      "GBPUSD", "SELL", stage, group_stage,
      IntendedEntryPrice=1.3000, GroupWeightedFillPrice=1.3000 if filled else None,
      TotalFilledVolume=filled, RemainingVolume=filled,
    ),
  })
  exposures = await load_active_exposures(redis, symbol="GBPUSD")
  assert len(exposures) == 1
  decision = evaluate_opposite_exposure(
    "GBPUSD", "BUY", 1.3500, exposures,
    instrument_geometry.opposite_position_policy("GBPUSD"),
  )
  assert decision.allowed is False
  assert decision.reason_code == FX_OPPOSITE_NOT_ALLOWED


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("stage", "group_stage"),
  [("Closed", "closed"), ("Cancelled", "cancelled"), ("Expired", "expired"),
   ("Rejected", "rejected"), ("Closed", "failed")],
)
async def test_terminal_plans_do_not_block(stage, group_stage):
  redis = _FakeRedis({
    "p1": _plan_state(
      "GBPUSD", "SELL", stage, group_stage, IntendedEntryPrice=1.3000,
    ),
  })
  exposures = await load_active_exposures(redis, symbol="GBPUSD")
  decision = evaluate_opposite_exposure(
    "GBPUSD", "BUY", 1.3500, exposures,
    instrument_geometry.opposite_position_policy("GBPUSD"),
  )
  assert decision.allowed is True


@pytest.mark.asyncio
async def test_filled_plan_uses_broker_fill_price_and_pending_uses_planned_entry():
  redis = _FakeRedis({
    "filled": _plan_state(
      "XAU", "BUY", "FullyOpen", "fully_open",
      IntendedEntryPrice=4290.0, GroupWeightedFillPrice=4300.0,
      TotalFilledVolume=10, RemainingVolume=10,
    ),
    "pending": _plan_state(
      "XAU", "BUY", "Received", "received", IntendedEntryPrice=4500.0,
    ),
  })
  exposures = {e.plan_id: e for e in await load_active_exposures(redis, symbol="XAU")}
  assert exposures["v8:XAU-BUY-FullyOpen"].entry_price == 4300.0
  assert exposures["v8:XAU-BUY-Received"].entry_price == 4500.0


@pytest.mark.asyncio
async def test_xau_multi_leg_ladder_is_one_group_at_recorded_group_price():
  redis = _FakeRedis({
    "ladder": _plan_state(
      "XAU", "BUY", "PartiallyOpen", "partially_open",
      GroupWeightedFillPrice=4300.0, TotalFilledVolume=20, RemainingVolume=20,
      Legs=[
        {"LegId": "L1", "IntendedPrice": 4302.0, "FillPrice": 4301.0},
        {"LegId": "L2", "IntendedPrice": 4298.0, "FillPrice": 4299.0},
      ],
    ),
  })
  exposures = await load_active_exposures(redis, symbol="XAU")
  assert len(exposures) == 1
  assert exposures[0].entry_price == 4300.0
  assert evaluate_opposite_exposure(
    "XAU", "SELL", 4314.0, exposures, XAU_POLICY
  ).allowed is False
  assert evaluate_opposite_exposure(
    "XAU", "SELL", 4315.0, exposures, XAU_POLICY
  ).allowed is True

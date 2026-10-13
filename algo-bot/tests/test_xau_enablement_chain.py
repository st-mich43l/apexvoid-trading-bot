"""XAU enablement certification for ``ifvg`` (v3) and ``session_level`` (v4).

``config/instruments.yml`` now carries the enabled list (XAU observes only
``liquidity_sweep`` and ``impulse_pullback``). The tests pin that list, and use a
runtime override to exercise the rollback (restoring the two names) on real
PostgreSQL and a real Redis.

Covered: both strategies reach an executable TradePlan V8 through the real consumer,
the reviewed policy, the worker and plan admission; an observation without a
confirmed reaction cannot execute; the XAU/XAUUSD alias; the other XAU containment
and FX are unchanged; restoring the two names re-contains them without cancelling or
altering anything already published.
"""

from __future__ import annotations

import json

import pytest

from app.autotrade import go_containment as containment
from app.autotrade import go_opportunity_policy as pol
from app.autotrade import worker
from app.autotrade.go_plan_cancel import plan_cancel_key
from app.autotrade.multi_match import deserialize_matches, strategy_matches_key
from tests.support.canonical_fixtures import install_runtime_overrides
from tests.test_go_full_chain import (  # noqa: F401 - fixtures and helpers
  _spot,
  catalog_kafka_record,
  consumer_for,
  cycle,
  h,
  plans,
  prod,
  read_trade_plan,
  route,
)

pytestmark = pytest.mark.real_redis

REBUILT = ("ifvg", "session_level")
KEY = "instruments.XAU.overrides.execution.go_opportunity.observe_only_strategies"
PROPOSED = ["liquidity_sweep", "impulse_pullback"]
CURRENT = ["ifvg", "liquidity_sweep", "session_level", "impulse_pullback"]


def simulate(monkeypatch, names):
  install_runtime_overrides(monkeypatch, {KEY: list(names)})


@pytest.mark.no_database
def test_the_proposed_list_removes_exactly_the_two_rebuilt_strategies(monkeypatch):
  assert set(CURRENT) - set(PROPOSED) == set(REBUILT)
  assert containment.observe_only_strategies("XAU") == set(PROPOSED)        # the enabled file
  simulate(monkeypatch, PROPOSED)
  assert containment.observe_only_strategies("XAU") == {"liquidity_sweep", "impulse_pullback"}
  assert containment.observe_only_strategies("XAUUSD") == containment.observe_only_strategies("XAU")
  # The M15 structure containment and every FX list are untouched by the change.
  assert containment.observe_only_structure_timeframes("XAU") == {"M15"}
  for symbol in ("EURUSD", "GBPUSD", "GBPJPY", "USDJPY"):
    assert containment.observe_only_strategies(symbol) == {"impulse_pullback"}
    assert containment.observe_only_structure_timeframes(symbol) == {"M15"}
  for scope in ("ifvg", "session_level"):
    assert containment.containment_reason("XAU", scope, None) is None
    assert containment.containment_reason("XAUUSD", scope, None) is None
  for scope in ("liquidity_sweep", "impulse_pullback"):
    assert containment.containment_reason("XAU", scope, None) == "execution_contained"
  assert containment.containment_reason("XAU", "supply", "M15") == "execution_contained_structure_timeframe"


@pytest.mark.parametrize("scope", REBUILT)
@pytest.mark.asyncio
async def test_a_confirmed_reaction_reaches_a_valid_tradeplan_v8_once_xau_stops_containing_it(h, prod, monkeypatch, scope):
  simulate(monkeypatch, PROPOSED)
  await h.activate(scope=scope)
  await h._ensure()
  await consumer_for(h).process_record(catalog_kafka_record(h.clock.now, scope))
  match = deserialize_matches(await prod.get(strategy_matches_key("XAU")))[0]
  assert match.structural_source == f"go:{scope}" and "origin:go" in match.tags
  plan_id = await worker._publish_trade_plan_v8(prod, "XAU", _spot(4354.1, 4354.3), match, frames={})
  assert plan_id is not None, f"{scope} rejected: {await route(prod, match.match_id)}"
  plan = await read_trade_plan(prod, plan_id)
  assert plan is not None and plan.setup_id == match.match_id
  assert plan.provenance.confirmation_source == "go_analysis_engine"
  plan.validate()
  # The plan is the ordinary automatic one: nothing about it is special-cased.
  assert plan.sizing.mode == "equity_table" and plan.stop.type == "absolute" and plan.targets
  assert (await route(prod, match.match_id))["status"] == "candidate_published"
  # A repeated cycle does not publish a second plan for the same thesis.
  await cycle(prod, n=3)
  assert len([p for p in await plans(prod) if p["setup_id"] == match.match_id]) == 1


@pytest.mark.parametrize("scope", REBUILT)
@pytest.mark.asyncio
async def test_an_observation_without_a_confirmed_reaction_can_never_execute(h, prod, monkeypatch, scope):
  """Resting / incomplete reactions: the reviewed policy requires the reaction, so even
  with containment lifted nothing is matched, planned or published."""
  assert pol.REVIEWED_SCOPES[scope].requires_reaction
  simulate(monkeypatch, PROPOSED)
  await h.activate(scope=scope)
  await h._ensure()
  record = catalog_kafka_record(h.clock.now, scope)
  raw = json.loads(record.value)
  raw["payload"]["technical_context"].pop("confirmation", None)
  record.value = json.dumps(raw).encode()
  await consumer_for(h).process_record(record)
  assert await prod.get(strategy_matches_key("XAU")) is None
  await cycle(prod, n=3)
  assert await plans(prod) == []


@pytest.mark.parametrize("scope", REBUILT)
@pytest.mark.asyncio
async def test_evidence_without_the_strategy_prefix_is_not_accepted(h, prod, monkeypatch, scope):
  simulate(monkeypatch, PROPOSED)
  await h.activate(scope=scope)
  await h._ensure()
  record = catalog_kafka_record(h.clock.now, scope)
  raw = json.loads(record.value)
  raw["payload"]["evidence"] = [{"code": "displacement_grade"}]
  record.value = json.dumps(raw).encode()
  await consumer_for(h).process_record(record)
  assert await prod.get(strategy_matches_key("XAU")) is None
  assert await plans(prod) == []


@pytest.mark.parametrize("scope", ["liquidity_sweep", "impulse_pullback"])
@pytest.mark.asyncio
async def test_the_other_contained_strategies_stay_contained(h, prod, monkeypatch, scope):
  simulate(monkeypatch, PROPOSED)
  await h.activate(scope=scope)
  await h._ensure()
  await consumer_for(h).process_record(catalog_kafka_record(h.clock.now, scope))
  assert await prod.get(strategy_matches_key("XAU")) is None
  assert (await h.decisions())[-1]["reason"] == "execution_contained"
  await cycle(prod, n=3)
  assert await plans(prod) == []


@pytest.mark.parametrize("scope", REBUILT)
@pytest.mark.asyncio
async def test_rollback_recontains_new_matches_without_touching_a_published_plan(h, prod, monkeypatch, scope):
  """Restoring the two names stops NEW plans only: a plan already published stays in
  the stream, in its state, uncancelled; a match stored before the rollback cannot
  become a plan; the Go engine, the other strategies and the position management
  are not involved at all (containment is read at match creation and plan admission)."""
  simulate(monkeypatch, PROPOSED)
  await h.activate(scope=scope)
  await h._ensure()
  await consumer_for(h).process_record(catalog_kafka_record(h.clock.now, scope))
  match = deserialize_matches(await prod.get(strategy_matches_key("XAU")))[0]
  plan_id = await worker._publish_trade_plan_v8(prod, "XAU", _spot(4354.1, 4354.3), match, frames={})
  assert plan_id is not None
  before = [json.dumps(p, sort_keys=True) for p in await plans(prod)]
  state_before = await prod.get(f"execution:plan_state:{plan_id}")

  simulate(monkeypatch, CURRENT)                                             # the rollback
  assert containment.containment_reason("XAU", scope, None) == "execution_contained"
  await cycle(prod, n=3)
  assert [json.dumps(p, sort_keys=True) for p in await plans(prod)] == before
  assert await prod.get(f"execution:plan_state:{plan_id}") == state_before == "published"
  assert not await prod.exists(plan_cancel_key(plan_id))

  # A match that predates the rollback cannot become a NEW plan either.
  other = catalog_kafka_record(h.clock.now, scope, offset=2)
  raw = json.loads(other.value)
  raw["payload"]["id"] = f"opp_{scope}_after_rollback"
  raw["event_id"] = f"evt-{scope}-after-rollback"
  other.value = json.dumps(raw).encode()
  await consumer_for(h).process_record(other)
  assert (await h.decisions())[-1]["reason"] == "execution_contained"
  assert len(await plans(prod)) == 1

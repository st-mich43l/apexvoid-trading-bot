"""S13C: Go opportunity -> StrategyMatch -> the real V8 plan pipeline, fenced.

The end-to-end tests run the *real* worker plan build against fakeredis; only
the Go event is a fixture (the golden envelope the Go encoder pins).
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

from app.analysis_client import authority as auth
from app.analysis_client.authority import AuthorityFence
from app.analysis_client.models import (
  InvalidationTopic,
  OpportunityTopic,
  parse_analysis_event,
)
from app.analysis_client.repository import PostgresAnalysisOpportunityRepository
from app.autotrade import go_opportunity_policy as pol
from app.autotrade import worker
from app.autotrade.multi_match import deserialize_matches, strategy_matches_key
from app.autotrade.setup_lifecycle import CONFIRMED, INVALIDATED, load_setup
from app.autotrade.trade_plan_stream import read_plan_state, read_trade_plan
from app.persistence import redis_state, store
from tests.test_analysis_authority_fence import Clock, MemoryStore
from tests.test_analysis_client_models import _invalidated
from tests.configuration.canonical_fixtures import install_runtime_overrides
from tests.test_publish_trade_plan_v8 import (  # noqa: F401 - autouse fixtures
  _freeze_technique_killzone_hour,
  _m1_trigger_bar,
  _no_news_by_default,
)

GOLDEN = Path(__file__).resolve().parents[2] / "contracts/analysis/examples/opportunity-v1-technical-context.json"


def golden(now: int | None = None, **payload_overrides):
  """The Go-pinned envelope, re-based so its times are relative to ``now``."""
  raw = json.loads(GOLDEN.read_text())
  if now is not None:
    shift = now - raw["payload"]["created_at"] - 60      # observed a minute ago
    raw["occurred_at"] += shift
    raw["produced_at"] += shift
    p = raw["payload"]
    for key in ("formed_at", "created_at", "expires_at"):
      p[key] += shift
    p["technical_context"]["reference_time"] += shift
  # A reviewed, test-only confirmed event. The raw Go golden file remains
  # the unconfirmed resting-zone example; production must obtain these
  # fields from Go's independent causal reaction and H1/H4 calculations.
  p = raw["payload"]
  observed = p["technical_context"]["reference_time"]
  p["technical_context"]["confirmation"] = {
    "zone_id": "zone-golden-supply",
    "touch_bar_time": observed - 300,
    "confirmation_bar_time": observed,
    "reaction_type": "rejection",
  }
  p["technical_context"]["higher_timeframes"] = [{
    "timeframe": "H1", "direction": "SELL", "layer": "major", "reference_time": observed - 3900,
  }]
  raw["payload"].update(payload_overrides)
  return raw


def event(now=None, **overrides):
  return parse_analysis_event(OpportunityTopic, json.dumps(golden(now, **overrides)))


SUPPLY = pol.REVIEWED_SCOPES["supply"]
DEMAND = pol.REVIEWED_SCOPES["demand"]


# ---- pure translation ---------------------------------------------------------

def test_supply_translation_is_exact_and_carries_authority_provenance():
  ev = event()
  now = ev.payload.created_at + 60
  match = pol.build_strategy_match(ev, profile=SUPPLY, epoch=3, now=now)

  assert (match.symbol, match.direction, match.strategy, match.source_tf) == ("XAU", "SELL", "Supply Demand", "M5")
  assert (match.entry_low, match.entry_high) == (4352.5, 4356.0)
  assert match.structure_swing == 4358.75                     # Go invalidation, verbatim
  assert match.atr == 3.61 and match.current_price == 4351.9  # Go facts, not recomputed
  assert match.expires_at == ev.payload.expires_at and match.issued_at == ev.payload.created_at
  # SELL enters at the proximal (lower) edge: 4352.5 -> 4344.0 is 85 pips at XAU pip 0.1.
  assert match.targets_pips == (85,)
  assert match.absolute_target_price == 4344.0
  assert match.reasons == ("m5_supply_zone_fresh", "m5_supply_zone_relevance_immediate")
  assert match.confluence == 2
  assert match.structural_kind == "supply" and match.structural_zone_id == "zone-golden-supply"
  assert match.match_id == "go_opp_golden_supply_xau" and match.thesis_id.startswith("go-thesis-")
  assert match.htf_bias == "down" and match.regime_kind == ""  # real Go H1 structure; no invented regime
  assert (match.touch_bar_ts, match.confirmation_bar_ts, match.reaction_type) == (str(ev.payload.created_at - 300), str(ev.payload.created_at), "rejection")
  assert match.bias_relationship == "with_bias" and match.strategy_mode == "with_bias"  # Go bias SELL == direction
  tags = set(match.tags)
  assert {auth.GO_ORIGIN_TAG, "catalog:supply", "authority_epoch:3", "bias_source:go_primary_tf", "htf_bias_source:go_H1", "go_reaction:rejection"} <= tags
  elig = match.execution_eligibility
  assert elig.allowed and elig.planned_entry_price == 4354.25
  assert elig.measured["source"] == "go" and elig.reward_risk == round(85 / 62.5, 4)


def test_go_higher_timeframe_bias_never_uses_primary_m5_as_substitute():
  raw = golden()
  p = raw["payload"]
  observed = p["technical_context"]["reference_time"]
  p["technical_context"]["higher_timeframes"] = [
    {"timeframe": "H4", "direction": "BUY", "layer": "intermediate", "reference_time": observed - 18000},
    {"timeframe": "H1", "direction": "SELL", "layer": "major", "reference_time": observed - 3900},
  ]
  ev = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  match = pol.build_strategy_match(ev, profile=SUPPLY, epoch=1, now=p["created_at"] + 1)
  assert match.htf_bias == "down"
  assert "htf_bias_source:go_H1" in match.tags
  # Preserve the H4 disagreement on the Kafka contract; do not flatten it
  # into the primary bias or replace the reviewed H1-first policy.
  assert {item.timeframe: item.direction for item in ev.payload.technical_context.higher_timeframes} == {"H1": "SELL", "H4": "BUY"}


@pytest.mark.parametrize("mutate", [
  lambda r: r["payload"]["technical_context"]["higher_timeframes"].append(
    dict(r["payload"]["technical_context"]["higher_timeframes"][0])
  ),
  lambda r: r["payload"]["technical_context"]["higher_timeframes"].__setitem__(
    0, {"timeframe": "H1", "direction": "SELL", "layer": "major", "reference_time": r["payload"]["created_at"]}
  ),
])
def test_higher_timeframe_duplicates_and_unclosed_bars_fail_contract(mutate):
  raw = golden()
  raw["payload"]["technical_context"]["higher_timeframes"] = [
    {"timeframe": "H1", "direction": "SELL", "layer": "major", "reference_time": raw["payload"]["created_at"] - 3900},
  ]
  mutate(raw)
  from app.analysis_client.models import AnalysisContractError
  with pytest.raises(AnalysisContractError):
    parse_analysis_event(OpportunityTopic, json.dumps(raw))


def test_counter_bias_and_neutral_are_derived_only_from_go_bias():
  raw = golden()
  raw["payload"]["technical_context"]["bias"]["direction"] = "BUY"
  assert pol.build_strategy_match(parse_analysis_event(OpportunityTopic, json.dumps(raw)), profile=SUPPLY, epoch=1, now=raw["payload"]["created_at"] + 1).bias_relationship == "counter_bias"
  del raw["payload"]["technical_context"]["bias"]
  assert pol.build_strategy_match(parse_analysis_event(OpportunityTopic, json.dumps(raw)), profile=SUPPLY, epoch=1, now=raw["payload"]["created_at"] + 1).bias_relationship == "neutral"


def test_demand_translation_mirrors_geometry_for_buy():
  raw = golden()
  p = raw["payload"]
  p.update({
    "strategy": "demand", "direction": "BUY", "entry": {"low": 4330.0, "high": 4333.5},
    "invalidation": {"price": 4327.0}, "targets": [{"price": {"price": 4341.0}}, {"price": {"price": 4350.0}}],
  })
  p["technical_context"].update({"reference_price": 4334.0, "bias": {"direction": "BUY", "layer": "internal"}})
  match = pol.build_strategy_match(parse_analysis_event(OpportunityTopic, json.dumps(raw)), profile=DEMAND, epoch=1, now=p["created_at"] + 1)
  # BUY enters at the proximal (upper) edge 4333.5.
  assert match.targets_pips == (75, 165) and match.absolute_target_price == 4350.0
  assert match.structural_kind == "demand" and match.direction == "BUY"


@pytest.mark.parametrize("mutate,code", [
  (lambda r: r["payload"].pop("technical_context"), "technical_context_unavailable"),
  (lambda r: r["payload"].pop("timeframe"), "missing_observed_timeframe"),
  (lambda r: r["payload"].update(symbol="NOSUCH"), "unknown_instrument"),
  (lambda r: r["payload"].update(targets=[{"price": {"price": 4352.48}}]), "target_not_beyond_entry"),
])
def test_missing_or_unusable_facts_are_rejected_not_approximated(mutate, code):
  raw = golden()
  mutate(raw)
  ev = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  with pytest.raises(pol.AdapterRejection) as exc:
    pol.build_strategy_match(ev, profile=SUPPLY, epoch=1, now=raw["payload"]["created_at"] + 1)
  assert exc.value.code == code


def test_direction_and_expiry_guards():
  ev = event()
  with pytest.raises(pol.AdapterRejection) as wrong:
    pol.build_strategy_match(ev, profile=DEMAND, epoch=1, now=ev.payload.created_at + 1)   # SELL into demand scope
  assert wrong.value.code == "direction_scope_mismatch"
  with pytest.raises(pol.AdapterRejection) as expired:
    pol.build_strategy_match(ev, profile=SUPPLY, epoch=1, now=ev.payload.expires_at)
  assert expired.value.code == "opportunity_expired"


def test_only_reviewed_zone_scopes_are_adaptable():
  assert set(pol.REVIEWED_SCOPES) == {"supply", "demand"}
  assert set(pol.REVIEWED_SCOPES) <= auth.CATALOG_STRATEGY_IDS
  for scope, profile in pol.REVIEWED_SCOPES.items():
    assert scope in auth.catalog_ids_for_legacy(profile.legacy_strategy, profile.direction)


# ---- runner: fence, durable ledger, Redis match store ---------------------------

pytestmark_db = pytest.mark.asyncio


class Harness:
  def __init__(self, sql, monkeypatch):
    self.clock = Clock(now=time.time())
    self.fence = AuthorityFence(MemoryStore(), cache_ttl=0.0, clock=self.clock)
    monkeypatch.setattr(auth, "_default_fence", self.fence)
    self.repo = PostgresAnalysisOpportunityRepository()
    self.policy = pol.GoOpportunityPolicy(self.repo, fence=self.fence, clock=self.clock, multiple_matches_enabled=lambda: True)
    self.sql = sql
    self.offset = 0
    self._ready = False

  async def _ensure(self):
    if not self._ready:
      await store.init_db()
      self._ready = True

  async def deliver(self, ev, topic=OpportunityTopic):
    await self._ensure()
    self.offset += 1
    result = await self.repo.apply(ev, topic=topic, partition=0, offset=self.offset)
    if topic == OpportunityTopic:
      return await self.policy.on_creation(ev, result)
    return await self.policy.on_terminal(ev, result)

  async def grant(self, scope="supply", symbol="XAU"):
    await self._ensure()
    await self.fence.record_acceptance(symbol, scope, "ev", approved_by="owner", ttl_seconds=3600)
    await self.fence.begin_transfer(symbol, scope, "go", expected_epoch=0, actor="owner", reason="approved", evidence_ref="ev", drain_seconds=5)
    self.clock.advance(5)

  async def decisions(self):
    await self._ensure()
    rows = await self.sql.fetch("SELECT outcome, reason, mode FROM analysis_shadow_decisions ORDER BY decision_id")
    return [dict(r) for r in rows]


@pytest.fixture
def h(sql, monkeypatch):
  install_runtime_overrides(monkeypatch, {"analysis.technical_authority.consumer_enabled": True})
  return Harness(sql, monkeypatch)


@pytest.mark.asyncio
async def test_python_owned_scope_writes_no_match_and_records_why(h):
  now = int(h.clock.now)
  assert await h.deliver(event(now)) == "not_adapted"
  assert await redis_state.get_client().get(strategy_matches_key("XAU")) is None
  assert await h.decisions() == [{"outcome": "not_adapted", "reason": "not_go_owner:scope_owned_by_python", "mode": "go"}]


@pytest.mark.asyncio
async def test_go_owned_scope_writes_a_confirmed_setup_and_one_match_idempotently(h):
  await h.grant()
  ev = event(int(h.clock.now))
  assert await h.deliver(ev) == "match_written"
  client = redis_state.get_client()
  matches = deserialize_matches(await client.get(strategy_matches_key("XAU")))
  assert [m.match_id for m in matches] == ["go_opp_golden_supply_xau"]
  assert "authority_epoch:1" in matches[0].tags
  assert (await load_setup(client, "go_opp_golden_supply_xau")).state == CONFIRMED
  # Redelivery of the same Kafka event is idempotent (it must be, so a failed
  # first attempt can be retried) and a different event for the same
  # opportunity adds nothing.
  assert await h.deliver(ev) == "match_written"
  again = copy.deepcopy(golden(int(h.clock.now)))
  again["event_id"] = "evt-create-other"
  assert await h.deliver(parse_analysis_event(OpportunityTopic, json.dumps(again))) == "ignored_duplicate_opportunity"
  assert len(deserialize_matches(await client.get(strategy_matches_key("XAU")))) == 1


@pytest.mark.asyncio
async def test_late_redelivery_of_a_creation_never_resurrects_a_terminated_opportunity(h):
  await h.grant()
  ev = event(int(h.clock.now))
  await h.deliver(ev)
  term = parse_analysis_event(InvalidationTopic, json.dumps(_invalidated(payload={
    "opportunity_id": "opp_golden_supply_xau", "symbol": "XAU", "strategy": "supply",
    "reason_code": "STRUCTURE_INVALIDATED", "invalidated_at": int(h.clock.now),
  })))
  await h.deliver(term, InvalidationTopic)
  assert await h.deliver(ev) == "ignored_not_active"          # Kafka redelivers the old creation
  assert await redis_state.get_client().get(strategy_matches_key("XAU")) is None


@pytest.mark.asyncio
async def test_unreviewed_scope_and_missing_facts_are_recorded_and_dropped(h):
  await h.grant()
  now = int(h.clock.now)
  raw = golden(now)
  raw["payload"]["strategy"] = "order_block"
  assert await h.deliver(parse_analysis_event(OpportunityTopic, json.dumps(raw))) == "not_adapted"

  raw = golden(now)
  raw["event_id"], raw["payload"]["id"] = "evt-2", "opp-2"
  del raw["payload"]["technical_context"]
  assert await h.deliver(parse_analysis_event(OpportunityTopic, json.dumps(raw))) == "rejected"
  reasons = [d["reason"] for d in await h.decisions()]
  assert reasons == ["scope_not_reviewed", "technical_context_unavailable"]
  assert await redis_state.get_client().get(strategy_matches_key("XAU")) is None


@pytest.mark.asyncio
async def test_single_match_key_ambiguity_fails_closed(h):
  await h.grant()
  h.policy = pol.GoOpportunityPolicy(h.repo, fence=h.fence, clock=h.clock, multiple_matches_enabled=lambda: False)
  assert await h.deliver(event(int(h.clock.now))) == "not_adapted"
  assert (await h.decisions())[-1]["reason"] == "multiple_matches_disabled"


@pytest.mark.asyncio
async def test_unreadable_fence_fails_closed(h):
  class Down:
    async def get(self, *_):
      raise ConnectionError("down")
  h.policy = pol.GoOpportunityPolicy(h.repo, fence=AuthorityFence(Down(), cache_ttl=0.0), clock=h.clock, multiple_matches_enabled=lambda: True)
  assert await h.deliver(event(int(h.clock.now))) == "not_adapted"
  assert (await h.decisions())[-1]["reason"].startswith("authority_unavailable")


@pytest.mark.asyncio
async def test_go_terminal_withdraws_the_match_and_invalidates_the_setup(h):
  await h.grant()
  await h.deliver(event(int(h.clock.now)))
  term = parse_analysis_event(InvalidationTopic, json.dumps(_invalidated(payload={
    "opportunity_id": "opp_golden_supply_xau", "symbol": "XAU", "strategy": "supply",
    "reason_code": "ZONE_INVALIDATED", "invalidated_at": int(h.clock.now),
  })))
  assert await h.deliver(term, InvalidationTopic) == "match_withdrawn"
  client = redis_state.get_client()
  assert await client.get(strategy_matches_key("XAU")) is None
  assert (await load_setup(client, "go_opp_golden_supply_xau")).state == INVALIDATED


# ---- end to end: Go event -> match -> REAL V8 plan --------------------------------

def _spot(bid, ask):
  return worker.AutoTradeSpot(price=(bid + ask) / 2, ts=int(time.time()), fresh=True, bid=bid, ask=ask)


async def _outcome(client, match):
  from app.autotrade.route_outcome import route_outcome_key
  return json.loads(await client.get(route_outcome_key(match.symbol, match.match_id)))


@pytest.mark.asyncio
async def test_resting_go_zone_is_a_non_executable_observation(h):
  await h.grant()
  raw = golden(int(h.clock.now))
  del raw["payload"]["technical_context"]["confirmation"]
  assert await h.deliver(parse_analysis_event(OpportunityTopic, json.dumps(raw))) == "rejected"
  assert (await h.decisions())[-1]["reason"] == "reaction_confirmation_unavailable"
  assert await redis_state.get_client().get(strategy_matches_key("XAU")) is None


def test_missing_go_htf_structure_is_rejected_not_recreated_from_m5():
  raw = golden()
  del raw["payload"]["technical_context"]["higher_timeframes"]
  ev = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  with pytest.raises(pol.AdapterRejection) as exc:
    pol.build_strategy_match(ev, profile=SUPPLY, epoch=1, now=raw["payload"]["created_at"] + 1)
  assert exc.value.code == "higher_timeframe_bias_unavailable"


@pytest.mark.asyncio
async def test_existing_v8_builder_fails_closed_if_htf_is_stripped(h):
  from dataclasses import replace
  await h.grant()
  await h.deliver(event(int(h.clock.now)))
  client = redis_state.get_client()
  base = deserialize_matches(await client.get(strategy_matches_key("XAU")))[0]
  match = replace(base, htf_bias="")
  assert await worker._publish_trade_plan_v8(client, "XAU", _spot(4354.1, 4354.3), match, frames={"M1": _m1_trigger_bar()}) is None
  assert (await _outcome(client, match))["reason_code"] == "v8_missing_htf_bias"


@pytest.mark.asyncio
async def test_go_owned_zone_becomes_a_real_v8_plan_with_real_contract_fields(h):
  """No Python detector or post-adaptation mutation supplies confirmation.

  The Go-pinned resting golden is enriched with the additive confirmed
  reaction/HTF test fixture; the same typed event is consumed by the actual
  adapter and the unchanged V8 policy and publisher.
  """
  await h.grant()
  await h.deliver(event(int(h.clock.now)))
  client = redis_state.get_client()
  match = deserialize_matches(await client.get(strategy_matches_key("XAU")))[0]
  assert match.htf_bias == "down" and match.reaction_type == "rejection"
  plan_id = await worker._publish_trade_plan_v8(client, "XAU", _spot(4354.1, 4354.3), match, frames={"M1": _m1_trigger_bar()})
  assert plan_id is not None, f"policy rejected: {await _outcome(client, match)}"
  plan = await read_trade_plan(client, plan_id)
  assert plan is not None and await read_plan_state(client, plan_id) == "published"
  assert plan.setup_id == "go_opp_golden_supply_xau" and plan.thesis_id == match.thesis_id
  assert plan.analysis.direction == "SELL" and plan.source_structure.kind == "supply"


@pytest.mark.asyncio
async def test_rollback_between_match_write_and_publish_blocks_the_plan(h):
  await h.grant()
  await h.deliver(event(int(h.clock.now)))
  client = redis_state.get_client()
  match = deserialize_matches(await client.get(strategy_matches_key("XAU")))[0]
  await h.fence.rollback("XAU", "supply", expected_epoch=1, actor="oncall", reason="rollback", drain_seconds=30)
  plan_id = await worker._publish_trade_plan_v8(client, "XAU", _spot(4354.1, 4354.3), match, frames={"M1": _m1_trigger_bar()})
  assert plan_id is None
  raw = await client.get(worker_route_key(match))
  assert json.loads(raw)["reason_code"] == "authority_fenced"


def worker_route_key(match):
  from app.autotrade.route_outcome import route_outcome_key
  return route_outcome_key(match.symbol, match.match_id)

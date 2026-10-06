"""Go opportunity -> StrategyMatch -> the real V8 plan pipeline.

The end-to-end tests run the *real* worker plan build against fakeredis; only
the Go event is a fixture (the golden envelope the Go encoder pins).
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

from app.analysis_client.provenance import CATALOG_STRATEGY_IDS, GO_ORIGIN_TAG
from app.analysis_client.models import (
  ArbitrationTopic,
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
from tests.test_analysis_client_models import _invalidated
from tests.support.canonical_fixtures import install_runtime_overrides
from tests.test_publish_trade_plan_v8 import (  # noqa: F401 - autouse fixtures
  _freeze_technique_killzone_hour,
  _m1_trigger_bar,
  _no_news_by_default,
)

GOLDEN = Path(__file__).resolve().parents[2] / "contracts/analysis/examples/opportunity-v1-technical-context.json"


class Clock:
  def __init__(self, now: float):
    self.now = now

  def __call__(self) -> float:
    return self.now

  def advance(self, seconds: float) -> None:
    self.now += seconds


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

@pytest.mark.no_database
def test_supply_translation_is_exact_and_carries_go_provenance():
  ev = event()
  now = ev.payload.created_at + 60
  match = pol.build_strategy_match(ev, profile=SUPPLY, now=now)

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
  # Go's real per-instance quality (distinct from the legacy confluence
  # evidence-code count above) - verbatim from the envelope, never recomputed.
  assert match.quality_overall == 0.82
  assert match.quality_components == {"zone_strength_quality": 0.9}
  assert match.structural_kind == "supply" and match.structural_zone_id == "zone-golden-supply"
  assert match.match_id == "go_opp_golden_supply_xau" and match.thesis_id == pol._thesis_id("XAU", "supply_demand", "SELL", ev.payload.technical_context.confirmation.zone_id)
  assert match.htf_bias == "down" and match.regime_kind == ""  # real Go H1 structure; no invented regime
  assert (match.touch_bar_ts, match.confirmation_bar_ts, match.reaction_type) == (str(ev.payload.created_at - 300), str(ev.payload.created_at), "rejection")
  assert match.bias_relationship == "with_bias" and match.strategy_mode == "with_bias"  # Go bias SELL == direction
  tags = set(match.tags)
  assert {GO_ORIGIN_TAG, "catalog:supply", "bias_source:go_primary_tf", "htf_bias_source:go_H1", "go_reaction:rejection"} <= tags
  elig = match.execution_eligibility
  assert elig.allowed and elig.planned_entry_price == 4354.25
  assert elig.measured["source"] == "go" and elig.reward_risk == round(85 / 62.5, 4)


@pytest.mark.no_database
def test_go_mad_telemetry_is_copied_verbatim_without_python_reanalysis():
  raw = golden()
  raw["payload"]["technical_context"]["mad"] = {
    "version": 2,
    "phase": "manip",
    "confidence": 0.91,
    "affinity": 0.86,
    "direction": "SELL",
    "sweep_side": "high",
    "reclaim": True,
    "range_quality_atr": 2.1,
    "acceptance_closes": 0,
    "reason_code": "asia_sweep_reclaim",
  }
  match = pol.build_strategy_match(
    parse_analysis_event(OpportunityTopic, json.dumps(raw)),
    profile=SUPPLY,
    now=raw["payload"]["created_at"] + 60,
  )
  assert (match.mad_version, match.mad_phase) == (2, "manip")
  assert match.mad_confidence == 0.91 and match.mad_affinity == 0.86
  assert match.mad_direction == "SELL" and match.mad_sweep_side == "high"
  assert match.mad_reason_code == "asia_sweep_reclaim"


@pytest.mark.no_database
def test_go_confluence_stars_are_consumed_without_evidence_count_recalculation():
  raw = golden()
  raw["payload"]["evidence"] = [{"code": "m5_supply_zone_fresh"}]
  raw["payload"]["technical_context"]["confluence"] = {
    "version": "v1", "selected_stars": 3, "v1_stars": 3, "v2_stars": 2,
    "v2_raw": 14.0, "raw_factor_score": 12.0,
    "zone_quality_score": 2.0, "mad_bonus": 0.0,
    "factors": {
      "htf_aligned": True, "touches": 0, "wick_rejection": True,
      "displacement_grade": True, "session_context": True,
      "structural_agreement": True, "fib_touch": False, "choch": False,
    },
  }
  match = pol.build_strategy_match(
    parse_analysis_event(OpportunityTopic, json.dumps(raw)),
    profile=SUPPLY, now=raw["payload"]["created_at"] + 1,
  )
  assert match.confluence == 3
  assert (match.confluence_v1, match.confluence_v2, match.confluence_scoring_version) == (3, 2, "v1")


@pytest.mark.no_database
def test_go_higher_timeframe_bias_never_uses_primary_m5_as_substitute():
  raw = golden()
  p = raw["payload"]
  observed = p["technical_context"]["reference_time"]
  p["technical_context"]["higher_timeframes"] = [
    {"timeframe": "H4", "direction": "BUY", "layer": "intermediate", "reference_time": observed - 18000},
    {"timeframe": "H1", "direction": "SELL", "layer": "major", "reference_time": observed - 3900},
  ]
  ev = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  match = pol.build_strategy_match(ev, profile=SUPPLY, now=p["created_at"] + 1)
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
@pytest.mark.no_database
def test_higher_timeframe_duplicates_and_unclosed_bars_fail_contract(mutate):
  raw = golden()
  raw["payload"]["technical_context"]["higher_timeframes"] = [
    {"timeframe": "H1", "direction": "SELL", "layer": "major", "reference_time": raw["payload"]["created_at"] - 3900},
  ]
  mutate(raw)
  from app.analysis_client.models import AnalysisContractError
  with pytest.raises(AnalysisContractError):
    parse_analysis_event(OpportunityTopic, json.dumps(raw))


@pytest.mark.parametrize("field,value", [
  ("touch_bar_time", -1),
  ("confirmation_bar_time", 10**12),
  ("reaction_type", "assumed_reclaim"),
  ("zone_id", ""),
])
@pytest.mark.no_database
def test_invalid_reaction_evidence_is_rejected_at_the_kafka_contract(field, value):
  raw = golden()
  raw["payload"]["technical_context"]["confirmation"][field] = value
  from app.analysis_client.models import AnalysisContractError
  with pytest.raises(AnalysisContractError):
    parse_analysis_event(OpportunityTopic, json.dumps(raw))


@pytest.mark.no_database
def test_counter_bias_and_neutral_are_derived_only_from_go_bias():
  raw = golden()
  raw["payload"]["technical_context"]["bias"]["direction"] = "BUY"
  assert pol.build_strategy_match(parse_analysis_event(OpportunityTopic, json.dumps(raw)), profile=SUPPLY, now=raw["payload"]["created_at"] + 1).bias_relationship == "counter_bias"
  del raw["payload"]["technical_context"]["bias"]
  assert pol.build_strategy_match(parse_analysis_event(OpportunityTopic, json.dumps(raw)), profile=SUPPLY, now=raw["payload"]["created_at"] + 1).bias_relationship == "neutral"


@pytest.mark.no_database
def test_demand_translation_mirrors_geometry_for_buy():
  raw = golden()
  p = raw["payload"]
  p.update({
    "strategy": "demand", "direction": "BUY", "entry": {"low": 4330.0, "high": 4333.5},
    "invalidation": {"price": 4327.0}, "targets": [{"price": {"price": 4341.0}}, {"price": {"price": 4350.0}}],
  })
  p["evidence"] = [
    {"code": "m5_demand_zone_fresh"},
    {"code": "m5_demand_zone_rejection_confirmed"},
  ]
  p["technical_context"].update({"reference_price": 4334.0, "bias": {"direction": "BUY", "layer": "internal"}})
  match = pol.build_strategy_match(parse_analysis_event(OpportunityTopic, json.dumps(raw)), profile=DEMAND, now=p["created_at"] + 1)
  # BUY enters at the proximal (upper) edge 4333.5.
  assert match.targets_pips == (75, 165) and match.absolute_target_price == 4350.0
  assert match.structural_kind == "demand" and match.direction == "BUY"


@pytest.mark.parametrize("mutate,code", [
  (lambda r: r["payload"].pop("technical_context"), "technical_context_unavailable"),
  (lambda r: r["payload"].update(symbol="NOSUCH"), "unknown_instrument"),
  (lambda r: r["payload"].update(targets=[{"price": {"price": 4352.48}}]), "target_not_beyond_entry"),
])
@pytest.mark.no_database
def test_missing_or_unusable_facts_are_rejected_not_approximated(mutate, code):
  raw = golden()
  mutate(raw)
  ev = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  with pytest.raises(pol.AdapterRejection) as exc:
    pol.build_strategy_match(ev, profile=SUPPLY, now=raw["payload"]["created_at"] + 1)
  assert exc.value.code == code


@pytest.mark.no_database
def test_missing_observed_timeframe_with_htf_facts_fails_at_contract_boundary():
  raw = golden()
  del raw["payload"]["timeframe"]
  from app.analysis_client.models import AnalysisContractError
  with pytest.raises(AnalysisContractError, match="timeframe is required"):
    parse_analysis_event(OpportunityTopic, json.dumps(raw))


@pytest.mark.no_database
def test_confirmed_go_identity_survives_redis_without_confusing_zone_and_opportunity():
  match = pol.build_strategy_match(event(), profile=SUPPLY, now=golden()["payload"]["created_at"] + 1)
  from app.autotrade.multi_match import serialize_matches, deserialize_matches
  from dataclasses import replace
  assert match.match_id != f"go_{match.structural_zone_id}"
  assert deserialize_matches(serialize_matches([match])) == [match]
  # A missing, forged, or duplicated opportunity provenance cannot turn a
  # real zone into a differently identified executable Go opportunity.
  for bad in (
    replace(match, tags=tuple(t for t in match.tags if not t.startswith("go_opportunity:"))),
    replace(match, tags=match.tags + ("go_opportunity:other",)),
    replace(match, match_id="go_some_other_opportunity"),
  ):
    assert deserialize_matches(serialize_matches([bad])) == []


@pytest.mark.no_database
def test_direction_and_expiry_guards():
  ev = event()
  with pytest.raises(pol.AdapterRejection) as wrong:
    pol.build_strategy_match(ev, profile=DEMAND, now=ev.payload.created_at + 1)   # SELL into demand scope
  assert wrong.value.code == "direction_scope_mismatch"
  with pytest.raises(pol.AdapterRejection) as expired:
    pol.build_strategy_match(ev, profile=SUPPLY, now=ev.payload.expires_at)
  assert expired.value.code == "opportunity_expired"


@pytest.mark.no_database
def test_every_enabled_catalog_scope_has_an_explicit_adapter():
  assert set(pol.REVIEWED_SCOPES) == set(CATALOG_STRATEGY_IDS)


@pytest.mark.parametrize("scope", sorted(pol.REVIEWED_SCOPES))
@pytest.mark.no_database
def test_each_catalog_adapter_preserves_its_own_evidence_and_geometry(scope):
  profile = pol.REVIEWED_SCOPES[scope]
  raw = golden()
  payload = raw["payload"]
  payload["strategy"] = scope
  payload["direction"] = "BUY" if scope == "demand" else "SELL" if scope == "supply" else "BUY"
  payload["timeframe"] = "M1" if scope in {"range_sweep", "impulse_pullback", "scalp_breakout_retest"} else "M5"
  if payload["direction"] == "BUY":
    payload.update({
      "entry": {"low": 4330.0, "high": 4333.5},
      "invalidation": {"price": 4327.0},
      "targets": [{"price": {"price": 4341.0}}],
    })
  evidence = profile.evidence_prefixes[0]
  payload["evidence"] = [{"code": evidence + ("confirmed" if evidence.endswith("_") else "")}]
  if not profile.requires_reaction:
    payload["technical_context"].pop("confirmation", None)
  event_payload = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  match = pol.build_strategy_match(
    event_payload,
    profile=profile,
        now=payload["created_at"] + 1,
  )
  assert match.structural_source == f"go:{scope}"
  assert match.direction == payload["direction"]
  assert match.targets_pips
  assert GO_ORIGIN_TAG in match.tags
  assert "go_strategy_confirmed" in match.tags


@pytest.mark.no_database
def test_range_sweep_adapter_marks_room_as_one_r_two_r_scalp_book():
  """The Go opposite-range target is a room ceiling, not a broker TP."""
  raw = golden()
  payload = raw["payload"]
  payload.update({
    "strategy": "range_sweep",
    "direction": "SELL",
    "timeframe": "M1",
    "entry": {"low": 4194.90, "high": 4196.46},
    "invalidation": {"price": 4198.888285714286},
    "targets": [{"price": {"price": 4165.78}}],
    "evidence": [
      {"code": "m5_range_context"},
      {"code": "m1_edge_sweep"},
      {"code": "m1_reclaim"},
    ],
  })
  payload["technical_context"].pop("confirmation", None)
  event_payload = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  match = pol.build_strategy_match(
    event_payload,
    profile=pol.REVIEWED_SCOPES["range_sweep"],
    now=payload["created_at"] + 1,
  )

  assert match.targets_pips == (291,)
  assert match.absolute_target_price == 4165.78
  assert match.scalp_target_r_multiples == (1.0, 2.0)


@pytest.mark.no_database
def test_breakout_retest_scalp_v3_opportunity_is_accepted_without_python_recomputation():
  """A real Go Breakout Retest Scalp V3 opportunity (XAU, 1:1 target, M5
  structure flip confirmed on M1) goes through the adapter as emitted: the
  evidence codes the Go strategy now publishes satisfy the strategy's own
  evidence contract and its geometry reaches the match unchanged."""
  raw = golden()
  payload = raw["payload"]
  payload.update({
    "strategy": "scalp_breakout_retest",
    "direction": "BUY",
    "timeframe": "M1",
    "entry": {"low": 4143.508, "high": 4145.572},
    "invalidation": {"price": 4143.35},
    "targets": [{"price": {"price": 4147.78}}],
    "evidence": [
      {"code": "m5_breakout_level_m1_swing_high"},
      {"code": "m5_breakout_accepted"},
      {"code": "m5_breakout_retest_confirmed"},
      {"code": "m1_execution_confirmed"},
    ],
  })
  payload["technical_context"].pop("confirmation", None)
  event_payload = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  match = pol.build_strategy_match(
    event_payload,
    profile=pol.REVIEWED_SCOPES["scalp_breakout_retest"],
    now=payload["created_at"] + 1,
  )
  assert match.structural_source == "go:scalp_breakout_retest"
  assert match.direction == "BUY"
  assert match.targets_pips == (22,)
  assert match.absolute_target_price == 4147.78


@pytest.mark.no_database
def test_breakout_retest_scalp_without_its_own_evidence_is_rejected():
  raw = golden()
  payload = raw["payload"]
  payload.update({
    "strategy": "scalp_breakout_retest",
    "direction": "BUY",
    "timeframe": "M1",
    "entry": {"low": 4143.5, "high": 4145.5},
    "invalidation": {"price": 4143.0},
    "targets": [{"price": {"price": 4148.0}}],
    "evidence": [{"code": "m5_supply_zone_fresh"}],
  })
  payload["technical_context"].pop("confirmation", None)
  event_payload = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  with pytest.raises(pol.AdapterRejection) as rejected:
    pol.build_strategy_match(
      event_payload,
      profile=pol.REVIEWED_SCOPES["scalp_breakout_retest"],
      now=payload["created_at"] + 1,
    )
  assert rejected.value.code == "strategy_evidence_mismatch"


def _no_reaction_match(*, entry_low, entry_high, opportunity_id, scope="box_breakout"):
  profile = pol.REVIEWED_SCOPES[scope]
  raw = golden()
  raw["payload"]["id"] = opportunity_id
  raw["payload"]["strategy"] = scope
  raw["payload"]["direction"] = "BUY"
  raw["payload"]["timeframe"] = "M5"
  raw["payload"]["entry"] = {"low": entry_low, "high": entry_high}
  raw["payload"]["invalidation"] = {"price": entry_low - 5.0}
  raw["payload"]["targets"] = [{"price": {"price": entry_high + 10.0}}]
  raw["payload"]["evidence"] = [{"code": profile.evidence_prefixes[0]}]
  raw["payload"]["technical_context"].pop("confirmation", None)
  event_payload = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  return pol.build_strategy_match(
    event_payload, profile=profile, now=raw["payload"]["created_at"] + 1,
  )


@pytest.mark.no_database
def test_repeated_sliding_window_opportunities_share_one_thesis():
  """Incident 2026-09-29: box_breakout (no persistent Go-side zone object,
  unlike supply/demand's reaction.zone_id) re-fired on the same real GBPJPY
  breakout across several M5 closes, each with a distinct deterministic
  opportunity ID because its SetupKey includes the current sliding window's
  own bounds. Before this fix, thesis_id fell back to payload.id - unique by
  construction - so every re-fire became its own thesis and its own plan.
  Two overlapping-zone re-observations of the same strategy/direction/symbol
  must collapse onto one thesis_id, the same guarantee supply/demand already
  had, or `create_setup`'s active-thesis claim never engages.
  """
  first = _no_reaction_match(
    entry_low=4352.5, entry_high=4356.0, opportunity_id="opp_a",
  )
  # A later re-evaluation of essentially the same breakout: same zone
  # midpoint (the level the box compressed around), slightly narrower band -
  # exactly the kind of sliding-window drift that produced a fresh
  # deterministic ID in the incident.
  nearby = _no_reaction_match(
    entry_low=4353.25, entry_high=4355.25, opportunity_id="opp_b",
  )
  assert first.thesis_id == nearby.thesis_id, (
    "overlapping re-observations of the same sliding-window strategy must "
    "share one thesis, or duplicate plans can be built for the same setup"
  )


@pytest.mark.no_database
def test_genuinely_distant_opportunities_get_different_theses():
  near = _no_reaction_match(
    entry_low=4352.5, entry_high=4356.0, opportunity_id="opp_a",
  )
  # Many ATRs away: a real, independent setup, not a re-observation.
  far = _no_reaction_match(
    entry_low=4400.0, entry_high=4403.5, opportunity_id="opp_c",
  )
  assert near.thesis_id != far.thesis_id, (
    "bucketing must not merge genuinely independent setups just because "
    "they share a strategy/symbol/direction"
  )


# ---- runner: durable ledger, Redis match store ----------------------------------

pytestmark_db = pytest.mark.asyncio


class Harness:
  def __init__(self, sql, monkeypatch):
    self.clock = Clock(now=time.time())
    self.repo = PostgresAnalysisOpportunityRepository()
    self.policy = pol.GoOpportunityPolicy(self.repo, clock=self.clock, multiple_matches_enabled=lambda: True)
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

  async def activate(self, **_ignored):
    await self._ensure()
    # The live Kafka consumer is active from process start. There is no
    # The live Kafka consumer is already active in this fixture.

  async def decisions(self):
    await self._ensure()
    rows = await self.sql.fetch("SELECT outcome, reason, mode FROM analysis_shadow_decisions ORDER BY decision_id")
    return [dict(r) for r in rows]


@pytest.fixture
def h(sql, monkeypatch):
  install_runtime_overrides(monkeypatch, {
    "analysis.technical_authority.consumer_enabled": True,
    "instruments.XAU.stop_envelope.max_pips": 65,
  })
  return Harness(sql, monkeypatch)


@pytest.mark.asyncio
async def test_unconfigured_scope_defaults_to_go_in_global_go_mode(h):
  now = int(h.clock.now)
  assert await h.deliver(event(now)) == "match_written"
  assert await redis_state.get_client().get(strategy_matches_key("XAU")) is not None
  assert await h.decisions() == [{"outcome": "match_written", "reason": "go_live", "mode": "go"}]


@pytest.mark.no_database
def test_xau_observes_but_does_not_trade_the_contained_strategies():
  assert pol.observe_only_strategies("XAU") == {"ifvg", "liquidity_sweep", "range_sweep"}
  # Containment is instrument-owned: the other instruments and every strategy
  # not named (Key Level, Breakout Retest Scalp, ...) are untouched.
  for symbol in ("EURUSD", "GBPUSD", "GBPJPY", "USDJPY"):
    assert pol.observe_only_strategies(symbol) == frozenset()
  assert not {"key_level", "scalp_breakout_retest", "fvg"} & pol.observe_only_strategies("XAU")


@pytest.mark.asyncio
async def test_a_contained_xau_strategy_is_recorded_but_never_becomes_a_match(h):
  await h.activate()
  raw = golden(int(h.clock.now))
  raw["payload"]["strategy"] = "ifvg"
  raw["payload"]["evidence"] = [{"code": "m5_ifvg_confirmed"}]
  ev = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  assert await h.deliver(ev) == "not_adapted"
  assert await redis_state.get_client().get(strategy_matches_key("XAU")) is None
  assert await h.decisions() == [{"outcome": "not_adapted", "reason": "execution_contained", "mode": "go"}]


@pytest.mark.asyncio
async def test_live_go_writes_a_confirmed_setup_and_one_match_idempotently(h):
  await h.activate()
  ev = event(int(h.clock.now))
  assert await h.deliver(ev) == "match_written"
  client = redis_state.get_client()
  matches = deserialize_matches(await client.get(strategy_matches_key("XAU")))
  assert [m.match_id for m in matches] == ["go_opp_golden_supply_xau"]
  assert GO_ORIGIN_TAG in matches[0].tags
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
  await h.activate()
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
  await h.activate()
  now = int(h.clock.now)
  raw = golden(now)
  # Every catalog ID the Go engine can actually emit (registry.go's knownIDs) has a
  # reviewed adapter now; this exercises the defensive fallback for an event the
  # producer's own schema does not close off (the contract's `strategy` is free text).
  raw["payload"]["strategy"] = "not_a_registered_go_strategy"
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
  await h.activate()
  h.policy = pol.GoOpportunityPolicy(h.repo, clock=h.clock, multiple_matches_enabled=lambda: False)
  assert await h.deliver(event(int(h.clock.now))) == "not_adapted"
  assert (await h.decisions())[-1]["reason"] == "multiple_matches_disabled"


@pytest.mark.asyncio
async def test_live_go_does_not_consult_a_legacy_store(h):
  h.policy = pol.GoOpportunityPolicy(h.repo, clock=h.clock, multiple_matches_enabled=lambda: True)
  assert await h.deliver(event(int(h.clock.now))) == "match_written"
  assert (await h.decisions())[-1]["reason"] == "go_live"


@pytest.mark.asyncio
async def test_go_terminal_withdraws_the_match_and_invalidates_the_setup(h):
  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  term = parse_analysis_event(InvalidationTopic, json.dumps(_invalidated(payload={
    "opportunity_id": "opp_golden_supply_xau", "symbol": "XAU", "strategy": "supply",
    "reason_code": "ZONE_INVALIDATED", "invalidated_at": int(h.clock.now),
  })))
  assert await h.deliver(term, InvalidationTopic) == "match_withdrawn"
  client = redis_state.get_client()
  assert await client.get(strategy_matches_key("XAU")) is None
  assert (await load_setup(client, "go_opp_golden_supply_xau")).state == INVALIDATED


def _arbitration_event(**overrides):
  event = {
    "event_id": "evt-arb-1", "event_type": ArbitrationTopic,
    "event_version": 1, "occurred_at": 102, "produced_at": 103,
    "producer": "apexvoid-analysis-engine", "correlation_id": "corr-arb-1",
    "payload": {
      "opportunity_id": "opp_golden_supply_xau", "symbol": "XAU",
      "status": "winner", "reason_code": "ranked_single_direction", "decided_at": 102,
    },
  }
  event.update(overrides)
  return parse_analysis_event(ArbitrationTopic, json.dumps(event))


@pytest.mark.asyncio
async def test_arbitration_decision_annotates_the_live_match(h):
  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  client = redis_state.get_client()

  assert await h.policy.on_arbitration_decision(_arbitration_event()) == "arbitration_updated"
  matches = deserialize_matches(await client.get(strategy_matches_key("XAU")))
  assert len(matches) == 1
  assert matches[0].arbitration_status == "winner"
  assert matches[0].arbitration_reason_code == "ranked_single_direction"

  # Republishing the identical decision is a no-op, not an error.
  assert await h.policy.on_arbitration_decision(_arbitration_event()) == "unchanged"

  # A changed decision updates the same match in place.
  held = _arbitration_event(payload={
    "opportunity_id": "opp_golden_supply_xau", "symbol": "XAU",
    "status": "conflict_held", "reason_code": "opposite_direction_conflict",
    "conflicting_with": ["opp_golden_supply_xau", "opp_rival"], "decided_at": 103,
  })
  assert await h.policy.on_arbitration_decision(held) == "arbitration_updated"
  matches = deserialize_matches(await client.get(strategy_matches_key("XAU")))
  assert matches[0].arbitration_status == "conflict_held"
  assert matches[0].arbitration_reason_code == "opposite_direction_conflict"


@pytest.mark.asyncio
async def test_arbitration_cannot_repromote_a_match_absent_from_go_live_book(h):
  from app.autotrade.go_live_opportunities import go_live_opportunities_key

  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  client = redis_state.get_client()
  await client.set(
    go_live_opportunities_key("XAU"),
    json.dumps({"symbol": "XAU", "generated_at": int(h.clock.now), "ids": []}),
  )

  assert await h.policy.on_arbitration_decision(_arbitration_event()) == "arbitration_updated"
  matches = deserialize_matches(await client.get(strategy_matches_key("XAU")))
  assert matches[0].arbitration_status == "suppressed"
  assert matches[0].arbitration_reason_code == "go_opportunity_not_live"

  # The same redelivered event must not restore the stale winner.
  assert await h.policy.on_arbitration_decision(_arbitration_event()) == "unchanged"
  matches = deserialize_matches(await client.get(strategy_matches_key("XAU")))
  assert matches[0].arbitration_status == "suppressed"


@pytest.mark.asyncio
async def test_arbitration_decision_annotates_thesis_correlation(h):
  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  client = redis_state.get_client()

  correlated = _arbitration_event(payload={
    "opportunity_id": "opp_golden_supply_xau", "symbol": "XAU",
    "status": "winner", "reason_code": "ranked_single_direction",
    # Go's own raw opportunity_ids - go_opportunity_policy must translate
    # these through match_id_for, matching every other Go-origin match_id.
    "thesis_id": "opp_golden_supply_xau", "merged_with": ["opp_sibling"],
    "decided_at": 102,
  })
  assert await h.policy.on_arbitration_decision(correlated) == "arbitration_updated"
  matches = deserialize_matches(await client.get(strategy_matches_key("XAU")))
  assert matches[0].go_thesis_id == "go_opp_golden_supply_xau"
  assert matches[0].go_merged_with == ("go_opp_sibling",)


@pytest.mark.asyncio
async def test_arbitration_decision_for_an_unknown_match_is_dropped(h):
  await h.activate()
  # No creation event delivered - nothing live for this opportunity_id yet
  # (or it was already withdrawn by on_terminal). Must not raise or create
  # a phantom match.
  assert await h.policy.on_arbitration_decision(_arbitration_event()) == "arbitration_stored_pending"
  assert await redis_state.get_client().get(strategy_matches_key("XAU")) is None


@pytest.mark.asyncio
async def test_pending_arbitration_is_joined_when_lifecycle_event_arrives_after_it(h):
  await h.activate()
  assert await h.policy.on_arbitration_decision(_arbitration_event()) == "arbitration_stored_pending"
  assert await h.deliver(event(int(h.clock.now))) == "match_written"
  matches = deserialize_matches(await redis_state.get_client().get(strategy_matches_key("XAU")))
  assert matches[0].arbitration_status == "winner"


@pytest.mark.asyncio
async def test_stale_live_projection_does_not_suppress_a_current_arbitration(h):
  """Kafka can beat the Redis projection during an engine restart.

  An older live-set document is not evidence that a newly decided Go
  opportunity is absent; the next projection is authoritative.  The policy
  must therefore retain the winner instead of writing the permanent
  ``go_opportunity_not_live`` suppression used for a current, non-member ID.
  """
  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  client = redis_state.get_client()
  from app.autotrade.go_live_opportunities import go_live_opportunities_key
  await client.set(
    go_live_opportunities_key("XAU"),
    json.dumps({"generated_at": 100, "ids": []}),
  )
  assert await h.policy.on_arbitration_decision(_arbitration_event()) == "arbitration_updated"
  matches = deserialize_matches(await client.get(strategy_matches_key("XAU")))
  assert matches[0].arbitration_status == "winner"


# ---- end to end: Go event -> match -> REAL V8 plan --------------------------------

def _spot(bid, ask):
  return worker.AutoTradeSpot(price=(bid + ask) / 2, ts=int(time.time()), fresh=True, bid=bid, ask=ask)


async def _outcome(client, match):
  from app.autotrade.route_outcome import route_outcome_key
  return json.loads(await client.get(route_outcome_key(match.symbol, match.match_id)))


@pytest.mark.asyncio
async def test_resting_go_zone_is_a_non_executable_observation(h):
  await h.activate()
  raw = golden(int(h.clock.now))
  del raw["payload"]["technical_context"]["confirmation"]
  assert await h.deliver(parse_analysis_event(OpportunityTopic, json.dumps(raw))) == "rejected"
  assert (await h.decisions())[-1]["reason"] == "reaction_confirmation_unavailable"
  assert await redis_state.get_client().get(strategy_matches_key("XAU")) is None


def test_missing_go_htf_structure_is_explicitly_neutral():
  raw = golden()
  del raw["payload"]["technical_context"]["higher_timeframes"]
  ev = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  match = pol.build_strategy_match(
    ev, profile=SUPPLY, now=raw["payload"]["created_at"] + 1,
  )
  assert match.htf_bias == "neutral"
  assert "htf_bias_source:go_neutral" in match.tags


def test_m15_is_used_only_after_h1_h4_are_unavailable():
  raw = golden()
  raw["payload"]["technical_context"]["higher_timeframes"] = [{
    "timeframe": "M15", "direction": "BUY", "layer": "internal",
    "reference_time": raw["payload"]["created_at"] - 900,
  }]
  ev = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  match = pol.build_strategy_match(
    ev, profile=SUPPLY, now=raw["payload"]["created_at"] + 1,
  )
  assert match.htf_bias == "up"
  assert "htf_bias_source:go_M15" in match.tags


@pytest.mark.asyncio
async def test_existing_v8_builder_fails_closed_if_htf_is_stripped(h):
  from dataclasses import replace
  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  client = redis_state.get_client()
  base = deserialize_matches(await client.get(strategy_matches_key("XAU")))[0]
  match = replace(base, htf_bias="")
  assert await worker._publish_trade_plan_v8(client, "XAU", _spot(4354.1, 4354.3), match, frames={"M1": _m1_trigger_bar()}) is None
  # A Go-origin match's confirmation policy (GO_ORIGIN_TAG bypass) sets zone_family/
  # require_quote_inside_zone False, so it skips the legacy M5-authoritative "preflight"
  # htf_bias check (v8_missing_htf_bias) a Python-detected match would hit there; it fails
  # closed one stage later, in trade_plan_builder's own htf_bias requirement — still no
  # plan, still the correct cause.
  assert (await _outcome(client, match))["reason_code"] == "missing_htf_bias"


@pytest.mark.asyncio
async def test_go_owned_zone_becomes_a_real_v8_plan_with_real_contract_fields(h):
  """No Python detector or post-adaptation mutation supplies confirmation.

  The Go-pinned resting golden is enriched with the additive confirmed
  reaction/HTF test fixture; the same typed event is consumed by the actual
  adapter and the unchanged V8 policy and publisher.
  """
  await h.activate()
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
async def test_match_write_and_publish_have_no_scope_approval_dependency(h):
  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  client = redis_state.get_client()
  match = deserialize_matches(await client.get(strategy_matches_key("XAU")))[0]
  plan_id = await worker._publish_trade_plan_v8(client, "XAU", _spot(4354.1, 4354.3), match, frames={"M1": _m1_trigger_bar()})
  assert plan_id is not None
  assert await read_plan_state(client, plan_id) == "published"


def worker_route_key(match):
  from app.autotrade.route_outcome import route_outcome_key
  return route_outcome_key(match.symbol, match.match_id)

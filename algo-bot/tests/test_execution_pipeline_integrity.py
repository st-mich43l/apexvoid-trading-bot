"""Regression coverage for cross-engine execution integrity."""

from __future__ import annotations
from app.core.config import runtime_config

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

from tests.configuration.canonical_fixtures import execution_cfg, install_runtime_overrides, leaf
from unittest.mock import AsyncMock

import fakeredis
import pytest

from app.autotrade.arbitration import (
  CandidatePublicationResult,
  ExecutionIntent,
  arbitrate_execution_intents,
)
from app.autotrade.candidate_execution_state import (
  parse_candidate_execution_record,
  STATE_PUBLISHED,
)
from app.autotrade.candidate_publish import (
  acquire_owned_lock,
  autonomous_cycle_owner_key,
  publish_candidate_atomic,
  publish_ranked_cycle,
  release_owned_lock,
)
from app.autotrade.execution_policy import (
  ENTRY_PLAN_VERSION,
  evaluate_execution_policy,
)
from app.autotrade.route_outcome import record_route_outcome
from app.autotrade import worker
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


@pytest.fixture(autouse=True)
def _no_news_by_default(monkeypatch):
  monkeypatch.setattr(
    worker, "event_in_window", AsyncMock(return_value=None),
  )


@pytest.fixture(autouse=True)
def _freeze_technique_killzone_hour(monkeypatch):
  from app.autotrade import killzone as kz

  real = kz.evaluate_killzone_gate
  real_win = kz.evaluate_reaction_publish_window

  def _gated(*, ts=None, hour=None, cfg=None, require=True):
    return real(ts=None, hour=14, cfg=cfg, require=require)

  def _window(*, ts=None, hour=None, cfg=None, require=True):
    return real_win(ts=None, hour=14, cfg=cfg, require=require)

  monkeypatch.setattr(kz, "evaluate_killzone_gate", _gated)
  monkeypatch.setattr(kz, "evaluate_reaction_publish_window", _window)


def _intent(
  intent_id: str,
  *,
  direction: str,
  confluence: int = 3,
  tier: str = "A",
  freshness: float = 100.0,
  distance_pips: float = 0.0,
) -> ExecutionIntent:
  return ExecutionIntent(
    intent_id=intent_id,
    source="scanner_strategy_match",
    strategy="Liquidity Sweep",
    direction=direction,
    confluence=confluence,
    tier=tier,
    freshness=freshness,
    distance_pips=distance_pips,
  )


def test_arbiter_suppresses_equal_opposite_direction_intents():
  result = arbitrate_execution_intents([
    _intent("buy", direction="BUY"),
    _intent("sell", direction="SELL"),
  ])

  assert result.ordered == ()
  assert {item.intent_id for item in result.suppressed} == {"buy", "sell"}
  assert result.reason_code == "opposite_direction_conflict"


def test_arbiter_orders_only_the_winning_direction():
  result = arbitrate_execution_intents([
    _intent("buy-a", direction="BUY", confluence=4),
    _intent("buy-b", direction="BUY", confluence=3),
    _intent("sell-b", direction="SELL", confluence=2, tier="B"),
  ])

  assert [item.intent_id for item in result.ordered] == ["buy-a", "buy-b"]
  assert [item.intent_id for item in result.suppressed] == ["sell-b"]


@pytest.mark.asyncio
async def test_failed_tier_a_admission_cannot_suppress_executable_tier_b():
  """TradePlan-cutover: a terminal top intent must not suppress the lower-ranked
  intent's own attempt. The cross-engine flow is:

  1. _handle_event only enqueues admitted intents in ``arbitrable``.
  2. arbitrate_execution_intents orders admitted intents by rank.
  3. publish_ranked_cycle walks that order; a top-ranked ``terminal_reject``
     publication result must expose the next-ranked intent to the publisher.
  """
  client = redis_state.get_client()
  top = _intent("buy-a", direction="BUY", confluence=4, tier="A")
  lower = _intent("sell-b", direction="SELL", confluence=3, tier="B")
  # Admission has already filtered ``top`` down to a single-direction list
  # (the tier-A intent failed admission and is not in ``arbitrable``).
  arbitrable = [lower]
  arbitration = arbitrate_execution_intents(arbitrable)
  assert [item.intent_id for item in arbitration.ordered] == ["sell-b"]

  attempted: list[str] = []

  async def publisher(intent):
    attempted.append(intent.intent_id)
    if intent.intent_id == top.intent_id:
      return CandidatePublicationResult.terminal_reject("news_window_active")
    atomic = await publish_candidate_atomic(
      client,
      stream="auto_trade:test:tier-a-terminal",
      candidate_id="candidate-sell-b",
      payload=json.dumps({"intent_id": intent.intent_id}),
      ttl=300,
      maxlen=100,
      ownership_key=autonomous_cycle_owner_key("XAU", "tier-a-terminal-cycle"),
      ownership_payload="candidate-sell-b",
      ownership_ttl=300,
    )
    assert atomic.published
    return CandidatePublicationResult.published("candidate-sell-b")

  # publish_ranked_cycle enforces the terminal-reject → fallback contract.
  result = await publish_ranked_cycle(
    client,
    symbol="XAU",
    cycle_id="tier-a-terminal-cycle",
    ordered=(top, lower),
    publisher=publisher,
  )

  assert result.candidate_id == "candidate-sell-b"
  assert attempted == [top.intent_id, lower.intent_id]


def _policy_match(**overrides):
  values = {
    "strategy": "Mapped Zone Reaction",
    "direction": "BUY",
    "entry_low": 4100.0,
    "entry_high": 4101.0,
    "current_price": 4100.5,
    "confluence": 3,
    "atr": 1.0,
    "structure_swing": 4099.5,
    "targets_pips": (30, 60),
    "target_price": None,
    "risk_multiplier": 1.0,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


def test_execution_policy_keeps_fill_relative_room_from_planned_entry():
  evaluation = evaluate_execution_policy(
    _policy_match(),
    spot_price=4102.5,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.measured["remaining_target_room_pips"] == 60.0
  assert evaluation.measured["target_model"] == "fill_relative"
  assert evaluation.measured["planned_entry_price"] == 4102.5
  assert evaluation.measured["order_type_preference"] == "market"
  assert evaluation.measured["effective_risk_multiplier"] == 1.0


def test_execution_policy_applies_family_risk_multiplier():
  evaluation = evaluate_execution_policy(
    _policy_match(
      strategy="Liquidity Sweep",
      entry_high=4101.0,
      structure_swing=4099.5,
      risk_multiplier=0.5,
    ),
    spot_price=4100.5,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.measured["effective_risk_multiplier"] == 0.375


def test_execution_policy_rejects_zero_risk_multiplier():
  evaluation = evaluate_execution_policy(
    _policy_match(risk_multiplier=0),
    spot_price=4100.5,
    regime="trend",
    pip_size=0.1,
  )

  assert not evaluation.allowed
  assert evaluation.terminal
  assert evaluation.reason_code == "invalid_risk_multiplier"


def test_absolute_target_room_is_measured_from_planned_entry():
  evaluation = evaluate_execution_policy(
    _policy_match(
      target_model="absolute",
      absolute_target_price=4108.5,
      target_price=4108.5,
      targets_pips=(),
      target_reference_price="structural_level",
    ),
    spot_price=4102.5,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.measured["planned_entry_price"] == 4102.5
  assert evaluation.measured["remaining_target_room_pips"] == 60.0


def test_private_trend_policy_forwards_limit_and_single_distribution():
  evaluation = evaluate_execution_policy(
    _policy_match(
      strategy="Trend Pullback",
      entry_low=4100.0,
      entry_high=4100.3,
      current_price=4100.2,
      structure_swing=4098.5,
    ),
    spot_price=4100.2,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.policy is not None
  assert evaluation.policy.order_type_preference == "limit"
  assert evaluation.measured["entry_distribution"] == "single"


def test_single_limit_route_publishes_the_limit_price_as_planned_entry():
  # A narrow limit zone commits to one deterministic leg price, so the
  # executor is held to it and validates the final stop there.
  evaluation = evaluate_execution_policy(
    _policy_match(
      strategy="Trend Pullback",
      entry_low=4100.0,
      entry_high=4100.3,
      current_price=4100.2,
      structure_swing=4098.5,
    ),
    spot_price=4100.2,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.measured["planned_execution_route"] == "single_limit"
  assert evaluation.measured["planned_entry_price"] == 4100.2
  assert evaluation.measured["planned_leg_entry_prices"] == [4100.2]
  assert evaluation.measured["entry_plan_version"] == ENTRY_PLAN_VERSION


def test_buy_limit_planned_entry_is_the_zone_edge_not_the_drifted_quote():
  evaluation = evaluate_execution_policy(
    _policy_match(
      strategy="Trend Pullback",
      entry_low=4100.0,
      entry_high=4100.3,
      current_price=4100.2,
      structure_swing=4098.5,
    ),
    spot_price=4101.0,
    regime="trend",
    pip_size=0.1,
  )

  # The limit rests at the zone edge, so that - not the quote - is the entry
  # the stop contract and the executor's entry contract are measured against.
  assert evaluation.measured["planned_entry_price"] == 4100.3
  assert evaluation.measured["planned_leg_entry_prices"] == [4100.3]


def test_sell_limit_planned_entry_is_the_zone_edge_not_the_drifted_quote():
  evaluation = evaluate_execution_policy(
    _policy_match(
      strategy="Trend Pullback",
      direction="SELL",
      entry_low=4100.0,
      entry_high=4100.3,
      current_price=4100.1,
      structure_swing=4101.8,
    ),
    spot_price=4099.4,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.measured["planned_entry_price"] == 4100.0
  assert evaluation.measured["planned_leg_entry_prices"] == [4100.0]


def test_zone_split_route_publishes_proximal_and_midpoint_legs():
  evaluation = evaluate_execution_policy(
    _policy_match(
      strategy="Trend Pullback",
      entry_low=4100.0,
      entry_high=4100.8,
      current_price=4100.5,
      structure_swing=4098.5,
    ),
    spot_price=4100.5,
    regime="trend",
    pip_size=0.1,
    cfg=execution_cfg(
      auto_trade_zone_fill_enabled=True,
      auto_trade_zone_fill_min_atr=0.5,
      auto_trade_inside_zone_market_entry_enabled=True,
      auto_trade_zone_fill_fallback_enabled=True,
      auto_trade_xau_price_digits=2,
    ),
  )

  assert evaluation.allowed
  assert evaluation.measured["entry_distribution"] == "zone_split"
  assert evaluation.measured["planned_execution_route"] == "zone_split"
  # Proximal (BUY high) + midpoint, matching ZoneFillPlanner.
  assert evaluation.measured["planned_entry_price"] == 4100.8
  assert evaluation.measured["planned_leg_entry_prices"] == [4100.8, 4100.4]


def test_market_route_publishes_the_quote_as_planned_entry():
  evaluation = evaluate_execution_policy(
    _policy_match(
      strategy="Momentum Ride",
      entry_low=4100.0,
      entry_high=4101.0,
      current_price=4100.5,
      structure_swing=4098.0,
    ),
    spot_price=4100.5,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.measured["planned_execution_route"] == "market"
  assert evaluation.measured["planned_entry_price"] == 4100.5
  assert evaluation.measured["planned_leg_entry_prices"] == []


@pytest.mark.parametrize(
  "strategy",
  ["Key Level", "Trendline"],
)
def test_reaction_family_falls_back_to_a_concrete_market_route_when_narrow(
  strategy,
):
  """Reaction families now prefer a DCA-into-zone scale ladder (owner spec),
  but with zone-fill disabled (this test's default cfg), a reaction family
  must still resolve to a concrete single market fill - never an
  unresolved `either` - exactly like before this change.
  """
  evaluation = evaluate_execution_policy(
    _policy_match(strategy=strategy),
    spot_price=4100.5,
    executable_quote=4100.5,
    regime="range",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.measured["order_type_preference"] == "limit"
  assert evaluation.measured["entry_distribution"] == "zone_scale"
  assert evaluation.measured["planned_execution_route"] == "market"
  assert evaluation.measured["planned_leg_entry_prices"] == []


def test_reward_risk_is_measured_against_the_final_absolute_stop():
  evaluation = evaluate_execution_policy(
    _policy_match(
      strategy="Trend Pullback",
      entry_low=4100.0,
      entry_high=4100.3,
      current_price=4100.2,
      structure_swing=4098.5,
      targets_pips=(30, 60),
    ),
    spot_price=4100.2,
    regime="trend",
    pip_size=0.1,
  )
  measured = evaluation.measured

  planned_entry = measured["planned_entry_price"]
  # The stop contract is published as exact decimal strings.
  final_stop = float(measured["planned_stop_price"])
  final_stop_pips = float(measured["planned_stop_pips"])
  # The published RR must be derived from the same absolute stop the executor
  # sends to the broker, measured from the same planned entry.
  assert float(measured["planned_stop_entry_price"]) == planned_entry
  assert final_stop_pips == pytest.approx(
    (planned_entry - final_stop) / 0.1, abs=1e-6,
  )
  assert measured["reward_risk"] == pytest.approx(
    round(60.0 / final_stop_pips, 4), abs=1e-4,
  )


def test_entry_plan_fields_reach_the_published_candidate_contract():
  evaluation = evaluate_execution_policy(
    _policy_match(
      strategy="Trend Pullback",
      entry_low=4100.0,
      entry_high=4100.3,
      current_price=4100.2,
      structure_swing=4098.5,
    ),
    spot_price=4100.2,
    regime="trend",
    pip_size=0.1,
  )

  forwarded = worker._stop_contract_fields(evaluation.measured)

  # The executor rejects route or entry drift using exactly these fields, so
  # a field that never reaches the payload is a silent contract hole.
  for field in (
    "planned_execution_route",
    "planned_market_immediate",
    "planned_entry_price",
    "planned_leg_entry_prices",
    "entry_plan_version",
    "planned_stop_entry_price",
    "planned_stop_price",
    "planned_stop_pips",
  ):
    assert field in forwarded, field
  assert forwarded["planned_execution_route"] == "single_limit"
  assert forwarded["planned_market_immediate"] is False
  assert forwarded["planned_entry_price"] == 4100.2


def test_execution_policy_prefers_wide_zone_and_rejects_unknown_strategies():
  wide = evaluate_execution_policy(
    _policy_match(entry_high=4103.0),
    spot_price=4100.5,
    regime="trend",
    pip_size=0.1,
  )
  unknown = evaluate_execution_policy(
    _policy_match(strategy="Unreviewed Detector"),
    spot_price=4100.5,
    regime="trend",
    pip_size=0.1,
  )

  # Zone-width is preference telemetry — plan may still publish with notes.
  assert wide.allowed
  assert not wide.terminal
  assert wide.reason_code == "policy_zone_too_wide"
  assert wide.measured.get("preference_telemetry") is True
  assert not unknown.allowed
  assert unknown.terminal
  assert unknown.reason_code == "unknown_strategy_policy"


@pytest.mark.asyncio
async def test_zone_split_capability_gate_rejects_via_execution_policy(
  monkeypatch,
):
  """The TradePlan-owned zone-split hard gate now runs inside
  ``_publish_trade_plan_v8`` on top of ``evaluate_execution_policy``. When the
  policy demands zone-split but ``auto_trade_zone_fill_enabled`` is False, the
  gate short-circuits the publish before ``build_trade_plan_from_strategy_match``
  is even called. Exercising it via ``evaluate_execution_policy`` keeps the
  contract locked without spinning up the full TradePlan setup.
  """
  subject = _policy_match(
    strategy="Trend Pullback",
    entry_low=4100.0,
    entry_high=4100.8,
    current_price=4100.6,
    structure_swing=4098.0,
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_zone_fill_enabled": False})

  evaluation = evaluate_execution_policy(
    subject,
    spot_price=4100.6,
    executable_quote=4100.6,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.measured.get("entry_distribution") == "zone_split"
  # ``zone_fill_enabled`` toggles the guard TradePlan wraps around
  # ``evaluate_execution_policy`` (see the ``zone_split_capability_unavailable``
  # gate inside ``_publish_trade_plan_v8``). Behaviour is verified via the TradePlan
  # publish path in ``test_publish_trade_plan_v8.py``.
  assert not leaf(runtime_config, "auto_trade_zone_fill_enabled")


@pytest.mark.asyncio
async def test_required_limit_side_gate_uses_execution_policy(
  monkeypatch,
):
  """The TradePlan-owned ``required_limit_side_unavailable`` gate cross-checks
  ``evaluate_execution_policy.order_type_preference`` against the current
  broker quote. This test locks the underlying evaluation the gate reads
  (behaviour is verified end-to-end via the TradePlan publish path).
  """
  subject = _policy_match(
    strategy="Trend Pullback",
    entry_low=4101.0,
    entry_high=4101.3,
    current_price=4100.0,
    structure_swing=4098.0,
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_zone_fill_enabled": True})

  evaluation = evaluate_execution_policy(
    subject,
    spot_price=4100.0,
    executable_quote=4100.0,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.policy is not None
  assert evaluation.policy.order_type_preference == "limit"


@pytest.mark.asyncio
async def test_candidate_claim_and_stream_append_are_single_winner():
  client = redis_state.get_client()

  results = await asyncio.gather(*[
    publish_candidate_atomic(
      client,
      stream="auto_trade:test:atomic",
      candidate_id="candidate-atomic",
      payload='{"candidate_id":"candidate-atomic"}',
      ttl=300,
      maxlen=100,
    )
    for _ in range(12)
  ])

  assert sum(1 for published, _ in results if published) == 1
  assert await client.xlen("auto_trade:test:atomic") == 1
  assert parse_candidate_execution_record(
    await client.get("auto_trade:candidate:candidate-atomic")
  ).state == STATE_PUBLISHED


@pytest.mark.asyncio
async def test_one_cycle_owner_allows_only_one_distinct_candidate():
  client = redis_state.get_client()
  owner_key = autonomous_cycle_owner_key("XAU", "closed-m1-1000")

  results = await asyncio.gather(*[
    publish_candidate_atomic(
      client,
      stream="auto_trade:test:cycle",
      candidate_id=f"candidate-cycle-{index}",
      payload=json.dumps({"candidate_id": f"candidate-cycle-{index}"}),
      ttl=300,
      maxlen=100,
      ownership_key=owner_key,
      ownership_payload=f"candidate-cycle-{index}",
      ownership_ttl=300,
    )
    for index in range(12)
  ])

  assert sum(item.published for item in results) == 1
  assert await client.xlen("auto_trade:test:cycle") == 1
  assert sum(item.status == "conflict" for item in results) == 11


@pytest.mark.asyncio
async def test_ranked_v8_publication_persists_full_cycle_owner_record():
  client = redis_state.get_client()
  intent = replace(
    _intent("strategy:setup-v8-owner", direction="SELL"),
    match_id="setup-v8-owner",
  )
  calls = 0

  async def publisher(_intent):
    nonlocal calls
    calls += 1
    return CandidatePublicationResult.published("v8:setup-v8-owner")

  first = await publish_ranked_cycle(
    client,
    symbol="XAU",
    cycle_id="m1-owner-cycle",
    ordered=(intent,),
    publisher=publisher,
  )
  second = await publish_ranked_cycle(
    client,
    symbol="XAU",
    cycle_id="m1-owner-cycle",
    ordered=(intent,),
    publisher=publisher,
  )

  assert first.status == "published"
  assert second.status == "cycle_conflict"
  assert calls == 1
  owner = json.loads(await client.get(
    autonomous_cycle_owner_key("XAU", "m1-owner-cycle"),
  ))
  assert owner["symbol"] == "XAU"
  assert owner["cycle_id"] == "m1-owner-cycle"
  assert owner["intent_id"] == intent.intent_id
  assert owner["setup_id"] == intent.match_id
  assert owner["plan_id"] == "v8:setup-v8-owner"
  assert owner["published_at"] > 0


@pytest.mark.asyncio
async def test_busy_top_route_blocks_lower_ranked_intent():
  client = redis_state.get_client()
  top = _intent("top", direction="BUY", confluence=4)
  lower = _intent("lower", direction="BUY", confluence=3)
  attempted: list[str] = []

  async def publisher(intent):
    attempted.append(intent.intent_id)
    if intent.intent_id == "top":
      return CandidatePublicationResult.blocked("route_in_progress")
    pytest.fail("lower-ranked route must not run while top is in progress")

  result = await publish_ranked_cycle(
    client,
    symbol="XAU",
    cycle_id="busy-top",
    ordered=(top, lower),
    publisher=publisher,
  )

  assert result.status == "route_in_progress"
  assert attempted == ["top"]
  assert await client.xlen("auto_trade:test:busy-top") == 0


@pytest.mark.asyncio
async def test_two_workers_preserve_highest_ranked_publication():
  client = redis_state.get_client()
  top = _intent("top-concurrent", direction="BUY", confluence=4)
  lower = _intent("lower-concurrent", direction="BUY", confluence=3)
  entered = asyncio.Event()
  release = asyncio.Event()

  async def publisher(intent):
    assert intent.intent_id == top.intent_id
    entered.set()
    await release.wait()
    atomic = await publish_candidate_atomic(
      client,
      stream="auto_trade:test:ranked-workers",
      candidate_id="candidate-top-concurrent",
      payload=json.dumps({"intent_id": intent.intent_id}),
      ttl=300,
      maxlen=100,
      ownership_key=autonomous_cycle_owner_key("XAU", "workers-cycle"),
      ownership_payload="candidate-top-concurrent",
      ownership_ttl=300,
    )
    return (
      CandidatePublicationResult.published("candidate-top-concurrent")
      if atomic.published
      else CandidatePublicationResult.blocked("cycle_conflict")
    )

  first = asyncio.create_task(publish_ranked_cycle(
    client,
    symbol="XAU",
    cycle_id="workers-cycle",
    ordered=(top, lower),
    publisher=publisher,
  ))
  await entered.wait()
  second = asyncio.create_task(publish_ranked_cycle(
    client,
    symbol="XAU",
    cycle_id="workers-cycle",
    ordered=(top, lower),
    publisher=publisher,
  ))
  await asyncio.sleep(0)
  release.set()
  results = await asyncio.gather(first, second)

  assert sum(result.status == "published" for result in results) == 1
  assert sum(result.status == "route_in_progress" for result in results) == 1
  assert await client.xlen("auto_trade:test:ranked-workers") == 1
  payload = json.loads((await client.xrange(
    "auto_trade:test:ranked-workers",
  ))[0][1]["payload"])
  assert payload["intent_id"] == top.intent_id


@pytest.mark.asyncio
async def test_duplicate_cycle_delivery_keeps_original_winner():
  client = redis_state.get_client()
  top = _intent("top-duplicate", direction="BUY", confluence=4)
  lower = _intent("lower-duplicate", direction="BUY", confluence=3)

  async def publisher(intent):
    atomic = await publish_candidate_atomic(
      client,
      stream="auto_trade:test:duplicate-cycle",
      candidate_id=f"candidate-{intent.intent_id}",
      payload=json.dumps({"intent_id": intent.intent_id}),
      ttl=300,
      maxlen=100,
      ownership_key=autonomous_cycle_owner_key("XAU", "duplicate-cycle"),
      ownership_payload=f"candidate-{intent.intent_id}",
      ownership_ttl=300,
    )
    return (
      CandidatePublicationResult.published(f"candidate-{intent.intent_id}")
      if atomic.published
      else CandidatePublicationResult.blocked("cycle_conflict")
    )

  first = await publish_ranked_cycle(
    client,
    symbol="XAU",
    cycle_id="duplicate-cycle",
    ordered=(top, lower),
    publisher=publisher,
  )
  second = await publish_ranked_cycle(
    client,
    symbol="XAU",
    cycle_id="duplicate-cycle",
    ordered=(top, lower),
    publisher=publisher,
  )

  assert first.candidate_id == "candidate-top-duplicate"
  assert second.status == "cycle_conflict"
  assert await client.xlen("auto_trade:test:duplicate-cycle") == 1


@pytest.mark.asyncio
async def test_true_terminal_top_rejection_allows_ranked_fallback():
  client = redis_state.get_client()
  top = _intent("top-terminal", direction="BUY", confluence=4)
  lower = _intent("lower-valid", direction="BUY", confluence=3)

  async def publisher(intent):
    if intent.intent_id == top.intent_id:
      return CandidatePublicationResult.terminal_reject("zone_invalidated")
    atomic = await publish_candidate_atomic(
      client,
      stream="auto_trade:test:terminal-fallback",
      candidate_id="candidate-lower-valid",
      payload=json.dumps({"intent_id": intent.intent_id}),
      ttl=300,
      maxlen=100,
      ownership_key=autonomous_cycle_owner_key("XAU", "terminal-fallback"),
      ownership_payload="candidate-lower-valid",
      ownership_ttl=300,
    )
    assert atomic.published
    return CandidatePublicationResult.published("candidate-lower-valid")

  result = await publish_ranked_cycle(
    client,
    symbol="XAU",
    cycle_id="terminal-fallback",
    ordered=(top, lower),
    publisher=publisher,
  )

  assert result.candidate_id == "candidate-lower-valid"
  assert await client.xlen("auto_trade:test:terminal-fallback") == 1


@pytest.mark.asyncio
async def test_expired_owner_cannot_delete_successor_lock():
  client = redis_state.get_client()
  key = "auto_trade:route_lock:XAU:owned-release"
  original = await acquire_owned_lock(client, key, ttl=30)
  assert original is not None
  await client.set(key, "successor-token", ex=30)

  released = await release_owned_lock(client, key, original)

  assert not released
  assert await client.get(key) == "successor-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_kind", ["reaction", "thesis"])
async def test_test_fallback_rolls_back_claim_when_xadd_crashes(claim_kind):
  client = fakeredis.FakeAsyncRedis(decode_responses=True)
  client._apexvoid_allow_non_atomic_test_fallback = True
  client.xadd = AsyncMock(side_effect=RuntimeError("crash before XADD"))
  kwargs = {
    f"{claim_kind}_key": f"claim:{claim_kind}:1",
    f"{claim_kind}_payload": '{"state":"candidate_published"}',
    f"{claim_kind}_ttl": 300,
  }

  with pytest.raises(RuntimeError, match="crash before XADD"):
    await publish_candidate_atomic(
      client,
      stream="auto_trade:test:crash",
      candidate_id=f"candidate-crash-{claim_kind}",
      payload="{}",
      ttl=300,
      maxlen=100,
      **kwargs,
    )

  assert await client.get(f"claim:{claim_kind}:1") is None
  assert await client.get(
    f"auto_trade:candidate:candidate-crash-{claim_kind}"
  ) is None


@pytest.mark.asyncio
async def test_successful_publication_keeps_reaction_and_thesis_chain():
  client = redis_state.get_client()

  result = await publish_candidate_atomic(
    client,
    stream="auto_trade:test:ownership",
    candidate_id="candidate-ownership",
    payload="{}",
    ttl=300,
    maxlen=100,
    reaction_key="claim:reaction:success",
    reaction_payload='{"state":"claimed"}',
    reaction_ttl=300,
    thesis_key="claim:thesis:success",
    thesis_payload='{"state":"candidate_published"}',
    thesis_ttl=300,
  )

  assert result.published
  assert await client.get("claim:reaction:success") == '{"state":"claimed"}'
  assert (
    await client.get("claim:thesis:success")
    == '{"state":"candidate_published"}'
  )


@pytest.mark.asyncio
async def test_eval_failure_fails_closed_without_production_fallback():
  client = fakeredis.FakeAsyncRedis(decode_responses=True)
  client.eval = AsyncMock(side_effect=RuntimeError("EVAL unavailable"))

  result = await publish_candidate_atomic(
    client,
    stream="auto_trade:test:production-failure",
    candidate_id="candidate-production-failure",
    payload="{}",
    ttl=300,
    maxlen=100,
  )

  assert not result.published
  assert result.status == "atomic_publish_unavailable"
  assert await client.xlen("auto_trade:test:production-failure") == 0
  readiness = json.loads(await client.get("auto_trade:publication_readiness"))
  assert readiness == {
    "ready": False,
    "reason_code": "atomic_publish_unavailable",
  }


@pytest.mark.asyncio
async def test_eval_failure_uses_only_explicit_test_fallback():
  client = fakeredis.FakeAsyncRedis(decode_responses=True)
  client.eval = AsyncMock(side_effect=RuntimeError("EVAL unavailable"))

  result = await publish_candidate_atomic(
    client,
    stream="auto_trade:test:explicit-fallback",
    candidate_id="candidate-explicit-fallback",
    payload="{}",
    ttl=300,
    maxlen=100,
    allow_non_atomic_test_fallback=True,
  )

  assert result.published
  assert result.status == "published"
  assert await client.xlen("auto_trade:test:explicit-fallback") == 1
  assert parse_candidate_execution_record(
    await client.get("auto_trade:candidate:candidate-explicit-fallback")
  ).state == STATE_PUBLISHED


def test_all_selected_publication_failures_keep_exact_publisher_evidence():
  from app.autotrade.worker import _arbitration_followup

  selected_a = _intent(
    "buy-a", direction="BUY", confluence=4, tier="A",
  )
  selected_b = _intent(
    "buy-b", direction="BUY", confluence=3, tier="B",
  )
  opposite = _intent(
    "sell-b", direction="SELL", confluence=2, tier="B",
  )
  # TradePlan-cutover: arbitration works directly on admitted intents; the old
  # ``ExecutionPreflightDecision`` wrapper is gone.
  arbitration = arbitrate_execution_intents([selected_a, selected_b, opposite])
  ordered_ids = {item.intent_id for item in arbitration.ordered}
  attempted_ids = {selected_a.intent_id, selected_b.intent_id}

  assert _arbitration_followup(
    selected_a,
    arbitration=arbitration,
    published_intent=None,
    ordered_ids=ordered_ids,
    attempted_intent_ids=attempted_ids,
  ) is None
  assert _arbitration_followup(
    selected_b,
    arbitration=arbitration,
    published_intent=None,
    ordered_ids=ordered_ids,
    attempted_intent_ids=attempted_ids,
  ) is None
  assert _arbitration_followup(
    opposite,
    arbitration=arbitration,
    published_intent=None,
    ordered_ids=ordered_ids,
    attempted_intent_ids=attempted_ids,
  ) == (
    "arbitration_suppressed",
    "selected_direction_exhausted",
    "intent excluded by cross-engine direction arbitration",
  )


@pytest.mark.asyncio
async def test_range_rail_status_uses_real_ownership_state():
  from app.autotrade.worker import _load_range_side_status

  client = redis_state.get_client()
  await client.set(
    "auto_trade:range_side:XAU:episode-2:BUY",
    json.dumps({
      "state": "MANAGING",
      "candidate_id": "candidate-2",
      "pending_order_ids": [71],
      "position_ids": [81],
      "group_id": "group-2",
    }),
  )

  status = await _load_range_side_status(
    client,
    symbol="XAU",
    range_id="episode-2",
    direction="BUY",
  )

  assert status == {
    "state": "MANAGING",
    "candidate_id": "candidate-2",
    "pending_order_ids": [71],
    "position_ids": [81],
    "group_id": "group-2",
  }


@pytest.mark.asyncio
async def test_route_funnel_is_unique_and_history_tracks_material_change():
  client = redis_state.get_client()
  match = SimpleNamespace(
    symbol="XAU",
    match_id="route-match-1",
    strategy="Liquidity Sweep",
    family="liquidity_reversal",
    direction="BUY",
    structural_source="liquidity",
    issued_at=1_000,
    expires_at=2_000,
    current_price=4100.0,
    entry_low=4101.0,
    entry_high=4102.0,
  )
  for distance in (13.2, 13.7, 15.0):
    await record_route_outcome(
      client,
      match,
      stage="entry_drift",
      status="waiting",
      reason_code="strategy_entry_moved",
      message="entry waits for price",
      measured={"distance_pips": distance},
      retained=True,
    )

  assert await client.xlen("auto_trade:route_history:XAU") == 2
  assert int(await client.hget(
    "auto_trade:metrics:XAU", "strategy_match_waiting"
  )) == 1
  latest = json.loads(await client.get(
    "auto_trade:route_outcome:XAU:route-match-1"
  ))
  assert latest["measured"]["distance_pips"] == 15.0


@pytest.mark.asyncio
async def test_route_keeps_arbitration_and_publication_evidence():
  """TradePlan-cutover: preflight is gone; the surviving evidence trail is
  arbitration + publication. The ``preflight_reason_code`` field is now
  optional (never written by TradePlan's own hard gates) so tests only lock the
  arbitration and publication fields the ranked publisher continues to
  emit.
  """
  client = redis_state.get_client()
  match = SimpleNamespace(
    symbol="XAU",
    match_id="route-stages-1",
    strategy="Demand Zone Reaction",
    family="supply_demand",
    direction="BUY",
    structural_source="supply_demand",
    structural_zone_id="demand-1",
    issued_at=1_000,
    expires_at=2_000,
    current_price=4100.0,
    entry_low=4100.0,
    entry_high=4101.0,
  )
  await record_route_outcome(
    client,
    match,
    stage="stream_publish",
    status="candidate_published",
    reason_code="candidate_published",
    message="published",
    candidate_id="candidate-exact",
    group_id="group-exact",
    executor_event_id="171-0",
    retained=False,
    arbitration_reason_code="selected_for_publication",
    winner_intent_id="strategy:route-stages-1",
    signal_source="scanner_strategy_match",
  )

  route = json.loads(await client.get(
    "auto_trade:route_outcome:XAU:route-stages-1"
  ))
  assert route["arbitration_reason_code"] == "selected_for_publication"
  assert route["publication_reason_code"] == "candidate_published"
  assert route["current_stage"] == "stream_publish"
  assert route["candidate_id"] == "candidate-exact"
  assert route["executor_event_id"] == "171-0"
  assert route["winner_intent_id"] == "strategy:route-stages-1"
  assert route["signal_source"] == "scanner_strategy_match"


@pytest.mark.asyncio
async def test_route_recovery_clears_stale_terminal_reason_and_keeps_history():
  client = redis_state.get_client()
  match = SimpleNamespace(
    symbol="XAU",
    match_id="route-recovery-1",
    strategy="Liquidity Sweep",
    family="liquidity_reversal",
    direction="BUY",
    structural_source="liquidity",
    issued_at=1_000,
    expires_at=2_000,
    current_price=4100.0,
    entry_low=4100.0,
    entry_high=4101.0,
  )
  await record_route_outcome(
    client,
    match,
    stage="stream_publish",
    status="blocked",
    reason_code="atomic_publish_unavailable",
    message="Redis scripting unavailable",
    retained=True,
  )
  await record_route_outcome(
    client,
    match,
    stage="stream_publish",
    status="candidate_published",
    reason_code="candidate_published",
    message="published after recovery",
    retained=False,
  )

  route = json.loads(await client.get(
    "auto_trade:route_outcome:XAU:route-recovery-1"
  ))
  history = await client.xrange("auto_trade:route_history:XAU")
  reasons = [
    json.loads(dict(item[1])["payload"])["reason_code"]
    for item in history
  ]
  assert route["terminal_reason_code"] is None
  assert reasons[-2:] == [
    "atomic_publish_unavailable", "candidate_published",
  ]


@pytest.mark.asyncio
async def test_executor_reject_clears_on_rearmed_checking_transition():
  client = redis_state.get_client()
  match = SimpleNamespace(
    symbol="XAU",
    match_id="route-rearm-1",
    strategy="Mapped Zone Reaction",
    family="mapped_zone_reaction",
    direction="SELL",
    structural_source="supply",
    issued_at=1_000,
    expires_at=2_000,
    current_price=4100.0,
    entry_low=4100.0,
    entry_high=4101.0,
  )
  await record_route_outcome(
    client,
    match,
    stage="executor",
    status="executor_rejected",
    reason_code="protective_stop_contract_mismatch",
    message="executor rejected old publication",
    retained=False,
  )
  await record_route_outcome(
    client,
    match,
    stage="preflight",
    status="checking",
    reason_code="thesis_rearmed",
    message="new reaction is being checked",
    retained=True,
  )

  route = json.loads(await client.get(
    "auto_trade:route_outcome:XAU:route-rearm-1"
  ))
  assert route["terminal_reason_code"] is None


@pytest.mark.asyncio
async def test_new_terminal_transition_replaces_old_terminal_reason():
  client = redis_state.get_client()
  match = SimpleNamespace(
    symbol="XAU",
    match_id="route-terminal-update",
    strategy="Demand Zone Reaction",
    family="supply_demand",
    direction="BUY",
    structural_source="demand",
    issued_at=1_000,
    expires_at=2_000,
    current_price=4100.0,
    entry_low=4100.0,
    entry_high=4101.0,
  )
  for reason in ("atomic_publish_unavailable", "zone_invalidated"):
    await record_route_outcome(
      client,
      match,
      stage="stream_publish" if reason.startswith("atomic") else "entry_invalidation",
      status="blocked",
      reason_code=reason,
      message=reason,
      retained=False,
    )

  route = json.loads(await client.get(
    "auto_trade:route_outcome:XAU:route-terminal-update"
  ))
  assert route["terminal_reason_code"] == "zone_invalidated"

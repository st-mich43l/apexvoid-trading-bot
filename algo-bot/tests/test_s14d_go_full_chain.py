"""S14D: a confirmed Go-origin Kafka event travels, through the real adapter and the
unchanged Auto Algo pipeline, to an executable TradePlan V8, on real PostgreSQL and a
real Redis (production Lua included). Every existing control still gates it.

Chain covered here (the executor half lives in ctrader-engine's GoDerivedPlanChainTests
and the delivery half in test_s14d_executor_events_delivery.py, both consuming the SAME
plan bytes exported below):

  Kafka record -> durable ledger -> reviewed Go policy -> Redis StrategyMatch ->
  Auto Algo preflight -> TradePlan V8 -> execution:trade_plans
"""

from __future__ import annotations

import ast
import json
import os
import re
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from redis.asyncio import Redis

from app.analysis_client.consumer import AnalysisOpportunityConsumer
from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.analysis.structural_reaction_support import structural_thesis_id
from app.autotrade import go_opportunity_policy as pol
from app.autotrade import killzone, worker
from app.autotrade import zone_execution_cutover as cutover
from app.autotrade.go_plan_cancel import request_plan_cancel
from app.autotrade.multi_match import deserialize_matches, strategy_matches_key
from app.autotrade.route_outcome import route_outcome_key
from app.autotrade.setup_lifecycle import CONFIRMED, PLAN_PUBLISHED, load_setup
from app.autotrade.trade_plan import TradePlan
from app.autotrade.zone_watch import (
  GRADE_A,
  WATCHING_RETEST,
  discover_zone_watch,
  load_zone_watch,
  transition_zone_watch,
)
from app.persistence import redis_state
from tests.configuration.canonical_fixtures import install_runtime_overrides
from tests.test_go_opportunity_policy import Harness, golden
from tests.test_s14a_shadow_dry_run import live_inputs
from tests.test_publish_trade_plan_v8 import (  # noqa: F401 - autouse fixtures
  _freeze_technique_killzone_hour,
  _no_news_by_default,
)

pytestmark = pytest.mark.real_redis

STREAM = "execution:trade_plans"
FIXTURE = Path(__file__).resolve().parents[2] / "contracts" / "autotrade" / "go-derived-plan-xau-supply.json"


@pytest.fixture
def prod(monkeypatch, event_loop):
  url = os.getenv("REAL_REDIS_URL")
  if not url:
    pytest.fail("REAL_REDIS_URL is required for the S14D chain tests")
  client = Redis.from_url(url, decode_responses=True)
  event_loop.run_until_complete(client.ping())
  event_loop.run_until_complete(client.flushdb())
  monkeypatch.setattr(redis_state, "_client", client)
  try:
    yield client
  finally:
    event_loop.run_until_complete(client.flushdb())
    event_loop.run_until_complete(client.aclose())


@pytest.fixture
def h(sql, monkeypatch, prod):
  install_runtime_overrides(monkeypatch, {"analysis.technical_authority.consumer_enabled": True})
  live_inputs(monkeypatch)
  return Harness(sql, monkeypatch)


def kafka_record(now: int, opp: str = "opp_chain", *, ago: int = 60, offset: int = 1):
  """A Kafka record exactly as the Go producer would have written it."""
  raw = golden(int(now) + 60 - ago, id=opp)
  raw["event_id"] = f"evt-{opp}"
  return SimpleNamespace(topic=OpportunityTopic, partition=0, offset=offset, timestamp=(int(now) - 5) * 1000, value=json.dumps(raw).encode())


def catalog_kafka_record(now: int, scope: str, *, offset: int = 1):
  """Build a Go-shaped record for one reviewed catalog adapter.

  The payload keeps its strategy-specific evidence and geometry. The test is
  deliberately downstream of the real consumer, so a missing adapter or a
  Python reconstruction cannot make the complete-path matrix pass.
  """
  raw = golden(int(now) - 60, id=f"opp_{scope}")
  payload = raw["payload"]
  direction = "SELL" if scope == "supply" else "BUY"
  payload.update({
    "strategy": scope,
    "direction": direction,
    "timeframe": "M1" if scope in {"range_sweep", "impulse_pullback", "scalp_breakout_retest"} else "M5",
  })
  if direction == "BUY":
    payload.update({
      "entry": {"low": 4352.5, "high": 4356.0},
      "invalidation": {"price": 4350.0},
      "targets": [{"price": {"price": 4362.0}}, {"price": {"price": 4368.0}}],
    })
  payload["technical_context"]["bias"] = {"direction": direction, "layer": "internal"}
  observed = payload["technical_context"]["reference_time"]
  payload["technical_context"]["higher_timeframes"] = [{
    "timeframe": "H1", "direction": direction, "layer": "major", "reference_time": observed - 3900,
  }]
  profile = pol.REVIEWED_SCOPES[scope]
  evidence = profile.evidence_prefixes[0]
  payload["evidence"] = [{"code": evidence + ("confirmed" if evidence.endswith("_") else "")}]
  if scope not in {"supply", "demand"}:
    payload["technical_context"].pop("confirmation", None)
  raw["event_id"] = f"evt-{scope}"
  return SimpleNamespace(
    topic=OpportunityTopic,
    partition=0,
    offset=offset,
    timestamp=int(now) * 1000,
    value=json.dumps(raw).encode(),
  )


def consumer_for(h) -> AnalysisOpportunityConsumer:
  return AnalysisOpportunityConsumer(h.repo, policy=h.policy)


async def cycle(prod, *, n: int = 1):
  for i in range(n):
    await worker._handle_event(f"XAU:M1:{int(time.time()) + 60 * i}", client=prod)


async def plans(prod) -> list[dict]:
  return [json.loads(fields["payload"]) for _id, fields in await prod.xrange(STREAM)]


def _spot(bid: float, ask: float, *, fresh: bool = True) -> worker.AutoTradeSpot:
  return worker.AutoTradeSpot(price=round((bid + ask) / 2, 5), ts=int(time.time()), fresh=fresh, bid=bid, ask=ask)


async def read_trade_plan(prod, plan_id: str) -> TradePlan | None:
  for raw in await plans(prod):
    if raw.get("plan_id") == plan_id:
      return TradePlan.from_dict(raw)
  return None


async def route(prod, match_id: str) -> dict:
  return json.loads(await prod.get(route_outcome_key("XAU", match_id)))


async def go_event_delivered(h, opp: str = "opp_chain", **kw):
  await h.activate()
  await h._ensure()
  record = kafka_record(h.clock.now, opp, **kw)
  await consumer_for(h).process_record(record)
  return record


# ---- the chain ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kafka_event_becomes_a_real_v8_plan_with_full_provenance(h, prod, sql):
  record = await go_event_delivered(h)
  # durable PostgreSQL lifecycle first
  assert await h.repo.opportunity_state("opp_chain") == "active"
  assert [r["outcome"] for r in await sql.fetch("SELECT outcome FROM analysis_shadow_decisions")] == ["match_written"]
  # reviewed Go policy -> Redis StrategyMatch (confirmed setup)
  match = deserialize_matches(await prod.get(strategy_matches_key("XAU")))[0]
  assert (await load_setup(prod, match.match_id)).state == CONFIRMED

  await cycle(prod)                                                   # Auto Algo preflight -> TradePlan V8

  published = await plans(prod)
  assert len(published) == 1
  plan = published[0]
  event = json.loads(record.value)["payload"]
  # identity / provenance
  assert plan["plan_id"] == f"v8:go_{event['id']}" == worker._v8_plan_id(match)
  assert plan["setup_id"] == match.match_id == f"go_{event['id']}"
  zone_id = event["technical_context"]["confirmation"]["zone_id"]
  assert plan["thesis_id"] == match.thesis_id == pol._thesis_id("XAU", "supply_demand", "SELL", zone_id)
  tags = set(plan["analysis"]["tags"])
  assert {"origin:go", "catalog:supply", f"go_opportunity:{event['id']}", "htf_bias_source:go_H1", "go_reaction:rejection"} <= tags
  assert plan["analysis"]["strategy"] == "Supply Demand" and plan["analysis"]["direction"] == "SELL"
  # actual structural zone identity, entry band and invalidation come from Go, not from a Python detector
  assert plan["source_structure"]["structure_id"] == event["technical_context"]["confirmation"]["zone_id"]
  assert plan["source_structure"]["kind"] == "supply"
  assert (float(plan["source_structure"]["low"]), float(plan["source_structure"]["high"])) == (event["entry"]["low"], event["entry"]["high"])
  assert (float(plan["entry"]["zone_low"]), float(plan["entry"]["zone_high"])) == (event["entry"]["low"], event["entry"]["high"])
  assert float(plan["source_structure"]["invalidation_price"]) == event["invalidation"]["price"]
  # confirmation timestamps and expiry: never outlive the technical opportunity
  assert plan["analysis"]["confirmation_bar_ts"] == event["technical_context"]["confirmation"]["confirmation_bar_time"]
  assert plan["expires_at"] <= event["expires_at"] and plan["entry"]["expires_at"] <= event["expires_at"]
  # a normal automatic plan: full Auto Algo contract, never Manual Algo's gate bypass
  assert "bypass_analysis_gates" not in json.dumps(plan) and plan["execution_policy"]["cancel_on_expiry"] is True
  assert plan["sizing"]["mode"] == "equity_table" and plan["risk"]["max_group_risk_percent"] and plan["risk"]["risk_percent"]
  assert plan["stop"]["type"] == "absolute" and plan["targets"]
  TradePlan.from_dict(plan).validate()                                # the executor's own contract validator
  # setup lifecycle, dedup tombstone, executor state and the Go plan index
  assert (await load_setup(prod, match.match_id)).state == PLAN_PUBLISHED
  assert await prod.get(f"execution:plan_state:{plan['plan_id']}") == "published"
  assert await prod.exists(f"execution:plan_dedup:{plan['plan_id']}")
  assert (await route(prod, match.match_id))["status"] == "candidate_published"
  assert json.loads(await prod.hget("analysis:go_plans", plan["plan_id"]))["scope"] == "supply"


@pytest.mark.asyncio
async def test_go_zone_book_hard_blocks_a_go_origin_plan_inside_a_published_opposing_zone(h, prod):
  """The worker's execution-time opposing-barrier recheck (production
  finding 2026-09-28: it was a silent no-op for every Go-origin match) now
  prefers the Go engine's own live-published zone book over a Python OHLC
  recompute. A supply zone Go publishes that contains the Key Level match's
  own entry band must hard-block the plan, exactly as a Python-origin
  match's own opposing-barrier check already would.

  Key Level (not Supply/Demand) is deliberately used here: Supply/Demand is
  a "technique" strategy, and evaluate_structural_target_room's own
  same-wall overlap glue (filter_overlapping_opposing_entries) is designed
  to drop an opposing entry that substantially overlaps a technique
  match's own entry band - by design, since a technique zone's own map
  entry commonly reappears in the opposing pool. That glue does not apply
  to Key Level, so a genuinely separate opposing zone the Go zone book
  publishes reaches the real containment hard-block untouched.
  """
  from app.autotrade.go_zone_book import go_zone_book_key

  await h.activate(scope="key_level")
  await h._ensure()
  record = catalog_kafka_record(h.clock.now, "key_level")
  await consumer_for(h).process_record(record)
  match = deserialize_matches(await prod.get(strategy_matches_key("XAU")))[0]

  await prod.set(go_zone_book_key("XAU"), json.dumps({
    "symbol": "XAU", "generated_at": int(h.clock.now),
    "entries": [{
      "timeframe": "M15", "kind": "supply", "low": 4349.0, "high": 4355.0,
      "atr": 4.0, "strength": 0.9, "touch_count": 2, "state": "fresh",
    }],
  }))

  plan_id = await worker._publish_trade_plan_v8(
    prod, "XAU", _spot(4354.1, 4354.3), match, frames={},
  )
  assert plan_id is None
  assert await plans(prod) == []


@pytest.mark.asyncio
async def test_go_zone_book_unavailable_continues_without_python_fallback(h, prod):
  """No zone book published (Go has not written one, or it expired): the
  plan still publishes using Go's opportunity geometry, without inventing a
  competing Python zone book or silently blocking the opportunity."""
  await h.activate(scope="key_level")
  await h._ensure()
  record = catalog_kafka_record(h.clock.now, "key_level")
  await consumer_for(h).process_record(record)
  match = deserialize_matches(await prod.get(strategy_matches_key("XAU")))[0]

  plan_id = await worker._publish_trade_plan_v8(
    prod, "XAU", _spot(4354.1, 4354.3), match, frames={},
  )
  assert plan_id is not None
  assert len(await plans(prod)) == 1


@pytest.mark.parametrize("scope", sorted(pol.REVIEWED_SCOPES))
@pytest.mark.asyncio
async def test_every_reviewed_go_strategy_reaches_tradeplan_v8(h, prod, scope):
  """The complete Kafka -> adapter -> V8 path is covered for every scope."""
  await h.activate(scope=scope)
  await h._ensure()
  record = catalog_kafka_record(h.clock.now, scope)
  await consumer_for(h).process_record(record)

  match = deserialize_matches(await prod.get(strategy_matches_key("XAU")))[0]
  assert match.structural_source == f"go:{scope}"
  assert "origin:go" in match.tags
  plan_id = await worker._publish_trade_plan_v8(
    prod,
    "XAU",
    _spot(4354.1, 4354.3),
    match,
    frames={},
  )
  assert plan_id is not None, f"{scope} policy rejected: {await route(prod, match.match_id)}"
  plan = await read_trade_plan(prod, plan_id)
  assert plan is not None
  assert plan.setup_id == match.match_id
  assert plan.provenance.confirmation_source == "go_analysis_engine"
  assert plan.analysis.tags and "origin:go" in plan.analysis.tags
  TradePlan.from_dict(plan.to_dict()).validate()


@pytest.mark.asyncio
async def test_redelivery_and_repeated_cycles_never_duplicate_the_plan(h, prod):
  record = await go_event_delivered(h)
  await cycle(prod, n=3)
  await consumer_for(h).process_record(record)                        # Kafka redelivers the same record
  await cycle(prod, n=3)
  assert len(await plans(prod)) == 1
  assert (await prod.xlen(STREAM)) == 1


@pytest.mark.asyncio
async def test_a_second_confirmation_of_the_same_zone_is_not_a_second_order(h, prod):
  """Go re-confirms a zone on consecutive bars under distinct opportunity ids."""
  await h.activate()
  await h._ensure()
  consumer = consumer_for(h)
  await consumer.process_record(kafka_record(h.clock.now, "opp_first", ago=65, offset=1))
  await consumer.process_record(kafka_record(h.clock.now, "opp_second", ago=60, offset=2))
  assert len(deserialize_matches(await prod.get(strategy_matches_key("XAU")))) == 2
  await cycle(prod, n=4)
  published = await plans(prod)
  assert len(published) == 1                                          # exactly one executable plan for the zone
  loser = "go_opp_first" if published[0]["setup_id"] == "go_opp_second" else "go_opp_second"
  assert (await load_setup(prod, loser)).state != PLAN_PUBLISHED        # same thesis: never a second executable plan
  assert published[0]["thesis_id"] == deserialize_matches(await prod.get(strategy_matches_key("XAU")))[0].thesis_id


@pytest.mark.asyncio
async def test_a_second_go_event_after_publication_does_not_add_a_plan_while_the_first_is_live(h, prod):
  await go_event_delivered(h, "opp_early", ago=120)
  await cycle(prod)
  assert len(await plans(prod)) == 1
  await consumer_for(h).process_record(kafka_record(h.clock.now, "opp_later", ago=30, offset=2))
  await cycle(prod, n=3)
  assert len(await plans(prod)) == 1


# ---- every Auto Algo control still applies to a Go-origin match ------------------------------------------

def _stale_spot(mp):
  mp.setattr(worker, "_load_spot", AsyncMock(return_value=worker.AutoTradeSpot(
    price=4354.2, ts=int(time.time()) - 600, fresh=False, bid=4354.1, ask=4354.3)))


_REAL_PUBLISH_WINDOW = killzone.evaluate_reaction_publish_window     # captured before the suite's autouse hour freeze


def _outside_publish_window(mp):
  mp.setattr(killzone, "evaluate_reaction_publish_window",
             lambda *, ts=None, hour=None, cfg=None, require=True: _REAL_PUBLISH_WINDOW(ts=None, hour=3, cfg=cfg, require=require))


def _below_min_confluence(mp):
  install_runtime_overrides(mp, {"actionability.gates.min_confluence": 99})


def _auto_trade_disabled(mp):
  install_runtime_overrides(mp, legacy_overrides={"auto_trade_enabled": False})


def _price_outside_entry_contract(mp):
  mp.setattr(worker, "_load_spot", AsyncMock(return_value=worker.AutoTradeSpot(
    price=4339.1, ts=int(time.time()), fresh=True, bid=4339.0, ask=4339.2)))


CONTROLS = [
  ("stale_spot", _stale_spot, "waiting", "stale_spot"),
  ("publish_window", _outside_publish_window, "waiting", "outside_reaction_publish_window"),
  ("min_confluence", _below_min_confluence, "blocked", "confluence_below_minimum"),
  ("auto_trade_off", _auto_trade_disabled, "blocked", "auto_trade_disabled"),
  ("entry_contract", _price_outside_entry_contract, "waiting", "waiting_retest_entry_zone"),
]


@pytest.mark.parametrize("name,setup,status,reason", CONTROLS, ids=[c[0] for c in CONTROLS])
@pytest.mark.asyncio
async def test_existing_auto_algo_controls_still_gate_a_go_origin_match(h, prod, monkeypatch, name, setup, status, reason):
  setup(monkeypatch)
  await go_event_delivered(h)
  await cycle(prod, n=2)
  assert await prod.xlen(STREAM) == 0, "a control that should have gated the Go match let a plan through"
  outcome = await route(prod, "go_opp_chain")
  assert (outcome["status"], outcome["reason_code"]) == (status, reason)


@pytest.mark.asyncio
async def test_a_withdrawn_or_unowned_go_match_never_publishes(h, prod):
  await go_event_delivered(h)
  await request_plan_cancel(prod, "v8:go_opp_chain", reason="zone_invalidated", source="opportunity_invalidated", requested_at=int(h.clock.now))
  await cycle(prod, n=2)
  assert await prod.xlen(STREAM) == 0 and (await route(prod, "go_opp_chain"))["reason_code"] == "go_plan_withdrawn"


@pytest.mark.asyncio
async def test_no_scope_grant_is_needed_between_match_and_plan(h, prod):
  await go_event_delivered(h)
  await cycle(prod, n=2)
  assert len(await plans(prod)) == 1
  assert (await route(prod, "go_opp_chain"))["status"] == "candidate_published"


# ---- Go-only automatic path: a leftover Python scanner match never trades ----------------------------

def _stale_python_match():
  """A Python-scanner-shaped leftover sitting in Redis at cutover: the same executable
  geometry as the golden Go match, none of the Go provenance tags, and the scanner's own
  structural identity with no Go provenance; the worker must reject it even if
  its geometry looks like a valid Go match."""
  now = int(time.time())
  event = parse_analysis_event(OpportunityTopic, json.dumps(golden(now)))
  go = pol.build_strategy_match(event, profile=pol.REVIEWED_SCOPES["supply"], now=now)
  legacy = replace(
    go,
    tags=tuple(t for t in go.tags if t.startswith(("kind:", "bias:"))),
    structural_source="scanner:supply_demand",
  )
  return replace(legacy, match_id=structural_thesis_id(
    symbol=legacy.symbol, strategy=legacy.strategy, direction=legacy.direction,
    structural_source=legacy.structural_source, structural_id=legacy.structural_zone_id,
    touch_bar_ts=str(legacy.touch_bar_ts), confirmation_bar_ts=str(legacy.confirmation_bar_ts),
  ))


async def _plant(prod, match):
  await pol.GoOpportunityPolicy._advance_setup(prod, match)
  await pol.GoOpportunityPolicy._store_match(prod, match, int(time.time()))


@pytest.mark.asyncio
async def test_stale_python_match_cannot_produce_a_plan_in_go_mode(h, prod, monkeypatch):
  await h._ensure()
  stale = _stale_python_match()
  await _plant(prod, stale)
  assert [m.match_id for m in deserialize_matches(await prod.get(strategy_matches_key("XAU")))] == [stale.match_id]
  await cycle(prod, n=3)
  assert await prod.xlen(STREAM) == 0
  assert await prod.get(route_outcome_key("XAU", stale.match_id)) is None   # never even preflighted
  assert (await load_setup(prod, stale.match_id)).state == CONFIRMED         # left alone, not traded


@pytest.mark.asyncio
async def test_go_match_still_publishes_next_to_a_stale_python_match_in_go_mode(h, prod, monkeypatch):
  await go_event_delivered(h)
  stale = _stale_python_match()
  await _plant(prod, stale)
  await cycle(prod, n=2)
  published = await plans(prod)
  assert [plan["setup_id"] for plan in published] == ["go_opp_chain"]
  assert "origin:go" in published[0]["analysis"]["tags"]
  assert await prod.get(route_outcome_key("XAU", stale.match_id)) is None


@pytest.mark.asyncio
async def test_retained_python_zone_watch_cannot_activate_in_go_mode(h, prod, monkeypatch):
  """Historical ZoneWatch state cannot bypass the Go provenance boundary."""
  await h._ensure()
  # What install_zone_execution_cutover() binds at startup, without leaking the install.
  monkeypatch.setattr(cutover, "_ORIGINAL_DIRECT_PUBLISH", worker.try_publish_executable_signal)
  monkeypatch.setattr(cutover, "_ensure_published_root_card", AsyncMock())
  retained = _stale_python_match()
  now = int(time.time())
  record, _created = await discover_zone_watch(
    prod, zone_id="zone-retained", symbol="XAU", direction=retained.direction,
    low=retained.entry_low, high=retained.entry_high, source_timeframe="M5",
    structural_sources=("supply_demand",), confluence_tags=(), grade=GRADE_A, score=3.0,
    structure_signature="zone-retained", confirmed_at=now,
  )
  # Discovery leaves a retained zone watching for its retest, as the cutover does.
  record, _ = await transition_zone_watch(prod, "zone-retained", WATCHING_RETEST, reason_code="zone_discovered")
  await prod.set("price:XAU:spot", json.dumps({"bid": 4354.1, "ask": 4354.3, "ts": now}))

  activated = await cutover._activate_match(prod, record, retained, event_ts=str(now))

  assert activated is None
  assert await plans(prod) == []
  assert (await load_zone_watch(prod, "zone-retained")).state == WATCHING_RETEST


# ---- static guarantees --------------------------------------------------------------------------------------

GO_PATH_FILES = ("go_opportunity_policy.py", "go_shadow_policy.py", "go_plan_cancel.py")
FORBIDDEN = re.compile(r"bypass_analysis_gates|manual_algo|manual_execution")


def _code_tokens(path: Path) -> set[str]:
  """Every identifier, attribute, keyword and string literal that is *code* (docstrings excluded)."""
  tree = ast.parse(path.read_text())
  docstrings = set()
  for node in ast.walk(tree):
    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
      body = getattr(node, "body", [])
      if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
        docstrings.add(id(body[0].value))
  tokens: set[str] = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Name):
      tokens.add(node.id)
    elif isinstance(node, ast.Attribute):
      tokens.add(node.attr)
    elif isinstance(node, ast.keyword) and node.arg:
      tokens.add(node.arg)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
      tokens.update(a.name for a in node.names)
      if isinstance(node, ast.ImportFrom) and node.module:
        tokens.add(node.module)
    elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
      tokens.add(node.value)
  return tokens


def test_the_go_path_never_touches_manual_algos_analysis_gate_bypass():
  root = Path(worker.__file__).parent
  for name in GO_PATH_FILES:
    offending = {t for t in _code_tokens(root / name) if FORBIDDEN.search(t)}
    assert not offending, f"{name} references Manual Algo machinery: {offending}"
  # the automatic publish path itself never reads the bypass either
  offending = {t for t in _code_tokens(root / "trade_plan_stream.py") if FORBIDDEN.search(t)}
  assert not offending


# ---- the plan bytes the executor and delivery suites consume ---------------------------------------------------

_VOLATILE = {"created_at": 1_790_000_000, "expires_at": 2_000_000_000}


def normalized(plan: dict) -> dict:
  """Drop the wall-clock-dependent fields so the same plan hashes the same on every run."""
  out = json.loads(json.dumps(plan))
  out.update(_VOLATILE)
  out["entry"]["expires_at"] = _VOLATILE["expires_at"]
  out["provenance"]["confirmation_bar_ts"] = 1_790_000_000
  out["provenance"]["zone_episode_id"] = "<episode>"
  for key in ("formation_bar_ts", "confirmation_bar_ts"):
    out["analysis"][key] = 1_790_000_000
  return out


@pytest.mark.asyncio
async def test_the_published_plan_is_the_shared_fixture_the_executor_consumes(h, prod):
  await go_event_delivered(h)
  await cycle(prod)
  (plan,) = await plans(prod)
  fresh = normalized(plan)
  if os.getenv("UPDATE_GOLDEN") == "1":
    FIXTURE.write_text(json.dumps(fresh, indent=2, sort_keys=True) + "\n")
  committed = json.loads(FIXTURE.read_text())
  assert fresh == committed, "the Go-derived plan drifted: regenerate with UPDATE_GOLDEN=1, review, and update the C# expectations"


def test_go_thesis_identity_is_the_legacy_stable_identity_of_the_zone_not_of_the_bar():
  """Same rule and same bytes as the scanner's TradePlan thesis: a new confirmation
  timestamp alone must never create a new thesis."""
  from app.analysis.structural_reaction_support import thesis_id as legacy_thesis_id
  assert pol._thesis_id("XAU", "supply_demand", "SELL", "zone:M5:supply:1789387500") == legacy_thesis_id(
    symbol="XAU", strategy_family="supply_demand", direction="SELL", structural_id="zone:M5:supply:1789387500")
  assert pol._thesis_id("XAU", "supply_demand", "SELL", "zone-a") != pol._thesis_id("XAU", "supply_demand", "SELL", "zone-b")
  assert pol._thesis_id("XAU", "supply_demand", "SELL", "zone-a") != pol._thesis_id("XAU", "supply_demand", "BUY", "zone-a")

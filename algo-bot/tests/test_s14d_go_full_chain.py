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
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from redis.asyncio import Redis

from app.analysis_client.consumer import AnalysisOpportunityConsumer
from app.analysis_client.models import OpportunityTopic
from app.autotrade import go_opportunity_policy as pol
from app.autotrade import killzone, worker
from app.autotrade.go_plan_cancel import request_plan_cancel
from app.autotrade.multi_match import deserialize_matches, strategy_matches_key
from app.autotrade.route_outcome import route_outcome_key
from app.autotrade.setup_lifecycle import CONFIRMED, PLAN_PUBLISHED, load_setup
from app.autotrade.trade_plan import TradePlan
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


def consumer_for(h) -> AnalysisOpportunityConsumer:
  return AnalysisOpportunityConsumer(h.repo, shadow=None, mode="go", policy=h.policy)


async def cycle(prod, *, n: int = 1):
  for i in range(n):
    await worker._handle_event(f"XAU:M1:{int(time.time()) + 60 * i}", client=prod)


async def plans(prod) -> list[dict]:
  return [json.loads(fields["payload"]) for _id, fields in await prod.xrange(STREAM)]


async def route(prod, match_id: str) -> dict:
  return json.loads(await prod.get(route_outcome_key("XAU", match_id)))


async def granted_and_delivered(h, opp: str = "opp_chain", **kw):
  await h.grant()
  await h._ensure()
  record = kafka_record(h.clock.now, opp, **kw)
  await consumer_for(h).process_record(record)
  return record


# ---- the chain ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kafka_event_becomes_a_real_v8_plan_with_full_provenance(h, prod, sql):
  record = await granted_and_delivered(h)
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
  assert {"authority:go", "catalog:supply", "authority_epoch:1", f"go_opportunity:{event['id']}", "htf_bias_source:go_H1", "go_reaction:rejection"} <= tags
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
  assert json.loads(await prod.hget("analysis:go_plans", plan["plan_id"]))["epoch"] == 1


@pytest.mark.asyncio
async def test_redelivery_and_repeated_cycles_never_duplicate_the_plan(h, prod):
  record = await granted_and_delivered(h)
  await cycle(prod, n=3)
  await consumer_for(h).process_record(record)                        # Kafka redelivers the same record
  await cycle(prod, n=3)
  assert len(await plans(prod)) == 1
  assert (await prod.xlen(STREAM)) == 1


@pytest.mark.asyncio
async def test_a_second_confirmation_of_the_same_zone_is_not_a_second_order(h, prod):
  """Go re-confirms a zone on consecutive bars under distinct opportunity ids."""
  await h.grant()
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
  await granted_and_delivered(h, "opp_early", ago=120)
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
  await granted_and_delivered(h)
  await cycle(prod, n=2)
  assert await prod.xlen(STREAM) == 0, "a control that should have gated the Go match let a plan through"
  outcome = await route(prod, "go_opp_chain")
  assert (outcome["status"], outcome["reason_code"]) == (status, reason)


@pytest.mark.asyncio
async def test_a_withdrawn_or_unowned_go_match_never_publishes(h, prod):
  await granted_and_delivered(h)
  await request_plan_cancel(prod, "v8:go_opp_chain", reason="zone_invalidated", source="opportunity_invalidated", requested_at=int(h.clock.now))
  await cycle(prod, n=2)
  assert await prod.xlen(STREAM) == 0 and (await route(prod, "go_opp_chain"))["reason_code"] == "go_plan_withdrawn"


@pytest.mark.asyncio
async def test_rollback_between_match_and_plan_fences_the_publication(h, prod):
  await granted_and_delivered(h)
  await h.fence.rollback("XAU", "supply", expected_epoch=1, actor="oncall", reason="drill", drain_seconds=30)
  await cycle(prod, n=2)
  assert await prod.xlen(STREAM) == 0 and (await route(prod, "go_opp_chain"))["reason_code"] == "authority_fenced"


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
  await granted_and_delivered(h)
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

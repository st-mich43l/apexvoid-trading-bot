"""S14A: the real policy dry run for Go opportunities, with zero side effects.

Real PostgreSQL and a real Redis (``REAL_REDIS_URL``). The dry run executes the
actual ``worker._handle_event`` cycle against an in-memory overlay; these tests
prove what it decides *and* that production state is untouched.
"""

from __future__ import annotations

import json
import os
import time
from unittest.mock import AsyncMock

import pytest
from redis.asyncio import Redis

from app.analysis_client import authority as auth
from app.analysis_client.consumer import AnalysisOpportunityConsumer
from app.analysis_client.models import InvalidationTopic, OpportunityTopic, parse_analysis_event
from app.analysis_client.shadow import AnalysisShadowEvaluator
from app.analysis_client.shadow_overlay import READ_COMMANDS, OverlayRedis, dry_run_context
from app.autotrade import go_shadow_policy as shadow_mod
from app.autotrade import worker
from app.autotrade.gate import AutoScalpDecision
from app.autotrade.go_shadow_policy import GoShadowPolicy, plan_digest
from app.autotrade.multi_match import deserialize_matches, strategy_matches_key
from app.autotrade.route_outcome import route_outcome_key
from app.autotrade.trend import RegimeInfo, TrendDecision
from app.persistence import redis_state, store
from tests.configuration.canonical_fixtures import install_runtime_overrides
from tests.test_analysis_client_models import _invalidated
from tests.test_go_opportunity_policy import Harness, event, golden
from tests.test_publish_trade_plan_v8 import (  # noqa: F401 - autouse fixtures
  _freeze_technique_killzone_hour,
  _m1_trigger_bar,
  _no_news_by_default,
)

pytestmark = pytest.mark.real_redis


class RecordingRedis:
  """Wraps the 'production' client and logs every command that reaches it."""

  def __init__(self, inner):
    self.inner = inner
    self.commands: list[str] = []

  def __getattr__(self, name):
    attr = getattr(self.inner, name)
    if not callable(attr):
      return attr
    if name == "scan_iter":
      def scan(*a, **k):
        self.commands.append(name)
        return attr(*a, **k)
      return scan

    async def call(*a, **k):
      self.commands.append(name)
      return await attr(*a, **k)
    return call


@pytest.fixture
def prod(monkeypatch, event_loop):
  """The production Redis: a real one, wrapped so every command is logged."""
  url = os.getenv("REAL_REDIS_URL")
  if not url:
    pytest.fail("REAL_REDIS_URL is required for the S14A dry-run tests")
  client = Redis.from_url(url, decode_responses=True)
  event_loop.run_until_complete(client.ping())
  event_loop.run_until_complete(client.flushdb())
  recording = RecordingRedis(client)
  monkeypatch.setattr(redis_state, "_client", recording)
  try:
    yield recording
  finally:
    event_loop.run_until_complete(client.flushdb())
    event_loop.run_until_complete(client.aclose())


def live_inputs(monkeypatch, *, bid=4354.1, ask=4354.3, news=None):
  """The market inputs the live worker cycle would read, pinned like the repo's own worker tests."""
  install_runtime_overrides(
    monkeypatch, {"strategies.matching.multiple_matches_enabled": True},
    legacy_overrides={
      "auto_trade_enabled": True, "auto_trade_symbols": "XAU",
      "auto_trade_strategy_match_enabled": True, "auto_trade_news_guard_minutes": 0,
    },
  )
  frames = {"M1": _m1_trigger_bar()}
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=news))
  monkeypatch.setattr(worker, "_load_frames", AsyncMock(return_value=frames))
  monkeypatch.setattr(worker, "_load_spot", AsyncMock(return_value=worker.AutoTradeSpot(
    price=(bid + ask) / 2, ts=int(time.time()), fresh=True, bid=bid, ask=ask)))
  monkeypatch.setattr(worker, "evaluate_auto_scalp_gate", lambda *a, **k: AutoScalpDecision("waiting_for_box"))
  monkeypatch.setattr(worker, "_resolve_worker_range", AsyncMock(return_value=(AutoScalpDecision("waiting_for_box"), None, {})))
  monkeypatch.setattr(worker, "classify_regime", lambda *a, **k: RegimeInfo("trend", "down", 3, 1.0, True, None, ("shadow test",)))
  monkeypatch.setattr(worker, "evaluate_trend_gate", lambda *a, **k: TrendDecision("no_setup"))
  monkeypatch.setattr(worker, "_htf_zones", lambda *a, **k: [])
  monkeypatch.setattr(worker, "_htf_levels", lambda *a, **k: [])


@pytest.fixture
def h(sql, monkeypatch, prod):
  install_runtime_overrides(monkeypatch, {"analysis.technical_authority.consumer_enabled": True})
  live_inputs(monkeypatch)
  harness = Harness(sql, monkeypatch)
  harness.shadow = GoShadowPolicy(
    harness.repo, fence=harness.fence, clock=harness.clock, multiple_matches_enabled=lambda: True,
  )
  return harness


async def dump(client) -> dict:
  """Every key, its type, value and whether it has a TTL."""
  inner = getattr(client, "inner", client)
  out = {}
  async for key in inner.scan_iter("*"):
    kind = await inner.type(key)
    getter = {
      "string": lambda k=key: inner.get(k), "hash": lambda k=key: inner.hgetall(k),
      "set": lambda k=key: inner.smembers(k), "zset": lambda k=key: inner.zrange(k, 0, -1, withscores=True),
      "list": lambda k=key: inner.lrange(k, 0, -1), "stream": lambda k=key: inner.xrange(k),
    }[kind]
    out[key] = (kind, await getter(), await inner.ttl(key) >= 0)
  return out


async def table_counts(sql) -> dict[str, int]:
  tables = [r["tablename"] for r in await sql.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
  return {t: await sql.val(f"SELECT count(*) FROM {t}") for t in sorted(tables)}


async def ledger(h, ev):
  """What the consumer does just before the shadow: apply the event durably."""
  await h._ensure()
  h.offset += 1
  return await h.repo.apply(ev, topic=OpportunityTopic, partition=0, offset=h.offset)


async def decisions(h):
  await h._ensure()
  rows = await h.sql.fetch("SELECT outcome, reason, mode, details FROM analysis_shadow_decisions ORDER BY decision_id")
  return [{"outcome": r["outcome"], "reason": r["reason"], "mode": r["mode"],
           "details": json.loads(r["details"]) if isinstance(r["details"], str) else r["details"]} for r in rows]


# ---- what it decides ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_python_owned_scope_yields_would_publish_with_the_full_plan_and_no_side_effect(h, prod, sql):
  ev = event(int(h.clock.now))
  await ledger(h, ev)
  redis_before, tables_before = await dump(prod), await table_counts(sql)
  prod.commands.clear()

  decision = await h.shadow.dry_run_creation(ev)

  assert (decision.outcome, decision.reason) == ("would_publish", "candidate_published")
  row = (await decisions(h))[-1]
  d = row["details"]
  assert row["mode"] == "go_shadow" and d["dry_run"] is True and d["scope"] == "supply"
  assert d["fence_actual"]["owner"] == "python" and d["fence_actual"]["go_allowed"] is False   # not Go-owned, and irrelevant to the dry run
  assert d["plan_id"] == "v8:go_opp_golden_supply_xau" and d["plan"]["analysis"]["direction"] == "SELL"
  assert d["plan_digest"] == plan_digest(d["plan"]) and len(d["plan_digest"]) == 64
  assert d["route"]["status"] == "candidate_published"
  assert d["overlay"]["written_keys"] > 0 and d["overlay"]["writes"]
  assert set(d["overlay"]["real_reads"]) <= READ_COMMANDS
  # Zero side effects: production Redis identical, only reads reached it, no stream entry,
  # and PostgreSQL gained exactly the one decision row.
  assert await dump(prod) == redis_before
  assert prod.commands and set(prod.commands) <= READ_COMMANDS
  assert await prod.inner.xlen("execution:trade_plans") == 0
  tables_after = await table_counts(sql)
  assert {t: tables_after[t] - tables_before[t] for t in tables_after if tables_after[t] != tables_before[t]} == {"analysis_shadow_decisions": 1}


@pytest.mark.asyncio
async def test_dry_run_plan_is_identical_to_the_plan_the_live_pipeline_publishes(h, prod):
  """Same inputs through the shadow and through the live (granted) path."""
  ev = event(int(h.clock.now))
  await ledger(h, ev)
  await h.shadow.dry_run_creation(ev)
  shadow_details = (await decisions(h))[-1]["details"]

  await prod.inner.flushdb()
  await h.grant()
  ev_live = event(int(h.clock.now))
  assert await h.deliver(ev_live) == "match_written"
  await worker._handle_event(f"XAU:M1:{int(time.time())}", client=prod.inner)
  raw = await prod.inner.get("execution:plan:v8:go_opp_golden_supply_xau")
  assert raw is not None, "live path did not publish"
  live_plan = json.loads(raw)
  assert plan_digest(live_plan) == shadow_details["plan_digest"]
  for field in ("entry", "stop", "targets", "risk", "sizing", "management", "execution_policy", "analysis", "source_structure"):
    if field == "analysis":                      # reasons/tags carry the authority epoch: 0 in shadow, 1 live
      assert live_plan[field]["direction"] == shadow_details["plan"][field]["direction"]
      continue
    assert live_plan[field] == shadow_details["plan"][field], field


@pytest.mark.asyncio
async def test_a_price_outside_the_entry_contract_would_wait_and_publishes_nothing(h, prod, monkeypatch):
  live_inputs(monkeypatch, bid=4339.0, ask=4339.2)
  ev = event(int(h.clock.now))
  await ledger(h, ev)
  before = await dump(prod)
  decision = await h.shadow.dry_run_creation(ev)
  assert (decision.outcome, decision.reason) == ("would_wait", "waiting_retest_entry_zone")
  d = (await decisions(h))[-1]["details"]
  assert d["route"]["status"] == "waiting" and d["route"]["measured"]["phase"] == "waiting_retest" and "plan" not in d
  assert await dump(prod) == before


@pytest.mark.asyncio
async def test_a_policy_block_is_a_would_reject_with_the_gates_own_reason(h, prod, monkeypatch):
  install_runtime_overrides(monkeypatch, {"actionability.gates.min_confluence": 99})
  ev = event(int(h.clock.now))
  await ledger(h, ev)
  before = await dump(prod)
  decision = await h.shadow.dry_run_creation(ev)
  assert (decision.outcome, decision.reason) == ("would_reject", "confluence_below_minimum")
  d = (await decisions(h))[-1]["details"]
  assert d["route"]["status"] == "blocked" and "plan" not in d
  assert await dump(prod) == before


@pytest.mark.asyncio
async def test_pre_policy_stops_are_recorded_as_not_adapted_with_their_reason(h, prod):
  now = int(h.clock.now)
  raw = golden(now)
  raw["payload"]["strategy"] = "order_block"
  unreviewed = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  await ledger(h, unreviewed)
  assert (await h.shadow.dry_run_creation(unreviewed)).reason == "scope_not_reviewed"

  stale = parse_analysis_event(OpportunityTopic, json.dumps({**golden(now - 5_000, id="opp-stale"), "event_id": "evt-stale"}))
  await ledger(h, stale)
  assert (await h.shadow.dry_run_creation(stale)).reason == "event_too_old"

  h.shadow = GoShadowPolicy(h.repo, fence=h.fence, clock=h.clock, multiple_matches_enabled=lambda: False)
  fresh = event(now)
  await ledger(h, fresh)
  assert (await h.shadow.dry_run_creation(fresh)).reason == "multiple_matches_disabled"
  assert all(r["outcome"] == "not_adapted" for r in await decisions(h))
  assert await prod.inner.keys("*") == []                    # nothing at all was written


@pytest.mark.asyncio
async def test_missing_policy_facts_are_rejected_not_guessed(h, prod):
  raw = golden(int(h.clock.now))
  del raw["payload"]["technical_context"]["confirmation"]
  ev = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  await ledger(h, ev)
  decision = await h.shadow.dry_run_creation(ev)
  assert (decision.outcome, decision.reason) == ("rejected", "reaction_confirmation_unavailable")


@pytest.mark.asyncio
async def test_a_terminated_opportunity_is_not_evaluated(h, prod):
  ev = event(int(h.clock.now))
  await ledger(h, ev)
  term = parse_analysis_event(InvalidationTopic, json.dumps(_invalidated(payload={
    "opportunity_id": "opp_golden_supply_xau", "symbol": "XAU", "strategy": "supply",
    "reason_code": "ZONE_INVALIDATED", "invalidated_at": int(h.clock.now)})))
  await h.repo.apply(term, topic=InvalidationTopic, partition=0, offset=99)
  assert (await h.shadow.dry_run_creation(ev)).reason == "ignored_not_active"


@pytest.mark.asyncio
async def test_a_failing_dry_run_is_recorded_and_still_leaves_production_untouched(h, prod, monkeypatch):
  ev = event(int(h.clock.now))
  await ledger(h, ev)
  before = await dump(prod)

  async def boom(*_a, **_k):
    raise RuntimeError("policy bug")
  monkeypatch.setattr(worker, "_handle_event", boom)
  decision = await h.shadow.dry_run_creation(ev)
  assert (decision.outcome, decision.reason) == ("dry_run_error", "RuntimeError")
  assert "policy bug" in (await decisions(h))[-1]["details"]["error"]
  assert await dump(prod) == before


@pytest.mark.asyncio
async def test_repeating_the_dry_run_records_one_row_per_outcome(h, prod):
  ev = event(int(h.clock.now))
  await ledger(h, ev)
  await h.shadow.dry_run_creation(ev)
  await h.shadow.dry_run_creation(ev)
  assert [r["outcome"] for r in await decisions(h)] == ["would_publish"]


# ---- containment: the guards that make "zero side effects" true ------------------------

@pytest.mark.asyncio
async def test_the_fence_bypass_exists_only_for_the_overlay(h, prod):
  """A Go match on a Python-owned scope: fenced for a real client, allowed for the overlay."""
  ev = event(int(h.clock.now))
  await ledger(h, ev)
  await h.grant()
  await h.deliver(event(int(h.clock.now)))                  # writes the match on the real store
  match = deserialize_matches(await prod.inner.get(strategy_matches_key("XAU")))[0]
  await h.fence.rollback("XAU", "supply", expected_epoch=1, actor="oncall", reason="back", drain_seconds=30)
  spot = worker.AutoTradeSpot(price=4354.2, ts=int(time.time()), fresh=True, bid=4354.1, ask=4354.3)
  assert await worker._publish_trade_plan_v8(prod.inner, "XAU", spot, match, frames={"M1": _m1_trigger_bar()}) is None
  route = json.loads(await prod.inner.get(route_outcome_key("XAU", match.match_id)))
  assert route["reason_code"] == "authority_fenced"
  overlay = OverlayRedis(prod.inner)
  with dry_run_context(overlay):
    assert await worker._publish_trade_plan_v8(overlay, "XAU", spot, match, frames={"M1": _m1_trigger_bar()}) is not None


@pytest.mark.asyncio
async def test_no_telegram_card_is_ever_created_by_a_dry_run(h, prod, monkeypatch):
  from app.autotrade import setup_card
  spy = AsyncMock(side_effect=AssertionError("Telegram root card requested by a dry run"))
  monkeypatch.setattr(setup_card, "ensure_plan_published_root_card", spy)
  ev = event(int(h.clock.now))
  await ledger(h, ev)
  assert (await h.shadow.dry_run_creation(ev)).outcome == "would_publish"
  spy.assert_not_called()


@pytest.mark.asyncio
async def test_postgres_is_read_only_inside_a_dry_run(h, prod):
  await h._ensure()
  overlay = OverlayRedis(prod.inner)
  with dry_run_context(overlay):
    async with store._connect() as db:
      assert await db.fetchval("SELECT 1") == 1
      for statement in ("INSERT INTO analysis_shadow_decisions (opportunity_id) VALUES ('x')", "DELETE FROM analysis_opportunities"):
        with pytest.raises(store.DryRunWriteError):
          await db.execute(statement)
      with pytest.raises(store.DryRunWriteError):
        await db.fetch("INSERT INTO analysis_authority_acceptance VALUES ('a','b','c','d',1,2) RETURNING 1")
      with pytest.raises(store.DryRunWriteError):
        db.transaction()
  async with store._connect() as db:                        # outside: normal connection again
    assert await db.execute("SELECT 1") is not None


@pytest.mark.asyncio
async def test_the_shared_client_is_the_overlay_only_while_a_dry_run_runs(h, prod):
  overlay = OverlayRedis(prod.inner)
  assert redis_state.get_client() is prod
  with dry_run_context(overlay):
    assert redis_state.get_client() is overlay
  assert redis_state.get_client() is prod


# ---- consumer wiring ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_go_shadow_consumer_records_the_real_outcome_instead_of_contract_gap(h, prod):
  from types import SimpleNamespace
  evaluator = AnalysisShadowEvaluator(h.repo, dry_run=h.shadow.dry_run_creation)
  consumer = AnalysisOpportunityConsumer(h.repo, shadow=evaluator, mode="go_shadow")
  await h._ensure()
  now = int(h.clock.now)
  record = SimpleNamespace(topic=OpportunityTopic, partition=0, offset=1, timestamp=(now - 10) * 1000,
                           value=json.dumps(golden(now)).encode())
  await consumer.process_record(record)
  rows = await decisions(h)
  assert [r["outcome"] for r in rows] == ["would_publish"] and "contract_gap" not in [r["outcome"] for r in rows]
  assert rows[0]["details"]["published_at"] == now - 10       # Kafka publish time reached the gate
  assert await prod.inner.xlen("execution:trade_plans") == 0


@pytest.mark.asyncio
async def test_without_a_wired_policy_the_evaluator_says_so_honestly(h):
  ev = event(int(h.clock.now))
  await ledger(h, ev)
  decision = await AnalysisShadowEvaluator(h.repo).evaluate_creation(ev)
  assert (decision.outcome, decision.reason) == ("shadow_unavailable", "dry_run_policy_not_configured")

"""S14F acceptance tooling: gates are computed from recorded evidence and absence is never a pass.

Real PostgreSQL + real Redis. The clean-window evidence is produced by the real S14A shadow dry run;
violations are injected only to prove each gate can fail.
"""

from __future__ import annotations

import ast
import json
import os
import time
from argparse import Namespace
from pathlib import Path

import pytest
from redis.asyncio import Redis

from app.analysis_client import acceptance as acc
from app.analysis_client.models import InvalidationTopic, OpportunityTopic, parse_analysis_event
from app.persistence import redis_state, store
from app.scripts import shadow_acceptance as cli
from tests.test_analysis_client_models import _invalidated
from tests.test_go_opportunity_policy import Harness, event, golden
from tests.test_s14a_shadow_dry_run import live_inputs
from tests.configuration.canonical_fixtures import install_runtime_overrides
from tests.test_publish_trade_plan_v8 import (  # noqa: F401 - autouse fixtures
  _freeze_technique_killzone_hour,
  _no_news_by_default,
)

pytestmark = pytest.mark.real_redis


@pytest.fixture
def prod(monkeypatch, event_loop):
  url = os.getenv("REAL_REDIS_URL")
  if not url:
    pytest.fail("REAL_REDIS_URL is required for the S14F tests")
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
  harness = Harness(sql, monkeypatch)
  from app.autotrade.go_shadow_policy import GoShadowPolicy
  harness.shadow = GoShadowPolicy(harness.repo, fence=harness.fence, clock=harness.clock, multiple_matches_enabled=lambda: True)
  return harness


def args(**overrides) -> Namespace:
  base = dict(symbol="XAU", since="0", until=str(int(time.time()) + 10_000_000), kafka_json=None, replay_report=None,
              dispositions=None, images_json=None, expected_shas_json=None, risk_review_json=None)
  base.update(overrides)
  return Namespace(**base)


async def report(prod, **overrides):
  await store.init_db()
  async with store._connect() as db:
    return await cli.build_report(args(**overrides), db=db, redis=prod)


def statuses(rep) -> dict[str, str]:
  return {g["name"]: g["status"] for g in rep["gates"]}


async def clean_shadow_window(h):
  await h._ensure()
  ev = event(int(h.clock.now))
  h.offset += 1
  await h.repo.apply(ev, topic=OpportunityTopic, partition=0, offset=h.offset)
  decision = await h.shadow.dry_run_creation(ev)
  return ev, decision


# ---- absence is never a pass -------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_empty_window_passes_nothing(prod, sql):
  rep = await report(prod)
  assert rep["overall"] == "blocked"
  assert set(statuses(rep).values()) == {acc.NOT_MEASURED}                           # not one gate passes on an empty window


@pytest.mark.asyncio
async def test_a_clean_real_shadow_window_passes_only_what_it_actually_measured(h, prod):
  _ev, decision = await clean_shadow_window(h)
  assert decision.outcome == "would_publish"
  rep = await report(prod)
  s = statuses(rep)
  assert s["no_orphan_or_resurrected_lifecycle"] == acc.PASS
  assert s["no_missing_confirmation_or_htf_bypass"] == acc.PASS
  assert s["no_shadow_plan_reservation_card_or_broker_side_effect"] == acc.PASS      # production Redis/Postgres untouched by the dry run
  assert s["no_duplicate_plans_or_orders"] == acc.PASS
  # everything that needs production facts nobody supplied stays not_measured
  for gate in ("no_stale_bootstrap_replay_or_pre_activation_plan", "no_risk_limit_or_group_exposure_violation",
               "kafka_delivery_lag_restart_outage_within_limits", "go_python_differences_reconciled_or_explicitly_approved",
               "deployed_shas_match_reviewed_commits"):
    assert s[gate] == acc.NOT_MEASURED, gate
  assert rep["overall"] == "blocked"
  assert rep["connectivity"]["decisions_by_outcome"] == {"go_shadow:would_publish": 1}
  assert "approves nothing" in rep["note"]


# ---- operator-supplied evidence --------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kafka_gate_needs_the_operators_group_offsets_and_drills(h, prod, tmp_path):
  await clean_shadow_window(h)
  partial = tmp_path / "kafka.json"
  partial.write_text(json.dumps({"consumer_group": "g", "committed_offsets": {"0": 10}}))
  rep = await report(prod, kafka_json=str(partial))
  gate = next(g for g in rep["gates"] if g["name"].startswith("kafka"))
  assert gate["status"] == acc.NOT_MEASURED and "restart_recovery" in gate["evidence"]["missing"]
  partial.write_text(json.dumps({"consumer_group": "g", "committed_offsets": {"0": 10}, "lag_messages": 0,
                                 "restart_recovery": {"ok": True}, "outage_recovery": {"ok": True}}))
  gate = next(g for g in (await report(prod, kafka_json=str(partial)))["gates"] if g["name"].startswith("kafka"))
  assert gate["status"] in {acc.PASS, acc.FAIL}      # decided by the ledger's own measured lag vs the limit
  assert gate["evidence"]["events"] == 1


@pytest.mark.asyncio
async def test_differences_stay_open_until_the_owner_approves_each_by_name_with_evidence(h, prod, tmp_path):
  await clean_shadow_window(h)
  replay = tmp_path / "replay.json"
  replay.write_text(json.dumps({"verdict": "unresolved_differences", "matching": {"matched": 22}, "gates": {"no_go_only": False, "no_python_only": False, "python_replay_is_full_stride": True}}))
  assert next(g for g in (await report(prod, replay_report=str(replay)))["gates"] if g["name"].startswith("go_python"))["status"] == acc.OPEN
  approvals = tmp_path / "approvals.json"
  approvals.write_text(json.dumps({"approved": {"no_go_only": {"approved_by": "", "evidence": "x"}, "no_python_only": {"approved_by": "owner", "evidence": "review-1"}}}))
  gate = next(g for g in (await report(prod, replay_report=str(replay), dispositions=str(approvals)))["gates"] if g["name"].startswith("go_python"))
  assert gate["status"] == acc.OPEN and gate["evidence"]["unapproved"] == ["no_go_only"]     # an approval without an approver does not count
  approvals.write_text(json.dumps({"approved": {"no_go_only": {"approved_by": "owner", "evidence": "review-1"}, "no_python_only": {"approved_by": "owner", "evidence": "review-1"}}}))
  gate = next(g for g in (await report(prod, replay_report=str(replay), dispositions=str(approvals)))["gates"] if g["name"].startswith("go_python"))
  assert gate["status"] == acc.PASS


@pytest.mark.asyncio
async def test_deployed_shas_must_equal_the_reviewed_commits(prod, tmp_path):
  images, reviewed = tmp_path / "images.json", tmp_path / "reviewed.json"
  reviewed.write_text(json.dumps({"analysis-engine": "aaa", "algo-bot": "bbb"}))
  images.write_text(json.dumps({"analysis-engine": "aaa", "algo-bot": "OLD"}))
  gate = next(g for g in (await report(prod, images_json=str(images), expected_shas_json=str(reviewed)))["gates"] if g["name"].startswith("deployed"))
  assert gate["status"] == acc.FAIL and gate["evidence"]["mismatched"] == {"algo-bot": {"deployed": "OLD", "reviewed": "bbb"}}
  images.write_text(json.dumps({"analysis-engine": "aaa", "algo-bot": "bbb"}))
  gate = next(g for g in (await report(prod, images_json=str(images), expected_shas_json=str(reviewed)))["gates"] if g["name"].startswith("deployed"))
  assert gate["status"] == acc.PASS


# ---- every gate can fail -----------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_go_origin_executable_state_during_shadow_fails_the_side_effect_gate(h, prod):
  await clean_shadow_window(h)
  await prod.set("execution:plan:v8:go_leaked", "{}")
  await prod.hset("analysis:go_plans", "v8:go_leaked", "{}")
  plan = {"plan_id": "v8:go_leaked", "thesis_id": "t", "analysis": {"tags": ["authority:go"]}}
  await prod.xadd("execution:trade_plans", {"payload": json.dumps(plan)})
  gate = next(g for g in (await report(prod))["gates"] if g["name"].startswith("no_shadow"))
  assert gate["status"] == acc.FAIL
  assert gate["evidence"]["found"]["execution:plan:v8:go_*"] == 1 and gate["evidence"]["found"]["analysis:go_plans"] == 1


@pytest.mark.asyncio
async def test_two_go_plans_for_one_thesis_fail_the_duplicate_gate(h, prod):
  await clean_shadow_window(h)
  for plan_id in ("v8:go_a", "v8:go_b"):
    await prod.xadd("execution:trade_plans", {"payload": json.dumps({"plan_id": plan_id, "thesis_id": "same", "analysis": {"tags": ["authority:go"]}})})
  gate = next(g for g in (await report(prod))["gates"] if g["name"].startswith("no_duplicate"))
  assert gate["status"] == acc.FAIL and gate["evidence"]["duplicate_theses"] == {"same": 2}


@pytest.mark.asyncio
async def test_a_terminal_without_a_creation_and_a_resurrected_row_fail_the_lifecycle_gate(h, prod, sql):
  await clean_shadow_window(h)
  term = parse_analysis_event(InvalidationTopic, json.dumps(_invalidated(payload={
    "opportunity_id": "opp_never_created", "symbol": "XAU", "strategy": "supply", "reason_code": "ZONE_INVALIDATED", "invalidated_at": int(h.clock.now)})))
  h.offset += 1
  result = await h.repo.apply(term, topic=InvalidationTopic, partition=0, offset=h.offset)
  assert result.disposition == "unknown_terminal_tombstoned"
  gate = next(g for g in (await report(prod))["gates"] if g["name"].startswith("no_orphan"))
  assert gate["status"] == acc.FAIL and gate["evidence"]["orphan_terminals"] == 1
  await sql.exec("DELETE FROM analysis_opportunities WHERE opportunity_id = 'opp_never_created'")
  await sql.exec("UPDATE analysis_opportunities SET terminal_event_id = 'evt-x' WHERE state = 'active'")
  gate = next(g for g in (await report(prod))["gates"] if g["name"].startswith("no_orphan"))
  assert gate["status"] == acc.FAIL and gate["evidence"]["resurrected"] == 1


@pytest.mark.asyncio
async def test_a_live_plan_older_than_the_limit_or_before_the_boundary_fails_the_freshness_gate(h, prod):
  await clean_shadow_window(h)
  await h.repo.record_shadow_decision(opportunity_id="opp_golden_supply_xau", event_id="evt-live", outcome="match_written", reason="go_owned_scope",
                                      details={"match_id": "go_x", "event_age_seconds": 5_000, "delivery_lag_seconds": 1, "observed_at": 10, "activation_boundary": 5}, mode="go")
  gate = next(g for g in (await report(prod))["gates"] if g["name"].startswith("no_stale"))
  assert gate["status"] == acc.FAIL and gate["evidence"]["violations"] == ["go_x"]
  # ...and a go-mode window is no longer a pure shadow window, so the side-effect gate refuses to judge it
  assert next(g for g in (await report(prod))["gates"] if g["name"].startswith("no_shadow"))["status"] == acc.NOT_MEASURED


@pytest.mark.asyncio
async def test_a_decision_taken_without_confirmation_or_htf_facts_fails_the_policy_input_gate(h, prod, sql):
  raw = golden(int(h.clock.now))
  del raw["payload"]["technical_context"]["confirmation"]
  ev = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  await h._ensure()
  h.offset += 1
  await h.repo.apply(ev, topic=OpportunityTopic, partition=0, offset=h.offset)
  await h.repo.record_shadow_decision(opportunity_id=ev.payload.id, event_id=ev.event_id, outcome="would_publish", reason="candidate_published", details={}, mode="go_shadow")
  gate = next(g for g in (await report(prod))["gates"] if g["name"].startswith("no_missing"))
  assert gate["status"] == acc.FAIL and gate["evidence"]["bypasses"] == 1


# ---- the tool itself ------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cli_writes_json_and_markdown_that_state_blockers(h, prod, tmp_path, monkeypatch):
  await clean_shadow_window(h)
  out_md = tmp_path / "a.md"
  out_md.write_text(cli.render_markdown(await report(prod)))
  text = out_md.read_text()
  assert "Overall: `blocked`" in text and "## Blockers" in text and "not_measured" in text and "never an approval" in text


def test_the_acceptance_tooling_can_never_accept_or_grant():
  root = Path(cli.__file__).parents[1]
  for path in (root / "scripts" / "shadow_acceptance.py", root / "analysis_client" / "acceptance.py"):
    tree = ast.parse(path.read_text())
    called = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not called & {"record_acceptance", "begin_transfer", "rollback_all", "AuthorityFence", "PostgresAuthorityStore"}, path.name

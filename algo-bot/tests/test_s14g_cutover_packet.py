"""S14G: the pre-grant operator packet presents facts, reads the real epoch, and stops."""

from __future__ import annotations

import ast
import json
import os
from argparse import Namespace
from pathlib import Path

import pytest
from redis.asyncio import Redis

from app.analysis_client.authority import AuthorityFence, PostgresAuthorityStore
from app.persistence import redis_state, store
from app.scripts import cutover_packet as cli
from tests.test_analysis_authority_fence import Clock
from tests.configuration.canonical_fixtures import install_runtime_overrides

pytestmark = pytest.mark.real_redis


@pytest.fixture
def prod(monkeypatch, event_loop):
  url = os.getenv("REAL_REDIS_URL")
  if not url:
    pytest.fail("REAL_REDIS_URL is required for the S14G tests")
  client = Redis.from_url(url, decode_responses=True)
  event_loop.run_until_complete(client.ping())
  event_loop.run_until_complete(client.flushdb())
  monkeypatch.setattr(redis_state, "_client", client)
  try:
    yield client
  finally:
    event_loop.run_until_complete(client.flushdb())
    event_loop.run_until_complete(client.aclose())


def args(**overrides) -> Namespace:
  base = dict(symbol="XAU", scope="supply", equity=None, acceptance_report=None, images_json=None, drain_seconds=None)
  base.update(overrides)
  return Namespace(**base)


async def packet(prod, **overrides):
  await store.init_db()
  async with store._connect() as db:
    return await cli.build_packet(args(**overrides), db=db, redis=prod)


@pytest.mark.asyncio
async def test_a_python_owned_scope_reports_epoch_zero_as_an_absent_row_and_stops(sql, prod):
  p = await packet(prod)
  assert p["production_status"]["current_epoch_for_scope"] == 0
  assert "absent row" in p["production_status"]["epoch_source"]
  assert p["production_status"]["authority_scopes"].startswith("none recorded")
  assert p["STOP"].startswith("STOP.") and "grants nothing and records nothing" in p["STOP"]
  assert p["operator_action_required"]["explicit_approval_needed"] is True
  assert p["evidence"]["acceptance_report"].startswith("unavailable") and p["deployed_shas"].startswith("unavailable")


@pytest.mark.asyncio
async def test_the_current_epoch_is_read_from_status_never_assumed(sql, prod):
  await store.init_db()
  clock = Clock(1_000_000.0)
  fence = AuthorityFence(PostgresAuthorityStore(), cache_ttl=0.0, clock=clock)
  await fence.record_acceptance("XAU", "supply", "drill", approved_by="drill", ttl_seconds=3600)
  await fence.begin_transfer("XAU", "supply", "go", expected_epoch=0, actor="drill", reason="drill", evidence_ref="drill", drain_seconds=5)
  clock.advance(10)
  await fence.rollback("XAU", "supply", expected_epoch=1, actor="drill", reason="drill", drain_seconds=5)
  p = await packet(prod)
  assert p["production_status"]["current_epoch_for_scope"] == 2
  assert p["production_status"]["epoch_source"] == "analysis_authority_scopes row"


@pytest.mark.asyncio
async def test_only_reviewed_scopes_are_eligible(sql, prod):
  assert (await packet(prod, scope="supply"))["proposed_scope"]["eligible"] is True
  assert (await packet(prod, scope="demand"))["proposed_scope"]["eligible"] is True
  for scope in ("order_block", "fvg", "key_level"):
    assert (await packet(prod, scope=scope))["proposed_scope"]["eligible"] is False
  assert (await packet(prod, symbol="EURUSD"))["proposed_scope"]["eligible"] is False


def test_maximum_exposure_is_computed_from_the_existing_controls_and_names_its_caveat():
  ex = cli.maximum_exposure(2_000.0, stop_max_pips=60, risk_leg_gate=False)
  assert ex["equity_table_lots"] == 0.15 and ex["worst_case_ladder_loss"] == 90.0 and ex["worst_case_risk_leg_loss"] == 0.0
  assert ex["worst_case_group_loss"] == 90.0 and ex["worst_case_group_loss_percent_of_equity"] == 4.5
  with_leg = cli.maximum_exposure(2_000.0, stop_max_pips=60, risk_leg_gate=True)
  assert with_leg["worst_case_risk_leg_loss"] == 7.5 and with_leg["worst_case_group_loss"] == 97.5
  assert any("declarative" in note for note in ex["notes"])
  assert cli.maximum_exposure(None, stop_max_pips=60, risk_leg_gate=False)["status"] == "unavailable"


@pytest.mark.asyncio
async def test_open_positions_and_go_plans_are_listed_from_redis_and_marked_go_origin(sql, prod):
  await prod.sadd("auto_trade:positions", "501")
  await prod.set("auto_trade:position:501", json.dumps({"direction": "SELL", "entry_price": 4354.1, "symbol": "XAU", "group_id": "v8:go_opp_x", "remaining_volume": 1500}))
  await prod.hset("analysis:go_plans", "v8:go_opp_x", "{}")
  exposure = (await packet(prod))["open_exposure_and_pending_plans"]
  assert exposure["status"] == "read" and exposure["count"] == 1 and exposure["registered_go_plans"] == 1
  assert exposure["items"][0]["position_id"] == 501 and exposure["items"][0]["direction"] == "SELL"


@pytest.mark.asyncio
async def test_unreadable_redis_is_printed_as_unavailable_not_estimated(sql):
  await store.init_db()
  async with store._connect() as db:
    p = await cli.build_packet(args(), db=db, redis=None)
  assert p["open_exposure_and_pending_plans"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_blocking_gates_from_the_acceptance_report_are_carried_through(sql, prod, tmp_path):
  report = tmp_path / "acceptance.json"
  report.write_text(json.dumps({"overall": "blocked", "window": {"since": 1, "until": 2}, "gates": [
    {"name": "a", "status": "pass"}, {"name": "b", "status": "not_measured"}, {"name": "c", "status": "open"}]}))
  p = await packet(prod, acceptance_report=str(report))
  assert p["evidence"]["acceptance_report"]["overall"] == "blocked"
  assert p["evidence"]["blocking_gates"] == [{"name": "b", "status": "not_measured"}, {"name": "c", "status": "open"}]


@pytest.mark.asyncio
async def test_generating_the_packet_changes_nothing(sql, prod):
  await store.init_db()
  before_rows = await sql.val("SELECT count(*) FROM analysis_authority_scopes")
  before_keys = sorted(await prod.keys("*"))
  await packet(prod, equity=2000.0)
  assert await sql.val("SELECT count(*) FROM analysis_authority_scopes") == before_rows
  assert await sql.val("SELECT count(*) FROM analysis_authority_acceptance") == 0
  assert sorted(await prod.keys("*")) == before_keys


@pytest.mark.asyncio
async def test_rollback_commands_are_exact_and_the_templates_use_placeholders_only(sql, prod):
  p = await packet(prod)
  rb = p["rollback_commands"]
  assert rb["rollback_scope"].startswith("python -m app.scripts.analysis_authority rollback --symbol XAU --scope supply --expected-epoch <")
  assert "withdraw --symbol XAU --scope supply" in rb["withdraw_only_redis_needed"]
  for template in p["operator_action_required"]["templates_not_executed"]:
    if " accept " in template or " grant " in template:
      assert "<" in template and ">" in template                                 # never a runnable, pre-filled approval
  text = cli.render_markdown(p)
  assert "Operator action (NOT executed)" in text and "STOP." in text


def test_the_packet_tool_can_never_accept_or_grant():
  tree = ast.parse(Path(cli.__file__).read_text())
  called = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
  assert not called & {"record_acceptance", "begin_transfer", "rollback", "rollback_all", "AuthorityFence", "PostgresAuthorityStore"}

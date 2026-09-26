"""Operator CLI: the only way a scope moves to Go, and the way back."""

from __future__ import annotations

import pytest

from app.scripts import analysis_authority as cli
from app.analysis_client.authority import TransferRefused


def _args(*argv):
  return cli.build_parser().parse_args(list(argv))


@pytest.mark.asyncio
async def test_grant_is_refused_without_a_recorded_acceptance_and_writes_nothing(sql):
  with pytest.raises(TransferRefused) as exc:
    await cli.run(_args("grant", "--symbol", "xau", "--scope", "supply", "--expected-epoch", "0", "--evidence", "ev", "--actor", "me", "--reason", "r", "--drain-seconds", "6"))
  assert exc.value.code == "acceptance_missing"
  assert (await cli.run(_args("status")))["scopes"] == []


@pytest.mark.asyncio
async def test_accept_grant_status_rollback_round_trip(sql):
  await cli.run(_args("accept", "--symbol", "XAU", "--scope", "supply", "--evidence", "shadow-2026-09", "--approved-by", "owner", "--ttl-hours", "1"))
  granted = await cli.run(_args("grant", "--symbol", "XAU", "--scope", "supply", "--expected-epoch", "0", "--evidence", "shadow-2026-09", "--actor", "owner", "--reason", "approved", "--drain-seconds", "6"))
  assert granted["handover"]["epoch"] == 1 and granted["handover"]["target_owner"] == "go"

  status = await cli.run(_args("status", "--symbol", "XAU"))
  assert [(s["strategy_id"], s["epoch"], s["target_owner"]) for s in status["scopes"]] == [("supply", 1, "go")]

  rolled = await cli.run(_args("rollback", "--symbol", "XAU", "--scope", "supply", "--expected-epoch", "1", "--actor", "oncall", "--reason", "rollback", "--drain-seconds", "6"))
  assert rolled["handover"]["target_owner"] == "python" and rolled["handover"]["epoch"] == 2

  with pytest.raises(TransferRefused) as stale:
    await cli.run(_args("rollback", "--symbol", "XAU", "--scope", "supply", "--expected-epoch", "1", "--actor", "oncall", "--reason", "again", "--drain-seconds", "6"))
  assert stale.value.code == "stale_epoch"


@pytest.mark.asyncio
async def test_rollback_all_moves_every_go_scope(sql):
  for scope in ("supply", "demand"):
    await cli.run(_args("accept", "--symbol", "XAU", "--scope", scope, "--evidence", "e", "--approved-by", "o"))
    await cli.run(_args("grant", "--symbol", "XAU", "--scope", scope, "--expected-epoch", "0", "--evidence", "e", "--actor", "o", "--reason", "r", "--drain-seconds", "6"))
  out = await cli.run(_args("rollback-all", "--actor", "oncall", "--reason", "incident", "--drain-seconds", "6"))
  assert sorted(r["strategy_id"] for r in out["rolled_back"]) == ["demand", "supply"]


def test_main_prints_a_refusal_and_returns_nonzero(capsys, monkeypatch):
  async def boom(_args):
    raise TransferRefused("acceptance_missing", "no acceptance")
  monkeypatch.setattr(cli, "run", boom)
  assert cli.main(["grant", "--symbol", "XAU", "--scope", "supply", "--expected-epoch", "0", "--evidence", "e", "--actor", "a", "--reason", "r"]) == 2
  assert "acceptance_missing" in capsys.readouterr().out


def test_parser_requires_audit_fields():
  with pytest.raises(SystemExit):
    _args("grant", "--symbol", "XAU", "--scope", "supply", "--expected-epoch", "0", "--evidence", "e")


# ---- S14B: rollback = fence flip, then withdrawal ---------------------------------

async def _seed_go_work():
  """A Go match waiting for its retest plus a queued Go plan, as after a live grant."""
  from app.autotrade import go_opportunity_policy as pol
  from app.autotrade.go_plan_cancel import register_go_plan
  from app.autotrade.multi_match import serialize_matches, strategy_matches_key
  from app.persistence import redis_state
  from tests.test_go_opportunity_policy import event
  ev = event(1_789_961_160)
  match = pol.build_strategy_match(ev, profile=pol.REVIEWED_SCOPES["supply"], epoch=1, now=1_789_961_160)
  client = redis_state.get_client()
  await client.set(strategy_matches_key("XAU"), serialize_matches([match]), ex=600)
  await register_go_plan(client, plan_id="v8:go_queued", match=match, expires_at=1_789_970_000)
  return client, match


@pytest.mark.asyncio
async def test_rollback_flips_the_fence_then_withdraws_go_work(sql):
  from app.autotrade.go_plan_cancel import read_plan_cancel
  from app.autotrade.multi_match import strategy_matches_key
  await cli.run(_args("accept", "--symbol", "XAU", "--scope", "supply", "--evidence", "e", "--approved-by", "o"))
  await cli.run(_args("grant", "--symbol", "XAU", "--scope", "supply", "--expected-epoch", "0", "--evidence", "e", "--actor", "o", "--reason", "r", "--drain-seconds", "6"))
  client, match = await _seed_go_work()
  out = await cli.run(_args("rollback", "--symbol", "XAU", "--scope", "supply", "--expected-epoch", "1", "--actor", "oncall", "--reason", "drill", "--drain-seconds", "6"))
  assert out["handover"]["target_owner"] == "python"
  assert out["withdrawal"]["matches_removed"] == [match.match_id]
  assert sorted(out["withdrawal"]["plans_cancel_requested"]) == sorted(["v8:go_queued", f"v8:{match.match_id}"])
  assert await client.get(strategy_matches_key("XAU")) is None
  assert (await read_plan_cancel(client, "v8:go_queued"))["source"] == "authority_rollback"


@pytest.mark.asyncio
async def test_withdraw_rerun_is_idempotent_and_rollback_all_withdraws_each_scope(sql):
  await cli.run(_args("accept", "--symbol", "XAU", "--scope", "supply", "--evidence", "e", "--approved-by", "o"))
  await cli.run(_args("grant", "--symbol", "XAU", "--scope", "supply", "--expected-epoch", "0", "--evidence", "e", "--actor", "o", "--reason", "r", "--drain-seconds", "6"))
  await _seed_go_work()
  out = await cli.run(_args("rollback-all", "--actor", "oncall", "--reason", "incident", "--drain-seconds", "6"))
  assert [w["scope"] for w in out["withdrawals"]] == ["supply"] and out["withdrawals"][0]["matches_removed"]
  again = await cli.run(_args("withdraw", "--symbol", "XAU", "--scope", "supply", "--actor", "oncall", "--reason", "rerun"))
  assert again["withdrawal"]["matches_removed"] == [] and again["withdrawal"]["plans_cancel_requested"] == []
  assert "v8:go_queued" in again["withdrawal"]["plans_already_requested"]


def test_a_failed_withdrawal_is_reported_and_exits_nonzero(capsys, monkeypatch):
  async def half_done(_args):
    return {"handover": {"epoch": 2}, "withdrawal": {"error": "ConnectionError: redis down", "symbol": "XAU", "scope": "supply"}}
  monkeypatch.setattr(cli, "run", half_done)
  assert cli.main(["rollback", "--symbol", "XAU", "--scope", "supply", "--expected-epoch", "1", "--actor", "a", "--reason", "r"]) == 3
  assert "redis down" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_withdraw_reports_instead_of_raising_when_redis_is_down(sql, monkeypatch):
  async def boom(*_a, **_k):
    raise ConnectionError("redis down")
  monkeypatch.setattr(cli, "withdraw_go_scope", boom)
  out = await cli._withdraw("xau", "supply", "r", "me")
  assert out == {"error": "ConnectionError: redis down", "symbol": "XAU", "scope": "supply"}

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

"""The manual-book export reads inside BEGIN READ ONLY, refuses to read otherwise, and builds the study's query."""

from __future__ import annotations

import asyncio

import pytest

from tools import manual_level_export as export

pytestmark = pytest.mark.no_database


class FakeConnection:
  def __init__(self, read_only="on", signals=(), results=()):
    self.read_only, self.signals, self.results = read_only, list(signals), list(results)
    self.statements: list[str] = []

  async def execute(self, sql, *args):
    self.statements.append(sql)

  async def fetchval(self, sql, *args):
    self.statements.append(sql)
    return self.read_only

  async def fetch(self, sql, *args):
    self.statements.append(sql)
    return self.results if "auto_trade_results" in sql else self.signals


def run(coro):
  return asyncio.run(coro)


def test_the_default_query_is_the_manual_book_and_only_selects():
  sql, params = export.build_query()
  assert sql.startswith("SELECT id, ts, symbol, action, entry, entry_end, sl, original_sl, tps, order_type,")
  assert "FROM manual_signals WHERE trade_stream = ANY($1::text[]) AND parent_id IS NULL ORDER BY ts" in sql
  assert params == [["manual"]]
  assert not any(word in sql.upper().split() for word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"))


def test_filters_add_parameters_not_string_concatenation():
  sql, params = export.build_query(("manual", "algo_manual"), ["xau"], export.parse_since("2026-10-05"))
  assert params == [["manual", "algo_manual"], ["XAU"], 1791158400]
  assert "symbol = ANY($2::text[])" in sql and "ts >= $3" in sql and "XAU" not in sql


def test_every_statement_runs_inside_a_read_only_transaction():
  conn = FakeConnection(signals=[{"id": 5, "tps": "[1.0, 2.0]"}])
  rows = run(export.export(conn))
  assert rows == [{"id": 5, "tps": [1.0, 2.0]}]
  assert conn.statements[0] == "BEGIN READ ONLY"
  assert conn.statements[1] == "SHOW transaction_read_only"
  assert conn.statements[2].startswith("SELECT ") and conn.statements[-1] == "ROLLBACK"


def test_the_export_refuses_when_the_server_does_not_confirm_read_only():
  conn = FakeConnection(read_only="off", signals=[{"id": 1}])
  with pytest.raises(export.ExportRefused):
    run(export.export(conn))
  assert not any(s.startswith("SELECT ") for s in conn.statements)      # nothing was read
  assert conn.statements[-1] == "ROLLBACK"


def test_closed_results_are_joined_by_the_manual_group_id():
  conn = FakeConnection(
    signals=[{"id": 5}, {"id": 6}],
    results=[{"group_id": "manual:5:0", "net_r": None, "result_pips": 55}, {"group_id": "v8:go_opp_x", "result_pips": 1}],
  )
  rows = run(export.export(conn, with_closed_results=True))
  assert [len(r["closed_results"]) for r in rows] == [1, 0]
  assert rows[0]["closed_results"][0]["result_pips"] == 55

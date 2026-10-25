"""Export the owner's manual book to JSONL for the level-attribution study (read-only).

The study (analysis-engine/test/manuallevels) asks which structures the owner's manual entries and stops sit on and whether the analysis engine sees
them. This tool only reads ``manual_signals``: every statement runs inside ``BEGIN READ ONLY`` and the tool refuses to run unless the server confirms
``transaction_read_only = on`` first. The output is production data: write it to a local path and never commit it.

  python -m tools.manual_level_export --out manual-signals.jsonl                       # the manual book: trade_stream = 'manual'
  python -m tools.manual_level_export --out all.jsonl --streams manual,algo_manual     # also the signals the owner armed for the algo
  python -m tools.manual_level_export --out x.jsonl --symbols XAU --since 2026-10-05 --with-closed-results

``--with-closed-results`` joins the closed ``auto_trade_results`` row of a signal: the group id of a manual signal is ``manual:<signal id>:<leg>``
(``trade_stream`` ``manual`` or ``algo_manual``), which is the only link between the two tables. A signal that never filled has no row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

COLUMNS = (
  "id", "ts", "symbol", "action", "entry", "entry_end", "sl", "original_sl", "tps", "order_type",
  "setup_type", "confluence", "note", "result_pips", "status", "fill_state", "filled_at",
  "broker_fill_price", "trade_stream", "parent_id",
)
DEFAULT_STREAMS = ("manual",)
RESULT_COLUMNS = ("group_id", "trade_stream", "closed_at", "symbol", "direction", "result_pips", "net_r", "exit_path", "result_method", "stop_pips", "booked_tp_count")
_GROUP = re.compile(r"^manual:(\d+):")


class ExportRefused(RuntimeError):
  """The export would not be read-only; nothing was read."""


def build_query(streams: Sequence[str] = DEFAULT_STREAMS, symbols: Sequence[str] = (), since: int | None = None) -> tuple[str, list[Any]]:
  """The signal query and its parameters. With the defaults it is exactly the manual-book query of the study."""
  clauses = ["trade_stream = ANY($1::text[])", "parent_id IS NULL"]
  params: list[Any] = [list(streams)]
  if symbols:
    params.append([s.upper() for s in symbols])
    clauses.append(f"symbol = ANY(${len(params)}::text[])")
  if since is not None:
    params.append(int(since))
    clauses.append(f"ts >= ${len(params)}")
  return f"SELECT {', '.join(COLUMNS)} FROM manual_signals WHERE {' AND '.join(clauses)} ORDER BY ts", params


RESULTS_QUERY = (
  f"SELECT {', '.join(RESULT_COLUMNS)} FROM auto_trade_results "
  "WHERE trade_stream IN ('manual', 'algo_manual') AND group_id LIKE 'manual:%' ORDER BY closed_at"
)


def parse_since(value: str | None) -> int | None:
  if not value:
    return None
  if value.isdigit():
    return int(value)
  return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def _row(record: Any) -> dict[str, Any]:
  out = dict(record)
  tps = out.get("tps")
  if isinstance(tps, str):
    try:
      out["tps"] = json.loads(tps)
    except ValueError:
      pass
  return out


async def export(conn: Any, *, streams: Sequence[str] = DEFAULT_STREAMS, symbols: Sequence[str] = (), since: int | None = None,
                 with_closed_results: bool = False) -> list[dict[str, Any]]:
  """Read the signals (and optionally their closed results) inside one READ ONLY transaction."""
  await conn.execute("BEGIN READ ONLY")
  try:
    state = await conn.fetchval("SHOW transaction_read_only")
    if str(state).lower() != "on":
      raise ExportRefused(f"transaction_read_only is {state!r}, refusing to read")
    sql, params = build_query(streams, symbols, since)
    rows = [_row(r) for r in await conn.fetch(sql, *params)]
    if with_closed_results:
      results: dict[int, list[dict[str, Any]]] = {}
      for record in await conn.fetch(RESULTS_QUERY):
        match = _GROUP.match(str(record["group_id"]))
        if match:
          results.setdefault(int(match.group(1)), []).append(dict(record))
      for row in rows:
        row["closed_results"] = results.get(int(row["id"]), [])
    return rows
  finally:
    await conn.execute("ROLLBACK")


async def _run(args: argparse.Namespace) -> int:
  import asyncpg
  from app.core.config import runtime_config

  conn = await asyncpg.connect(args.dsn or runtime_config.runtime.postgres.url, command_timeout=30)
  try:
    rows = await export(conn, streams=[s.strip() for s in args.streams.split(",") if s.strip()], symbols=[s for s in args.symbols.split(",") if s],
                        since=parse_since(args.since), with_closed_results=args.with_closed_results)
  finally:
    await conn.close()
  text = "".join(json.dumps(r, default=str, sort_keys=True) + "\n" for r in rows)
  if args.out == "-":                     # stream to stdout (nothing is written inside a container); the summary goes to stderr
    sys.stdout.write(text)
    print(json.dumps({"out": "-", "signals": len(rows), "streams": args.streams}), file=sys.stderr)
  else:
    Path(args.out).write_text(text)
    print(json.dumps({"out": args.out, "signals": len(rows), "streams": args.streams}))
  return 0


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--out", required=True)
  parser.add_argument("--streams", default=",".join(DEFAULT_STREAMS), help="trade_stream values to read (default: manual)")
  parser.add_argument("--symbols", default="", help="comma-separated symbol filter")
  parser.add_argument("--since", default="", help="UTC date (2026-10-05) or Unix seconds")
  parser.add_argument("--with-closed-results", action="store_true")
  parser.add_argument("--dsn", default="", help="override the configured Postgres URL")
  args = parser.parse_args(argv)
  return asyncio.run(_run(args))


if __name__ == "__main__":
  sys.exit(main())

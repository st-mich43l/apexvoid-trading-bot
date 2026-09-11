"""Auto-only (trade_stream='algo_auto') strategy performance baseline.

2026-09 Key Level structural repair. This is NOT a replay tool: it does not
re-evaluate any historical decision against old or new code. True replay
against the pre-2026-09-07 Market Map structural model is infeasible - no
per-timeframe zone analysis or OHLC context was ever persisted for
autonomous trades, and the one available fallback (live Redis M5 bar
retention) only reaches back a handful of days, nowhere near the purge
date. This script reports real, chronological outcomes from the fields
that DO exist (``auto_trade_results``/``auto_trade_fills``), so any
before/after comparison in its output is "before/after this many trades
closed" against real recorded results - never a counterfactual re-decision.

Strategy names are canonicalized through the same alias table the rest of
the app uses (``app.autotrade.strategy_names.resolve_strategy``) so a
query spanning the 2026-09-08 "Key Level Reaction" -> "Key Level" rename
(PR #507) does not silently undercount one side of the split.

Usage (inside the algo-bot container or local with DATABASE_URL)::

  python -m app.scripts.auto_strategy_baseline --strategy "Key Level" \\
    --symbol XAU --split-at 2026-09-07T09:05:55Z 2026-09-10T07:49:59Z
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.autotrade.strategy_names import resolve_strategy


def _canonical_setup(raw: str | None) -> str:
  if not raw:
    return "(untagged)"
  resolved = resolve_strategy(raw.strip())
  return resolved.canonical if resolved is not None else raw.strip()


def _period_label(closed_at: int, cutoffs: list[int]) -> str:
  index = sum(1 for cutoff in cutoffs if closed_at >= cutoff)
  return f"period_{index}"


def _parse_cutoff(value: str) -> int:
  text = value.strip()
  if text.endswith("Z"):
    text = text[:-1] + "+00:00"
  dt = datetime.fromisoformat(text)
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
  return int(dt.timestamp())


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
  n = len(rows)
  if n == 0:
    return {"trades": 0}
  wins = [row["result_pips"] for row in rows if row["result_pips"] > 0]
  losses = [row["result_pips"] for row in rows if row["result_pips"] < 0]
  net = sum(row["result_pips"] for row in rows)
  return {
    "trades": n,
    "win_pct": round(100.0 * len(wins) / n, 1),
    "avg_win": round(sum(wins) / len(wins), 1) if wins else None,
    "avg_loss": round(sum(losses) / len(losses), 1) if losses else None,
    "net_pips": round(net, 1),
    "pips_per_trade": round(net / n, 2),
  }


async def run(
  *,
  strategy: str | None,
  symbol: str,
  cutoffs: list[int],
) -> dict[str, Any]:
  """``strategy``: a canonical or alias display name (e.g. "Key Level" or
  "Key Level Reaction" - either resolves the same). ``None`` reports every
  strategy, grouped by canonical name. ``cutoffs`` are unix timestamps that
  split the chronological output into ``len(cutoffs) + 1`` periods - pass
  none for a single all-time summary.
  """
  dsn = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN")
  if not dsn:
    raise SystemExit("DATABASE_URL required")
  dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
  pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
  assert pool is not None
  cutoffs = sorted(cutoffs)
  try:
    async with pool.acquire() as conn:
      records = await conn.fetch(
        """
        SELECT result_pips, closed_at, setup_type
        FROM auto_trade_results
        WHERE trade_stream = 'algo_auto' AND symbol = $1
        ORDER BY closed_at
        """,
        symbol,
      )
    rows = [
      {
        "result_pips": float(record["result_pips"]),
        "closed_at": int(record["closed_at"]),
        "setup": _canonical_setup(record["setup_type"]),
      }
      for record in records
    ]
    if strategy is not None:
      resolved = resolve_strategy(strategy)
      canonical = resolved.canonical if resolved is not None else strategy
      rows = [row for row in rows if row["setup"] == canonical]
    periods: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
      periods.setdefault(_period_label(row["closed_at"], cutoffs), []).append(row)
    return {
      "generated_at": datetime.now(timezone.utc).isoformat(),
      "scope": "recorded_outcomes_only_not_a_replay",
      "symbol": symbol,
      "strategy_filter": strategy,
      "cutoffs_utc": [
        datetime.fromtimestamp(c, tz=timezone.utc).isoformat() for c in cutoffs
      ],
      "total": _summarize(rows),
      "by_period": {
        label: _summarize(period_rows)
        for label, period_rows in sorted(periods.items())
      },
      "by_setup": {
        setup: _summarize([row for row in rows if row["setup"] == setup])
        for setup in sorted({row["setup"] for row in rows})
      },
    }
  finally:
    await pool.close()


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--strategy",
    default=None,
    help='Canonical or alias strategy name (e.g. "Key Level"). '
    "Omit to report every strategy.",
  )
  parser.add_argument("--symbol", default="XAU")
  parser.add_argument(
    "--split-at",
    nargs="*",
    default=[],
    metavar="ISO8601_UTC",
    help="Zero or more UTC timestamps splitting the output into periods "
    "(e.g. --split-at 2026-09-07T09:05:55Z 2026-09-10T07:49:59Z)",
  )
  args = parser.parse_args()
  print(
    "auto_strategy_baseline scope=recorded_outcomes_only "
    "(not a replay - see module docstring)",
    file=sys.stderr,
  )
  cutoffs = [_parse_cutoff(value) for value in args.split_at]
  payload = asyncio.run(
    run(strategy=args.strategy, symbol=args.symbol, cutoffs=cutoffs)
  )
  print(
    f"symbol={payload['symbol']} strategy={payload['strategy_filter'] or 'ALL'} "
    f"total_trades={payload['total'].get('trades', 0)}",
    file=sys.stderr,
  )
  for label, summary in payload["by_period"].items():
    print(f"  {label}: {summary}", file=sys.stderr)
  print("by_setup:", file=sys.stderr)
  for setup, summary in payload["by_setup"].items():
    print(f"  {setup:24} {summary}", file=sys.stderr)
  import json

  sys.stdout.write(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
  main()

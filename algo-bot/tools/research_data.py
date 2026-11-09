"""Operator tool for the research data foundation (docs/research-data.md): schema, status, gaps, retention, one-off seeding.

  python -m tools.research_data init-schema          create market_bars, market_bar_revisions, market_quote_minutes, opportunity_outcome_labels
  python -m tools.research_data status               coverage per series + the archiver's health record; exit 1 when unhealthy, 2 when it never ran
  python -m tools.research_data gaps --days 7        bars missing from the stored series, split into scheduled closures and unexplained
  python -m tools.research_data prune --dry-run      what the retention setting would delete
  python -m tools.research_data seed-from-redis      one catch-up pass: copy what the Redis windows hold now (idempotent), then exit

Only ``init-schema``, ``prune`` (without --dry-run) and ``seed-from-redis`` write, and only to the research tables. Run inside the algo-bot container.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from app.persistence import redis_state, store
from app.research import bar_archive, bar_store, outcome_store


async def _init_schema() -> int:
  async with store._connect() as db:
    await bar_store.ensure_schema(db)
    await outcome_store.ensure_schema(db)
  print("schema ready: market_bars, market_bar_revisions, market_quote_minutes, opportunity_outcome_labels")
  return 0


async def _status() -> int:
  cfg = bar_archive.load_config()
  now = time.time()
  async with store._connect() as db:
    rows = await db.fetch(
      "SELECT symbol, timeframe, count(*) n, min(open_ts) first_ts, max(open_ts) last_ts, sum(revisions) revised FROM market_bars "
      "GROUP BY symbol, timeframe ORDER BY symbol, timeframe"
    )
    quotes = await db.fetchrow("SELECT count(*) n, min(minute_ts) first_ts, max(minute_ts) last_ts FROM market_quote_minutes")
    labels = await db.fetchval("SELECT count(*) FROM opportunity_outcome_labels") if await db.fetchval("SELECT to_regclass('opportunity_outcome_labels')") else None
  print(f"{'symbol':<8}{'tf':<5}{'bars':>9}{'first (UTC)':>22}{'last (UTC)':>22}{'lag_s':>8}{'revised':>9}")
  for r in rows:
    first, last = time.strftime('%Y-%m-%d %H:%M', time.gmtime(r['first_ts'])), time.strftime('%Y-%m-%d %H:%M', time.gmtime(r['last_ts']))
    print(f"{r['symbol']:<8}{r['timeframe']:<5}{r['n']:>9}{first:>22}{last:>22}{int(now - r['last_ts']):>8}{int(r['revised'] or 0):>9}")
  print(f"quote minutes: {quotes['n']}" + ("" if not quotes["n"] else f" ({time.strftime('%Y-%m-%d %H:%M', time.gmtime(quotes['first_ts']))} .. {time.strftime('%Y-%m-%d %H:%M', time.gmtime(quotes['last_ts']))})"))
  if labels is not None:
    print(f"outcome labels: {labels}")
  health = {
    (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
    for k, v in (await redis_state.get_client().hgetall(bar_archive.HEALTH_KEY)).items()
  }
  healthy, reasons = bar_archive.health_status(health, now=now, stale_after_seconds=cfg.stale_after_seconds)
  print("archiver health:", "OK" if healthy else "UNHEALTHY")
  for reason in reasons:
    print("  -", reason)
  if health:
    print("  ", {k: v for k, v in health.items() if k != "series"})
  return 0 if healthy else (2 if not health else 1)


async def _gaps(days: int, symbol: str | None) -> int:
  since = int(time.time()) - days * 86400
  cfg = bar_archive.load_config()
  total = unexplained = 0
  async with store._connect() as db:
    for sym in ([symbol.upper()] if symbol else list(cfg.symbols)):
      for tf in cfg.timeframes:
        rows = await db.fetch(
          "SELECT open_ts FROM market_bars WHERE symbol = $1 AND timeframe = $2 AND open_ts >= $3 ORDER BY open_ts", sym, tf, since,
        )
        for gap in bar_store.find_gaps(sym, tf, [int(r["open_ts"]) for r in rows]):
          total += 1
          unexplained += gap.unexplained
          if gap.unexplained:
            f = lambda t: time.strftime("%a %m-%d %H:%M", time.gmtime(t))
            print(f"{sym:<7}{tf:<4} {f(gap.after_ts)} -> {f(gap.before_ts)} UTC  missing {gap.missing}  unexplained {gap.unexplained}")
  print(f"{total} gaps in {days} days, {unexplained} unexplained missing bars (the rest are scheduled closures)")
  return 0 if unexplained == 0 else 1


async def _prune(dry_run: bool) -> int:
  cfg = bar_archive.load_config()
  now = int(time.time())
  async with store._connect() as db:
    if dry_run:
      for tf, days in cfg.retention_days.items():
        cutoff = now - days * 86400
        table, column = ("market_quote_minutes", "minute_ts") if tf == "quote_minutes" else ("market_bars", "open_ts")
        extra = "" if tf == "quote_minutes" else f" AND timeframe = '{tf}'"
        count = await db.fetchval(f"SELECT count(*) FROM {table} WHERE {column} < $1{extra}", cutoff)
        print(f"{tf:<14} retention {days:>5} d  would delete {count}")
      return 0
    print(json.dumps(await bar_store.prune(db, cfg.retention_days, now=now)))
  return 0


async def _seed() -> int:
  cfg = bar_archive.load_config()
  archiver = bar_archive.BarArchiver(redis_state.get_client(), store._connect, cfg)
  async with store._connect() as db:
    await bar_store.ensure_schema(db)
  await archiver.reconcile(full=True, ingest_path=bar_store.INGEST_BACKFILL)
  print("seeded:", archiver._stats, "unexplained missing bars in the last 7 days:", archiver._gaps_unexplained)
  return 0


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  sub = parser.add_subparsers(dest="command", required=True)
  sub.add_parser("init-schema")
  sub.add_parser("status")
  gaps = sub.add_parser("gaps")
  gaps.add_argument("--days", type=int, default=7)
  gaps.add_argument("--symbol")
  prune = sub.add_parser("prune")
  prune.add_argument("--dry-run", action="store_true")
  sub.add_parser("seed-from-redis")
  args = parser.parse_args()
  runner = {
    "init-schema": _init_schema, "status": _status, "seed-from-redis": _seed,
    "gaps": lambda: _gaps(args.days, args.symbol), "prune": lambda: _prune(args.dry_run),
  }[args.command]
  return asyncio.run(runner())


if __name__ == "__main__":
  sys.exit(main())

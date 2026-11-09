"""Forward outcome labeller: hypothetical (replay-derived) outcomes for every published Go opportunity, executed or not.

  python -m tools.outcome_labeller --once [--since-days 14] [--limit 1000] [--dry-run]   label what the stored bars cover, then exit
  python -m tools.outcome_labeller --follow --interval 300                                loop (an operator-started process; nothing starts it)
  python -m tools.outcome_labeller --opportunities-jsonl OPPS --capture CAPTURE           offline: no database, labels to stdout as JSON lines

Assumptions (all stored with each label): --latency-seconds, --slippage-pips, --spread-basis {max,mean,min}, --max-hold-hours. The
methodology and its limits are in docs/research-data.md and the module docstring of app/research/outcome_model.py.
The offline input is one JSON object per line: {"creation": <analysis.opportunity.v1 envelope>, "terminal": <terminal envelope or null>}; the
capture is the file written by tools.capture_bars (its M1 series is the bar source, spreads fall back to the assumption).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from app.core.symbols import pip_for
from app.research import bar_archive, outcome_model, outcome_store
from app.research.outcome_model import Assumptions, Bar


def _assumptions(args) -> Assumptions:
  return Assumptions(
    latency_seconds=args.latency_seconds, slippage_pips=args.slippage_pips, spread_basis=args.spread_basis,
    max_hold_seconds=int(args.max_hold_hours * 3600),
  )


async def _run_db(args, a: Assumptions) -> int:
  from app.persistence import store

  cfg = bar_archive.load_config()
  while True:
    now = int(time.time())
    async with store._connect() as db:
      await outcome_store.ensure_schema(db) if not args.dry_run else None
      summary = await outcome_store.label_batch(
        db, a, symbols=list(cfg.symbols), since_ts=now - int(args.since_days * 86400), limit=args.limit, now=now, dry_run=args.dry_run,
      )
    print(json.dumps({
      "label_set": a.label_set, "examined": summary.examined, "written": summary.written, "skipped_unusable": summary.skipped_unusable,
      "outcomes": dict(sorted(summary.outcomes.items())), "dry_run": args.dry_run,
    }))
    if not args.follow:
      return 0
    await asyncio.sleep(args.interval)


def _run_offline(args, a: Assumptions) -> int:
  capture = json.load(open(args.capture))
  series = capture["timeframes"]["M1"]
  bars = [Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in series]
  symbol = str(capture["symbol"]).upper()
  coverage = bars[-1].open_ts if bars else None
  for line in open(args.opportunities_jsonl):
    if not line.strip():
      continue
    record = json.loads(line)
    spec, quality_version = outcome_model.spec_from_rows(record["creation"], record.get("terminal"), pip=pip_for(symbol))
    if spec is None or spec.symbol != symbol:
      continue
    label = outcome_model.label_opportunity(spec, bars, lambda minute: None, a, coverage_end=coverage)
    print(json.dumps(outcome_store.label_to_row(spec, label, a, quality_version), sort_keys=True))
  return 0


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--once", action="store_true", help="label once and exit (default)")
  parser.add_argument("--follow", action="store_true")
  parser.add_argument("--interval", type=int, default=300)
  parser.add_argument("--since-days", type=float, default=14)
  parser.add_argument("--limit", type=int, default=1000)
  parser.add_argument("--dry-run", action="store_true")
  parser.add_argument("--latency-seconds", type=int, default=0)
  parser.add_argument("--slippage-pips", type=float, default=0.0)
  parser.add_argument("--spread-basis", choices=("max", "mean", "min"), default="max")
  parser.add_argument("--max-hold-hours", type=float, default=24.0)
  parser.add_argument("--opportunities-jsonl")
  parser.add_argument("--capture")
  args = parser.parse_args()
  a = _assumptions(args)
  if args.opportunities_jsonl or args.capture:
    if not (args.opportunities_jsonl and args.capture):
      parser.error("offline mode needs both --opportunities-jsonl and --capture")
    return _run_offline(args, a)
  return asyncio.run(_run_db(args, a))


if __name__ == "__main__":
  sys.exit(main())

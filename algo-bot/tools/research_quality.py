"""Read-only comparison of quality bands, neutral selection and hypothetical quality-based selection on one labelled candidate population.

  python -m tools.research_quality [--since-days 60] [--label-set v1:proximal_limit:max] [--json]
  python -m tools.research_quality --labels-jsonl FILE        offline: rows written by tools.outcome_labeller

Candidates are the first publication of each structural episode; bands are walk-forward within each strategy; outcomes are the labeller's
hypothetical ones (``r_low`` counts ambiguous bars as the stop). Nothing is written, nothing here changes any setting, and the result is
not evidence that quality ranking works unless its interval says so on enough days: read the CI, not the mean. See app/research/quality_report.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from app.research import quality_report


async def _load_db(label_set: str, since_ts: int) -> list[dict]:
  from app.persistence import store

  async with store._connect() as db:
    async with db.transaction(readonly=True):
      rows = await db.fetch("SELECT * FROM opportunity_outcome_labels WHERE label_set = $1 AND created_at >= $2 ORDER BY created_at", label_set, since_ts)
  return [dict(r) for r in rows]


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--since-days", type=float, default=60)
  parser.add_argument("--label-set", default="v1:proximal_limit:max")
  parser.add_argument("--labels-jsonl")
  parser.add_argument("--episode-gap-hours", type=float, default=24.0)
  parser.add_argument("--min-history", type=int, default=quality_report.MIN_HISTORY)
  parser.add_argument("--r-model", choices=("target", "ladder"), default="target", help="target: first touch of the published stop/target; ladder: 50%% at 1R, break-even, 50%% at 2R")
  parser.add_argument("--include-gaps", action="store_true", help="also use labels whose bars had an unexplained gap")
  parser.add_argument("--json", action="store_true")
  args = parser.parse_args()
  if args.labels_jsonl:
    rows = [json.loads(line) for line in open(args.labels_jsonl) if line.strip()]
  else:
    rows = asyncio.run(_load_db(args.label_set, int(time.time() - args.since_days * 86400)))
  result = quality_report.report(
    rows, episode_gap=int(args.episode_gap_hours * 3600), min_history=args.min_history, include_gaps=args.include_gaps, r_model=args.r_model,
  )
  print(json.dumps(result, indent=2, default=str) if args.json else quality_report.render(result))
  return 0


if __name__ == "__main__":
  sys.exit(main())

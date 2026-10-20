"""Q2: what a change to the opportunity-quality rules did to the published opportunities, and were the ones it removed any good?

Inputs (all written by ``analysis-engine/test/aequality``; see tools/q1_opportunity_quality.py):
  --before / --after  records.jsonl of the replay before and after the change (AEQ_DUMP=dir go test ./test/aequality -run TestReplayAllStrategies)
  --random            random.jsonl of the BEFORE replay (every removed opportunity has its matched random counterparts there)

    python tools/q2_before_after.py --before old/records.jsonl --after new/records.jsonl --random old/random.jsonl --out q2.json

Reported (hypothetical outcomes on closed candles, never broker P&L):
  counts      per strategy: kept, removed and new opportunities
  stars       the confluence stars of the confirmed opportunities, before and after
  factors     how often each confluence factor is published as true on a confirmed opportunity, before and after
  versus_random  removed and kept opportunities against matched random entries (same risk, cost, ladder and horizon), by strategy
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import q1_opportunity_quality as q1  # noqa: E402


def load(path):
  return {r["id"]: r for r in q1.load_jsonl(path)}


def counts(before, after):
  out = collections.defaultdict(lambda: {"kept": 0, "removed": 0, "new": 0})
  for i, r in before.items():
    out[r["strategy"]]["kept" if i in after else "removed"] += 1
  for i, r in after.items():
    if i not in before:
      out[r["strategy"]]["new"] += 1
  return dict(out)


def stars(records):
  out = collections.defaultdict(collections.Counter)
  for r in records.values():
    if r.get("has_reaction"):
      out[r["strategy"]][r["stars"]] += 1
  return {k: dict(sorted(v.items())) for k, v in out.items()}


def factors(records):
  names = ("wick", "structural", "displacement", "htf_aligned", "session", "choch", "fib")
  tally = collections.defaultdict(lambda: collections.Counter())
  total = collections.Counter()
  for r in records.values():
    if not r.get("has_reaction"):
      continue
    total[r["strategy"]] += 1
    for name in names:
      if r["factors"].get(name):
        tally[r["strategy"]][name] += 1
  return {s: {"n": total[s], **{n: round(tally[s][n] / total[s], 3) for n in names}} for s in total}


def split_vs_random(before, after, randoms):
  removed = [r for i, r in before.items() if i not in after]
  kept = [r for i, r in before.items() if i in after]
  out = {}
  for label, rows in (("removed", removed), ("kept", kept)):
    result = q1.versus_random(rows, randoms)
    out[label] = {"pooled": result["pooled"], "by_strategy": {k: v.get("all", {}) for k, v in result["by_strategy"].items()}}
  return out


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--before", required=True)
  ap.add_argument("--after", required=True)
  ap.add_argument("--random", required=True)
  ap.add_argument("--out", required=True)
  args = ap.parse_args()
  before, after = load(args.before), load(args.after)
  randoms = {r["id"]: r for r in q1.load_jsonl(args.random)}
  result = {
    "total": {"before": len(before), "after": len(after)},
    "counts": counts(before, after),
    "stars": {"before": stars(before), "after": stars(after)},
    "factors": {"before": factors(before), "after": factors(after)},
    "versus_random": split_vs_random(before, after, randoms),
  }
  with open(args.out, "w") as fh:
    json.dump(result, fh, indent=1, sort_keys=True)
  print(json.dumps(result["counts"], indent=1, sort_keys=True))


if __name__ == "__main__":
  main()

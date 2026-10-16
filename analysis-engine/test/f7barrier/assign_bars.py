"""Split a {symbol: [bars]} request across committed captures so every bar is evaluated once, on the capture that gives it the
longest M5 history (the engine that ran in production kept about 3,000 M5 bars, so more history is closer to production).

    assign_bars.py --bars bars.json --testdata analysis-engine/testdata --out-dir /tmp/f7/assign [--exclude m1]
Writes <out-dir>/<capture-name>.bars.json and prints the assignment summary.
"""
import argparse
import collections
import glob
import json
import os
import re


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--bars", required=True)
  parser.add_argument("--testdata", required=True)
  parser.add_argument("--out-dir", required=True)
  parser.add_argument("--min-history", type=int, default=1200, help="M5 bars a capture must hold before the bar")
  args = parser.parse_args()
  wanted = json.load(open(args.bars))
  captures = collections.defaultdict(list)
  for path in sorted(glob.glob(os.path.join(args.testdata, "replay-*-production-capture-2026*.json"))):
    name = os.path.basename(path)
    if re.search(r"capture-20260921", name):
      continue  # pre-cutover, outside the production window
    raw = json.load(open(path))
    times = [row[0] for row in raw["timeframes"]["M5"]]
    captures[raw["symbol"]].append((name, times[0], times[-1], times))
  os.makedirs(args.out_dir, exist_ok=True)
  assigned = collections.defaultdict(lambda: collections.defaultdict(list))
  unassigned = collections.Counter()
  for symbol, bars in wanted.items():
    for bar in bars:
      best = None
      for name, first, last, times in captures.get(symbol, []):
        if not (first <= bar <= last):
          continue
        import bisect
        history = bisect.bisect_right(times, bar)
        if history >= args.min_history and (best is None or history > best[1]):
          best = (name, history)
      if best is None:
        unassigned[symbol] += 1
      else:
        assigned[best[0]][symbol].append(bar)
  summary = {}
  for name, per_symbol in assigned.items():
    with open(os.path.join(args.out_dir, name + ".bars.json"), "w") as out:
      json.dump(per_symbol, out)
    summary[name] = {s: len(v) for s, v in per_symbol.items()}
  print(json.dumps({"assigned": summary, "unassigned_no_capture_or_short_history": dict(unassigned)}, indent=1))


if __name__ == "__main__":
  main()

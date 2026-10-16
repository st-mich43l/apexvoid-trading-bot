"""Anatomy of the Go barriers that decided production admissions: what each blocking barrier is built from.

For every rejected event with a replayed Go book, find the barrier that decided the rule (the replay's ``c.barrier``) and the
live zones it was merged from, then measure what the same-side union adds:

* ``constituents``   live, width-gated zones of M5/M15/H1 inside the merged band (the exact inputs ``barrier.Build`` merged)
* ``inflation``      merged width / widest constituent (1.0 = a single zone, no union)
* ``single_hit``     is the planned entry inside ONE constituent, or only inside the union?  A block that exists only in the
                     union is a block no single zone supports ("same-side union must not imply unsupported prices")
* ``gap_fraction``   share of the merged band covered by no constituent (a chain of zones bridged by touching edges)
* ``unmerged``       the decision the same rules give on the constituents one by one (each its own barrier)

    anatomy.py --replay replay.jsonl --events events.jsonl --books-dir out/f7 --out anatomy.json
"""
import argparse
import collections
import glob
import json
import os
import statistics

PIP = {"XAU": 0.1, "EURUSD": 1e-4, "GBPUSD": 1e-4, "USDJPY": 0.01, "GBPJPY": 0.01}
LIVE = {"fresh", "touched", "partially_mitigated"}
MAX_ATR, MAX_PIPS = 2.0, 100.0  # config/execution.yml execution.policy.execution_zone_max_width_{atr,pips}


def load_go(books_dir):
  go = {}
  for path in sorted(glob.glob(os.path.join(books_dir, "*.go.jsonl"))):
    for line in open(path):
      rec = json.loads(line)
      key = (rec["symbol"], rec["bar"])
      if key not in go or rec["history_bars_m5"] > go[key]["history_bars_m5"]:
        go[key] = rec
  return go


def union_length(spans):
  total, end = 0.0, None
  for lo, hi in sorted(spans):
    if end is None or lo > end:
      total += hi - lo
      end = hi
    elif hi > end:
      total += hi - end
      end = hi
  return total


def constituents(rec, barrier, pip):
  side = barrier["side"]
  out = []
  for z in rec["raw_zones"]:
    if z["side"] != ("buy" if side == "buy" else "sell") and not (z["kind"] == ("demand" if side == "buy" else "supply")):
      continue
    if z["kind"] != ("demand" if side == "buy" else "supply") or z["state"] not in LIVE:
      continue
    width = z["high"] - z["low"]
    if width <= 0 or z["atr"] <= 0 or width / z["atr"] > MAX_ATR or width / pip > MAX_PIPS:
      continue
    if z["high"] < barrier["low"] - 1e-9 or z["low"] > barrier["high"] + 1e-9:
      continue
    out.append(z)
  return out


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--replay", required=True)
  parser.add_argument("--events", required=True)
  parser.add_argument("--books-dir", required=True)
  parser.add_argument("--out", required=True)
  args = parser.parse_args()
  go = load_go(args.books_dir)
  events = {json.loads(l)["match_id"] + "|" + str(json.loads(l)["ts"]): json.loads(l) for l in open(args.events)}
  rows, agg = [], collections.defaultdict(list)
  seen_by_type = collections.Counter()
  for line in open(args.replay):
    r = json.loads(line)
    if r["kind"] != "rejected" or "c" not in r or r["c"].get("barrier") is None or r["c"]["allowed"]:
      continue
    rec = go[(r["symbol"], r["bar"])]
    ev = events[r["match_id"] + "|" + str(r["ts"])]
    pip = PIP[r["symbol"]]
    b = r["c"]["barrier"]
    cons = constituents(rec, b, pip)
    width = b["high"] - b["low"]
    covered = union_length([(max(z["low"], b["low"]), min(z["high"], b["high"])) for z in cons]) if cons else 0.0
    planned = ev["planned_entry"]
    single = [z for z in cons if z["low"] <= planned <= z["high"]]
    widest = max((z["high"] - z["low"] for z in cons), default=0.0)
    row = {
      "match_id": r["match_id"], "symbol": r["symbol"], "strategy": r["strategy"], "direction": r["direction"], "ts": r["ts"],
      "reason": r["c"]["reason"], "barrier_width_pips": round(width / pip, 1), "barrier_width_atr": round(width / ev["atr"], 2),
      "n_constituents": len(cons), "inflation": round(width / widest, 2) if widest > 0 else None,
      "gap_fraction": round(1 - covered / width, 3) if width > 0 and cons else None,
      "entry_in_single_constituent": bool(single), "entry_in_merged": b["low"] <= planned <= b["high"],
      "tfs": sorted({z["tf"] for z in cons}), "states": dict(collections.Counter(z["state"] for z in cons)),
      "max_touches": max((z["touches"] for z in cons), default=None), "min_touches": min((z["touches"] for z in cons), default=None),
      "strength_max": max((z["strength"] for z in cons), default=None), "barrier_score": b["score"], "barrier_tier": b["tier"],
      "constituent_coverage_complete": bool(cons) and covered >= 0.98 * width,
    }
    rows.append(row)
  def pct(n, d):
    return round(100.0 * n / d, 1) if d else None
  complete = [x for x in rows if x["constituent_coverage_complete"]]
  summary = {
    "blocking_barriers": len(rows), "with_complete_constituents": len(complete),
    "single_zone_barrier_pct": pct(sum(1 for x in complete if x["n_constituents"] == 1), len(complete)),
    "merged_barrier_pct": pct(sum(1 for x in complete if x["n_constituents"] > 1), len(complete)),
    "median_constituents": statistics.median([x["n_constituents"] for x in complete]) if complete else None,
    "median_width_pips": statistics.median([x["barrier_width_pips"] for x in rows]) if rows else None,
    "median_width_atr": statistics.median([x["barrier_width_atr"] for x in rows]) if rows else None,
    "wider_than_one_zone_limit_pct": pct(sum(1 for x in rows if x["barrier_width_atr"] > MAX_ATR), len(rows)),
    "block_only_in_union_pct": pct(sum(1 for x in complete if x["entry_in_merged"] and not x["entry_in_single_constituent"]), sum(1 for x in complete if x["entry_in_merged"])),
    "median_gap_fraction": statistics.median([x["gap_fraction"] for x in complete if x["gap_fraction"] is not None]) if complete else None,
  }
  by = collections.defaultdict(lambda: collections.Counter())
  for x in complete:
    by[x["symbol"]]["n"] += 1
    by[x["symbol"]]["merged"] += x["n_constituents"] > 1
    by[x["symbol"]]["union_only"] += x["entry_in_merged"] and not x["entry_in_single_constituent"]
  summary["by_symbol"] = {k: dict(v) for k, v in by.items()}
  json.dump({"summary": summary, "rows": rows}, open(args.out, "w"), separators=(",", ":"))
  print(json.dumps(summary, indent=1))


if __name__ == "__main__":
  main()

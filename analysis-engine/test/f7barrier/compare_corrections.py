"""Old versus corrected opposing-barrier decisions on the same replayed opportunities.

Inputs are four ``decide_dump.py`` files (rule revision x book dump):

    A  old rule, old books   (master: the baseline)
    B  new rule, old books   (the target-room correction alone)
    C  old rule, new books   (the lifecycle correction alone)
    D  new rule, new books   (the proposed change)

Every decision that differs between A and D is listed with its cause (which correction moved it), its old and new reason and
room. Hypothetical outcomes use the plain causal stop / +1R / +2R race of ``simulate.py`` (NOT the TradePlan ladder): they are a
sanity check that corrected decisions are not concentrated in bad trades, never a profit claim.

    python compare_corrections.py --a A.jsonl --b B.jsonl --c C.jsonl --d D.jsonl --testdata analysis-engine/testdata --out result.json
"""
import argparse
import bisect
import collections
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simulate import PIP, load_candles, race  # noqa: E402


def load(path):
  rows = {}
  for line in open(path):
    r = json.loads(line)
    rows[(r["capture"], r["bar"], r["id"])] = r
  return rows


def label(r):
  return "allow" if r["allowed"] else r["reason"]


def main():
  ap = argparse.ArgumentParser()
  for k in "abcd":
    ap.add_argument("--" + k, required=True)
  ap.add_argument("--testdata", required=True)
  ap.add_argument("--out", required=True)
  ap.add_argument("--cost-pips", type=float, default=1.0)
  args = ap.parse_args()
  A, B, C, D = (load(getattr(args, k)) for k in "abcd")
  keys = sorted(set(A) & set(B) & set(C) & set(D))
  if not (len(keys) == len(A) == len(B) == len(C) == len(D)):
    raise SystemExit(f"runs do not cover the same opportunities: {len(A)} {len(B)} {len(C)} {len(D)} common {len(keys)}")
  candles = load_candles(args.testdata)
  times = {s: [t for t, _ in rows] for s, rows in candles.items()}

  totals = collections.Counter()
  changed = []
  matrix = collections.Counter()
  by_strategy = collections.defaultdict(collections.Counter)
  by_symbol = collections.defaultdict(collections.Counter)
  cause = collections.Counter()
  for k in keys:
    a, b, c, d = A[k], B[k], C[k], D[k]
    totals["opportunities"] += 1
    if a["admissible"]:
      totals["admissible"] += 1
      totals["admissible_allowed_old"] += a["allowed"]
      totals["admissible_allowed_new"] += d["allowed"]
    if a["allowed"] == d["allowed"] and a["reason"] == d["reason"] and (a["barrier"] if "barrier" in a else None) == (d["barrier"] if "barrier" in d else None):
      continue
    why = "both" if (label(a) != label(b) and label(a) != label(c)) else "target_room_rule" if label(a) != label(b) else "zone_lifecycle" if label(a) != label(c) else "other"
    if label(a) == label(d):
      why = "same_label_other_barrier"
    key = (label(a), label(d))
    totals["changed_decisions"] += 1
    if a["admissible"]:
      totals["changed_admissible"] += 1
    matrix[f"{key[0]} -> {key[1]}" + ("" if a["admissible"] else "  [resting, never admitted]")] += 1
    cause[why] += 1
    by_strategy[a["strategy"]][f"{key[0]} -> {key[1]}"] += 1
    by_symbol[a["symbol"]][f"{key[0]} -> {key[1]}"] += 1
    changed.append((k, a, d, why))

  # hypothetical outcomes of admissible candidates, by group
  def simulate(r):
    sym = r["symbol"]
    if sym not in candles or r.get("stop_envelope") is None:
      return None
    pip = PIP[sym]
    env = r["stop_envelope"]
    floor, cap = float(env.get("FloorPips") or env.get("floor_pips") or 0), float(env.get("CapPips") or env.get("cap_pips") or 0)
    if not floor or not cap:
      return None
    entry = r["price"]
    stop_pips = min(cap, max(floor, abs(entry - r["invalidation"]) / pip))
    eff = entry + args.cost_pips * pip if r["direction"] == "BUY" else entry - args.cost_pips * pip
    first = bisect.bisect_right(times[sym], r["bar"] + 300 - 1)
    horizon = candles[sym][first:first + 144]
    if len(horizon) < 6:
      return None
    res = race(r["direction"], eff, stop_pips * pip, horizon, (1.0, 2.0))
    return res

  def stats(rows):
    xs = []
    for r in rows:
      res = simulate(r)
      if res is None:
        continue
      r1, r2 = res[1.0], res[2.0]
      xs.append((r1[0] == "win", r2[1] if r2[0] != "censored" else r2[1], (r["symbol"], r["direction"], r["bar"] // 3600)))
    n = len(xs)
    if not n:
      return {"n": 0}
    exp = sum(x[1] for x in xs) / n
    clusters = collections.defaultdict(list)
    for x in xs:
      clusters[x[2]].append(x[1])
    cl = list(clusters.values())
    rnd = random.Random(7)
    boots = sorted(sum(sum(rnd.choice(cl)) for _ in cl) / max(1, sum(len(rnd.choice(cl)) for _ in cl)) for _ in range(300))
    return {"n": n, "episodes": len(cl), "win_at_1R": round(sum(x[0] for x in xs) / n, 3), "expectancy_2R_ladder_R": round(exp, 3),
            "ci95": [round(boots[7], 3), round(boots[292], 3)]}

  groups = {
    "newly admitted (block -> allow), admissible": [d for (_, a, d, _) in changed if a["admissible"] and not a["allowed"] and d["allowed"]],
    "newly blocked (allow -> block), admissible": [d for (_, a, d, _) in changed if a["admissible"] and a["allowed"] and not d["allowed"]],
    "admitted before and after, admissible": [D[k] for k in keys if A[k]["admissible"] and A[k]["allowed"] and D[k]["allowed"]],
    "still blocked, admissible": [D[k] for k in keys if A[k]["admissible"] and not A[k]["allowed"] and not D[k]["allowed"]],
  }
  outcomes = {name: stats(rows) for name, rows in groups.items()}

  # room (pips) behind each correction
  def room(rows):
    vals = sorted(r["usable_room_pips"] for r in rows if r.get("usable_room_pips") is not None)
    return {"n": len(vals), "median_pips": vals[len(vals) // 2] if vals else None}
  newly = groups["newly admitted (block -> allow), admissible"]
  detail = {
    "newly_admitted_room_in_front_pips": room(newly),
    "newly_admitted_still_has_a_barrier_ahead": sum(1 for r in newly if r.get("barrier")),
    "newly_admitted_no_barrier": sum(1 for r in newly if not r.get("barrier")),
  }
  adverse = [{"capture": k[0], "bar": k[1], "id": k[2], "symbol": d["symbol"], "strategy": d["strategy"], "direction": d["direction"], "old": label(a), "new": label(d),
              "cause": why, "old_barrier": a.get("barrier"), "new_barrier": d.get("barrier"), "old_member": (a.get("decisive_member") or {}).get("id"),
              "new_member": (d.get("decisive_member") or {}).get("id")}
             for (k, a, d, why) in changed if (a["admissible"] and a["allowed"] and not d["allowed"])][:25]
  samples = collections.defaultdict(list)
  for (k, a, d, why) in changed:
    if a["admissible"] and len(samples[(a["strategy"], label(a), label(d))]) < 2:
      samples[(a["strategy"], label(a), label(d))].append({"capture": k[0], "bar": k[1], "id": k[2], "symbol": d["symbol"], "direction": d["direction"], "planned": a["planned"],
          "price": a["price"], "old": label(a), "new": label(d), "cause": why, "old_barrier": a.get("barrier"), "old_member": a.get("decisive_member"),
          "new_barrier": d.get("barrier"), "new_room_pips": d.get("usable_room_pips"), "behind_excluded": d.get("behind_excluded")})
  result = {"totals": dict(totals), "transitions": dict(matrix), "cause": dict(cause), "by_strategy": {k: dict(v) for k, v in by_strategy.items()},
            "by_symbol": {k: dict(v) for k, v in by_symbol.items()}, "hypothetical_outcomes": outcomes, "detail": detail, "adverse_examples": adverse,
            "samples": [v for vs in samples.values() for v in vs][:40]}
  json.dump(result, open(args.out, "w"), indent=1)
  print(json.dumps({k: result[k] for k in ("totals", "transitions", "cause", "by_symbol", "hypothetical_outcomes", "detail")}, indent=1))
  print("by_strategy", json.dumps(result["by_strategy"]))


main()

#!/usr/bin/env python3
"""The opposing-barrier book: the zone consumer that reaches execution.

Algo Bot's execution-time target-room recheck (structural_target_room.py) reads the barrier book. The frozen Python built it
from every timeframe's merged, HTF-boosted `zones` (`structural_barriers.build_structural_barrier_book`); Go publishes the
book built from the V2 zone state of M5/M15/H1 (`redis.BuildZoneBook` -> `barrier.Build`). This tool compares the two at
every hourly checkpoint: the barrier sets, and what execution actually consumes from them, the nearest opposing barrier and
therefore the room in front of a BUY / a SELL.

Usage: barrier_compare.py --dir <dump dir> --ref a1c77584 --out barrier-diff.json
"""
import argparse, collections, json, os, statistics

PIP = {"XAU": 0.1, "EURUSD": 1e-4, "GBPUSD": 1e-4, "USDJPY": 0.01, "GBPJPY": 0.01}


def overlap(a, b):
  lo, hi = max(a[0], b[0]), min(a[1], b[1])
  return 0.0 if hi <= lo else (hi - lo) / max(1e-12, min(a[1] - a[0], b[1] - b[0]))


def nearest(barriers, price, direction):
  """Distance from price to the nearest barrier that opposes a trade in `direction`."""
  best = None
  for b in barriers:
    if direction == "BUY" and b["side"] == "sell" and b["high"] > price:
      d = max(0.0, b["low"] - price)
    elif direction == "SELL" and b["side"] == "buy" and b["low"] < price:
      d = max(0.0, price - b["high"])
    else:
      continue
    best = d if best is None or d < best else best
  return best


def compare(directory, ref, field):
  tot = collections.Counter(); diffs = collections.defaultdict(list); per_symbol = collections.defaultdict(collections.Counter)
  for fn in sorted(os.listdir(directory)):
    if not fn.endswith(".go.jsonl"): continue
    cap = fn[:-len(".go.jsonl")]
    py_path = os.path.join(directory, f"{cap}.py-{ref}.jsonl")
    if not os.path.exists(py_path): continue
    go = {json.loads(l)["bar"]: json.loads(l) for l in open(os.path.join(directory, fn))}
    py = {json.loads(l)["bar"]: json.loads(l) for l in open(py_path)}
    for bar in sorted(set(go) & set(py)):
      g, p = go[bar], py[bar]
      if "barriers" not in g or field not in p: continue
      sym = g["symbol"]; pip = PIP[sym]; price = p["price"]
      gb = g["barriers"] or []; pb = p[field] or []
      tot["checkpoints"] += 1; per_symbol[sym]["checkpoints"] += 1
      tot["python_barriers"] += len(pb); tot["go_barriers"] += len(gb)
      for side in ("buy", "sell"):
        pS = [b for b in pb if b["side"] == side]; gS = [b for b in gb if b["side"] == side]
        for b in pS:
          if any(overlap((b["low"], b["high"]), (c["low"], c["high"])) >= 0.5 for c in gS): tot["python_barrier_matched"] += 1
          else: tot["python_barrier_unmatched"] += 1
        for c in gS:
          if not any(overlap((b["low"], b["high"]), (c["low"], c["high"])) >= 0.5 for b in pS): tot["go_barrier_unmatched"] += 1
      for tier in ("level", "zone", "major"):
        tot["python_tier_" + tier] += sum(1 for b in pb if b["tier"] == tier)
        tot["go_tier_" + tier] += sum(1 for b in gb if b["tier"] == tier)
      for direction in ("BUY", "SELL"):
        # The hard gate of structural_target_room: the planned entry inside an opposing barrier is rejected.
        def contained(book):
          opp = "sell" if direction == "BUY" else "buy"
          return any(b["side"] == opp and b["low"] <= price <= b["high"] for b in book)
        cp, cg = contained(pb), contained(gb)
        tot["entry_blocked_python"] += cp; tot["entry_blocked_go"] += cg
        tot["entry_block_agree" if cp == cg else ("entry_block_go_only" if cg else "entry_block_python_only")] += 1
        tot["entry_evaluations"] += 1
        dp, dg = nearest(pb, price, direction), nearest(gb, price, direction)
        key = direction
        if dp is None and dg is None:
          tot["room_both_none"] += 1
        elif dp is None:
          tot["room_python_none_go_has"] += 1
        elif dg is None:
          tot["room_go_none_python_has"] += 1
        else:
          delta = (dg - dp) / pip
          diffs[key].append(delta); diffs["ALL"].append(delta)
          tot["room_compared"] += 1; per_symbol[sym]["room_compared"] += 1
          if abs(delta) <= 1.0: tot["room_within_1_pip"] += 1; per_symbol[sym]["room_within_1_pip"] += 1
          elif delta < 0: tot["go_room_smaller"] += 1; per_symbol[sym]["go_room_smaller"] += 1
          else: tot["go_room_larger"] += 1; per_symbol[sym]["go_room_larger"] += 1
  def q(v, f):
    if not v: return None
    v = sorted(v); return round(v[min(len(v) - 1, int(f * len(v)))], 2)
  summary = {k: {"n": len(v), "median_pips": q(v, .5), "p10": q(v, .1), "p90": q(v, .9), "min": min(v), "max": max(v)} for k, v in diffs.items() if v}
  return {"reference": ref, "python_book": field, "counts": dict(tot), "room_delta_pips_go_minus_python": summary,
          "per_symbol": {k: dict(v) for k, v in per_symbol.items()}}


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--dir", required=True); ap.add_argument("--refs", default="a1c77584,1c9f323"); ap.add_argument("--out", required=True)
  a = ap.parse_args()
  out = {"description": "Go zone-book barriers vs the frozen Python barrier books at every hourly checkpoint. 'barriers' is the scanner-side book "
         "(merged, HTF-boosted zones of every timeframe, tiered, confluence-boosted); 'barriers_recheck' is the book the frozen worker built at "
         "TradePlan time (supply/demand zones of each timeframe, mitigated dropped, no HTF boost).", "comparisons": []}
  for ref in a.refs.split(","):
    for field in ("barriers", "barriers_recheck"):
      res = compare(a.dir, ref, field)
      if res["counts"].get("checkpoints"): out["comparisons"].append(res)
  json.dump(out, open(a.out, "w"), indent=1)
  for r in out["comparisons"]:
    c = r["counts"]
    print(r["reference"], r["python_book"], {k: c.get(k) for k in ("checkpoints", "python_barriers", "go_barriers", "room_compared", "room_within_1_pip", "go_room_smaller", "go_room_larger", "entry_evaluations", "entry_blocked_python", "entry_blocked_go")}, r["room_delta_pips_go_minus_python"].get("ALL", {}).get("median_pips"))


main()

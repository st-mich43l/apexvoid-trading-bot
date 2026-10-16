#!/usr/bin/env python3
"""Causality / repaint audit over hourly checkpoint dumps (Python oracle or Go).

For every pair of consecutive checkpoints of one capture and timeframe it checks, on the elements that lie
inside the later checkpoint's window:
  - confirmation: no swing is reported before its fractal is confirmed (confirmed_index_t <= last_t);
  - repaint (swings, breaks): an element reported at checkpoint k with a time inside both windows is reported
    again, with the same kind/price/level, at checkpoint k+1.
Writes a JSON summary. Usage: causality_check.py --dir <out dir> --suffix go.jsonl|py-1c9f323.jsonl --out file.json
"""
import argparse, collections, json, os

def key_swing(s): return (s["kind"], round(s["price"], 6), s["index_t"])
def key_break(b): return (b["kind"], b["direction"], round(b["level"], 6), b["index_t"])

def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--dir", required=True); ap.add_argument("--suffix", required=True); ap.add_argument("--out", required=True)
  a = ap.parse_args()
  tot = collections.Counter(); examples = []
  for fn in sorted(os.listdir(a.dir)):
    if not fn.endswith("." + a.suffix): continue
    rows = [json.loads(l) for l in open(os.path.join(a.dir, fn))]
    for tf in ("M5", "M15", "H1"):
      prev = None
      for r in rows:
        fr = r["frames"][tf]
        for s in fr["swings"]:
          tot["swings_checked"] += 1
          ct = s.get("confirmed_index_t")
          if ct is not None and ct > fr["last_t"]:
            tot["swing_reported_before_confirmation"] += 1
        if prev is not None:
          lo = min((s["index_t"] for s in fr["swings"]), default=None)
          later_s = {key_swing(s) for s in fr["swings"]}
          for s in prev["swings"]:
            if lo is not None and s["index_t"] > lo and s["index_t"] <= prev["last_t"]:
              tot["swing_repaint_checked"] += 1
              if key_swing(s) not in later_s:
                tot["swing_repainted_or_dropped"] += 1
                if len(examples) < 10: examples.append({"capture": fn, "tf": tf, "swing": s, "next_last_t": fr["last_t"]})
          later_b = {key_break(b) for b in fr["breaks"]}
          for b in prev["breaks"]:
            if lo is not None and b["index_t"] > lo:
              tot["break_repaint_checked"] += 1
              if key_break(b) not in later_b: tot["break_repainted_or_dropped"] += 1
        prev = fr
  json.dump({"suffix": a.suffix, "counts": dict(tot), "examples": examples}, open(a.out, "w"), indent=1)
  print(a.suffix, dict(tot))

main()

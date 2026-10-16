"""Render a seeded, stratified sample of F-7 admission events as charts for labelling by eye.

Each chart: M5 candles (100 bars before the check, 36 after), the planned entry and candidate band, the Go barrier that decided
the rule (red), the frozen Python barriers A / B that overlap the same price range (orange / purple outlines), and the live
width-gated zones the Go barrier was merged from (grey). Nothing is drawn from outcomes except the 36 bars after the line.

    cases.py --replay replay.jsonl --events events.jsonl --books-dir out/f7 --oracle-tag a1c77584 --out-dir cases \
        --testdata analysis-engine/testdata --per-class 3 --seed 11
Needs matplotlib.
"""
import argparse
import collections
import glob
import json
import os
import random

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

PIP = {"XAU": 0.1, "EURUSD": 1e-4, "GBPUSD": 1e-4, "USDJPY": 0.01, "GBPJPY": 0.01}
LIVE = {"fresh", "touched", "partially_mitigated"}


def blk(d):
  return d is not None and not d["allowed"]


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--replay", required=True)
  parser.add_argument("--events", required=True)
  parser.add_argument("--books-dir", required=True)
  parser.add_argument("--oracle-tag", default="a1c77584")
  parser.add_argument("--testdata", required=True)
  parser.add_argument("--out-dir", required=True)
  parser.add_argument("--per-class", type=int, default=3)
  parser.add_argument("--seed", type=int, default=11)
  args = parser.parse_args()
  os.makedirs(args.out_dir, exist_ok=True)
  events = {}
  for l in open(args.events):
    e = json.loads(l)
    events[e["match_id"] + "|" + str(e["ts"]) + "|" + e["kind"]] = e
  replay = [json.loads(l) for l in open(args.replay)]
  pop = [r for r in replay if "a" in r and "c" in r and "d_credible" in r and "b" in r]
  classes = {
    "K1_go_blocks_python_allows_label_no": lambda r: r["kind"] == "rejected" and blk(r["c"]) and not blk(r["a"]) and not blk(r["b"]) and not blk(r["d_credible"]),
    "K2_go_blocks_python_allows_label_yes": lambda r: r["kind"] == "rejected" and blk(r["c"]) and not blk(r["a"]) and not blk(r["b"]) and blk(r["d_credible"]),
    "K3_go_admitted_unmerged_would_block": lambda r: r["kind"] == "published" and not blk(r["c"]) and blk(r["d_unmerged"]),
    "K4_room_below_cost": lambda r: r["kind"] == "rejected" and r["logged_reason"] == "opposing_barrier_room_below_cost" and blk(r["c"]),
    "K5_all_books_block": lambda r: r["kind"] == "rejected" and blk(r["c"]) and blk(r["a"]) and blk(r["b"]),
  }
  rng = random.Random(args.seed)
  go, py = {}, {}
  for path in glob.glob(os.path.join(args.books_dir, "*.go.jsonl")):
    for line in open(path):
      rec = json.loads(line)
      k = (rec["symbol"], rec["bar"])
      if k not in go or rec["history_bars_m5"] > go[k]["history_bars_m5"]:
        go[k] = rec
  for path in glob.glob(os.path.join(args.books_dir, f"*.{args.oracle_tag}.jsonl")):
    for line in open(path):
      rec = json.loads(line)
      k = (rec["symbol"], rec["bar"])
      if k not in py or rec["history_bars_m5"] > py[k]["history_bars_m5"]:
        py[k] = rec
  candles = collections.defaultdict(dict)
  for path in glob.glob(os.path.join(args.testdata, "replay-*-production-capture-2026*.json")):
    raw = json.load(open(path))
    for t, o, h, l, c, *_ in raw["timeframes"]["M5"]:
      candles[raw["symbol"]][int(t)] = (o, h, l, c)
  index = []
  for name, test in classes.items():
    pool = sorted([r for r in pop if test(r)], key=lambda r: (r["ts"], r["match_id"]))
    chosen = rng.sample(pool, min(args.per_class, len(pool)))
    for n, r in enumerate(chosen, 1):
      ev = events[r["match_id"] + "|" + str(r["ts"]) + "|" + r["kind"]]
      sym, bar, pip = r["symbol"], r["bar"], PIP[r["symbol"]]
      times = sorted(t for t in candles[sym] if bar - 100 * 300 <= t <= bar + 36 * 300)
      fig, ax = plt.subplots(figsize=(11, 5.5))
      for i, t in enumerate(times):
        o, h, l, c = candles[sym][t]
        color = "#2a9d4f" if c >= o else "#d1495b"
        ax.plot([i, i], [l, h], color=color, lw=0.8)
        ax.add_patch(Rectangle((i - 0.3, min(o, c)), 0.6, max(abs(c - o), pip * 0.05), color=color))
      event_i = times.index(bar) if bar in times else 100
      ax.axvline(event_i + 1, color="#222", ls=":", lw=1)
      lo_view = min(candles[sym][t][2] for t in times)
      hi_view = max(candles[sym][t][1] for t in times)
      ax.axhspan(ev["entry_low"], ev["entry_high"], xmin=0.0, xmax=1.0, color="#3b82f6", alpha=0.12)
      ax.axhline(ev["planned_entry"], color="#3b82f6", lw=1.2)
      rec = go.get((sym, bar), {})
      for z in rec.get("raw_zones", []):
        if z["state"] in LIVE and z["kind"] in ("supply", "demand") and z["high"] >= lo_view and z["low"] <= hi_view:
          ax.add_patch(Rectangle((0, z["low"]), len(times), z["high"] - z["low"], fill=False, ec="#999", lw=0.5, alpha=0.7))
      cb = (r.get("c") or {}).get("barrier")
      if cb:
        ax.add_patch(Rectangle((0, cb["low"]), len(times), cb["high"] - cb["low"], color="#ef4444", alpha=0.18))
      for key, color, label in (("barriers_a", "#f97316", "A"), ("barriers_b", "#7c3aed", "B")):
        for b in (py.get((sym, bar)) or {}).get(key, []):
          if b["high"] >= lo_view and b["low"] <= hi_view:
            ax.add_patch(Rectangle((0, b["low"]), len(times), b["high"] - b["low"], fill=False, ec=color, lw=1.4, ls="--" if label == "B" else "-"))
      ax.set_ylim(lo_view - (hi_view - lo_view) * 0.03, hi_view + (hi_view - lo_view) * 0.03)
      ax.set_xlim(-1, len(times))
      w = (cb["high"] - cb["low"]) / pip if cb else 0
      ax.set_title(f"{name}  #{n}  {sym} {r['strategy']} {r['direction']}  {r['kind']}:{r['logged_reason']}\n"
                   f"Go={r['c']['reason']}  A={r['a']['reason']}  B={r['b']['reason']}  label={r['d_credible']['reason']}  Go barrier width={w:.0f} pips  entry={ev['planned_entry']}", fontsize=9)
      ax.set_xticks([])
      fname = f"{name}_{n}.png"
      fig.savefig(os.path.join(args.out_dir, fname), dpi=80, bbox_inches="tight")
      plt.close(fig)
      index.append({"file": fname, "class": name, "match_id": r["match_id"], "symbol": sym, "strategy": r["strategy"], "direction": r["direction"], "ts": r["ts"],
                    "kind": r["kind"], "logged_reason": r["logged_reason"], "go": r["c"]["reason"], "A": r["a"]["reason"], "B": r["b"]["reason"], "label": r["d_credible"]["reason"]})
  json.dump(index, open(os.path.join(args.out_dir, "index.json"), "w"), indent=1)
  print(json.dumps({"charts": len(index)}))


if __name__ == "__main__":
  main()

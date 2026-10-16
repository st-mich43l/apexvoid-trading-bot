"""Independent structural-credibility labels for the zones that make up a Go barrier, and the books built from them.

Run from ``algo-bot`` (it reuses the definition-based oracle of ``tools/ae_ground_truth.py``: plain textbook definitions over the
raw closed candles, no engine code or parameter). For every requested (symbol, M5 bar) the Go record's live, width-gated
zones are labelled:

* ``reacted``      the zone was followed by a move of >= 1.5 ATR within 8 bars (the reaction a supply/demand zone exists to mark)
* ``oracle_origin`` an oracle impulse origin of the same side, live by the oracle's rule at that bar, overlaps the zone (>= 30%
                   of the narrower width) - the "textbook base before an impulse"
* ``broken``       the oracle sees an unreclaimed close beyond the zone's far edge by > 0.5 ATR (price left and stayed away)
* ``credible``     reacted and oracle_origin and not broken

and three books are written for ``replay_admission.py --extra-books``:

* ``d_unmerged``   every live width-gated zone as its own barrier (no same-side union)
* ``d_credible``   only the credible zones, each as its own barrier  <- the labelled "genuine opposing structure" reference
* ``d_credible_merged`` the credible zones union-merged like barrier.Build, to isolate what merging alone does

and, for Model D design (Go-available facts only, no oracle label): ``c_merge_capped`` (barrier.Build's merge, refusing a union
wider than the execution width gate) and a grid ``g_t<max touches>_s<min strength>`` (zones filtered on their own lifecycle
facts, then merged like barrier.Build).

The labels use candles up to the bar's close only (no later bar), except ``reacted`` which by construction needs the 8 bars after
the zone's origin - always earlier than the check. Nothing here is a trading rule; ``d_credible`` is a measuring stick.

    cd algo-bot && python ../analysis-engine/test/f7barrier/credibility.py --events events.jsonl --books-dir out/f7 \
        --out-books out/f7/extra_books.jsonl --out-labels out/f7/credibility.jsonl
"""
import argparse
import bisect
import collections
import glob
import json
import os
import sys

PIP = {"XAU": 0.1, "EURUSD": 1e-4, "GBPUSD": 1e-4, "USDJPY": 0.01, "GBPJPY": 0.01}
LIVE = {"fresh", "touched", "partially_mitigated"}
MAX_ATR, MAX_PIPS = 2.0, 100.0
TF_SECONDS = {"M5": 300, "M15": 900, "H1": 3600}
BARS_BACK = {"M5": 700, "M15": 400, "H1": 250}


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--events", required=True)
  parser.add_argument("--books-dir", required=True)
  parser.add_argument("--testdata", default=os.path.join("..", "analysis-engine", "testdata"))
  parser.add_argument("--out-books", required=True)
  parser.add_argument("--out-labels", required=True)
  args = parser.parse_args()
  sys.path.insert(0, os.getcwd())
  from tools import ae_ground_truth as gt

  frames = collections.defaultdict(dict)  # symbol -> tf -> {time: row}
  for path in sorted(glob.glob(os.path.join(args.testdata, "replay-*-production-capture-2026*.json"))):
    raw = json.load(open(path))
    for tf, rows in raw["timeframes"].items():
      if tf in TF_SECONDS:
        slot = frames[raw["symbol"]].setdefault(tf, {})
        for r in rows:
          slot[int(r[0])] = [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])]
  ordered = {sym: {tf: sorted(rows.values()) for tf, rows in per.items()} for sym, per in frames.items()}
  times = {sym: {tf: [r[0] for r in rows] for tf, rows in per.items()} for sym, per in ordered.items()}

  go = {}
  for path in sorted(glob.glob(os.path.join(args.books_dir, "*.go.jsonl"))):
    for line in open(path):
      rec = json.loads(line)
      key = (rec["symbol"], rec["bar"])
      if key not in go or rec["history_bars_m5"] > go[key]["history_bars_m5"]:
        go[key] = rec
  wanted = sorted({(json.loads(l)["symbol"], json.loads(l)["bar"]) for l in open(args.events)} & set(go))

  def series_at(sym, tf, bar):
    close = bar + 300
    cut = bisect.bisect_right(times[sym][tf], close - TF_SECONDS[tf])
    rows = ordered[sym][tf][max(0, cut - BARS_BACK[tf]):cut]
    return gt.Series(rows) if len(rows) > 60 else None

  def fits(cur, z):
    """The execution width gate applied to the union, not only to its members."""
    high = max(cur["high"], z["high"])
    atr = max(cur["atr"], z["atr"])
    width = high - cur["low"]
    return width / atr <= MAX_ATR and width / PIP[cur["sym"]] <= MAX_PIPS

  def merge(rows, cap=False):
    """barrier.Build's same-side union-merge (touching or overlapping bands), minus tiers and cross-side reconciliation."""
    out = []
    for side in ("buy", "sell"):
      cur = None
      for z in sorted((r for r in rows if r["side"] == side), key=lambda r: (r["low"], r["high"])):
        if cur is not None and z["low"] <= cur["high"] and (not cap or fits(cur, z)):
          cur = {**cur, "high": max(cur["high"], z["high"]), "score": max(cur["score"], z["score"]), "touches": min(cur["touches"], z["touches"]), "atr": max(cur["atr"], z["atr"])}
        else:
          if cur is not None:
            out.append(cur)
          cur = dict(z)
      if cur is not None:
        out.append(cur)
    return out

  n_labels = collections.Counter()
  with open(args.out_books, "w") as books_out, open(args.out_labels, "w") as labels_out:
    for sym, bar in wanted:
      rec = go[(sym, bar)]
      pip = PIP[sym]
      series, impulses = {}, {}
      for tf in TF_SECONDS:
        s = series_at(sym, tf, bar)
        series[tf] = s
        impulses[tf] = [i for i in gt.oracle_impulses(s) if i["live"]] if s else []
      unmerged, credible = [], []
      for z in rec["raw_zones"]:
        if z["kind"] not in ("supply", "demand") or z["state"] not in LIVE:
          continue
        width = z["high"] - z["low"]
        if width <= 0 or z["atr"] <= 0 or width / z["atr"] > MAX_ATR or width / pip > MAX_PIPS:
          continue
        side = "buy" if z["kind"] == "demand" else "sell"
        row = {"side": side, "low": z["low"], "high": z["high"], "tier": "zone", "score": z["strength"], "touches": z["touches"], "atr": z["atr"], "sym": sym}
        unmerged.append(row)
        s = series[z["tf"]]
        reacted = origin = broken = None
        if s is not None and z["origin_time"] in s.index:
          i = s.index[z["origin_time"]]
          move = gt._reaction_move(s, i, z["kind"], z["high"], z["low"])
          reacted = None if move is None else move >= gt.REACTION_ATR
          edge = z["low"] if z["kind"] == "demand" else z["high"]
          broken = gt.unreclaimed_breach(s, i + 2, z["kind"], edge, 0.5, s.atr[-1])
          origin = any(
            imp["side"] == z["kind"] and min(z["high"], imp["high"]) - max(z["low"], imp["low"]) > 0
            and min(z["high"], imp["high"]) - max(z["low"], imp["low"]) >= 0.3 * min(width, imp["high"] - imp["low"])
            for imp in impulses[z["tf"]])
        is_credible = bool(reacted) and bool(origin) and not broken
        n_labels[(z["tf"], is_credible)] += 1
        labels_out.write(json.dumps({"symbol": sym, "bar": bar, "tf": z["tf"], "kind": z["kind"], "low": z["low"], "high": z["high"],
                                     "state": z["state"], "touches": z["touches"], "strength": z["strength"], "origin_time": z["origin_time"],
                                     "reacted": reacted, "oracle_origin": origin, "broken": broken, "credible": is_credible},
                                    separators=(",", ":")) + "\n")
        if is_credible:
          credible.append(row)
      books = {"d_unmerged": unmerged, "d_credible": credible, "d_credible_merged": merge(credible), "c_merge_capped": merge(unmerged, cap=True)}
      for max_touches in (0, 1, 2, 3, 99):
        for min_strength in (0.0, 0.5, 0.65, 0.8):
          books[f"g_t{max_touches}_s{min_strength}"] = merge([z for z in unmerged if z["touches"] <= max_touches and z["score"] >= min_strength])
      books_out.write(json.dumps({"symbol": sym, "bar": bar, "books": books}, separators=(",", ":")) + "\n")
  print(json.dumps({"bars": len(wanted), "zones_labelled": {f"{tf}|credible={c}": v for (tf, c), v in sorted(n_labels.items())}}))


if __name__ == "__main__":
  main()

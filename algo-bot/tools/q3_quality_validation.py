"""Q3: does anything a strategy knows at the decision predict a better outcome than a matched random entry would have had?

Reuses the replay records and the matched random counterparts of ``q1_opportunity_quality`` (nothing here simulates a trade):

    cd analysis-engine
    AEQ_DUMP=/tmp/aeq        go test ./test/aequality -run TestReplayAllStrategies
    AEQ_RANDOM_OUT=/tmp/aeq  go test ./test/aequality -run TestMatchedRandomBaseline
    cd ../algo-bot && python tools/q3_quality_validation.py --records /tmp/aeq/records.jsonl --random /tmp/aeq/random.jsonl \
        --out q3.json

Outcome measure. For each thinned, filled opportunity: its hypothetical R (the real ladder, with costs) minus the mean R of the matched random entries
(same instrument, direction, technical risk, cost, ladder and 24 h horizon). The difference removes the instrument, the stop size and the drift of the
period, which is what a raw R comparison between quality groups cannot. Groups are strategy x (XAU | FX); symbols are not mixed with XAU.

A split (a fact known at the decision, high against low) is *supported* only if all of these hold: both sides have at least ``MIN_SIDE`` observations in
BOTH windows; the development difference exceeds the Bonferroni-adjusted bootstrap bound for every split tried; the out-of-sample difference has the same
sign and at least half the size and its own 95% interval does not lie on the other side of zero. Nothing is supported by a small sample: it is reported
as insufficient.
"""
import argparse
import collections
import json
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import q1_opportunity_quality as q1  # noqa: E402

MIN_SIDE = 25
REPS = 1500
SEED = 20261020
FX = ("EURUSD", "GBPUSD", "USDJPY", "GBPJPY")


def group_of(symbol):
  return "XAU" if symbol == "XAU" else "FX"


def observations(records, randoms, thin=True):
  """Filled, thinned observations with the outcome, the random control and every fact the record carries."""
  out = []
  for r in (q1.thin(records) if thin else records):
    sim = (r.get("sim") or {}).get("ladder_cost")
    rd = randoms.get(r["id"])
    if not sim or sim["result"] not in q1.FILLED or not rd:
      continue
    nocost = ((r.get("sim") or {}).get("ladder") or {}).get("r")
    f = r.get("factors") or {}
    comp = r.get("components") or {}
    atr_pips = (r["atr"] / q1.PIP[r["symbol"]]) if r.get("atr") else None
    row = {
      "id": r["id"], "strategy": r["strategy"], "symbol": r["symbol"], "group": group_of(r["symbol"]), "t": r["eval_time"], "direction": r["direction"],
      "r": sim["r"], "r_nocost": nocost, "rand": rd["mean_r"], "diff": sim["r"] - rd["mean_r"], "result": sim["result"],
      "mfe": sim.get("mfe"), "mae": sim.get("mae"), "stars": r.get("stars"), "quality": r.get("quality"),
      "cluster": (r["symbol"], r["eval_time"] // 86400),
      "facts": {k: (1.0 if v is True else 0.0 if v is False else v) for k, v in f.items() if isinstance(v, (bool, int, float))},
      "components": {k: v for k, v in comp.items() if isinstance(v, (int, float))},
      "room_cover": r.get("room_cover"), "tech_rr": r.get("tech_rr"), "pattern": r.get("reaction_pattern") or r.get("pattern"),
      "exec_risk_atr": (r["exec_risk_pips"] / atr_pips) if atr_pips and r.get("exec_risk_pips") else None,
      "entry_width_atr": (r["entry_width_pips"] / atr_pips) if atr_pips and r.get("entry_width_pips") is not None else None,
    }
    row["facts"].update(r.get("facts") or {})
    out.append(row)
  return out


def mean(xs):
  return sum(xs) / len(xs) if xs else None


def boot_difference(high, low, reps=REPS, seed=SEED):
  """Cluster bootstrap of mean(diff | high) - mean(diff | low) with symbol-day clusters; returns (estimate, se, 95% interval)."""
  clusters = collections.defaultdict(lambda: ([], []))
  for x in high:
    clusters[x["cluster"]][0].append(x["diff"])
  for x in low:
    clusters[x["cluster"]][1].append(x["diff"])
  keys = list(clusters)
  if len(keys) < 3 or not high or not low:
    return None
  rnd = random.Random(seed)
  est = mean([x["diff"] for x in high]) - mean([x["diff"] for x in low])
  draws = []
  for _ in range(reps):
    h, l = [], []
    for _k in keys:
      a, b = clusters[rnd.choice(keys)]
      h += a
      l += b
    if h and l:
      draws.append(mean(h) - mean(l))
  if len(draws) < reps // 2:
    return None
  draws.sort()
  return est, statistics.pstdev(draws), (draws[int(len(draws) * .025)], draws[int(len(draws) * .975)])


def summary(rows):
  """Count, expectancy, win rate, average win/loss, profit factor, excursions, cost drag and the difference to random, with a cluster interval."""
  n = len(rows)
  if n == 0:
    return {"n": 0}
  rs = [x["r"] for x in rows]
  wins, losses = [v for v in rs if v > 0], [v for v in rs if v < 0]
  clusters = collections.defaultdict(list)
  for x in rows:
    clusters[x["cluster"]].append(x["diff"])
  out = {"n": n, "mean_r": round(mean(rs), 3), "mean_random_r": round(mean([x["rand"] for x in rows]), 3),
         "mean_difference_r": round(mean([x["diff"] for x in rows]), 3), "ci95": q1.boot(clusters), "win_rate": round(len(wins) / n, 3),
         "avg_win_r": round(mean(wins), 3) if wins else None, "avg_loss_r": round(mean(losses), 3) if losses else None,
         "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and wins else None}
  nocost = [x["r_nocost"] for x in rows if x["r_nocost"] is not None]
  if nocost:
    out["cost_drag_r"] = round(mean(nocost) - mean(rs), 3)
  mfe, mae = [x["mfe"] for x in rows if x["mfe"] is not None], [x["mae"] for x in rows if x["mae"] is not None]
  if mfe:
    out["mean_mfe_r"], out["mean_mae_r"] = round(mean(mfe), 3), round(mean(mae), 3)
  return out


def windows(rows):
  return {"development": [x for x in rows if x["t"] < q1.CUT], "out_of_sample": [x for x in rows if x["t"] >= q1.CUT]}


def split_candidates(rows):
  """The facts known at the decision, each as a high/low split fixed in advance: a boolean as it is, a number at the median of its own group."""
  names = collections.Counter()
  for x in rows:
    for k in x["facts"]:
      names["fact:" + k] += 1
    for k in x["components"]:
      names["component:" + k] += 1
  splits = {}
  for name in names:
    kind, key = name.split(":", 1)
    source = (lambda x, key=key: x["facts"].get(key)) if kind == "fact" else (lambda x, key=key: x["components"].get(key))
    values = [source(x) for x in rows if source(x) is not None]
    if len(values) < 2 * MIN_SIDE or len(set(values)) < 2:
      continue
    if set(values) <= {0.0, 1.0}:
      splits[name] = (source, lambda v: v == 1.0, "yes vs no")
    else:
      cut = statistics.median(values)
      if min(values) == cut:  # a median at the minimum would put everything on one side
        above = [v for v in values if v > cut]
        if not above:
          continue
        cut = min(above) - 1e-12
      splits[name] = (source, lambda v, cut=cut: v > cut, f"> {cut:.4g}")
  for key, label in (("stars", "3 vs 2 stars"), ("quality", "quality above its median"), ("room_cover", "room cover >= 1"), ("exec_risk_atr", "executable risk above its median"),
                     ("entry_width_atr", "entry width above its median")):
    values = [x[key] for x in rows if x.get(key) is not None]
    if len(values) < 2 * MIN_SIDE or len(set(values)) < 2:
      continue
    cut = {"stars": 2.5, "room_cover": 0.9999}.get(key, statistics.median(values))
    splits["geometry:" + key] = ((lambda x, key=key: x.get(key)), (lambda v, cut=cut: v > cut), label)
  return splits


def evaluate_splits(rows, tests_total):
  """Every split of one group, development then out of sample, with the verdict of the rule in the module docstring."""
  out = []
  z = statistics.NormalDist().inv_cdf(1 - 0.025 / max(1, tests_total))
  win = windows(rows)
  for name, (source, is_high, label) in split_candidates(rows).items():
    entry = {"split": name, "high_means": label}
    sides = {}
    for w, sub in win.items():
      hi = [x for x in sub if source(x) is not None and is_high(source(x))]
      lo = [x for x in sub if source(x) is not None and not is_high(source(x))]
      sides[w] = (hi, lo)
      entry[w] = {"n_high": len(hi), "n_low": len(lo)}
      if len(hi) >= MIN_SIDE and len(lo) >= MIN_SIDE:
        b = boot_difference(hi, lo)
        if b:
          est, se, ci = b
          entry[w].update({"difference": round(est, 3), "ci95": [round(ci[0], 3), round(ci[1], 3)], "se": round(se, 3),
                           "mean_diff_high": round(mean([x["diff"] for x in hi]), 3), "mean_diff_low": round(mean([x["diff"] for x in lo]), 3)})
    d, o = entry["development"], entry["out_of_sample"]
    if "difference" not in d or "difference" not in o:
      entry["verdict"] = "insufficient"
    else:
      same = d["difference"] * o["difference"] > 0 and abs(o["difference"]) >= abs(d["difference"]) / 2
      opposed = (o["ci95"][1] < 0 < d["difference"]) or (o["ci95"][0] > 0 > d["difference"])
      strong = abs(d["difference"]) > z * d["se"]
      entry["verdict"] = "supported" if strong and same and not opposed else "not supported"
    out.append(entry)
  return out


def pooled_stars(rows):
  """The one pre-specified pooled question: across strategies, is a 3-star opportunity better than a 2-star one of the SAME strategy and instrument group?
  The difference-to-random measure already removes the instrument and stop size; each group contributes its own 3-star and 2-star means, weighted by its
  3-star count (the stratified difference), so a strategy that publishes mostly 3 stars cannot stand in for the others."""
  groups = collections.defaultdict(list)
  for x in rows:
    if x["stars"] in (2, 3):
      groups[(x["strategy"], x["group"])].append(x)
  out = {}
  for window in ("development", "out_of_sample"):
    per, hi_all, lo_all = [], [], []
    for key, sub in groups.items():
      hi = [x for x in windows(sub)[window] if x["stars"] == 3]
      lo = [x for x in windows(sub)[window] if x["stars"] == 2]
      if len(hi) >= 5 and len(lo) >= 5:
        per.append({"group": f"{key[0]}|{key[1]}", "n_3": len(hi), "n_2": len(lo), "difference": round(mean([x["diff"] for x in hi]) - mean([x["diff"] for x in lo]), 3)})
        hi_all += hi
        lo_all += lo
    if not per:
      continue
    # stratified: re-centre each side on its group's 2-star mean so a 3-star is compared with its own strategy's 2-star
    base = {}
    for key, sub in groups.items():
      lo = [x["diff"] for x in windows(sub)[window] if x["stars"] == 2]
      base[key] = mean(lo) if lo else 0.0
    for x in hi_all:
      x["_c"] = x["diff"] - base[(x["strategy"], x["group"])]
    zero = [dict(x, diff=0.0) for x in lo_all]
    shifted = [dict(x, diff=x["_c"]) for x in hi_all]
    b = boot_difference(shifted, zero)
    out[window] = {"groups": per, "observations_3": len(hi_all), "observations_2": len(lo_all),
                   "stratified_difference": round(b[0], 3) if b else None, "ci95": [round(b[2][0], 3), round(b[2][1], 3)] if b else None}
  return out


def group_report(rows, tests_total=None):
  report = {"all": summary(rows), "development": summary(windows(rows)["development"]), "out_of_sample": summary(windows(rows)["out_of_sample"])}
  report["by_stars"] = {str(s): {w: summary([x for x in sub if x["stars"] == s]) for w, sub in windows(rows).items()} for s in sorted({x["stars"] for x in rows if x["stars"]})}
  return report


def run(records, randoms):
  rows = observations(records, randoms)
  groups = collections.defaultdict(list)
  for x in rows:
    groups[(x["strategy"], x["group"])].append(x)
  testable = {k: v for k, v in groups.items() if min(len(windows(v)["development"]), len(windows(v)["out_of_sample"])) >= 2 * MIN_SIDE}
  # The multiple-comparison count is the number of splits actually tried across every testable group.
  tried = sum(len(split_candidates(v)) for v in testable.values())
  out = {"observations": len(rows), "min_side": MIN_SIDE, "splits_tried": tried, "pooled_stars": pooled_stars(rows), "groups": {}, "insufficient_groups": {}}
  for key, v in sorted(groups.items()):
    name = f"{key[0]}|{key[1]}"
    if key not in testable:
      out["insufficient_groups"][name] = {w: len(sub) for w, sub in windows(v).items()}
      continue
    rep = group_report(v)
    splits = evaluate_splits(v, tried)
    rep["splits_supported"] = [s for s in splits if s["verdict"] == "supported"]
    rep["splits_tested"] = len(splits)
    rep["splits"] = splits
    out["groups"][name] = rep
  return out


def retained_vs_removed(before, after, randoms, strategy):
  """Q2 changed what a strategy admits. Were the opportunities it stopped publishing technically inferior (worse than random entries) and the retained ones stronger?
  ``before`` is a replay under the old admission rule, ``after`` under the current one; an opportunity is retained when its id is still published."""
  kept = {r["id"] for r in after if r["strategy"] == strategy}
  rows = observations([r for r in before if r["strategy"] == strategy], randoms)
  out = {}
  for g in ("XAU", "FX"):
    sub = [x for x in rows if x["group"] == g]
    removed, retained = [x for x in sub if x["id"] not in kept], [x for x in sub if x["id"] in kept]
    entry = {"removed": {"all": summary(removed), **{w: summary(v) for w, v in windows(removed).items()}},
             "retained": {"all": summary(retained), **{w: summary(v) for w, v in windows(retained).items()}}}
    for w in ("development", "out_of_sample"):
      hi, lo = windows(retained)[w], windows(removed)[w]
      b = boot_difference(hi, lo) if len(hi) >= MIN_SIDE and len(lo) >= MIN_SIDE else None
      entry.setdefault("retained_minus_removed", {})[w] = {"difference": round(b[0], 3), "ci95": [round(b[2][0], 3), round(b[2][1], 3)]} if b else "insufficient"
    # What the removed ones lacked: the share of each fact among the removed and the retained.
    facts = collections.defaultdict(lambda: [0, 0, 0, 0])
    for x in sub:
      side = 0 if x["id"] in kept else 1
      for k, v in x["facts"].items():
        if k in ("htf_aligned", "session", "wick", "displacement", "structural", "fib", "choch"):
          facts[k][side * 2] += 1
          facts[k][side * 2 + 1] += 1 if v == 1.0 else 0
    entry["fact_share"] = {k: {"retained": round(v[1] / v[0], 3) if v[0] else None, "removed": round(v[3] / v[2], 3) if v[2] else None} for k, v in facts.items()}
    out[g] = entry
  return out


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--records", required=True)
  ap.add_argument("--random", required=True)
  ap.add_argument("--extra-random", default="", help="a second random.jsonl for records the first does not cover")
  ap.add_argument("--before", default="", help="records of the replay under the previous admission rule (Key Level retained/removed)")
  ap.add_argument("--out", required=True)
  a = ap.parse_args()
  records = q1.load_jsonl(a.records)
  randoms = {r["id"]: r for r in q1.load_jsonl(a.random)}
  if a.extra_random:
    for r in q1.load_jsonl(a.extra_random):
      randoms.setdefault(r["id"], r)
  result = run(records, randoms)
  if a.before:
    before = q1.load_jsonl(a.before)
    result["retained_vs_removed"] = {name: retained_vs_removed(before, records, randoms, name) for name in ("key_level", "ifvg", "crt", "trendline")}
  json.dump(result, open(a.out, "w"), indent=1)
  print(json.dumps({"observations": result["observations"], "splits_tried": result["splits_tried"], "groups": list(result["groups"]), "insufficient": result["insufficient_groups"]}, indent=1))


if __name__ == "__main__":
  main()

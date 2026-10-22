"""Q4: does the strategy-specific technical quality score separate setups better than the stars it replaces?

Reuses the Q3 machinery (``q3_quality_validation.observations``: thinned, filled opportunities with their hypothetical R, the matched random
control and the published facts) and compares, on the SAME opportunities, the score of the previous model (``--before``: Overall = stars / 3 for most
strategies) with the score of the new one (``--after``).

The evaluation rule is fixed here, before any outcome is looked at, and is run once:

  * Unit: a filled, thinned opportunity of one strategy and one instrument group (XAU or FX). Outcome: its R minus the mean R of its matched random
    entries (same instrument, direction, risk, cost, ladder, horizon).
  * Windows: development before 2026-10-07 UTC, out of sample after. Nothing is fitted on either window; the only threshold, the median of the score,
    is taken from the development window of the group and applied to both.
  * Separation: high score (above the development median; for a score that takes one or two values, the top value) against the rest, as the difference of
    mean outcomes with a symbol-day cluster-bootstrap interval, per group and window; and Spearman correlation between score and outcome.
  * Pooled: the groups are stratified (each high-score outcome is centred on its own group's low-score mean), so a strategy that scores high everywhere
    cannot stand in for the others.
  * Ready for live ranking only if ALL hold: the pooled out-of-sample separation is positive with an interval excluding zero; it has the same sign in
    development; at least half of the testable groups have a positive out-of-sample separation; and the new score separates better than the old one in the
    pooled out-of-sample estimate. Otherwise the score is informational.

    python q4_quality_eval.py --before records_old.jsonl --after records_new.jsonl --random random_new.jsonl --out q4.json
"""
import argparse
import collections
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import q1_opportunity_quality as q1  # noqa: E402
import q3_quality_validation as q3  # noqa: E402

MIN_SIDE = 25


def ranks(values):
  order = sorted(range(len(values)), key=lambda i: values[i])
  out = [0.0] * len(values)
  i = 0
  while i < len(order):
    j = i
    while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
      j += 1
    for k in range(i, j + 1):
      out[order[k]] = (i + j) / 2 + 1
    i = j + 1
  return out


def spearman(xs, ys):
  """Rank correlation with average ranks for ties; None when either side is constant or there are fewer than three pairs."""
  if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
    return None
  rx, ry = ranks(xs), ranks(ys)
  mx, my = statistics.fmean(rx), statistics.fmean(ry)
  num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
  den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
  return num / den if den else None


def split(rows, score, cut):
  """High = above the cut; when that leaves nothing (the cut is the maximum, a score with one or two values) the top value is high."""
  high = [r for r in rows if score(r) > cut]
  if not high:
    top = max((score(r) for r in rows), default=None)
    high = [r for r in rows if top is not None and score(r) == top and score(r) > min(score(x) for x in rows)]
  ids = {id(r) for r in high}
  return high, [r for r in rows if id(r) not in ids]


def development_cut(rows, score):
  dev = [score(r) for r in rows if r["t"] < q1.CUT]
  return statistics.median(dev) if dev else None


def distribution(values):
  if not values:
    return {"n": 0}
  ordered = sorted(values)
  q = lambda p: ordered[min(len(ordered) - 1, int(p * len(ordered)))]
  modal = collections.Counter(round(v, 6) for v in values).most_common(1)[0][1] / len(values)
  return {"n": len(values), "distinct": len({round(v, 6) for v in values}), "mean": round(statistics.fmean(values), 3),
          "sd": round(statistics.pstdev(values), 3), "p10": round(q(.1), 3), "p50": round(q(.5), 3), "p90": round(q(.9), 3), "share_at_modal_value": round(modal, 3)}


def separation(rows, score, cut):
  """Mean outcome of the high-score rows minus the rest, with its interval, and the rank correlation, per window."""
  out = {}
  for window, sub in q3.windows(rows).items():
    entry = {"n": len(sub), "spearman": None}
    s = spearman([score(r) for r in sub], [r["diff"] for r in sub])
    entry["spearman"] = None if s is None else round(s, 3)
    if cut is not None and len(sub) >= 2 * MIN_SIDE:
      high, low = split(sub, score, cut)
      entry.update({"n_high": len(high), "n_low": len(low)})
      if len(high) >= MIN_SIDE and len(low) >= MIN_SIDE:
        b = q3.boot_difference(high, low)
        if b:
          entry.update({"separation": round(b[0], 3), "ci95": [round(b[2][0], 3), round(b[2][1], 3)],
                        "mean_diff_high": round(statistics.fmean(r["diff"] for r in high), 3), "mean_diff_low": round(statistics.fmean(r["diff"] for r in low), 3),
                        "win_rate_high": round(sum(r["r"] > 0 for r in high) / len(high), 3), "win_rate_low": round(sum(r["r"] > 0 for r in low) / len(low), 3)})
    out[window] = entry
  return out


def pooled(groups, score_of, cut_of):
  """Stratified high-minus-low across groups, per window: each high row is centred on its own group's low mean."""
  out = {}
  for window in ("development", "out_of_sample"):
    shifted, zero, used = [], [], []
    for key, rows in groups.items():
      sub = q3.windows(rows)[window]
      cut = cut_of[key]
      if cut is None or len(sub) < 2 * MIN_SIDE:
        continue
      high, low = split(sub, score_of, cut)
      if len(high) < MIN_SIDE or len(low) < MIN_SIDE:
        continue
      base = statistics.fmean(r["diff"] for r in low)
      shifted += [dict(r, diff=r["diff"] - base) for r in high]
      zero += [dict(r, diff=0.0) for r in low]
      used.append(key)
    b = q3.boot_difference(shifted, zero) if shifted and zero else None
    out[window] = {"groups": used, "n_high": len(shifted), "n_low": len(zero),
                   "separation": None if not b else round(b[0], 3), "ci95": None if not b else [round(b[2][0], 3), round(b[2][1], 3)]}
  return out


# Zone strategies also publish resting observations (no reaction yet): they are not executable and are scored on their one measurable
# dimension, so they are not comparable with confirmed reactions and are left out of the evaluation.
RESTING_CAPABLE = {"supply", "demand", "order_block", "fvg", "confluence_zone"}


def confirmed(records):
  return [r for r in records if r["strategy"] not in RESTING_CAPABLE or r.get("has_reaction")]


def join(before_records, after_records, randoms):
  """Observations of the after set (confirmed opportunities only) with the old score attached by opportunity id."""
  old = {r["id"]: r for r in before_records}
  rows = q3.observations(confirmed(after_records), randoms, thin=True)
  for r in rows:
    prior = old.get(r["id"])
    r["old_quality"] = None if prior is None else prior.get("quality")
    r["old_stars"] = None if prior is None else prior.get("stars")
  return [r for r in rows if r["old_quality"] is not None]


def dimensions(rows):
  names = sorted({k for r in rows for k in r["components"] if k.startswith("score_")})
  return names


def correlation_matrix(rows, names):
  out, worst = {}, 0.0
  for i, a in enumerate(names):
    for b in names[i + 1:]:
      pairs = [(r["components"][a], r["components"][b]) for r in rows if a in r["components"] and b in r["components"]]
      c = spearman([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 20 else None
      if c is not None:
        out[f"{a}|{b}"] = round(c, 2)
        worst = max(worst, abs(c))
  return out, round(worst, 2)


def evaluate(before_records, after_records, randoms):
  rows = join(before_records, after_records, randoms)
  groups = collections.defaultdict(list)
  for r in rows:
    groups[(r["strategy"], r["group"])].append(r)
  report = {"observations": len(rows), "groups": {}}
  new_score, old_score = (lambda r: r["quality"]), (lambda r: r["old_quality"])
  cuts_new = {k: development_cut(v, new_score) for k, v in groups.items()}
  cuts_old = {k: development_cut(v, old_score) for k, v in groups.items()}
  for key, sub in sorted(groups.items()):
    name = f"{key[0]}|{key[1]}"
    dims = dimensions(sub)
    entry = {"n": len(sub), "new_score": distribution([r["quality"] for r in sub]), "old_score": distribution([r["old_quality"] for r in sub]),
             "spearman_new_vs_old": None if spearman([r["quality"] for r in sub], [r["old_quality"] for r in sub]) is None else round(spearman([r["quality"] for r in sub], [r["old_quality"] for r in sub]), 3),
             "new": separation(sub, new_score, cuts_new[key]), "old": separation(sub, old_score, cuts_old[key]),
             "dimensions": {d: distribution([r["components"][d] for r in sub if d in r["components"]]) for d in dims}}
    entry["dimension_correlations"], entry["max_abs_dimension_correlation"] = correlation_matrix(sub, dims)
    entry["dimension_vs_outcome"] = {d: {w: (None if spearman([r["components"].get(d, 0.0) for r in s], [r["diff"] for r in s]) is None
                                             else round(spearman([r["components"].get(d, 0.0) for r in s], [r["diff"] for r in s]), 3)) for w, s in q3.windows(sub).items()} for d in dims}
    report["groups"][name] = entry
  report["pooled"] = {"new": pooled(groups, new_score, cuts_new), "old": pooled(groups, old_score, cuts_old)}
  report["ready_for_live_ranking"] = readiness(report)
  return report


def readiness(report):
  new = report["pooled"]["new"]
  old = report["pooled"]["old"]
  oos, dev = new["out_of_sample"], new["development"]
  reasons = []
  if oos["separation"] is None or oos["ci95"] is None or oos["ci95"][0] <= 0:
    reasons.append("pooled out-of-sample separation is not positive with an interval above zero")
  if dev["separation"] is None or dev["separation"] <= 0:
    reasons.append("pooled development separation is not positive")
  testable = [g for g in report["groups"].values() if g["new"]["out_of_sample"].get("separation") is not None]
  positive = [g for g in testable if g["new"]["out_of_sample"]["separation"] > 0]
  if not testable or len(positive) * 2 < len(testable):
    reasons.append(f"only {len(positive)} of {len(testable)} testable groups separate positively out of sample")
  if old["out_of_sample"]["separation"] is not None and (oos["separation"] is None or oos["separation"] <= old["out_of_sample"]["separation"]):
    reasons.append("the new score does not separate better than the stars it replaces")
  return {"ready": not reasons, "reasons": reasons}


def main():
  ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  ap.add_argument("--before", required=True, help="records.jsonl of the replay under the previous quality model")
  ap.add_argument("--after", required=True, help="records.jsonl of the replay under the new quality model")
  ap.add_argument("--random", required=True, help="random.jsonl matched to the after set")
  ap.add_argument("--out", required=True)
  a = ap.parse_args()
  randoms = {r["id"]: r for r in q1.load_jsonl(a.random)}
  report = evaluate(q1.load_jsonl(a.before), q1.load_jsonl(a.after), randoms)
  json.dump(report, open(a.out, "w"), indent=1)
  print(json.dumps({"observations": report["observations"], "pooled": report["pooled"], "ready": report["ready_for_live_ranking"]}, indent=1))


if __name__ == "__main__":
  main()

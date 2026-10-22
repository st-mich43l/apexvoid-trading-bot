"""Q5.3: does the frozen model-v1 technical quality score separate outcomes on bars it has never been evaluated on?

Q4 froze the scoring rule and evaluated it once on captures that ended 2026-10-14. This runs the SAME rule, the same cuts and the same
evaluation unit (``q4_quality_eval``: a filled, thinned, confirmed opportunity of one strategy and one XAU|FX group; outcome = its hypothetical R minus
the mean R of its matched random entries) on a window that did not exist when the rule was frozen:

  all           the three together (the cut still comes from development; nothing is refitted)

  development   before 2026-10-07            (Q4's, unchanged: it is where each group's cut, the median score, is taken)
  out_of_sample 2026-10-07 .. 2026-10-18     (Q4's, plus the later part of the week of 10-13 that the new captures add)
  forward       from 2026-10-19 00:00 UTC    (the first full week after the freeze; the captures hold the weekend gap, so it opens on Monday with
                                              more than three trading days of history behind the first bar)

Nothing is fitted on any window. The evaluation rule is fixed here, before the forward outcomes were read: the cut of a group is its development median; a
group is testable only with at least ``MIN_SIDE`` opportunities on each side; the pooled estimate centres every group on its own low-score mean.

It adds what the earlier evaluations did not report: the standard error and the smallest separation a window could have resolved (``mde80``: 2.8 standard
errors, 80% power at the 5% level), so that "no separation" can be told apart from "no power to see one"; per-dimension saturation; and the executable
risk against the technical risk behind the ``score_room`` dimension.

    python q53_quality_forward.py --records records.jsonl --random random.jsonl --out q53.json
"""
import argparse
import collections
import datetime as dt
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import q1_opportunity_quality as q1  # noqa: E402
import q3_quality_validation as q3  # noqa: E402
import q4_quality_eval as q4  # noqa: E402

FORWARD = int(dt.datetime(2026, 10, 19, tzinfo=dt.timezone.utc).timestamp())
MIN_SIDE = q4.MIN_SIDE
WINDOWS = ("development", "out_of_sample", "forward", "all")


def window_of(t):
  return "development" if t < q1.CUT else "out_of_sample" if t < FORWARD else "forward"


def windows(rows):
  out = {w: [] for w in WINDOWS}
  for r in rows:
    out[window_of(r["t"])].append(r)
    out["all"].append(r)
  return out


def separation(rows, score, cut):
  """Per window: high-minus-low mean outcome, its cluster interval and standard error, the smallest effect the window could resolve, Spearman."""
  out = {}
  for window, sub in windows(rows).items():
    entry = {"n": len(sub), "spearman": None}
    s = q4.spearman([score(r) for r in sub], [r["diff"] for r in sub])
    entry["spearman"] = None if s is None else round(s, 3)
    if cut is not None and len(sub) >= 2 * MIN_SIDE:
      high, low = q4.split(sub, score, cut)
      entry.update({"n_high": len(high), "n_low": len(low)})
      if len(high) >= MIN_SIDE and len(low) >= MIN_SIDE:
        b = q3.boot_difference(high, low)
        if b:
          entry.update({"separation": round(b[0], 3), "se": round(b[1], 3), "ci95": [round(b[2][0], 3), round(b[2][1], 3)], "mde80": round(2.8 * b[1], 3),
                        "mean_diff_high": round(statistics.fmean(r["diff"] for r in high), 3), "mean_diff_low": round(statistics.fmean(r["diff"] for r in low), 3)})
    out[window] = entry
  return out


def pooled(groups, score_of, cut_of):
  out = {}
  for window in WINDOWS:
    shifted, zero, used = [], [], []
    for key, rows in groups.items():
      sub = windows(rows)[window]
      cut = cut_of[key]
      if cut is None or len(sub) < 2 * MIN_SIDE:
        continue
      high, low = q4.split(sub, score_of, cut)
      if len(high) < MIN_SIDE or len(low) < MIN_SIDE:
        continue
      base = statistics.fmean(r["diff"] for r in low)
      shifted += [dict(r, diff=r["diff"] - base) for r in high]
      zero += [dict(r, diff=0.0) for r in low]
      used.append("|".join(key))
    b = q3.boot_difference(shifted, zero) if shifted and zero else None
    out[window] = {"groups": used, "n_high": len(shifted), "n_low": len(zero), "separation": None if not b else round(b[0], 3),
                   "se": None if not b else round(b[1], 3), "ci95": None if not b else [round(b[2][0], 3), round(b[2][1], 3)],
                   "mde80": None if not b else round(2.8 * b[1], 3)}
  return out


def saturation(values):
  """How informative a dimension is: share at 0, at 1, modal share, distinct values, standard deviation."""
  n = len(values)
  if not n:
    return {"n": 0}
  rounded = [round(v, 6) for v in values]
  modal = collections.Counter(rounded).most_common(1)[0][1] / n
  return {"n": n, "at_0": round(sum(v <= 1e-9 for v in values) / n, 3), "at_1": round(sum(v >= 1 - 1e-9 for v in values) / n, 3),
          "modal_share": round(modal, 3), "distinct": len(set(rounded)), "sd": round(statistics.pstdev(values), 3)}


def risk_audit(rows):
  """The score's room dimension reads technical risk; the executor's risk is the invalidation widened to the strategy's floor."""
  out = {}
  by = collections.defaultdict(list)
  for r in rows:
    comp = r["components"]
    if "score_room" in comp and r.get("tech_rr") and r.get("room_cover") is not None:
      by[(r["strategy"], r["group"])].append(r)
  for key, sub in sorted(by.items()):
    tech, cover = [r["tech_rr"] for r in sub], [r["room_cover"] for r in sub]
    exec_room = [q_room(c) for c in cover]
    tech_room = [r["components"]["score_room"] for r in sub]
    widened = [c / t for c, t in zip(cover, tech) if t > 0 and c is not None]
    out["|".join(key)] = {
      "n": len(sub),
      "technical_rr_median": round(statistics.median(tech), 2), "executable_cover_median": round(statistics.median(cover), 2),
      "executable_over_technical_median": round(statistics.median(widened), 2) if widened else None,
      "score_room_mean": round(statistics.fmean(tech_room), 3), "score_room_on_executable_risk_mean": round(statistics.fmean(exec_room), 3),
      "share_room_positive": round(sum(v > 0 for v in tech_room) / len(sub), 3),
      "share_room_positive_but_executable_cover_below_1": round(sum(1 for v, c in zip(tech_room, cover) if v > 0 and c < 1) / len(sub), 3),
      "share_first_target_inside_executable_risk": round(sum(c < 1 for c in cover) / len(sub), 3),
    }
  return out


def q_room(cover):
  """score_room if the same ramp (1R scoring 0, 3R scoring 1) were read on the executable risk: the first technical target over the widened stop."""
  return 0.0 if cover is None or not math.isfinite(cover) else max(0.0, min(1.0, (cover - 1.0) / 2.0))


def dimension_effects(groups):
  """Pooled high-minus-low outcome of each dimension on its own, over all windows, in the groups that publish it.

  Diagnostic for the question "which part of the score carries the sign"; it selects nothing (the equal-weight rule does not change with it).
  """
  names = sorted({d for rows in groups.values() for r in rows for d in r["components"] if d.startswith("score_")})
  out = {}
  for d in names:
    sub_groups = {k: [r for r in rows if d in r["components"]] for k, rows in groups.items()}
    sub_groups = {k: v for k, v in sub_groups.items() if len(v) >= 2 * MIN_SIDE}
    if not sub_groups:
      continue
    score_of = lambda r, d=d: r["components"][d]
    cuts = {k: q4.development_cut(v, score_of) for k, v in sub_groups.items()}
    got = pooled(sub_groups, score_of, cuts)["all"]
    out[d] = {"groups": len(got["groups"]), "separation": got["separation"], "ci95": got["ci95"]}
  return out


def evaluate(records, randoms):
  rows = q3.observations(q4.confirmed(records), randoms, thin=True)
  groups = collections.defaultdict(list)
  for r in rows:
    groups[(r["strategy"], r["group"])].append(r)
  score = lambda r: r["quality"]
  cuts = {k: q4.development_cut(v, score) for k, v in groups.items()}
  report = {"observations": len(rows), "window_counts": {w: len(v) for w, v in windows(rows).items() if w != "all"}, "groups": {}, "forward_start": FORWARD}
  for key, sub in sorted(groups.items()):
    dims = q4.dimensions(sub)
    entry = {"n": len(sub), "by_window": {w: len(v) for w, v in windows(sub).items()}, "score": q4.distribution([r["quality"] for r in sub]),
             "separation": separation(sub, score, cuts[key]), "cut": None if cuts[key] is None else round(cuts[key], 3),
             "dimension_saturation": {d: saturation([r["components"][d] for r in sub if d in r["components"]]) for d in dims},
             "dimension_vs_outcome": {d: {w: (None if q4.spearman([r["components"].get(d, 0.0) for r in s], [r["diff"] for r in s]) is None
                                              else round(q4.spearman([r["components"].get(d, 0.0) for r in s], [r["diff"] for r in s]), 3))
                                          for w, s in windows(sub).items()} for d in dims}}
    report["groups"]["|".join(key)] = entry
  report["pooled"] = pooled(groups, score, cuts)
  report["risk_audit"] = risk_audit(rows)
  report["dimension_effects_all_windows"] = dimension_effects(groups)
  # Whole-score outcome by fixed score band within each window (no cut fitted): the shape, not a test
  bands = {}
  for w, sub in windows(rows).items():
    if w == "all":
      continue
    edges = ((0, 0.4), (0.4, 0.55), (0.55, 0.7), (0.7, 1.01))
    bands[w] = {f"{a:.2f}-{min(b, 1):.2f}": {"n": len(s), "mean_diff": None if not s else round(statistics.fmean(r["diff"] for r in s), 3),
                                              "mean_r": None if not s else round(statistics.fmean(r["r"] for r in s), 3)}
                for a, b in edges for s in [[r for r in sub if a <= r["quality"] < b]]}
  report["score_bands"] = bands
  return report


def main():
  ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  ap.add_argument("--records", required=True)
  ap.add_argument("--random", required=True)
  ap.add_argument("--out", required=True)
  a = ap.parse_args()
  randoms = {r["id"]: r for r in q1.load_jsonl(a.random)}
  report = evaluate(q1.load_jsonl(a.records), randoms)
  json.dump(report, open(a.out, "w"), indent=1)
  print(json.dumps({"observations": report["observations"], "window_counts": report["window_counts"], "pooled": report["pooled"]}, indent=1))


if __name__ == "__main__":
  main()

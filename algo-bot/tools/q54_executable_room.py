"""Q5.4: does the room in front of an admitted plan, read in R of the stop the executor really uses, separate outcomes?

Offline and read-only. Two kinds of evidence are kept apart and never pooled:

  * REPLAY (counterfactual): every opportunity the Go replay publishes (``analysis-engine/test/aequality``), admitted by the worker's own
    room decision (``analysis-engine/test/f7barrier/decide_dump.py``) and the global two-star floor, then entered and exited by the
    replay's simulator with the real ladder and costs. Outcomes are hypothetical, never broker P&L.
  * REALIZED: closed algo_auto groups with their fills and creation envelope, R from ``result_accounting.estimate`` with its method.

The unit of the replay is Q3's: filled, confirmed, one per symbol, strategy, direction and UTC hour; the baseline is the matched random entry.
A candidate rule is evaluated as removed against kept trades: the difference of their mean R, a symbol-day cluster bootstrap interval and the
smallest effect the sample could resolve (mde80 = 2.8 standard errors). Nothing here selects a threshold.

    python q54_executable_room.py --records records.jsonl --decisions decisions.jsonl --random random.jsonl --out q54.json [--groups groups.jsonl]
"""
import argparse
import collections
import datetime as dt
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from app.autotrade import result_accounting as accounting  # noqa: E402
import q1_opportunity_quality as q1  # noqa: E402
import q3_quality_validation as q3  # noqa: E402

FX = ("EURUSD", "GBPUSD", "USDJPY", "GBPJPY")
ROOM_BUCKETS = ((0.0, 0.5, "<0.5"), (0.5, 1.0, "0.5-1"), (1.0, 1.5, "1-1.5"), (1.5, 2.0, "1.5-2"), (2.0, float("inf"), ">2"))
WIDENING_BUCKETS = ((0.0, 1.0001, "1.0 (floor not binding)"), (1.0001, 1.5, "1-1.5"), (1.5, 2.5, "1.5-2.5"), (2.5, float("inf"), ">2.5"))
# Strategies the Q2 purge removed (live from 2026-10-20 08:10Z): their closed groups say nothing about the book that runs now.
PURGED = frozenset("range_edge session_level liquidity_sweep range_sweep box_breakout snap_back momentum_ride scalp_breakout_retest".split())
REPS = 1500
SEED = 54
WINDOWS = (("development (<2026-10-07)", None, "2026-10-07"), ("out of sample (10-07..10-18)", "2026-10-07", "2026-10-19"),
           ("forward (>=2026-10-19)", "2026-10-19", None))

# Candidate rules, fixed BEFORE they were evaluated. Each is a predicate on an observation; none is an approved gate.
RULES = {
  "A: room to the nearest buffered barrier < 1R (TP1 beyond it)": lambda o: o["room_r"] is not None and o["room_r"] < 1.0,
  "B: Go's first technical target < 1R of the executable stop": lambda o: o["first_target_r"] < 1.0,
  "C: room to the nearest buffered barrier < 0.5R": lambda o: o["room_r"] is not None and o["room_r"] < 0.5,
  "D: stop widened to the floor by more than 2.5x": lambda o: o["widening"] is not None and o["widening"] > 2.5,
}


def jsonl(path):
  return [json.loads(line) for line in open(path) if line.startswith("{")]


def bucket(value, buckets):
  if value is None:
    return None
  for lo, hi, name in buckets:
    if lo <= value < hi:
      return name
  return None


def first_decisions(rows):
  """The worker's decision on the first bar each opportunity appeared on (a thesis repeats on later bars)."""
  first = {}
  for row in rows:
    key = row["id"]
    if key not in first or row["bar"] < first[key]["bar"]:
      first[key] = row
  return first


def admitted(records, decisions, min_stars=2):
  """What the worker would have admitted: reaction-confirmed (adapter), room decision allowed, and the global confluence floor.

  A record with no decision is a scalp that owns its room (``bypasses_opposing_structure_gates``) or has no ATR: the barrier book says
  nothing about it, so it is left out rather than guessed.
  """
  out = []
  for record in records:
    decision = decisions.get(record["id"])
    if not decision or not decision["admissible"] or not decision["allowed"]:
      continue
    if (record.get("stars") or 0) < min_stars:
      continue
    out.append(record)
  return out


def attach_geometry(observations, records_by_id, decisions):
  """Executable geometry of each observation, from the record and the worker's own room decision."""
  for obs in observations:
    record, decision = records_by_id[obs["id"]], decisions[obs["id"]]
    executable, technical = record["exec_risk_pips"], record["tech_risk_pips"]
    usable = decision.get("usable_room_pips")
    obs.update(
      exec_risk=executable, tech_risk=technical,
      widening=(executable / technical) if technical else None,
      first_target_r=(record["tech_reward_pips"] / executable) if executable else None,
      room_r=(usable / executable) if usable is not None and executable else None,
      decision_reason=decision["reason"],
    )
  return observations


def cluster_bootstrap(values_by_cluster, reps=REPS, seed=SEED):
  keys = list(values_by_cluster)
  if len(keys) < 3:
    return None
  rnd = random.Random(seed)
  draws = []
  for _ in range(reps):
    pool = [v for k in (rnd.choice(keys) for _ in keys) for v in values_by_cluster[k]]
    draws.append(sum(pool) / len(pool))
  draws.sort()
  return [round(draws[int(reps * .025)], 3), round(draws[int(reps * .975)], 3)]


def max_drawdown(items):
  total = peak = worst = 0.0
  for item in sorted(items, key=lambda x: x["t"]):
    total += item["r"]
    peak = max(peak, total)
    worst = min(worst, total - peak)
  return round(worst, 1)


def summary(items):
  if not items:
    return {"n": 0}
  values = [x["r"] for x in items]
  gains, losses = sum(v for v in values if v > 0), -sum(v for v in values if v < 0)
  by_r, by_diff = collections.defaultdict(list), collections.defaultdict(list)
  for x in items:
    by_r[x["cluster"]].append(x["r"])
    if "diff" in x:
      by_diff[x["cluster"]].append(x["diff"])
  out = {"n": len(values), "mean_r": round(statistics.mean(values), 3), "mean_r_ci": cluster_bootstrap(by_r),
         "win_rate": round(sum(v > 0 for v in values) / len(values), 3), "profit_factor": round(gains / losses, 2) if losses else None,
         "sum_r": round(sum(values), 1), "max_drawdown_r": max_drawdown(items), "symbol_days": len(by_r)}
  if all("diff" in x for x in items):
    out["above_random"] = round(statistics.mean(x["diff"] for x in items), 3)
    out["above_random_ci"] = cluster_bootstrap(by_diff)
  return out


def difference(removed, kept, key="r", reps=REPS, seed=SEED):
  """mean(key | removed) - mean(key | kept) with a symbol-day cluster bootstrap and the smallest effect the sample could resolve."""
  clusters = collections.defaultdict(lambda: ([], []))
  for x in removed:
    clusters[x["cluster"]][0].append(x[key])
  for x in kept:
    clusters[x["cluster"]][1].append(x[key])
  keys = list(clusters)
  if len(keys) < 3 or not removed or not kept:
    return None
  rnd = random.Random(seed)
  draws = []
  for _ in range(reps):
    a, b = [], []
    for _k in keys:
      u, v = clusters[rnd.choice(keys)]
      a += u
      b += v
    if a and b:
      draws.append(statistics.mean(a) - statistics.mean(b))
  draws.sort()
  estimate = statistics.mean(x[key] for x in removed) - statistics.mean(x[key] for x in kept)
  return {"estimate": round(estimate, 3), "ci": [round(draws[int(len(draws) * .025)], 3), round(draws[int(len(draws) * .975)], 3)],
          "mde80": round(2.8 * statistics.pstdev(draws), 3)}


def evaluate_rule(items, predicate, min_side=25):
  removed, kept = [x for x in items if predicate(x)], [x for x in items if not predicate(x)]
  if len(removed) < min_side or len(kept) < min_side:
    return {"n": len(items), "removed": len(removed), "kept": len(kept), "evaluated": False}
  return {"n": len(items), "evaluated": True, "admission_rate": round(len(kept) / len(items), 3),
          "removed": len(removed), "removed_winners": sum(x["r"] > 0 for x in removed), "removed_losers": sum(x["r"] <= 0 for x in removed),
          "removed_sum_r": round(sum(x["r"] for x in removed), 1), "removed_mean_r": round(statistics.mean(x["r"] for x in removed), 3),
          "kept": len(kept), "kept_mean_r": round(statistics.mean(x["r"] for x in kept), 3),
          "removed_minus_kept_r": difference(removed, kept, "r"), "removed_minus_kept_vs_random": difference(removed, kept, "diff"),
          "max_drawdown_all_r": max_drawdown(items), "max_drawdown_kept_r": max_drawdown(kept)}


def in_window(obs, start, end):
  def epoch(day):
    return dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
  return (start is None or obs["t"] >= epoch(start)) and (end is None or obs["t"] < epoch(end))


def replay_report(records, decision_rows, randoms):
  decisions = first_decisions(decision_rows)
  by_id = {r["id"]: r for r in records}
  admit = admitted(records, decisions)
  observations = attach_geometry(q3.observations(admit, randoms, thin=True), by_id, decisions)
  report = {"records": len(records), "decided": len(decisions), "admitted": len(admit), "observations": len(observations), "by_group": {}}
  for group in ("XAU", "FX"):
    items = [o for o in observations if o["group"] == group]
    block = {"all": summary(items), "room_r": {}, "first_target_r": {}, "widening": {}, "rules": {}, "rules_by_window": {}, "rules_by_instrument": {}}
    for _, _, name in ROOM_BUCKETS:
      block["room_r"][name] = summary([o for o in items if bucket(o["room_r"], ROOM_BUCKETS) == name])
      block["first_target_r"][name] = summary([o for o in items if bucket(o["first_target_r"], ROOM_BUCKETS) == name])
    block["room_r"]["no_barrier"] = summary([o for o in items if o["room_r"] is None])
    for _, _, name in WIDENING_BUCKETS:
      block["widening"][name] = summary([o for o in items if bucket(o["widening"], WIDENING_BUCKETS) == name])
    for name, predicate in RULES.items():
      block["rules"][name] = evaluate_rule(items, predicate)
      block["rules_by_window"][name] = {label: evaluate_rule([o for o in items if in_window(o, a, b)], predicate) for label, a, b in WINDOWS}
      block["rules_by_instrument"][name] = {s: evaluate_rule([o for o in items if o["symbol"] == s], predicate) for s in sorted({o["symbol"] for o in items})}
    report["by_group"][group] = block
  return report, observations


def realized_geometry(group, pip_sizes=q1.PIP):
  """Technical against executable geometry of one closed group, from its fills and creation envelope. None when it cannot be measured."""
  payload = (group.get("env") or {}).get("payload") or {}
  fills = group.get("fills") or []
  volume = sum(f["vol"] for f in fills)
  executable = group.get("stop_pips")
  if not payload or volume <= 0 or not executable or executable <= 0:
    return None
  pip = pip_sizes[group["symbol"]]
  entry = sum(f["entry"] * f["vol"] for f in fills) / volume
  sign = 1 if group["direction"] == "BUY" else -1
  technical = sign * (entry - payload["invalidation"]["price"]) / pip
  target = ((payload.get("targets") or [{}])[0].get("price") or {}).get("price")
  if target is None or technical <= 0:
    return None
  first_target = sign * (target - entry) / pip
  if first_target <= 0:
    return None
  return {"entry": entry, "technical_risk_pips": technical, "executable_risk_pips": executable, "widening": executable / technical,
          "first_target_pips": first_target, "first_target_r": first_target / executable, "technical_rr": first_target / technical}


def realized_report(groups):
  """Closed groups by the technical first target read in R of the executable stop, per result method (estimates are never merged with
  measurements: ``legacy_loss_unbooked`` assumes -1 for a loser, ``reconstructed_ladder_v1`` rebuilds the ladder)."""
  rows, purged = [], 0
  for g in groups:
    if ((g.get("env") or {}).get("payload") or {}).get("strategy") in PURGED:
      purged += 1
      continue
    geometry = realized_geometry(g)
    if geometry is None:
      continue
    est, method = accounting.estimate({
      "exit_path": g["exit_path"], "realized_pips": g["realized_pips"], "risk_pips": g["risk_pips"], "symbol": g["symbol"],
      "booked_tp_count": g["booked_tp_count"], "result_pips": g["result_pips"], "stop_pips": g["stop_pips"], "closed_at": g["closed_at"],
      "net_r": g.get("net_r"), "result_method": g.get("result_method"), "weighted_risk_pips": g.get("weighted_risk_pips")})
    if est is None:
      continue
    day = dt.datetime.fromtimestamp(g["closed_at"], dt.timezone.utc).date()
    rows.append({**geometry, "r": est, "method": method, "symbol": g["symbol"], "group": "XAU" if g["symbol"] == "XAU" else "FX",
                 "strategy": g["env"]["payload"]["strategy"], "t": g["closed_at"], "cluster": (g["symbol"], day.isoformat())})
  report = {"groups": len(rows), "excluded_purged_strategy": purged, "methods": dict(collections.Counter(r["method"] for r in rows)), "by_group": {}}
  for group in ("XAU", "FX"):
    items = [r for r in rows if r["group"] == group]
    block = {"all": summary(items), "widening_median": round(statistics.median([r["widening"] for r in items]), 2) if items else None,
             "first_target_r": {name: summary([r for r in items if bucket(r["first_target_r"], ROOM_BUCKETS) == name]) for _, _, name in ROOM_BUCKETS},
             "by_method": {m: summary([r for r in items if r["method"] == m]) for m in sorted({r["method"] for r in items})}}
    block["first_target_r_measured_methods_only"] = {
      name: summary([r for r in items if r["method"] in ("fills_v1", "fill_record_risk") and bucket(r["first_target_r"], ROOM_BUCKETS) == name])
      for _, _, name in ROOM_BUCKETS}
    report["by_group"][group] = block
  return report


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--records", required=True)
  ap.add_argument("--decisions", required=True)
  ap.add_argument("--random", required=True)
  ap.add_argument("--groups", help="closed groups with fills and creation envelope (one JSON object per line)")
  ap.add_argument("--out", required=True)
  args = ap.parse_args()
  randoms = {r["id"]: r for r in jsonl(args.random)}
  report, _ = replay_report(jsonl(args.records), jsonl(args.decisions), randoms)
  out = {"replay_counterfactual": report}
  if args.groups:
    out["realized"] = realized_report(jsonl(args.groups))
  json.dump(out, open(args.out, "w"), indent=1, sort_keys=True)


if __name__ == "__main__":
  main()

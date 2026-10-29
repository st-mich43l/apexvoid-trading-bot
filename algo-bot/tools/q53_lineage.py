"""Q5.3: tie every closed auto group to the opportunity, the scoring rule and the configuration that produced it, then read the realized R.

Inputs are read-only exports (the queries are in `git show f02d62f:docs/strategies/q53-quality-expectancy.md`):

  --results  auto_trade_results, one JSON object per line (``select * ...``)
  --opps     the creation envelopes of those groups' opportunities (``analysis_opportunity_events``, payload plus ``config_fingerprint`` and
             ``produced_at``), one JSON object per line

The join is the group id ``v8:go_opp_<h>`` -> opportunity id ``opp_<h>``: one group, one opportunity, so a thesis that re-published under several ids
is never counted twice for one fill. The scoring rule is read off the envelope: a Q4 (model v1) score carries ``score_*`` components, every earlier
score does not, so the two are never mixed; ``quality_model_version`` names the rule from Q5.3 on. The result is ``result_accounting.estimate`` (never
rewritten): ``fills_v1`` is a measurement, every other method an estimate, and the mix is always reported.

    python q53_lineage.py --results results.jsonl --opps exec_opps.jsonl --out lineage.json
"""
import argparse
import collections
import datetime as dt
import json
import math
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from app.autotrade import result_accounting as accounting  # noqa: E402
import q4_quality_eval as q4  # noqa: E402

PIP = {"XAU": 0.1, "EURUSD": 1e-4, "GBPUSD": 1e-4, "USDJPY": 0.01, "GBPJPY": 0.01}
ORDER = {"strategy_purged_before": "range_edge session_level liquidity_sweep range_sweep box_breakout snap_back momentum_ride scalp_breakout_retest"}
PURGED = set(ORDER["strategy_purged_before"].split())
# Deployment boundaries of the scoring and accounting rules (UTC), from the image start times on the host and the merge times.
BOUNDARIES = {
  "q4_quality_v1": dt.datetime(2026, 10, 22, 6, 12, tzinfo=dt.timezone.utc),
  "q5_1_net_r": dt.datetime(2026, 10, 22, 10, 25, tzinfo=dt.timezone.utc),
  "q5_2_executable_room": dt.datetime(2026, 10, 22, 14, 35, tzinfo=dt.timezone.utc),
}
MIN_GROUP = 40


def jsonl(path):
  return [json.loads(line) for line in open(path) if line.startswith("{")]


def quality_format(payload):
  components = (payload.get("quality") or {}).get("components") or {}
  if "quality_model_version" in components:
    return "model_v%d" % int(components["quality_model_version"])
  return "model_v1" if any(k.startswith("score_") for k in components) else "legacy"


def bootstrap_spearman(rows, x, y, rounds=600, seed=53):
  """Spearman of x against y with a symbol-day cluster bootstrap interval."""
  point = q4.spearman([x(r) for r in rows], [y(r) for r in rows])
  if point is None:
    return None
  clusters = collections.defaultdict(list)
  for r in rows:
    clusters[r["cluster"]].append(r)
  keys = list(clusters)
  if len(keys) < 5:
    return {"rho": round(point, 3), "ci95": None}
  rnd = random.Random(seed)
  draws = []
  for _ in range(rounds):
    sample = [r for _ in keys for r in clusters[rnd.choice(keys)]]
    v = q4.spearman([x(r) for r in sample], [y(r) for r in sample])
    if v is not None:
      draws.append(v)
  draws.sort()
  return {"rho": round(point, 3), "ci95": [round(draws[int(.025 * len(draws))], 3), round(draws[int(.975 * len(draws))], 3)] if len(draws) > 50 else None}


def summarize(rows):
  rs = [r["r"] for r in rows]
  if not rs:
    return {"n": 0}
  wins, losses = [v for v in rs if v > 0], [v for v in rs if v < 0]
  clusters = collections.defaultdict(list)
  for r in rows:
    clusters[r["cluster"]].append(r["r"])
  keys = list(clusters)
  rnd = random.Random(53)
  draws = []
  for _ in range(600):
    sample = [v for _ in keys for v in clusters[rnd.choice(keys)]]
    if sample:
      draws.append(statistics.fmean(sample))
  draws.sort()
  return {"n": len(rs), "mean_r": round(statistics.fmean(rs), 3), "sum_r": round(sum(rs), 2),
          "ci95": [round(draws[int(.025 * len(draws))], 3), round(draws[int(.975 * len(draws))], 3)] if len(keys) >= 5 and draws else None,
          "profit_factor": round(sum(wins) / -sum(losses), 3) if wins and losses else None,
          "stop_out_share": round(sum(1 for r in rows if r["exit_path"] == "full_stop") / len(rows), 3),
          "exit_paths": dict(collections.Counter(r["exit_path"] for r in rows).most_common())}


def main():
  ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  ap.add_argument("--results", required=True)
  ap.add_argument("--opps", required=True)
  ap.add_argument("--out", required=True)
  a = ap.parse_args()
  envelopes = {d["id"]: d for d in jsonl(a.opps)}
  rows, missing = [], 0
  for res in jsonl(a.results):
    opp = envelopes.get(res["group_id"].replace("v8:go_opp_", "opp_"))
    if opp is None:
      missing += 1
      continue
    r, method = accounting.estimate(res)
    if r is None:
      continue
    symbol = res["symbol"]
    pip = PIP.get(symbol)
    direction = opp["direction"]
    proximal = opp["entry"]["high"] if direction == "BUY" else opp["entry"]["low"]
    tech_risk = abs(proximal - opp["invalidation"]["price"]) / pip if pip else None
    first_target = min((abs(t["price"]["price"] - proximal) for t in opp["targets"]), default=None)
    first_target_pips = None if first_target is None or not pip else first_target / pip
    stop = res.get("stop_pips")
    produced = int(opp.get("produced_at") or 0)
    comps = (opp.get("quality") or {}).get("components") or {}
    rows.append({
      "group_id": res["group_id"], "strategy": opp["strategy"], "symbol": symbol, "group": "XAU" if symbol == "XAU" else "FX", "direction": direction,
      "closed_at": res["closed_at"], "created_at": opp["created_at"], "produced_at": produced, "r": r, "method": method, "exit_path": res.get("exit_path"),
      "quality_format": quality_format(opp), "overall": (opp.get("quality") or {}).get("overall"),
      "stars": ((opp.get("technical_context") or {}).get("confluence") or {}).get("selected_stars"),
      "config_fingerprint": opp.get("config_fingerprint"), "components": comps,
      "tech_risk_pips": tech_risk, "stop_pips": stop, "widening": (stop / tech_risk) if stop and tech_risk else None,
      "first_target_pips": first_target_pips,
      "tech_rr": (first_target_pips / tech_risk) if first_target_pips and tech_risk else None,
      "exec_rr": (first_target_pips / stop) if first_target_pips and stop else None,
      "cluster": (symbol, res["closed_at"] // 86400), "purged": opp["strategy"] in PURGED,
    })
  report = {"closed_groups_with_a_result": len(rows), "closed_groups_without_envelope": missing, "boundaries_utc": {k: v.isoformat() for k, v in BOUNDARIES.items()}}
  report["by_quality_format"] = dict(collections.Counter(r["quality_format"] for r in rows))
  report["by_result_method"] = dict(collections.Counter(r["method"] for r in rows))
  report["by_config_fingerprint"] = {"distinct": len({r["config_fingerprint"] for r in rows}), "largest": collections.Counter(r["config_fingerprint"][:8] for r in rows if r["config_fingerprint"]).most_common(5)}
  boundary = int(BOUNDARIES["q4_quality_v1"].timestamp())
  report["format_agrees_with_publish_time"] = {
    "v1_scored_before_boundary": sum(1 for r in rows if r["quality_format"] != "legacy" and r["produced_at"] < boundary),
    "legacy_scored_after_boundary": sum(1 for r in rows if r["quality_format"] == "legacy" and r["produced_at"] >= boundary)}
  report["duplicate_opportunity_ids"] = len(rows) - len({r["group_id"] for r in rows})
  current = [r for r in rows if not r["purged"]]
  report["current_strategies"] = {"groups": len(current), "purged_groups": len(rows) - len(current)}
  report["realized_r"] = {"all": summarize(rows), "current_strategies": summarize(current)}
  by_method = collections.defaultdict(list)
  for r in current:
    by_method[r["method"]].append(r)
  report["realized_r"]["current_by_method"] = {m: summarize(v) for m, v in sorted(by_method.items())}
  groups = collections.defaultdict(list)
  for r in current:
    groups[(r["strategy"], r["group"])].append(r)
  report["by_strategy_group"] = {"|".join(k): summarize(v) for k, v in sorted(groups.items()) if len(v) >= 10}
  # Score against realized R, in the formats it was published in, within one strategy and instrument group, never across groups
  disc = {}
  for fmt in ("legacy", "model_v1"):
    for key, sub in sorted(groups.items()):
      sub = [r for r in sub if r["quality_format"] == fmt and r["overall"] is not None]
      if len(sub) < MIN_GROUP:
        continue
      disc[f"{fmt}|{key[0]}|{key[1]}"] = {"n": len(sub), "overall_vs_r": bootstrap_spearman(sub, lambda r: r["overall"], lambda r: r["r"]),
                                           "stars_vs_r": bootstrap_spearman(sub, lambda r: r["stars"] or 0, lambda r: r["r"]),
                                           "methods": dict(collections.Counter(r["method"] for r in sub))}
  report["score_vs_realized_r"] = disc
  v1 = [r for r in current if r["quality_format"] != "legacy"]
  report["model_v1_closed_groups"] = {"n": len(v1), "measured_fills_v1": sum(1 for r in v1 if r["method"] == "fills_v1"),
                                       "rows": [{"strategy": r["strategy"], "symbol": r["symbol"], "overall": round(r["overall"], 3), "r": round(r["r"], 3), "method": r["method"],
                                                 "closed_at": dt.datetime.fromtimestamp(r["closed_at"], dt.timezone.utc).strftime("%m-%d %H:%M")} for r in sorted(v1, key=lambda r: r["closed_at"])]}
  # Technical against executable risk of the trades that were actually taken
  widen = collections.defaultdict(list)
  for r in current:
    if r["widening"]:
      widen[(r["strategy"], r["group"])].append(r)
  report["technical_vs_executable_risk"] = {
    "|".join(k): {"n": len(v), "tech_risk_pips_median": round(statistics.median(x["tech_risk_pips"] for x in v), 1), "stop_pips_median": round(statistics.median(x["stop_pips"] for x in v), 1),
                  "widening_median": round(statistics.median(x["widening"] for x in v), 2),
                  "tech_rr_median": round(statistics.median(x["tech_rr"] for x in v if x["tech_rr"]), 2) if any(x["tech_rr"] for x in v) else None,
                  "exec_rr_median": round(statistics.median(x["exec_rr"] for x in v if x["exec_rr"]), 2) if any(x["exec_rr"] for x in v) else None,
                  "tech_rr_vs_r": bootstrap_spearman([x for x in v if x["tech_rr"]], lambda x: x["tech_rr"], lambda x: x["r"]) if len(v) >= MIN_GROUP else None,
                  "exec_rr_vs_r": bootstrap_spearman([x for x in v if x["exec_rr"]], lambda x: x["exec_rr"], lambda x: x["r"]) if len(v) >= MIN_GROUP else None}
    for k, v in sorted(widen.items()) if len(v) >= 10}
  # How many closed groups a high-against-low comparison needs before it could resolve a given separation (80% power, 5% level, clusters
  # ignored, so a lower bound): n per side = 2 * (2.8 * sd / effect) ** 2.
  sd = statistics.pstdev([r["r"] for r in current]) if len(current) > 1 else None
  report["sample_needed"] = None if not sd else {"sd_of_r": round(sd, 3),
                                                  "per_side_for_0.25r": math.ceil(2 * (2.8 * sd / 0.25) ** 2), "per_side_for_0.10r": math.ceil(2 * (2.8 * sd / 0.10) ** 2),
                                                  "closed_groups_per_day_current_strategies": round(len(current) / max(1, (max(r["closed_at"] for r in current) - min(r["closed_at"] for r in current)) / 86400), 1)}
  json.dump(report, open(a.out, "w"), indent=1)
  print(json.dumps({k: report[k] for k in ("closed_groups_with_a_result", "by_quality_format", "by_result_method", "format_agrees_with_publish_time", "current_strategies")}, indent=1))


if __name__ == "__main__":
  main()

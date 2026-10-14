"""Aggregate the all-strategy replay and the production trades into the AE quality results.

Reproduce (repository root, Python 3.12, the algo-bot requirements installed):

    cd analysis-engine && AEQ_DUMP=/tmp/aeq go test ./test/aequality -run TestReplayAllStrategies
    cd ../algo-bot && PYTHONPATH=. python -m tools.ae_quality_eval /tmp/aeq/records.jsonl \
        --trades /tmp/trades.csv --out ../docs/strategies/ae-quality-replay-results.json

``--trades`` is optional: it is a read-only export of ``auto_trade_results`` joined to the
opportunity ledger (the query is in docs/strategies/ae-quality-certification.md) and is not
committed. Without it the production section is omitted and the replay section is unchanged.

Everything simulated here is a *hypothetical opportunity outcome*: closed candles, causal fills,
assumed constant spreads, single-leg entry at the proximal edge, the instrument's stop envelope.
It is never broker P&L and never an expectancy claim. Intervals are seeded cluster bootstraps
(clusters are symbol and UTC day, because opportunities repeat the same thesis for hours).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SEED = 20261014
OOS_START = int(datetime(2026, 10, 7, tzinfo=timezone.utc).timestamp())
PIP = {"XAU": 0.1, "EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01, "GBPJPY": 0.01}
FILLED = ("target", "stop", "stop_be", "timeout")


# ---------------------------------------------------------------- config facts

def requires_reaction() -> set[str]:
  source = (REPO / "algo-bot/app/autotrade/go_opportunity_policy.py").read_text()
  return set(re.findall(r'ScopeProfile\("(\w+)",[^\n]*?requires_reaction=True', source))


def _find(node, key):
  if isinstance(node, dict):
    if key in node:
      return node[key]
    for value in node.values():
      found = _find(value, key)
      if found is not None:
        return found
  return None


def containment() -> dict[str, dict]:
  document = yaml.safe_load((REPO / "config/instruments.yml").read_text())
  instruments = document.get("instruments", document)
  out = {}
  for symbol in PIP:
    node = instruments.get(symbol, {})
    out[symbol] = {
      "observe_only_strategies": sorted(_find(node, "observe_only_strategies") or ()),
      "observe_only_structure_timeframes": sorted(_find(node, "observe_only_structure_timeframes") or ()),
    }
  return out


def strategy_config() -> dict[str, dict]:
  document = yaml.safe_load((REPO / "config/analysis.yml").read_text())
  strategies = document["analysis"]["strategies"]
  return {name: {"version": body.get("version"), "enabled": body.get("enabled"), "symbols": body.get("symbols")}
          for name, body in strategies.items()}


# ---------------------------------------------------------------- statistics

def spearman(xs: list[float], ys: list[float]) -> float | None:
  if len(xs) < 5 or len(set(xs)) < 2 or len(set(ys)) < 2:
    return None

  def ranks(values: list[float]) -> list[float]:
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

  rx, ry = ranks(xs), ranks(ys)
  mx, my = statistics.fmean(rx), statistics.fmean(ry)
  cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
  var = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
  return None if var == 0 else cov / var


def cluster_ci(items: list[tuple[str, float]], rng: random.Random, reps: int = 1500) -> tuple[float, float] | None:
  """95% interval of the mean, resampling whole clusters (a thesis repeats for hours)."""
  clusters: dict[str, list[float]] = defaultdict(list)
  for key, value in items:
    clusters[key].append(value)
  if len(clusters) < 3:
    return None
  groups = list(clusters.values())
  means = []
  for _ in range(reps):
    sample = [rng.choice(groups) for _ in groups]
    flat = [v for g in sample for v in g]
    means.append(statistics.fmean(flat))
  means.sort()
  return round(means[int(0.025 * reps)], 3), round(means[int(0.975 * reps)], 3)


def day_cluster(record: dict) -> str:
  return f"{record['symbol']}|{datetime.fromtimestamp(record['eval_time'], timezone.utc):%Y-%m-%d}"


def thin(records: list[dict]) -> list[dict]:
  """One opportunity per symbol, strategy, direction and hour: a stream of repeats is one thesis."""
  seen, out = set(), []
  for r in records:
    key = (r["symbol"], r["strategy"], r["direction"], r["eval_time"] // 3600)
    if key not in seen:
      seen.add(key)
      out.append(r)
  return out


def sim_summary(records: list[dict], name: str, rng: random.Random) -> dict:
  results = [r["sim"].get(name) for r in records if r["sim"].get(name)]
  filled = [(day_cluster(r), r["sim"][name]["r"]) for r in records if r["sim"].get(name, {}).get("result") in FILLED]
  values = [v for _, v in filled]
  out = {
    "n_opportunities": len(records), "n_filled": len(values),
    "unfilled": sum(1 for x in results if x["result"] == "unfilled"),
    "not_executable": sum(1 for x in results if x["result"] == "not_executable"),
    "censored": sum(1 for x in results if x["result"] == "censored"),
  }
  if values:
    wins = sum(1 for v in values if v > 0)
    gain = sum(v for v in values if v > 0)
    loss = -sum(v for v in values if v < 0)
    out.update({
      "mean_r": round(statistics.fmean(values), 3), "win_rate": round(wins / len(values), 3),
      "profit_factor": round(gain / loss, 2) if loss > 0 else None, "sum_r": round(sum(values), 1),
      "worst_r": round(min(values), 2), "ci95": cluster_ci(filled, rng),
    })
  return out


def quantile_table(records: list[dict], field: str, name: str) -> dict:
  """Hypothetical mean R by within-strategy tercile of a score."""
  filled = [r for r in records if r["sim"].get(name, {}).get("result") in FILLED]
  if len(filled) < 30 or len({r[field] for r in filled}) < 3:
    return {}
  ordered = sorted(filled, key=lambda r: r[field])
  third = len(ordered) // 3
  parts = {"low": ordered[:third], "mid": ordered[third:2 * third], "high": ordered[2 * third:]}
  return {k: {"n": len(v), "mean_r": round(statistics.fmean(r["sim"][name]["r"] for r in v), 3),
              "score_range": [round(min(r[field] for r in v), 3), round(max(r[field] for r in v), 3)]}
          for k, v in parts.items() if v}


# ---------------------------------------------------------------- replay section

def replay_section(records: list[dict], rng: random.Random) -> dict:
  needs_reaction = requires_reaction()
  contained = containment()
  config = strategy_config()
  by_strategy: dict[str, list[dict]] = defaultdict(list)
  resting = Counter()
  for r in records:
    if r["strategy"] in needs_reaction and not r["has_reaction"]:
      resting[r["strategy"]] += 1
      continue
    by_strategy[r["strategy"]].append(r)
  out = {}
  for strategy, items in sorted(by_strategy.items()):
    thinned = thin(items)
    executable = [r for r in items if r["executable"]]
    rr = [r["tech_rr"] for r in items if r["tech_rr"] > 0]
    cover = [r["room_cover"] for r in executable]
    quality = [r["quality"] for r in items]
    entry: dict = {
      "version": items[0]["version"], "configured_symbols": config.get(strategy, {}).get("symbols"),
      "unique_confirmed": len(items), "unique_resting_observations": resting[strategy],
      "thinned_episodes": len(thinned), "median_repeats": statistics.median(r["repeats"] for r in items),
      "invariant_violations": dict(Counter(v for r in items for v in r.get("violations") or [])),
      "stop_within_cap_share": round(len(executable) / len(items), 3),
      "median_entry_width_pips": round(statistics.median(r["entry_width_pips"] for r in items), 1),
      "median_technical_risk_pips": round(statistics.median(r["tech_risk_pips"] for r in items), 1),
      "median_technical_rr": round(statistics.median(rr), 2) if rr else None,
      "share_technical_rr_below_1": round(sum(1 for x in rr if x < 1) / len(rr), 3) if rr else None,
      "median_room_cover": round(statistics.median(cover), 2) if cover else None,
      "share_room_cover_below_1": round(sum(1 for x in cover if x < 1) / len(cover), 3) if cover else None,
      "quality": {"distinct_values_1pct": len({round(q, 2) for q in quality}), "min": round(min(quality), 3),
                  "median": round(statistics.median(quality), 3), "max": round(max(quality), 3)},
      "components": sorted({k for r in items for k in (r["components"] or {})}),
      "simulation": {}, "by_symbol": {},
    }
    for name in ("ladder_cost", "oneR_cost", "twoR_cost"):
      entry["simulation"][name] = {"all": sim_summary(thinned, name, rng),
                                   "dev": sim_summary([r for r in thinned if r["eval_time"] < OOS_START], name, rng),
                                   "oos": sim_summary([r for r in thinned if r["eval_time"] >= OOS_START], name, rng)}
    entry["simulation"]["ladder_no_cost"] = {"all": sim_summary(thinned, "ladder", rng)}
    for symbol in sorted({r["symbol"] for r in items}):
      sub = [r for r in thinned if r["symbol"] == symbol]
      entry["by_symbol"][symbol] = {
        "thinned": len(sub), "observe_only_here": strategy in contained[symbol]["observe_only_strategies"],
        "median_room_cover": round(statistics.median([r["room_cover"] for r in sub if r["executable"]] or [float("nan")]), 2),
        "median_technical_risk_pips": round(statistics.median(r["tech_risk_pips"] for r in sub), 1),
        "ladder_cost": sim_summary(sub, "ladder_cost", rng),
      }
    filled = [r for r in thinned if r["sim"].get("ladder_cost", {}).get("result") in FILLED]
    entry["quality_vs_hypothetical_r"] = {
      "spearman": None if not filled else spearman([r["quality"] for r in filled], [r["sim"]["ladder_cost"]["r"] for r in filled]),
      "spearman_v2_raw": None if not filled else spearman([r["v2_raw"] for r in filled], [r["sim"]["ladder_cost"]["r"] for r in filled]),
      "terciles_of_quality": quantile_table(thinned, "quality", "ladder_cost"),
      "terciles_of_v2_raw": quantile_table(thinned, "v2_raw", "ladder_cost"),
    }
    for key in ("spearman", "spearman_v2_raw"):
      if entry["quality_vs_hypothetical_r"][key] is not None:
        entry["quality_vs_hypothetical_r"][key] = round(entry["quality_vs_hypothetical_r"][key], 3)
    out[strategy] = entry
  return out


# ---------------------------------------------------------------- production trades

def corrected_r(row: dict) -> float | None:
  """Realized R of a trade closed under the new executor contract (from 2026-10-08).

  ``realized_pips`` is the volume-weighted pips of what the executor booked at targets plus any
  still-open volume; it is exactly 0.0 on every full stop (the stop-out loss is not added), so a
  stopped group is -1R (the legacy ``result_pips`` of those rows is -1.03 x its stop on average).
  """
  if not row["exit_path"] or row["realized_pips"] == "":
    return None
  if row["exit_path"] == "full_stop":
    return -1.0
  risk = float(row["risk_pips"]) if row["risk_pips"] else (float(row["stop_pips"]) if row["stop_pips"] else None)
  return float(row["realized_pips"]) / risk if risk else None


def trades_section(path: str, rng: random.Random) -> dict:
  rows = [r for r in csv.DictReader(open(path)) if r["trade_stream"] == "algo_auto"]
  out: dict = {"algo_auto_trades": len(rows), "first_close": min(int(r["closed_at"]) for r in rows),
               "with_new_contract_realized": sum(1 for r in rows if r["realized_pips"] != ""),
               "realized_pips_defect": {
                 "full_stop_rows": sum(1 for r in rows if r["exit_path"] == "full_stop"),
                 "full_stop_rows_with_realized_pips_exactly_0": sum(1 for r in rows if r["exit_path"] == "full_stop" and r["realized_pips"] != "" and float(r["realized_pips"]) == 0.0),
                 "full_stop_rows_with_realized_pips": sum(1 for r in rows if r["exit_path"] == "full_stop" and r["realized_pips"] != ""),
                 "legacy_result_over_stop_mean": round(statistics.fmean(float(r["result_pips"]) / float(r["stop_pips"]) for r in rows if r["exit_path"] == "full_stop" and r["stop_pips"] and float(r["stop_pips"]) > 0), 3),
               }, "by_strategy": {}, "by_symbol": {}}
  by: dict[str, list[dict]] = defaultdict(list)
  for r in rows:
    by[r["setup_type"]].append(r)
  def summarise(items: list[dict]) -> dict:
    paths = Counter(r["exit_path"] for r in items)
    rs = [(f"{r['symbol']}|{datetime.fromtimestamp(int(r['closed_at']), timezone.utc):%Y-%m-%d}", corrected_r(r)) for r in items]
    rs = [(k, v) for k, v in rs if v is not None]
    values = [v for _, v in rs]
    entry = {"trades": len(items), "exit_paths": dict(paths), "stop_rate": round(paths["full_stop"] / len(items), 3),
             "symbols": dict(Counter(r["symbol"] for r in items)), "r_trades": len(values)}
    if values:
      gain, loss = sum(v for v in values if v > 0), -sum(v for v in values if v < 0)
      entry.update({"mean_r": round(statistics.fmean(values), 3), "win_rate": round(sum(1 for v in values if v > 0) / len(values), 3),
                    "profit_factor": round(gain / loss, 2) if loss else None, "sum_r": round(sum(values), 1), "ci95": cluster_ci(rs, rng)})
    return entry

  for name, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
    out["by_strategy"][name] = summarise(items)
  by_id: dict[str, list[dict]] = defaultdict(list)
  for r in rows:
    if r["strategy"]:
      by_id[r["strategy"]].append(r)
  out["by_strategy_id"] = {sid: summarise(items) for sid, items in sorted(by_id.items())}
  allr = [(f"{r['symbol']}|{datetime.fromtimestamp(int(r['closed_at']), timezone.utc):%Y-%m-%d}", corrected_r(r)) for r in rows]
  allr = [(k, v) for k, v in allr if v is not None]
  out["pooled"] = {"r_trades": len(allr), "mean_r": round(statistics.fmean(v for _, v in allr), 3), "ci95": cluster_ci(allr, rng)}
  for symbol in sorted({r["symbol"] for r in rows}):
    sub = [(f"{r['symbol']}|{datetime.fromtimestamp(int(r['closed_at']), timezone.utc):%Y-%m-%d}", corrected_r(r)) for r in rows if r["symbol"] == symbol]
    sub = [(k, v) for k, v in sub if v is not None]
    if sub:
      out["by_symbol"][symbol] = {"r_trades": len(sub), "mean_r": round(statistics.fmean(v for _, v in sub), 3), "ci95": cluster_ci(sub, rng)}
  # Quality score against realized R inside the strategies that have enough trades.
  out["quality_vs_realized_r"] = {}
  for name in ("Range Edge Scalp", "Key Level", "Range Sweep Scalp"):
    pairs = [(float(r["q"]), corrected_r(r)) for r in by.get(name, []) if r["q"] and corrected_r(r) is not None]
    if len(pairs) >= 8:
      ordered = sorted(pairs)
      third = len(ordered) // 3
      out["quality_vs_realized_r"][name] = {
        "n": len(pairs), "spearman": None if spearman([p[0] for p in pairs], [p[1] for p in pairs]) is None else round(spearman([p[0] for p in pairs], [p[1] for p in pairs]), 3),
        "terciles": {k: {"n": len(v), "mean_r": round(statistics.fmean(x[1] for x in v), 3), "quality_range": [round(v[0][0], 3), round(v[-1][0], 3)]}
                     for k, v in {"low": ordered[:third], "mid": ordered[third:2 * third], "high": ordered[2 * third:]}.items() if v}}
  return out


SCALPS = {"Range Edge Scalp", "Fade Scalp", "Range Sweep Scalp", "Impulse Pullback Scalp", "Breakout Retest Scalp"}


def exit_path_bounds(rows: list[dict]) -> dict:
  """Bounds on the mean R of a scalp strategy from exit paths alone (1R/2R, 50/50, stop to break-even).

  full_stop -1; tp1_be +0.5; tp2_full +1.5; tp1_stop is the first half booked at +1R and the rest stopped
  anywhere between -1R and break-even, so it lies in [0, +0.5] (its midpoint, +0.25, feeds the interval);
  manual_close is excluded. This needs no realized_pips, so it covers every trade, including those closed
  before the new executor contract. The 95% interval is a seeded cluster bootstrap (symbol and UTC day).
  """
  value = {"full_stop": -1.0, "tp1_be": 0.5, "tp2_full": 1.5, "tp1_stop": 0.25}
  items = [(f"{r['symbol']}|{datetime.fromtimestamp(int(r['closed_at']), timezone.utc):%Y-%m-%d}", value[r["exit_path"]])
           for r in rows if r["exit_path"] in value]
  paths = Counter(r["exit_path"] for r in rows if r["exit_path"] and r["exit_path"] != "manual_close")
  n = sum(paths.values())
  if not n:
    return {}
  low = -1.0 * paths["full_stop"] + 0.5 * paths["tp1_be"] + 1.5 * paths["tp2_full"] + 0.0 * paths["tp1_stop"]
  high = low + 0.5 * paths["tp1_stop"]
  return {"trades": n, "paths": dict(paths), "mean_r_low": round(low / n, 3), "mean_r_high": round(high / n, 3),
          "mean_r_midpoint_ci95": cluster_ci(items, random.Random(SEED)), "full_stop_rate": round(paths["full_stop"] / n, 3)}


def range_sweep_forensics(rows: list[dict]) -> dict:
  """The Range Sweep production trades split by the revision that published them (target label)."""
  def width(r):
    pip = PIP.get(r["symbol"], 0.0001)
    return (float(r["ehigh"]) - float(r["elow"])) / pip if r["elow"] and r["ehigh"] else None
  out: dict = {"trades": len(rows), "quality_range": None, "by_target_label": {}}
  q = sorted(float(r["q"]) for r in rows if r["q"])
  if q:
    out["quality_range"] = [round(q[0], 3), round(q[-1], 3)]
  for label in sorted({r["t1label"] for r in rows}):
    group = [r for r in rows if r["t1label"] == label]
    ratios = []
    for r in group:
      try:
        ratios.append(abs(float(r["t1"]) - float(r["avg_entry"])) / PIP.get(r["symbol"], 0.0001) / float(r["stop_pips"]))
      except (ValueError, ZeroDivisionError):
        pass
    widths = [w for w in (width(r) for r in group) if w is not None]
    out["by_target_label"][label] = {
      "trades": len(group), "symbols": dict(Counter(r["symbol"] for r in group)),
      "exit_paths": dict(Counter(r["exit_path"] for r in group)),
      "published_target_over_executed_stop_median": round(statistics.median(ratios), 2) if ratios else None,
      "entry_band_pips_median": round(statistics.median(widths), 1) if widths else None,
      "first_close": min(int(r["closed_at"]) for r in group), "last_close": max(int(r["closed_at"]) for r in group)}
  out["exit_path_bounds"] = exit_path_bounds(rows)
  return out


def strategy_matrix(report: dict) -> dict:
  """Merge the reviewer's matrix with the measured replay and production numbers."""
  document = yaml.safe_load((REPO / "docs/strategies/ae-strategy-matrix.yml").read_text())
  config = strategy_config()
  by_id = report.get("production_trades", {}).get("by_strategy_id", {})
  matrix = {}
  for sid, row in document["strategies"].items():
    replay = report["replay"].get(sid, {})
    sim = replay.get("simulation", {}).get("ladder_cost", {})
    observe = sorted(sym for sym, c in report["containment"].items() if sid in c["observe_only_strategies"])
    prod = by_id.get(sid)
    matrix[sid] = {
      "strategy_id": sid, "version": config.get(sid, {}).get("version"), "enabled": config.get(sid, {}).get("enabled"),
      "enabled_instruments": config.get(sid, {}).get("symbols") or sorted(report["containment"]),
      "observe_only_instruments": observe,
      "technical_thesis": row["technical_thesis"], "validation_conditions": row["validation_conditions"],
      "quality_formula": row["quality_formula"], "confirmed_defects": row["confirmed_defects"],
      "real_trade_sample_size": None if prod is None else {"trades": prod["trades"], "r_known": prod["r_trades"]},
      "realized_r_evidence": None if prod is None or "mean_r" not in prod else {
        "mean_r": prod["mean_r"], "ci95": prod.get("ci95"), "win_rate": prod.get("win_rate"), "sum_r": prod.get("sum_r"),
        "stop_rate": prod["stop_rate"]},
      "replay_evidence": {"opportunities": sim.get("all", {}).get("n_opportunities"), "filled": sim.get("all", {}).get("n_filled"),
                          "mean_r": sim.get("all", {}).get("mean_r"), "ci95": sim.get("all", {}).get("ci95"),
                          "oos_mean_r": sim.get("oos", {}).get("mean_r")},
      "execution_compatibility": {"median_room_cover": replay.get("median_room_cover"),
                                  "share_room_cover_below_1": replay.get("share_room_cover_below_1"),
                                  "share_technical_rr_below_1": replay.get("share_technical_rr_below_1"),
                                  "median_technical_rr": replay.get("median_technical_rr")},
      "verdicts": row["verdict"], "independent_review_status": "pending", "prior_certification": row["prior_certification"],
      "final_recommendation": row["recommendation"], "recommendation_scope": row["scope"],
      "owner_approval_required": row["owner_approval_required"], "rationale": row["rationale"],
    }
  return matrix


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("records")
  parser.add_argument("--trades", default="")
  parser.add_argument("--out", default="")
  args = parser.parse_args()
  rng = random.Random(SEED)
  with open(args.records) as handle:
    records = [json.loads(line) for line in handle]
  records.sort(key=lambda r: (r["eval_time"], r["id"]))
  report = {
    "seed": SEED, "oos_starts_utc": "2026-10-07", "records": len(records),
    "captures": sorted({r["capture"] for r in records}),
    "window": [min(r["eval_time"] for r in records), max(r["eval_time"] for r in records)],
    "replay": replay_section(records, rng),
    "containment": containment(),
  }
  if args.trades:
    report["production_trades"] = trades_section(args.trades, rng)
    all_rows = [r for r in csv.DictReader(open(args.trades)) if r["trade_stream"] == "algo_auto"]
    report["production_trades"]["scalp_exit_path_bounds"] = {
      name: exit_path_bounds([r for r in all_rows if r["setup_type"] == name]) for name in sorted(SCALPS)}
    report["production_trades"]["range_sweep_forensics"] = range_sweep_forensics(
      [r for r in all_rows if r["setup_type"] == "Range Sweep Scalp"])
    report["strategy_matrix"] = strategy_matrix(report)
  text = json.dumps(report, indent=1, sort_keys=True)
  if args.out:
    Path(args.out).write_text(text + "\n")
  print(text[:2000])


if __name__ == "__main__":
  main()

"""Which measured components of an AE candidate carry information about its outcome?

Reproduce (repository root, Python 3.12, the algo-bot requirements installed):

    cd analysis-engine && AEQ_DUMP=/tmp/aeq go test ./test/aequality -run TestReplayAllStrategies
    cd ../algo-bot && PYTHONPATH=. python -m tools.ae_quality_calibration /tmp/aeq/records.jsonl \
        --out /tmp/ae-quality-calibration-results.json

Method. The records are the all-strategy replay of every committed production capture (candidates
with their published components, geometry and a closed-candle causal simulation, see
``ae_quality_eval``). One opportunity per symbol, strategy, direction and hour ("thinned": a thesis
that repeats for hours is one observation). The target is the hypothetical R of the ladder
simulation with costs; unfilled and non-executable opportunities have none and are excluded.

A feature is *supported* only if all of the following hold, decided on the development window
(before 2026-10-07) and then checked once on the out-of-sample window (from 2026-10-07):

* the day-cluster bootstrap 95% interval of its development Spearman excludes zero,
* the development |Spearman| exceeds the Bonferroni threshold for the number of features tried
  (the interval alone would pass about one feature in twenty by chance),
* the out-of-sample Spearman has the same sign and is not smaller than half the development one.

Cross-strategy comparability is the point of a quality score, so pooled tests use the
**within-strategy mid-rank percentile** of each feature: a strategy's own scale does not matter,
only where this candidate sits among that strategy's candidates. Strategy-specific components are
also tested inside their own strategy.

Nothing here is an expectancy claim: hypothetical outcomes, assumed spreads, two weeks.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

from tools.ae_quality_eval import (
  FILLED,
  OOS_START,
  PIP,
  cluster_ci,
  day_cluster,
  spearman,
  thin,
)

SEED = 20261015
TARGET = "ladder_cost"
MIN_N = 60


def features_of(record: dict) -> dict[str, float]:
  """Strategy-independent measured facts of one candidate. A fact that does not exist is absent."""
  out: dict[str, float] = {}
  pip = PIP[record["symbol"]]
  atr_pips = record["atr"] / pip if record.get("atr") else None
  if record.get("room_cover") and record["room_cover"] > 0:
    out["log_room_cover"] = math.log(record["room_cover"])
  if record.get("tech_rr") and record["tech_rr"] > 0:
    out["log_technical_rr"] = math.log(record["tech_rr"])
  if atr_pips:
    out["entry_width_atr"] = record["entry_width_pips"] / atr_pips
    out["technical_risk_atr"] = record["tech_risk_pips"] / atr_pips
    out["executable_risk_atr"] = record["exec_risk_pips"] / atr_pips
  if record["entry_width_pips"] > 0 and record["tech_risk_pips"] > 0:
    out["entry_width_over_risk"] = record["entry_width_pips"] / record["tech_risk_pips"]
  out["v2_raw"] = record["v2_raw"]
  out["stars"] = float(record["stars"])
  out["quality"] = record["quality"]
  out["repeats"] = float(record["repeats"])
  if record.get("formed_at"):
    out["age_hours"] = max(0.0, (record["eval_time"] - record["formed_at"]) / 3600)
  factors = record.get("factors") or {}
  for name in ("htf_aligned", "wick", "displacement", "session", "structural", "fib", "choch"):
    if name in factors:
      out[f"factor_{name}"] = 1.0 if factors[name] else 0.0
  for name in ("touches", "zone_quality", "raw_factor"):
    if isinstance(factors.get(name), (int, float)):
      out[f"factor_{name}"] = float(factors[name])
  return out


def components_of(record: dict) -> dict[str, float]:
  return {f"component_{k}": v for k, v in (record.get("components") or {}).items()
          if isinstance(v, (int, float)) and math.isfinite(v)}


def _midrank_percentiles(values: list[float]) -> list[float]:
  order = sorted(range(len(values)), key=lambda i: values[i])
  out = [0.0] * len(values)
  i = 0
  while i < len(order):
    j = i
    while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
      j += 1
    for k in range(i, j + 1):
      out[order[k]] = ((i + j) / 2 + 0.5) / len(values)
    i = j + 1
  return out


def within_strategy_percentiles(rows: list[dict], name: str, *, by_symbol: bool = False) -> dict[int, float]:
  """Mid-rank percentile of feature ``name`` inside each strategy (by row index); with
  ``by_symbol`` inside each strategy and symbol, which removes differences between instruments."""
  by_strategy: dict[tuple, list[int]] = defaultdict(list)
  for index, row in enumerate(rows):
    if name in row["f"]:
      by_strategy[(row["strategy"], row["symbol"] if by_symbol else "")].append(index)
  out: dict[int, float] = {}
  for indexes in by_strategy.values():
    if len({rows[i]["f"][name] for i in indexes}) < 2:
      continue  # a constant carries no ordering
    for i, p in zip(indexes, _midrank_percentiles([rows[i]["f"][name] for i in indexes])):
      out[i] = p
  return out


def bootstrap_spearman(pairs: list[tuple[str, float, float]], rng: random.Random, reps: int = 1000) -> tuple[float, float] | None:
  """95% interval of Spearman, resampling whole symbol-days."""
  clusters: dict[str, list[tuple[float, float]]] = defaultdict(list)
  for key, x, y in pairs:
    clusters[key].append((x, y))
  if len(clusters) < 5:
    return None
  groups = list(clusters.values())
  values = []
  for _ in range(reps):
    flat = [p for g in (rng.choice(groups) for _ in groups) for p in g]
    rho = spearman([p[0] for p in flat], [p[1] for p in flat])
    if rho is not None:
      values.append(rho)
  if len(values) < reps // 2:
    return None
  values.sort()
  return round(values[int(0.025 * len(values))], 3), round(values[int(0.975 * len(values))], 3)


def bonferroni_threshold(n: int, tests: int) -> float:
  """|rho| that a two-sided test at family-wise 5% needs (normal approximation, Fisher z)."""
  if n < 10 or tests < 1:
    return 1.0
  alpha = 0.05 / tests
  # inverse normal by bisection on the survival function
  low, high = 0.0, 10.0
  for _ in range(60):
    mid = (low + high) / 2
    if math.erfc(mid / math.sqrt(2)) > alpha:
      low = mid
    else:
      high = mid
  return round(math.tanh(high / math.sqrt(n - 3)), 3)


def evaluate_feature(rows: list[dict], name: str, rng: random.Random, tests: int, *, pooled: bool = True, by_symbol: bool = False) -> dict:
  percentile = within_strategy_percentiles(rows, name, by_symbol=by_symbol) if pooled else {}
  result: dict = {"feature": name}
  for period, keep in (("dev", lambda r: r["eval_time"] < OOS_START), ("oos", lambda r: r["eval_time"] >= OOS_START)):
    pairs = []
    for index, row in enumerate(rows):
      if not keep(row):
        continue
      x = percentile.get(index) if pooled else row["f"].get(name)
      if x is None:
        continue
      pairs.append((day_cluster(row), x, row["r"]))
    rho = spearman([p[1] for p in pairs], [p[2] for p in pairs])
    result[period] = {"n": len(pairs), "spearman": None if rho is None else round(rho, 3)}
    if period == "dev":
      result[period]["ci95"] = bootstrap_spearman(pairs, rng) if rho is not None else None
  dev, oos = result["dev"], result["oos"]
  threshold = bonferroni_threshold(dev["n"], tests)
  result["bonferroni_abs_rho"] = threshold
  ok = (
    dev["spearman"] is not None and oos["spearman"] is not None and dev["ci95"] is not None
    and (dev["ci95"][0] > 0 or dev["ci95"][1] < 0)
    and abs(dev["spearman"]) >= threshold
    and dev["spearman"] * oos["spearman"] > 0
    and abs(oos["spearman"]) >= abs(dev["spearman"]) / 2
  )
  result["supported"] = bool(ok)
  return result


def composite(rows: list[dict], names_signs: list[tuple[str, int]]) -> dict[int, float]:
  """Equal-weight mean of signed within-strategy percentiles. No weight is fitted."""
  parts = []
  for name, sign in names_signs:
    percentile = within_strategy_percentiles(rows, name)
    parts.append({i: (p if sign > 0 else 1 - p) for i, p in percentile.items()})
  out = {}
  for index in range(len(rows)):
    values = [part[index] for part in parts if index in part]
    if values:
      out[index] = statistics.fmean(values)
  return out


def tercile_means(rows: list[dict], score: dict[int, float], keep) -> dict:
  items = [(score[i], rows[i]) for i in range(len(rows)) if i in score and keep(rows[i])]
  if len(items) < 30:
    return {"n": len(items)}
  items.sort(key=lambda x: x[0])
  third = len(items) // 3
  buckets = {"low": items[:third], "middle": items[third:2 * third], "high": items[2 * third:]}
  rng = random.Random(SEED)
  out = {"n": len(items)}
  for name, bucket in buckets.items():
    values = [(day_cluster(r), r["r"]) for _, r in bucket]
    out[name] = {"n": len(values), "mean_r": round(statistics.fmean(v for _, v in values), 3), "ci95": cluster_ci(values, rng)}
  out["spearman"] = None if len(items) < 5 else _round(spearman([s for s, _ in items], [r["r"] for _, r in items]))
  return out


def _round(value):
  return None if value is None else round(value, 3)


def run(records: list[dict]) -> dict:
  rng = random.Random(SEED)
  thinned = thin(sorted(records, key=lambda r: (r["eval_time"], r["id"])))
  rows = []
  for record in thinned:
    sim = record["sim"].get(TARGET, {})
    if sim.get("result") not in FILLED:
      continue
    row = {"strategy": record["strategy"], "symbol": record["symbol"], "eval_time": record["eval_time"],
           "r": sim["r"], "f": {**features_of(record), **components_of(record)}}
    row["day"] = day_cluster(record)
    rows.append(row)
  names = sorted({n for r in rows for n in r["f"]})
  report: dict = {
    "seed": SEED, "target": TARGET, "oos_starts_utc": "2026-10-07",
    "thinned_filled": len(rows),
    "dev": sum(1 for r in rows if r["eval_time"] < OOS_START), "oos": sum(1 for r in rows if r["eval_time"] >= OOS_START),
    "features_tried": len(names),
  }
  # 1. pooled, within-strategy percentile
  pooled = [evaluate_feature(rows, name, rng, len(names)) for name in names]
  report["pooled"] = sorted(pooled, key=lambda r: -abs(r["dev"]["spearman"] or 0))
  # The same test with the percentile taken inside strategy AND symbol: no instrument effect left.
  by_symbol = [evaluate_feature(rows, name, rng, len(names), by_symbol=True) for name in names]
  report["pooled_within_strategy_and_symbol"] = sorted(by_symbol, key=lambda r: -abs(r["dev"]["spearman"] or 0))
  report["supported_within_strategy_and_symbol"] = [r["feature"] for r in by_symbol if r["supported"]]
  # 2. inside each strategy, own components and shared features (n large enough)
  by_strategy: dict[str, list] = {}
  for strategy in sorted({r["strategy"] for r in rows}):
    sub = [r for r in rows if r["strategy"] == strategy]
    if sum(1 for r in sub if r["eval_time"] < OOS_START) < MIN_N or sum(1 for r in sub if r["eval_time"] >= OOS_START) < MIN_N // 2:
      by_strategy[strategy] = {"skipped": "fewer than the minimum filled observations in a window", "n": len(sub)}
      continue
    sub_names = sorted({n for r in sub for n in r["f"] if len({x["f"].get(n) for x in sub if n in x["f"]}) > 1})
    results = [evaluate_feature(sub, name, rng, len(sub_names) * len({r["strategy"] for r in rows}), pooled=False) for name in sub_names]
    by_strategy[strategy] = {"n": len(sub), "features_tried": len(sub_names),
                             "supported": [r["feature"] for r in results if r["supported"]],
                             "strongest": sorted(results, key=lambda r: -abs(r["dev"]["spearman"] or 0))[:5]}
  report["by_strategy"] = by_strategy
  # 3. the composite of the pooled supported features, equal weights, signs from the development window
  supported = [(r["feature"], 1 if r["dev"]["spearman"] > 0 else -1) for r in pooled if r["supported"]]
  report["supported_pooled_features"] = [{"feature": n, "sign": s} for n, s in supported]
  # Stability of each supported feature by symbol (pooled within-strategy percentiles, all periods).
  report["supported_by_symbol"] = {}
  for name, _ in supported:
    percentile = within_strategy_percentiles(rows, name)
    per_symbol = {}
    for symbol in sorted({r["symbol"] for r in rows}):
      for period, keep in (("dev", lambda r: r["eval_time"] < OOS_START), ("oos", lambda r: r["eval_time"] >= OOS_START)):
        pairs = [(percentile[i], r["r"]) for i, r in enumerate(rows) if r["symbol"] == symbol and keep(r) and i in percentile]
        rho = spearman([p for p, _ in pairs], [v for _, v in pairs])
        per_symbol.setdefault(symbol, {})[period] = {"n": len(pairs), "spearman": _round(rho)}
    report["supported_by_symbol"][name] = per_symbol
  existing = {i: row["f"]["quality"] for i, row in enumerate(rows) if "quality" in row["f"]}
  report["existing_quality"] = {
    "dev": tercile_means(rows, {i: p for i, p in within_strategy_percentiles(rows, "quality").items()}, lambda r: r["eval_time"] < OOS_START),
    "oos": tercile_means(rows, {i: p for i, p in within_strategy_percentiles(rows, "quality").items()}, lambda r: r["eval_time"] >= OOS_START),
  }
  if supported:
    score = composite(rows, supported)
    report["composite"] = {
      "definition": "equal-weight mean of the signed within-strategy percentiles of the supported features",
      "dev": tercile_means(rows, score, lambda r: r["eval_time"] < OOS_START),
      "oos": tercile_means(rows, score, lambda r: r["eval_time"] >= OOS_START),
    }
  else:
    report["composite"] = None
  del existing
  report["reading"] = (
    "A feature is supported only with a development interval that excludes zero, a Bonferroni-level "
    "development correlation and the same sign out of sample. Absence of support is not evidence of "
    "absence: the window is two weeks and neighbouring days share a regime."
  )
  return report


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("records")
  parser.add_argument("--out", default="")
  args = parser.parse_args()
  with open(args.records) as handle:
    records = [json.loads(line) for line in handle]
  report = run(records)
  text = json.dumps(report, indent=1, sort_keys=True)
  if args.out:
    Path(args.out).write_text(text + "\n")
  print(json.dumps({k: report[k] for k in ("thinned_filled", "dev", "oos", "features_tried", "supported_pooled_features", "composite")}, indent=1))


if __name__ == "__main__":
  main()

"""Offline impact of removing ``quality`` from the Algo Bot arbitration (P1 follow-up).

Reproduce (repository root, Python 3.12, the algo-bot requirements installed):

  cd analysis-engine && ARBITRATION_DUMP_DIR=/tmp/arb-dump \
    go test ./test/replaycapture -run TestDumpCandidates
  cd ../algo-bot && PYTHONPATH=. python -m tools.arbitration_impact /tmp/arb-dump \
    --out ../docs/strategies/arbitration-impact-results.json

Question: ``arbitrate_execution_intents`` ranks executable intents by ``quality_overall`` (then
confluence stars, ``v2_raw``, freshness, id) and holds a direction conflict unless the best
quality leads by ``conflict_margin_quality``. The quality certification found that no strategy's
``quality_overall`` is monotone in outcome and that the scores are on different scales per
strategy. What would change if the ranking and the margin stopped reading them?

It reuses ``tools.arbitration_eval`` (production admission code, production arbitration code,
the 45-minute corridor reservation, causal closed-candle fills) and adds:

* the 14 October captures, deduplicated: one row set per (symbol, bar), taken from the capture
  with the deepest history; the captures overlap by hours to days;
* one merged closed-bar M5 series per symbol for every outcome (the M1 series of two captures is
  not used, so these numbers are not comparable with ``arbitration-eval-results.json``);
* a chronological split per symbol (first 60% of the merged span is development, the rest is
  out of sample);
* models N (no quality anywhere) and NB (no quality, bias-aligned intent first);
* a direction-conflict section and outcomes by quality tercile, strategy and bias relationship.

Everything simulated is a hypothetical opportunity outcome, not broker P&L.
"""

from __future__ import annotations

import argparse
import glob
import json
import random
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from tools import arbitration_eval as ev

CAPTURE_DATE = re.compile(r"-(\d{8})\.json")


def _capture_order(name: str) -> tuple[str, str]:
  match = CAPTURE_DATE.search(name)
  return (match.group(1) if match else "", name)


def _merged_label(symbol: str) -> str:
  return f"merged:{symbol}"


def load_rows(dump_dir: str) -> list[dict]:
  """One row set per (symbol, bar_tf, bar): the capture whose own history is deepest there."""
  per_capture: dict[str, list[dict]] = defaultdict(list)
  for path in sorted(glob.glob(str(Path(dump_dir) / "*.jsonl"))):
    with open(path) as handle:
      for line in handle:
        row = json.loads(line)
        per_capture[row["capture"]].append(row)
  first_seen: dict[tuple, str] = {}
  for capture in sorted(per_capture, key=_capture_order):
    # A capture's own first bars have no history behind them; an earlier capture that covers the
    # same bar always has more, so the earliest capture wins every overlapping bar.
    for row in per_capture[capture]:
      key = (row["symbol"], row["bar_tf"], row["bar_time"])
      first_seen.setdefault(key, capture)
  rows = [row for capture, items in per_capture.items() for row in items
          if first_seen[(row["symbol"], row["bar_tf"], row["bar_time"])] == capture]
  for row in rows:
    row["source_capture"] = row["capture"]
    row["capture"] = _merged_label(row["symbol"])
  rows.sort(key=lambda r: (r["symbol"], r["bar_time"], r["bar_tf"], r["id"]))
  return rows


_MERGED: dict[str, tuple[list[tuple], str]] = {}


def load_bars(capture: str) -> tuple[list[tuple], str]:
  if not capture.startswith("merged:"):
    return _ORIGINAL_LOAD_BARS(capture)
  if capture not in _MERGED:
    symbol = capture.split(":", 1)[1]
    series: dict[int, tuple] = {}
    for path in sorted(glob.glob(str(ev.CAPTURES / f"replay-*{symbol.lower()}*-production-capture-*.json")),
                       key=_capture_order):
      name = Path(path).name
      if symbol == "XAU" and not name.startswith("replay-xau-"):
        continue
      if symbol != "XAU" and not name.startswith(f"replay-{symbol.lower()}-"):
        continue
      for row in json.loads(Path(path).read_text())["timeframes"].get("M5", []):
        series.setdefault(row[0], tuple(row))
    _MERGED[capture] = ([series[t] for t in sorted(series)], "M5")
  return _MERGED[capture]


_ORIGINAL_LOAD_BARS = ev.load_bars


# ---------------------------------------------------------------- extra analysis

def _percentile_of(values: list[float]):
  """Mid-rank percentile in [0, 1]: equal scores share one percentile, so a plateau is one bucket."""
  ordered = sorted(values)

  def percentile(value: float) -> float:
    below = sum(1 for v in ordered if v < value - 1e-9)
    equal = sum(1 for v in ordered if abs(v - value) <= 1e-9)
    return (below + equal / 2) / len(ordered)
  return percentile


def conflict_section(cycles: list[dict], models: list[ev.Model], boundary: dict[str, float]) -> dict:
  """Cycles in which executable intents of both directions exist: what each model decides."""
  out: dict[str, Counter] = {m.name: Counter() for m in models}
  margins: list[float] = []
  for cycle in cycles:
    executable = [i for i in cycle["intents"] if i.executable_now]
    if len({i.direction for i in executable}) < 2:
      continue
    ranked = sorted(executable, key=lambda i: -(i.quality_overall or 0.0))
    top = ranked[0]
    margins.append((top.quality_overall or 0.0) - max(
      (i.quality_overall or 0.0) for i in executable if i.direction != top.direction))
    period = "dev" if cycle["bar_time"] <= boundary[cycle["capture"]] else "oos"
    for model in models:
      winners, _, reason, _ = model.arbitrate(cycle["intents"])
      out[model.name]["cycles"] += 1
      out[model.name][f"cycles_{period}"] += 1
      if reason == "opposite_direction_conflict":
        out[model.name]["held"] += 1
      else:
        chosen = next((w for w in winners if w.executable_now), None)
        aligned = chosen is not None and str(chosen.bias_relationship or "").casefold() == "with_bias"
        out[model.name]["decided_with_bias" if aligned else "decided_without_bias"] += 1
  gaps = {
    "n": len(margins),
    "share_below_margin": round(sum(1 for g in margins if g < ev.CONFLICT_MARGIN) / len(margins), 3) if margins else None,
    "median_gap": round(statistics.median(margins), 3) if margins else None,
  }
  return {"opposite_executable_cycles": gaps, "by_model": {k: dict(v) for k, v in out.items()}}


def outcome_by_group(trades: list[dict], outcome, bias_at: dict) -> dict:
  """Mean hypothetical R of the incumbent's published trades by quality tercile, strategy and bias."""
  usable = [(t, outcome(t["row"])) for t in trades]
  usable = [(t, o) for t, o in usable if ev.usable(o)]
  if not usable:
    return {}
  percentile = _percentile_of([t["row"]["quality"] for t, _ in usable])
  low, high = 1 / 3, 2 / 3

  def summary(items):
    values = [o["r"] for _, o in items]
    return {"n": len(values), "mean_r": round(statistics.fmean(values), 3) if values else None}

  groups = {
    "quality_tercile_by_midrank_percentile": {
      "low": summary([x for x in usable if percentile(x[0]["row"]["quality"]) <= low]),
      "middle": summary([x for x in usable if low < percentile(x[0]["row"]["quality"]) <= high]),
      "high": summary([x for x in usable if percentile(x[0]["row"]["quality"]) > high]),
    },
    "stars": {str(s): summary([x for x in usable if x[0]["row"]["confluence"]["selected_stars"] == s])
              for s in sorted({x[0]["row"]["confluence"]["selected_stars"] for x in usable})},
  }
  by_bias: dict[str, list] = defaultdict(list)
  for t, o in usable:
    by_bias[str(bias_at.get((t["id"], t["bar_time"])) or "unknown")].append((t, o))
  groups["bias_relationship"] = {k: summary(v) for k, v in sorted(by_bias.items())}
  by_strategy: dict[str, list] = defaultdict(list)
  for t, o in usable:
    by_strategy[t["strategy"]].append((t, o))
  groups["strategy"] = {k: summary(v) for k, v in sorted(by_strategy.items(), key=lambda kv: -len(kv[1]))}
  rho = ev.spearman([t["row"]["quality"] for t, _ in usable], [o["r"] for _, o in usable])
  groups["spearman_quality_vs_r"] = None if rho is None else round(rho, 3)
  return groups


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("dump_dir")
  parser.add_argument("--out", default="")
  args = parser.parse_args()
  ev.load_rows, ev.load_bars = load_rows, load_bars

  captured: dict = {}
  original_decision_metrics = ev.decision_metrics

  def decision_metrics(cycles, models, boundary):
    captured["cycles"], captured["models"], captured["boundary"] = cycles, models, boundary
    return original_decision_metrics(cycles, models, boundary)

  ev.decision_metrics = decision_metrics
  report = ev.run(args.dump_dir, extra_models=(ev.model_n(), ev.model_nb()))
  cycles, models, boundary = captured["cycles"], captured["models"], captured["boundary"]
  report["conflicts"] = conflict_section(cycles, models, boundary)

  def outcome(row, scale=1.0):
    bars, tf = load_bars(row["capture"])
    return ev.simulate(row, bars, tf, spread_scale=scale)

  incumbent, _ = ev.play_forward(cycles, models[0])
  bias_at = {(i.intent_id, c["bar_time"]): i.bias_relationship for c in cycles for i in c["intents"]}
  report["incumbent_published_by_group"] = outcome_by_group(incumbent, outcome, bias_at)
  report["method"] = {
    "deduplicated_rows": len(load_rows(args.dump_dir)),
    "bars": "one merged closed-bar M5 series per symbol (all committed production captures)",
    "split": "chronological per symbol: first 60% development, remainder out of sample",
  }
  text = json.dumps(report, indent=2, sort_keys=True)
  if args.out:
    Path(args.out).write_text(text + "\n")
  print(json.dumps({k: report[k] for k in ("cycles", "eligible_intents", "conflicts", "paired_divergence", "incumbent_published_by_group")}, indent=1, sort_keys=True))


if __name__ == "__main__":
  main()

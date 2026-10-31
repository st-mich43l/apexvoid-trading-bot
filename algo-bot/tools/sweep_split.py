"""Split the simulated outcomes of fvg and order_block by the measured prior sweep and the causal structure break.

Reproduce (repository root, Python 3.12, the algo-bot requirements installed): see docs/strategies/liquidity-sweep-and-structure.md, section 8.

    cd analysis-engine && AEQ_DUMP=/tmp/aeq go test ./test/aequality -run TestReplayAllStrategies
    cd ../algo-bot && PYTHONPATH=. python -m tools.sweep_split /tmp/aeq/records.jsonl [--json out.json]

The records are test/aequality's: every unique opportunity, with its causal simulation under costs (`oneR_cost`, `twoR_cost`, `ladder_cost`) and the
quality measures `prior_sweep` (0/1) and `break_kind` (1 BOS, -1 CHoCH, 0 none) that fvg v9 and order_block v9 publish. Everything is a *hypothetical
opportunity outcome*, never broker P&L. For each cell: the opportunities, the ones that filled, the win rate at 1R, the mean R at 1R / 2R / the ladder
with a seeded 95% cluster bootstrap (clusters are symbol and UTC day: a thesis repeats for hours), and for each split the two-sided permutation p-value
of the difference of the mean R (labels permuted over the filled opportunities; it ignores the clustering, so it is optimistic, and the interval of
the difference is the cluster bootstrap's).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

from tools.ae_quality_eval import FILLED, SEED, cluster_ci, day_cluster

STRATEGIES = ("fvg", "order_block")
SIMS = ("oneR_cost", "twoR_cost", "ladder_cost")
PERMUTATIONS = 5000
BOOTSTRAPS = 1500


def load(paths: list[str]) -> list[dict]:
  records = []
  for path in paths:
    with open(path) as handle:
      records.extend(json.loads(line) for line in handle)
  return records


def _label_sweep(record: dict) -> str | None:
  value = record.get("components", {}).get("prior_sweep")
  return None if value is None else ("sweep" if value >= 0.5 else "no sweep")


def _label_break(record: dict) -> str | None:
  value = record.get("components", {}).get("break_kind")
  if value is None:
    return None
  return "BOS" if value > 0.5 else "CHoCH" if value < -0.5 else "no break"


SPLITS = {"prior_sweep": _label_sweep, "break_kind": _label_break}


def filled(record: dict, sim: str) -> dict | None:
  outcome = record.get("sim", {}).get(sim)
  return outcome if outcome and outcome.get("result") in FILLED else None


def mean_r(records: list[dict], sim: str) -> float | None:
  values = [o["r"] for o in (filled(r, sim) for r in records) if o]
  return statistics.fmean(values) if values else None


def permutation_p(yes: list[float], no: list[float], rng: random.Random) -> float | None:
  """Two-sided permutation p-value of the difference of the means."""
  if len(yes) < 3 or len(no) < 3:
    return None
  observed = abs(statistics.fmean(yes) - statistics.fmean(no))
  pooled = yes + no
  k = len(yes)
  hits = 0
  for _ in range(PERMUTATIONS):
    rng.shuffle(pooled)
    if abs(statistics.fmean(pooled[:k]) - statistics.fmean(pooled[k:])) >= observed - 1e-12:
      hits += 1
  return round((hits + 1) / (PERMUTATIONS + 1), 3)


def difference_ci(a: list[dict], b: list[dict], sim: str, rng: random.Random) -> tuple[float, float] | None:
  """95% interval of mean(a) - mean(b), resampling whole symbol-day clusters of both groups together."""
  clusters: dict[str, tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
  for group, index in ((a, 0), (b, 1)):
    for record in group:
      outcome = filled(record, sim)
      if outcome:
        clusters[day_cluster(record)][index].append(outcome["r"])
  keys = list(clusters)
  if len(keys) < 3:
    return None
  diffs = []
  for _ in range(BOOTSTRAPS):
    first: list[float] = []
    second: list[float] = []
    for key in (rng.choice(keys) for _ in keys):
      first += clusters[key][0]
      second += clusters[key][1]
    if first and second:
      diffs.append(statistics.fmean(first) - statistics.fmean(second))
  if len(diffs) < BOOTSTRAPS // 2:
    return None
  diffs.sort()
  return round(diffs[int(0.025 * len(diffs))], 3), round(diffs[int(0.975 * len(diffs))], 3)


def cell(records: list[dict], rng: random.Random) -> dict:
  one = [o for o in (filled(r, "oneR_cost") for r in records) if o]
  out = {"opportunities": len(records), "filled": len(one),
         "win_rate_1r": round(sum(1 for o in one if o["r"] > 0) / len(one), 3) if one else None}
  for sim in SIMS:
    values = [(day_cluster(r), o["r"]) for r in records if (o := filled(r, sim))]
    out[sim] = {"mean_r": round(statistics.fmean(v for _, v in values), 3) if values else None,
                "ci95": cluster_ci(values, rng, BOOTSTRAPS) if values else None}
  return out


def split_table(records: list[dict], rng: random.Random) -> dict:
  report: dict = {}
  for strategy in STRATEGIES:
    items = [r for r in records if r["strategy"] == strategy and r.get("has_reaction")]
    entry: dict = {"opportunities": len(items), "all": cell(items, rng), "splits": {}}
    for name, label in SPLITS.items():
      groups: dict[str, list[dict]] = defaultdict(list)
      for r in items:
        value = label(r)
        if value is not None:
          groups[value].append(r)
      if not groups:
        continue
      cells = {value: cell(rows, rng) for value, rows in sorted(groups.items())}
      tests = {}
      pairs = [("sweep", "no sweep")] if name == "prior_sweep" else [("BOS", "CHoCH"), ("BOS", "no break"), ("CHoCH", "no break")]
      for yes, no in pairs:
        if yes in groups and no in groups:
          for sim in SIMS:
            a = [o["r"] for o in (filled(r, sim) for r in groups[yes]) if o]
            b = [o["r"] for o in (filled(r, sim) for r in groups[no]) if o]
            tests[f"{yes} vs {no} / {sim}"] = {"difference": round(statistics.fmean(a) - statistics.fmean(b), 3) if a and b else None,
                                               "ci95": difference_ci(groups[yes], groups[no], sim, rng),
                                               "permutation_p": permutation_p(a, b, rng)}
      entry["splits"][name] = {"cells": cells, "tests": tests}
    report[strategy] = entry
  return report


def fmt(value, digits=2):
  return "" if value is None else f"{value:+.{digits}f}"


def markdown(report: dict) -> str:
  lines = []
  for strategy, entry in report.items():
    lines.append(f"\n**{strategy}**: {entry['opportunities']} confirmed opportunities\n")
    lines.append("| split | cell | n | filled | win 1R | mean R 1R [95%] | mean R 2R [95%] | mean R ladder [95%] |\n|---|---|---:|---:|---:|---|---|---|")

    def row(split, name, c):
      def r(sim):
        v = c[sim]
        ci = f" [{fmt(v['ci95'][0])}, {fmt(v['ci95'][1])}]" if v["ci95"] else ""
        return f"{fmt(v['mean_r'])}{ci}"
      win = "" if c["win_rate_1r"] is None else f"{c['win_rate_1r']:.0%}"
      lines.append(f"| {split} | {name} | {c['opportunities']} | {c['filled']} | {win} | {r('oneR_cost')} | {r('twoR_cost')} | {r('ladder_cost')} |")

    row("all", "all", entry["all"])
    for split, content in entry["splits"].items():
      for name, c in content["cells"].items():
        row(split, name, c)
    for split, content in entry["splits"].items():
      for name, t in content["tests"].items():
        ci = f" [{fmt(t['ci95'][0])}, {fmt(t['ci95'][1])}]" if t["ci95"] else ""
        p = "" if t["permutation_p"] is None else f"{t['permutation_p']:.3f}"
        lines.append(f"| {split} test | {name} | | | | difference {fmt(t['difference'])}{ci} | p = {p} | |")
  return "\n".join(lines)


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("records", nargs="+")
  parser.add_argument("--json", default="")
  args = parser.parse_args()
  report = split_table(load(args.records), random.Random(SEED))
  print(markdown(report))
  if args.json:
    Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
  main()

"""Offline, deterministic comparison of cross-strategy arbitration models.

Reproduce (from the repository root, Python 3.12, the algo-bot requirements installed):

    cd analysis-engine && ARBITRATION_DUMP_DIR=/tmp/arb-dump \
      go test ./test/replaycapture -run TestDumpCandidates
    cd ../algo-bot && PYTHONPATH=. python -m tools.arbitration_eval /tmp/arb-dump \
      --out /tmp/arbitration-eval.json

The dump holds, for every closed bar of every committed production capture, each
candidate the Go engine emitted with its published quality, confluence, evidence and
geometry. This tool

* rebuilds each decision cycle's eligible intents and runs the **production**
  ``arbitrate_execution_intents`` as the incumbent (model A), checking that the generic
  re-implementation used for the alternatives reproduces it exactly;
* ranks the same eligible sets under alternative models and reports selection
  agreement, ties, winner changes and conflicts;
* plays each model forward in time with the production 45-minute entry-corridor
  reservation, and measures what the published candidates would have done under a
  conservative single-leg limit/market simulator on the captured bars.

What it is not: the simulator is a **hypothetical opportunity outcome**, not broker
results. Entries are single leg, the XAU/FX ladders, partial fills, risk legs, opposing
exposure and the live quote are not modelled, and the spreads are assumed constants. It
supports "these models pick different trades and here is how those trades would have fared
under one fixed rule", never an expectancy claim. Outcome-calibrated ranking needs clean
broker results; ``auto_trade_results.realized_pips`` has existed only since 2026-10-08.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Callable

import yaml

from app.autotrade.arbitration import (
  ExecutionIntent,
  _collapse_theses,
  _rank,
  arbitrate_execution_intents,
  same_thesis,
)
from app.autotrade.entry_overlap import WINDOW_SECONDS, corridors_overlap

REPO = Path(__file__).resolve().parents[2]
CAPTURES = REPO / "analysis-engine" / "testdata"
SEED = 20261008
CONTRACT_TOLERANCE_PIPS = 3.0
CONFLICT_MARGIN = 0.15
DEV_FRACTION = 0.6

PIP_SIZE = {"XAU": 0.1, "EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01, "GBPJPY": 0.01}
# Assumed constant spreads in price units (bars are bid-only). Doubled in the sensitivity run.
SPREAD = {"XAU": 0.25, "EURUSD": 0.00008, "GBPUSD": 0.0001, "USDJPY": 0.008, "GBPJPY": 0.02}
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}
TARGET_R = (1.0, 2.0, 3.0, 4.0)
CLOSE_RATIOS = (0.4, 0.2, 0.2, 0.2)


# ---------------------------------------------------------------- inputs

def load_rows(dump_dir: str) -> list[dict]:
  rows: list[dict] = []
  for path in sorted(glob.glob(str(Path(dump_dir) / "*.jsonl"))):
    with open(path) as handle:
      rows.extend(json.loads(line) for line in handle)
  rows.sort(key=lambda r: (r["symbol"], r["bar_time"], r["bar_tf"], r["id"]))
  return rows


def requires_reaction() -> set[str]:
  source = (REPO / "algo-bot/app/autotrade/go_opportunity_policy.py").read_text()
  return set(re.findall(r'ScopeProfile\("(\w+)",[^\n]*?requires_reaction=True', source))


def containment() -> dict[str, tuple[set[str], set[str]]]:
  """Per instrument: observe-only strategies and observe-only structure timeframes."""
  document = yaml.safe_load((REPO / "config/instruments.yml").read_text())

  def find(node, key):
    if isinstance(node, dict):
      if key in node:
        return node[key]
      for value in node.values():
        found = find(value, key)
        if found is not None:
          return found
    return None

  out: dict[str, tuple[set[str], set[str]]] = {}
  instruments = document.get("instruments", document)
  for symbol in PIP_SIZE:
    node = instruments.get(symbol, {})
    out[symbol] = (
      set(find(node, "observe_only_strategies") or ()),
      set(find(node, "observe_only_structure_timeframes") or ()),
    )
  return out


def load_bars(capture: str) -> tuple[list[tuple], str]:
  document = json.loads((CAPTURES / capture).read_text())
  timeframe = "M1" if "M1" in document["timeframes"] else "M5"
  return [tuple(row) for row in document["timeframes"][timeframe]], timeframe


# ---------------------------------------------------------------- eligibility

def pips(symbol: str, price_distance: float) -> float:
  return price_distance / PIP_SIZE[symbol]


def eligible(row: dict, *, reaction_required: set[str], contained: dict) -> tuple[bool, bool]:
  """(eligible, executable_now). Eligibility is decided before any ranking."""
  symbol, strategy = row["symbol"], row["strategy"]
  if strategy in reaction_required and not row["has_reaction"]:
    return False, False
  observe_strategies, observe_structures = contained.get(symbol, (set(), set()))
  if strategy in observe_strategies or row.get("structure_tf") in observe_structures:
    return False, False
  if row["expires_at"] and row["expires_at"] <= row["bar_time"]:
    return False, False
  if row.get("confluence") is None or row.get("atr") is None:
    return False, False
  low, high, close = row["entry_low"], row["entry_high"], row["reference_price"]
  if not (math.isfinite(low) and math.isfinite(high) and high >= low > 0):
    return False, False
  invalidation = row["invalidation"]
  if (row["direction"] == "BUY" and close <= invalidation) or (row["direction"] == "SELL" and close >= invalidation):
    return False, False
  tolerance = CONTRACT_TOLERANCE_PIPS * PIP_SIZE[symbol]
  return True, (low - tolerance <= close <= high + tolerance)


def to_intent(row: dict, executable_now: bool) -> ExecutionIntent:
  confluence = row["confluence"]
  bias = row.get("bias") or ""
  relation = "neutral" if not bias else ("with_bias" if bias == row["direction"] else "counter_bias")
  return ExecutionIntent(
    intent_id=row["id"], source="go_analysis_engine", strategy=row["strategy"], direction=row["direction"],
    confluence=int(confluence["selected_stars"]), freshness=float(row["created_at"]), distance_pips=0.0,
    symbol=row["symbol"], timeframe=row["observed_tf"], entry_low=row["entry_low"], entry_high=row["entry_high"],
    structural_id=row["structural_id"] or row["id"], quality_overall=float(row["quality"]),
    structural_quality=float(confluence["v2_raw"]), atr=float(row["atr"]), bias_relationship=relation,
    executable_now=executable_now,
  )


# ---------------------------------------------------------------- models

def _percentile_table(rows: list[dict]) -> dict[str, list[float]]:
  scores: dict[str, list[float]] = defaultdict(list)
  for row in rows:
    scores[row["strategy"]].append(float(row["quality"]))
  return {name: sorted(values) for name, values in scores.items()}


def make_percentile(table: dict[str, list[float]]) -> Callable[[str, float], float]:
  def percentile(strategy: str, quality: float) -> float:
    values = table.get(strategy)
    if not values:
      return 0.0
    below = sum(1 for value in values if value < quality - 1e-9)
    equal = sum(1 for value in values if abs(value - quality) <= 1e-9)
    return (below + equal / 2) / len(values)
  return percentile


class Model:
  """A ranking model: a total order over intents plus the score its conflict margin uses."""

  def __init__(self, name: str, key: Callable[[ExecutionIntent], tuple], score: Callable[[ExecutionIntent], float]):
    self.name, self.key, self.score = name, key, score

  def arbitrate(self, intents: list[ExecutionIntent]):
    """Production control flow (direction decision, margin, one winner per thesis) with this
    model's order and score. Reproduces ``arbitrate_execution_intents`` exactly for model A."""
    if not intents:
      return (), (), "no_intent", {}
    ranked = sorted(intents, key=self.key)
    pool = [item for item in ranked if item.executable_now] or ranked
    top = pool[0]
    opposing = [item for item in pool if item.direction != top.direction]
    if opposing and self.score(top) - self.score(opposing[0]) < CONFLICT_MARGIN:
      aligned = {item.direction for item in pool if str(item.bias_relationship or "").casefold() == "with_bias"}
      if len(aligned) == 1:
        direction = next(iter(aligned))
        top = next(item for item in pool if item.direction == direction)
      else:
        return (), tuple(ranked), "opposite_direction_conflict", {}
    same_direction = [item for item in ranked if item.direction == top.direction]
    winners, losers = _collapse_theses(same_direction)
    return tuple(winners), tuple(item for item in ranked if item.direction != top.direction or item.intent_id in losers), "ranked_single_direction", losers


def model_a() -> Model:
  return Model("A_incumbent", _rank, lambda i: i.quality_overall or 0.0)


def model_b(table: dict[str, list[float]]) -> Model:
  percentile = make_percentile(table)

  def adjusted(i: ExecutionIntent) -> float:
    return percentile(i.strategy, i.quality_overall or 0.0)

  def key(i: ExecutionIntent) -> tuple:
    return (0 if i.executable_now else 1, -adjusted(i), -i.confluence, -(i.structural_quality or 0.0), -i.freshness, i.intent_id)
  return Model("B_per_strategy_percentile", key, adjusted)


def model_c() -> Model:
  def key(i: ExecutionIntent) -> tuple:
    return (0 if i.executable_now else 1, -i.confluence, -(i.structural_quality or 0.0), -(i.quality_overall or 0.0), -i.freshness, i.intent_id)
  return Model("C_shared_confluence_first", key, lambda i: i.confluence / 3.0)


# ---------------------------------------------------------------- cycles

def build_cycles(rows: list[dict]) -> list[dict]:
  reaction_required, contained = requires_reaction(), containment()
  by_cycle: dict[tuple, list[dict]] = defaultdict(list)
  for row in rows:
    by_cycle[(row["capture"], row["symbol"], row["bar_tf"], row["bar_time"])].append(row)
  cycles = []
  for (capture, symbol, tf, bar_time), items in sorted(by_cycle.items(), key=lambda x: (x[0][1], x[0][3], x[0][2])):
    intents, source = [], {}
    for row in items:
      ok, executable = eligible(row, reaction_required=reaction_required, contained=contained)
      if ok:
        intents.append(to_intent(row, executable))
        source[row["id"]] = row
    cycles.append({"capture": capture, "symbol": symbol, "tf": tf, "bar_time": bar_time, "intents": intents, "rows": source,
                   "emitted": len(items), "ineligible": len(items) - len(intents)})
  return cycles


def split_time(cycles: list[dict]) -> dict[str, float]:
  """Chronological dev/OOS boundary per capture (first DEV_FRACTION of its time span)."""
  spans: dict[str, tuple[int, int]] = {}
  for cycle in cycles:
    low, high = spans.get(cycle["capture"], (cycle["bar_time"], cycle["bar_time"]))
    spans[cycle["capture"]] = (min(low, cycle["bar_time"]), max(high, cycle["bar_time"]))
  return {capture: low + DEV_FRACTION * (high - low) for capture, (low, high) in spans.items()}


# ---------------------------------------------------------------- hypothetical outcome

def simulate(row: dict, bars: list[tuple], tf: str, *, spread_scale: float = 1.0) -> dict:
  """One candidate under one fixed rule. Bars are bid. Returns {filled, r, reason}."""
  symbol, side = row["symbol"], row["direction"]
  pip, spread = PIP_SIZE[symbol], SPREAD[symbol] * spread_scale
  floor, cap = row.get("stop_floor_pips"), row.get("stop_cap_pips")
  if floor is None or cap is None:
    return {"filled": False, "r": 0.0, "reason": "no_stop_envelope"}
  low, high, close = row["entry_low"], row["entry_high"], row["reference_price"]
  tolerance = CONTRACT_TOLERANCE_PIPS * pip
  inside = low - tolerance <= close <= high + tolerance
  decision = row["bar_time"] + TF_SECONDS[row["bar_tf"]]
  expiry = min(row["expires_at"] or decision + 86400, decision + 86400)
  proximal = high if side == "BUY" else low
  entry_ref = close if inside else proximal
  stop_pips = abs(entry_ref - row["invalidation"]) / pip
  if stop_pips > cap + 1e-9:
    return {"filled": False, "r": 0.0, "reason": "stop_above_cap"}
  stop_pips = max(stop_pips, float(floor))
  distance = stop_pips * pip
  index = next((i for i, bar in enumerate(bars) if bar[0] >= decision), None)
  if index is None:
    return {"filled": False, "r": 0.0, "reason": "no_bars"}
  buy = side == "BUY"
  entry = None
  position = 0.0
  r_total = 0.0
  stop = None
  stage = 0
  for bar in bars[index:]:
    t, o, h, l, c = bar[0], bar[1], bar[2], bar[3], bar[4]
    if t >= expiry:
      break
    if entry is None:
      if (buy and c <= row["invalidation"]) or (not buy and c >= row["invalidation"]):
        return {"filled": False, "r": 0.0, "reason": "invalidated_before_fill"}
      if inside and t == bars[index][0]:
        entry = o + spread if buy else o
      elif not inside:
        if buy and l + spread <= proximal:
          entry = proximal
        elif not buy and h >= proximal:
          entry = proximal
      if entry is None:
        continue
      position = 1.0
      stop = entry - distance if buy else entry + distance
      # Conservative: on the fill bar only the stop can act.
      if (buy and l <= stop) or (not buy and h + spread >= stop):
        return {"filled": True, "r": -1.0, "reason": "stopped_on_fill_bar"}
      continue
    # In a trade. Conservative ordering inside one bar: stop first.
    stop_hit = (l <= stop) if buy else (h + spread >= stop)
    if stop_hit:
      r_total += position * ((stop - entry) / distance if buy else (entry - stop) / distance)
      return {"filled": True, "r": round(r_total, 4), "reason": "stop" if stage == 0 else "be_or_trail_stop"}
    while stage < len(TARGET_R):
      target = entry + TARGET_R[stage] * distance if buy else entry - TARGET_R[stage] * distance
      reached = (h >= target) if buy else (l + spread <= target)
      if not reached:
        break
      r_total += CLOSE_RATIOS[stage] * TARGET_R[stage]
      position -= CLOSE_RATIOS[stage]
      stage += 1
      if stage == 1:
        stop = entry  # break-even after 1R
      if position <= 1e-9:
        return {"filled": True, "r": round(r_total, 4), "reason": "all_targets"}
  if entry is None:
    return {"filled": False, "r": 0.0, "reason": "unfilled_until_expiry"}
  last = bars[-1][4]
  r_total += position * (((last - entry) if buy else (entry - last)) / distance)
  return {"filled": True, "r": round(r_total, 4), "reason": "timeout"}


# ---------------------------------------------------------------- evaluation

def decision_metrics(cycles: list[dict], models: list[Model], boundary: dict[str, float]) -> dict:
  """Selection behaviour on the identical eligible sets, with no time dimension."""
  baseline = models[0]
  out: dict = {m.name: defaultdict(int) for m in models}
  changes: dict = {m.name: Counter() for m in models[1:]}
  by_symbol: dict = {m.name: Counter() for m in models[1:]}
  consistency_failures = 0
  for cycle in cycles:
    intents = cycle["intents"]
    if not intents:
      continue
    prod = arbitrate_execution_intents(intents, conflict_margin_quality=CONFLICT_MARGIN)
    base_winners, base_suppressed, base_reason, _ = baseline.arbitrate(intents)
    if tuple(i.intent_id for i in prod.ordered) != tuple(i.intent_id for i in base_winners) or prod.reason_code != base_reason:
      consistency_failures += 1
    executable = [i for i in intents if i.executable_now]
    contested = len(executable) >= 2
    period = "dev" if cycle["bar_time"] <= boundary[cycle["capture"]] else "oos"
    for model in models:
      winners, suppressed, reason, _losers = model.arbitrate(intents)
      stats = out[model.name]
      stats["cycles"] += 1
      stats[f"cycles_{period}"] += 1
      stats["intents"] += len(intents)
      if reason == "opposite_direction_conflict":
        stats["conflicts"] += 1
      stats["suppressed"] += len(suppressed)
      # Same-thesis groups with at least two strategies: how often the winner's quality is
      # tied with a loser's (the 2,786-group / 47% finding of the previous audit, here under
      # the production grouping rather than transitive corridor chains).
      if winners and reason != "opposite_direction_conflict":
        by_id = {i.intent_id: i for i in intents}
        members: dict[str, list[ExecutionIntent]] = defaultdict(list)
        for loser_id, owner_id in _losers.items():
          members[owner_id].append(by_id[loser_id])
        for winner in winners:
          group = [winner, *members.get(winner.intent_id, [])]
          if len({g.strategy for g in group}) >= 2:
            stats["cross_strategy_groups"] += 1
            if abs(model.score(winner) - max(model.score(g) for g in group[1:])) < 1e-9:
              stats["cross_strategy_groups_tied_on_score"] += 1
      if contested and winners:
        stats["contested"] += 1
        stats[f"contested_{period}"] += 1
        top = winners[0]
        same = [i for i in executable if same_thesis(top, i) and i.intent_id != top.intent_id]
        if same:
          key = model.key(top)
          best_other = min(model.key(i) for i in same)
          # tie on the model's primary quantity (rank position 1)
          if abs(key[1] - best_other[1]) < 1e-12:
            stats["top_primary_tie"] += 1
        if model is not baseline and baseline_winner(base_winners) != top.intent_id:
          stats["winner_differs"] += 1
          stats[f"winner_differs_{period}"] += 1
          changes[model.name][(baseline_winner_strategy(base_winners), top.strategy)] += 1
          by_symbol[model.name][cycle["symbol"]] += 1
  return {
    "consistency_failures_vs_production": consistency_failures,
    "models": {name: dict(stats) for name, stats in out.items()},
    "winner_changes": {name: {f"{a}->{b}": n for (a, b), n in c.most_common(15)} for name, c in changes.items()},
    "winner_changes_by_symbol": {name: dict(c) for name, c in by_symbol.items()},
  }


def baseline_winner(winners) -> str | None:
  return winners[0].intent_id if winners else None


def baseline_winner_strategy(winners) -> str | None:
  return winners[0].strategy if winners else None


def play_forward(cycles: list[dict], model: Model) -> tuple[list[dict], list[dict]]:
  """Sequential publication under the production 45-minute entry-corridor reservation.

  Returns the published trades and the blocked attempts (a better-or-equal ranked
  candidate stopped by an earlier winner's reservation)."""
  reservations: dict[str, list[dict]] = defaultdict(list)
  published_ids: set[str] = set()
  published, blocked_attempts = [], []
  blocked_seen: set[tuple] = set()
  for cycle in cycles:
    intents = [i for i in cycle["intents"] if i.intent_id not in published_ids]
    winners, _, reason, _ = model.arbitrate(intents)
    if not winners:
      continue
    chosen = next((w for w in winners if w.executable_now), None)
    if chosen is None:
      continue
    now = cycle["bar_time"]
    live = [r for r in reservations[chosen.symbol] if now - r["time"] <= WINDOW_SECONDS]
    reservations[chosen.symbol] = live
    holder = next((
      r for r in live
      if r["direction"] == chosen.direction
      and corridors_overlap(chosen.entry_low, chosen.entry_high, r["low"], r["high"], chosen.atr)
    ), None)
    if holder is not None:
      pair = (chosen.intent_id, holder["id"])
      if pair not in blocked_seen:
        blocked_seen.add(pair)
        blocked_attempts.append({
          "symbol": chosen.symbol, "bar_time": now, "capture": cycle["capture"],
          "blocked": cycle["rows"][chosen.intent_id], "holder": holder["row"],
          # model rank keys without the executable flag and the id tie-break
          "stronger": model.key(chosen)[1:4] < holder["key"][1:4],
          "same_strategy": chosen.strategy == holder["strategy"],
        })
      continue
    reservations[chosen.symbol].append({
      "low": chosen.entry_low, "high": chosen.entry_high, "direction": chosen.direction, "time": now,
      "id": chosen.intent_id, "key": model.key(chosen), "strategy": chosen.strategy,
      "row": cycle["rows"][chosen.intent_id],
    })
    published_ids.add(chosen.intent_id)
    published.append({"id": chosen.intent_id, "strategy": chosen.strategy, "symbol": chosen.symbol, "bar_time": now,
                      "capture": cycle["capture"], "row": cycle["rows"][chosen.intent_id]})
  return published, blocked_attempts


def spearman(xs: list[float], ys: list[float]) -> float | None:
  if len(xs) < 5:
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


def session_of(bar_time: int) -> str:
  hour = (bar_time // 3600) % 24
  return "asia" if hour < 7 else "london" if hour < 13 else "new_york" if hour < 21 else "late"


def candidate_outcomes(cycles: list[dict], boundary: dict, outcome, rng: random.Random) -> dict:
  """Hypothetical outcome of every eligible, executable candidate (first such cycle),
  split by the dimensions the ranking could be tempted to read. Separates quality from
  profitability: it reports how weakly the scores relate to the outcomes."""
  first: dict[str, tuple[dict, dict]] = {}
  for cycle in cycles:
    for intent in cycle["intents"]:
      if intent.executable_now and intent.intent_id not in first:
        first[intent.intent_id] = (cycle, cycle["rows"][intent.intent_id])
  records = []
  for cycle, row in first.values():
    result = outcome(row)
    atr_pips = pips(row["symbol"], row["atr"])
    records.append({
      "strategy": row["strategy"], "symbol": row["symbol"], "tf": row["observed_tf"],
      "session": session_of(row["bar_time"]), "quality": row["quality"],
      "stars": row["confluence"]["selected_stars"], "v2_raw": row["confluence"]["v2_raw"],
      "atr_pips": atr_pips, "filled": result["filled"], "r": result["r"], "reason": result["reason"],
      "period": "dev" if cycle["bar_time"] <= boundary[cycle["capture"]] else "oos",
    })

  def agg(subset: list[dict]) -> dict:
    filled = [x["r"] for x in subset if x["filled"]]
    return {"n": len(subset), "stop_above_cap_rate": round(sum(1 for x in subset if x["reason"] == "stop_above_cap") / len(subset), 3) if subset else None,
            "fill_rate": round(len(filled) / len(subset), 3) if subset else None,
            "mean_r": round(statistics.fmean(filled), 3) if filled else None,
            "win_rate": round(sum(1 for r in filled if r > 0) / len(filled), 3) if filled else None}

  def group(key) -> dict:
    buckets: dict = defaultdict(list)
    for x in records:
      buckets[key(x)].append(x)
    return {str(k): agg(v) for k, v in sorted(buckets.items(), key=lambda kv: str(kv[0]))}

  # Volatility terciles per symbol.
  terciles: dict[str, tuple[float, float]] = {}
  for symbol in PIP_SIZE:
    values = sorted(x["atr_pips"] for x in records if x["symbol"] == symbol)
    if len(values) >= 3:
      terciles[symbol] = (values[len(values) // 3], values[2 * len(values) // 3])

  def vol(x):
    low, high = terciles.get(x["symbol"], (0, 0))
    return "low" if x["atr_pips"] <= low else "mid" if x["atr_pips"] <= high else "high"

  def quality_bin(x):
    q = x["quality"]
    return "<0.5" if q < 0.5 else "0.5-0.67" if q < 0.67 - 1e-9 else "0.67" if q < 0.8 else "0.8-0.99" if q < 0.999 else "1.0"

  correlations = {}
  filled = [x for x in records if x["filled"]]
  for label, field in (("quality", "quality"), ("stars", "stars"), ("v2_raw", "v2_raw")):
    pooled = spearman([x[field] for x in filled], [x["r"] for x in filled])
    within = {}
    for strategy in sorted({x["strategy"] for x in filled}):
      items = [x for x in filled if x["strategy"] == strategy]
      if len(items) >= 30 and len({x[field] for x in items}) > 1:
        within[strategy] = round(spearman([x[field] for x in items], [x["r"] for x in items]) or 0.0, 3)
    for period in ("dev", "oos"):
      items = [x for x in filled if x["period"] == period]
      correlations[f"{label}_{period}"] = round(spearman([x[field] for x in items], [x["r"] for x in items]) or 0.0, 3)
    correlations[label] = {"pooled": None if pooled is None else round(pooled, 3), "within_strategy": within}
  return {
    "unique_executable_candidates": len(records),
    "outcome_reasons": dict(Counter(x["reason"] for x in records)),
    "by_strategy": group(lambda x: x["strategy"]), "by_symbol": group(lambda x: x["symbol"]),
    "by_observed_timeframe": group(lambda x: x["tf"]), "by_session": group(lambda x: x["session"]),
    "by_volatility_tercile": group(vol), "by_quality_bin": group(quality_bin), "by_stars": group(lambda x: x["stars"]),
    "rank_correlation_with_hypothetical_r": correlations,
  }


def thesis_audit(cycles: list[dict], rng: random.Random) -> dict:
  """Same-thesis grouping: chaining, corridor-only merges, and delivery-order independence."""
  contested = chained = corridor_only = merges = order_mismatch = shuffles = 0
  gap_bins: Counter = Counter()
  cross_strategy_corridor = 0
  group_count_diff = 0
  for cycle in cycles:
    intents = [i for i in cycle["intents"] if i.executable_now]
    if len(intents) < 2:
      continue
    reference = arbitrate_execution_intents(intents)
    for _ in range(3):
      shuffled = intents[:]
      rng.shuffle(shuffled)
      other = arbitrate_execution_intents(shuffled)
      shuffles += 1
      if (tuple(i.intent_id for i in other.ordered) != tuple(i.intent_id for i in reference.ordered)
          or {i.intent_id for i in other.suppressed} != {i.intent_id for i in reference.suppressed}
          or other.reason_code != reference.reason_code):
        order_mismatch += 1
    for direction in ("BUY", "SELL"):
      side = [i for i in intents if i.direction == direction]
      if len(side) < 2:
        continue
      contested += 1
      ranked = sorted(side, key=_rank)
      winners, losers = _collapse_theses(ranked)
      # Transitive closure of the pairwise relation.
      parent = {i.intent_id: i.intent_id for i in side}

      def find(x):
        while parent[x] != x:
          parent[x] = parent[parent[x]]
          x = parent[x]
        return x

      pairs = [(a, b) for idx, a in enumerate(side) for b in side[idx + 1:] if same_thesis(a, b)]
      for a, b in pairs:
        parent[find(a.intent_id)] = find(b.intent_id)
      closure_groups = len({find(i.intent_id) for i in side})
      if closure_groups != len(winners):
        chained += 1
        group_count_diff += abs(closure_groups - len(winners))
      by_id = {i.intent_id: i for i in side}
      for loser_id, owner_id in losers.items():
        loser, owner = by_id[loser_id], by_id[owner_id]
        merges += 1
        explicit = (loser.go_thesis_id and loser.go_thesis_id == owner.go_thesis_id) or (
          loser.structural_id and loser.structural_id == owner.structural_id)
        if explicit:
          continue
        corridor_only += 1
        if loser.strategy != owner.strategy:
          cross_strategy_corridor += 1
        gap = max(0.0, max(loser.entry_low, owner.entry_low) - min(loser.entry_high, owner.entry_high))
        atr = max(loser.atr, owner.atr) or 1.0
        gap_bins["zones_intersect" if gap <= 0 else "gap<=0.5ATR" if gap <= 0.5 * atr else "gap<=1ATR"] += 1
  return {
    "same_direction_contested_cycles": contested, "merges": merges, "merges_by_corridor_pad_only": corridor_only,
    "corridor_only_cross_strategy": cross_strategy_corridor, "corridor_only_zone_gap": dict(gap_bins),
    "cycles_where_chain_changes_group_count": chained, "delivery_order_shuffles": shuffles,
    "delivery_order_mismatches": order_mismatch,
  }


def bootstrap_ci(values: list[float], rng: random.Random, n: int = 2000) -> tuple[float, float]:
  if len(values) < 2:
    return (float("nan"), float("nan"))
  means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(n))
  return means[int(0.025 * n)], means[int(0.975 * n)]


def summarize(trades: list[dict], outcomes: dict) -> dict:
  filled = [outcomes[t["id"]] for t in trades if outcomes[t["id"]]["filled"]]
  r = [o["r"] for o in filled]
  return {
    "published": len(trades), "filled": len(filled),
    "fill_rate": round(len(filled) / len(trades), 3) if trades else None,
    "mean_r": round(statistics.fmean(r), 4) if r else None,
    "win_rate": round(sum(1 for x in r if x > 0) / len(r), 3) if r else None,
    "sum_r": round(sum(r), 2),
  }


def run(dump_dir: str) -> dict:
  rng = random.Random(SEED)
  rows = load_rows(dump_dir)
  cycles = build_cycles(rows)
  boundary = split_time(cycles)
  dev_rows = [r for r in rows if r["bar_time"] <= boundary[r["capture"]] and r.get("has_reaction", True)]
  table = _percentile_table(dev_rows)  # model B is fitted on the development window only
  models = [model_a(), model_b(table), model_c()]
  bars_cache: dict[str, tuple[list[tuple], str]] = {}

  def bars_for(capture: str):
    if capture not in bars_cache:
      bars_cache[capture] = load_bars(capture)
    return bars_cache[capture]

  outcome_cache: dict[tuple, dict] = {}

  def outcome(row: dict, scale: float = 1.0) -> dict:
    key = (row["id"], row["bar_time"], scale)
    if key not in outcome_cache:
      bars, _ = bars_for(row["capture"])
      outcome_cache[key] = simulate(row, bars, row["bar_tf"], spread_scale=scale)
    return outcome_cache[key]

  report: dict = {
    "seed": SEED, "captures": sorted({r["capture"] for r in rows}), "candidates": len(rows),
    "cycles": len(cycles), "dev_fraction": DEV_FRACTION,
    "eligible_intents": sum(len(c["intents"]) for c in cycles),
    "ineligible_emitted": sum(c["ineligible"] for c in cycles),
    "decision": decision_metrics(cycles, models, boundary),
    "play_forward": {}, "paired_divergence": {},
  }
  report["thesis_audit"] = thesis_audit(cycles, rng)
  report["candidate_outcomes"] = candidate_outcomes(cycles, boundary, outcome, rng)

  played = {m.name: play_forward(cycles, m) for m in models}
  streams = {name: trades for name, (trades, _) in played.items()}
  for name, trades in streams.items():
    outcomes = {t["id"]: outcome(t["row"]) for t in trades}
    split = {"all": trades,
             "dev": [t for t in trades if t["bar_time"] <= boundary[t["capture"]]],
             "oos": [t for t in trades if t["bar_time"] > boundary[t["capture"]]]}
    report["play_forward"][name] = {
      period: summarize(subset, outcomes) for period, subset in split.items()
    }
    report["play_forward"][name]["by_strategy"] = dict(Counter(t["strategy"] for t in trades).most_common())
    report["play_forward"][name]["by_symbol"] = dict(Counter(t["symbol"] for t in trades).most_common())
    report["play_forward"][name]["double_spread"] = summarize(
      trades, {t["id"]: outcome(t["row"], 2.0) for t in trades},
    )
  for name, (_, blocked_attempts) in played.items():
    stronger = [x for x in blocked_attempts if x["stronger"]]
    deltas = []
    for item in stronger:
      held, tried = outcome(item["holder"]), outcome(item["blocked"])
      if held["filled"] and tried["filled"]:
        deltas.append(tried["r"] - held["r"])
    low, high = bootstrap_ci(deltas, rng)
    report["play_forward"][name]["reservation"] = {
      "blocked_attempts": len(blocked_attempts), "blocked_but_better_ranked": len(stronger),
      "blocked_same_strategy": sum(1 for x in blocked_attempts if x["same_strategy"]),
      "pairs_both_filled": len(deltas),
      "mean_delta_r_if_the_better_ranked_had_displaced_the_holder": round(statistics.fmean(deltas), 4) if deltas else None,
      "ci95": [round(low, 3), round(high, 3)] if deltas else None,
    }
  base_ids = {t["id"] for t in streams[models[0].name]}
  for model in models[1:]:
    ids = {t["id"] for t in streams[model.name]}
    report["play_forward"][model.name]["same_published_as_baseline"] = round(len(ids & base_ids) / max(1, len(ids | base_ids)), 3)

  # Paired divergence: the first cycle of each (symbol, direction, 45 min window, pair of
  # candidates) where a model's executable winner differs from the incumbent's.
  for model in models[1:]:
    seen: set[tuple] = set()
    pairs = []
    for cycle in cycles:
      intents = cycle["intents"]
      if len([i for i in intents if i.executable_now]) < 2:
        continue
      a, *_ = models[0].arbitrate(intents)
      b, *_ = model.arbitrate(intents)
      wa = next((w for w in a if w.executable_now), None)
      wb = next((w for w in b if w.executable_now), None)
      if wa is None or wb is None or wa.intent_id == wb.intent_id:
        continue
      key = (wa.symbol, wa.direction, wa.intent_id, wb.intent_id)
      if key in seen:
        continue
      seen.add(key)
      pairs.append((cycle, wa, wb))
    deltas = {"all": [], "dev": [], "oos": []}
    unfilled = Counter()
    for cycle, wa, wb in pairs:
      ra, rb = outcome(cycle["rows"][wa.intent_id]), outcome(cycle["rows"][wb.intent_id])
      period = "dev" if cycle["bar_time"] <= boundary[cycle["capture"]] else "oos"
      if ra["filled"] and rb["filled"]:
        deltas["all"].append(rb["r"] - ra["r"])
        deltas[period].append(rb["r"] - ra["r"])
      else:
        unfilled["incumbent_unfilled" if not ra["filled"] and rb["filled"] else
                 "alternative_unfilled" if ra["filled"] and not rb["filled"] else "neither_filled"] += 1
    result = {"pairs": len(pairs), "unfilled": dict(unfilled)}
    for period, values in deltas.items():
      low, high = bootstrap_ci(values, rng)
      result[period] = {
        "pairs_both_filled": len(values),
        "mean_delta_r": round(statistics.fmean(values), 4) if values else None,
        "ci95": [round(low, 3), round(high, 3)] if values else None,
      }
    report["paired_divergence"][model.name] = result
  return report


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("dump_dir")
  parser.add_argument("--out", default="")
  args = parser.parse_args()
  report = run(args.dump_dir)
  text = json.dumps(report, indent=2, sort_keys=True)
  if args.out:
    Path(args.out).write_text(text + "\n")
  print(text)


if __name__ == "__main__":
  main()

"""Offline, deterministic comparison of cross-strategy arbitration models.

Reproduce (from the repository root, Python 3.12, the algo-bot requirements installed):

    cd analysis-engine && ARBITRATION_DUMP_DIR=/tmp/arb-dump \
      go test ./test/replaycapture -run TestDumpCandidates
    cd ../algo-bot && PYTHONPATH=. python -m tools.arbitration_eval /tmp/arb-dump \
      --out /tmp/arbitration-eval.json [--production-export prod_export.jsonl]

The dump holds, for every closed bar of every committed production capture, each candidate
the Go engine emitted, with the exact ``analysis.opportunity.v1`` envelope it would publish.
This tool

* admits each candidate through **Algo Bot's own admission code** (the reviewed-scope
  registry, instrument containment, freshness, ``build_strategy_match`` and the static
  admission gates) - it never re-derives eligibility - and builds each cycle's intents with
  the very function the worker uses (``execution_intent_for_match``), with "executable now"
  decided by the worker's own ``_execution_quote_access``;
* ranks the same eligible sets under alternative models and reports selection agreement,
  ties, winner changes and conflicts;
* plays each model forward in time with the production 45-minute entry-corridor
  reservation, and measures what the published candidates would have done under a
  conservative single-leg limit/market simulator on the captured bars;
* states three separate validation levels (A function equivalence, B pipeline eligibility
  equivalence, C historical decision equivalence) with explicit denominators.

What it is not: the simulator is a **hypothetical opportunity outcome**, not broker results.
Entries are single leg, the XAU/FX ladders, partial fills, risk legs, opposing exposure and
the live quote are not modelled, and the spreads are assumed constants. It is strictly causal:
no simulated outcome uses a price from a candle that had not completed by its exit or
valuation cutoff, and a trade whose history ends before its expiry is reported as censored,
never as a normal timeout. It supports "these models pick different trades and here is how
those trades would have fared under one fixed rule", never an expectancy claim.
Outcome-calibrated ranking needs clean broker results;
``auto_trade_results.realized_pips`` has existed only since 2026-10-08.
"""

from __future__ import annotations

import argparse
import dataclasses
import glob
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from app.analysis_client.freshness import FreshnessLimits, evaluate_freshness
from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.autotrade.arbitration import (
  ExecutionIntent,
  _collapse_theses,
  _rank,
  arbitrate_execution_intents,
  same_thesis,
)
from app.autotrade.entry_overlap import WINDOW_SECONDS, corridors_overlap
from app.autotrade.execution_intent import execution_intent_for_match
from app.autotrade.go_containment import containment_reason, structure_timeframe_of
from app.autotrade.go_opportunity_policy import (
  REVIEWED_SCOPES,
  AdapterRejection,
  build_strategy_match,
)

REPO = Path(__file__).resolve().parents[2]
CAPTURES = REPO / "analysis-engine" / "testdata"
SEED = 20261008
CONTRACT_TOLERANCE_PIPS = 3.0
CONFLICT_MARGIN = 0.15
DEV_FRACTION = 0.6
MIN_REALIZED_PER_STRATEGY = 30
RANK_FIELDS = ("score", "confluence", "structural_quality", "freshness", "intent_id")

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


def load_bars(capture: str) -> tuple[list[tuple], str]:
  document = json.loads((CAPTURES / capture).read_text())
  timeframe = "M1" if "M1" in document["timeframes"] else "M5"
  return [tuple(row) for row in document["timeframes"][timeframe]], timeframe


def pips(symbol: str, price_distance: float) -> float:
  return price_distance / PIP_SIZE[symbol]


# ---------------------------------------------------------------- admission (Level B)

_ENVELOPE_CACHE: dict[str, object] = {}


def parse_envelope(raw: dict | str):
  """The production parser, on the envelope the engine would publish."""
  text = raw if isinstance(raw, str) else json.dumps(raw)
  event = _ENVELOPE_CACHE.get(text)
  if event is None:
    event = parse_analysis_event(OpportunityTopic, text)
    _ENVELOPE_CACHE[text] = event
  return event


def freshness_limits() -> FreshnessLimits:
  from app.core.config import runtime_config
  authority = runtime_config.analysis.technical_authority
  return FreshnessLimits(authority.max_event_age_seconds, authority.max_delivery_lag_seconds)


def admit(envelope: dict | str, *, now: int, published_at: int | None = None, static: bool = True):
  """Run Algo Bot's admission on one creation envelope. Returns ``(match, None)`` when the
  opportunity becomes a StrategyMatch, else ``(None, reason_code)``.

  The order and the code are production's (``GoOpportunityPolicy.on_creation`` followed by
  the worker's static admission): reviewed scope, instrument containment, freshness,
  the adapter (timeframe, mandatory reaction confirmation and its recency, direction scope,
  evidence contract, expiry, geometry), then the enable switches and confluence floor.
  Nothing here knows what any strategy requires; the registry and the adapter decide.

  ``static=False`` stops after the adapter: ``GoOpportunityPolicy.on_creation`` records
  ``match_written`` there, and the worker's enable switches and confluence floor run later,
  per decision cycle - so Level B compares against creation only."""
  from app.autotrade.worker import static_admission_failure

  event = parse_envelope(envelope)
  payload = event.payload
  profile = REVIEWED_SCOPES.get(payload.strategy)
  if profile is None:
    return None, "scope_not_reviewed"
  contained = containment_reason(
    payload.symbol, payload.strategy,
    structure_timeframe_of(payload.structure_timeframe, tuple(item.code for item in payload.evidence)),
  )
  if contained is not None:
    return None, contained
  verdict = evaluate_freshness(
    observed_at=payload.created_at, expires_at=payload.expires_at, produced_at=event.produced_at,
    published_at=published_at, consumed_at=now, limits=freshness_limits(),
  )
  if not verdict.ok:
    return None, verdict.code
  try:
    match = build_strategy_match(event, profile=profile, now=now)
  except AdapterRejection as exc:
    return None, exc.code
  if static:
    failure = static_admission_failure(match, symbol=payload.symbol)
    if failure is not None:
      return None, failure.reason_code
  return match, None


def executable_now(match, *, bid: float, ask: float) -> bool:
  """The worker's own definition of "the quote may act on this match right now"."""
  from app.autotrade import worker
  from app.core import instrument_geometry

  spot = SimpleNamespace(bid=bid, ask=ask, price=bid, fresh=True)
  inst = instrument_geometry.instrument_runtime(match.symbol)
  return bool(worker._execution_quote_access(match, spot, match.symbol, inst)[1])


def to_intent(match, *, bid: float, spread: float, cycle_id: str) -> ExecutionIntent:
  return execution_intent_for_match(
    match, symbol=match.symbol, spot_price=bid,
    executable_now=executable_now(match, bid=bid, ask=bid + spread), cycle_id=cycle_id,
  )


# ---------------------------------------------------------------- the P1 first-cut rule (reporting only)

# The eligibility the first P1 evaluation used, kept ONLY to measure what the correction
# changed. Its strategy requirement list was read by a regex that missed the registry
# entries written positionally (supply, demand); that exact omission is reproduced here
# by name so the before/after difference is reproducible without parsing source.
LEGACY_REGEX_MISSED = frozenset({"supply", "demand"})
LEGACY_TOLERANCE_PIPS = 3.0


def legacy_eligible(row: dict, contained: dict) -> bool:
  symbol, strategy = row["symbol"], row["strategy"]
  profile = REVIEWED_SCOPES.get(strategy)
  required = profile is not None and profile.requires_reaction and strategy not in LEGACY_REGEX_MISSED
  if required and not row["has_reaction"]:
    return False
  observe_strategies, observe_structures = contained.get(symbol, (set(), set()))
  if strategy in observe_strategies or row.get("structure_tf") in observe_structures:
    return False
  if row["expires_at"] and row["expires_at"] <= row["bar_time"]:
    return False
  if row.get("confluence") is None or row.get("atr") is None:
    return False
  low, high, close = row["entry_low"], row["entry_high"], row["reference_price"]
  if not (math.isfinite(low) and math.isfinite(high) and high >= low > 0):
    return False
  invalidation = row["invalidation"]
  return not (
    (row["direction"] == "BUY" and close <= invalidation)
    or (row["direction"] == "SELL" and close >= invalidation)
  )


def legacy_containment() -> dict[str, tuple[set[str], set[str]]]:
  from app.autotrade.go_containment import observe_only_strategies, observe_only_structure_timeframes
  return {symbol: (set(observe_only_strategies(symbol)), set(observe_only_structure_timeframes(symbol))) for symbol in PIP_SIZE}


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

def build_cycles(rows: list[dict], *, spread_scale: float = 1.0) -> tuple[list[dict], dict]:
  """Decision cycles of eligible intents, plus the admission accounting.

  An opportunity is admitted once, when it is first seen (that is when Algo Bot's
  ``on_creation`` runs, with the Kafka event's own envelope); every later bar it is still
  emitted on re-uses that match while it has not expired. Per cycle the intents are built
  with the worker's own builder and "executable now" from the worker's own quote rule."""
  legacy_contained = legacy_containment()
  admitted: dict[str, tuple | None] = {}
  accounting = {
    "rows": 0, "unique_opportunities": 0, "no_envelope": 0, "admitted_unique": 0,
    "by_code": Counter(), "by_strategy_code": defaultdict(Counter), "by_symbol_code": defaultdict(Counter),
    "legacy_eligible_rows": 0, "corrected_eligible_rows": 0, "legacy_only_rows": 0, "corrected_only_rows": 0,
    "legacy_only_by_strategy_symbol": Counter(), "corrected_only_by_strategy_symbol": Counter(),
  }
  by_cycle: dict[tuple, list[dict]] = defaultdict(list)
  for row in rows:
    by_cycle[(row["capture"], row["symbol"], row["bar_tf"], row["bar_time"])].append(row)
  cycles = []
  for (capture, symbol, tf, bar_time), items in sorted(by_cycle.items(), key=lambda x: (x[0][1], x[0][3], x[0][2])):
    decision = bar_time + TF_SECONDS[tf]
    intents, source = [], {}
    spread = SPREAD[symbol] * spread_scale
    for row in items:
      accounting["rows"] += 1
      legacy_ok = legacy_eligible(row, legacy_contained)
      oid = row["id"]
      if oid not in admitted:
        accounting["unique_opportunities"] += 1
        envelope = row.get("envelope")
        if envelope is None:
          admitted[oid] = None
          accounting["no_envelope"] += 1
          accounting["by_code"]["no_envelope"] += 1
        else:
          match, code = admit(envelope, now=decision)
          admitted[oid] = (match, code)
          accounting["by_code"][code or "admitted"] += 1
          accounting["by_strategy_code"][row["strategy"]][code or "admitted"] += 1
          accounting["by_symbol_code"][symbol][code or "admitted"] += 1
          if match is not None:
            accounting["admitted_unique"] += 1
      state = admitted[oid]
      match = state[0] if state else None
      ok = match is not None and match.expires_at > decision
      if ok:
        intent = to_intent(match, bid=row["reference_price"], spread=spread, cycle_id=str(bar_time))
        intents.append(intent)
        source[intent.intent_id] = row
      accounting["legacy_eligible_rows"] += int(legacy_ok)
      accounting["corrected_eligible_rows"] += int(ok)
      if legacy_ok and not ok:
        accounting["legacy_only_rows"] += 1
        accounting["legacy_only_by_strategy_symbol"][f"{row['strategy']}|{symbol}"] += 1
      if ok and not legacy_ok:
        accounting["corrected_only_rows"] += 1
        accounting["corrected_only_by_strategy_symbol"][f"{row['strategy']}|{symbol}"] += 1
    cycles.append({"capture": capture, "symbol": symbol, "tf": tf, "bar_time": bar_time, "intents": intents, "rows": source,
                   "emitted": len(items), "ineligible": len(items) - len(intents)})
  return cycles, accounting


def split_time(cycles: list[dict]) -> dict[str, float]:
  """Chronological dev/OOS boundary per capture (first DEV_FRACTION of its time span)."""
  spans: dict[str, tuple[int, int]] = {}
  for cycle in cycles:
    low, high = spans.get(cycle["capture"], (cycle["bar_time"], cycle["bar_time"]))
    spans[cycle["capture"]] = (min(low, cycle["bar_time"]), max(high, cycle["bar_time"]))
  return {capture: low + DEV_FRACTION * (high - low) for capture, (low, high) in spans.items()}


# ---------------------------------------------------------------- hypothetical outcome

def _outcome(status: str, reason: str, *, filled: bool = False, r: float | None = 0.0, **times) -> dict:
  return {"filled": filled, "r": r, "reason": reason, "status": status, **times}


def usable(outcome: dict) -> bool:
  """A filled trade with a causally valid, finished result (counts toward expectancy)."""
  return bool(outcome["filled"] and outcome["r"] is not None and outcome["status"] == "complete")


def simulate(row: dict, bars: list[tuple], tf: str, *, spread_scale: float = 1.0) -> dict:
  """One candidate under one fixed rule, strictly causal. Bars are bid; ``tf`` is the
  timeframe of ``bars`` (a candle opening at ``t`` is complete at ``t + seconds(tf)``).

  Returns ``{filled, r, reason, status, ...}`` where ``status`` is

  * ``complete`` - the trade ended on a stop or target, or was valued at its effective expiry
    with the history covering that expiry;
  * ``censored`` - the trade was still open when the available history ended before its
    effective expiry: ``r`` is a mark at the last completed candle, labelled, never a timeout;
  * ``unavailable`` - the trade was filled but no completed candle exists to value it:
    ``r`` is None. A price is never invented;
  * ``not_filled`` - it never filled, or its envelope forbade the trade.

  Causality: a candle is read only once it is complete (``t + seconds <= cutoff``), where the
  cutoff is the effective expiry. A candle that opens before the expiry and closes after it
  is not used, because its extremes may lie after the cutoff. Inside one candle the stop acts
  before targets (the sequence is unknowable). Timestamps are reported: ``entry_at``,
  ``closed_at`` (the candle close the result depends on), ``last_observation_at``,
  ``effective_expiry``."""
  symbol, side = row["symbol"], row["direction"]
  pip, spread = PIP_SIZE[symbol], SPREAD[symbol] * spread_scale
  floor, cap = row.get("stop_floor_pips"), row.get("stop_cap_pips")
  if floor is None or cap is None:
    return _outcome("not_filled", "no_stop_envelope")
  low, high, close = row["entry_low"], row["entry_high"], row["reference_price"]
  tolerance = CONTRACT_TOLERANCE_PIPS * pip
  inside = low - tolerance <= close <= high + tolerance
  decision = row["bar_time"] + TF_SECONDS[row["bar_tf"]]
  expiry = min(row["expires_at"] or decision + 86400, decision + 86400)
  seconds = TF_SECONDS[tf]
  proximal = high if side == "BUY" else low
  entry_ref = close if inside else proximal
  stop_pips = abs(entry_ref - row["invalidation"]) / pip
  if stop_pips > cap + 1e-9:
    return _outcome("not_filled", "stop_above_cap")
  stop_pips = max(stop_pips, float(floor))
  distance = stop_pips * pip
  index = next((i for i, bar in enumerate(bars) if bar[0] >= decision), None)
  if index is None:
    return _outcome("not_filled", "no_bars", effective_expiry=expiry)
  # History covers the expiry when a candle opens at/after it or the last candle closes at/after it.
  covers_expiry = bars[-1][0] >= expiry or bars[-1][0] + seconds >= expiry
  buy = side == "BUY"
  entry = None
  entry_at = None
  position = 0.0
  r_total = 0.0
  stop = None
  stage = 0
  last_complete = None  # close time of the last completed candle read after entry
  last_close = None     # that candle's close price: the only valuation price allowed
  first_bar_time = bars[index][0]

  def done(status, reason, r, closed_at, observed_at=None):
    return _outcome(status, reason, filled=True, r=None if r is None else round(r, 4), entry_at=entry_at,
                    closed_at=closed_at, last_observation_at=closed_at if observed_at is None else observed_at,
                    effective_expiry=expiry)

  for bar in bars[index:]:
    t, o, h, l, c = bar[0], bar[1], bar[2], bar[3], bar[4]
    closes_at = t + seconds
    if closes_at > expiry:
      break  # not complete by the cutoff: its extremes may lie after it
    if entry is None:
      if (buy and c <= row["invalidation"]) or (not buy and c >= row["invalidation"]):
        return _outcome("not_filled", "invalidated_before_fill", effective_expiry=expiry)
      if inside and t == first_bar_time:
        entry = o + spread if buy else o
      elif not inside:
        if buy and l + spread <= proximal:
          entry = proximal
        elif not buy and h >= proximal:
          entry = proximal
      if entry is None:
        continue
      entry_at = t
      position = 1.0
      stop = entry - distance if buy else entry + distance
      # Conservative: on the fill candle only the stop can act.
      if (buy and l <= stop) or (not buy and h + spread >= stop):
        return done("complete", "stopped_on_fill_bar", -1.0, closes_at)
      last_complete, last_close = closes_at, c
      continue
    # In a trade. Conservative ordering inside one candle: stop first.
    stop_hit = (l <= stop) if buy else (h + spread >= stop)
    if stop_hit:
      r_total += position * ((stop - entry) / distance if buy else (entry - stop) / distance)
      return done("complete", "stop" if stage == 0 else "be_or_trail_stop", r_total, closes_at)
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
        return done("complete", "all_targets", r_total, closes_at)
    last_complete, last_close = closes_at, c
  if entry is None:
    # Never filled inside the window. A history that ended early leaves this unknowable.
    return _outcome("not_filled" if covers_expiry else "censored_unfilled",
                    "unfilled_until_expiry" if covers_expiry else "history_ended_before_fill",
                    effective_expiry=expiry)
  # Open at the cutoff: value at the last completed candle at or before it - never a later one.
  if last_complete is None or last_close is None:
    # Filled on the very last candle read: nothing after the fill is complete to value against.
    return _outcome("unavailable", "no_completed_candle_after_fill", filled=True, r=None, entry_at=entry_at,
                    effective_expiry=expiry)
  mark = ((last_close - entry) if buy else (entry - last_close)) / distance
  value = r_total + position * mark
  if covers_expiry:
    return done("complete", "timeout", value, expiry, observed_at=last_complete)
  return done("censored", "history_ended_before_expiry", value, last_complete)


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
        # Which rank field separated the two best executable intents. "intent_id" means every
        # earlier field tied and the order was settled by the identifier alone.
        pair = sorted((model.key(i) for i in executable), key=lambda key: key)[:2]
        label = RANK_FIELDS[next((n for n in range(1, 6) if pair[0][n] != pair[1][n]), 5) - 1]
        stats[f"decided_by_{label}"] += 1
        if model is not baseline and baseline_winner(base_winners) != winners[0].intent_id:
          stats[f"differs_decided_by_{label}"] += 1
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
      "atr_pips": atr_pips, "filled": result["filled"], "usable": usable(result), "status": result["status"],
      "r": result["r"], "reason": result["reason"],
      "period": "dev" if cycle["bar_time"] <= boundary[cycle["capture"]] else "oos",
    })

  def agg(subset: list[dict]) -> dict:
    filled = [x["r"] for x in subset if x["usable"]]
    return {"n": len(subset), "stop_above_cap_rate": round(sum(1 for x in subset if x["reason"] == "stop_above_cap") / len(subset), 3) if subset else None,
            "fill_rate": round(sum(1 for x in subset if x["filled"]) / len(subset), 3) if subset else None,
            "censored": sum(1 for x in subset if x["status"] == "censored"),
            "unavailable": sum(1 for x in subset if x["status"] == "unavailable"),
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
  filled = [x for x in records if x["usable"]]
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
    "outcome_status": dict(Counter(x["status"] for x in records)),
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


def cluster_bootstrap_ci(items: list[tuple[str, float]], rng: random.Random, n: int = 2000) -> tuple[float, float] | None:
  """95% interval of the mean when observations are not independent: whole clusters (here a
  symbol-day, i.e. one market episode) are resampled, not individual pairs. Neighbouring
  candidates on the same bars share their outcome, so the pair-level interval overstates
  certainty."""
  clusters: dict[str, list[float]] = defaultdict(list)
  for key, value in items:
    clusters[key].append(value)
  if len(clusters) < 3:
    return None
  groups = list(clusters.values())
  means = []
  for _ in range(n):
    sample = [value for group in rng.choices(groups, k=len(groups)) for value in group]
    means.append(statistics.fmean(sample))
  means.sort()
  return means[int(0.025 * n)], means[int(0.975 * n)]


def summarize(trades: list[dict], outcomes: dict) -> dict:
  filled = [outcomes[t["id"]] for t in trades if usable(outcomes[t["id"]])]
  r = [o["r"] for o in filled]
  statuses = Counter(outcomes[t["id"]]["status"] for t in trades)
  return {
    "published": len(trades), "filled": len(filled),
    "censored": statuses.get("censored", 0), "unavailable": statuses.get("unavailable", 0),
    "fill_rate": round(len(filled) / len(trades), 3) if trades else None,
    "mean_r": round(statistics.fmean(r), 4) if r else None,
    "win_rate": round(sum(1 for x in r if x > 0) / len(r), 3) if r else None,
    "sum_r": round(sum(r), 2),
  }


def model_d_assessment(results_csv: str) -> dict:
  """Model D (outcome-calibrated ranking) needs verified realized outcomes per strategy.
  Counts what exists; it never fits anything on fewer than ``MIN_REALIZED_PER_STRATEGY``."""
  if not results_csv:
    return {"evaluated": False, "reason": "no production results supplied (--production-results)"}
  import csv
  with open(results_csv) as handle:
    rows = list(csv.reader(handle))
  per_strategy: Counter = Counter()
  total = with_realized = 0
  for group_id, setup, symbol, result_pips, realized_pips, closed_at in rows:
    total += 1
    if realized_pips not in {"", "\\N"}:
      with_realized += 1
      per_strategy[setup] += 1
  enough = {name: n for name, n in per_strategy.items() if n >= MIN_REALIZED_PER_STRATEGY}
  return {
    "evaluated": False, "closed_trades": total, "with_realized_pips": with_realized,
    "realized_by_strategy": dict(per_strategy.most_common()),
    "strategies_with_enough_outcomes": enough, "minimum_per_strategy": MIN_REALIZED_PER_STRATEGY,
    "reason": (
      "insufficient verified realized outcomes: realized_pips exists only for trades closed since the executor began "
      "publishing it (2026-10-08), and result_pips is the highest target reached, not a realized result"
      if not enough else "enough outcomes for some strategies - a calibration study is possible but not part of this evaluation"
    ),
  }


def registry_table() -> list[dict]:
  """One row per registered Go strategy, straight from the reviewed-scope registry."""
  return [
    {
      "strategy": name, "requires_reaction": profile.requires_reaction,
      "timeframes": sorted(profile.allowed_timeframes), "direction": profile.direction,
    }
    for name, profile in sorted(REVIEWED_SCOPES.items())
  ]


def admission_report(accounting: dict) -> dict:
  """What Algo Bot's admission did to the emitted candidates, and what the correction changed
  relative to the first P1 evaluation's regex-based eligibility."""
  return {
    "rows": accounting["rows"], "unique_opportunities": accounting["unique_opportunities"],
    "admitted_unique": accounting["admitted_unique"], "no_envelope": accounting["no_envelope"],
    "exclusion_reasons": dict(accounting["by_code"].most_common()),
    "by_strategy": {k: dict(v.most_common()) for k, v in sorted(accounting["by_strategy_code"].items())},
    "by_symbol": {k: dict(v.most_common()) for k, v in sorted(accounting["by_symbol_code"].items())},
    "correction_effect": {
      "first_cut_eligible_rows": accounting["legacy_eligible_rows"],
      "corrected_eligible_rows": accounting["corrected_eligible_rows"],
      "eligible_before_not_after_rows": accounting["legacy_only_rows"],
      "eligible_after_not_before_rows": accounting["corrected_only_rows"],
      "eligible_before_not_after_by_strategy_symbol": dict(accounting["legacy_only_by_strategy_symbol"].most_common()),
      "eligible_after_not_before_by_strategy_symbol": dict(accounting["corrected_only_by_strategy_symbol"].most_common()),
    },
  }


def level_a(decision: dict, cycles: list[dict]) -> dict:
  """Function equivalence: the offline arbitration reproduces the production function on
  identical inputs. Says nothing about whether the inputs equal production's."""
  with_intents = sum(1 for c in cycles if c["intents"])
  return {
    "claim": "offline model A == production arbitrate_execution_intents on identical intents",
    "cycles_compared": with_intents, "disagreements": decision["consistency_failures_vs_production"],
    "denominator": "cycles with at least one admitted intent",
  }


def _load_jsonl(path: str) -> list[dict]:
  with open(path) as handle:
    return [json.loads(line) for line in handle if line.strip()]


def _production_outcome(decisions: list[dict]) -> tuple[str, str] | None:
  """The admission verdict Algo Bot recorded for an opportunity (its on_creation decision)."""
  for decision in decisions:
    if decision.get("mode") == "go" and decision.get("outcome") in {"match_written", "rejected", "not_adapted"}:
      return decision["outcome"], decision["reason"]
  return None


def level_b(production_export: str, since: int = 0) -> dict:
  """Pipeline eligibility equivalence: Algo Bot's admission, run offline on the creation
  envelope production actually stored, at the time production actually decided, against the
  decision production recorded. Same code, same input: a disagreement is configuration or
  code that changed after production decided.

  Reported twice: over the whole export (the admission code and the containment lists changed
  during the window, so version drift is included and labelled) and over the decisions made
  at or after ``since``, when the deployed admission code and containment equal the
  repository's."""
  if not production_export:
    return {"available": False, "reason": "no production export supplied (--production-export)"}
  scopes = {"all_history": Counter(), "same_code_version": Counter()}
  pairs = {name: Counter() for name in scopes}
  agreed = {name: Counter() for name in scopes}
  for record in _load_jsonl(production_export):
    envelope = record.get("envelope")
    recorded = _production_outcome(record.get("decisions") or [])
    decided_at = next((d["at"] for d in (record.get("decisions") or []) if d.get("mode") == "go"), None)
    names = ["all_history"] + (["same_code_version"] if decided_at is not None and int(decided_at) >= since else [])
    for name in names:
      scopes[name]["exported"] += 1
    if not envelope or recorded is None or decided_at is None:
      for name in names:
        scopes[name]["unavailable_no_envelope_or_decision"] += 1
      continue
    try:
      match, code = admit(envelope, now=int(decided_at), static=False)
    except Exception:
      for name in names:
        scopes[name]["unavailable_unparseable_envelope"] += 1
      continue
    if match is None and code in {"event_too_old", "delivery_lag_exceeded", "opportunity_expired"} and (
      (envelope.get("payload") or {}).get("recovered_at") is not None
    ):
      # A recovery event is admitted past the age limit only when Redis confirms the id is
      # still in the engine's live book: state this offline run cannot see.
      for name in names:
        scopes[name]["unavailable_recovery_event_needs_redis_live_book"] += 1
      continue
    offline = ("match_written", "go_live") if match is not None else ("rejected_or_not_adapted", code)
    expected = recorded if recorded[0] == "match_written" else ("rejected_or_not_adapted", recorded[1])
    for name in names:
      scopes[name]["compared"] += 1
      if offline == expected:
        scopes[name]["agree"] += 1
        agreed[name][recorded[1]] += 1
      else:
        scopes[name]["disagree"] += 1
        pairs[name][f"production {recorded[0]}|{recorded[1]}  vs  offline {offline[0]}|{offline[1]}"] += 1
  out = {"available": True, "claim": "offline admission == production admission on production's own envelopes",
         "same_code_version_since": since}
  for name, stats in scopes.items():
    compared = stats["compared"]
    out[name] = {
      **{k: stats[k] for k in ("exported", "compared", "agree", "disagree")},
      "unavailable": {k: v for k, v in stats.items() if k.startswith("unavailable")},
      "agreement_rate": round(stats["agree"] / compared, 4) if compared else None,
      "agreed_by_production_reason": dict(agreed[name].most_common()),
      "disagreements": dict(pairs[name].most_common(20)),
    }
  return out


def level_c(rows: list[dict], cycles: list[dict], production_export: str, production_plans: str) -> dict:
  """Historical decision equivalence: do the reconstructed candidates and the offline
  selection reproduce what production actually saw and published?

  Only opportunities whose id exists in both the replay and the production ledger can be
  compared. The replay runs the current engine build on the captured bars, so most ids differ;
  that is reported as unavailable, never as a match."""
  out: dict = {"claim": "reconstructed candidates and selection == what production recorded and published"}
  replay_ids = {row["id"] for row in rows}
  if not production_export:
    return {**out, "available": False, "reason": "no production export supplied"}
  records = {r["opportunity_id"]: r for r in _load_jsonl(production_export)}
  common = sorted(replay_ids & set(records))
  out["candidates"] = {
    "replay_unique_ids": len(replay_ids), "production_exported_ids": len(records), "ids_in_both": len(common),
    "unavailable_replay_only": len(replay_ids) - len(common),
  }
  by_id = {row["id"]: row for row in rows}
  identical = Counter()
  for oid in common:
    row, prod = by_id[oid], records[oid]
    payload = (prod.get("envelope") or {}).get("payload") or {}
    fields = {
      "direction": payload.get("direction") == row["direction"],
      "entry": bool(payload.get("entry")) and abs(payload["entry"]["low"] - row["entry_low"]) < 1e-9 and abs(payload["entry"]["high"] - row["entry_high"]) < 1e-9,
      "quality": bool(payload.get("quality")) and abs(payload["quality"]["overall"] - row["quality"]) < 1e-9,
      "invalidation": bool(payload.get("invalidation")) and abs(payload["invalidation"]["price"] - row["invalidation"]) < 1e-9,
      "timeframe": (payload.get("timeframe") or "").upper() == row["observed_tf"],
    }
    identical["compared"] += 1
    for name, ok in fields.items():
      identical[name] += int(ok)
    identical["all_fields"] += int(all(fields.values()))
  out["candidate_field_identity"] = dict(identical)
  # Admission decision production made vs the offline admission of the same id.
  admitted_ids = {intent.intent_id.removeprefix("strategy:go_") for c in cycles for intent in c["intents"]}
  agree = Counter()
  for oid in common:
    recorded = _production_outcome(records[oid].get("decisions") or [])
    if recorded is None:
      agree["unavailable_no_recorded_decision"] += 1
      continue
    agree["compared"] += 1
    agree["agree"] += int((recorded[0] == "match_written") == (oid in admitted_ids))
  out["admission_on_shared_ids"] = dict(agree)
  # Published plans: only plans that filled leave a durable record.
  if production_plans:
    import csv
    with open(production_plans) as handle:
      filled = {line[0].removeprefix("v8:go_"): line for line in csv.reader(handle)}
    in_replay = sorted(set(filled) & replay_ids)
    out["published_plans"] = {
      "production_filled_plans": len(filled), "also_in_replay": len(in_replay),
      "unavailable": "plans that were published but never filled leave no durable record; plans absent from the replay were produced by a different engine build or arrived outside the captures",
    }
    out["selection_equivalence"] = {
      "compared": len(in_replay),
      "note": "a denominator this small cannot establish that offline selection reproduces production's publication decisions",
    }
  out["available"] = True
  return out


def run(dump_dir: str, *, production_export: str = "", production_plans: str = "", production_results: str = "", code_since: int = 0) -> dict:
  rng = random.Random(SEED)
  rows = load_rows(dump_dir)
  cycles, accounting = build_cycles(rows)
  boundary = split_time(cycles)
  # Model B is fitted on the development window only, on the opportunities Algo Bot admits.
  dev_rows = [
    cycle["rows"][intent.intent_id]
    for cycle in cycles if cycle["bar_time"] <= boundary[cycle["capture"]]
    for intent in cycle["intents"]
  ]
  table = _percentile_table(dev_rows)
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
      bars, series_tf = bars_for(row["capture"])
      outcome_cache[key] = simulate(row, bars, series_tf, spread_scale=scale)
    return outcome_cache[key]

  report: dict = {
    "seed": SEED, "captures": sorted({r["capture"] for r in rows}), "candidates": len(rows),
    "cycles": len(cycles), "dev_fraction": DEV_FRACTION,
    "eligible_intents": sum(len(c["intents"]) for c in cycles),
    "ineligible_emitted": sum(c["ineligible"] for c in cycles),
    "registry": registry_table(),
    "admission": admission_report(accounting),
    "decision": decision_metrics(cycles, models, boundary),
    "play_forward": {}, "paired_divergence": {},
  }
  report["thesis_audit"] = thesis_audit(cycles, rng)
  report["model_d"] = model_d_assessment(production_results)
  report["validation"] = {
    "level_a_function_equivalence": level_a(report["decision"], cycles),
    "level_b_pipeline_eligibility_equivalence": level_b(production_export, code_since),
    "level_c_historical_decision_equivalence": level_c(rows, cycles, production_export, production_plans),
  }
  report["candidate_outcomes"] = candidate_outcomes(cycles, boundary, outcome, rng)

  played = {m.name: play_forward(cycles, m) for m in models}
  streams = {name: trades for name, (trades, _) in played.items()}
  selection = report["validation"]["level_c_historical_decision_equivalence"].get("selection_equivalence")
  if selection is not None and production_plans:
    import csv
    with open(production_plans) as handle:
      filled = {line[0].removeprefix("v8:go_") for line in csv.reader(handle)}
    published_a = {t["id"] for t in streams[models[0].name]}
    shared = filled & {r["id"] for r in rows}
    selection["model_a_published_offline"] = len(shared & published_a)
    selection["model_a_did_not_publish_offline"] = len(shared - published_a)
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
      if usable(held) and usable(tried):
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
    clustered: list[tuple[str, float]] = []
    unfilled = Counter()
    for cycle, wa, wb in pairs:
      ra, rb = outcome(cycle["rows"][wa.intent_id]), outcome(cycle["rows"][wb.intent_id])
      period = "dev" if cycle["bar_time"] <= boundary[cycle["capture"]] else "oos"
      if usable(ra) and usable(rb):
        deltas["all"].append(rb["r"] - ra["r"])
        deltas[period].append(rb["r"] - ra["r"])
        clustered.append((f"{wa.symbol}|{cycle['bar_time'] // 86400}", rb["r"] - ra["r"]))
      else:
        unfilled["incumbent_unusable" if not usable(ra) and usable(rb) else
                 "alternative_unusable" if usable(ra) and not usable(rb) else "neither_usable"] += 1
    cluster_ci = cluster_bootstrap_ci(clustered, rng)
    result = {
      "pairs": len(pairs), "unfilled": dict(unfilled),
      "episodes_symbol_day": len({key for key, _ in clustered}),
      "cluster_ci95_symbol_day": None if cluster_ci is None else [round(cluster_ci[0], 3), round(cluster_ci[1], 3)],
      "reading": "pair-level interval treats neighbouring candidates as independent; the cluster interval resamples whole symbol-days",
    }
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
  parser.add_argument("--production-export", default="", help="JSONL of production opportunities with their creation envelope and recorded decisions")
  parser.add_argument("--production-results", default="", help="CSV of closed production trades (group_id,setup,symbol,result_pips,realized_pips,closed_at)")
  parser.add_argument("--code-since", type=int, default=0, help="unix time from which the deployed admission code and containment equal this repository's")
  parser.add_argument("--production-plans", default="", help="CSV of production plans that filled (group_id,symbol,setup,direction,filled_at,fills)")
  args = parser.parse_args()
  report = run(args.dump_dir, production_export=args.production_export, production_plans=args.production_plans, production_results=args.production_results, code_since=args.code_since)
  text = json.dumps(report, indent=2, sort_keys=True)
  if args.out:
    Path(args.out).write_text(text + "\n")
  print(text)


if __name__ == "__main__":
  main()

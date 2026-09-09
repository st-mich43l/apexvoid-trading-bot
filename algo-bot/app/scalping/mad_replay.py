"""MAD-2: observe-only phase × session expectancy on the scalp replay lab.

Counterfactual only — applies ``mad_hard_gate`` as a research filter on top of
math gates / paper fills. Does **not** change live publish or allow/block.

Lab events must carry an explicit phase stamp:

  measured.mad.phase  or  measured.mad_phase

Never invent a phase from price alone here (that is MAD-0 classify on live
tape). Missing stamp → ``unclear`` (neutral gate).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from app.analysis.mad_phase import (
  MAD_VERSION,
  PHASE_UNCLEAR,
  PHASES,
  MadPhaseSnapshot,
  compute_mad_affinity,
  mad_hard_gate,
)
from app.scalping.replay import aggregate_report, calibration_report, split_dataset
from app.scalping.replay_lab import LabEvent, load_lab_events, replay_lab_event


# Map lab / HFS archetype aliases onto mad_hard_gate strategy keys.
_STRATEGY_FOR_GATE: dict[str, str] = {
  "liquidity_sweep_reversal": "liquidity_sweep_reversal",
  "range_sweep": "range_sweep",
  "hfs_range_sweep": "range_sweep",
  "range_edge_mean_reversion": "range_edge_mean_reversion",
  "range_edge": "range_edge",
  "impulse_pullback_continuation": "impulse_pullback_continuation",
  "impulse_pullback": "impulse_pullback",
  "breakout_retest": "breakout_retest",
  "key_level_reaction": "structural_reaction",
  "key level reaction": "structural_reaction",
  "supply zone": "structural_reaction",
  "order block": "structural_reaction",
  "fvg": "structural_reaction",
  "ifvg": "structural_reaction",
  "zone reaction": "structural_reaction",
}


def resolve_event_mad_snapshot(event: LabEvent) -> MadPhaseSnapshot | None:
  """Read a stamped MAD v2 snapshot only — never re-classify Asia box offline.

  Fixtures carry the full ``MadPhaseSnapshot.to_dict()`` payload under
  ``measured.mad``. Older fixtures that only stamped a bare
  ``measured.mad.phase``/``measured.mad_phase`` string still resolve to a
  phase-only snapshot (no direction/confidence — v1-shaped, mad_version
  absent, so replay never silently credits v2 evidence a v1 fixture never
  captured).
  """
  measured = dict(event.measured or {})
  mad = measured.get("mad")
  if isinstance(mad, dict) and mad:
    snapshot = MadPhaseSnapshot.from_dict(mad)
    if snapshot.phase in PHASES:
      return snapshot
  legacy_phase = measured.get("mad_phase")
  if legacy_phase is not None:
    phase = str(legacy_phase or "").casefold()
    if phase in PHASES:
      return MadPhaseSnapshot(
        phase=phase, asia=None, range_quality_atr=None, price_vs_asia=None,
        sweep_side=None, reclaim=False, reason_code="",
      )
  return None


def resolve_event_mad_phase(event: LabEvent) -> str:
  """Read stamped phase only — do not re-classify Asia box offline."""
  snapshot = resolve_event_mad_snapshot(event)
  return snapshot.phase if snapshot is not None else PHASE_UNCLEAR


def gate_strategy_key(strategy: str) -> str:
  key = str(strategy or "").strip().casefold()
  return _STRATEGY_FOR_GATE.get(key, key)


def _affinity_bucket(snapshot: MadPhaseSnapshot | None, direction_score: float) -> str:
  """aligned / neutral / opposed (§9/§18) — never derived from confidence or
  strategy_score, only from whether the phase's own directional read agrees
  with the candidate's direction."""
  if snapshot is None or snapshot.phase == PHASE_UNCLEAR:
    return "neutral"
  directional = snapshot.manipulation_direction or snapshot.expansion_direction
  if directional is None:
    return "neutral"
  return "aligned" if direction_score >= 1.0 else "opposed"


def _confidence_bucket(confidence: float) -> str:
  if confidence >= 0.66:
    return "high"
  if confidence >= 0.33:
    return "medium"
  return "low"


def replay_lab_event_with_mad(event: LabEvent) -> dict[str, Any]:
  """Paper replay row + MAD-1 would_gate counterfactual fields, plus MAD v2
  continuous affinity/confidence and their aligned/neutral/opposed and
  low/medium/high buckets (§9/§10/§18)."""
  row = replay_lab_event(event)
  snapshot = resolve_event_mad_snapshot(event)
  phase = snapshot.phase if snapshot is not None else PHASE_UNCLEAR
  preview = mad_hard_gate(phase=phase, strategy=gate_strategy_key(event.strategy))
  baseline_traded = row.get("outcome") not in {None, "blocked"}
  would_block = bool(preview.would_block)

  affinity = (
    compute_mad_affinity(
      snapshot, direction=event.direction,
      strategy=gate_strategy_key(event.strategy),
    )
    if snapshot is not None
    else None
  )
  row.update({
    "mad_phase": phase,
    "mad_would_block": would_block,
    "mad_gate_reason": preview.reason_code,
    "mad_kept": bool(baseline_traded and not would_block),
    "mad_filtered": bool(baseline_traded and would_block),
    "mad_version": snapshot.mad_version if snapshot is not None else None,
    "mad_confidence": snapshot.confidence if snapshot is not None else 0.0,
    "mad_direction": (
      (snapshot.manipulation_direction or snapshot.expansion_direction)
      if snapshot is not None else None
    ),
    "mad_affinity": affinity.final if affinity is not None else 0.0,
    "mad_affinity_bucket": _affinity_bucket(
      snapshot, affinity.direction_score if affinity is not None else 0.0,
    ),
    "mad_confidence_bucket": _confidence_bucket(
      snapshot.confidence if snapshot is not None else 0.0,
    ),
    "stop_pips": (
      abs(event.price - event.stop_price) / event.pip_size
      if event.stop_price is not None and event.pip_size > 0 else None
    ),
    "target_pips": (
      abs(event.target_price - event.price) / event.pip_size
      if event.target_price is not None and event.pip_size > 0 else None
    ),
  })
  return row


def _bucket_key(row: dict[str, Any]) -> tuple[str, str, str]:
  return (
    str(row.get("mad_phase") or PHASE_UNCLEAR),
    str(row.get("session") or "unknown"),
    str(row.get("archetype") or "unknown"),
  )


def _slice_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
  traded = [r for r in rows if r.get("outcome") not in {None, "blocked"}]
  agg = aggregate_report(traded)
  return {
    "n_events": len(rows),
    "n_traded": len(traded),
    "n_blocked_math": sum(1 for r in rows if r.get("outcome") == "blocked"),
    "expectancy_r": float(agg.get("expectancy_r") or 0.0),
    "win_rate": float(agg.get("win_rate") or 0.0),
    "profit_factor": float(agg.get("profit_factor") or 0.0),
    "count": int(agg.get("count") or 0),
  }


def phase_session_strategy_table(
  rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
  """Expectancy by phase × session × strategy: baseline vs MAD-kept."""
  groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
  for row in rows:
    groups[_bucket_key(row)].append(row)

  table: list[dict[str, Any]] = []
  for (phase, session, strategy), bucket in sorted(groups.items()):
    baseline = [r for r in bucket if r.get("outcome") not in {None, "blocked"}]
    kept = [r for r in baseline if not r.get("mad_would_block")]
    filtered = [r for r in baseline if r.get("mad_would_block")]
    base_agg = aggregate_report(baseline)
    kept_agg = aggregate_report(kept)
    filtered_agg = aggregate_report(filtered)
    base_exp = float(base_agg.get("expectancy_r") or 0.0)
    kept_exp = float(kept_agg.get("expectancy_r") or 0.0)
    table.append({
      "phase": phase,
      "session": session,
      "strategy": strategy,
      "n_events": len(bucket),
      "baseline": {
        "n": len(baseline),
        "expectancy_r": base_exp,
        "win_rate": float(base_agg.get("win_rate") or 0.0),
      },
      "mad_kept": {
        "n": len(kept),
        "expectancy_r": kept_exp,
        "win_rate": float(kept_agg.get("win_rate") or 0.0),
      },
      "mad_filtered": {
        "n": len(filtered),
        "expectancy_r": float(filtered_agg.get("expectancy_r") or 0.0),
        "win_rate": float(filtered_agg.get("win_rate") or 0.0),
      },
      "delta_expectancy_r_kept_minus_baseline": (
        kept_exp - base_exp if kept and baseline else 0.0
      ),
    })
  return table


def _median(values: Sequence[float]) -> float:
  ordered = sorted(values)
  n = len(ordered)
  if n == 0:
    return 0.0
  mid = n // 2
  return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _extended_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
  """§18 full metric set — trade count, win rate, avg/median R, expectancy
  R, profit factor, MAE, MFE, avg stop, avg target — merging the fields
  aggregate_report already computes with the ones it doesn't (median R,
  avg stop/target pips) rather than recomputing win_rate/profit_factor a
  second way (§20)."""
  agg = aggregate_report(rows)
  net_r = [float(r.get("net_r") or 0.0) for r in rows]
  stops = [float(r["stop_pips"]) for r in rows if r.get("stop_pips") is not None]
  targets = [float(r["target_pips"]) for r in rows if r.get("target_pips") is not None]
  return {
    "trade_count": int(agg.get("count") or 0),
    "win_rate": float(agg.get("win_rate") or 0.0),
    "avg_r": float(agg.get("expectancy_r") or 0.0),
    "median_r": _median(net_r),
    "expectancy_r": float(agg.get("expectancy_r") or 0.0),
    "profit_factor": float(agg.get("profit_factor") or 0.0),
    "avg_mae_pips": float(agg.get("avg_mae_pips") or 0.0),
    "avg_mfe_pips": float(agg.get("avg_mfe_pips") or 0.0),
    "avg_stop_pips": (sum(stops) / len(stops)) if stops else None,
    "avg_target_pips": (sum(targets) / len(targets)) if targets else None,
  }


def mad_affinity_confidence_table(
  rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
  """§18: full metric set per (symbol, strategy, direction, session, phase,
  affinity_bucket, confidence_bucket) cell, baseline-traded rows only —
  the richer replacement for phase_session_strategy_table's phase-only
  bucketing, directional and confidence-aware.
  """
  def _key(row: dict[str, Any]) -> tuple[str, str, str, str, str, str, str]:
    return (
      str(row.get("symbol") or "unknown"),
      str(row.get("archetype") or "unknown"),
      str(row.get("direction") or "unknown"),
      str(row.get("session") or "unknown"),
      str(row.get("mad_phase") or PHASE_UNCLEAR),
      str(row.get("mad_affinity_bucket") or "neutral"),
      str(row.get("mad_confidence_bucket") or "low"),
    )

  traded = [r for r in rows if r.get("outcome") not in {None, "blocked"}]
  groups: dict[tuple[str, str, str, str, str, str, str], list[dict[str, Any]]] = (
    defaultdict(list)
  )
  for row in traded:
    groups[_key(row)].append(row)

  table: list[dict[str, Any]] = []
  for (symbol, strategy, direction, session, phase, affinity_bucket, confidence_bucket), bucket in (
    sorted(groups.items())
  ):
    table.append({
      "symbol": symbol,
      "strategy": strategy,
      "direction": direction,
      "session": session,
      "phase": phase,
      "affinity_bucket": affinity_bucket,
      "confidence_bucket": confidence_bucket,
      **_extended_metrics(bucket),
    })
  return table


def mad_alignment_comparison(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
  """§9/§18: MAD-aligned vs MAD-neutral vs MAD-opposed, baseline-traded rows
  only. Never promoted on win rate alone — expectancy_r and profit_factor
  travel alongside it in the same cell."""
  traded = [r for r in rows if r.get("outcome") not in {None, "blocked"}]
  out: dict[str, Any] = {}
  for bucket in ("aligned", "neutral", "opposed"):
    bucket_rows = [r for r in traded if r.get("mad_affinity_bucket") == bucket]
    out[bucket] = _extended_metrics(bucket_rows)
  return out


def strategy_baselines(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
  """Range Sweep / Impulse baselines vs MAD-kept (research canvas MAD-2)."""
  def _family(name: str) -> str:
    key = name.casefold()
    if key in {
      "liquidity_sweep_reversal", "range_sweep", "hfs_range_sweep",
      "range_edge_mean_reversion", "range_edge",
    }:
      return "range_family"
    if key in {
      "impulse_pullback_continuation", "impulse_pullback",
    }:
      return "impulse_family"
    return "other"

  out: dict[str, Any] = {}
  for family in ("range_family", "impulse_family"):
    fam_rows = [r for r in rows if _family(str(r.get("archetype") or "")) == family]
    baseline = [r for r in fam_rows if r.get("outcome") not in {None, "blocked"}]
    kept = [r for r in baseline if not r.get("mad_would_block")]
    kept_agg = aggregate_report(kept)
    out[family] = {
      "baseline": _slice_metrics(fam_rows),
      "mad_kept": {
        "n_traded": len(kept),
        "expectancy_r": float(kept_agg.get("expectancy_r") or 0.0),
        "win_rate": float(kept_agg.get("win_rate") or 0.0),
      },
    }
  return out


def mad_expectancy_report(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
  """Full MAD-2 report. Holdout is reported separately — never tune on it."""
  traded = [r for r in rows if r.get("outcome") not in {None, "blocked"}]
  kept = [r for r in traded if not r.get("mad_would_block")]
  filtered = [r for r in traded if r.get("mad_would_block")]
  splits = split_dataset(list(rows))

  def _split_mad(split_rows: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = [r for r in split_rows if r.get("outcome") not in {None, "blocked"}]
    kept_split = [r for r in baseline if not r.get("mad_would_block")]
    return {
      "n_events": len(split_rows),
      "baseline": aggregate_report(baseline),
      "mad_kept": aggregate_report(kept_split),
      "mad_filtered_n": sum(1 for r in baseline if r.get("mad_would_block")),
      "by_phase_session_strategy": phase_session_strategy_table(split_rows),
      "by_affinity_confidence": mad_affinity_confidence_table(split_rows),
      "alignment_comparison": mad_alignment_comparison(split_rows),
    }

  # §16/§19 — legacy (v1, mad_version absent/1) and v2-scored populations
  # must never be silently mixed. Report the split so a reviewer can see it.
  version_counts: dict[str, int] = defaultdict(int)
  for row in rows:
    version_counts[str(row.get("mad_version") or "legacy")] += 1

  return {
    "version": "mad-2",
    "mad_code_version": MAD_VERSION,
    "mad_version_population": dict(sorted(version_counts.items())),
    "discipline": {
      "mode": "observe_only_counterfactual",
      "rule": "never_tune_thresholds_on_holdout",
      "live_publish": "unchanged",
      "note": (
        "mad_would_block is a research preview of MAD-4 gates; "
        "do not enable live hard block until holdout expectancy is green."
      ),
    },
    "summary": {
      "n_events": len(rows),
      "n_baseline_traded": len(traded),
      "n_mad_kept": len(kept),
      "n_mad_filtered": len(filtered),
      "n_unclear_phase": sum(
        1 for r in rows if r.get("mad_phase") == PHASE_UNCLEAR
      ),
      "baseline_expectancy_r": float(
        aggregate_report(traded).get("expectancy_r") or 0.0
      ),
      "mad_kept_expectancy_r": float(
        aggregate_report(kept).get("expectancy_r") or 0.0
      ),
    },
    "strategy_baselines": strategy_baselines(rows),
    "by_phase_session_strategy": phase_session_strategy_table(rows),
    # §9/§18: directional aligned/neutral/opposed comparison — never
    # promoted on win rate alone (expectancy_r/profit_factor sit alongside).
    "alignment_comparison": mad_alignment_comparison(rows),
    "by_affinity_confidence": mad_affinity_confidence_table(rows),
    "calibration_baseline_traded": calibration_report(traded),
    "splits": {
      "development": _split_mad(splits["development"]),
      "validation": _split_mad(splits["validation"]),
      "holdout": _split_mad(splits["holdout"]),
    },
    "events": list(rows),
  }


def replay_mad_fixture(path: Path) -> dict[str, Any]:
  events = load_lab_events(path)
  rows = [replay_lab_event_with_mad(event) for event in events]
  return mad_expectancy_report(rows)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description="MAD-2 phase×session expectancy (observe-only counterfactual)",
  )
  parser.add_argument("--fixture", type=Path, required=True)
  parser.add_argument("--output", type=Path, required=True)
  args = parser.parse_args(argv)
  report = replay_mad_fixture(args.fixture)
  # Keep event dump optional size — write summary without full events for CLI
  # unless small; always include events for fixture tests via API.
  args.output.parent.mkdir(parents=True, exist_ok=True)
  payload = dict(report)
  # Drop bulky per-event feature dumps in CLI output; keep MAD fields.
  slim_events = []
  for row in payload.get("events") or []:
    slim_events.append({
      k: row.get(k)
      for k in (
        "timestamp", "session", "archetype", "direction", "symbol",
        "outcome", "net_r", "mad_phase", "mad_would_block", "mad_gate_reason",
        "mad_kept", "mad_filtered", "gate_allowed", "gate_reason",
        "mad_version", "mad_confidence", "mad_affinity", "mad_direction",
        "mad_affinity_bucket", "mad_confidence_bucket",
      )
    })
  payload["events"] = slim_events
  args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())

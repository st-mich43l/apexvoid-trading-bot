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

from app.analysis.mad_phase import PHASE_UNCLEAR, PHASES, mad_hard_gate
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
}


def resolve_event_mad_phase(event: LabEvent) -> str:
  """Read stamped phase only — do not re-classify Asia box offline."""
  measured = dict(event.measured or {})
  mad = measured.get("mad")
  if isinstance(mad, dict) and mad.get("phase") is not None:
    phase = str(mad.get("phase") or "").casefold()
  elif measured.get("mad_phase") is not None:
    phase = str(measured.get("mad_phase") or "").casefold()
  else:
    phase = PHASE_UNCLEAR
  if phase not in PHASES:
    return PHASE_UNCLEAR
  return phase


def gate_strategy_key(strategy: str) -> str:
  key = str(strategy or "").strip().casefold()
  return _STRATEGY_FOR_GATE.get(key, key)


def replay_lab_event_with_mad(event: LabEvent) -> dict[str, Any]:
  """Paper replay row + MAD-1 would_gate counterfactual fields."""
  row = replay_lab_event(event)
  phase = resolve_event_mad_phase(event)
  preview = mad_hard_gate(phase=phase, strategy=gate_strategy_key(event.strategy))
  baseline_traded = row.get("outcome") not in {None, "blocked"}
  would_block = bool(preview.would_block)
  row.update({
    "mad_phase": phase,
    "mad_would_block": would_block,
    "mad_gate_reason": preview.reason_code,
    "mad_kept": bool(baseline_traded and not would_block),
    "mad_filtered": bool(baseline_traded and would_block),
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
    }

  return {
    "version": "mad-2",
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
      )
    })
  payload["events"] = slim_events
  args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())

"""Offline scalp replay and deterministic paper outcomes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from app.scalping.models import PaperOutcome, ScalpOpportunity


def evaluate_paper_outcome(
  opportunity: ScalpOpportunity,
  bars_after: pd.DataFrame,
  *,
  pip_size: float,
  spread_pips: float = 0.0,
  slippage_pips: float = 0.0,
  max_hold_bars: int = 45,
) -> PaperOutcome:
  """Chronological paper eval. Same-bar target/stop → stop wins (conservative)."""
  if bars_after is None or bars_after.empty or pip_size <= 0:
    return PaperOutcome(
      outcome="no_data",
      bars_held=0,
      mfe_pips=0.0,
      mae_pips=0.0,
      net_pips=0.0,
      exit_price=None,
      exit_reason="no_data",
    )

  direction = opportunity.direction.upper()
  entry = float(opportunity.trigger_price)
  if direction == "BUY":
    entry += (spread_pips + slippage_pips) * pip_size
  else:
    entry -= (spread_pips + slippage_pips) * pip_size

  stop = float(opportunity.invalidation_price)
  target = float(opportunity.expected_target_price)
  mfe = 0.0
  mae = 0.0
  held = 0

  for _, bar in bars_after.iterrows():
    held += 1
    high = float(bar["high"])
    low = float(bar["low"])
    if direction == "BUY":
      mfe = max(mfe, (high - entry) / pip_size)
      mae = min(mae, (low - entry) / pip_size)
      hit_stop = low <= stop
      hit_target = high >= target
      if hit_stop and hit_target:
        net = (stop - entry) / pip_size
        return PaperOutcome("stop", held, mfe, mae, net, stop, "same_bar_stop_priority")
      if hit_stop:
        net = (stop - entry) / pip_size
        return PaperOutcome("stop", held, mfe, mae, net, stop, "stop")
      if hit_target:
        net = (target - entry) / pip_size
        return PaperOutcome("target", held, mfe, mae, net, target, "target")
    else:
      mfe = max(mfe, (entry - low) / pip_size)
      mae = min(mae, (entry - high) / pip_size)
      hit_stop = high >= stop
      hit_target = low <= target
      if hit_stop and hit_target:
        net = (entry - stop) / pip_size
        return PaperOutcome("stop", held, mfe, mae, net, stop, "same_bar_stop_priority")
      if hit_stop:
        net = (entry - stop) / pip_size
        return PaperOutcome("stop", held, mfe, mae, net, stop, "stop")
      if hit_target:
        net = (entry - target) / pip_size
        return PaperOutcome("target", held, mfe, mae, net, target, "target")
    if held >= max_hold_bars:
      close = float(bar["close"])
      net = (close - entry) / pip_size if direction == "BUY" else (entry - close) / pip_size
      return PaperOutcome("max_hold", held, mfe, mae, net, close, "maximum_hold")

  last_close = float(bars_after.iloc[-1]["close"])
  net = (last_close - entry) / pip_size if direction == "BUY" else (entry - last_close) / pip_size
  return PaperOutcome("open_end", held, mfe, mae, net, last_close, "bars_exhausted")


def aggregate_report(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
  items = list(rows)
  if not items:
    return {
      "count": 0,
      "win_rate": 0.0,
      "profit_factor": 0.0,
      "expectancy_r": 0.0,
      "max_drawdown_r": 0.0,
      "avg_mfe_pips": 0.0,
      "avg_mae_pips": 0.0,
    }
  wins = [r for r in items if r.get("outcome") == "target"]
  losses = [r for r in items if r.get("outcome") == "stop"]
  win_r = [float(r.get("net_r") or 0.0) for r in wins]
  loss_r = [abs(float(r.get("net_r") or 0.0)) for r in losses]
  gross_win = sum(win_r)
  gross_loss = sum(loss_r) or 1e-9
  equity = 0.0
  peak = 0.0
  max_dd = 0.0
  streak = 0
  max_streak = 0
  for row in items:
    equity += float(row.get("net_r") or 0.0)
    peak = max(peak, equity)
    max_dd = min(max_dd, equity - peak)
    if float(row.get("net_r") or 0.0) < 0:
      streak += 1
      max_streak = max(max_streak, streak)
    else:
      streak = 0
  mfe_vals = [float(r.get("mfe_pips") or 0.0) for r in items]
  mae_vals = [float(r.get("mae_pips") or 0.0) for r in items]
  return {
    "count": len(items),
    "wins": len(wins),
    "losses": len(losses),
    "win_rate": len(wins) / len(items),
    "profit_factor": gross_win / gross_loss,
    "expectancy_r": sum(float(r.get("net_r") or 0.0) for r in items) / len(items),
    "max_drawdown_r": abs(max_dd),
    "maximum_consecutive_losses": max_streak,
    "avg_mfe_pips": sum(mfe_vals) / len(mfe_vals),
    "avg_mae_pips": sum(mae_vals) / len(mae_vals),
    "by_session": _group(items, "session"),
    "by_archetype": _group(items, "archetype"),
    "blocked_buy_top": sum(1 for r in items if r.get("block_reason") == "buy_in_premium"),
    "blocked_sell_bottom": sum(1 for r in items if r.get("block_reason") == "sell_in_discount"),
  }


def split_dataset(
  rows: list[dict[str, Any]],
  *,
  development: float = 0.60,
  validation: float = 0.20,
  holdout: float = 0.20,
  timestamp_key: str = "timestamp",
) -> dict[str, list[dict[str, Any]]]:
  """Chronological 60/20/20 split. Holdout must remain untouched during tuning."""
  if abs(development + validation + holdout - 1.0) > 1e-6:
    raise ValueError("split fractions must sum to 1.0")
  ordered = sorted(rows, key=lambda row: int(row.get(timestamp_key) or 0))
  n = len(ordered)
  if n == 0:
    return {"development": [], "validation": [], "holdout": []}
  i_dev = int(n * development)
  i_val = i_dev + int(n * validation)
  # Ensure holdout gets the remainder so fractions don't drop the last rows.
  return {
    "development": ordered[:i_dev],
    "validation": ordered[i_dev:i_val],
    "holdout": ordered[i_val:],
  }


def calibration_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
  """Replay lab summary with split discipline metadata."""
  splits = split_dataset(rows)
  return {
    "discipline": {
      "development": 0.60,
      "validation": 0.20,
      "holdout": 0.20,
      "rule": "never_tune_thresholds_on_holdout",
      "prefer": "wide_positive_expectancy_regions",
    },
    "development": aggregate_report(splits["development"]),
    "validation": aggregate_report(splits["validation"]),
    "holdout": aggregate_report(splits["holdout"]),
    "full": aggregate_report(rows),
  }


def _group(items: list[dict[str, Any]], key: str) -> dict[str, Any]:
  out: dict[str, dict[str, float]] = {}
  for row in items:
    bucket = str(row.get(key) or "unknown")
    slot = out.setdefault(bucket, {"count": 0, "wins": 0})
    slot["count"] += 1
    if row.get("outcome") == "target":
      slot["wins"] += 1
  return out


def replay_from_fixture(path: Path) -> dict[str, Any]:
  """Replay a JSONL fixture of opportunities + subsequent bars."""
  rows: list[dict[str, Any]] = []
  for line in path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
      continue
    payload = json.loads(line)
    opp = ScalpOpportunity.from_json(json.dumps(payload["opportunity"]))
    bars = pd.DataFrame(payload["bars_after"])
    if "time" in bars.columns:
      bars = bars.set_index(pd.to_datetime(bars["time"], utc=True))
    outcome = evaluate_paper_outcome(
      opp,
      bars,
      pip_size=float(payload.get("pip_size", 0.1)),
      spread_pips=float(payload.get("spread_pips", 1.0)),
      slippage_pips=float(payload.get("slippage_pips", 0.5)),
      max_hold_bars=int(payload.get("max_hold_bars", 45)),
    )
    stop = float(opp.expected_stop_pips) or 1.0
    rows.append({
      "opportunity_id": opp.opportunity_id,
      "context_id": opp.context_id,
      "timestamp": opp.trigger_bar_ts,
      "session": payload.get("session", "unknown"),
      "archetype": opp.archetype,
      "direction": opp.direction,
      "entry": opp.trigger_price,
      "stop": opp.invalidation_price,
      "target": opp.expected_target_price,
      "target_pips": opp.expected_target_pips,
      "stop_pips": opp.expected_stop_pips,
      "net_reward_risk": opp.expected_reward_risk,
      "range_position": opp.location_position,
      "trigger": opp.trigger_type,
      "decision": payload.get("decision", "allowed"),
      "outcome": outcome.outcome,
      "mfe_pips": outcome.mfe_pips,
      "mae_pips": outcome.mae_pips,
      "bars_held": outcome.bars_held,
      "net_r": outcome.net_pips / stop,
      "block_reason": payload.get("block_reason"),
    })
  return {"opportunities": rows, "aggregate": aggregate_report(rows), "calibration": calibration_report(rows)}


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="Replay HFS scalp paper outcomes")
  parser.add_argument("--symbol", default="XAU")
  parser.add_argument("--from", dest="date_from", default=None)
  parser.add_argument("--to", dest="date_to", default=None)
  parser.add_argument("--fixture", type=Path, default=None)
  parser.add_argument("--output", type=Path, required=True)
  args = parser.parse_args(argv)
  if args.fixture is None:
    report = {
      "error": "redis_ chronogical_replay_requires_fixture_in_this_build",
      "symbol": args.symbol,
      "from": args.date_from,
      "to": args.date_to,
      "hint": "Pass --fixture path/to/events.jsonl for offline paper replay",
    }
  else:
    report = replay_from_fixture(args.fixture)
    report["symbol"] = args.symbol
    report["from"] = args.date_from
    report["to"] = args.date_to
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())

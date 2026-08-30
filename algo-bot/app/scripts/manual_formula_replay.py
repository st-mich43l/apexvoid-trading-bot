"""Replay strategy math on manual_algo_charts for closed /algo trades.

Reads Postgres (DATABASE_URL), loads filled-event OHLC snapshots, builds
causal frames through fill_ts, runs analyze + matching detectors, and emits
per-trade formula features + a W/L scorecard by strategy.

Usage (inside algo-bot container or local with DATABASE_URL)::

  python -m app.scripts.manual_formula_replay --json /tmp/replay.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import asyncpg
import pandas as pd

from app.analysis.detectors import (
  DetectionResult,
  DetectorSettings,
  flip_demand_zone_reaction,
  flip_supply_zone_reaction,
  key_level_reaction,
  replay_build_context,
)
from app.analysis.math_utils import atr_scalar
from app.analysis.technique_detectors import (
  confluence_zone_reaction,
  fvg_technique_reaction,
  ifvg_technique_reaction,
  order_block_technique_reaction,
  supply_demand_technique_reaction,
)
from app.autotrade.reaction_funnel import normalize_setup_type


# Freeform owner tags → canonical auto strategy (extends normalize_setup_type).
_TAG_TO_STRATEGY = {
  "supply": "Supply Demand",
  "demand": "Supply Demand",
  "ob": "Order Block",
  "confulence": "Confluence Zone",
  "confluence": "Confluence Zone",
  "golden-fibo": "Golden Fib",
}


def _bars_to_df(bars: list[dict[str, Any]], *, max_ts: int | None) -> pd.DataFrame:
  rows = []
  for bar in bars:
    t = int(bar["t"])
    if max_ts is not None and t > max_ts:
      continue
    rows.append({
      "t": t,
      "open": float(bar["o"]),
      "high": float(bar["h"]),
      "low": float(bar["l"]),
      "close": float(bar["c"]),
      "volume": float(bar.get("v") or 0),
    })
  if not rows:
    return pd.DataFrame(
      columns=["open", "high", "low", "close", "volume"],
      index=pd.DatetimeIndex([], tz="UTC", name="time"),
    )
  df = pd.DataFrame(rows)
  index = pd.to_datetime(df.pop("t"), unit="s", utc=True)
  df.index = pd.DatetimeIndex(index, name="time")
  return df[["open", "high", "low", "close", "volume"]]


def _strategy_for_tag(raw: str | None) -> str:
  text = str(raw or "").strip()
  if not text:
    return "(blank)"
  short = _TAG_TO_STRATEGY.get(text.casefold())
  if short:
    return short
  mapped = normalize_setup_type(text)
  if mapped and mapped.casefold() != text.casefold():
    return mapped
  if mapped:
    # normalize may only title-case; still try alias table on casefold
    return _TAG_TO_STRATEGY.get(mapped.casefold(), mapped)
  return text


def _nearest_zone(zones: list[Any], price: float, side: str | None) -> Any | None:
  best = None
  best_dist = None
  for zone in zones:
    if side and getattr(zone, "side", None) not in {side, None}:
      # Zone.side is demand/supply; map BUY→demand SELL→supply
      want = "demand" if side == "BUY" else "supply"
      if getattr(zone, "side", None) != want:
        continue
    mid = (float(zone.low) + float(zone.high)) / 2.0
    dist = abs(mid - price)
    inside = float(zone.low) <= price <= float(zone.high)
    score_dist = 0.0 if inside else dist
    if best_dist is None or score_dist < best_dist:
      best_dist = score_dist
      best = zone
  return best


def _run_detector(strategy: str, direction: str, ctx: Any) -> DetectionResult | None:
  if strategy == "Key Level Reaction":
    return key_level_reaction(ctx)
  if strategy == "Flip Zone":
    return (
      flip_demand_zone_reaction(ctx)
      if direction == "BUY"
      else flip_supply_zone_reaction(ctx)
    )
  if strategy == "Supply Demand":
    return supply_demand_technique_reaction(ctx)
  if strategy == "Order Block":
    return order_block_technique_reaction(ctx)
  if strategy == "FVG":
    return fvg_technique_reaction(ctx)
  if strategy == "iFVG":
    return ifvg_technique_reaction(ctx)
  if strategy == "Confluence Zone":
    return confluence_zone_reaction(ctx)
  return None


def _feature_row(
  *,
  signal: dict[str, Any],
  frames: dict[str, pd.DataFrame],
  strategy: str,
) -> dict[str, Any]:
  direction = str(signal["action"]).upper()
  fill = float(signal["broker_fill_price"] or signal["entry"] or 0)
  result = float(signal["result_pips"] or 0)
  settings = DetectorSettings()
  ctx = replay_build_context(
    str(signal.get("symbol") or "XAU"),
    "M5",
    frames,
    settings,
    ["H1", "M15"],
  )
  analysis = ctx.analysis
  m5 = analysis.per_tf.get("M5") if analysis else None
  m15 = analysis.per_tf.get("M15") if analysis else None
  h1 = analysis.per_tf.get("H1") if analysis else None

  zones = list(getattr(m5, "zones", ()) or ()) if m5 else []
  nearest = _nearest_zone(zones, fill, direction)
  zone_score = float(getattr(nearest, "score", 0) or 0) if nearest else None
  zone_reasons = list(getattr(nearest, "score_reasons", ()) or ()) if nearest else []
  zone_source = getattr(nearest, "source", None) if nearest else None
  zone_touches = int(getattr(nearest, "touches", 0) or 0) if nearest else None

  htf_bias = str(getattr(analysis, "htf_bias", "unknown") or "unknown")
  htf_aligned = (
    (htf_bias == "up" and direction == "BUY")
    or (htf_bias == "down" and direction == "SELL")
  )
  htf_known = htf_bias in {"up", "down", "range"}

  # Entry location: position in M15 window range (chart-backed proxy for PD).
  entry_pos = None
  if "M15" in frames and not frames["M15"].empty:
    df15 = frames["M15"]
    lo = float(df15["low"].min())
    hi = float(df15["high"].max())
    if hi > lo:
      entry_pos = (fill - lo) / (hi - lo)
  # Prefer analysis dealing range when present.
  try:
    dr = getattr(analysis, "dealing_range", None)
    if dr is not None and getattr(dr, "high", None) is not None and getattr(dr, "low", None) is not None:
      hi = float(dr.high)
      lo = float(dr.low)
      if hi > lo:
        entry_pos = (fill - lo) / (hi - lo)
  except Exception:
    pass

  atr = atr_scalar(m5.atr) if m5 is not None else 0.0
  detection = None
  detect_error = None
  try:
    detection = _run_detector(strategy, direction, ctx)
  except Exception as exc:
    detect_error = str(exc)

  tech_near = []
  if m5 is not None:
    for inst in list(getattr(m5, "technique_instances", ()) or ()):
      kind = getattr(inst, "kind", None) or getattr(inst, "technique", None)
      lo = float(getattr(inst, "low", 0) or 0)
      hi = float(getattr(inst, "high", 0) or 0)
      if hi < lo:
        continue
      if lo <= fill <= hi or abs(((lo + hi) / 2) - fill) <= max(atr, 0.5):
        tech_near.append({
          "kind": str(kind),
          "low": lo,
          "high": hi,
          "score": float(getattr(inst, "score", 0) or 0),
        })

  return {
    "signal_id": int(signal["id"]),
    "setup_tag": signal.get("setup_type"),
    "strategy": strategy,
    "direction": direction,
    "result_pips": result,
    "win": result > 0,
    "fill": fill,
    "filled_at": int(signal["filled_at"] or 0),
    "bars": {tf: int(len(df)) for tf, df in frames.items()},
    "htf_bias": htf_bias,
    "htf_known": htf_known,
    "htf_aligned": htf_aligned if htf_known else None,
    "entry_position": round(entry_pos, 4) if entry_pos is not None else None,
    "atr_m5": round(atr, 4) if atr else None,
    "nearest_zone_score": zone_score,
    "nearest_zone_source": zone_source,
    "nearest_zone_touches": zone_touches,
    "nearest_zone_reasons": zone_reasons,
    "technique_near_count": len(tech_near),
    "technique_near": tech_near[:5],
    "detector_fired": detection is not None,
    "detector_setup": getattr(detection, "setup", None) if detection else None,
    "detector_confluence": getattr(detection, "confluence", None) if detection else None,
    "detector_confirmation": (
      (getattr(detection, "confirmation_type", None) or getattr(detection, "confirmation", None))
      if detection else None
    ),
    "detector_source_score": getattr(detection, "source_score", None) if detection else None,
    "detector_error": detect_error,
  }


def _scorecard(rows: list[dict[str, Any]]) -> dict[str, Any]:
  by: dict[str, list[dict[str, Any]]] = defaultdict(list)
  for row in rows:
    key = f"{row['strategy']}|{row['direction']}"
    by[key].append(row)

  cards = []
  for key, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
    strategy, direction = key.split("|", 1)
    wins = [r for r in items if r["win"]]
    losses = [r for r in items if not r["win"]]
    n = len(items)
    if n < 3:
      continue

    def rate(pred) -> float | None:
      flagged = [r for r in items if pred(r)]
      if len(flagged) < 3:
        return None
      return round(100.0 * sum(1 for r in flagged if r["win"]) / len(flagged), 1)

    def mean(vals: list[float | None]) -> float | None:
      clean = [float(v) for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
      if not clean:
        return None
      return round(sum(clean) / len(clean), 3)

    htf_on = [r for r in items if r.get("htf_aligned") is True]
    htf_off = [r for r in items if r.get("htf_aligned") is False]
    fired = [r for r in items if r.get("detector_fired")]
    silent = [r for r in items if not r.get("detector_fired")]

    cards.append({
      "strategy": strategy,
      "direction": direction,
      "n": n,
      "wins": len(wins),
      "losses": len(losses),
      "win_pct": round(100.0 * len(wins) / n, 1),
      "avg_pips": round(sum(r["result_pips"] for r in items) / n, 1),
      "htf_aligned_n": len(htf_on),
      "htf_aligned_win_pct": round(100.0 * sum(1 for r in htf_on if r["win"]) / len(htf_on), 1) if len(htf_on) >= 3 else None,
      "htf_counter_n": len(htf_off),
      "htf_counter_win_pct": round(100.0 * sum(1 for r in htf_off if r["win"]) / len(htf_off), 1) if len(htf_off) >= 3 else None,
      "detector_fired_n": len(fired),
      "detector_fired_win_pct": round(100.0 * sum(1 for r in fired if r["win"]) / len(fired), 1) if len(fired) >= 3 else None,
      "detector_miss_n": len(silent),
      "detector_miss_win_pct": round(100.0 * sum(1 for r in silent if r["win"]) / len(silent), 1) if len(silent) >= 3 else None,
      "avg_zone_score_wins": mean([r.get("nearest_zone_score") for r in wins]),
      "avg_zone_score_losses": mean([r.get("nearest_zone_score") for r in losses]),
      "avg_entry_pos_wins": mean([r.get("entry_position") for r in wins]),
      "avg_entry_pos_losses": mean([r.get("entry_position") for r in losses]),
      "avg_confluence_wins": mean([r.get("detector_confluence") for r in wins if r.get("detector_fired")]),
      "avg_confluence_losses": mean([r.get("detector_confluence") for r in losses if r.get("detector_fired")]),
      "reason_hits_wins": _reason_freq(wins),
      "reason_hits_losses": _reason_freq(losses),
    })
  return {"cells": cards, "trade_count": len(rows)}


def _reason_freq(rows: list[dict[str, Any]], top: int = 8) -> list[dict[str, Any]]:
  counts: dict[str, int] = defaultdict(int)
  for row in rows:
    for reason in row.get("nearest_zone_reasons") or []:
      # reasons often like "htf+3" or descriptive strings
      key = str(reason).split("+")[0].split(":")[0].strip()[:48]
      if key:
        counts[key] += 1
  ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:top]
  return [{"reason": k, "n": v} for k, v in ranked]


async def _load_trades(pool: asyncpg.Pool) -> list[dict[str, Any]]:
  rows = await pool.fetch(
    """
    SELECT s.id, s.setup_type, s.action, s.result_pips, s.filled_at,
           s.broker_fill_price, s.entry, s.entry_end, s.sl, s.symbol
    FROM manual_signals s
    WHERE s.status = 'closed'
      AND COALESCE(s.trade_stream, '') = 'algo_manual'
      AND s.filled_at IS NOT NULL
      AND EXISTS (
        SELECT 1 FROM manual_algo_charts c
        WHERE c.signal_id = s.id AND c.event = 'filled'
      )
    ORDER BY s.id
    """
  )
  return [dict(row) for row in rows]


async def _load_frames(
  pool: asyncpg.Pool,
  signal_id: int,
  filled_at: int,
) -> dict[str, pd.DataFrame]:
  charts = await pool.fetch(
    """
    SELECT timeframe, bars
    FROM manual_algo_charts
    WHERE signal_id = $1 AND event = 'filled'
    """,
    signal_id,
  )
  frames: dict[str, pd.DataFrame] = {}
  for row in charts:
    bars = row["bars"]
    if isinstance(bars, str):
      bars = json.loads(bars)
    frames[str(row["timeframe"]).upper()] = _bars_to_df(bars, max_ts=int(filled_at))
  return frames


async def run(limit: int | None = None) -> dict[str, Any]:
  dsn = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN")
  if not dsn:
    raise SystemExit("DATABASE_URL required")
  # asyncpg wants postgresql:// not sqlalchemy postgresql+asyncpg://
  dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
  pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
  assert pool is not None
  try:
    trades = await _load_trades(pool)
    if limit:
      trades = trades[:limit]
    features: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for trade in trades:
      strategy = _strategy_for_tag(trade.get("setup_type"))
      try:
        frames = await _load_frames(pool, int(trade["id"]), int(trade["filled_at"]))
        if "M5" not in frames or frames["M5"].empty:
          errors.append({"signal_id": trade["id"], "error": "missing_m5"})
          continue
        features.append(_feature_row(signal=trade, frames=frames, strategy=strategy))
      except Exception as exc:
        errors.append({"signal_id": trade["id"], "error": str(exc)})
    return {
      "generated_at": datetime.now(timezone.utc).isoformat(),
      "coverage": {
        "charted_closed": len(trades),
        "replayed": len(features),
        "errors": len(errors),
      },
      "scorecard": _scorecard(features),
      "features": features,
      "errors": errors[:40],
    }
  finally:
    await pool.close()


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--json", default="-")
  parser.add_argument("--limit", type=int, default=None)
  args = parser.parse_args()
  payload = asyncio.run(run(limit=args.limit))
  text = json.dumps(payload, indent=2, default=str)
  if args.json == "-":
    sys.stdout.write(text)
  else:
    with open(args.json, "w", encoding="utf-8") as fh:
      fh.write(text)
    # Compact human summary
    sc = payload["scorecard"]
    print(f"replayed={payload['coverage']['replayed']} errors={payload['coverage']['errors']}", file=sys.stderr)
    for cell in sc.get("cells", []):
      print(
        f"{cell['strategy']:22} {cell['direction']:4} n={cell['n']:2} "
        f"WR={cell['win_pct']:5} avg={cell['avg_pips']:6} "
        f"htf_al={cell['htf_aligned_win_pct']} htf_ctr={cell['htf_counter_win_pct']} "
        f"det={cell['detector_fired_win_pct']} miss={cell['detector_miss_win_pct']} "
        f"zW={cell['avg_zone_score_wins']} zL={cell['avg_zone_score_losses']} "
        f"posW={cell['avg_entry_pos_wins']} posL={cell['avg_entry_pos_losses']}",
        file=sys.stderr,
      )


if __name__ == "__main__":
  main()

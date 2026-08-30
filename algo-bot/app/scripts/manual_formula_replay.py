"""Replay strategy discovery math on manual_algo_charts for closed /algo trades.

Reads Postgres (DATABASE_URL), loads filled-event OHLC snapshots, builds
causal frames through each scanned M5 bar (default: fill and up to N bars
before), runs analyze + matching detectors, and emits per-trade formula
features + a W/L scorecard by strategy.

Scope: **discovery only**. This harness does not call
``evaluate_entry_activation`` — no entry-location gates, M1 trigger,
``demand_requires_sweep_reclaim``, or ``sell_not_proximal``. It answers
whether a detector would have proposed the setup, not whether the bot would
have taken the trade. Activation-stage replay is separate work.

Usage (inside algo-bot container or local with DATABASE_URL)::

  python -m app.scripts.manual_formula_replay --json /tmp/replay.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
from collections import Counter
from collections import defaultdict
from dataclasses import fields
from datetime import datetime
from datetime import timezone
from typing import Any

import asyncpg
import pandas as pd

from app.analysis.detectors import (
  DetectionResult,
  DetectorSettings,
  detector_settings_from,
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

_RATE_MIN_N = 8
_CELL_POWER_N = 20
_HTF_CONFOUND_AGREEMENT = 0.70


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
  if n <= 0:
    return (0.0, 1.0)
  p = k / n
  den = 1.0 + z * z / n
  centre = (p + z * z / (2 * n)) / den
  half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
  return (max(0.0, centre - half), min(1.0, centre + half))


def _fisher_exact_2x2(a: int, b: int, c: int, d: int) -> float:
  """Two-sided Fisher exact p-value for a 2×2 table via ``math.comb``."""
  n = a + b + c + d
  if n == 0:
    return 1.0
  row1 = a + b
  row2 = c + d
  col1 = a + c
  if row1 == 0 or row2 == 0 or col1 == 0 or (b + d) == 0:
    return 1.0

  def hyper(k: int) -> float:
    if k < 0 or k > row1 or (col1 - k) < 0 or (col1 - k) > row2:
      return 0.0
    return (
      math.comb(row1, k) * math.comb(row2, col1 - k) / math.comb(n, col1)
    )

  p_obs = hyper(a)
  total = 0.0
  lo = max(0, col1 - row2)
  hi = min(row1, col1)
  for k in range(lo, hi + 1):
    pk = hyper(k)
    if pk <= p_obs + 1e-15:
      total += pk
  return min(1.0, total)


def _rate(k: int, n: int, *, min_n: int = _RATE_MIN_N) -> dict[str, Any] | None:
  if n < min_n:
    return None
  lo, hi = _wilson(k, n)
  return {
    "pct": round(100.0 * k / n, 1),
    "n": n,
    "ci_low": round(100.0 * lo, 1),
    "ci_high": round(100.0 * hi, 1),
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


def _truncate_frames(
  frames: dict[str, pd.DataFrame],
  max_ts: int,
) -> dict[str, pd.DataFrame]:
  out: dict[str, pd.DataFrame] = {}
  for tf, df in frames.items():
    if df.empty:
      out[tf] = df
      continue
    mask = [int(ts.timestamp()) <= max_ts for ts in df.index]
    out[tf] = df.loc[mask]
  return out


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
    return _TAG_TO_STRATEGY.get(mapped.casefold(), mapped)
  return text


def _nearest_zone(zones: list[Any], price: float, side: str | None) -> Any | None:
  best = None
  best_dist = None
  for zone in zones:
    if side and getattr(zone, "side", None) not in {side, None}:
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


def _detection_direction_match(
  detection: DetectionResult | None,
  direction: str,
) -> bool:
  return detection is not None and str(detection.direction).upper() == direction


def _detection_matched(
  detection: DetectionResult | None,
  *,
  direction: str,
  fill: float,
  atr: float,
  match_atr: float,
) -> bool:
  if not _detection_direction_match(detection, direction):
    return False
  assert detection is not None
  zone = detection.entry_zone
  lo = float(zone.low)
  hi = float(zone.high)
  if lo > hi:
    lo, hi = hi, lo
  if lo <= fill <= hi:
    return True
  edge = min(abs(fill - lo), abs(fill - hi))
  return edge <= float(match_atr) * max(0.0, float(atr))


def _entry_position_fields(
  analysis: Any,
  frames: dict[str, pd.DataFrame],
  fill: float,
) -> tuple[float | None, float | None, bool | None]:
  """Return (raw, clamped, dealing_range_brackets)."""
  raw: float | None = None
  brackets: bool | None = None
  if "M15" in frames and not frames["M15"].empty:
    df15 = frames["M15"]
    lo = float(df15["low"].min())
    hi = float(df15["high"].max())
    if hi > lo:
      raw = (fill - lo) / (hi - lo)
  try:
    dr = getattr(analysis, "dealing_range", None)
    if (
      dr is not None
      and getattr(dr, "high", None) is not None
      and getattr(dr, "low", None) is not None
    ):
      hi = float(dr.high)
      lo = float(dr.low)
      if hi > lo:
        raw = (fill - lo) / (hi - lo)
        brackets = lo <= fill <= hi
  except Exception:
    pass
  if raw is None:
    return None, None, brackets
  clamped = min(1.0, max(0.0, float(raw)))
  return raw, clamped, brackets


def _r_multiple(result_pips: float, entry: Any, sl: Any) -> float | None:
  try:
    entry_f = float(entry)
    sl_f = float(sl)
  except (TypeError, ValueError):
    return None
  risk = abs(entry_f - sl_f)
  if risk <= 0:
    return None
  # result_pips is already in pips; convert risk price → pips when possible.
  # For XAU-style charts, pip size is typically 0.1; prefer ratio of pips to
  # stop distance expressed in the same price units via result/risk when
  # result was computed as price_delta / pip_size. Use price R when both are
  # prices: result_price / risk ≈ (result_pips * pip) / risk. Without pip
  # size on the row, approximate R as result_pips / (risk / pip) only if we
  # know pip. Safer: store R as result_pips / risk_pips when risk looks like
  # pips, else result_pips / (risk * 10) for XAU 0.1 pip — too heuristic.
  # Spec: result_pips / |entry - sl|. Treat |entry-sl| as price; if risk is
  # large like 5.0 on XAU that's 50 pips at 0.1 — inconsistent units.
  # Keep literal: result_pips / |entry-sl| as specified.
  return float(result_pips) / risk


def _settings_fingerprint(settings: DetectorSettings) -> str:
  payload = {f.name: getattr(settings, f.name) for f in fields(settings)}
  blob = json.dumps(payload, sort_keys=True, default=str)
  return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _resolve_settings(mode: str) -> DetectorSettings:
  if mode == "default":
    return DetectorSettings()
  if mode != "runtime":
    raise ValueError(f"unknown settings mode: {mode}")
  from app.core.config import runtime_config
  return detector_settings_from(runtime_config)


def _reason_freq(
  rows: list[dict[str, Any]],
  *,
  key: str,
  top: int = 8,
) -> list[dict[str, Any]]:
  counts: dict[str, int] = defaultdict(int)
  for row in rows:
    for reason in row.get(key) or []:
      text = str(reason).split("+")[0].split(":")[0].strip()[:48]
      if text:
        counts[text] += 1
  ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:top]
  return [{"reason": name, "n": count} for name, count in ranked]


def _mean(vals: list[Any]) -> float | None:
  clean = [
    float(v)
    for v in vals
    if v is not None and not (isinstance(v, float) and math.isnan(v))
  ]
  if not clean:
    return None
  return round(sum(clean) / len(clean), 3)


def _median(vals: list[float]) -> float | None:
  if not vals:
    return None
  ordered = sorted(vals)
  mid = len(ordered) // 2
  if len(ordered) % 2:
    return round(ordered[mid], 3)
  return round((ordered[mid - 1] + ordered[mid]) / 2.0, 3)


def _scan_detections(
  *,
  frames: dict[str, pd.DataFrame],
  signal: dict[str, Any],
  strategy: str,
  settings: DetectorSettings,
  scan_bars: int,
  match_atr: float,
) -> dict[str, Any]:
  direction = str(signal["action"]).upper()
  fill = float(signal["broker_fill_price"] or signal["entry"] or 0)
  m5 = frames.get("M5")
  if m5 is None or m5.empty:
    return {
      "detector_fired_any": False,
      "detector_direction_match": False,
      "detector_matched": False,
      "detector_fired_at_fill": False,
      "detector_offset_bars": None,
      "detector_scanned_bars": 0,
      "detection": None,
      "detect_error": "missing_m5",
      "atr_m5": 0.0,
      "analysis": None,
      "fill_frames": frames,
    }

  fill_idx = len(m5) - 1
  start_idx = max(0, fill_idx - max(0, int(scan_bars)))
  first_match_offset: int | None = None
  first_match: DetectionResult | None = None
  fill_detection: DetectionResult | None = None
  fill_ctx: Any | None = None
  fill_atr = 0.0
  fill_truncated = frames
  detect_error = None
  scanned = 0
  any_result = False
  # Walk earliest → fill so first_match is the earliest publish.
  for idx in range(start_idx, fill_idx + 1):
    bar_ts = int(m5.index[idx].timestamp())
    truncated = _truncate_frames(frames, bar_ts)
    if "M5" not in truncated or truncated["M5"].empty:
      continue
    scanned += 1
    ctx = None
    detection = None
    try:
      ctx = replay_build_context(
        str(signal.get("symbol") or "XAU"),
        "M5",
        truncated,
        settings,
        ["H1", "M15"],
      )
      detection = _run_detector(strategy, direction, ctx)
    except Exception as exc:
      detect_error = str(exc)
    if detection is not None:
      any_result = True
    atr = 0.0
    if ctx is not None and ctx.analysis is not None:
      m5_item = ctx.analysis.per_tf.get("M5")
      atr = atr_scalar(m5_item.atr) if m5_item is not None else 0.0
    matched = _detection_matched(
      detection,
      direction=direction,
      fill=fill,
      atr=atr,
      match_atr=match_atr,
    )
    if idx == fill_idx:
      fill_detection = detection
      fill_ctx = ctx
      fill_atr = atr
      fill_truncated = truncated
    if matched and first_match_offset is None:
      first_match_offset = fill_idx - idx
      first_match = detection

  analysis = getattr(fill_ctx, "analysis", None) if fill_ctx is not None else None
  report = first_match if first_match is not None else fill_detection
  direction_match = _detection_direction_match(report, direction)
  matched = first_match_offset is not None or _detection_matched(
    fill_detection,
    direction=direction,
    fill=fill,
    atr=fill_atr,
    match_atr=match_atr,
  )

  return {
    "detector_fired_any": any_result,
    "detector_direction_match": direction_match,
    "detector_matched": matched,
    "detector_fired_at_fill": fill_detection is not None,
    "detector_offset_bars": first_match_offset,
    "detector_scanned_bars": scanned,
    "detection": report,
    "detect_error": detect_error,
    "atr_m5": fill_atr,
    "analysis": analysis,
    "fill_frames": fill_truncated,
  }


def _feature_row(
  *,
  signal: dict[str, Any],
  frames: dict[str, pd.DataFrame],
  strategy: str,
  settings: DetectorSettings,
  scan_bars: int = 6,
  match_atr: float = 0.5,
) -> dict[str, Any]:
  direction = str(signal["action"]).upper()
  fill = float(signal["broker_fill_price"] or signal["entry"] or 0)
  result = float(signal["result_pips"] or 0)
  scan = _scan_detections(
    frames=frames,
    signal=signal,
    strategy=strategy,
    settings=settings,
    scan_bars=scan_bars,
    match_atr=match_atr,
  )
  analysis = scan["analysis"]
  fill_frames = scan["fill_frames"]
  m5 = analysis.per_tf.get("M5") if analysis else None
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

  entry_raw, entry_clamped, brackets = _entry_position_fields(
    analysis, fill_frames, fill,
  )
  atr = float(scan["atr_m5"] or 0.0)
  detection: DetectionResult | None = scan["detection"]

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

  detector_reasons = list(getattr(detection, "reasons", ()) or []) if detection else []
  r_mult = _r_multiple(result, signal.get("entry"), signal.get("sl"))

  return {
    "signal_id": int(signal["id"]),
    "setup_tag": signal.get("setup_type"),
    "strategy": strategy,
    "direction": direction,
    "result_pips": result,
    "win": result > 0,
    "r_multiple": round(r_mult, 4) if r_mult is not None else None,
    "fill": fill,
    "filled_at": int(signal["filled_at"] or 0),
    "bars": {tf: int(len(df)) for tf, df in frames.items()},
    "htf_bias": htf_bias,
    "htf_known": htf_known,
    "htf_aligned": htf_aligned if htf_known else None,
    "entry_position_raw": round(entry_raw, 4) if entry_raw is not None else None,
    "entry_position": round(entry_clamped, 4) if entry_clamped is not None else None,
    "dealing_range_brackets": brackets,
    "atr_m5": round(atr, 4) if atr else None,
    "nearest_zone_score": zone_score,
    "nearest_zone_source": zone_source,
    "nearest_zone_touches": zone_touches,
    "nearest_zone_reasons": zone_reasons,
    "technique_near_count": len(tech_near),
    "technique_near": tech_near[:5],
    "detector_fired_any": bool(scan["detector_fired_any"]),
    "detector_direction_match": bool(scan["detector_direction_match"]),
    "detector_matched": bool(scan["detector_matched"]),
    "detector_fired_at_fill": bool(scan["detector_fired_at_fill"]),
    "detector_offset_bars": scan["detector_offset_bars"],
    "detector_scanned_bars": scan["detector_scanned_bars"],
    # Compat alias for pre-repair readers (fill-bar any-result).
    "detector_fired": bool(scan["detector_fired_at_fill"]),
    "detector_setup": getattr(detection, "setup", None) if detection else None,
    "detector_confluence": getattr(detection, "confluence", None) if detection else None,
    "detector_confirmation": (
      (
        getattr(detection, "confirmation_type", None)
        or getattr(detection, "confirmation", None)
      )
      if detection else None
    ),
    "detector_source_score": getattr(detection, "source_score", None) if detection else None,
    "detector_reasons": detector_reasons,
    "detector_error": scan["detect_error"],
  }


def _htf_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
  bias_counts = Counter(str(r.get("htf_bias") or "unknown") for r in rows)
  comparable = [
    r for r in rows
    if r.get("htf_aligned") is not None and r.get("direction") in {"BUY", "SELL"}
  ]
  if not comparable:
    agreement = None
  else:
    agree = sum(
      1
      for r in comparable
      if bool(r["htf_aligned"]) == (r["direction"] == "BUY")
    )
    agreement = round(agree / len(comparable), 4)
  return {
    "htf_bias_distribution": dict(bias_counts),
    "htf_direction_agreement": agreement,
    "htf_direction_agreement_n": len(comparable),
  }


def _scorecard(rows: list[dict[str, Any]]) -> dict[str, Any]:
  diagnostics = _htf_diagnostics(rows)
  agreement = diagnostics.get("htf_direction_agreement")
  confounded = (
    agreement is not None and float(agreement) > _HTF_CONFOUND_AGREEMENT
  )

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

    pips = [float(r["result_pips"]) for r in items]
    win_pips = [float(r["result_pips"]) for r in wins]
    loss_pips = [float(r["result_pips"]) for r in losses]
    gross_win = sum(win_pips) if win_pips else 0.0
    gross_loss = abs(sum(loss_pips)) if loss_pips else 0.0
    r_vals = [
      float(r["r_multiple"])
      for r in items
      if r.get("r_multiple") is not None
    ]

    htf_on = [r for r in items if r.get("htf_aligned") is True]
    htf_off = [r for r in items if r.get("htf_aligned") is False]
    matched = [r for r in items if r.get("detector_matched")]
    missed = [r for r in items if not r.get("detector_matched")]
    brackets_true = [
      r for r in items if r.get("dealing_range_brackets") is True
    ]
    brackets_known = [
      r for r in items if r.get("dealing_range_brackets") is not None
    ]

    htf_aligned_rate = _rate(sum(1 for r in htf_on if r["win"]), len(htf_on))
    htf_counter_rate = _rate(sum(1 for r in htf_off if r["win"]), len(htf_off))
    htf_fisher = None
    if len(htf_on) >= _RATE_MIN_N and len(htf_off) >= _RATE_MIN_N:
      htf_fisher = round(
        _fisher_exact_2x2(
          sum(1 for r in htf_on if r["win"]),
          sum(1 for r in htf_on if not r["win"]),
          sum(1 for r in htf_off if r["win"]),
          sum(1 for r in htf_off if not r["win"]),
        ),
        4,
      )

    cell: dict[str, Any] = {
      "strategy": strategy,
      "direction": direction,
      "n": n,
      "wins": len(wins),
      "losses": len(losses),
      "cell_underpowered": n < _CELL_POWER_N,
      "win_rate": _rate(len(wins), n),
      "win_pct": round(100.0 * len(wins) / n, 1),
      "avg_pips": round(sum(pips) / n, 1),
      "median_pips": _median(pips),
      "expectancy_pips": round(sum(pips) / n, 3),
      "avg_win_pips": _mean(win_pips),
      "avg_loss_pips": _mean(loss_pips),
      "profit_factor": (
        round(gross_win / gross_loss, 3) if gross_loss > 0 else None
      ),
      "avg_r": _mean(r_vals),
      "expectancy_r": _mean(r_vals),
      "htf_aligned_n": len(htf_on),
      "htf_counter_n": len(htf_off),
      "htf_confounded": confounded,
      "htf_aligned_win_pct": None if confounded else (
        None if htf_aligned_rate is None else htf_aligned_rate["pct"]
      ),
      "htf_counter_win_pct": None if confounded else (
        None if htf_counter_rate is None else htf_counter_rate["pct"]
      ),
      "htf_aligned_win_rate": None if confounded else htf_aligned_rate,
      "htf_counter_win_rate": None if confounded else htf_counter_rate,
      "htf_suppressed_reason": (
        "confounded_with_direction" if confounded else None
      ),
      "htf_fisher_p": None if confounded else htf_fisher,
      "detector_matched_n": len(matched),
      "detector_miss_n": len(missed),
      "detector_matched_win_rate": _rate(
        sum(1 for r in matched if r["win"]), len(matched),
      ),
      "detector_miss_win_rate": _rate(
        sum(1 for r in missed if r["win"]), len(missed),
      ),
      "detector_matched_win_pct": (
        None
        if _rate(sum(1 for r in matched if r["win"]), len(matched)) is None
        else _rate(sum(1 for r in matched if r["win"]), len(matched))["pct"]
      ),
      "detector_miss_win_pct": (
        None
        if _rate(sum(1 for r in missed if r["win"]), len(missed)) is None
        else _rate(sum(1 for r in missed if r["win"]), len(missed))["pct"]
      ),
      "detector_fisher_p": (
        round(
          _fisher_exact_2x2(
            sum(1 for r in matched if r["win"]),
            sum(1 for r in matched if not r["win"]),
            sum(1 for r in missed if r["win"]),
            sum(1 for r in missed if not r["win"]),
          ),
          4,
        )
        if len(matched) >= _RATE_MIN_N and len(missed) >= _RATE_MIN_N
        else None
      ),
      "detector_fired_at_fill_n": sum(
        1 for r in items if r.get("detector_fired_at_fill")
      ),
      "avg_zone_score_wins": _mean([r.get("nearest_zone_score") for r in wins]),
      "avg_zone_score_losses": _mean(
        [r.get("nearest_zone_score") for r in losses]
      ),
      "avg_entry_pos_wins": _mean([r.get("entry_position") for r in wins]),
      "avg_entry_pos_losses": _mean([r.get("entry_position") for r in losses]),
      "dealing_range_brackets_pct": (
        round(100.0 * len(brackets_true) / len(brackets_known), 1)
        if brackets_known else None
      ),
      "avg_confluence_wins": _mean([
        r.get("detector_confluence")
        for r in wins if r.get("detector_matched")
      ]),
      "avg_confluence_losses": _mean([
        r.get("detector_confluence")
        for r in losses if r.get("detector_matched")
      ]),
      "avg_technique_near_wins": _mean([
        r.get("technique_near_count") for r in wins
      ]),
      "avg_technique_near_losses": _mean([
        r.get("technique_near_count") for r in losses
      ]),
      "technique_near_zero_pct": round(
        100.0 * sum(1 for r in items if int(r.get("technique_near_count") or 0) == 0) / n,
        1,
      ),
      "nearest_zone_reason_hits_wins": _reason_freq(
        wins, key="nearest_zone_reasons",
      ),
      "nearest_zone_reason_hits_losses": _reason_freq(
        losses, key="nearest_zone_reasons",
      ),
      "detector_reason_hits_wins": _reason_freq(
        [r for r in wins if r.get("detector_matched")],
        key="detector_reasons",
      ),
      "detector_reason_hits_losses": _reason_freq(
        [r for r in losses if r.get("detector_matched")],
        key="detector_reasons",
      ),
    }
    cards.append(cell)
  return {
    "cells": cards,
    "trade_count": len(rows),
    "diagnostics": diagnostics,
  }


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


async def run(
  limit: int | None = None,
  *,
  settings_mode: str = "runtime",
  scan_bars: int = 6,
  match_atr: float = 0.5,
) -> dict[str, Any]:
  dsn = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN")
  if not dsn:
    raise SystemExit("DATABASE_URL required")
  dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
  settings = _resolve_settings(settings_mode)
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
        features.append(
          _feature_row(
            signal=trade,
            frames=frames,
            strategy=strategy,
            settings=settings,
            scan_bars=scan_bars,
            match_atr=match_atr,
          )
        )
      except Exception as exc:
        errors.append({"signal_id": trade["id"], "error": str(exc)})
    scorecard = _scorecard(features)
    return {
      "generated_at": datetime.now(timezone.utc).isoformat(),
      "scope": "discovery_only",
      "settings_mode": settings_mode,
      "settings_fingerprint_sha256": _settings_fingerprint(settings),
      "scan_bars": scan_bars,
      "match_atr": match_atr,
      "coverage": {
        "charted_closed": len(trades),
        "replayed": len(features),
        "errors": len(errors),
      },
      "scorecard": scorecard,
      "features": features,
      "errors": errors[:40],
    }
  finally:
    await pool.close()


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--json", default="-")
  parser.add_argument("--limit", type=int, default=None)
  parser.add_argument(
    "--settings",
    choices=("runtime", "default"),
    default="runtime",
    help="DetectorSettings source (default: runtime / deployed config)",
  )
  parser.add_argument(
    "--scan-bars",
    type=int,
    default=6,
    help="M5 bars before fill to scan for the earliest matching detection",
  )
  parser.add_argument(
    "--match-atr",
    type=float,
    default=0.5,
    help="ATR multiples from entry_zone edge for detector_matched",
  )
  args = parser.parse_args()
  print(
    "manual_formula_replay scope=discovery_only "
    "(no evaluate_entry_activation / M1 trigger / entry-location gates)",
    file=sys.stderr,
  )
  payload = asyncio.run(
    run(
      limit=args.limit,
      settings_mode=args.settings,
      scan_bars=args.scan_bars,
      match_atr=args.match_atr,
    )
  )
  text = json.dumps(payload, indent=2, default=str)
  if args.json == "-":
    sys.stdout.write(text)
  else:
    with open(args.json, "w", encoding="utf-8") as fh:
      fh.write(text)
    sc = payload["scorecard"]
    print(
      f"replayed={payload['coverage']['replayed']} "
      f"errors={payload['coverage']['errors']} "
      f"settings={payload['settings_mode']} "
      f"scan_bars={payload['scan_bars']}",
      file=sys.stderr,
    )
    diag = sc.get("diagnostics") or {}
    print(
      f"htf_direction_agreement={diag.get('htf_direction_agreement')} "
      f"bias={diag.get('htf_bias_distribution')}",
      file=sys.stderr,
    )
    for cell in sc.get("cells", []):
      print(
        f"{cell['strategy']:22} {cell['direction']:4} n={cell['n']:2} "
        f"WR={cell['win_pct']:5} E={cell['expectancy_pips']:6} "
        f"det={cell['detector_matched_win_pct']} "
        f"miss={cell['detector_miss_win_pct']} "
        f"htf_suppressed={cell.get('htf_suppressed_reason')} "
        f"underpowered={cell.get('cell_underpowered')}",
        file=sys.stderr,
      )


if __name__ == "__main__":
  main()

"""Price-action scanner over closed Redis OHLC bars."""

import json
import logging
import math
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Awaitable, Callable, Iterable

from app.persistence import redis_state
from app.core.config import settings
from app.analysis.detectors import (
  DEFAULT_DETECTORS,
  DetectionContext,
  DetectionResult,
  DetectorSettings,
  SetupDetector,
  build_context,
)
from app.analysis.market_map import MarketMap, build_map, map_reference, rail_reference
from app.analysis.market_map_delivery import cache_analysis
from app.analysis.ohlc_source import RedisOHLCSource
from app.analysis.structure import Zone
from app.core.symbols import SYMBOLS, pip_for
from app.bot.client import send_scanner_with_retry

log = logging.getLogger(__name__)

NotifyFn = Callable[..., Awaitable[Any]]


class SpotSnapshot:
  def __init__(self, price: float, ts: int, fresh: bool) -> None:
    self.price = price
    self.ts = ts
    self.fresh = fresh


def _csv(value: str) -> list[str]:
  return [
    item.strip().upper()
    for item in value.split(",")
    if item.strip()
  ]


def _watched_symbols() -> set[str]:
  return set(_csv(settings.scanner_symbols))


def _htf_tfs() -> list[str]:
  return _csv(settings.scanner_htf)


def _all_tfs(exec_tf: str, htf_tfs: Iterable[str]) -> list[str]:
  result = [exec_tf.upper()]
  for tf in htf_tfs:
    tf = tf.upper()
    if tf not in result:
      result.append(tf)
  return result


def _detector_settings() -> DetectorSettings:
  return DetectorSettings(
    confluence_floor=settings.scanner_confluence_floor,
    max_entry_atr=settings.max_entry_atr,
    range_lookback=settings.range_lookback,
    atr_length=settings.atr_length,
    swing_fractal_n=settings.swing_fractal_n,
    zigzag_pct=settings.zigzag_pct,
    zigzag_atr_mult=settings.zigzag_atr_mult,
    displacement_atr_mult=settings.displacement_atr_mult,
    zone_width=settings.zone_width,
    zone_merge_overlap=settings.zone_merge_overlap,
    max_merged_zone_atr=settings.max_merged_zone_atr,
    equal_tol_atr=settings.equal_tol_atr,
    level_cluster_atr=settings.level_cluster_atr,
    round_step=settings.round_step,
    key_level_min_touches=settings.key_level_min_touches,
    momentum_lookback=settings.momentum_lookback,
    momentum_body_frac=settings.momentum_body_frac,
    session_asia_start=settings.session_asia_start,
    session_london_start=settings.session_london_start,
    session_ny_start=settings.session_ny_start,
    daily_rollover_utc_hour=settings.daily_rollover_utc_hour,
    eq_band=settings.eq_band,
    strict_pd_gate=settings.strict_pd_gate,
    sweep_body_frac=settings.sweep_body_frac,
    sweep_react_bars=settings.sweep_react_bars,
    inducement_band_atr=settings.inducement_band_atr,
    max_zone_width_atr=settings.max_zone_width_atr,
    proximal_band_atr=settings.proximal_band_atr,
    chop_filter_enabled=settings.chop_filter_enabled,
    chop_range_atr=settings.chop_range_atr,
    chop_lookback=settings.chop_lookback,
    chop_edge_frac=settings.chop_edge_frac,
    tl_min_touches=settings.tl_min_touches,
    tl_tol_atr=settings.tl_tol_atr,
    tl_max_slope_atr=settings.tl_max_slope_atr,
    coil_contract=settings.coil_contract,
    breakout_buffer_atr=settings.breakout_buffer_atr,
    breakout_accept_bars=settings.breakout_accept_bars,
    breakout_max_age_bars=settings.breakout_max_age_bars,
    allow_counter_trend=settings.allow_counter_trend,
    counter_min_zone_score=settings.counter_min_zone_score,
    counter_extreme_pd=settings.counter_extreme_pd,
    counter_level_min_touches=settings.counter_level_min_touches,
    range_scalp_enabled=settings.range_scalp_enabled,
    range_scalp_lookback=settings.range_scalp_lookback,
    range_scalp_cluster_atr=settings.range_scalp_cluster_atr,
    range_scalp_min_touches=settings.range_scalp_min_touches,
    range_scalp_min_wick_frac=settings.range_scalp_min_wick_frac,
    range_scalp_entry_tol_atr=settings.range_scalp_entry_tol_atr,
    range_scalp_min_width_atr=settings.range_scalp_min_width_atr,
    range_scalp_max_width_atr=settings.range_scalp_max_width_atr,
    range_scalp_min_room_atr=settings.range_scalp_min_room_atr,
    range_scalp_break_closes=settings.range_scalp_break_closes,
    range_scalp_min_wick_rejections=settings.range_scalp_min_wick_rejections,
    range_scalp_allow_rejection_only=settings.range_scalp_allow_rejection_only,
  )


def _parse_bar_event(data: object) -> tuple[str, str, str] | None:
  text = data.decode() if isinstance(data, bytes) else str(data)
  parts = text.strip().split(":")
  if len(parts) < 3:
    return None
  symbol, tf = parts[0].upper(), parts[1].upper()
  return symbol, tf, ":".join(parts[2:])


def _price_text(value: float, symbol: str, *, grouped: bool = False) -> str:
  digits = int(SYMBOLS.get(symbol.upper(), {}).get("digits", 2))
  spec = f",.{digits}f" if grouped else f".{digits}f"
  return f"{value:{spec}}".rstrip("0").rstrip(".")


def _pip_size(symbol: str) -> float:
  try:
    return pip_for(symbol)
  except KeyError:
    return 1.0


def _level_bucket(symbol: str, level: float, bucket_pips: int) -> str:
  pip = _pip_size(symbol)
  unit = max(1, int(bucket_pips)) * pip
  bucket = round(float(level) / unit) * unit
  return _price_text(bucket, symbol)


def _dedup_key(symbol: str, tf: str, result: DetectionResult) -> str:
  bucket = _level_bucket(
    symbol,
    result.key_level,
    settings.scanner_level_bucket,
  )
  return f"scanner:alerted:{symbol}:{tf}:{result.setup}:{bucket}"


def _band_dedup_key(symbol: str, result: DetectionResult) -> str:
  midpoint = (result.entry_zone.low + result.entry_zone.high) / 2
  bucket = _level_bucket(
    symbol,
    midpoint,
    settings.scanner_level_bucket,
  )
  return f"scanner:alerted_band:{symbol}:{result.direction}:{bucket}"


# --- B3: setup invalidation --------------------------------------------------
# Mirrors the *pattern* of worker.py's _apply_box_retirement (autotrade path):
# a retirement-flag key with a TTL, checked on every subsequent scan, cleared
# once fired so a broken setup is never re-announced as invalidated twice.
# Cannot reuse that function directly - it operates on AutoScalpDecision/
# auto_trade:box:* state, a different pipeline entirely (see its docstring).
_ACTIVE_SETUP_TTL_SECONDS = 4 * 3600
_INVALIDATION_BREAK_BUFFER_ATR = 0.1


def _active_setup_key(
  symbol: str,
  tf: str,
  setup: str,
  direction: str,
) -> str:
  slug = setup.lower().replace(" ", "_")
  return f"scanner:setup:active:{symbol.upper()}:{tf.upper()}:{slug}:{direction.upper()}"


async def _track_active_setups(
  client: Any,
  symbol: str,
  tf: str,
  sent: list[DetectionResult],
) -> None:
  for result in sent:
    payload = json.dumps({
      "setup": result.setup,
      "direction": result.direction,
      "zone_low": result.entry_zone.low,
      "zone_high": result.entry_zone.high,
      "confluence": result.confluence,
    }, separators=(",", ":"))
    await client.set(
      _active_setup_key(symbol, tf, result.setup, result.direction),
      payload,
      ex=_ACTIVE_SETUP_TTL_SECONDS,
    )


def _format_invalidation(state: dict[str, Any], symbol: str, tf: str) -> str:
  direction = str(state.get("direction", ""))
  setup = str(state.get("setup", "setup"))
  low = float(state.get("zone_low", 0.0))
  high = float(state.get("zone_high", 0.0))
  return (
    f"⌛ <b>{escape(symbol)} {escape(tf)} · SETUP INVALIDATED</b>\n"
    f"{escape(direction)} · {escape(setup)} · zone "
    f"{_zone_text(Zone(low, high, 'demand' if direction == 'BUY' else 'supply'), symbol)} "
    "no longer holds - structure broke through it."
  )


async def _check_setup_invalidations(
  client: Any,
  symbol: str,
  tf: str,
  df: Any,
  notify: NotifyFn,
  atr: float,
) -> None:
  if df.empty:
    return
  close = float(df["close"].iloc[-1])
  if not math.isfinite(close):
    return
  buffer = max(0.0, _INVALIDATION_BREAK_BUFFER_ATR) * max(0.0, atr)
  pattern = f"scanner:setup:active:{symbol.upper()}:{tf.upper()}:*"
  async for key in client.scan_iter(match=pattern):
    raw = await client.get(key)
    if not raw:
      continue
    try:
      state = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
      continue
    direction = state.get("direction")
    try:
      zone_low = float(state["zone_low"])
      zone_high = float(state["zone_high"])
    except (KeyError, TypeError, ValueError):
      await client.delete(key)
      continue
    invalidated = (
      close > zone_high + buffer if direction == "SELL"
      else close < zone_low - buffer if direction == "BUY"
      else False
    )
    if not invalidated:
      continue
    await client.delete(key)
    if settings.telegram_owner_id:
      await notify(
        _format_invalidation(state, symbol, tf),
        chat_id=settings.telegram_owner_id,
      )


def _htf_bias_text(ctx: DetectionContext, htf_order: list[str]) -> str:
  for tf in htf_order:
    structure = ctx.structures.get(tf)
    if structure and structure.bias == ctx.htf_bias and structure.bias != "range":
      return f"{ctx.htf_bias} ({tf})"
  if ctx.htf_bias != "range":
    return f"{ctx.htf_bias} ({ctx.tf})"
  return "range"


def _zone_text(zone: Zone, symbol: str, *, grouped: bool = False) -> str:
  return (
    f"{_price_text(zone.low, symbol, grouped=grouped)}"
    f"–{_price_text(zone.high, symbol, grouped=grouped)}"
  )


def _copy_draft(symbol: str, result: DetectionResult) -> str | None:
  """Build an editable one-line command without inventing SL/TP levels."""
  if symbol.upper() != "XAU":
    return None
  setup = re.sub(r"[^a-z0-9]+", "-", result.setup.lower()).strip("-")
  grade = "*" * max(1, min(3, int(result.confluence)))
  entry = (
    f"{_price_text(result.entry_zone.low, symbol)}-"
    f"{_price_text(result.entry_zone.high, symbol)}"
  )
  return (
    f"gold {result.direction.lower()} entry zone ({entry}) "
    f"/ sl SL / tp TP1/TP2/TP3 / setup {setup} {grade}"
  )


def _format_detection(
  symbol: str,
  tf: str,
  ctx: DetectionContext,
  result: DetectionResult,
  htf_order: list[str],
  also: list[DetectionResult] | None = None,
  market_map: MarketMap | None = None,
) -> str:
  stars = "⭐" * max(1, min(3, int(result.confluence)))
  direction_icon = "🟢" if result.direction.upper() == "BUY" else "🔴"
  extra_reasons = [
    reason for reason in result.reasons
    if not reason.lower().startswith("htf bias")
  ][:6 if result.setup in {"Box Breakout", "Range Edge Scalp"} else 2]
  lines = [
    f"🔎 <b>{escape(symbol)} {escape(tf)} · SETUP FORMING</b>",
    (
      f"{direction_icon} <b>{escape(result.direction)} · "
      f"{escape(result.setup)}</b> · {stars}"
    ),
  ]
  if result.mode == "range_scalp":
    lines.append("↔️ <b>Mode:</b> RANGE SCALP · two-sided local range")
  elif result.mode != "with_trend":
    label = "reaction scalp" if result.mode == "counter_reaction" else "counter swing"
    lines.append(
      f"⚠️ <b>Mode:</b> Counter-trend · {label}"
    )
  lines.extend([
    "",
    "📍 <b>Trade area</b>",
    _price_line(symbol, tf, ctx, result),
    (
      "• <b>Entry zone:</b> "
      f"<b>{_zone_text(result.entry_zone, symbol, grouped=True)}</b>"
    ),
    (
      "• <b>Key level:</b> "
      f"<b>{_price_text(result.key_level, symbol, grouped=True)}</b>"
    ),
    "",
    "🧭 <b>Context</b>",
    f"• <b>HTF bias:</b> {escape(_htf_bias_text(ctx, htf_order))}",
  ])
  regime_line = _regime_line(symbol, tf, ctx)
  if regime_line:
    lines.append(f"• {regime_line}")
  if market_map is not None:
    reference = map_reference(
      market_map,
      result.direction,
      result.entry_zone.low,
      result.entry_zone.high,
    )
    if reference:
      lines.append(f"• {escape(reference)}")
    rail = rail_reference(
      market_map,
      result.entry_zone.low,
      result.entry_zone.high,
    )
    if rail:
      lines.append(f"• {escape(rail)}")
  lines.extend(f"• {escape(reason)}" for reason in extra_reasons)
  for extra in also or []:
    extra_stars = "⭐" * max(1, min(3, int(extra.confluence)))
    lines.append(
      "• <b>Also:</b> "
      f"{escape(_compact_setup(extra.setup))} · "
      f"{escape(_zone_text(extra.entry_zone, symbol, grouped=True))} "
      f"{extra_stars}"
    )
  draft = _copy_draft(symbol, result)
  if draft:
    lines.extend([
      "",
      "📋 <b>Copy draft</b> <i>· fill SL/TP</i>",
      f"<code>{escape(draft)}</code>",
    ])
  lines.append("→ Review confirmation, SL &amp; TP before posting.")
  return "\n".join(lines)


def _regime_line(symbol: str, tf: str, ctx: DetectionContext) -> str | None:
  regime = getattr(ctx, "regime", None)
  if regime is None or getattr(regime, "kind", None) != "chop":
    return None
  low = _price_text(float(regime.range_low), symbol, grouped=True)
  high = _price_text(float(regime.range_high), symbol, grouped=True)
  return f"≈ range-bound {low}-{high} ({escape(tf.upper())}) · fading edge"


def _price_line(
  symbol: str,
  tf: str,
  ctx: DetectionContext,
  result: DetectionResult,
) -> str:
  if ctx.spot_price is not None:
    return (
      "• <b>Price now:</b> "
      f"<b>{_price_text(result.current_price, symbol, grouped=True)}</b> "
      "<i>(live)</i>"
    )
  return (
    "• <b>Trigger close:</b> "
    f"<b>{_price_text(result.current_price, symbol, grouped=True)}</b> "
    f"<i>({tf.upper()} · {_trigger_close_text(ctx, tf)})</i>"
  )


def _trigger_close_text(ctx: DetectionContext, tf: str) -> str:
  try:
    frame = ctx.frames[ctx.tf]
    ts = frame.index[-1]
    close_ts = ts.to_pydatetime() + timedelta(seconds=_tf_seconds(tf))
    close_ts = close_ts.astimezone(timezone.utc)
    return close_ts.strftime("%H:%M UTC")
  except Exception:
    return "trigger bar"


def _tf_seconds(tf: str) -> int:
  tf = tf.upper()
  if tf.startswith("M") and tf[1:].isdigit():
    return int(tf[1:]) * 60
  if tf.startswith("H") and tf[1:].isdigit():
    return int(tf[1:]) * 3600
  return 0


def _compact_setup(setup: str) -> str:
  return setup.replace(" & ", "&").replace(" ", "")


async def _load_frames(
  source: RedisOHLCSource,
  symbol: str,
  exec_tf: str,
  htf_order: list[str],
  window: int | None = None,
) -> dict[str, Any]:
  frames = {}
  count = max(50, int(window or settings.scanner_window))
  for tf in _all_tfs(exec_tf, htf_order):
    df = await source.window(symbol, tf, count)
    if not df.empty:
      frames[tf] = df
  return frames


async def _load_spot_snapshot(client: Any, symbol: str) -> SpotSnapshot | None:
  raw = await client.get(f"price:{symbol.upper()}:spot")
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    payload = json.loads(text)
    bid = float(payload["bid"])
    ask = float(payload["ask"])
    ts = int(payload["ts"])
  except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None
  price = (bid + ask) / 2
  now = int(datetime.now(timezone.utc).timestamp())
  return SpotSnapshot(
    price=price,
    ts=ts,
    fresh=now - ts <= max(0, settings.spot_fresh_secs),
  )


def _attach_price_context(
  ctx: DetectionContext,
  spot: SpotSnapshot | None,
  event_ts: str,
  df: Any,
) -> DetectionContext:
  price, ts = _trusted_spot_values(spot, df)
  try:
    return replace(ctx, spot_price=price, spot_ts=ts, trigger_ts=event_ts)
  except TypeError:
    setattr(ctx, "spot_price", price)
    setattr(ctx, "spot_ts", ts)
    setattr(ctx, "trigger_ts", event_ts)
    return ctx


def _trusted_spot_values(
  spot: SpotSnapshot | None,
  df: Any,
) -> tuple[float | None, int | None]:
  if spot is None or not spot.fresh:
    return None, None

  close = float(df["close"].iloc[-1])
  gate = max(0.0, settings.spot_max_deviation_pct) / 100.0
  bad = (
    not math.isfinite(spot.price)
    or spot.price <= 0
    or not math.isfinite(close)
    or close <= 0
    or abs(spot.price - close) / close > gate
  )
  if bad:
    log.warning(
      "spot %s implausible vs close %s (deviation gate %.1f%%) - "
      "falling back to bar close",
      spot.price,
      close,
      settings.spot_max_deviation_pct,
    )
    return None, None
  return spot.price, spot.ts


def _digest_results(
  results: list[DetectionResult],
) -> tuple[list[DetectionResult], list[dict[str, Any]]]:
  primary, primary_conflicts = _suppress_overlaps([
    result for result in results
    if result.mode in {"with_trend", "range_scalp"}
  ])
  if primary:
    candidates, conflicts = primary, primary_conflicts
  else:
    candidates, conflicts = _suppress_overlaps([
      result for result in results
      if result.mode not in {"with_trend", "range_scalp"}
    ])
  ordered = sorted(candidates, key=_result_rank)
  return ordered[:max(1, settings.scanner_top_n)], conflicts


def _conflict_record(
  stronger: DetectionResult,
  weaker: DetectionResult,
  outcome: str,
) -> dict[str, Any]:
  return {
    "outcome": outcome,  # "stronger_kept" | "both_dropped"
    "a": {
      "setup": stronger.setup,
      "direction": stronger.direction,
      "confluence": stronger.confluence,
    },
    "b": {
      "setup": weaker.setup,
      "direction": weaker.direction,
      "confluence": weaker.confluence,
    },
  }


def _suppress_overlaps(
  results: list[DetectionResult],
) -> tuple[list[DetectionResult], list[dict[str, Any]]]:
  """Same-direction overlap is a duplicate - keep the higher-ranked, drop the
  other. Opposite-direction overlap is a contradiction: two credible,
  contradictory readings mean the market is undecided, so unless the
  higher-ranked result's confluence decisively beats the other's, both are
  dropped rather than shipping either as a coin flip.
  """
  ordered = sorted(results, key=_result_rank)
  selected: list[DetectionResult] = []
  conflicts: list[dict[str, Any]] = []
  same_threshold = max(0.0, settings.alert_overlap_suppress)
  conflict_threshold = max(0.0, settings.scanner_conflict_overlap)
  margin = max(0.0, settings.scanner_conflict_margin)
  for result in ordered:
    same_direction_duplicate = any(
      result.direction == kept.direction
      and _zone_overlap_ratio(result.entry_zone, kept.entry_zone) >= same_threshold
      for kept in selected
    )
    if same_direction_duplicate:
      continue
    # `selected` is built in rank order, so the first opposing overlap found
    # is always the strongest (highest-ranked) survivor so far.
    opposing = next(
      (
        kept for kept in selected
        if result.direction != kept.direction
        and _zone_overlap_ratio(result.entry_zone, kept.entry_zone)
          >= conflict_threshold
      ),
      None,
    )
    if opposing is not None:
      if opposing.confluence - result.confluence >= margin:
        conflicts.append(_conflict_record(opposing, result, "stronger_kept"))
        continue
      selected.remove(opposing)
      conflicts.append(_conflict_record(opposing, result, "both_dropped"))
      continue
    selected.append(result)
  return selected, conflicts


def _result_rank(result: DetectionResult) -> tuple[float, float, float]:
  return (
    -float(result.confluence),
    -float(getattr(result.entry_zone, "score", 0.0)),
    _result_zone_distance(result),
  )


def _result_zone_distance(result: DetectionResult) -> float:
  zone = result.entry_zone
  price = result.current_price
  if zone.low <= price <= zone.high:
    return 0.0
  return min(abs(price - zone.low), abs(price - zone.high))


def _zone_overlap_ratio(first: Zone, second: Zone) -> float:
  overlap = min(first.high, second.high) - max(first.low, second.low)
  if overlap <= 0:
    return 0.0
  smaller = min(first.high - first.low, second.high - second.low)
  if smaller <= 0:
    return 1.0
  return overlap / smaller


async def _notify_digest_once(
  client: Any,
  symbol: str,
  tf: str,
  ctx: DetectionContext,
  results: list[DetectionResult],
  notify: NotifyFn,
  htf_order: list[str],
  market_map: MarketMap | None = None,
) -> list[DetectionResult]:
  if not results:
    return []
  if not settings.telegram_owner_id:
    log.info(
      "scanner detection suppressed: TELEGRAM_OWNER_ID not set "
      "symbol=%s tf=%s count=%s",
      symbol,
      tf,
      len(results),
    )
    return []

  band_key = _band_dedup_key(symbol, results[0])
  if await client.get(band_key) is not None:
    log.debug(
      "scanner detection suppressed by zone band TTL symbol=%s tf=%s key=%s",
      symbol,
      tf,
      band_key,
    )
    return []

  claimed_results = []
  for result in results:
    key = _dedup_key(symbol, tf, result)
    claimed = await client.set(
      key,
      "1",
      ex=settings.scanner_alert_ttl,
      nx=True,
    )
    if claimed:
      claimed_results.append(result)
  if not claimed_results:
    return []
  await client.set(band_key, "1", ex=settings.zone_alert_ttl)
  await notify(
    _format_detection(
      symbol,
      tf,
      ctx,
      claimed_results[0],
      htf_order,
      claimed_results[1:],
      market_map,
    ),
    chat_id=settings.telegram_owner_id,
  )
  await _track_active_setups(client, symbol, tf, claimed_results)
  return claimed_results


async def _record_status(
  client: Any,
  *,
  symbol: str,
  tf: str,
  event_ts: str,
  frames: dict[str, Any],
  detected: list[DetectionResult],
  sent: list[DetectionResult],
  status: str,
  market_map: MarketMap | None = None,
  scalp: dict[str, Any] | None = None,
  conflicts: list[dict[str, Any]] | None = None,
) -> None:
  map_counts = {
    "buys": len(market_map.buys) if market_map is not None else 0,
    "sells": len(market_map.sells) if market_map is not None else 0,
    "majors": len(market_map.majors) if market_map is not None else 0,
  }
  payload = {
    "status": status,
    "symbol": symbol,
    "tf": tf,
    "event_ts": event_ts,
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "frames": {
      name: len(frame)
      for name, frame in sorted(frames.items())
    },
    "detected": [
      {
        "setup": item.setup,
        "mode": item.mode,
        "direction": item.direction,
        "key_level": item.key_level,
        "entry_zone": {
          "low": item.entry_zone.low,
          "high": item.entry_zone.high,
          "score": getattr(item.entry_zone, "score", 0.0),
          "score_reasons": list(getattr(item.entry_zone, "score_reasons", []) or []),
        },
        "current_price": item.current_price,
        "confluence": item.confluence,
      }
      for item in detected
    ],
    "conflicts": conflicts or [],
    "sent": len(sent),
    "map": map_counts,
    "map_summary": (
      f"map: buys={map_counts['buys']} sells={map_counts['sells']} "
      f"majors={map_counts['majors']}"
    ),
    "scalp": scalp or {
      "state": "unavailable",
      "barriers": 0,
      "supports": 0,
      "resistances": 0,
      "range": None,
    },
  }
  encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
  await client.set("scanner:last_tick", encoded)
  await client.set(f"scanner:last_tick:{symbol}:{tf}", encoded)


# --- B5: per-detector reporting ---------------------------------------------
# scanner.py already builds `detected` on every scan (line ~723) but nothing
# reads it historically - `scanner:last_tick*` is overwrite-only, holding
# only the single latest snapshot. This appends a bounded, queryable history
# so the BOX_* tuning and regime-router-exclusivity questions (out of scope
# for this PR) have data to work from before anyone touches those constants.
_DETECT_LOG_MAXLEN = 5000
_DETECT_LOG_TTL_SECONDS = 8 * 24 * 3600


def _detect_log_key(symbol: str, tf: str) -> str:
  return f"scanner:detect_log:{symbol.upper()}:{tf.upper()}"


async def _append_detect_log(
  client: Any,
  symbol: str,
  tf: str,
  detected: list[DetectionResult],
  sent: list[DetectionResult],
  conflicts: list[dict[str, Any]],
) -> None:
  if not detected:
    return
  sent_keys = {(item.setup, item.direction) for item in sent}
  conflict_keys = {
    (side["setup"], side["direction"])
    for record in conflicts
    for side in (record["a"], record["b"])
  }
  entries = []
  for item in detected:
    detection_key = (item.setup, item.direction)
    if detection_key in sent_keys:
      outcome = "sent"
    elif detection_key in conflict_keys:
      outcome = "dropped_conflict"
    else:
      outcome = "suppressed_duplicate"
    entries.append({
      "setup": item.setup,
      "confluence": item.confluence,
      "outcome": outcome,
    })
  record = json.dumps({
    "recorded_at": datetime.now(timezone.utc).timestamp(),
    "entries": entries,
  }, separators=(",", ":"))
  key = _detect_log_key(symbol, tf)
  await client.lpush(key, record)
  await client.ltrim(key, 0, _DETECT_LOG_MAXLEN - 1)
  await client.expire(key, _DETECT_LOG_TTL_SECONDS)


async def scan_report(
  client: Any,
  symbol: str,
  tf: str,
  hours: float = 24.0,
) -> dict[str, dict[str, float]]:
  """Aggregate the last ``hours`` of detections into a per-detector table:
  fire count, mean confluence, times sent (~ranked first and delivered),
  times suppressed as a same-direction duplicate, times dropped as an
  opposite-direction conflict. Read-only - does not tune any BOX_* constant.
  """
  cutoff = datetime.now(timezone.utc).timestamp() - max(0.0, hours) * 3600
  raw = await client.lrange(_detect_log_key(symbol, tf), 0, _DETECT_LOG_MAXLEN - 1)
  totals: dict[str, dict[str, float]] = {}
  for item in raw:
    try:
      record = json.loads(item)
    except (TypeError, json.JSONDecodeError):
      continue
    if float(record.get("recorded_at", 0.0)) < cutoff:
      continue
    for entry in record.get("entries", []):
      setup = str(entry.get("setup", "unknown"))
      row = totals.setdefault(setup, {
        "fires": 0.0,
        "confluence_sum": 0.0,
        "sent": 0.0,
        "suppressed_duplicate": 0.0,
        "dropped_conflict": 0.0,
      })
      row["fires"] += 1
      row["confluence_sum"] += float(entry.get("confluence", 0))
      outcome = entry.get("outcome")
      if outcome in row:
        row[outcome] += 1
  return {
    setup: {
      "fires": row["fires"],
      "mean_confluence": row["confluence_sum"] / row["fires"] if row["fires"] else 0.0,
      "sent": row["sent"],
      "suppressed_duplicate": row["suppressed_duplicate"],
      "dropped_conflict": row["dropped_conflict"],
    }
    for setup, row in totals.items()
  }


def format_scan_report(
  rows: dict[str, dict[str, float]],
  symbol: str,
  tf: str,
  hours: float,
) -> str:
  if not rows:
    return (
      f"📊 <b>Scan report · {escape(symbol)} {escape(tf)} · "
      f"{hours:.0f}h</b>\nNo detections recorded in this window."
    )
  lines = [f"📊 <b>Scan report · {escape(symbol)} {escape(tf)} · {hours:.0f}h</b>", ""]
  for setup, row in sorted(rows.items(), key=lambda item: -item[1]["fires"]):
    lines.append(
      f"<b>{escape(setup)}</b> · fires {int(row['fires'])} · "
      f"avg {row['mean_confluence']:.1f}★ · sent {int(row['sent'])} · "
      f"dup {int(row['suppressed_duplicate'])} · "
      f"conflict {int(row['dropped_conflict'])}"
    )
  return "\n".join(lines)


def _scalp_status(ctx: DetectionContext) -> dict[str, Any]:
  st = ctx.structures.get(ctx.tf)
  if st is None:
    return {
      "state": "missing_structure",
      "barriers": 0,
      "supports": 0,
      "resistances": 0,
      "range": None,
    }
  barriers = list(st.scalp_barriers)
  scalp_range = st.scalp_range
  enabled = ctx.settings.range_scalp_enabled
  state = "disabled" if not enabled else "no_range"
  range_payload = None
  if scalp_range is not None:
    frame = ctx.frames.get(ctx.tf)
    touched = []
    if frame is not None and not frame.empty:
      row = frame.iloc[-1]
      if float(row["low"]) <= scalp_range.lower.high:
        touched.append("lower")
      if float(row["high"]) >= scalp_range.upper.low:
        touched.append("upper")
    if enabled:
      state = "edge_touch" if touched else "waiting_edge"
    range_payload = {
      "lower": scalp_range.lower.level,
      "upper": scalp_range.upper.level,
      "eq": scalp_range.eq,
      "width_atr": scalp_range.width_atr,
      "quality": scalp_range.quality,
      "touched": touched,
    }
  return {
    "state": state,
    "barriers": len(barriers),
    "supports": sum(barrier.side == "support" for barrier in barriers),
    "resistances": sum(barrier.side == "resistance" for barrier in barriers),
    "range": range_payload,
  }


async def _load_market_context_for_symbol(
  symbol: str,
  *,
  source: RedisOHLCSource | None = None,
  client: Any | None = None,
  event_ts: str | None = None,
  exec_tf: str | None = None,
  htf_order: list[str] | None = None,
  cache_market_analysis: bool = True,
  window: int | None = None,
) -> tuple[DetectionContext | None, dict[str, Any]]:
  symbol = symbol.upper()
  client = client or redis_state.get_client()
  source = source or RedisOHLCSource(client)
  exec_tf = (exec_tf or settings.scanner_exec_tf).upper()
  htf_order = htf_order or _htf_tfs()
  spot = await _load_spot_snapshot(client, symbol)
  frames = await _load_frames(
    source,
    symbol,
    exec_tf,
    htf_order,
    window=window,
  )
  if exec_tf not in frames:
    return None, frames
  trigger = event_ts or str(frames[exec_tf].index[-1])
  ctx = build_context(
    symbol,
    exec_tf,
    frames,
    _detector_settings(),
    htf_order,
  )
  ctx = _attach_price_context(ctx, spot, trigger, frames[exec_tf])
  analysis = getattr(ctx, "analysis", None)
  if analysis is not None and cache_market_analysis:
    price = (
      float(ctx.spot_price)
      if getattr(ctx, "spot_price", None) is not None
      else float(frames[exec_tf]["close"].iloc[-1])
    )
    cache_analysis(symbol, analysis, price, frames[exec_tf].index[-1])
  return ctx, frames


async def _handle_event(
  data: object,
  *,
  source: RedisOHLCSource | None = None,
  client: Any | None = None,
  detectors: Iterable[SetupDetector] | None = None,
  notify: NotifyFn | None = None,
) -> list[DetectionResult]:
  parsed = _parse_bar_event(data)
  if parsed is None:
    return []
  symbol, tf, event_ts = parsed
  exec_tf = settings.scanner_exec_tf.upper()
  if symbol not in _watched_symbols():
    return []

  if tf != exec_tf:
    return []

  client = client or redis_state.get_client()
  notify = notify or send_scanner_with_retry
  htf_order = _htf_tfs()
  ctx, frames = await _load_market_context_for_symbol(
    symbol,
    source=source,
    client=client,
    event_ts=event_ts,
  )
  if ctx is None:
    await _record_status(
      client,
      symbol=symbol,
      tf=exec_tf,
      event_ts=event_ts,
      frames=frames,
      detected=[],
      sent=[],
      status="missing_exec_frame",
    )
    return []

  exec_indicators = getattr(ctx, "indicators", {}).get(exec_tf)
  invalidation_atr = (
    float(exec_indicators.atr.iloc[-1])
    if exec_indicators is not None and not exec_indicators.atr.empty
    else 0.0
  )
  if not math.isfinite(invalidation_atr):
    invalidation_atr = 0.0
  await _check_setup_invalidations(
    client, symbol, exec_tf, frames[exec_tf], notify, invalidation_atr,
  )

  analysis = getattr(ctx, "analysis", None)
  current_map = None
  if analysis is not None:
    price = (
      float(ctx.spot_price)
      if getattr(ctx, "spot_price", None) is not None
      else float(frames[exec_tf]["close"].iloc[-1])
    )
    current_map = build_map(analysis, price, settings)
  detected = []
  for detector in detectors or DEFAULT_DETECTORS:
    result = detector(ctx)
    if result is None:
      continue
    detected.append(result)
  digest, conflicts = _digest_results(detected)
  sent = await _notify_digest_once(
    client,
    symbol,
    exec_tf,
    ctx,
    digest,
    notify,
    htf_order,
    current_map,
  )
  await _record_status(
    client,
    symbol=symbol,
    tf=exec_tf,
    event_ts=event_ts,
    frames=frames,
    detected=detected,
    sent=sent,
    status="ok",
    market_map=current_map,
    scalp=_scalp_status(ctx),
    conflicts=conflicts,
  )
  await _append_detect_log(client, symbol, exec_tf, detected, sent, conflicts)
  return sent


async def scanner_loop() -> None:
  """Subscribe to closed-bar events and analyze scanner detections."""
  if not settings.scanner_enabled:
    log.info("Price-action scanner disabled: SCANNER_ENABLED=false")
    return
  if not settings.telegram_owner_id:
    log.info(
      "Price-action scanner notifications disabled: TELEGRAM_OWNER_ID not set"
    )

  client = redis_state.get_client()
  source = RedisOHLCSource(client)
  pubsub = client.pubsub()
  await pubsub.subscribe("bars:new")
  log.info(
    "Price-action scanner watching %s on %s (%s)",
    ",".join(sorted(_watched_symbols())),
    settings.scanner_exec_tf.upper(),
    "owner DM enabled" if settings.telegram_owner_id else "analysis only",
  )
  try:
    async for message in pubsub.listen():
      if message.get("type") != "message":
        continue
      try:
        await _handle_event(message.get("data"), source=source, client=client)
      except Exception:
        log.exception("scanner tick failed")
  finally:
    await pubsub.unsubscribe("bars:new")
    await pubsub.close()

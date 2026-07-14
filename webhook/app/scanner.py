"""Price-action scanner over closed Redis OHLC bars."""

import json
import logging
from datetime import datetime, timezone
from html import escape
from typing import Any, Awaitable, Callable, Iterable

from app import redis_state
from app.config import settings
from app.detectors import (
  DEFAULT_DETECTORS,
  DetectionContext,
  DetectionResult,
  DetectorSettings,
  SetupDetector,
  build_context,
)
from app.ohlc_source import RedisOHLCSource
from app.structure import Zone
from app.symbols import SYMBOLS, pip_for
from app.tg_core import send_scanner_with_retry

log = logging.getLogger(__name__)

NotifyFn = Callable[..., Awaitable[Any]]


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
    f"-{_price_text(zone.high, symbol, grouped=grouped)}"
  )


def _format_detection(
  symbol: str,
  tf: str,
  ctx: DetectionContext,
  result: DetectionResult,
  htf_order: list[str],
) -> str:
  stars = "⭐" * max(1, min(3, int(result.confluence)))
  extra_reasons = [
    reason for reason in result.reasons
    if not reason.lower().startswith("htf bias")
  ][:2]
  reason_suffix = (
    " · " + " · ".join(escape(reason) for reason in extra_reasons)
    if extra_reasons
    else ""
  )
  return "\n".join([
    f"🔎 <b>Setup forming</b> · {escape(symbol)} {escape(tf)}",
    (
      f"{escape(result.setup)} · <b>{escape(result.direction)}</b> "
      f"· {stars}"
    ),
    (
      f"Price now <b>{_price_text(result.current_price, symbol, grouped=True)}</b> "
      f"· entry <b>{_zone_text(result.entry_zone, symbol, grouped=True)}</b> "
      f"· key <b>{_price_text(result.key_level, symbol, grouped=True)}</b>"
    ),
    f"HTF bias: {escape(_htf_bias_text(ctx, htf_order))}{reason_suffix}",
    "→ review & post if it holds",
  ])


async def _load_frames(
  source: RedisOHLCSource,
  symbol: str,
  exec_tf: str,
  htf_order: list[str],
) -> dict[str, Any]:
  frames = {}
  for tf in _all_tfs(exec_tf, htf_order):
    df = await source.window(symbol, tf, settings.scanner_window)
    if not df.empty:
      frames[tf] = df
  return frames


async def _notify_once(
  client: Any,
  symbol: str,
  tf: str,
  ctx: DetectionContext,
  result: DetectionResult,
  notify: NotifyFn,
  htf_order: list[str],
) -> bool:
  if not settings.telegram_owner_id:
    log.info(
      "scanner detection suppressed: TELEGRAM_OWNER_ID not set "
      "symbol=%s tf=%s setup=%s direction=%s level=%s",
      symbol,
      tf,
      result.setup,
      result.direction,
      _price_text(result.key_level, symbol),
    )
    return False

  key = _dedup_key(symbol, tf, result)
  claimed = await client.set(
    key,
    "1",
    ex=settings.scanner_alert_ttl,
    nx=True,
  )
  if not claimed:
    return False
  await notify(
    _format_detection(symbol, tf, ctx, result, htf_order),
    chat_id=settings.telegram_owner_id,
  )
  return True


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
) -> None:
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
    "sent": len(sent),
  }
  encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
  await client.set("scanner:last_tick", encoded)
  await client.set(f"scanner:last_tick:{symbol}:{tf}", encoded)


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
  if tf != exec_tf or symbol not in _watched_symbols():
    return []

  client = client or redis_state.get_client()
  source = source or RedisOHLCSource(client)
  notify = notify or send_scanner_with_retry
  htf_order = _htf_tfs()
  frames = await _load_frames(source, symbol, exec_tf, htf_order)
  if exec_tf not in frames:
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

  ctx = build_context(
    symbol,
    exec_tf,
    frames,
    _detector_settings(),
    htf_order,
  )
  detected = []
  sent = []
  for detector in detectors or DEFAULT_DETECTORS:
    result = detector(ctx)
    if result is None:
      continue
    detected.append(result)
    if await _notify_once(client, symbol, exec_tf, ctx, result, notify, htf_order):
      sent.append(result)
  await _record_status(
    client,
    symbol=symbol,
    tf=exec_tf,
    event_ts=event_ts,
    frames=frames,
    detected=detected,
    sent=sent,
    status="ok",
  )
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

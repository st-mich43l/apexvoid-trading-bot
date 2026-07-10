"""Notify-only price-action scanner over closed Redis OHLC bars."""

import logging
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
from app.tg_core import send_with_retry

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
    wae_fast=settings.wae_fast,
    wae_slow=settings.wae_slow,
    wae_sensitivity=settings.wae_sensitivity,
    wae_bb_length=settings.wae_bb_length,
    wae_bb_mult=settings.wae_bb_mult,
  )


def _parse_bar_event(data: object) -> tuple[str, str, str] | None:
  text = data.decode() if isinstance(data, bytes) else str(data)
  parts = text.strip().split(":")
  if len(parts) < 3:
    return None
  symbol, tf = parts[0].upper(), parts[1].upper()
  return symbol, tf, ":".join(parts[2:])


def _price_text(value: float, symbol: str) -> str:
  digits = int(SYMBOLS.get(symbol.upper(), {}).get("digits", 2))
  return f"{value:.{digits}f}".rstrip("0").rstrip(".")


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


def _zone_text(zone: Zone, symbol: str) -> str:
  return (
    f"{_price_text(zone.low, symbol)}"
    f"-{_price_text(zone.high, symbol)}"
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
      f"Key level <b>{_price_text(result.key_level, symbol)}</b> "
      f"· entry zone <b>{_zone_text(result.entry_zone, symbol)}</b>"
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
  symbol, tf, _ts = parsed
  exec_tf = settings.scanner_exec_tf.upper()
  if tf != exec_tf or symbol not in _watched_symbols():
    return []

  source = source or RedisOHLCSource(client)
  client = client or redis_state.get_client()
  notify = notify or send_with_retry
  htf_order = _htf_tfs()
  frames = await _load_frames(source, symbol, exec_tf, htf_order)
  if exec_tf not in frames:
    return []

  ctx = build_context(
    symbol,
    exec_tf,
    frames,
    _detector_settings(),
    htf_order,
  )
  sent = []
  for detector in detectors or DEFAULT_DETECTORS:
    result = detector(ctx)
    if result is None:
      continue
    if await _notify_once(client, symbol, exec_tf, ctx, result, notify, htf_order):
      sent.append(result)
  return sent


async def scanner_loop() -> None:
  """Subscribe to closed-bar events and DM owner for scanner detections."""
  if not settings.scanner_enabled:
    log.info("Price-action scanner disabled: SCANNER_ENABLED=false")
    return
  if not settings.telegram_owner_id:
    log.info("Price-action scanner disabled: TELEGRAM_OWNER_ID not set")
    return

  client = redis_state.get_client()
  source = RedisOHLCSource(client)
  pubsub = client.pubsub()
  await pubsub.subscribe("bars:new")
  log.info(
    "Price-action scanner watching %s on %s",
    ",".join(sorted(_watched_symbols())),
    settings.scanner_exec_tf.upper(),
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

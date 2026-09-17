"""Owner-facing "what's actually near the market right now" report.

Before this module, `zone_watch.list_active_zone_watches` had exactly one
caller (the execution-evaluation loop) - there was no owner-facing surface
that separated "current, actionable" structure from old structural memory
that happens to still be lifecycle-valid. This is that surface: it reuses
`app.autotrade.zone_relevance`'s pure classification (never mutates a
ZoneWatch record) purely for display.
"""

from __future__ import annotations

import json
import math
import time

from app.analysis.math_utils import atr_scalar, atr_series
from app.analysis.ohlc_source import RedisOHLCSource
from app.autotrade import zone_relevance
from app.autotrade.zone_watch import ZoneWatch, list_active_zone_watches
from app.core.config import runtime_config
from app.persistence import redis_state


async def _load_spot(client, symbol: str) -> tuple[float, float] | None:
  raw = await client.get(f"price:{symbol.upper()}:spot")
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    payload = json.loads(text)
    bid = float(payload["bid"])
    ask = float(payload["ask"])
  except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None
  if not all(math.isfinite(value) and value > 0 for value in (bid, ask)):
    return None
  return bid, ask


async def _atr_by_source_timeframe(
  client, symbol: str, timeframes: set[str],
) -> dict[str, float]:
  source = RedisOHLCSource(client)
  length = int(runtime_config.analysis.atr.length)
  result: dict[str, float] = {}
  for tf in timeframes:
    if not tf:
      continue
    df = await source.window(symbol, tf, length + 5)
    if df.empty:
      continue
    result[tf] = atr_scalar(atr_series(df, length))
  return result


def _fmt_zone(record: ZoneWatch, relevance: "zone_relevance.ZoneRelevance", now: int) -> str:
  age_min = zone_relevance.age_since_discovery_seconds(record, now) // 60
  touch_age = zone_relevance.age_since_last_touch_seconds(record, now)
  touch_text = (
    f"last touched {touch_age // 60}m ago" if touch_age is not None
    else "never touched"
  )
  dist = (
    f"{relevance.distance_atr:.2f} ATR"
    if relevance.distance_atr is not None
    else f"{relevance.distance_price:.2f} price"
  )
  sources = ",".join(record.structural_sources) or record.grade
  return (
    f"{record.direction} {record.low:.2f}-{record.high:.2f} · {dist} away · "
    f"{sources} · Grade {record.grade} · discovered {age_min}m ago · {touch_text}"
  )


async def current_market_setups_text(symbol: str = "XAU") -> str:
  sym = str(symbol or "XAU").upper()
  client = redis_state.get_client()
  records = await list_active_zone_watches(client, symbol=sym)
  spot = await _load_spot(client, sym)
  if spot is None:
    return f"<b>{sym} setups</b>\n⚠️ No live spot price — cannot classify relevance."
  bid, ask = spot
  mid = (bid + ask) / 2.0
  atr_by_tf = await _atr_by_source_timeframe(
    client, sym, {record.source_timeframe for record in records},
  )
  now = int(time.time())

  # Per-record ATR (source_timeframe may differ per zone), not one shared
  # value - classify directly instead of routing through partition_by_
  # relevance's single-atr signature.
  def _sort_key(pair):
    record, relevance = pair
    distance = relevance.distance_atr if relevance.distance_atr is not None else relevance.distance_price
    return (distance, -record.score)

  scored = [
    (record, zone_relevance.classify_zone_relevance(
      record, mid, atr_by_tf.get(record.source_timeframe),
    ))
    for record in records
  ]
  active = sorted(
    (p for p in scored if p[1].relevance in zone_relevance.ACTIVE_WATCHLIST_RELEVANCE),
    key=_sort_key,
  )
  memory = sorted(
    (p for p in scored if p[1].relevance in zone_relevance.STRUCTURAL_MEMORY_RELEVANCE),
    key=_sort_key,
  )
  coverage = zone_relevance.coverage_state(active)

  lines = [f"<b>{sym} setups</b>  ·  spot {mid:.2f}", ""]
  if not active:
    lines.append("❌ <b>NO CURRENT ACTIONABLE SETUP</b>")
    lines.append(f"coverage: {coverage.upper()}")
    if memory:
      nearest_record, nearest_relevance = memory[0]
      lines.append("")
      lines.append("nearest structural memory:")
      lines.append(f"  {_fmt_zone(nearest_record, nearest_relevance, now)}")
  else:
    lines.append(f"<b>ACTIVE / {coverage.upper()}</b>  ({len(active)})")
    for record, relevance in active:
      lines.append(f"  {_fmt_zone(record, relevance, now)}")

  if memory:
    lines.append("")
    lines.append(f"<b>STRUCTURAL MEMORY</b>  ({len(memory)}, not currently actionable)")
    for record, relevance in memory[:10]:
      lines.append(f"  {_fmt_zone(record, relevance, now)}")
    if len(memory) > 10:
      lines.append(f"  … and {len(memory) - 10} more")

  return "\n".join(lines)

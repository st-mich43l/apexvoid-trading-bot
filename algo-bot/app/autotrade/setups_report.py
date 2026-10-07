"""Owner-facing "what is near the market right now" report.

Built only from Go's published zone book: the scored major zones around the
live quote, nearest first. It never mutates anything and is display-only.
"""

from __future__ import annotations

import json
import math

from app.autotrade.go_market_map import load_go_market_map
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


# How far ahead of price a map zone is still worth listing, and how many.
_MAP_ZONE_MAX_ATR = 8.0
_MAP_ZONE_LIMIT = 6


def _map_zone_distance(entry, price: float) -> float:
  if price < entry.lo:
    return entry.lo - price
  if price > entry.hi:
    return price - entry.hi
  return 0.0


def map_zone_lines(market_map, price: float, atr: float | None) -> list[str]:
  """Go's scored major map zones near price, nearest first (display only)."""
  if market_map is None or not getattr(market_map, "entries", None):
    return []
  limit = (atr * _MAP_ZONE_MAX_ATR) if atr and atr > 0 else None
  rows = []
  for entry in market_map.entries:
    if entry.tier != "major":
      continue
    distance = _map_zone_distance(entry, price)
    if limit is not None and distance > limit:
      continue
    rows.append((distance, entry))
  rows.sort(key=lambda row: (row[0], -row[1].score))
  lines: list[str] = []
  for distance, entry in rows[:_MAP_ZONE_LIMIT]:
    side = "SELL" if entry.side == "sell" else "BUY"
    if entry.contains_price or distance <= 0.0:
      where = "price inside"
    else:
      where = f"{distance:.1f} pts" + (f" / {distance / atr:.1f} ATR" if atr and atr > 0 else "")
    tags = ",".join(entry.tags[:4])
    lines.append(
      f"  {side} {entry.lo:.2f}-{entry.hi:.2f} · {where} · {tags} · "
      f"score {entry.score:.0f}"
    )
  return lines


async def current_market_setups_text(symbol: str = "XAU") -> str:
  sym = str(symbol or "XAU").upper()
  client = redis_state.get_client()
  spot = await _load_spot(client, sym)
  if spot is None:
    return f"<b>{sym} setups</b>\n⚠️ No live spot price — cannot place zones relative to price."
  mid = sum(spot) / 2.0
  lines = [f"<b>{sym} setups</b>  ·  spot {mid:.2f}", ""]
  try:
    market_map = await load_go_market_map(sym, client)
  except Exception:  # noqa: BLE001 - display-only Go facts fail closed
    market_map = None
  m5_atr = None if market_map is None else {
    tf.upper(): value for tf, value in market_map.atr_by_timeframe.items()
  }.get("M5")
  map_lines = map_zone_lines(market_map, mid, m5_atr)
  if not map_lines:
    lines.append("❌ <b>NO MAJOR ZONE NEAR PRICE</b>")
    return "\n".join(lines)
  bias = getattr(market_map, "bias", "") or "-"
  lines.append(f"<b>MARKET MAP ZONES</b>  (bias {bias}, nearest first)")
  lines.extend(map_lines)
  return "\n".join(lines)

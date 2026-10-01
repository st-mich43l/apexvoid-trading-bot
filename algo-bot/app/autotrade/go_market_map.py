"""Owner-facing market view built only from Go Analysis Engine facts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any

from app.persistence import redis_state


@dataclass(frozen=True)
class GoMapEntry:
  side: str
  lo: float
  hi: float
  tier: str
  tags: tuple[str, ...]
  score: float
  contains_price: bool


@dataclass(frozen=True)
class GoMarketMap:
  entries: tuple[GoMapEntry, ...]
  price: float
  bias: str
  generated_at: int
  atr_by_timeframe: dict[str, float]


def _spot_price(payload: Any) -> float | None:
  if not payload:
    return None
  try:
    raw = payload.decode() if isinstance(payload, bytes) else payload
    data = json.loads(raw)
    bid = float(data["bid"])
    ask = float(data["ask"])
  except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None
  if not all(math.isfinite(value) and value > 0 for value in (bid, ask)):
    return None
  return (bid + ask) / 2.0


async def load_go_market_map(symbol: str, client: Any | None = None) -> GoMarketMap | None:
  """Read the current Go zone book; missing/stale data is unavailable."""
  symbol = str(symbol or "").upper()
  if not symbol:
    return None
  client = client or redis_state.get_client()
  raw = await client.get(f"analysis:zone_book:{symbol}")
  if not raw:
    return None
  try:
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    document = json.loads(text)
  except (TypeError, ValueError, json.JSONDecodeError):
    return None
  if not isinstance(document, dict):
    return None
  price = _spot_price(await client.get(f"price:{symbol}:spot"))
  if price is None:
    return None

  atr_by_tf: dict[str, float] = {}
  for item in document.get("entries") or ():
    if not isinstance(item, dict):
      continue
    tf = str(item.get("timeframe") or "").upper()
    try:
      atr = float(item.get("atr") or 0.0)
    except (TypeError, ValueError):
      atr = 0.0
    if tf and math.isfinite(atr) and atr > 0:
      atr_by_tf[tf] = atr

  entries: list[GoMapEntry] = []
  published = document.get("barriers")
  if not isinstance(published, list):
    published = document.get("entries")
  for item in published or ():
    if not isinstance(item, dict):
      continue
    side = str(item.get("side") or "").lower()
    if side not in {"buy", "sell"}:
      kind = str(item.get("kind") or "").lower()
      side = "buy" if kind == "demand" else "sell" if kind == "supply" else ""
    try:
      low = float(item["low"])
      high = float(item["high"])
      score = float(item.get("score", item.get("strength", 0.0)) or 0.0)
    except (KeyError, TypeError, ValueError):
      continue
    if side not in {"buy", "sell"} or not all(
      math.isfinite(value) for value in (low, high, score)
    ) or high < low:
      continue
    timeframes = item.get("source_timeframes") or [item.get("timeframe", "")]
    tags = tuple(
      ["GO", *[str(value).upper() for value in timeframes if value]]
    )
    entries.append(GoMapEntry(
      side=side,
      lo=low,
      hi=high,
      # A published barrier is already Go's normalized actionable book.
      tier="major",
      tags=tags,
      score=score,
      contains_price=low <= price <= high,
    ))
  entries.sort(key=lambda item: (0 if item.contains_price else 1, abs((item.lo + item.hi) / 2 - price), -item.score))
  return GoMarketMap(
    entries=tuple(entries),
    price=price,
    bias="Go-owned",
    generated_at=int(document.get("generated_at") or 0),
    atr_by_timeframe=atr_by_tf,
  )


def render_go_market_map(market_map: GoMarketMap, symbol: str) -> str:
  lines = [
    f"🗺️ <b>{symbol.upper()} Market Map</b>",
    f"Price: {market_map.price:.5f}",
    "Bias: Go-owned Analysis Engine",
  ]
  if not market_map.entries:
    lines.append("\nNo live Go zones currently published.")
    return "\n".join(lines)
  lines.append("\n<b>LIVE GO ZONES</b>")
  for entry in market_map.entries[:12]:
    side = "SELL" if entry.side == "sell" else "BUY"
    location = " · price inside" if entry.contains_price else ""
    tags = ",".join(entry.tags)
    lines.append(
      f"  {side} {entry.lo:.5f}-{entry.hi:.5f} · {tags} · "
      f"score {entry.score:.0f}{location}"
    )
  return "\n".join(lines)

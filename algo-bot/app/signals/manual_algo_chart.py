"""Persist Redis OHLC around owner VIP trades for later formula fitting.

Snapshots never raise into the Telegram / fill path. Empty Redis is a skip.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.analysis.ohlc_source import _normalize_price
from app.persistence import redis_state
from app.persistence import store

log = logging.getLogger(__name__)

TIMEFRAMES: tuple[str, ...] = ("M1", "M5", "M15", "H1")
_BAR_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}
_LOOKBACK = {"M1": 90, "M5": 72, "M15": 48, "H1": 36}
_LOOKAHEAD = {"issued": {"M1": 0}, "filled": {"M1": 5}, "closed": {"M1": 15}}


def _window(tf: str, event: str, ts: int) -> tuple[int, int]:
  step = _BAR_SECONDS[tf]
  start = int(ts) - _LOOKBACK[tf] * step
  extra = int(_LOOKAHEAD.get(event, {}).get(tf, 0))
  end = int(ts) + extra * step
  return start, end


def parse_ohlc_payload(raw: Any, symbol: str = "XAU") -> dict[str, Any] | None:
  if isinstance(raw, bytes):
    raw = raw.decode()
  if isinstance(raw, str):
    try:
      raw = json.loads(raw)
    except json.JSONDecodeError:
      return None
  if not isinstance(raw, dict):
    return None
  try:
    t = int(raw["t"])
    return {
      "t": t,
      "o": _normalize_price(symbol, float(raw["o"])),
      "h": _normalize_price(symbol, float(raw["h"])),
      "l": _normalize_price(symbol, float(raw["l"])),
      "c": _normalize_price(symbol, float(raw["c"])),
      "v": float(raw.get("v") or 0),
    }
  except (KeyError, TypeError, ValueError):
    return None


async def fetch_ohlc_window(symbol: str, tf: str, start: int, end: int) -> list[dict[str, Any]]:
  key = f"bars:{str(symbol).upper()}:{tf}"
  client = redis_state.get_client()
  raw = await client.zrangebyscore(key, start, end)
  bars: list[dict[str, Any]] = []
  for item in raw:
    parsed = parse_ohlc_payload(item, symbol)
    if parsed is not None:
      bars.append(parsed)
  bars.sort(key=lambda row: row["t"])
  return bars


async def snapshot_manual_algo_chart(
  *,
  signal_id: int,
  event: str,
  ts: int,
  symbol: str = "XAU",
) -> int:
  """Write one row per timeframe. Returns how many timeframes stored."""
  stored = 0
  for tf in TIMEFRAMES:
    start, end = _window(tf, event, ts)
    try:
      bars = await fetch_ohlc_window(symbol, tf, start, end)
    except Exception:
      log.exception(
        "manual_algo_chart fetch failed signal=%s event=%s tf=%s",
        signal_id,
        event,
        tf,
      )
      continue
    if not bars:
      continue
    await store.upsert_manual_algo_chart(
      signal_id=signal_id,
      event=event,
      captured_at=int(ts),
      symbol=str(symbol).upper(),
      timeframe=tf,
      window_start=start,
      window_end=end,
      bars=bars,
    )
    stored += 1
  return stored

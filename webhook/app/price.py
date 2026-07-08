import csv
import io
import logging
import math
from datetime import datetime, timezone

import aiohttp

from app.config import settings

log = logging.getLogger(__name__)

_PRICE_URL = "https://api.tiingo.com/tiingo/fx/top"
_BARS_URL = "https://api.tiingo.com/tiingo/fx/xauusd/prices"


async def get_xau_price(session: aiohttp.ClientSession) -> float | None:
  """Fetch the current XAU/USD price, returning None on any feed failure."""
  try:
    timeout = aiohttp.ClientTimeout(total=10)
    async with session.get(
      _PRICE_URL,
      params={"tickers": "xauusd"},
      headers={"Authorization": f"Token {settings.tiingo_api_key}"},
      timeout=timeout,
    ) as response:
      if response.status == 429:
        log.warning("Tiingo rate limit reached; skipping watcher tick")
        return None
      response.raise_for_status()
      body = await response.json()
      quote = body[0]
      raw = quote.get("midPrice")
      if raw is None:
        bid, ask = quote.get("bidPrice"), quote.get("askPrice")
        raw = (bid + ask) / 2
      price = float(raw)
      if not math.isfinite(price):
        raise ValueError("non-finite price")
      return price
  except Exception as exc:
    # Exception messages may contain the request URL; keep them out of logs.
    log.warning("Could not fetch XAU/USD price (%s)", type(exc).__name__)
    return None


async def get_xau_bars(
  session: aiohttp.ClientSession,
  start_date: str | None = None,
) -> list[dict] | None:
  """Fetch 1-minute XAU/USD OHLC bars, returning None on any feed failure.

  Each bar is ``{"date": str, "open": float, "high": float, "low": float,
  "close": float}`` in chronological order. ``date`` is the raw ISO string from
  Tiingo (UTC) and is used verbatim as the watcher's bar cursor.

  Tiingo ignores the time portion of ``startDate`` and always returns bars from
  00:00 UTC of that day, so the caller filters to bars newer than its cursor.
  ``format=csv`` roughly halves the payload versus JSON.
  """
  if start_date is None:
    start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
  try:
    timeout = aiohttp.ClientTimeout(total=10)
    async with session.get(
      _BARS_URL,
      params={
        "resampleFreq": "1min",
        "startDate": start_date,
        "format": "csv",
      },
      headers={"Authorization": f"Token {settings.tiingo_api_key}"},
      timeout=timeout,
    ) as response:
      if response.status == 429:
        log.warning("Tiingo rate limit reached; skipping watcher tick")
        return None
      response.raise_for_status()
      text = await response.text()
    bars: list[dict] = []
    for row in csv.DictReader(io.StringIO(text)):
      bars.append({
        "date": row["date"],
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
      })
    bars.sort(key=lambda b: b["date"])
    return bars
  except Exception as exc:
    log.warning("Could not fetch XAU/USD bars (%s)", type(exc).__name__)
    return None

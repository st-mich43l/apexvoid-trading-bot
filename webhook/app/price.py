import logging
import math

import aiohttp

from app.config import settings

log = logging.getLogger(__name__)

_PRICE_URL = "https://api.twelvedata.com/price"


async def get_xau_price(session: aiohttp.ClientSession) -> float | None:
  """Fetch the current XAU/USD price, returning None on any feed failure."""
  try:
    timeout = aiohttp.ClientTimeout(total=10)
    async with session.get(
      _PRICE_URL,
      params={"symbol": "XAU/USD", "apikey": settings.twelvedata_api_key},
      timeout=timeout,
    ) as response:
      if response.status == 429:
        log.warning("Twelve Data rate limit reached; skipping watcher tick")
        return None
      response.raise_for_status()
      body = await response.json()
      price = float(body["price"])
      if not math.isfinite(price):
        raise ValueError("non-finite price")
      return price
  except Exception as exc:
    # Exception messages may contain the request URL, including the API key.
    log.warning("Could not fetch XAU/USD price (%s)", type(exc).__name__)
    return None

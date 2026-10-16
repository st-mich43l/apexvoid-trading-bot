"""The Go analysis-engine's published opposing-barrier book, as the worker's target-room check reads it."""

import json
import time

SYMBOLS = ("XAU", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY")


async def seed_fresh_zone_books(client, symbols=SYMBOLS):
  """A fresh book that explicitly lists no barriers for each symbol: Go computed the set and found nothing opposing.

  A test about the book itself overwrites or deletes the key; without a book the worker holds the new plan (that is the point).
  """
  now = int(time.time())
  for symbol in symbols:
    await client.set(f"analysis:zone_book:{symbol}", json.dumps({
      "schema_version": 1, "symbol": symbol, "generated_at": now, "entries": [], "barriers": [],
    }))

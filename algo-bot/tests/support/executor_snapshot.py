"""The executor's per-symbol snapshot, as the same-direction admission reads it."""

import json
import time


async def seed_idle_executor(client, symbol="XAU"):
  """An idle, ready executor: no live group on the symbol."""
  await client.set(f"auto_trade:executor_snapshot:{symbol}", json.dumps({
    "symbol": symbol, "ready": True, "group_ids": [], "updated_at": int(time.time()), "account_equity": 2005.63,
  }))

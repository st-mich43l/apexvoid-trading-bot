"""Range retirement and breakout-retest handoff after a confirmed break."""

from __future__ import annotations

import json
from typing import Any


BREAKOUT_RETEST_KEY_PREFIX = "auto_trade:breakout_retest"
RANGE_RETIRED_KEY_PREFIX = "auto_trade:box:retired"


def breakout_retest_key(symbol: str) -> str:
  return f"{BREAKOUT_RETEST_KEY_PREFIX}:{symbol.upper()}"


async def load_breakout_retest_watch(
  client: Any,
  symbol: str,
) -> dict[str, Any] | None:
  raw = await client.get(breakout_retest_key(symbol))
  if raw is None:
    return None
  try:
    payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
  except (TypeError, ValueError, json.JSONDecodeError):
    return None
  return payload if isinstance(payload, dict) else None



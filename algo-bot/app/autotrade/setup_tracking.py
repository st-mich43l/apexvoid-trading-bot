"""Execution-owned cleanup for legacy setup tracking keys.

The automatic technical producer is Go. This housekeeping helper remains
available to delivery when a position opens, but deliberately has no scanner
dependency so Telegram delivery cannot pull the retired Python detector into
the production import graph.
"""

from __future__ import annotations

from typing import Any


async def clear_active_setup_tracking(
  client: Any,
  symbol: str,
  *,
  tf: str | None = None,
  direction: str | None = None,
) -> None:
  """Drop stale invalidation-watch state after a setup opens."""
  symbol_token = symbol.upper()
  if tf:
    patterns = (
      f"scanner:setup:active_band:{symbol_token}:{tf.upper()}:*",
      f"scanner:setup:active:{symbol_token}:{tf.upper()}:*",
    )
  else:
    patterns = (
      f"scanner:setup:active_band:{symbol_token}:*",
      f"scanner:setup:active:{symbol_token}:*",
    )
  wanted = str(direction or "").upper() or None
  for pattern in patterns:
    async for key in client.scan_iter(match=pattern):
      key_text = key.decode() if isinstance(key, bytes) else str(key)
      if wanted:
        parts = key_text.split(":")
        if ":active_band:" in key_text:
          if len(parts) < 2 or parts[-2] != wanted:
            continue
        elif parts[-1] != wanted:
          continue
      await client.delete(key)

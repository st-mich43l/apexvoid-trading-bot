"""Scalping telemetry — separate keys from live auto_trade streams."""

from __future__ import annotations

import json
from typing import Any


def metric_key(symbol: str, name: str) -> str:
  return f"scalp:metric:{symbol.upper()}:{name}"


async def incr(client: Any, symbol: str, name: str) -> None:
  await client.incr(metric_key(symbol, name))


async def set_last(client: Any, kind: str, symbol: str, payload: dict[str, Any]) -> None:
  key = f"scalp:last_{kind}:{symbol.upper()}"
  await client.set(key, json.dumps(payload, separators=(",", ":"), sort_keys=True))


async def record_cycle(
  client: Any,
  symbol: str,
  payload: dict[str, Any],
) -> None:
  await set_last(client, "cycle", symbol, payload)
  for name in (
    "context_load_ms",
    "analysis_labels_ms",
    "microstructure_ms",
    "strategy_evaluation_ms",
    "persistence_ms",
    "cycle_total_ms",
  ):
    if name in payload:
      await client.hset(
        f"scalp:timing:{symbol.upper()}",
        name,
        str(payload[name]),
      )

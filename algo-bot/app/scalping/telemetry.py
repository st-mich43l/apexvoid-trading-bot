"""Scalping telemetry — separate keys from live auto_trade streams."""

from __future__ import annotations

from typing import Any


def metric_key(symbol: str, name: str) -> str:
  return f"scalp:metric:{symbol.upper()}:{name}"


async def incr(client: Any, symbol: str, name: str) -> None:
  await client.incr(metric_key(symbol, name))



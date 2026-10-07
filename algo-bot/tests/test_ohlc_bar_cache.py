"""Closed-bar OHLC window cache shared by dispatcher handlers."""

from __future__ import annotations

import json

import pytest

from app.marketdata.ohlc import (
  RedisOHLCSource,
)


pytestmark = pytest.mark.no_database


class _CountingRedis:
  def __init__(self, bars: list[tuple[int, float]]):
    self.calls: list[tuple[str, int, int]] = []
    self._bars = bars

  async def zrevrange(self, key, start, end, withscores=True):
    self.calls.append((key, int(start), int(end)))
    newest_first = list(reversed(self._bars))
    sliced = newest_first[start:end + 1]
    rows = []
    for ts, close in sliced:
      payload = json.dumps({
        "t": ts,
        "o": close,
        "h": close,
        "l": close,
        "c": close,
        "v": 1,
      })
      rows.append((payload, ts))
    return rows


@pytest.mark.asyncio
async def test_closed_bar_cache_reuses_larger_window():
  client = _CountingRedis([(1, 1.0), (2, 2.0), (3, 3.0)])
  source = RedisOHLCSource(client)
  source.begin_closed_bar_cache()

  first = await source.window("XAU", "M1", 3)
  second = await source.window("xau", "m1", 2)

  assert list(first["close"]) == [1.0, 2.0, 3.0]
  assert list(second["close"]) == [2.0, 3.0]
  assert len(client.calls) == 1

  source.end_closed_bar_cache()
  await source.window("XAU", "M1", 2)
  assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_closed_bar_cache_refetches_when_need_grows():
  client = _CountingRedis([(1, 1.0), (2, 2.0), (3, 3.0)])
  source = RedisOHLCSource(client)
  source.begin_closed_bar_cache()

  await source.window("XAU", "M5", 1)
  await source.window("XAU", "M5", 3)

  assert len(client.calls) == 2
  assert client.calls[0][2] == 0
  assert client.calls[1][2] == 2



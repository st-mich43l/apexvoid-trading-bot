import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app import broadcast, dedup, redis_state, scanner
from app.ohlc_source import RedisOHLCSource
from app.structure import Zone


def _frame() -> pd.DataFrame:
  index = pd.date_range("2026-07-10", periods=1, freq="5min", tz="UTC")
  return pd.DataFrame({
    "open": [4100.0],
    "high": [4101.0],
    "low": [4099.0],
    "close": [4100.5],
    "volume": [100.0],
  }, index=index)


@pytest.mark.asyncio
async def test_redis_ohlc_source_returns_oldest_to_newest_window():
  client = redis_state.get_client()
  for ts, close in ((1, 4100), (3, 4102), (2, 4101)):
    await client.zadd(
      "bars:XAU:M5",
      {
        json.dumps({
          "t": ts,
          "o": close - 0.5,
          "h": close + 1,
          "l": close - 1,
          "c": close,
          "v": 100,
        }): ts
      },
    )

  df = await RedisOHLCSource(client).window("xau", "m5", 2)

  assert list(df["close"]) == [4101.0, 4102.0]
  assert str(df.index.tz) == "UTC"
  assert df.index.name == "time"


@pytest.mark.asyncio
async def test_scanner_dedups_same_setup_level_and_only_dms_owner(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  broadcast_entry = AsyncMock()
  store_manual_signal = AsyncMock()
  monkeypatch.setattr(broadcast, "broadcast_entry", broadcast_entry)
  monkeypatch.setattr(dedup, "store_manual_signal", store_manual_signal)
  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "M30,M15")
  monkeypatch.setattr(scanner.settings, "scanner_window", 500)
  monkeypatch.setattr(scanner.settings, "scanner_alert_ttl", 7200)
  monkeypatch.setattr(scanner.settings, "scanner_level_bucket", 20)
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", 4242)

  class Source:
    async def window(self, symbol, tf, n):
      assert symbol == "XAU"
      assert tf in {"M5", "M30", "M15"}
      assert n == 500
      return _frame()

  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order: ctx,
  )
  result = scanner.DetectionResult(
    setup="Trend Pullback",
    direction="BUY",
    key_level=4100.0,
    entry_zone=Zone(4098, 4102, "demand"),
    confluence=3,
    reasons=["HTF bias up", "WAE reset"],
  )

  def detector(received_ctx):
    assert received_ctx is ctx
    return result

  first = await scanner._handle_event(
    "XAU:M5:1",
    source=Source(),
    client=client,
    detectors=(detector,),
    notify=notify,
  )
  second = await scanner._handle_event(
    "XAU:M5:2",
    source=Source(),
    client=client,
    detectors=(detector,),
    notify=notify,
  )

  assert first == [result]
  assert second == []
  notify.assert_awaited_once()
  text = notify.await_args.args[0]
  assert "Setup forming" in text
  assert "Trend Pullback" in text
  assert "+90 pips" not in text
  assert notify.await_args.kwargs == {"chat_id": 4242}
  assert await client.get(
    "scanner:alerted:XAU:M5:Trend Pullback:4100"
  ) == "1"
  assert await client.ttl(
    "scanner:alerted:XAU:M5:Trend Pullback:4100"
  ) > 0
  broadcast_entry.assert_not_awaited()
  store_manual_signal.assert_not_awaited()

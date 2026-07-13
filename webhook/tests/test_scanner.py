import json
from datetime import datetime, timezone
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
async def test_redis_ohlc_source_normalizes_legacy_ctrader_xau_scale():
  client = redis_state.get_client()
  await client.zadd(
    "bars:XAU:M5",
    {
      json.dumps({
        "t": 1,
        "o": 4104130,
        "h": 4107960,
        "l": 4103000,
        "c": 4105500,
        "v": 100,
      }): 1
    },
  )

  df = await RedisOHLCSource(client).window("xau", "m5", 1)

  assert df.iloc[0]["open"] == pytest.approx(4104.13)
  assert df.iloc[0]["high"] == pytest.approx(4107.96)
  assert df.iloc[0]["low"] == pytest.approx(4103.0)
  assert df.iloc[0]["close"] == pytest.approx(4105.5)


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
    current_price=4103.0,
    confluence=3,
    reasons=["HTF bias up", "rejection at support"],
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
  assert "Price <b>4,103</b>" in text
  assert "entry <b>4,098-4,102</b>" in text
  assert "key <b>4,100</b>" in text
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


@pytest.mark.asyncio
async def test_scanner_records_analysis_status_without_owner(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "M30,M15")
  monkeypatch.setattr(scanner.settings, "scanner_window", 500)
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", None)

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
    current_price=4103.0,
    confluence=3,
    reasons=["HTF bias up", "rejection at support"],
  )

  sent = await scanner._handle_event(
    "XAU:M5:123",
    source=Source(),
    client=client,
    detectors=(lambda received_ctx: result,),
    notify=notify,
  )

  assert sent == []
  notify.assert_not_awaited()
  status = json.loads(await client.get("scanner:last_tick:XAU:M5"))
  assert status["status"] == "ok"
  assert status["symbol"] == "XAU"
  assert status["tf"] == "M5"
  assert status["event_ts"] == "123"
  assert status["frames"] == {"M15": 1, "M30": 1, "M5": 1}
  assert status["detected"][0]["setup"] == "Trend Pullback"
  assert status["detected"][0]["mode"] == "with_trend"
  assert status["detected"][0]["current_price"] == 4103.0
  assert status["detected"][0]["entry_zone"] == {
    "low": 4098,
    "high": 4102,
    "score": 0.0,
    "score_reasons": [],
  }
  assert status["sent"] == 0


@pytest.mark.asyncio
async def test_scanner_digest_suppresses_overlap_and_only_claims_sent(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "M30,M15")
  monkeypatch.setattr(scanner.settings, "scanner_window", 500)
  monkeypatch.setattr(scanner.settings, "scanner_alert_ttl", 7200)
  monkeypatch.setattr(scanner.settings, "scanner_level_bucket", 20)
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", 4242)
  monkeypatch.setattr(scanner.settings, "scanner_top_n", 2)
  monkeypatch.setattr(scanner.settings, "alert_overlap_suppress", 0.5)

  class Source:
    async def window(self, symbol, tf, n):
      return _frame()

  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="down",
    structures={"M30": SimpleNamespace(bias="down")},
    frames={"M5": _frame()},
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order: ctx,
  )
  results = [
    scanner.DetectionResult(
      "Snap-Back",
      "SELL",
      4094.0,
      Zone(4094, 4096, "supply", score=13),
      4090.0,
      3,
      ["HTF bias down"],
    ),
    scanner.DetectionResult(
      "Fade Scalp",
      "SELL",
      4095.0,
      Zone(4095, 4097, "supply", score=11),
      4090.0,
      2,
      ["HTF bias down"],
    ),
    scanner.DetectionResult(
      "Break & Retest",
      "SELL",
      4105.0,
      Zone(4105, 4106, "supply", score=9),
      4090.0,
      2,
      ["HTF bias down"],
    ),
    scanner.DetectionResult(
      "Trend Pullback",
      "SELL",
      4110.0,
      Zone(4110, 4111, "supply", score=8),
      4090.0,
      1,
      ["HTF bias down"],
    ),
  ]

  def make_detector(result):
    return lambda received_ctx: result

  sent = await scanner._handle_event(
    "XAU:M5:1",
    source=Source(),
    client=client,
    detectors=tuple(make_detector(result) for result in results),
    notify=notify,
  )

  assert [item.setup for item in sent] == ["Snap-Back", "Break & Retest"]
  text = notify.await_args.args[0]
  assert text.count("Setup forming") == 1
  assert "Snap-Back" in text
  assert "also: Break&amp;Retest" in text
  assert await client.get(scanner._dedup_key("XAU", "M5", results[0])) == "1"
  assert await client.get(scanner._dedup_key("XAU", "M5", results[2])) == "1"
  assert await client.get(scanner._dedup_key("XAU", "M5", results[1])) is None


@pytest.mark.asyncio
async def test_scanner_uses_fresh_spot_for_context_and_live_render(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  now = int(datetime.now(timezone.utc).timestamp())
  await client.set(
    "price:XAU:spot",
    json.dumps({"bid": 4082.0, "ask": 4082.2, "ts": now}),
  )
  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "M30,M15")
  monkeypatch.setattr(scanner.settings, "scanner_window", 500)
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", 4242)
  monkeypatch.setattr(scanner.settings, "spot_fresh_secs", 30)

  class Source:
    async def window(self, symbol, tf, n):
      return _frame()

  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
    frames={"M5": _frame()},
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order: ctx,
  )

  def detector(received_ctx):
    assert received_ctx.spot_price == pytest.approx(4082.1)
    return scanner.DetectionResult(
      "Trend Pullback",
      "BUY",
      4080.0,
      Zone(4078, 4080, "demand"),
      received_ctx.spot_price,
      2,
      ["HTF bias up"],
    )

  await scanner._handle_event(
    "XAU:M5:1",
    source=Source(),
    client=client,
    detectors=(detector,),
    notify=notify,
  )

  text = notify.await_args.args[0]
  assert "Price now <b>4,082.1</b> (live)" in text

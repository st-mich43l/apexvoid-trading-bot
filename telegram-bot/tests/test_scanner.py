import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from app.analysis.engine import Regime
from app.signals import broadcast
from app.persistence import store, redis_state
from app.analysis import scanner
from app.analysis.market_map import MapEntry, MarketMap, ScalpRail
from app.analysis.ohlc_source import RedisOHLCSource
from app.signals.parsing import _parse_manual
from app.analysis.scalp_ranges import ScalpBarrier, ScalpRange
from app.analysis.structure import Zone
from app.analysis.zones import ZONE_RECONCILED_TAG_PREFIX


def _frame() -> pd.DataFrame:
  index = pd.date_range("2026-07-10", periods=1, freq="5min", tz="UTC")
  return pd.DataFrame({
    "open": [4100.0],
    "high": [4101.0],
    "low": [4099.0],
    "close": [4100.5],
    "volume": [100.0],
  }, index=index)


class StaticSource:
  async def window(self, symbol, tf, n):
    assert symbol == "XAU"
    assert tf in {"M5", "M30", "M15"}
    return _frame()


def test_scanner_copy_draft_becomes_valid_manual_signal_after_filling_risk():
  result = scanner.DetectionResult(
    "Fade Scalp",
    "SELL",
    4105.0,
    Zone(4104.13, 4107.96, "supply"),
    4105.38,
    3,
    ["HTF bias down"],
  )

  draft = scanner._copy_draft("XAU", result)
  assert draft is not None
  ready = draft.replace("SL", "4112").replace(
    "TP1/TP2/TP3",
    "4100/4095/4090",
  )

  parsed = _parse_manual(ready)
  assert parsed is not None
  assert parsed["action"] == "SELL"
  assert parsed["entry"] == pytest.approx(4104.13)
  assert parsed["entry_end"] == pytest.approx(4107.96)
  assert parsed["sl"] == pytest.approx(4112)
  assert parsed["tps"] == [4100, 4095, 4090]
  assert parsed["setup_type"] == "fade-scalp"
  assert parsed["confluence"] == 3


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
  monkeypatch.setattr(store, "store_manual_signal", store_manual_signal)
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
  assert "XAU M5 · SETUP FORMING" in text
  assert "BUY · Trend Pullback" in text
  assert "Trigger close:</b> <b>4,103</b>" in text
  assert "Entry zone:</b> <b>4,098–4,102</b>" in text
  assert "Key level:</b> <b>4,100</b>" in text
  assert "HTF bias:</b> up (M30)" in text
  assert "rejection at support" in text
  assert (
    "<code>gold buy entry zone (4098-4102) / sl SL / "
    "tp TP1/TP2/TP3 / setup trend-pullback ***</code>"
  ) in text
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
async def test_scanner_uses_dedicated_default_notifier(monkeypatch):
  client = redis_state.get_client()
  dedicated_notify = AsyncMock()
  monkeypatch.setattr(scanner, "send_scanner_with_retry", dedicated_notify)
  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "M30,M15")
  monkeypatch.setattr(scanner.settings, "scanner_window", 500)
  monkeypatch.setattr(scanner.settings, "scanner_alert_ttl", 7200)
  monkeypatch.setattr(scanner.settings, "scanner_level_bucket", 20)
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", 4242)

  class Source:
    async def window(self, symbol, tf, n):
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
    key_level=4111.0,
    entry_zone=Zone(4108, 4110, "demand"),
    current_price=4112.0,
    confluence=2,
    reasons=["HTF bias up", "fresh"],
  )

  sent = await scanner._handle_event(
    "XAU:M5:dedicated",
    source=Source(),
    client=client,
    detectors=(lambda received_ctx: result,),
  )

  assert sent == [result]
  dedicated_notify.assert_awaited_once()
  assert dedicated_notify.await_args.kwargs == {"chat_id": 4242}


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
  assert status["map"] == {"buys": 0, "sells": 0, "majors": 0}


@pytest.mark.asyncio
async def test_gate_status_records_market_map_counts():
  client = redis_state.get_client()
  market_map = MarketMap(
    [
      MapEntry("buy", 4025, 4028, 4025, 4028, "major", ["demand"], 13),
      MapEntry("buy", 4035, 4038, 4035, 4038, "zone", ["OB"], 9),
      MapEntry("sell", 4063, 4066, 4063, 4066, "zone", ["supply"], 8),
    ],
    4041,
    4047,
    4032,
    4062,
    "down",
    "M30",
  )

  await scanner._record_status(
    client,
    symbol="XAU",
    tf="M5",
    event_ts="map-counts",
    frames={"M5": _frame()},
    detected=[],
    sent=[],
    status="ok",
    market_map=market_map,
  )

  payload = json.loads(await client.get("scanner:last_tick:XAU:M5"))
  assert payload["map"] == {"buys": 2, "sells": 1, "majors": 1}
  assert payload["map_summary"] == "map: buys=2 sells=1 majors=1"


@pytest.mark.asyncio
async def test_scanner_caches_analysis_context_for_market_map(monkeypatch):
  client = redis_state.get_client()
  marker = object()
  cached = Mock()
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "M30,M15")
  monkeypatch.setattr(scanner.settings, "scanner_window", 500)
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order: SimpleNamespace(
      analysis=marker,
      spot_price=None,
      spot_ts=None,
      trigger_ts=None,
    ),
  )
  monkeypatch.setattr(scanner, "cache_analysis", cached)

  ctx, frames = await scanner._load_market_context_for_symbol(
    "XAU",
    source=StaticSource(),
    client=client,
    event_ts="cache-test",
  )

  assert ctx is not None
  assert set(frames) == {"M5", "M30", "M15"}
  cached.assert_called_once_with("XAU", marker, 4100.5, frames["M5"].index[-1])


@pytest.mark.asyncio
async def test_scanner_increments_zone_reconciled_counter(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "M30,M15")
  monkeypatch.setattr(scanner.settings, "scanner_window", 500)
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", None)

  class Source:
    async def window(self, symbol, tf, n):
      return _frame()

  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
    analysis=SimpleNamespace(
      per_tf={
        "M5": SimpleNamespace(
          zone_reconcile_dropped=0, zone_reconcile_aborted=False,
        ),
      },
    ),
    spot_price=None,
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order: ctx,
  )
  reconciled_map = MarketMap(
    [
      MapEntry(
        "buy",
        4112,
        4116,
        4112,
        4116,
        "zone",
        ["demand", f"{ZONE_RECONCILED_TAG_PREFIX}supply 4116.00-4127.00"],
        5,
      ),
    ],
    4113,
    None,
    None,
    None,
    "down",
    "M30",
  )
  monkeypatch.setattr(
    scanner, "build_map", lambda analysis, price, settings: reconciled_map,
  )

  await scanner._handle_event(
    "XAU:M5:reconciled",
    source=Source(),
    client=client,
    detectors=(lambda received_ctx: None,),
  )

  assert await client.get("auto_trade:zone_reconciled:XAU") == "1"


@pytest.mark.asyncio
async def test_scanner_increments_zone_dropped_and_aborted_counters(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "M30,M15")
  monkeypatch.setattr(scanner.settings, "scanner_window", 500)
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", None)

  class Source:
    async def window(self, symbol, tf, n):
      return _frame()

  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
    analysis=SimpleNamespace(
      per_tf={
        "M5": SimpleNamespace(
          zone_reconcile_dropped=3, zone_reconcile_aborted=True,
        ),
      },
    ),
    spot_price=None,
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order: ctx,
  )
  empty_map = MarketMap([], 4113, None, None, None, "down", "M30")
  monkeypatch.setattr(scanner, "build_map", lambda analysis, price, settings: empty_map)

  await scanner._handle_event(
    "XAU:M5:reconciled",
    source=Source(),
    client=client,
    detectors=(lambda received_ctx: None,),
  )

  assert await client.get("auto_trade:zone_dropped:XAU") == "3"
  assert await client.get("auto_trade:zone_reconcile_aborted:XAU") == "1"


def test_scanner_alert_references_containing_market_map_entry():
  result = scanner.DetectionResult(
    "Break & Retest",
    "SELL",
    4063,
    Zone(4063.5, 4065.5, "supply"),
    4060,
    2,
    ["HTF bias down"],
  )
  market_map = MarketMap(
    [
      MapEntry(
        "sell",
        4063,
        4066,
        4063,
        4066,
        "zone",
        ["supply", "flip"],
        10,
      ),
    ],
    4041,
    4047,
    4032,
    4062,
    "down",
    "M30",
    [
      ScalpRail(
        4064,
        4063,
        4065,
        4064,
        "SELL",
        ["micro ×3", "box-top"],
        5,
      ),
    ],
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="down",
    structures={"M30": SimpleNamespace(bias="down")},
    frames={"M5": _frame()},
    regime=None,
    spot_price=4060,
    trigger_ts="2026-07-16T08:45:00Z",
  )

  text = scanner._format_detection(
    "XAU",
    "M5",
    ctx,
    result,
    ["M30"],
    market_map=market_map,
  )

  assert "map: SELL 4,063–4,066 (flip·supply)" in text
  assert "rail: 🔴 SELL 4,064 micro ×3·box-top" in text


def test_range_scalp_alert_is_two_sided_and_keeps_target_reasons():
  result = scanner.DetectionResult(
    "Range Edge Scalp",
    "SELL",
    4110,
    Zone(4109.7, 4110.3, "supply", source="range_edge"),
    4109.5,
    3,
    [
      "local range 4100-4110",
      "upper barrier ×4",
      "wick rejection ×3",
      "sweep + reclaim",
      "TP1 EQ 4105",
      "TP2 edge 4100",
    ],
    mode="range_scalp",
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="range",
    structures={"M30": SimpleNamespace(bias="range")},
    frames={"M5": _frame()},
    regime=None,
    spot_price=4109.5,
    trigger_ts="2026-07-17T04:00:00Z",
  )

  text = scanner._format_detection("XAU", "M5", ctx, result, ["M30"])

  assert "RANGE SCALP" in text
  assert "COUNTER-TREND" not in text
  assert "TP1 EQ 4105" in text
  assert "TP2 edge 4100" in text


def test_scalp_status_reports_active_range_and_touched_edge():
  lower = ScalpBarrier(
    "support", 4100, 4099.7, 4100.3, 3, 3, 0, 8, ["micro ×3"], 8,
  )
  upper = ScalpBarrier(
    "resistance", 4110, 4109.7, 4110.3, 4, 3, 0, 9, ["micro ×4"], 9,
  )
  frame = _frame()
  frame.loc[frame.index[-1], ["high", "low", "close"]] = [4110.1, 4108.5, 4109.5]
  ctx = SimpleNamespace(
    tf="M5",
    settings=SimpleNamespace(range_scalp_enabled=True),
    structures={
      "M5": SimpleNamespace(
        scalp_barriers=[lower, upper],
        scalp_range=ScalpRange(lower, upper, 4105, 5, 17),
      ),
    },
    frames={"M5": frame},
  )

  status = scanner._scalp_status(ctx)

  assert status["state"] == "edge_touch"
  assert status["supports"] == 1
  assert status["resistances"] == 1
  assert status["range"]["touched"] == ["upper"]

  ctx.settings.range_scalp_enabled = False
  assert scanner._scalp_status(ctx)["state"] == "disabled"


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
  assert text.count("SETUP FORMING") == 1
  assert "Snap-Back" in text
  assert "Also:</b> Break&amp;Retest" in text
  assert await client.get(scanner._dedup_key("XAU", "M5", results[0])) == "1"
  assert await client.get(scanner._dedup_key("XAU", "M5", results[2])) == "1"
  assert await client.get(scanner._dedup_key("XAU", "M5", results[1])) is None


@pytest.mark.asyncio
async def test_scanner_zone_band_dedup_suppresses_cross_setup_ideas(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "M30,M15")
  monkeypatch.setattr(scanner.settings, "scanner_window", 500)
  monkeypatch.setattr(scanner.settings, "scanner_alert_ttl", 7200)
  monkeypatch.setattr(scanner.settings, "scanner_level_bucket", 20)
  monkeypatch.setattr(scanner.settings, "zone_alert_ttl", 14400)
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", 4242)
  monkeypatch.setattr(scanner.settings, "scanner_top_n", 1)

  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
    frames={"M5": _frame()},
    regime=Regime("chop", 4110, 4097, 3.0, ["fixture chop"]),
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order: ctx,
  )
  result_a = scanner.DetectionResult(
    "Fade Scalp",
    "BUY",
    4100.0,
    Zone(4099, 4101, "demand"),
    4103.0,
    3,
    ["HTF bias up", "range 4097-4110"],
  )
  result_b = scanner.DetectionResult(
    "Break & Retest",
    "BUY",
    4101.0,
    Zone(4099.4, 4100.6, "demand"),
    4103.0,
    2,
    ["HTF bias up"],
  )
  result_far = scanner.DetectionResult(
    "Zone Reaction",
    "BUY",
    4106.0,
    Zone(4105, 4107, "demand"),
    4108.0,
    2,
    ["HTF bias up"],
  )
  current = {"result": result_a}

  def detector(received_ctx):
    assert received_ctx is ctx
    return current["result"]

  first = await scanner._handle_event(
    "XAU:M5:1",
    source=StaticSource(),
    client=client,
    detectors=(detector,),
    notify=notify,
  )
  current["result"] = result_b
  same_band = await scanner._handle_event(
    "XAU:M5:2",
    source=StaticSource(),
    client=client,
    detectors=(detector,),
    notify=notify,
  )
  current["result"] = result_far
  far_band = await scanner._handle_event(
    "XAU:M5:3",
    source=StaticSource(),
    client=client,
    detectors=(detector,),
    notify=notify,
  )

  assert first == [result_a]
  assert same_band == []
  assert far_band == [result_far]
  assert notify.await_count == 2
  first_text = notify.await_args_list[0].args[0]
  assert "range-bound 4,097-4,110 (M5)" in first_text
  assert await client.get(scanner._band_dedup_key("XAU", result_a)) == "1"
  assert await client.get(scanner._dedup_key("XAU", "M5", result_b)) is None

  await client.delete(scanner._band_dedup_key("XAU", result_a))
  # This fixture's frame never advances, so result_far's zone would read as
  # "invalidated" (B3) on the very next scan against the same static close -
  # clear its tracking state, matching the band-dedup reset above, since this
  # test isn't exercising invalidation.
  await client.delete(
    scanner._active_setup_key("XAU", "M5", result_far.setup, result_far.direction)
  )
  current["result"] = result_b
  after_ttl = await scanner._handle_event(
    "XAU:M5:4",
    source=StaticSource(),
    client=client,
    detectors=(detector,),
    notify=notify,
  )

  assert after_ttl == [result_b]
  assert notify.await_count == 3


@pytest.mark.asyncio
async def test_box_breakout_second_alert_on_same_edge_is_band_deduped(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  monkeypatch.setattr(scanner.settings, "scanner_level_bucket", 20)
  monkeypatch.setattr(scanner.settings, "scanner_alert_ttl", 7200)
  monkeypatch.setattr(scanner.settings, "zone_alert_ttl", 14400)
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", 4242)
  result = scanner.DetectionResult(
    "Box Breakout",
    "BUY",
    4097.0,
    Zone(4109.5, 4110.5, "demand", source="box_breakout", score=9.5),
    4110.8,
    2,
    [
      "HTF bias up",
      "box 4097-4110",
      "accepted (2 closes)",
      "retest 4110",
      "measured +13.0",
      "coil",
    ],
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
    frames={"M5": _frame()},
    regime=Regime("chop", 4110, 4097, 3.0, ["fixture chop"], True),
    spot_price=None,
    trigger_ts="2026-07-10T00:00:00Z",
  )

  first = await scanner._notify_digest_once(
    client,
    "XAU",
    "M5",
    ctx,
    [result],
    notify,
    ["M30"],
  )
  await client.delete(scanner._dedup_key("XAU", "M5", result))
  second = await scanner._notify_digest_once(
    client,
    "XAU",
    "M5",
    ctx,
    [result],
    notify,
    ["M30"],
  )

  assert first == [result]
  assert second == []
  assert notify.await_count == 1
  text = notify.await_args.args[0]
  assert "box 4097-4110" in text
  assert "accepted (2 closes)" in text
  assert "measured +13.0" in text
  assert "coil" in text


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
  monkeypatch.setattr(scanner.settings, "spot_max_deviation_pct", 2.0)

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
  assert "Price now:</b> <b>4,082.1</b> <i>(live)</i>" in text


@pytest.mark.asyncio
async def test_scanner_rejects_implausible_spot_and_still_fires(monkeypatch, caplog):
  client = redis_state.get_client()
  notify = AsyncMock()
  now = int(datetime.now(timezone.utc).timestamp())
  await client.set(
    "price:XAU:spot",
    json.dumps({"bid": 4100500.0, "ask": 4100500.0, "ts": now}),
  )
  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "M30,M15")
  monkeypatch.setattr(scanner.settings, "scanner_window", 500)
  monkeypatch.setattr(scanner.settings, "scanner_alert_ttl", 7200)
  monkeypatch.setattr(scanner.settings, "scanner_level_bucket", 20)
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", 4242)
  monkeypatch.setattr(scanner.settings, "spot_fresh_secs", 30)
  monkeypatch.setattr(scanner.settings, "spot_max_deviation_pct", 2.0)

  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order: SimpleNamespace(
      tf=tf,
      htf_bias="up",
      structures={"M30": SimpleNamespace(bias="up")},
      frames=frames,
    ),
  )

  def detector(received_ctx):
    assert received_ctx.spot_price is None
    assert received_ctx.spot_ts is None
    close = float(received_ctx.frames["M5"]["close"].iloc[-1])
    assert close == pytest.approx(4100.5)
    return scanner.DetectionResult(
      "Trend Pullback",
      "BUY",
      4100.0,
      Zone(4099, 4101, "demand"),
      close,
      2,
      ["HTF bias up"],
    )

  caplog.set_level(logging.WARNING, logger="app.scanner")
  sent = await scanner._handle_event(
    "XAU:M5:1",
    source=StaticSource(),
    client=client,
    detectors=(detector,),
    notify=notify,
  )

  assert len(sent) == 1
  assert "implausible vs close" in caplog.text
  text = notify.await_args.args[0]
  assert "Trigger close:</b> <b>4,100.5</b> <i>(M5 · 00:05 UTC)</i>" in text
  assert "(live)" not in text


@pytest.mark.parametrize("price", [float("nan"), 0.0, -4100.0])
@pytest.mark.asyncio
async def test_scanner_rejects_bad_spot_values_without_crashing(
  monkeypatch,
  caplog,
  price,
):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  await client.set(
    "price:XAU:spot",
    json.dumps({"bid": price, "ask": price, "ts": now}),
  )
  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "M30,M15")
  monkeypatch.setattr(scanner.settings, "scanner_window", 500)
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", 4242)
  monkeypatch.setattr(scanner.settings, "spot_fresh_secs", 30)
  monkeypatch.setattr(scanner.settings, "spot_max_deviation_pct", 2.0)
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order: SimpleNamespace(
      tf=tf,
      htf_bias="up",
      structures={"M30": SimpleNamespace(bias="up")},
      frames=frames,
    ),
  )

  def detector(received_ctx):
    assert received_ctx.spot_price is None
    assert received_ctx.spot_ts is None
    return None

  caplog.set_level(logging.WARNING, logger="app.scanner")
  sent = await scanner._handle_event(
    "XAU:M5:1",
    source=StaticSource(),
    client=client,
    detectors=(detector,),
    notify=AsyncMock(),
  )

  assert sent == []
  assert "implausible vs close" in caplog.text


@pytest.mark.asyncio
async def test_scanner_missing_spot_keeps_fallback_without_warning(monkeypatch, caplog):
  client = redis_state.get_client()
  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "M30,M15")
  monkeypatch.setattr(scanner.settings, "scanner_window", 500)
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", 4242)
  monkeypatch.setattr(scanner.settings, "spot_fresh_secs", 30)
  monkeypatch.setattr(scanner.settings, "spot_max_deviation_pct", 2.0)
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order: SimpleNamespace(
      tf=tf,
      htf_bias="up",
      structures={"M30": SimpleNamespace(bias="up")},
      frames=frames,
    ),
  )

  def detector(received_ctx):
    assert received_ctx.spot_price is None
    assert received_ctx.spot_ts is None
    return None

  caplog.set_level(logging.WARNING, logger="app.scanner")
  sent = await scanner._handle_event(
    "XAU:M5:1",
    source=StaticSource(),
    client=client,
    detectors=(detector,),
    notify=AsyncMock(),
  )

  assert sent == []
  assert "implausible vs close" not in caplog.text


# --- B1: opposite-direction conflicts ---------------------------------------

def test_opposite_direction_conflict_with_decisive_margin_keeps_stronger(
  monkeypatch,
):
  monkeypatch.setattr(scanner.settings, "scanner_conflict_overlap", 0.5)
  monkeypatch.setattr(scanner.settings, "scanner_conflict_margin", 1)
  # Numbers lifted straight from the 22 Jul 2026 incident: overlap ratio 1.0.
  strong = scanner.DetectionResult(
    "Box Breakout", "BUY", 4121.5,
    Zone(4121.22, 4126.14, "demand"), 4123.0, 3, ["HTF bias up"],
  )
  weak = scanner.DetectionResult(
    "Range Edge Scalp", "SELL", 4123.5,
    Zone(4122.24, 4124.73, "supply"), 4123.0, 2, ["HTF bias down"],
  )

  selected, conflicts = scanner._suppress_overlaps([strong, weak])

  assert selected == [strong]
  assert len(conflicts) == 1
  assert conflicts[0]["outcome"] == "stronger_kept"
  assert conflicts[0]["a"]["setup"] == "Box Breakout"
  assert conflicts[0]["b"]["setup"] == "Range Edge Scalp"


def test_opposite_direction_conflict_with_equal_confluence_drops_both(monkeypatch):
  monkeypatch.setattr(scanner.settings, "scanner_conflict_overlap", 0.5)
  monkeypatch.setattr(scanner.settings, "scanner_conflict_margin", 1)
  a = scanner.DetectionResult(
    "Box Breakout", "BUY", 4121.5,
    Zone(4121.22, 4126.14, "demand"), 4123.0, 2, ["HTF bias up"],
  )
  b = scanner.DetectionResult(
    "Range Edge Scalp", "SELL", 4123.5,
    Zone(4122.24, 4124.73, "supply"), 4123.0, 2, ["HTF bias down"],
  )

  selected, conflicts = scanner._suppress_overlaps([a, b])

  assert selected == []
  assert len(conflicts) == 1
  assert conflicts[0]["outcome"] == "both_dropped"


def test_same_direction_overlap_behaviour_is_unchanged(monkeypatch):
  """Regression guard: B1 only changes opposite-direction handling."""
  monkeypatch.setattr(scanner.settings, "alert_overlap_suppress", 0.5)
  monkeypatch.setattr(scanner.settings, "scanner_conflict_overlap", 0.5)
  monkeypatch.setattr(scanner.settings, "scanner_conflict_margin", 1)
  strong = scanner.DetectionResult(
    "Snap-Back", "SELL", 4094.0,
    Zone(4094, 4096, "supply", score=13), 4090.0, 3, ["HTF bias down"],
  )
  weak = scanner.DetectionResult(
    "Fade Scalp", "SELL", 4095.0,
    Zone(4095, 4097, "supply", score=11), 4090.0, 2, ["HTF bias down"],
  )

  selected, conflicts = scanner._suppress_overlaps([strong, weak])

  assert selected == [strong]
  assert conflicts == []


# --- B3: setup invalidation --------------------------------------------------

@pytest.mark.asyncio
async def test_setup_invalidation_fires_when_zone_is_violated(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", 4242)
  key = scanner._active_setup_key("XAU", "M5", "Range Edge Scalp", "SELL")
  await client.set(key, json.dumps({
    "setup": "Range Edge Scalp",
    "direction": "SELL",
    "zone_low": 4122.24,
    "zone_high": 4124.73,
    "confluence": 2,
  }))
  df = pd.DataFrame(
    {"close": [4125.5]},
    index=pd.date_range("2026-07-22", periods=1, freq="5min", tz="UTC"),
  )

  await scanner._check_setup_invalidations(client, "XAU", "M5", df, notify, 0.0)

  notify.assert_awaited_once()
  text = notify.await_args.args[0]
  assert "SETUP INVALIDATED" in text
  assert "Range Edge Scalp" in text
  assert await client.get(key) is None


@pytest.mark.asyncio
async def test_setup_invalidation_does_not_fire_while_zone_holds():
  client = redis_state.get_client()
  notify = AsyncMock()
  key = scanner._active_setup_key("XAU", "M5", "Range Edge Scalp", "SELL")
  await client.set(key, json.dumps({
    "setup": "Range Edge Scalp",
    "direction": "SELL",
    "zone_low": 4122.24,
    "zone_high": 4124.73,
    "confluence": 2,
  }))
  df = pd.DataFrame(
    {"close": [4123.0]},
    index=pd.date_range("2026-07-22", periods=1, freq="5min", tz="UTC"),
  )

  await scanner._check_setup_invalidations(client, "XAU", "M5", df, notify, 0.0)

  notify.assert_not_awaited()
  assert await client.get(key) is not None


# --- B5: per-detector reporting ---------------------------------------------

@pytest.mark.asyncio
async def test_scan_report_aggregates_fires_sent_and_conflicts():
  client = redis_state.get_client()
  detected = [
    scanner.DetectionResult(
      "Fade Scalp", "SELL", 100.0, Zone(99, 101, "supply"), 100.5, 2, ["r"],
    ),
    scanner.DetectionResult(
      "Box Breakout", "BUY", 100.0, Zone(99, 101, "demand"), 100.5, 3, ["r"],
    ),
  ]
  sent = [detected[1]]
  conflicts = [{
    "outcome": "both_dropped",
    "a": {"setup": "Fade Scalp", "direction": "SELL", "confluence": 2},
    "b": {"setup": "Box Breakout", "direction": "BUY", "confluence": 3},
  }]

  await scanner._append_detect_log(client, "XAU", "M5", detected, sent, conflicts)
  rows = await scanner.scan_report(client, "XAU", "M5", hours=24)

  assert rows["Box Breakout"]["fires"] == 1
  assert rows["Box Breakout"]["sent"] == 1
  assert rows["Fade Scalp"]["fires"] == 1
  assert rows["Fade Scalp"]["dropped_conflict"] == 1
  text = scanner.format_scan_report(rows, "XAU", "M5", 24)
  assert "Box Breakout" in text
  assert "Fade Scalp" in text

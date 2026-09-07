import json
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf
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
    assert tf in {"M5", "M30", "M15", "H1"}
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
  # R ladder applies regardless of /algo - the filled-in TP1/TP2/TP3 draft
  # text is ignored just like any other owner-typed tp.
  risk = 4112 - 4104.13
  assert parsed["tps"] == [
    pytest.approx(4104.13 - r * risk) for r in (0.5, 1.0, 2.0, 3.0)
  ]
  assert parsed["setup_type"] == "fade-scalp"
  assert parsed["confluence"] == 3


def test_scanner_copy_draft_includes_planned_stop_price():
  from app.analysis.execution_eligibility import (
    EXECUTION_ELIGIBILITY_VERSION,
    STATIC_ELIGIBLE,
    ExecutionEligibility,
  )

  result = scanner.DetectionResult(
    "Key Level Reaction",
    "BUY",
    4075.0,
    Zone(4072.99, 4076.89, "demand"),
    4074.94,
    2,
    ["HTF bias up"],
    execution_eligibility=ExecutionEligibility(
      version=EXECUTION_ELIGIBILITY_VERSION,
      allowed=True,
      state=STATIC_ELIGIBLE,
      reason_code="static_eligibility_passed",
      message="ok",
      hard_block=False,
      direction="BUY",
      entry_low=4072.99,
      entry_high=4076.89,
      planned_entry_price=4075.0,
      measured={"planned_stop_price": "4070.50"},
    ),
  )

  draft = scanner._copy_draft("XAU", result)
  assert draft is not None
  assert "/ sl 4070.5 /" in draft or "/ sl 4070.50 /" in draft
  assert "sl SL" not in draft


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
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_alert_ttl": 7200})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_level_bucket": 20})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})

  class Source:
    async def window(self, symbol, tf, n):
      assert symbol == "XAU"
      assert tf in {"M5", "M30", "M15"}
      assert n == scanner.window_for_timeframe(tf)
      return _frame()

  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: ctx,
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

  # Non-negotiable Telegram requirement: this detection has no resolvable
  # execution_match, so it must never reach Telegram - not even as a
  # MARKET OBSERVATION/ANALYSIS ONLY card. _handle_event's return value
  # must reflect that honestly (nothing was actually sent), not just echo
  # back every detected candidate regardless of whether a card went out.
  assert first == []
  assert second == []
  notify.assert_not_awaited()
  # A non-executable observation must not reserve notification dedup: the
  # same structure may become executable on a later scan and then needs its
  # forming card.
  assert await client.get(
    "scanner:alerted:XAU:M5:Trend Pullback:4100"
  ) is None
  broadcast_entry.assert_not_awaited()
  store_manual_signal.assert_not_awaited()


@pytest.mark.asyncio
async def test_match_build_failure_is_logged_not_just_an_overwritable_snapshot(
  monkeypatch, caplog,
):
  """Live incident: a "Zone Reaction" setup passed actionability/room/
  target checks and still vanished as "no executable StrategyMatch" with
  nothing in auto_trade:gate_reject:* to explain it. Root cause:
  _build_one_strategy_match failed to construct a match for it, and the
  only record was a dimensioned metric bump plus
  auto_trade:last_match_build:{symbol} - a single snapshot key the very
  next scan cycle (even an unrelated "nothing detected" one) silently
  overwrites, so by the time anyone looked, the evidence was gone. Must
  be logged so it survives past the next scan cycle.
  """
  caplog.set_level(logging.INFO, logger="app.analysis.scanner")
  client = redis_state.get_client()
  notify = AsyncMock()
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})

  class Source:
    async def window(self, symbol, tf, n):
      return _frame()

  # No "indicators" attribute at all - _build_one_strategy_match's very
  # first check (`isinstance(indicators, dict)`) fails, exactly like a
  # real match-build failure would, without needing to fabricate a more
  # elaborate rejection scenario.
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: ctx,
  )
  result = scanner.DetectionResult(
    setup="Zone Reaction",
    direction="BUY",
    key_level=4130.0,
    entry_zone=Zone(4127.56, 4130.3, "demand"),
    current_price=4130.3,
    confluence=2,
    reasons=["HTF bias up"],
  )

  def detector(received_ctx):
    return result

  sent = await scanner._handle_event(
    "XAU:M5:1",
    source=Source(),
    client=client,
    detectors=(detector,),
    notify=notify,
  )

  assert sent == []
  notify.assert_not_awaited()
  assert "scanner match build blocked" in caplog.text
  assert "setup=Zone Reaction" in caplog.text
  assert "direction=BUY" in caplog.text
  assert "reason=missing_indicators" in caplog.text


@pytest.mark.asyncio
async def test_scanner_uses_dedicated_default_notifier(monkeypatch):
  client = redis_state.get_client()
  dedicated_notify = AsyncMock()
  monkeypatch.setattr(scanner, "send_scanner_with_retry", dedicated_notify)
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_alert_ttl": 7200})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_level_bucket": 20})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})

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
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: ctx,
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
  # A card is only ever sent for a resolvable execution_match now - stand
  # in a minimal match so this test can still verify which notifier
  # _handle_event defaults to, independent of match-resolution mechanics.
  match = SimpleNamespace(
    strategy="Trend Pullback",
    match_id="dedicated-notifier-test",
    confluence_zone_id=None,
    structural_zone_id=None,
    direction="BUY",
  )
  monkeypatch.setattr(
    scanner, "_sync_strategy_match", AsyncMock(return_value=match),
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": None})

  class Source:
    async def window(self, symbol, tf, n):
      assert symbol == "XAU"
      assert tf in {"M5", "M30", "M15"}
      assert n == scanner.window_for_timeframe(tf)
      return _frame()

  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: ctx,
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: SimpleNamespace(
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": None})

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
          zone_reconcile_dropped=0, zone_reconcile_aborted=False, regime=None,
        ),
      },
    ),
    spot_price=None,
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: ctx,
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
    scanner, "build_map", lambda analysis, price, settings=None: reconciled_map,
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": None})

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
          zone_reconcile_dropped=3, zone_reconcile_aborted=True, regime=None,
        ),
      },
    ),
    spot_price=None,
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: ctx,
  )
  empty_map = MarketMap([], 4113, None, None, None, "down", "M30")
  monkeypatch.setattr(scanner, "build_map", lambda analysis, price, settings=None: empty_map)

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

  # Owner 2026-08-25: card no longer carries a Mode: line at all - the
  # header only ever shows a unified with_bias/counter_bias Bias: line.
  assert "Mode:" not in text
  assert "RANGE SCALP" not in text
  assert "COUNTER-TREND" not in text
  assert "TP1 EQ 4105" in text
  assert "TP2 edge 4100" in text


@pytest.mark.no_database
def test_scanner_card_never_claims_ready_before_worker(monkeypatch):
  result = scanner.DetectionResult(
    "Range Edge Scalp",
    "BUY",
    4100,
    Zone(4099.5, 4100.5, "demand", source="range_edge"),
    4101,
    3,
    ["lower rail confirmed"],
    mode="range_scalp",
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="range",
    structures={"M30": SimpleNamespace(bias="range")},
    frames={"M5": _frame()},
    regime=None,
    spot_price=4100,
    trigger_ts="2026-07-17T04:00:00Z",
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})

  ready = scanner._format_detection(
    "XAU",
    "M5",
    ctx,
    result,
    ["M30"],
    execution_match=SimpleNamespace(),
  )
  blocked = scanner._format_detection(
    "XAU",
    "M5",
    ctx,
    result,
    ["M30"],
    execution_match=None,
  )

  assert "QUEUED" in ready
  assert "CHECKING" not in ready
  assert "Algo bot READY" not in ready
  assert "ANALYSIS ONLY" in blocked
  assert "Algo bot BLOCKED" not in blocked


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
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_alert_ttl": 7200})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_level_bucket": 20})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_top_n": 2})
  install_runtime_overrides(monkeypatch, legacy_overrides={"alert_overlap_suppress": 0.5})

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
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: ctx,
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

  # No resolvable execution_match for any of these: they must not reach
  # Telegram and, critically, must not burn dedup needed if the structure
  # becomes executable later.
  assert sent == []
  notify.assert_not_awaited()
  assert await client.get(scanner._dedup_key("XAU", "M5", results[0])) is None
  assert await client.get(scanner._dedup_key("XAU", "M5", results[1])) is None
  assert await client.get(scanner._dedup_key("XAU", "M5", results[2])) is None


def test_scanner_digest_zero_top_n_keeps_all_distinct_results(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_top_n": 0})
  results = [
    scanner.DetectionResult(
      f"Setup {index}",
      "BUY",
      4000.0 + index * 10,
      Zone(3999.0 + index * 10, 4001.0 + index * 10, "demand"),
      4005.0 + index * 10,
      3,
      ["distinct structural thesis"],
    )
    for index in range(3)
  ]

  digest, conflicts = scanner._digest_results(results)

  assert len(digest) == 3
  assert conflicts == []


@pytest.mark.asyncio
async def test_forming_card_cap_does_not_trim_execution_digest(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  sync_strategy_match = AsyncMock(return_value=None)
  monkeypatch.setattr(scanner, "_sync_strategy_match", sync_strategy_match)
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_alert_ttl": 7200})
  install_runtime_overrides(monkeypatch, legacy_overrides={"zone_alert_ttl": 14400})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_level_bucket": 20})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_top_n": 0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_card_top_n": 2})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_track_all_structural_matches": True,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_allow_counter_bias": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_require_structural_anchor": False,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_max_source_touches": 0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_suppress_counter_bias_in_range": False,})
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
    frames={"M5": _frame()},
    regime=None,
    analysis=SimpleNamespace(per_tf={}),
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: ctx,
  )
  monkeypatch.setattr(scanner, "cache_analysis", lambda *_args: None)
  monkeypatch.setattr(
    scanner,
    "build_map",
    lambda *_args, **_kwargs: MarketMap(
      [], 4000.0, None, None, None, "up", "M30",
    ),
  )
  results = [
    scanner.DetectionResult(
      "Demand Zone Reaction",
      "BUY",
      4000.0 + index * 10,
      Zone(
        3999.0 + index * 10,
        4001.0 + index * 10,
        "demand",
        score=float(10 - index),
      ),
      4002.0 + index * 10,
      8 - index,
      ["demand zone", "wick rejection"],
      structural_source="supply_demand",
      structural_id=f"zone-{index}",
      structural_kind="demand",
    )
    for index in range(6)
  ]

  sent = await scanner._handle_event(
    "XAU:M5:card-cap",
    source=StaticSource(),
    client=client,
    detectors=tuple(
      (lambda item: lambda received_ctx: item)(result)
      for result in reversed(results)
    ),
    notify=notify,
  )

  # sync_strategy_match resolves to None here - no execution_match, so no
  # card can be sent regardless of how many candidates the card cap keeps,
  # and the return value must say so honestly. The actual point of this
  # test - the card cap must not trim what reaches sync_strategy_match - is
  # proven below via execution_digest, independent of what got carded.
  assert sent == []
  notify.assert_not_awaited()
  sync_strategy_match.assert_awaited_once()
  execution_digest = sync_strategy_match.await_args.args[5]
  assert len(execution_digest) == len(results)
  assert all(item.confluence_zone_id for item in execution_digest)
  assert all([
    await client.get(scanner._dedup_key("XAU", "M5", result)) is None
    for result in execution_digest
  ])


@pytest.mark.asyncio
async def test_digest_results_no_longer_resolves_opposing_direction_conflicts(
  monkeypatch,
):
  """P0 zone/M1 simplification: _digest_results delegates to
  _suppress_overlaps, which is now same-direction-only - cross-side
  contested-corridor resolution moved entirely to
  actionability.py::resolve_actionability, called earlier in the real
  scanner pipeline (see _handle_event, where reward_risk_eligible_results
  is already filtered through actionability.actionable before ever
  reaching _digest_results). Calling _digest_results directly with a raw,
  unfiltered opposing pair - bypassing that upstream gate, as this test
  does on purpose - now keeps both, proving the responsibility genuinely
  moved rather than silently vanishing.
  """
  client = redis_state.get_client()
  notify = AsyncMock()
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_card_top_n": 2})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_level_bucket": 20})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_track_all_structural_matches": True,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_allow_counter_bias": True})
  buy = scanner.DetectionResult(
    "Demand Zone Reaction",
    "BUY",
    4100.0,
    Zone(4099.0, 4101.0, "demand", score=12),
    4100.5,
    4,
    ["demand zone"],
    structural_source="supply_demand",
    structural_id="buy-zone",
    structural_kind="demand",
  )
  sell = scanner.DetectionResult(
    "Supply Zone Reaction",
    "SELL",
    4100.0,
    Zone(4099.2, 4100.8, "supply", score=10),
    4100.5,
    2,
    ["supply zone"],
    structural_source="supply_demand",
    structural_id="sell-zone",
    structural_kind="supply",
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
    frames={"M5": _frame()},
    regime=None,
    spot_price=None,
    trigger_ts="2026-07-10T00:00:00Z",
  )

  execution_digest, conflicts = scanner._digest_results([buy, sell])

  assert len(execution_digest) == 2
  assert buy in execution_digest
  assert sell in execution_digest
  assert conflicts == []


@pytest.mark.asyncio
async def test_structural_band_dedup_survives_boundary_jitter(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_card_top_n": 2})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_level_bucket": 20})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_alert_ttl": 7200})
  install_runtime_overrides(monkeypatch, legacy_overrides={"zone_alert_ttl": 14400})
  first = scanner.DetectionResult(
    "Demand Zone Reaction",
    "BUY",
    4100.0,
    Zone(4099.50, 4100.50, "demand"),
    4101.0,
    3,
    ["demand zone"],
    structural_source="supply_demand",
    structural_id="zone-before-jitter",
    structural_kind="demand",
  )
  jittered = scanner.DetectionResult(
    "Demand Zone Reaction",
    "BUY",
    4100.005,
    Zone(4099.505, 4100.505, "demand"),
    4101.0,
    3,
    ["demand zone"],
    structural_source="supply_demand",
    structural_id="zone-after-jitter",
    structural_kind="demand",
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
    frames={"M5": _frame()},
    regime=None,
    spot_price=None,
    trigger_ts="2026-07-10T00:00:00Z",
  )

  first_sent = await scanner._notify_digest_once(
    client, "XAU", "M5", ctx, [first], notify, ["M30"],
  )
  second_sent = await scanner._notify_digest_once(
    client, "XAU", "M5", ctx, [jittered], notify, ["M30"],
  )

  assert scanner._band_dedup_key("XAU", first) == scanner._band_dedup_key(
    "XAU", jittered,
  )
  assert scanner._dedup_key("XAU", "M5", first) != scanner._dedup_key(
    "XAU", "M5", jittered,
  )
  # No execution_match passed in - no card can be sent, so nothing was
  # actually delivered either time. Band-dedup identity is proven above
  # independently of what got carded.
  assert first_sent == []
  assert second_sent == []
  notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_structural_anchor_preference_is_telemetry_not_execution_filter(
  monkeypatch,
):
  client = redis_state.get_client()
  notify = AsyncMock()
  sync_strategy_match = AsyncMock(return_value=None)
  monkeypatch.setattr(scanner, "_sync_strategy_match", sync_strategy_match)
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_require_structural_anchor": True,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_max_source_touches": 0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_suppress_counter_bias_in_range": False,})
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
    frames={"M5": _frame()},
    regime=None,
  )
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: ctx,
  )
  round_only = scanner.DetectionResult(
    "Key Level Reaction",
    "BUY",
    4100.0,
    Zone(4099.5, 4100.5, "demand"),
    4100.5,
    3,
    ["key round 4100 x4", "wick rejection"],
    structural_source="key_level",
    structural_id="round-only",
    structural_kind="round",
    source_touches=4,
  )
  anchored = scanner.DetectionResult(
    **{
      **round_only.__dict__,
      "structural_id": "round-with-ob",
      "reasons": ["key round 4100 x4", "OB", "wick rejection"],
    },
  )

  sent = await scanner._handle_event(
    "XAU:M5:structure-gate",
    source=StaticSource(),
    client=client,
    detectors=(lambda received_ctx: round_only,),
    notify=notify,
  )

  assert sent == []
  notify.assert_not_awaited()
  forwarded = sync_strategy_match.await_args.args[5]
  assert len(forwarded) == 1
  assert forwarded[0].setup == "Key Level Reaction"
  assert scanner._structure_card_gate(anchored, ctx) is None
  status = json.loads(await client.get("scanner:last_tick:XAU:M5"))
  assert len(status["detected"]) == 1
  assert status["detected"][0]["setup"] == "Key Level Reaction"
  assert status["structure_gated"] == [{
    "setup": "Key Level Reaction",
    "direction": "BUY",
    "reason": "round_without_structural_anchor",
  }]
  detect_log = json.loads((
    await client.lrange(scanner._detect_log_key("XAU", "M5"), 0, 0)
  )[0])
  assert detect_log["entries"][0]["outcome"] == "actionability_observed"
  assert (
    detect_log["entries"][0]["reason"]
    == "key_level_role_ambiguous"
  )


def test_structure_gate_defaults_are_a_noop(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_require_structural_anchor": False,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_max_source_touches": 0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_suppress_counter_bias_in_range": False,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_track_all_structural_matches": True,})
  result = scanner.DetectionResult(
    "Key Level Reaction",
    "SELL",
    4100.0,
    Zone(4099.5, 4100.5, "supply"),
    4100.0,
    1,
    ["key round 4100 x99"],
    mode="counter_bias",
    structural_source="key_level",
    structural_id="default-noop",
    structural_kind="round",
    source_touches=99,
    bias_relationship="counter_bias",
  )
  ctx = SimpleNamespace(
    tf="M5",
    structures={
      "M5": SimpleNamespace(
        scalp_range=SimpleNamespace(state="provisional_range"),
      ),
    },
    regime=Regime("chop", 4105, 4095, 2.0, ["fixture chop"]),
  )

  assert scanner._structure_card_gate(result, ctx) is None
  digest, conflicts = scanner._digest_results([result])
  assert digest == [result]
  assert digest[0] is result
  assert conflicts == []


def test_structure_gate_rejects_exhausted_levels_and_weak_range_counter_bias(
  monkeypatch,
):
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_require_structural_anchor": False,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_max_source_touches": 6})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_suppress_counter_bias_in_range": True,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_counter_bias_min_confluence": 3,})
  ctx = SimpleNamespace(
    tf="M5",
    structures={
      "M5": SimpleNamespace(
        scalp_range=SimpleNamespace(state="post_impulse_range"),
      ),
    },
    regime=None,
  )

  def result(
    *,
    touches: int,
    confluence: int,
    mode: str = "with_bias",
    relationship: str | None = None,
  ):
    return scanner.DetectionResult(
      "Demand Zone Reaction",
      "BUY",
      4100.0,
      Zone(4099.5, 4100.5, "demand"),
      4100.0,
      confluence,
      ["demand zone"],
      mode=mode,
      structural_source="supply_demand",
      structural_id=f"{touches}-{confluence}-{mode}-{relationship}",
      structural_kind="demand",
      source_touches=touches,
      bias_relationship=relationship,
    )

  assert (
    scanner._structure_card_gate(result(touches=7, confluence=4), ctx)
    == "source_level_exhausted"
  )
  assert scanner._structure_card_gate(result(touches=5, confluence=4), ctx) is None
  assert (
    scanner._structure_card_gate(
      result(touches=5, confluence=2, mode="counter_bias"),
      ctx,
    )
    == "low_confluence_counter_bias_in_range"
  )
  assert (
    scanner._structure_card_gate(
      result(
        touches=5,
        confluence=2,
        relationship="counter_bias",
      ),
      ctx,
    )
    == "low_confluence_counter_bias_in_range"
  )
  assert scanner._structure_card_gate(
    result(touches=5, confluence=3, mode="counter_bias"),
    ctx,
  ) is None


@pytest.mark.asyncio
async def test_scanner_zone_band_dedup_preserves_cross_setup_ideas(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_alert_ttl": 7200})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_level_bucket": 20})
  install_runtime_overrides(monkeypatch, legacy_overrides={"zone_alert_ttl": 14400})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_top_n": 1})

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
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: ctx,
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

  # No execution_match resolvable for any of these detections: no card is
  # delivered and no notification dedup is reserved.
  assert first == []
  assert same_band == []
  assert far_band == []
  notify.assert_not_awaited()
  assert await client.get(scanner._band_dedup_key("XAU", result_a)) is None
  assert await client.get(scanner._dedup_key("XAU", "M5", result_b)) is None

  await client.delete(scanner._band_dedup_key("XAU", result_b))
  await client.delete(scanner._dedup_key("XAU", "M5", result_b))
  # This fixture's frame never advances, so result_far's zone would read as
  # "invalidated" (B3) on the very next scan against the same static close -
  # clear its tracking state, matching the band-dedup reset above, since this
  # test isn't exercising invalidation.
  await client.delete(scanner._active_setup_band_key("XAU", "M5", result_far))
  current["result"] = result_b
  after_ttl = await scanner._handle_event(
    "XAU:M5:4",
    source=StaticSource(),
    client=client,
    detectors=(detector,),
    notify=notify,
  )

  assert after_ttl == []
  notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_box_breakout_second_alert_on_same_edge_is_band_deduped(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_level_bucket": 20})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_alert_ttl": 7200})
  install_runtime_overrides(monkeypatch, legacy_overrides={"zone_alert_ttl": 14400})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
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

  # No execution_match passed in - no card can be sent, so nothing was
  # actually delivered either time.
  assert first == []
  assert second == []
  notify.assert_not_awaited()


def test_box_breakout_now_participates_in_confluence_merge():
  """box_breakout/break_retest were kept replay-only specifically because
  they were "not yet re-verified against band-kind classification and
  canonical BREAKOUT_RETEST family merge" (detectors.py registry). The
  actual root cause: neither detector ever set structural_source/
  structural_id on its DetectionResult, and _merge_detection_confluence
  below only considers results with a truthy structural_id - so a Box
  Breakout firing on the same band as an already-live Key Level Reaction
  used to always stay a separate, unmerged result instead of collapsing
  into one order. Now that both fields are wired (see detectors.py), a
  Box Breakout result merges exactly like every other structural source.
  """
  box_result = scanner.DetectionResult(
    setup="Box Breakout",
    direction="BUY",
    key_level=4100.0,
    entry_zone=Zone(4109.6, 4110.4, "demand", source="box_breakout"),
    current_price=4110.8,
    confluence=2,
    reasons=["box breakout"],
    structural_source="box_breakout",
    structural_id="box-4100-4110-up-9",
    structural_low=4109.6,
    structural_high=4110.4,
  )
  key_level_result = scanner.DetectionResult(
    setup="Key Level Reaction",
    direction="BUY",
    key_level=4110.0,
    entry_zone=Zone(4109.8, 4110.2, "demand", source="key_level"),
    current_price=4110.8,
    confluence=2,
    reasons=["key level reaction"],
    structural_source="key_level",
    structural_id="key-level-4110",
    structural_low=4109.8,
    structural_high=4110.2,
  )

  merged = scanner._merge_detection_confluence(
    "XAU", "M5", [box_result, key_level_result], atr=2.0,
  )

  assert len(merged) == 1
  result = merged[0]
  assert result.confluence_zone_id is not None
  assert set(result.confluence_tags) == {"box_breakout", "key_level"}


@pytest.mark.asyncio
async def test_band_dedup_preserves_a_different_structural_setup(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  first = scanner.DetectionResult(
    "Trend Pullback",
    "BUY",
    4100.0,
    Zone(4099.5, 4100.5, "demand", score=9),
    4101.0,
    2,
    ["HTF bias up"],
  )
  second = scanner.DetectionResult(
    "Demand Reaction",
    "BUY",
    4100.0,
    Zone(4099.5, 4100.5, "demand", score=9),
    4101.0,
    2,
    ["HTF bias up"],
  )
  await client.set(
    scanner._band_dedup_key("XAU", first),
    "1",
    ex=3600,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"M30": SimpleNamespace(bias="up")},
    frames={"M5": _frame()},
    regime=None,
    spot_price=None,
    trigger_ts="2026-07-10T00:00:00Z",
  )

  sent = await scanner._notify_digest_once(
    client,
    "XAU",
    "M5",
    ctx,
    [first, second],
    notify,
    ["M30"],
  )

  # No execution_match passed in - no card can be sent, so nothing was
  # actually delivered. _notify_digest_once must report that accurately
  # (not the candidate list regardless of whether post_or_edit_forming_card
  # was ever called) - see test above for the same invariant.
  assert sent == []
  notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_scanner_uses_fresh_spot_for_context_and_live_render(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  now = int(datetime.now(timezone.utc).timestamp())
  await client.set(
    "price:XAU:spot",
    json.dumps({"bid": 4082.0, "ask": 4082.2, "ts": now}),
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"spot_fresh_secs": 30})
  install_runtime_overrides(monkeypatch, legacy_overrides={"spot_max_deviation_pct": 2.0})

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
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: ctx,
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

  # A card is only ever sent for a resolvable execution_match now - this
  # test is about live-spot rendering, not match resolution.
  monkeypatch.setattr(
    scanner,
    "_sync_strategy_match",
    AsyncMock(return_value=SimpleNamespace(
      strategy="Trend Pullback", match_id="fresh-spot-test",
      confluence_zone_id=None, structural_zone_id=None, direction="BUY",
    )),
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_alert_ttl": 7200})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_level_bucket": 20})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"spot_fresh_secs": 30})
  install_runtime_overrides(monkeypatch, legacy_overrides={"spot_max_deviation_pct": 2.0})

  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: SimpleNamespace(
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

  # A card is only ever sent for a resolvable execution_match now - this
  # test is about implausible-spot handling, not match resolution.
  monkeypatch.setattr(
    scanner,
    "_sync_strategy_match",
    AsyncMock(return_value=SimpleNamespace(
      strategy="Trend Pullback", match_id="implausible-spot-test",
      confluence_zone_id=None, structural_zone_id=None, direction="BUY",
    )),
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"spot_fresh_secs": 30})
  install_runtime_overrides(monkeypatch, legacy_overrides={"spot_max_deviation_pct": 2.0})
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: SimpleNamespace(
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"spot_fresh_secs": 30})
  install_runtime_overrides(monkeypatch, legacy_overrides={"spot_max_deviation_pct": 2.0})
  monkeypatch.setattr(
    scanner,
    "build_context",
    lambda symbol, tf, frames, settings, htf_order, **_kwargs: SimpleNamespace(
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

def test_suppress_overlaps_no_longer_resolves_opposing_direction_conflicts(
  monkeypatch,
):
  """P0 zone/M1 simplification: _suppress_overlaps used to re-run its own
  opposing-direction confluence-margin tiebreak (numbers below are lifted
  straight from the 22 Jul 2026 incident this originally regression-
  tested), duplicating what actionability.py::resolve_actionability's
  contested-corridor rule already resolves earlier in the same request.
  That responsibility has moved entirely upstream - see
  test_scanner_actionability.py's contested-corridor tests for the
  authoritative behavior. This proves _suppress_overlaps itself is now
  same-direction-only: an opposing overlapping pair both survive this
  specific function untouched (same-direction dedup is unaffected).
  """
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_track_all_structural_matches": False,})
  strong = scanner.DetectionResult(
    "Demand Zone Reaction", "BUY", 4121.5,
    Zone(4121.22, 4126.14, "demand"), 4123.0, 3, ["HTF bias up"],
  )
  weak = scanner.DetectionResult(
    "Range Edge Scalp", "SELL", 4123.5,
    Zone(4122.24, 4124.73, "supply"), 4123.0, 2, ["HTF bias down"],
  )

  selected, conflicts = scanner._suppress_overlaps([strong, weak])

  assert selected == [strong, weak]
  assert conflicts == []


def test_true_duplicate_same_direction_overlap_keeps_stronger(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"alert_overlap_suppress": 0.5})
  strong = scanner.DetectionResult(
    "Snap-Back", "SELL", 4094.0,
    Zone(4094, 4096, "supply", score=13), 4090.0, 3, ["HTF bias down"],
  )
  weak = scanner.DetectionResult(
    "Snap-Back", "SELL", 4095.0,
    Zone(4095, 4097, "supply", score=11), 4090.0, 2, ["HTF bias down"],
  )

  selected, conflicts = scanner._suppress_overlaps([strong, weak])

  assert selected == [strong]
  assert conflicts == []


# --- B3: setup invalidation --------------------------------------------------

@pytest.mark.asyncio
async def test_setup_invalidation_is_silent_when_zone_is_violated(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  result = scanner.DetectionResult(
    "Range Edge Scalp",
    "SELL",
    4124.0,
    Zone(4122.24, 4124.73, "supply"),
    4123.0,
    2,
    [],
  )
  key = scanner._active_setup_band_key("XAU", "M5", result)
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

  notify.assert_not_awaited()
  assert await client.get(key) is None


@pytest.mark.asyncio
async def test_setup_invalidation_does_not_fire_while_zone_holds():
  client = redis_state.get_client()
  notify = AsyncMock()
  result = scanner.DetectionResult(
    "Range Edge Scalp",
    "SELL",
    4123.0,
    Zone(4122.24, 4124.73, "supply"),
    4123.0,
    2,
    [],
  )
  key = scanner._active_setup_band_key("XAU", "M5", result)
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


@pytest.mark.asyncio
async def test_setup_invalidation_suppressed_after_autonomous_entry(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  await client.delete("auto_trade:positions")
  result = scanner.DetectionResult(
    "Key Level Reaction",
    "BUY",
    4095.0,
    Zone(4093.88, 4097.2, "demand"),
    4095.0,
    2,
    [],
  )
  key = scanner._active_setup_band_key("XAU", "M5", result)
  await client.set(key, json.dumps({
    "setup": "Key Level Reaction",
    "direction": "BUY",
    "zone_low": 4093.88,
    "zone_high": 4097.2,
    "confluence": 2,
  }))
  await client.sadd("auto_trade:positions", "39000344")
  await client.set(
    "auto_trade:position:39000344",
    json.dumps({
      "position_id": 39000344,
      "symbol": "XAU",
      "direction": 0,
      "remaining_volume": 400,
      "parent_group_id": None,
    }),
    ex=60,
  )
  df = pd.DataFrame(
    {"close": [4092.0]},
    index=pd.date_range("2026-07-27", periods=1, freq="5min", tz="UTC"),
  )

  await scanner._check_setup_invalidations(client, "XAU", "M5", df, notify, 0.0)

  notify.assert_not_awaited()
  assert await client.get(key) is None


@pytest.mark.asyncio
async def test_overlapping_setup_invalidations_are_all_silent(monkeypatch):
  client = redis_state.get_client()
  notify = AsyncMock()
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_level_bucket": 20})
  key_level = scanner.DetectionResult(
    "Key Level Reaction",
    "BUY",
    4095.0,
    Zone(4093.88, 4097.2, "demand"),
    4095.0,
    2,
    [],
  )
  break_retest = scanner.DetectionResult(
    "Break & Retest",
    "BUY",
    4094.5,
    Zone(4093.44, 4095.43, "demand"),
    4094.5,
    2,
    [],
  )
  await scanner._track_active_setups(client, "XAU", "M5", [key_level, break_retest])
  df = pd.DataFrame(
    {"close": [4092.0]},
    index=pd.date_range("2026-07-27", periods=1, freq="5min", tz="UTC"),
  )

  await scanner._check_setup_invalidations(client, "XAU", "M5", df, notify, 0.0)

  notify.assert_not_awaited()
  assert await client.get(
    scanner._active_setup_band_key("XAU", "M5", key_level),
  ) is None


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


def test_scanner_default_htf_is_h1_not_m30():
  # H1->M15->M5 single-analysis-source cutover (P2): the scanner's default
  # HTF stack is H1 (primary) + M15, with no M30 dependency anywhere.
  assert leaf(runtime_config, "scanner_htf") == "H1,M15"
  assert scanner._htf_tfs() == ["H1", "M15"]
  assert scanner._all_tfs("M5", scanner._htf_tfs()) == ["M5", "H1", "M15"]
  assert "M30" not in scanner._all_tfs("M5", scanner._htf_tfs())


@pytest.mark.asyncio
async def test_scanner_loads_frames_with_h1_present_and_m30_absent(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_window": 500})
  source = StaticSource()
  frames = await scanner._load_frames(source, "XAU", "M5", scanner._htf_tfs())
  assert "H1" in frames
  assert "M30" not in frames


def _card_ctx() -> SimpleNamespace:
  return SimpleNamespace(
    tf="M5",
    htf_bias="up",
    structures={"H1": SimpleNamespace(bias="up")},
    frames={"M5": _frame()},
    regime=None,
    spot_price=None,
    trigger_ts="2026-07-10T00:00:00Z",
  )


def _card_result(structural_id: str = "demand-1") -> "scanner.DetectionResult":
  return scanner.DetectionResult(
    "Demand Zone Reaction",
    "BUY",
    4100.0,
    Zone(4099.5, 4100.5, "demand"),
    4101.0,
    3,
    ["demand zone"],
    structural_source="supply_demand",
    structural_id=structural_id,
    structural_kind="demand",
  )


@pytest.mark.asyncio
async def test_non_executable_observation_does_not_burn_future_forming_card(
  monkeypatch,
):
  """Production incident: the same zone became executable 24m later."""
  client = redis_state.get_client()
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  result = _card_result("incident-zone")
  notify = AsyncMock(return_value=SimpleNamespace(message_id=9100))

  first = await scanner._notify_digest_once(
    client,
    "XAU",
    "M5",
    _card_ctx(),
    [result],
    notify,
    ["H1"],
  )

  assert first == []
  assert await client.get(scanner._dedup_key("XAU", "M5", result)) is None
  assert await client.get(scanner._band_dedup_key("XAU", result)) is None

  match = SimpleNamespace(
    strategy=result.setup,
    match_id="incident-setup-id",
    structural_zone_id=result.structural_id,
  )
  second = await scanner._notify_digest_once(
    client,
    "XAU",
    "M5",
    _card_ctx(),
    [result],
    notify,
    ["H1"],
    execution_match=match,
  )

  assert second == [result]
  notify.assert_awaited_once()
  assert await client.get(scanner._dedup_key("XAU", "M5", result)) == "1"
  assert await client.get(scanner._band_dedup_key("XAU", result)) == "1"


@pytest.mark.asyncio
async def test_confluence_zone_id_mismatch_falls_back_to_strategy_match(
  monkeypatch,
):
  """Live incident: "Zone Reaction BUY" passed actionability/room/target
  checks, then vanished with no logged reason one line later. Root cause:
  dedupe_matches (_build_strategy_match) can merge this exact result's
  StrategyMatch into a different match_id (Zone Reaction is a named
  alias-prone strategy in multi_match.py's same_thesis()) - the surviving
  match's confluence_zone_id then no longer equals the DetectionResult's
  own confluence_zone_id. A result WITHOUT a confluence_zone_id already
  fell back to matching by strategy/structural_id; a result WITH one did
  not, so the mismatch was fatal instead of just falling back like normal.
  """
  client = redis_state.get_client()
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  notify = AsyncMock(return_value=SimpleNamespace(message_id=9200))
  result = scanner.DetectionResult(
    "Zone Reaction",
    "BUY",
    4100.0,
    Zone(4099.5, 4100.5, "demand"),
    4101.0,
    3,
    ["zone reaction"],
    structural_source="supply_demand",
    structural_id="zone-x",
    structural_kind="demand",
    confluence_zone_id="pre-merge-zone-id",
  )
  # The survivor a same-thesis merge left behind - a different match_id and
  # confluence_zone_id, but the same strategy/structural identity.
  survivor = SimpleNamespace(
    strategy="Zone Reaction",
    match_id="survivor-after-merge",
    structural_zone_id="zone-x",
    confluence_zone_id="post-merge-zone-id",
    direction="BUY",
  )

  sent = await scanner._notify_digest_once(
    client,
    "XAU",
    "M5",
    _card_ctx(),
    [result],
    notify,
    ["H1"],
    execution_match=survivor,
  )

  assert sent == [result]
  notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_strategy_match_logs_the_real_build_rejection(
  monkeypatch, caplog,
):
  """Live incident: two SELL setups (Zone Reaction, Session Level Reaction)
  both passed actionability/room checks and still vanished as "no
  executable StrategyMatch" with nothing left to explain it -
  auto_trade:gate_reject:*/last_match_build had no fresh hit for either.
  _build_one_strategy_match is called TWICE per result: once inside
  _reward_risk_pre_gate purely for telemetry (that call's own failures are
  logged separately, "scanner match build blocked"), and again here inside
  _sync_strategy_match's _build_strategy_match - the one that actually
  feeds execution_match/strategy_matches_key. Nothing guarantees identical
  input/state between the two calls, so the telemetry call can succeed
  while this, the real one, fails - and until now, silently.
  """
  caplog.set_level(logging.INFO, logger="app.analysis.scanner")
  client = redis_state.get_client()
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda symbol, tf, event_ts, ctx, result, now=None: (
      None, "unknown_strategy_policy", {"strategy": result.setup},
    ),
  )
  result = scanner.DetectionResult(
    "Session Level Reaction",
    "SELL",
    4175.71,
    Zone(4175.71, 4181.23, "supply"),
    4175.71,
    2,
    ["session level"],
  )

  match = await scanner._sync_strategy_match(
    client, "XAU", "M5", "2026-08-05T07:10:07Z", SimpleNamespace(), [result],
  )

  assert match is None
  assert "scanner match build rejected" in caplog.text
  assert "reason=unknown_strategy_policy" in caplog.text
  assert "Session Level Reaction:SELL" in caplog.text


def test_build_strategy_match_logs_dedupe_merge_events(monkeypatch, caplog):
  """dedupe_matches computes which match_id got merged into which, but the
  return value used to be discarded (deduped, _events = dedupe_matches(...),
  _events never read again anywhere in this file) - a merge left zero
  trace anywhere, so a detection that just passed actionability/room/
  target checks looked like it vanished for no reason at all. Must now be
  logged so an operator can actually see what happened.
  """
  caplog.set_level(logging.INFO, logger="app.analysis.scanner")
  loser = SimpleNamespace(
    match_id="loser-id", strategy="Zone Reaction", direction="BUY",
    tier="B", confluence=2, atr=2.0,
  )
  survivor = SimpleNamespace(
    match_id="survivor-id", strategy="Flip Zone", direction="BUY",
    tier="B", confluence=3, atr=2.0,
  )
  first = scanner.DetectionResult(
    "Zone Reaction", "BUY", 4100.0, Zone(4099.5, 4100.5, "demand"),
    4101.0, 2, ["zone reaction"],
  )
  second = scanner.DetectionResult(
    "Flip Zone", "BUY", 4100.0, Zone(4099.5, 4100.5, "demand"),
    4101.0, 3, ["flip zone"],
  )
  built_by_id = {id(first): loser, id(second): survivor}
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda symbol, tf, event_ts, ctx, result, now=None: (
      built_by_id[id(result)], None, {},
    ),
  )
  monkeypatch.setattr(
    scanner,
    "dedupe_matches",
    lambda matches, atr, cfg=None: (
      [survivor],
      [
        {"match_id": "loser-id", "event": "merged_confluence", "into": "survivor-id"},
        {"match_id": "survivor-id", "event": "tracked", "strategy": "Flip Zone"},
      ],
    ),
  )

  match, reason, measured = scanner._build_strategy_match(
    "XAU", "M5", "2026-07-10T00:00:00Z", SimpleNamespace(), [first, second],
  )

  assert match is survivor
  assert reason is None
  assert "strategy match merged" in caplog.text
  assert "match_id=loser-id" in caplog.text
  assert "into=survivor-id" in caplog.text
  # "tracked" is the normal no-merge outcome for every result on every
  # scan - logging it too would be pure noise, so it must stay silent.
  assert caplog.text.count("strategy match merged") == 1


@pytest.mark.asyncio
async def test_one_forming_card_per_setup_identical_redetection_is_noop(
  monkeypatch,
):
  # One forming card per setup (P4): re-detection of the same setup_id
  # retains the existing card without an identical Telegram edit.
  client = redis_state.get_client()
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  sent_texts = []
  edited = []

  async def notify(text, **kwargs):
    sent_texts.append(text)
    return SimpleNamespace(message_id=9001)

  async def edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  match = SimpleNamespace(
    strategy="Demand Zone Reaction",
    match_id="p4-setup-1",
    structural_zone_id=None,
  )
  result = _card_result()

  await scanner._notify_digest_once(
    client, "XAU", "M5", _card_ctx(), [result], notify, ["H1"],
    execution_match=match, edit=edit,
  )
  # Second detection reaches here (band/dedup keys already claimed the
  # first time) by clearing them, simulating a later independent cycle
  # that reconfirms the same structural_id.
  await client.delete(scanner._dedup_key("XAU", "M5", result))
  await client.delete(scanner._band_dedup_key("XAU", result))

  await scanner._notify_digest_once(
    client, "XAU", "M5", _card_ctx(), [result], notify, ["H1"],
    execution_match=match, edit=edit,
  )

  assert len(sent_texts) == 1
  assert edited == []


@pytest.mark.asyncio
async def test_terminal_setup_is_never_re_carded_by_scanner(monkeypatch):
  from app.autotrade.setup_lifecycle import (
    CONFIRMED,
    INVALIDATED,
    create_setup,
    transition_setup,
  )

  client = redis_state.get_client()
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  await create_setup(
    client, setup_id="p4-setup-2", thesis_id="thesis-2", symbol="XAU",
  )
  for state in ("watching", "touched", "forming", CONFIRMED):
    await transition_setup(client, "p4-setup-2", state)
  await transition_setup(client, "p4-setup-2", INVALIDATED, reason_code="structure_broke")

  calls = []

  async def notify(text, **kwargs):
    calls.append(text)
    return SimpleNamespace(message_id=1)

  async def edit(chat_id, message_id, text):
    calls.append(text)

  match = SimpleNamespace(
    strategy="Demand Zone Reaction",
    match_id="p4-setup-2",
    structural_zone_id=None,
  )

  await scanner._notify_digest_once(
    client, "XAU", "M5", _card_ctx(), [_card_result()], notify, ["H1"],
    execution_match=match, edit=edit,
  )

  assert calls == []


@pytest.mark.asyncio
async def test_structure_invalidation_deletes_card_for_tracked_setup(monkeypatch):
  from app.autotrade.setup_lifecycle import (
    CONFIRMED,
    create_setup,
    load_setup,
    transition_setup,
  )
  from app.autotrade import setup_card as setup_card_module

  client = redis_state.get_client()
  await create_setup(
    client, setup_id="p4-setup-3", thesis_id="thesis-3", symbol="XAU",
  )
  for state in ("watching", "touched", "forming", CONFIRMED):
    await transition_setup(client, "p4-setup-3", state)
  await setup_card_module.save_forming_card(
    client, "p4-setup-3", chat_id=4242, message_id=7777,
  )
  await client.set(
    "scanner:setup:active_band:XAU:M5:test-bucket",
    json.dumps({
      "setup": "Demand Zone Reaction",
      "direction": "BUY",
      "zone_low": 4099.5,
      "zone_high": 4100.5,
      "confluence": 3,
      "match_id": "p4-setup-3",
    }),
    ex=3600,
  )
  deleted = []

  async def delete_fn(chat_id, message_id):
    deleted.append((chat_id, message_id))

  monkeypatch.setattr(scanner, "delete_scanner_message", delete_fn)
  monkeypatch.setattr(
    setup_card_module, "should_delete_root_on_terminal", lambda: True,
  )
  standalone_calls = []

  async def notify(text, **kwargs):
    standalone_calls.append(text)
    return SimpleNamespace(message_id=1)

  df = pd.DataFrame({
    "open": [4095.0], "high": [4095.5], "low": [4094.5], "close": [4095.0],
  }, index=pd.date_range("2026-07-10", periods=1, freq="5min", tz="UTC"))

  await scanner._check_setup_invalidations(client, "XAU", "M5", df, notify, atr=1.0)

  assert deleted == [(4242, 7777)]
  assert standalone_calls == []  # no "SETUP INVALIDATED" text posted
  record = await load_setup(client, "p4-setup-3")
  assert record.state == "invalidated"
  assert await setup_card_module.load_forming_card(client, "p4-setup-3") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "handoff_state",
  ["ready_event_enqueued", "worker_acknowledged", "armed_waiting_trigger"],
)
async def test_structure_invalidation_covers_the_durable_handoff_states(
  monkeypatch, handoff_state,
):
  """P1-1: legacy Redis handoff values (normalized to CONFIRMED for publish
  eligibility) must still be invalidated and have their card cleared if
  structure breaks while they wait. New code never writes these states, but
  in-flight records may still carry them.
  """
  import time
  from dataclasses import replace

  from app.autotrade.setup_lifecycle import (
    CONFIRMED,
    create_setup,
    load_setup,
    setup_key,
    transition_setup,
  )
  from app.autotrade import setup_card as setup_card_module

  client = redis_state.get_client()
  setup_id = f"p4-setup-handoff-{handoff_state}"
  await create_setup(
    client, setup_id=setup_id, thesis_id="thesis-handoff", symbol="XAU",
  )
  for state in ("watching", "touched", "forming", CONFIRMED):
    await transition_setup(client, setup_id, state)
  current = await load_setup(client, setup_id)
  assert current is not None
  seeded = replace(current, state=handoff_state, updated_at=int(time.time()))
  await client.set(
    setup_key(setup_id),
    json.dumps(seeded.to_dict(), separators=(",", ":")),
  )
  await setup_card_module.save_forming_card(
    client, setup_id, chat_id=4242, message_id=7778,
  )
  await client.set(
    f"scanner:setup:active_band:XAU:M5:test-bucket-{handoff_state}",
    json.dumps({
      "setup": "Demand Zone Reaction",
      "direction": "BUY",
      "zone_low": 4099.5,
      "zone_high": 4100.5,
      "confluence": 3,
      "match_id": setup_id,
    }),
    ex=3600,
  )
  deleted = []

  async def delete_fn(chat_id, message_id):
    deleted.append((chat_id, message_id))

  monkeypatch.setattr(scanner, "delete_scanner_message", delete_fn)
  monkeypatch.setattr(
    setup_card_module, "should_delete_root_on_terminal", lambda: True,
  )
  standalone_calls = []

  async def notify(text, **kwargs):
    standalone_calls.append(text)
    return SimpleNamespace(message_id=1)

  df = pd.DataFrame({
    "open": [4095.0], "high": [4095.5], "low": [4094.5], "close": [4095.0],
  }, index=pd.date_range("2026-07-10", periods=1, freq="5min", tz="UTC"))

  await scanner._check_setup_invalidations(client, "XAU", "M5", df, notify, atr=1.0)

  assert deleted == [(4242, 7778)]
  assert standalone_calls == []
  record = await load_setup(client, setup_id)
  assert record.state == "invalidated"
  assert await setup_card_module.load_forming_card(client, setup_id) is None

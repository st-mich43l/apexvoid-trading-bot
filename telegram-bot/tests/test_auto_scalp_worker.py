import inspect
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.autotrade import worker
from app.persistence import redis_state
from app.analysis import scanner
from app.autotrade.gate import AutoScalpBox, AutoScalpDecision, AutoScalpRail
from app.autotrade.scale_context import AutoScaleContext
from app.autotrade.trend import RegimeInfo, TrendDecision


def _frame() -> pd.DataFrame:
  index = pd.date_range("2026-07-20", periods=20, freq="1min", tz="UTC")
  return pd.DataFrame({
    "open": [4016.8] * 20,
    "high": [4017.4] * 20,
    "low": [4016.2] * 20,
    "close": [4017.0] * 20,
    "volume": [100.0] * 20,
  }, index=index)


def _decision() -> AutoScalpDecision:
  support = AutoScalpRail(
    "support",
    4016.5,
    4017.1,
    4016.8,
    3,
    8.0,
    ("M5", "M15"),
    ("M5 swing-low", "M15 range-low"),
  )
  resistance = AutoScalpRail(
    "resistance",
    4024.8,
    4025.4,
    4025.1,
    3,
    8.0,
    ("M5", "M15"),
    ("M5 swing-high", "M15 range-high"),
  )
  box = AutoScalpBox("xau-8034-8050", support, resistance, 77.0)
  return AutoScalpDecision(
    "candidate",
    direction="BUY",
    trigger="range_rejection",
    rail=support,
    target=resistance,
    target_room_pips=76.0,
    full_tp_pips=70,
    box=box,
    confluence=3,
    reasons=("M1 range rejection", "support rail"),
    rail_count=4,
  )


def _scale_context(now: int) -> AutoScaleContext:
  return AutoScaleContext(
    bar_ts=now - 60,
    atr=1.2,
    structure_swing=4014.8,
    displacement_direction="up",
    displacement_age_bars=1,
    bos_direction="up",
    bos_ts=now - 60,
    opposing_level_distance_atr=2.5,
  )


@pytest.mark.asyncio
async def test_worker_publishes_one_durable_auto_only_candidate(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_stream_maxlen", 100)
  monkeypatch.setattr(worker.settings, "auto_trade_candidate_ttl", 3600)
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  spot = worker.AutoTradeSpot(4017.2, now, True)

  first = await worker._publish_candidate(
    client, "XAU", "1784552400", spot, _decision(), _scale_context(now)
  )
  second = await worker._publish_candidate(
    client, "XAU", "1784552400", spot, _decision(), _scale_context(now)
  )

  assert first is not None
  assert second is None
  entries = await client.xrange("auto_trade:test")
  assert len(entries) == 1
  payload = json.loads(entries[0][1]["payload"])
  assert payload["candidate_id"] == first
  assert payload["setup"] == "Range Box Scalp"
  assert payload["mode"] == "auto_box_scalp"
  assert payload["timeframe"] == "M1"
  assert payload["direction"] == "BUY"
  assert payload["entry_zone"] == {"low": 4016.5, "high": 4017.1}
  assert payload["spot_ts"] == now
  assert payload["version"] == 3
  assert payload["range_id"] == "xau-8034-8050"
  assert payload["range_low"] == 4016.8
  assert payload["range_high"] == 4025.1
  assert payload["full_take_profit_pips"] == 70
  assert payload["structure_swing"] == 4014.8
  assert payload["displacement_age_bars"] == 1
  assert payload["bos_direction"] == "up"
  assert await client.exists(worker._box_edge_key(
    "XAU",
    "xau-8034-8050",
    "BUY",
  ))


@pytest.mark.asyncio
async def test_worker_handles_m1_without_calling_scanner(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_symbols", "XAU")
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  source = AsyncMock()
  source.window = AsyncMock(return_value=_frame())
  monkeypatch.setattr(
    worker,
    "_load_spot",
    AsyncMock(return_value=worker.AutoTradeSpot(4017.2, now, True)),
  )
  monkeypatch.setattr(
    worker,
    "evaluate_auto_scalp_gate",
    lambda frames, **kwargs: _decision(),
  )
  monkeypatch.setattr(
    worker,
    "build_auto_scale_context",
    lambda *args, **kwargs: _scale_context(now),
  )
  forming = AsyncMock()
  monkeypatch.setattr(scanner, "_handle_event", forming)

  result = await worker._handle_event(
    "XAU:M1:1784552400",
    source=source,
    client=client,
  )

  assert result == _decision()
  forming.assert_not_awaited()
  assert await client.xlen("auto_trade:test") == 1
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["state"] == "candidate"
  assert status["rail"]["role"] == "support"
  assert status["rail"]["timeframes"] == ["M5", "M15"]
  assert status["box"]["id"] == "xau-8034-8050"
  assert status["full_tp_pips"] == 70


@pytest.mark.asyncio
async def test_broken_box_is_retired_and_cannot_publish_again(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(
    worker.settings,
    "auto_trade_box_retire_seconds",
    3600,
  )
  candidate = _decision()
  broken = AutoScalpDecision(
    "box_broken",
    box=candidate.box,
    reasons=("accepted outside",),
  )

  result = await worker._apply_box_retirement(
    client,
    "XAU",
    broken,
  )
  retired = await worker._apply_box_retirement(
    client,
    "XAU",
    candidate,
  )

  assert result.state == "box_broken"
  assert retired.state == "box_retired"
  assert "already retired" in retired.reasons[-1]


@pytest.mark.asyncio
async def test_used_edge_rearms_only_after_midpoint_close():
  client = redis_state.get_client()
  decision = _decision()
  key = worker._box_edge_key(
    "XAU",
    decision.box.box_id,
    "BUY",
  )
  await client.set(key, "1")

  blocked = await worker._apply_box_retirement(
    client,
    "XAU",
    decision,
    price=4017.0,
  )
  rearmed = await worker._apply_box_retirement(
    client,
    "XAU",
    decision,
    price=4022.0,
  )

  assert blocked.state == "edge_disarmed"
  assert rearmed.state == "candidate"
  assert not await client.exists(key)


@pytest.mark.asyncio
async def test_worker_ignores_forming_timeframe_and_scanner_still_ignores_m1(
  monkeypatch,
):
  client = redis_state.get_client()
  monkeypatch.setattr(worker.settings, "auto_trade_symbols", "XAU")
  assert await worker._handle_event(
    "XAU:M5:1784552400",
    client=client,
  ) is None

  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  assert await scanner._handle_event(
    "XAU:M1:1784552400",
    client=client,
  ) == []
  assert await client.xlen("auto_trade:candidates") == 0


@pytest.mark.asyncio
async def test_candidate_fails_closed_on_news_missing_or_stale_spot(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(
    worker,
    "event_in_window",
    AsyncMock(return_value={"title": "US CPI"}),
  )
  decision = _decision()

  assert await worker._publish_candidate(
    client,
    "XAU",
    "1",
    worker.AutoTradeSpot(4016.4, 1, True),
    decision,
  ) is None
  assert await worker._publish_candidate(
    client, "XAU", "2", None, decision
  ) is None
  assert await worker._publish_candidate(
    client,
    "XAU",
    "3",
    worker.AutoTradeSpot(4016.4, 1, False),
    decision,
  ) is None
  assert await client.xlen("auto_trade:test") == 0


@pytest.mark.asyncio
async def test_non_candidate_decision_is_never_published(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  spot = worker.AutoTradeSpot(4100.0, 1, True)

  assert await worker._publish_candidate(
    client, "XAU", "1", spot, AutoScalpDecision("waiting_for_touch")
  ) is None
  assert await client.xlen("auto_trade:test") == 0


@pytest.mark.asyncio
async def test_trend_regime_blocks_box_publish_and_only_trend_path_fires(
  monkeypatch,
):
  """Mutual exclusion: even though evaluate_auto_scalp_gate legitimately
  returns a "candidate" box decision on this bar, the regime router says
  "trend" - so only the trend/breakout publish path may fire, never the
  box path, on the same bar.
  """
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_trend_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_symbols", "XAU")
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  source = AsyncMock()
  source.window = AsyncMock(return_value=_frame())
  monkeypatch.setattr(
    worker,
    "_load_spot",
    AsyncMock(return_value=worker.AutoTradeSpot(4017.2, now, True)),
  )
  monkeypatch.setattr(
    worker, "evaluate_auto_scalp_gate", lambda frames, **kwargs: _decision(),
  )
  monkeypatch.setattr(
    worker, "build_auto_scale_context", lambda *a, **k: _scale_context(now),
  )
  trend_regime = RegimeInfo("trend", "up", 5, 1.3, True, None, ("forced trend",))
  monkeypatch.setattr(
    worker, "classify_regime", lambda frames, decision, cfg: trend_regime,
  )
  trend_decision = TrendDecision(
    "candidate",
    direction="BUY",
    mode="pullback",
    entry_zone=(4016.0, 4016.5),
    key_level=4016.2,
    atr=1.2,
    structure_swing=4010.0,
    target_prices=(4020.0,),
    targets_pips=(38,),
    confluence=2,
    reasons=("forced",),
  )
  monkeypatch.setattr(worker, "evaluate_trend_gate", lambda *a, **k: trend_decision)

  result = await worker._handle_event(
    "XAU:M1:1784552400", source=source, client=client,
  )

  assert result == _decision()
  entries = await client.xrange("auto_trade:test")
  assert len(entries) == 1
  payload = json.loads(entries[0][1]["payload"])
  assert payload["mode"] == "auto_trend_pullback"
  assert payload["setup"] == "Trend Pullback"
  assert payload["regime"] == "trend"
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["regime"] == "trend"


def test_worker_source_has_no_forming_scanner_market_map_or_telegram_import():
  source = inspect.getsource(worker)
  forbidden = (
    "from app.analysis.scanner",
    "from app.analysis.detectors",
    "from app.analysis.market_map",
    "from app.bot.client",
  )
  assert all(item not in source for item in forbidden)

import inspect
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app import auto_scalp_worker, redis_state, scanner
from app.auto_scalp_gate import AutoScalpDecision, AutoScalpRail
from app.auto_scale_context import AutoScaleContext


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
  rail = AutoScalpRail(
    "support",
    4016.5,
    4017.1,
    4016.8,
    3,
    8.0,
    ("M5", "M15"),
    ("M5 swing-low", "M15 range-low"),
  )
  return AutoScalpDecision(
    "candidate",
    direction="BUY",
    trigger="range_rejection",
    rail=rail,
    target_room_pips=42.0,
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
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_stream_maxlen", 100)
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_candidate_ttl", 3600)
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(auto_scalp_worker, "event_in_window", AsyncMock(return_value=None))
  spot = auto_scalp_worker.AutoTradeSpot(4017.2, now, True)

  first = await auto_scalp_worker._publish_candidate(
    client, "XAU", "1784552400", spot, _decision(), _scale_context(now)
  )
  second = await auto_scalp_worker._publish_candidate(
    client, "XAU", "1784552400", spot, _decision(), _scale_context(now)
  )

  assert first is not None
  assert second is None
  entries = await client.xrange("auto_trade:test")
  assert len(entries) == 1
  payload = json.loads(entries[0][1]["payload"])
  assert payload["candidate_id"] == first
  assert payload["setup"] == "Auto Range Scalp"
  assert payload["mode"] == "auto_range_scalp"
  assert payload["timeframe"] == "M1"
  assert payload["direction"] == "BUY"
  assert payload["entry_zone"] == {"low": 4016.5, "high": 4017.1}
  assert payload["spot_ts"] == now
  assert payload["version"] == 2
  assert payload["structure_swing"] == 4014.8
  assert payload["displacement_age_bars"] == 1
  assert payload["bos_direction"] == "up"


@pytest.mark.asyncio
async def test_worker_handles_m1_without_calling_scanner(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_symbols", "XAU")
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(auto_scalp_worker, "event_in_window", AsyncMock(return_value=None))
  source = AsyncMock()
  source.window = AsyncMock(return_value=_frame())
  monkeypatch.setattr(
    auto_scalp_worker,
    "_load_spot",
    AsyncMock(return_value=auto_scalp_worker.AutoTradeSpot(4017.2, now, True)),
  )
  monkeypatch.setattr(
    auto_scalp_worker,
    "evaluate_auto_scalp_gate",
    lambda frames, **kwargs: _decision(),
  )
  forming = AsyncMock()
  monkeypatch.setattr(scanner, "_handle_event", forming)

  result = await auto_scalp_worker._handle_event(
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


@pytest.mark.asyncio
async def test_worker_ignores_forming_timeframe_and_scanner_still_ignores_m1(
  monkeypatch,
):
  client = redis_state.get_client()
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_symbols", "XAU")
  assert await auto_scalp_worker._handle_event(
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
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(
    auto_scalp_worker,
    "event_in_window",
    AsyncMock(return_value={"title": "US CPI"}),
  )
  decision = _decision()

  assert await auto_scalp_worker._publish_candidate(
    client,
    "XAU",
    "1",
    auto_scalp_worker.AutoTradeSpot(4016.4, 1, True),
    decision,
  ) is None
  assert await auto_scalp_worker._publish_candidate(
    client, "XAU", "2", None, decision
  ) is None
  assert await auto_scalp_worker._publish_candidate(
    client,
    "XAU",
    "3",
    auto_scalp_worker.AutoTradeSpot(4016.4, 1, False),
    decision,
  ) is None
  assert await client.xlen("auto_trade:test") == 0


@pytest.mark.asyncio
async def test_non_candidate_decision_is_never_published(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(auto_scalp_worker.settings, "auto_trade_stream", "auto_trade:test")
  spot = auto_scalp_worker.AutoTradeSpot(4100.0, 1, True)

  assert await auto_scalp_worker._publish_candidate(
    client, "XAU", "1", spot, AutoScalpDecision("waiting_for_touch")
  ) is None
  assert await client.xlen("auto_trade:test") == 0


def test_worker_source_has_no_forming_scanner_market_map_or_telegram_import():
  source = inspect.getsource(auto_scalp_worker)
  forbidden = (
    "from app.scanner",
    "from app.detectors",
    "from app.market_map",
    "from app.tg_core",
  )
  assert all(item not in source for item in forbidden)

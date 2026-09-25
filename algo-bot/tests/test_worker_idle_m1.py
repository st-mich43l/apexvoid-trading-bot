"""Idle M1 worker ticks skip leftover analysis when Redis has no match."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from app.autotrade import worker
from app.persistence import redis_state
from tests.configuration.canonical_fixtures import install_runtime_overrides


pytestmark = pytest.mark.no_database


def _frame() -> pd.DataFrame:
  index = pd.date_range("2026-07-20", periods=20, freq="1min", tz="UTC")
  return pd.DataFrame({
    "open": [4016.8] * 20,
    "high": [4017.4] * 20,
    "low": [4016.2] * 20,
    "close": [4017.0] * 20,
    "volume": [100.0] * 20,
  }, index=index)


@pytest.mark.asyncio
async def test_idle_m1_skips_pandas_gates_and_writes_thin_last_gate(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_symbols": "XAU"})
  publish = AsyncMock(return_value=None)
  gate = Mock()
  trend_fn = Mock()
  regime_fn = Mock()
  monkeypatch.setattr(worker, "_publish_trade_plan_v8", publish)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  source = AsyncMock()
  source.window = AsyncMock(return_value=_frame())
  monkeypatch.setattr(
    worker,
    "_load_spot",
    AsyncMock(return_value=worker.AutoTradeSpot(4017.2, now, True)),
  )
  monkeypatch.setattr(worker, "evaluate_auto_scalp_gate", gate)
  monkeypatch.setattr(worker, "evaluate_trend_gate", trend_fn)
  monkeypatch.setattr(worker, "classify_regime", regime_fn)

  result = await worker._handle_event(
    f"XAU:M1:{now}", source=source, client=client,
  )

  assert result is None
  publish.assert_not_awaited()
  gate.assert_not_called()
  trend_fn.assert_not_called()
  regime_fn.assert_not_called()
  source.window.assert_awaited_once()
  assert source.window.await_args.args[1] == "M1"
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["state"] == "idle_no_match"
  assert status["gate_source"] == "idle_no_match"


@pytest.mark.asyncio
async def test_mapped_thesis_rearm_does_not_bool_coerce_m1_frame(monkeypatch):
  """Prod 2026-08-17: frames.get('M1') or frames.get('M1') crashed every minute."""
  client = redis_state.get_client()
  df = _frame()
  called = {}

  async def fake_advance(client_arg, *, symbol, m1, atr):
    called["symbol"] = symbol
    called["rows"] = len(m1)
    called["atr"] = atr

  monkeypatch.setattr(worker, "_advance_mapped_thesis_rearms", fake_advance)
  await worker._advance_mapped_thesis_rearms_from_frames(
    client, symbol="XAU", frames={"M1": df},
  )
  assert called["symbol"] == "XAU"
  assert called["rows"] == 20
  assert called["atr"] > 0

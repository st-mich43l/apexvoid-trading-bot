"""Per-timeframe OHLC lookback contract.

H1 / M15 / M5 each own a structure window sized for their role; M1 stays a
shallow trigger/timing-only fetch.
"""

from __future__ import annotations
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

import pytest

from app.marketdata import ohlc
from app.analysis import scanner
from app.autotrade import worker


pytestmark = pytest.mark.no_database


class _RecordingSource:
  def __init__(self):
    self.calls: dict[str, int] = {}

  async def window(self, symbol, tf, n):
    self.calls[tf] = n
    import pandas as pd
    return pd.DataFrame(
      {"open": [], "high": [], "low": [], "close": [], "volume": []},
    )


def test_default_lookback_settings_fall_inside_the_documented_xau_ranges():
  # H1: major structure ~2.5-4 weeks
  assert 300 <= leaf(runtime_config, "xau_lookback_h1_bars") <= 500
  # M15: session structure ~2-3 days
  assert 200 <= leaf(runtime_config, "xau_lookback_m15_bars") <= 300
  # M5: current + previous session entry structure
  assert 100 <= leaf(runtime_config, "xau_lookback_m5_bars") <= 150
  # M1: trigger/timing only
  assert 100 <= leaf(runtime_config, "xau_lookback_m1_bars") <= 200


def test_window_for_timeframe_resolves_each_timeframe_independently(
  monkeypatch,
):
  install_runtime_overrides(monkeypatch, legacy_overrides={"xau_lookback_h1_bars": 400})
  install_runtime_overrides(monkeypatch, legacy_overrides={"xau_lookback_m15_bars": 250})
  install_runtime_overrides(monkeypatch, legacy_overrides={"xau_lookback_m5_bars": 150})
  install_runtime_overrides(monkeypatch, legacy_overrides={"xau_lookback_m1_bars": 150})

  assert ohlc.window_for_timeframe("H1") == 400
  assert ohlc.window_for_timeframe("M15") == 250
  assert ohlc.window_for_timeframe("M5") == 150
  assert ohlc.window_for_timeframe("M1") == 150
  assert ohlc.window_for_timeframe("h1") == 400  # case-insensitive


def test_window_for_timeframe_clamps_a_misconfigured_low_value(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"xau_lookback_m1_bars": 1})
  assert ohlc.window_for_timeframe("M1") == 50


@pytest.mark.asyncio
async def test_scanner_load_frames_requests_role_sized_windows(
  monkeypatch,
):
  install_runtime_overrides(monkeypatch, legacy_overrides={"xau_lookback_h1_bars": 400})
  install_runtime_overrides(monkeypatch, legacy_overrides={"xau_lookback_m15_bars": 250})
  install_runtime_overrides(monkeypatch, legacy_overrides={"xau_lookback_m5_bars": 150})
  source = _RecordingSource()

  await scanner._load_frames(source, "XAU", "M5", ["H1", "M15"])

  assert source.calls["M5"] == 150
  assert source.calls["H1"] == 400
  assert source.calls["M15"] == 250


@pytest.mark.asyncio
async def test_worker_load_frames_gives_m1_a_trigger_window(
  monkeypatch,
):
  install_runtime_overrides(monkeypatch, legacy_overrides={"xau_lookback_h1_bars": 400})
  install_runtime_overrides(monkeypatch, legacy_overrides={"xau_lookback_m15_bars": 250})
  install_runtime_overrides(monkeypatch, legacy_overrides={"xau_lookback_m5_bars": 150})
  install_runtime_overrides(monkeypatch, legacy_overrides={"xau_lookback_m1_bars": 150})
  source = _RecordingSource()

  await worker._load_frames(source, "XAU")

  assert source.calls[worker.EXECUTION_TIMEFRAME] == 150
  assert source.calls["M5"] == 150
  assert source.calls["H1"] == 400
  assert source.calls["M1"] == 150
  # Structure depth: H1 >> M15 > M5 ≈ M1 (M1 is timing-only).
  assert source.calls["H1"] > source.calls["M15"] > source.calls["M5"]
  assert source.calls["M1"] <= source.calls["M5"]

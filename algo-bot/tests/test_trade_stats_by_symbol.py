"""Tests for per-symbol /trade_stats overview."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.bot import wiring
from app.core.symbols import SYMBOLS
from app.signals.reports import build_stats, build_stats_by_symbol, format_stats
from tests.support.canonical_fixtures import install_runtime_overrides


def _dm(text: str):
  from types import SimpleNamespace

  return SimpleNamespace(
    text=text,
    chat=SimpleNamespace(type="private"),
    from_user=SimpleNamespace(id=42),
  )


@pytest.mark.asyncio
async def test_trade_stats_without_symbol_still_queries_all(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  records = AsyncMock(return_value=[])
  signals = AsyncMock(return_value=[])
  monkeypatch.setattr(wiring, "get_pips_records", records)
  monkeypatch.setattr(wiring, "get_all_signals", signals)

  await wiring.handle_trade_stats(_dm("/trade_stats week"))
  assert records.await_args.args[2] is None
  assert signals.await_args.args == (None,)


def test_format_stats_shows_symbol_overview_when_multiple_symbols():
  rows = [
    {
      "stream": "algo_auto",
      "fill_count": 1,
      "pips": 20,
      "sign": "+",
      "value": 20,
      "symbol": "XAU",
      "trade_key": "a1",
      "setup_type": "key-level",
      "signal_ts": 1,
    },
    {
      "stream": "algo_auto",
      "fill_count": 1,
      "pips": 14,
      "sign": "+",
      "value": 14,
      "symbol": "EURUSD",
      "trade_key": "a2",
      "setup_type": "key-level",
      "signal_ts": 2,
    },
  ]
  by_symbol = build_stats_by_symbol(rows, [], "UTC", 0, 8, 13)
  combined = build_stats(rows, [], "UTC", 0, 8, 13)
  rendered = format_stats(combined, "week", stats_by_symbol=by_symbol)

  assert "By symbol" in rendered
  assert "XAU" in rendered
  assert "EURUSD" in rendered

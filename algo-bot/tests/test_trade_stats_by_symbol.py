"""Tests for per-symbol /trade_stats overview."""

from __future__ import annotations

from app.core.symbols import SYMBOLS
from app.signals.reports import build_stats, build_stats_by_symbol, format_stats


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

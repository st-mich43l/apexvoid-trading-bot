"""/trade_stats per-stream book dedup: a manual /algo signal that gets
broker-executed produces two rows sharing one trade_key - one "manual"
(pips_log, written when the owner closes it in chat) and one
"algo_manual" (the broker's own authoritative fill/close). Without
exclusion, CHART / SIGNAL and ALGO MANUAL would each show that trade's
pips independently, double-counting it. COMBINED UNIQUE already collapsed
these correctly via _unique_trade_rows; the per-stream books did not.
"""

from __future__ import annotations

import pytest

from app.signals.reports import build_stats

pytestmark = pytest.mark.no_database


def _row(trade_key: str, stream: str, signed_pips: int, ts: int) -> dict:
  # build_stats reads pips as an unsigned magnitude + a separate sign field
  # (matching pips_log's own storage shape), then recomputes value = +/-pips.
  return {
    "stream": stream,
    "fill_count": 1,
    "pips": abs(signed_pips),
    "sign": "+" if signed_pips >= 0 else "-",
    "symbol": "XAU",
    "trade_key": trade_key,
    "setup_type": "key-level",
    "signal_ts": ts,
    "ts": ts,
  }


def test_broker_executed_manual_signal_counts_once_not_twice():
  rows = [
    # Same trade_key, both streams - the duplicated case this fix targets.
    _row("manual:1", "manual", 30, 1),
    _row("manual:1", "algo_manual", 30, 1),
    # A purely discretionary manual call, never auto-executed - must stay
    # in CHART / SIGNAL untouched.
    _row("manual:2", "manual", 20, 2),
    # An unrelated pure algo_manual trade.
    _row("manual:3", "algo_manual", -15, 3),
  ]
  stats = build_stats(rows, [], "UTC", 0, 8, 13)

  chart_signal = stats["by_stream"]["manual"]
  algo_manual = stats["by_stream"]["algo_manual"]
  combined_unique = stats["by_stream"]["all_unique"]

  # The duplicated trade (manual:1) must appear only in ALGO MANUAL.
  assert chart_signal["trades"] == 1
  assert chart_signal["total_pips"] == 20
  assert algo_manual["trades"] == 2
  assert algo_manual["total_pips"] == 15

  # COMBINED UNIQUE was already correct before this fix - unaffected.
  assert combined_unique["trades"] == 3
  assert combined_unique["total_pips"] == 35


def test_no_algo_manual_counterpart_leaves_chart_signal_unchanged():
  rows = [
    _row("manual:10", "manual", 40, 1),
    _row("manual:11", "manual", -10, 2),
  ]
  stats = build_stats(rows, [], "UTC", 0, 8, 13)

  chart_signal = stats["by_stream"]["manual"]
  assert chart_signal["trades"] == 2
  assert chart_signal["total_pips"] == 30

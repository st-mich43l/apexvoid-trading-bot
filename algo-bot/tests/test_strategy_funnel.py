"""/strategy_funnel: per-strategy setups -> trades -> closed -> net R.

Run from ``algo-bot``:  ``python -m unittest tests.test_strategy_funnel``  (stdlib unittest; needs the app's runtime dependencies).
"""
from __future__ import annotations

import asyncio
import os
import unittest
from contextlib import asynccontextmanager
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.autotrade import strategy_funnel as sf  # noqa: E402
from app.persistence import store  # noqa: E402


TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 11, 25, 20, 0, tzinfo=TZ)  # a Wednesday


def ts(*args) -> int:
  return int(datetime(*args, tzinfo=TZ).timestamp())


def row(strategy="key_level", published=100, trades=4, not_tradable=3, closed=3, wins=2, net_r=1.25):
  return sf.StrategyFunnelRow(strategy, published, trades, not_tradable, closed, wins, net_r)


class ParseArgsTest(unittest.TestCase):
  def test_default_is_this_week(self):
    self.assertEqual(sf.parse_args(""), ("week", ""))

  def test_calendar_periods_and_symbol_kept(self):
    self.assertEqual(sf.parse_args("XAU month"), ("month", "XAU"))
    self.assertEqual(sf.parse_args("last week EURUSD"), ("last week", "EURUSD"))
    self.assertEqual(sf.parse_args("xau Last Month"), ("last month", "xau"))
    self.assertEqual(sf.parse_args("this week"), ("week", ""))

  def test_rolling_days(self):
    self.assertEqual(sf.parse_args("XAU 14"), (14, "XAU"))
    self.assertEqual(sf.parse_args("30d"), (30, ""))

  def test_out_of_range_is_refused(self):
    self.assertIsNone(sf.parse_args("0"))
    self.assertIsNone(sf.parse_args(str(sf.MAX_DAYS + 1)))


class WindowTest(unittest.TestCase):
  def test_this_week_starts_monday_local_and_compares_with_last_week(self):
    w = sf.period_window("week", NOW)
    self.assertEqual((w.start, w.end), (ts(2026, 11, 23), int(NOW.timestamp())))
    self.assertEqual(w.label, "this week (23 Nov – 25 Nov)")
    self.assertEqual((w.previous.start, w.previous.end), (ts(2026, 11, 16), ts(2026, 11, 23)))
    self.assertEqual(w.previous.label, "week of 16 Nov")

  def test_last_week_is_the_full_previous_calendar_week(self):
    w = sf.period_window("last week", NOW)
    self.assertEqual((w.start, w.end), (ts(2026, 11, 16), ts(2026, 11, 23)))
    self.assertEqual(w.label, "last week (16 Nov – 22 Nov)")
    self.assertEqual(w.previous.start, ts(2026, 11, 9))

  def test_months(self):
    w = sf.period_window("month", NOW)
    self.assertEqual((w.start, w.previous.start, w.previous.end), (ts(2026, 11, 1), ts(2026, 10, 1), ts(2026, 11, 1)))
    self.assertEqual((w.label, w.previous.label), ("this month (1 Nov – 25 Nov)", "October"))
    w = sf.period_window("last month", NOW)
    self.assertEqual((w.start, w.end), (ts(2026, 10, 1), ts(2026, 11, 1)))
    self.assertEqual((w.label, w.previous.label), ("last month (1 Oct – 31 Oct)", "September"))

  def test_january_rolls_back_to_december(self):
    w = sf.period_window("last month", datetime(2027, 1, 5, 9, 0, tzinfo=TZ))
    self.assertEqual((w.start, w.end), (ts(2026, 12, 1), ts(2027, 1, 1)))
    self.assertEqual(w.previous.label, "November")

  def test_rolling_days(self):
    w = sf.period_window(7, NOW)
    self.assertEqual(w.end - w.start, 7 * 86400)
    self.assertEqual((w.label, w.previous.end), ("last 7 days", w.start))


class FormatTest(unittest.TestCase):
  def test_rows_sorted_by_published_with_totals(self):
    text = sf.format_strategy_funnel(
      [row("fvg", published=50, trades=5, not_tradable=0, closed=5, wins=1, net_r=-2.0), row()],
      sf.period_window("week", NOW), "XAU",
    )
    self.assertIn("XAU · this week (23 Nov – 25 Nov)", text)
    self.assertLess(text.index("Key Level"), text.index("FVG"))
    self.assertIn("Published <b>150</b> → trades <b>9</b> (6.0%) → closed <b>8</b>", text)
    self.assertIn("Net <b>-0.75 R</b> · win rate 38%", text)
    # 150 published - 9 trades - 3 not tradable
    self.assertIn("No trade: 138 refused by rules or expired · 3 not tradable", text)

  def test_no_closed_trades_shows_dashes_not_zero_r(self):
    text = sf.format_strategy_funnel([row("crt", published=20, trades=0, not_tradable=0, closed=0, wins=0, net_r=0.0)], sf.period_window(7, NOW), None)
    line = next(l for l in text.splitlines() if l.startswith("CRT"))
    self.assertEqual(line.split()[-2:], ["-", "-"])

  def test_ampersand_label_keeps_column_alignment(self):
    text = sf.format_strategy_funnel([row("break_retest"), row("key_level")], sf.period_window(7, NOW), None)
    kl = next(l for l in text.splitlines() if l.startswith("Key Level"))
    br = next(l for l in text.splitlines() if l.startswith("Break&amp;Ret"))
    self.assertEqual(len(br.replace("&amp;", "&")), len(kl))

  def test_empty_window(self):
    self.assertIn("No setups were published", sf.format_strategy_funnel([], sf.period_window(3, NOW), None))

  def test_previous_period_totals_line(self):
    w = sf.period_window("month", NOW)
    text = sf.format_strategy_funnel([row()], w, None, [row(published=200, trades=10, net_r=-1.5)])
    self.assertIn("October: published 200 → trades 10 (5.0%) · net -1.50 R", text)
    self.assertIn("October: no setups", sf.format_strategy_funnel([row()], w, None, []))

  def test_unknown_strategy_gets_a_readable_label(self):
    self.assertEqual(sf._label("new_thing"), "New Thing")


class FetchTest(unittest.TestCase):
  def test_passes_window_start_and_symbol(self):
    calls = []

    class FakeDb:
      async def fetch(self, sql, *params):
        calls.append(params)
        return [{"strategy": "key_level", "published": 10, "trades": 1, "not_tradable": 2, "closed": 1, "wins": 1, "net_r": 0.5}]

    @asynccontextmanager
    async def fake_connect():
      yield FakeDb()

    with mock.patch.object(store, "_connect", fake_connect):
      rows = asyncio.run(sf.fetch_strategy_funnel(100, 200, "xau"))
    self.assertEqual(calls, [(100, "XAU", 200)])
    self.assertEqual(rows, [row(published=10, trades=1, not_tradable=2, closed=1, wins=1, net_r=0.5)])
    self.assertEqual(rows[0].no_trade, 7)


if __name__ == "__main__":
  unittest.main()

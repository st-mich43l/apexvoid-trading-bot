"""/strategy_funnel: per-strategy setups -> trades -> closed -> net R.

Run from ``algo-bot``:  ``python -m unittest tests.test_strategy_funnel``  (stdlib unittest; needs the app's runtime dependencies).
"""
from __future__ import annotations

import asyncio
import os
import unittest
from contextlib import asynccontextmanager
from unittest import mock

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.autotrade import strategy_funnel as sf  # noqa: E402
from app.persistence import store  # noqa: E402


def row(strategy="key_level", published=100, trades=4, not_tradable=3, closed=3, wins=2, net_r=1.25):
  return sf.StrategyFunnelRow(strategy, published, trades, not_tradable, closed, wins, net_r)


class ParseArgsTest(unittest.TestCase):
  def test_default_seven_days(self):
    self.assertEqual(sf.parse_args(""), (7, ""))

  def test_days_with_or_without_suffix_and_symbol_kept(self):
    self.assertEqual(sf.parse_args("XAU 14"), (14, "XAU"))
    self.assertEqual(sf.parse_args("30d eurusd"), (30, "eurusd"))

  def test_out_of_range_is_refused(self):
    self.assertIsNone(sf.parse_args("0"))
    self.assertIsNone(sf.parse_args(str(sf.MAX_DAYS + 1)))


class FormatTest(unittest.TestCase):
  def test_rows_sorted_by_published_with_totals(self):
    text = sf.format_strategy_funnel(
      [row("fvg", published=50, trades=5, not_tradable=0, closed=5, wins=1, net_r=-2.0), row()],
      7, "XAU",
    )
    self.assertIn("XAU · last 7 days", text)
    self.assertLess(text.index("Key Level"), text.index("FVG"))
    self.assertIn("Published <b>150</b> → trades <b>9</b> (6.0%) → closed <b>8</b>", text)
    self.assertIn("Net <b>-0.75 R</b> · win rate 38%", text)
    # 150 published - 9 trades - 3 not tradable
    self.assertIn("No trade: 138 refused by rules or expired · 3 not tradable", text)

  def test_no_closed_trades_shows_dashes_not_zero_r(self):
    text = sf.format_strategy_funnel([row("crt", published=20, trades=0, not_tradable=0, closed=0, wins=0, net_r=0.0)], 7, None)
    line = next(l for l in text.splitlines() if l.startswith("CRT"))
    self.assertEqual(line.split()[-2:], ["-", "-"])

  def test_ampersand_label_keeps_column_alignment(self):
    text = sf.format_strategy_funnel([row("break_retest"), row("key_level")], 7, None)
    kl = next(l for l in text.splitlines() if l.startswith("Key Level"))
    br = next(l for l in text.splitlines() if l.startswith("Break&amp;Ret"))
    self.assertEqual(len(br.replace("&amp;", "&")), len(kl))

  def test_empty_window(self):
    self.assertIn("No setups were published", sf.format_strategy_funnel([], 3, None))

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
      rows = asyncio.run(sf.fetch_strategy_funnel(2, "xau", now=1_000_000))
    self.assertEqual(calls, [(1_000_000 - 2 * 86400, "XAU")])
    self.assertEqual(rows, [row(published=10, trades=1, not_tradable=2, closed=1, wins=1, net_r=0.5)])
    self.assertEqual(rows[0].no_trade, 7)


if __name__ == "__main__":
  unittest.main()

"""The root card shows the Structure / Order zone lines for the zone family only.

Run from ``algo-bot``:  ``python -m unittest tests.test_root_card_structure_lines``.
Every strategy now publishes a structure block (Key Level since KL11, Trendline, Confluence Zone, ...), which made these lines appear on setups whose structure is a
reaction band or a line, not a zone to read. Display only: nothing here touches a price, a stop or a volume.
"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.autotrade.setup_card import STRUCTURE_LINE_STRATEGIES, structure_lines  # noqa: E402


def match(strategy: str, direction: str = "BUY"):
  return SimpleNamespace(
    strategy=strategy, direction=direction, structure_timeframe="M15", structure_low=4084.63, structure_high=4092.34, structure_formed_at=1794211200,
  )


class StructureLineTests(unittest.TestCase):
  def test_the_zone_family_keeps_the_lines_and_the_order_zone_when_it_differs(self):
    for strategy in ("Supply Demand", "Order Block", "FVG"):
      with self.subTest(strategy=strategy):
        lines = structure_lines(match(strategy), symbol="XAU", execution_zone=(4084.63, 4089.15))
        self.assertTrue(lines[0].startswith("Structure: "), lines)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].startswith("Order zone: "), lines)
    one = structure_lines(match("FVG"), symbol="XAU", execution_zone=(4084.63, 4092.34))  # the order trades the whole structure
    self.assertEqual(len(one), 1)

  def test_every_other_strategy_shows_neither_line(self):
    others = ("Key Level", "Trendline", "Confluence Zone", "Flip Zone", "iFVG", "Break & Retest", "CRT", "Round Number", "Asia Range", "Shock Retest",
              "Range Box Scalp", "Impulse Pullback Scalp", "")
    for strategy in others:
      with self.subTest(strategy=strategy):
        self.assertEqual(structure_lines(match(strategy), symbol="XAU", execution_zone=(4084.63, 4089.15)), [])
    self.assertEqual(STRUCTURE_LINE_STRATEGIES, frozenset({"Supply Demand", "Order Block", "FVG"}))

  def test_a_zone_family_event_without_the_block_still_renders_nothing(self):
    bare = match("FVG")
    bare.structure_low = None
    self.assertEqual(structure_lines(bare, symbol="XAU", execution_zone=None), [])


if __name__ == "__main__":
  unittest.main()

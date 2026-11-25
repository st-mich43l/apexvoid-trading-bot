"""Deterministic layout fixtures for the Price Action v2.27 annotation rail (offline reference model, not TradingView).

Run from the repository root:  python3 -m unittest discover -s tradingview/tests
"""

from __future__ import annotations

import random
import unittest

from annotation_rail_ref import LIQUIDITY, Ann, layout, row_text, text_color

BEAR = "#F26B85"
BULL = "#00C7AE"
ASIA = "#B48AFF"
FVG = "#5194FF"
ROW = 1.0


def kl(y: float, touches: int = 2, in_play: bool = False, tier: int = 3) -> Ann:
  return Ann(y, "KL", f"×{touches}" + (" ●" if in_play else ""), f"{y:.2f}", "key", 30, 4, 2 if in_play else tier)


def vp(name: str, y: float) -> Ann:
  return Ann(y, name, "", f"{y:.2f}", "vp", 20 if name == "POC" else 19, 5, 2)


def prot(y: float, tone: str = BEAR) -> Ann:
  return Ann(y, "PROT", "", "", tone, 80, 0, 1)


def eq(y: float) -> Ann:
  return Ann(y, "EQ", "", f"{y:.2f}", LIQUIDITY, 35, 0, 1)


def asia(y: float, name: str = "Asia H") -> Ann:
  return Ann(y, name, "", "", ASIA, 50, 2, 3)


def legacy_clusters(anns: list[Ann], gap: float) -> list[list[float]]:
  """v2.26 rule: a new cluster only when the gap to the previous price is at least `gap` (single linkage)."""
  ys = sorted((a.y for a in anns), reverse=True)
  out: list[list[float]] = [[ys[0]]]
  for y in ys[1:]:
    if out[-1][-1] - y >= gap:
      out.append([y])
    else:
      out[-1].append(y)
  return out


class Invariants:
  def check(self, rail, anns, row_h=ROW):
    shown = sorted(rail.shown, key=lambda it: -it.target)
    for a, b in zip(shown, shown[1:]):
      # price order kept and no overlap (separation = half heights + gap)
      self.assertGreaterEqual(a.y - b.y, (a.rows + b.rows) * row_h / 2.0 + 0.15 * row_h - 1e-9)
    for it in rail.items:
      span = anns[it.members[0]].y - anns[it.members[-1]].y
      self.assertLessEqual(span, 2.0 * row_h + 1e-9)
      self.assertLessEqual(len(it.members), 4)
      ys = [anns[i].y for i in it.members]
      self.assertEqual(ys, sorted(ys, reverse=True))
    # every priced annotation (key level, POC/VAH/VAL, EQ) stays visible with its own price text
    shown_text = "\n".join(it.body for it in rail.shown)
    for a in anns:
      if a.px:
        self.assertIn(a.px, shown_text)


class RailTest(unittest.TestCase, Invariants):
  def test_1_single_annotation_sits_on_its_price(self):
    anns = [kl(3548.04, 3)]
    rail = layout(anns, ROW)
    self.assertEqual(len(rail.shown), 1)
    it = rail.shown[0]
    self.assertEqual(it.body, "KL ×3    3548.04")
    self.assertAlmostEqual(it.y, 3548.04)
    self.assertFalse(it.link)

  def test_2_unrelated_categories_at_one_price_are_separate_and_keep_their_colour(self):
    anns = [prot(100.0), kl(100.05), vp("POC", 100.02), asia(99.98)]
    rail = layout(anns, ROW)
    self.check(rail, anns)
    bodies = [it.body for it in sorted(rail.shown, key=lambda it: -it.y)]
    self.assertEqual(bodies, ["KL ×2    100.05\nPOC      100.02", "PROT", "Asia H"])
    p = next(it for it in rail.shown if it.body == "PROT")
    self.assertEqual(text_color(anns, p), BEAR)  # never neutralised by its neighbours
    self.assertEqual(text_color(anns, rail.shown[0]), "#CBD5E1")
    # the tier-1 PROT moves least
    moves = {it.body: abs(it.y - it.target) for it in rail.shown}
    self.assertLess(moves["PROT"], moves["Asia H"])

  def test_3_key_levels_near_prot_form_one_neutral_column_in_descending_order(self):
    anns = [prot(3566.40), kl(3565.83, 2), vp("VAH", 3565.10), kl(3564.42, 3)]
    rail = layout(anns, 1.2)
    self.check(rail, anns, 1.2)
    neutral = next(it for it in rail.shown if it.body.startswith("KL"))
    self.assertEqual(neutral.body.split("\n"), ["KL ×2    3565.83", "VAH      3565.10", "KL ×3    3564.42"])
    self.assertNotIn("PROT", neutral.body)

  def test_4_volume_profile_and_eq_group_without_renaming_eq(self):
    anns = [eq(3551.45), vp("POC", 3550.90), vp("VAL", 3550.20), vp("VAH", 3552.00)]
    rail = layout(anns, 1.0)
    self.check(rail, anns)
    self.assertEqual(len(rail.shown), 1)
    self.assertEqual(rail.shown[0].body.split("\n"), ["VAH      3552.00", "EQ       3551.45", "POC      3550.90", "VAL      3550.20"])

  def test_4b_separated_volume_levels_keep_separate_labels_on_their_prices(self):
    anns = [vp("VAH", 3560.0), vp("POC", 3550.0), vp("VAL", 3540.0)]
    rail = layout(anns, 1.0)
    self.assertEqual(len(rail.shown), 3)
    for it in rail.shown:
      self.assertAlmostEqual(it.y, it.target)

  def test_5_dense_zones_cap_rows_and_stay_ordered(self):
    anns = [Ann(100.0 - 0.3 * k, "FVG", "· T" if k % 2 else "", "", FVG, 45, 3, 2) for k in range(7)]
    rail = layout(anns, ROW)
    self.check(rail, anns)
    self.assertTrue(all(len(it.members) <= 4 for it in rail.items))
    self.assertEqual(sum(len(it.members) for it in rail.shown), 7)

  def test_6_chain_of_neighbours_does_not_become_one_cluster(self):
    anns = [kl(10.0 - 0.8 * k) for k in range(6)]
    self.assertEqual(len(legacy_clusters(anns, ROW)), 1)  # v2.26 chained all six
    rail = layout(anns, ROW)
    self.check(rail, anns)
    self.assertEqual([len(it.members) for it in rail.items], [3, 3])

  def test_7_competing_multiline_items_are_separated_by_their_height(self):
    anns = [kl(100.0), kl(99.5), kl(99.0), kl(98.6), Ann(98.4, "BSL", "", "", BULL, 60, 1, 2), kl(98.1), kl(97.6)]
    rail = layout(anns, ROW)
    self.check(rail, anns)
    self.assertGreaterEqual(len(rail.shown), 3)

  def test_8_text_scale_changes_row_height_and_spacing(self):
    anns = [prot(100.0), kl(99.6), asia(99.2)]
    small = layout(anns, 0.85)
    large = layout(anns, 1.25)
    self.check(small, anns, 0.85)
    self.check(large, anns, 1.25)
    spread = lambda r: max(it.y for it in r.shown) - min(it.y for it in r.shown)
    self.assertGreater(spread(large), spread(small))

  def test_9_budget_hides_only_low_priority_unpriced_items_and_names_them(self):
    anns = [asia(200.0 - 5 * k, f"S{k}") for k in range(30)] + [kl(30.0), vp("POC", 20.0), eq(10.0), prot(5.0)]
    rail = layout(anns, ROW)
    self.check(rail, anns)
    self.assertEqual(len(rail.shown), 20)
    hidden = [n for it in rail.shown for n in it.hidden]
    self.assertEqual(len(hidden), 14)
    for it in rail.items:
      if not it.alive:
        self.assertTrue(it.hideable)

  def test_9b_compact_budget_is_fourteen(self):
    anns = [asia(200.0 - 5 * k, f"S{k}") for k in range(30)]
    self.assertEqual(len(layout(anns, ROW, compact=True).shown), 14)

  def test_10_compact_keeps_every_price_and_counts_the_rest(self):
    anns = [Ann(100.4, "BSL", "", "", LIQUIDITY, 60, 1, 1), kl(100.1), Ann(99.9, "iBSL", "", "", LIQUIDITY, 40, 1, 3), kl(99.7)]
    smart = layout(anns, ROW)
    compact = layout(anns, ROW, compact=True)
    self.assertEqual(smart.shown[0].body.split("\n"), ["BSL", "KL ×2    100.10", "iBSL", "KL ×2    99.70"])
    self.assertEqual(compact.shown[0].body, "BSL\nKL ×2    100.10\nKL ×2    99.70 +1")
    self.check(compact, anns)

  def test_11_far_pushed_context_item_is_hidden_not_the_decision_levels(self):
    # eight decision levels 0.1 apart, a context level just below them: the stack is centred on the heavy tier-1 items, so the
    # context label would land more than 3 rows from its price and is hidden instead (named in a neighbour's tooltip)
    anns = [prot(100.0 - 0.1 * k, BEAR if k % 2 else BULL) for k in range(8)]
    anns.append(asia(99.25))
    rail = layout(anns, ROW)
    asia_item = next(it for it in rail.items if anns[it.members[0]].name == "Asia H")
    self.assertFalse(asia_item.alive)
    self.assertTrue(any("Asia H 99.25" in n for it in rail.shown for n in it.hidden))
    self.assertEqual(sum(1 for it in rail.shown if anns[it.lead].name == "PROT"), 8)
    self.check(rail, anns)

  def test_12_connectors_only_when_displaced_and_bounded(self):
    anns = [prot(100.0 - 0.05 * k, BEAR if k % 2 else BULL) for k in range(14)]
    rail = layout(anns, ROW)
    self.assertLessEqual(sum(it.link for it in rail.shown), 8)
    lone = layout([kl(50.0)], ROW)
    self.assertFalse(lone.shown[0].link)

  def test_13_randomised_invariants_and_determinism(self):
    rng = random.Random(2027)
    tones = [BEAR, BULL, ASIA, FVG, LIQUIDITY]
    for _ in range(300):
      anns = []
      for _ in range(rng.randint(1, 40)):
        kind = rng.random()
        y = round(rng.uniform(90, 110), 2)
        if kind < 0.3:
          anns.append(kl(y, rng.randint(2, 5), rng.random() < 0.3))
        elif kind < 0.4:
          anns.append(vp(rng.choice(["POC", "VAH", "VAL"]), y))
        else:
          anns.append(Ann(y, "X", "", "", rng.choice(tones), rng.randint(10, 80), rng.choice([0, 1, 2, 3]), rng.randint(1, 3)))
      row = rng.choice([0.85, 1.0, 1.25]) * rng.uniform(0.2, 1.5)
      compact = rng.random() < 0.5
      a = layout(anns, row, compact)
      b = layout(anns, row, compact)
      self.assertEqual([(it.y, it.body, it.alive) for it in a.items], [(it.y, it.body, it.alive) for it in b.items])
      self.check(a, anns, row)
      self.assertLessEqual(sum(it.link for it in a.shown), 8)
      self.assertLessEqual(len(a.shown), max(14 if compact else 20, sum(1 for it in a.items if not it.hideable)))
      for it in a.items:  # hidden items are only ever tier-3 and unpriced
        if not it.alive:
          self.assertTrue(it.hideable)

  def test_row_format(self):
    self.assertEqual(row_text(kl(3548.04, 3)), "KL ×3    3548.04")
    self.assertEqual(row_text(kl(3548.04, 12, True)), "KL ×12 ● 3548.04")
    self.assertEqual(row_text(vp("POC", 3549.67)), "POC      3549.67")
    self.assertEqual(row_text(Ann(1.0, "FVG", "· T", "", FVG)), "FVG · T")


if __name__ == "__main__":
  unittest.main()

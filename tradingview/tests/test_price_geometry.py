"""Fixtures for the geometry and lifecycle of ApexVoid | Price Geometry v1.0, run against the offline reference model (price_geometry_ref.py).

Run from the repository root:  python -m unittest discover -s tradingview/tests
These check the mathematics of the rules. They do not compile or run the Pine source: only TradingView can.

Bars are built from waypoints (bar, price): straight legs of small candles (wick 0.3), so every waypoint is a pivot and ATR(14) is about 1.1-1.3.
"""
from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from price_geometry_ref import Geometry, Params, Swing, wilder_atr  # noqa: E402


def bars_from(waypoints, wick=0.3):
  out, prev = [], waypoints[0][1]
  for (b0, p0), (b1, p1) in zip(waypoints, waypoints[1:]):
    for i in range(b0 + 1, b1 + 1):
      c = p0 + (p1 - p0) * (i - b0) / (b1 - b0)
      out.append((prev, max(prev, c) + wick, min(prev, c) - wick, c))
      prev = c
  return out


def mirror(bars, pivot=200.0):
  return [(pivot - o, pivot - l, pivot - h, pivot - c) for o, h, l, c in bars]


UPTREND = [(0, 118), (20, 100), (40, 115), (60, 105), (80, 120), (100, 110), (115, 118)]


def run(waypoints, params=None, bars=None, until=None):
  return Geometry(bars if bars is not None else bars_from(waypoints), params).run(until)


class TrendlineTests(unittest.TestCase):
  def test_01_valid_ascending_support_with_two_anchors(self):
    g = run(UPTREND, until=80)
    self.assertIsNotNone(g.support)
    s = g.support
    # The two confirmed swing lows (bar index = waypoint - 1: the first candle is waypoint 1), published when the second pivot was confirmed (pivot_len 6 later).
    self.assertEqual((s.b1, s.b2, s.published, s.touches), (19, 59, 65, 2))
    self.assertGreater(s.slope, 0)

  def test_02_valid_descending_resistance_with_two_anchors(self):
    g = Geometry(mirror(bars_from(UPTREND))).run(80)
    r = g.resistance
    self.assertIsNotNone(r)
    self.assertEqual((r.b1, r.b2, r.published), (19, 59, 65))
    self.assertLess(r.slope, 0)
    self.assertIsNone(g.support)

  def test_03_the_line_reproduces_both_anchors_exactly(self):
    s = run(UPTREND).published[0]
    self.assertEqual(s.at(s.b1), s.p1)
    self.assertAlmostEqual(s.at(s.b2), s.p2, places=12)
    self.assertAlmostEqual(s.slope, (s.p2 - s.p1) / (s.b2 - s.b1), places=15)

  def test_04_a_third_independent_touch_is_counted_after_publication(self):
    g = run(UPTREND)
    s = g.support
    self.assertEqual(s.touches, 3)
    self.assertEqual(s.touch_bars, [99])  # the 110 low lies exactly on the line (99.7 + 0.125 x 80)

  def test_05_consecutive_near_line_candles_are_one_reaction(self):
    # The third low is a shelf riding the line for 6 bars (every low within 0.2 ATR of it): one pivot, one touch.
    wps = [(0, 118), (20, 100), (40, 115), (60, 105), (80, 120), (99, 109.9), (105, 110.65), (120, 118)]
    g = run(wps)
    self.assertEqual(g.support.touches, 3)
    self.assertEqual(len(g.support.touch_bars), 1)

  def test_06_an_insignificant_swing_is_not_an_anchor(self):
    # A dip of 0.8 (under 1.5 ATR) between the first low and the high is no swing: the low list stays alternating and significant.
    wps = [(0, 118), (20, 100), (30, 108), (35, 107.2), (45, 115), (65, 105), (85, 120), (100, 113)]
    g = run(wps)
    lows = [s.price for s in g.swings if not s.high]
    self.assertNotIn(107.2 - 0.3, lows)
    self.assertEqual(g.published[0].b1, 19)

  def test_07_anchors_too_close_together_are_rejected(self):
    g = Geometry(bars_from(UPTREND), Params(min_sep=45)).run(90)
    self.assertIsNone(g.support)

  def test_08_an_effectively_flat_line_is_rejected(self):
    # Second low 0.3 above the first: rise 0.3 < 0.5 ATR.
    wps = [(0, 118), (20, 100), (40, 115), (60, 100.3), (80, 116), (95, 110)]
    self.assertEqual(run(wps).published, [])

  def test_09_material_historical_penetration_rejects_a_candidate(self):
    # Between the anchors price closes 2 points below the line (about 1.5 ATR): material, so the 20-60 pair is rejected.
    wps = [(0, 118), (20, 100), (30, 110), (38, 99.0), (40, 99.0), (48, 112), (60, 105), (80, 120), (95, 113)]
    g = run(wps)
    for line in g.published:
      self.assertNotEqual((line.b1, line.b2), (19, 59))

  def test_10_a_minor_wick_penetration_is_tolerated(self):
    bars = bars_from(UPTREND)
    o, h, l, c = bars[62]
    line_at = 99.7 + 0.125 * (62 - 19)
    bars[62] = (o, h, line_at - 0.35, c)  # between the second anchor and publication: a wick 0.35 below the line (under 0.5 ATR), the close above
    g = Geometry(bars).run(80)
    self.assertIsNotNone(g.support)
    self.assertEqual((g.support.b1, g.support.b2), (19, 59))

  def test_11_a_confirmed_close_beyond_breaks_the_line(self):
    wps = UPTREND[:-2] + [(100, 100), (110, 95)]
    g = run(wps)
    broken = [x for x in g.history if (x.b1, x.b2) == (19, 59)]
    self.assertEqual(len(broken), 1)
    line = broken[0]
    self.assertEqual(line.state, "broken")
    k = line.ended
    a = g.atr[k]
    self.assertLess(g.c[k], line.at(k) - 0.15 * a)
    self.assertGreaterEqual(g.c[k - 1], line.at(k - 1) - 0.15 * g.atr[k - 1])  # the first close beyond, not later

  def test_12_two_closes_option_needs_two_consecutive_closes(self):
    wps = UPTREND[:-2] + [(100, 100), (110, 95)]
    one = [x for x in run(wps).history if (x.b1, x.b2) == (19, 59)][0]
    two = [x for x in run(wps, Params(break_closes=2)).history if (x.b1, x.b2) == (19, 59)][0]
    self.assertEqual(two.ended, one.ended + 1)

  def test_13_an_active_line_is_not_replaced_by_every_new_pivot(self):
    # After the third touch the line has 3 touches; the later lows give 2-touch candidates, which never replace it.
    wps = UPTREND[:-1] + [(120, 125), (140, 116), (160, 128)]
    g = run(wps)
    self.assertEqual((g.support.b1, g.support.b2), (19, 59))
    self.assertEqual(len([x for x in g.published if x.support]), 1)

  def test_14_a_valid_replacement_follows_a_break(self):
    wps = UPTREND[:-2] + [(100, 100), (110, 95), (130, 110), (150, 100), (170, 114), (180, 108)]
    g = run(wps)
    self.assertTrue(any((x.b1, x.b2) == (19, 59) and x.state == "broken" for x in g.history))
    self.assertIsNotNone(g.support)
    self.assertGreater(g.support.b1, 59)
    self.assertGreater(g.support.published, [x for x in g.history if (x.b1, x.b2) == (19, 59)][0].ended)

  def test_15_a_candidate_already_broken_before_publication_is_rejected(self):
    # Price closes below the 20-60 line before the second low is even confirmed (a sharp drop right after it).
    wps = [(0, 118), (20, 100), (40, 115), (60, 105), (64, 102), (66, 101), (80, 117), (95, 110)]
    for line in run(wps).published:
      self.assertNotEqual((line.b1, line.b2), (19, 59))


class ChannelTests(unittest.TestCase):
  def test_01_valid_ascending_channel(self):
    g = run(UPTREND, until=80)
    ch = g.channel
    self.assertIsNotNone(ch)
    self.assertIs(ch.primary, g.support)
    self.assertEqual((ch.o_bar, ch.published), (39, 65))
    self.assertGreater(ch.offset, 0)

  def test_02_valid_descending_channel(self):
    g = Geometry(mirror(bars_from(UPTREND))).run(80)
    self.assertIsNotNone(g.channel)
    self.assertIs(g.channel.primary, g.resistance)
    self.assertLess(g.channel.offset, 0)

  def test_03_both_boundaries_have_the_same_slope_and_constant_width(self):
    ch = run(UPTREND, until=80).channel
    for t in (39, 65, 79, 200):
      self.assertAlmostEqual(ch.boundary(t) - ch.primary.at(t), ch.offset, places=12)
    self.assertAlmostEqual(ch.boundary(101) - ch.boundary(100), ch.primary.slope, places=12)

  def test_04_the_opposing_boundary_passes_through_the_opposing_reaction(self):
    ch = run(UPTREND, until=80).channel
    self.assertAlmostEqual(ch.boundary(ch.o_bar), ch.o_price, places=12)

  def test_05_the_midpoint_is_half_the_offset(self):
    ch = run(UPTREND, until=80).channel
    self.assertAlmostEqual(ch.midpoint(70), ch.primary.at(70) + ch.offset / 2, places=12)

  def test_06_an_opposing_reaction_on_the_wrong_side_is_never_used(self):
    g = run(UPTREND, until=80)
    line = g.support
    # Every swing high used must lie above the support; a swing below it (negative offset) cannot be an ascending channel's top.
    fake = Swing(bar=50, price=line.at(50) - 3, high=True, atr=1.0, confirmed=56)
    g.swings = [fake]
    self.assertIsNone(g.build_channel(line, 79))

  def test_07_an_insufficient_width_is_rejected(self):
    self.assertIsNone(Geometry(bars_from(UPTREND), Params(channel_min_width_atr=15.0)).run(80).channel)

  def test_08_material_containment_failure_rejects_the_channel(self):
    # A close well above the would-be top between the anchors (a spike that is not a swing high of the list) rejects it.
    bars = bars_from(UPTREND)
    o, h, l, c = bars[52]
    bars[52] = (o, 125.0, l, 124.5)
    g = Geometry(bars).run(80)
    self.assertTrue(g.channel is None or g.channel.o_bar != 39)

  def test_09_an_opposing_overshoot_is_a_breach_not_a_wider_channel(self):
    bars = bars_from(UPTREND[:-2] + [(100, 140), (110, 135)])
    g = Geometry(bars).run()
    chs = [c for c in g.channels if c.o_bar == 39]
    self.assertEqual(len(chs), 1)
    ch = chs[0]
    self.assertIsNotNone(ch.breached_at)  # breached first (the line itself is later retired as too far from price, which ends the channel)
    self.assertAlmostEqual(ch.offset, 115.3 - (99.7 + 0.125 * 20), places=9)  # the width it was built with

  def test_10_the_channel_ends_with_its_primary_line(self):
    g = run(UPTREND[:-2] + [(100, 100), (110, 95)])
    ch = [c for c in g.channels if c.o_bar == 39][0]
    self.assertEqual(ch.state, "ended")
    self.assertIsNot(g.channel, ch)

  def test_11_no_retrospective_widening(self):
    early = run(UPTREND, until=80).channel.offset
    late = [c for c in run(UPTREND).channels if c.o_bar == 39][0].offset
    self.assertEqual(early, late)  # the later, equally high 120.3 swing never re-fits it

  def test_12_the_channel_reuses_the_primary_line(self):
    g = run(UPTREND, until=80)
    self.assertIs(g.channel.primary, g.support)
    self.assertEqual(len(g.channels), 1)


class TemporalTests(unittest.TestCase):
  def test_01_no_line_before_the_second_pivot_is_confirmed(self):
    self.assertIsNone(run(UPTREND, until=65).support)  # bars 0..64: the 59 low is confirmed at 65
    self.assertIsNotNone(run(UPTREND, until=66).support)

  def test_02_no_channel_before_its_opposing_reaction_is_confirmed(self):
    g = run(UPTREND, until=66)
    self.assertGreaterEqual(g.channel.published, g.channel.o_bar + 6)

  def test_03_no_touch_before_publication(self):
    s = run(UPTREND).support
    self.assertTrue(all(b > s.published - 6 - 1 for b in s.touch_bars) and all(b > s.b2 for b in s.touch_bars))

  def test_04_no_future_bars_are_read(self):
    # The state after bar t is identical whether or not later bars exist (equivalent history, refreshed chart).
    full = bars_from(UPTREND)
    for t in (66, 80, 101):
      a = Geometry(full).run(t)
      b = Geometry(full[:t]).run()
      self.assertEqual([(x.b1, x.b2, x.published, x.touches) for x in a.published], [(x.b1, x.b2, x.published, x.touches) for x in b.published])
      self.assertEqual([(c.o_bar, c.offset, c.state) for c in a.channels], [(c.o_bar, c.offset, c.state) for c in b.channels])

  def test_05_replay_is_deterministic(self):
    a, b = run(UPTREND), run(UPTREND)
    self.assertEqual([(x.b1, x.b2, x.published, x.touches, x.state) for x in a.published], [(x.b1, x.b2, x.published, x.touches, x.state) for x in b.published])


class EdgeCaseTests(unittest.TestCase):
  def test_missing_atr_during_initialization_draws_nothing(self):
    self.assertTrue(all(math.isnan(v) for v in wilder_atr([1] * 10, [0] * 10, [0.5] * 10)))
    g = Geometry(bars_from([(0, 100), (5, 98), (10, 101)])).run()
    self.assertEqual((g.published, g.channels), ([], []))

  def test_zero_atr_fails_safe(self):
    flat = [(100.0, 100.0, 100.0, 100.0)] * 120
    g = Geometry(flat).run()
    self.assertEqual((g.published, g.channels), ([], []))

  def test_insufficient_swing_history(self):
    g = run([(0, 110), (20, 100), (40, 115), (50, 112)])
    self.assertEqual(g.published, [])

  def test_sideways_market_draws_nothing(self):
    wps = [(0, 110)] + [(i * 15, 100 + (8 if i % 2 else 0)) for i in range(1, 12)]
    g = run(wps)
    self.assertEqual(g.published, [])

  def test_large_gap_breaks_the_line(self):
    bars = bars_from(UPTREND[:-1] + [(110, 113)])
    o, h, l, c = bars[-1]
    bars.append((90.0, 91.0, 89.0, 90.0))  # a gap far below the line
    g = Geometry(bars).run()
    self.assertTrue(any((x.b1, x.b2) == (19, 59) and x.state == "broken" for x in g.history))

  def test_tick_size_scale_invariance(self):
    # FX-like prices (x 0.0001): the same geometry, the same decisions.
    scaled = [(o * 0.0001, h * 0.0001, l * 0.0001, c * 0.0001) for o, h, l, c in bars_from(UPTREND)]
    a, b = run(UPTREND), Geometry(scaled).run()
    self.assertEqual([(x.b1, x.b2, x.touches) for x in a.published], [(x.b1, x.b2, x.touches) for x in b.published])


if __name__ == "__main__":
  unittest.main()

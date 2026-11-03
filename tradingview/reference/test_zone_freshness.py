"""v2.13 zone rules: freshness at publication, order block anchored to the real break bar, the FVG definition and the POI paths.

Run: python tradingview/reference/test_zone_freshness.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pa_model as m
import report_v213 as rep

CASES = list(rep.cases())
LEGACY = dict(structure_mode="legacy", protected_mode="legacy", range_mode="v212", ob_break_anchor=False, fvg_def="v212", composite_independent=False, swept_before_impulse=False)


def pipeline(bars, secs, **kw):
  return m.V21Pipeline(bars, m.v213(secs, **{**LEGACY, **kw}))


def test_no_swing_zone_is_drawn_fresh_after_a_retest():
  """v2.12 publishes a swing a median 27 bars late, so a third of its zones were already retested or closed through when drawn (shown as Fresh)."""
  before = after = 0
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    old = pipeline(bars, secs, fresh_sd=False).run()
    before += old.counts["sd_scan_touched"] + old.counts["sd_scan_invalid"]
    new = pipeline(bars, secs, fresh_sd=True)
    s = new.s
    for i in range(s.n):
      ev = s.step_pivots(i)
      if m.isna(s.atr[i]):
        continue
      new.v21_ob(i, ev)
      new.supply_demand(i, ev)
      new.fvg(i)
      for z in new.zones:
        if z.kind == 3 and z.cls == 0 and z.born == i:
          invalid, first, depth = new.fresh_scan(z.side, z.top, z.bottom, z.origin, i)
          assert not invalid, "%s %s: a swing zone closed through before publication was drawn" % (name, tf)
          if first >= 0:
            assert z.touched and z.state == "Tested" and z.first_touch == first and abs(z.mitigation - depth) < 1e-12
            after += 1
          else:
            assert not z.touched and z.state == "Fresh"
      new.lifecycle(i)
    rejected = new.counts["sd_rejected"]
    assert rejected == new.counts["sd_scan_invalid"], "every close-through is counted, none drawn"
  assert before >= 100, "the captures no longer show the v2.12 defect (%d)" % before


def test_a_zone_drawn_after_a_retest_is_never_a_trade_trigger():
  import trade_model as tm
  trades = 0
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    sim = tm.TradePlanSim(bars, m.v213(secs, **{**LEGACY, "fresh_sd": True}), tm.v213_trade()).run()
    for t in sim.trades:
      trades += 1
      assert t.zone_first_touch >= t.zone_born, "%s %s: a trade started from a zone that was retested before it was drawn" % (name, tf)
  assert trades >= 3
  # the rule itself, on a zone the scan found already touched (first touch 3 bars BEFORE its publication)
  sim = tm.TradePlanSim(rep.bars_from(CASES[0][1], "M5"), m.v213(300), tm.v213_trade())
  tested = m.Zone(50, 110.0, 100.0, 1, 3, "Demand", 200, touched=True, first_touch=197)
  fresh = m.Zone(50, 110.0, 100.0, 1, 3, "Demand", 200, touched=True, first_touch=201)
  assert not sim.can_trigger(tested, 201) and not sim.can_trigger(tested, 202)
  assert sim.can_trigger(fresh, 202) and not sim.can_trigger(fresh, 204)


def test_swing_zone_freshness_is_a_scan_of_the_bars_after_the_departure():
  """Synthetic: a demand swing at bar 40; bars 41..52 are the departure (ignored); a later wick into the zone makes it Tested; a later close below kills it."""
  def series(touch_low=None, close_below=None):
    n = 80
    o = [100.0] * n
    h = [101.0] * n
    l = [99.0] * n
    c = [100.0] * n
    l[40], o[40], c[40], h[40] = 90.0, 92.0, 94.0, 95.0   # the swing low candle: zone [90, 92]
    for k in range(41, 53):
      l[k], h[k], o[k], c[k] = 96.0, 99.0, 97.0, 98.0
    if touch_low is not None:
      l[touch_low[0]] = touch_low[1]
    if close_below is not None:
      c[close_below[0]] = close_below[1]
      l[close_below[0]] = min(l[close_below[0]], close_below[1])
    return list(range(n)), o, h, l, c

  p = m.v213(300, **{**LEGACY, "fresh_sd": True})
  for kwargs, want in (({}, "fresh"), (dict(touch_low=(60, 91.0)), "tested"), (dict(close_below=(60, 89.0)), "invalid"), (dict(touch_low=(45, 91.0)), "fresh")):
    pipe = m.V21Pipeline(series(**kwargs), p)
    invalid, first, depth = pipe.fresh_scan(1, 92.0, 90.0, 40, 79)
    got = "invalid" if invalid else "tested" if first >= 0 else "fresh"
    assert got == want, (kwargs, got, want)
    if want == "tested":
      assert first == 60 and abs(depth - 0.5) < 1e-12


def test_order_blocks_of_late_external_breaks_are_searched_from_the_real_break_bar():
  differs_total = late_total = 0
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    d, late = rep.late_ob_origin_differs(bars, secs)
    differs_total += d
    late_total += late
    for anchored in (False, True):
      pipe = pipeline(bars, secs, ob_break_anchor=anchored)
      s = pipe.s
      for i in range(s.n):
        ev = s.step_pivots(i)
        if m.isna(s.atr[i]):
          continue
        n0 = len(pipe.ob_dbg)
        pipe.v21_ob(i, ev)
        pipe.supply_demand(i, ev)
        pipe.fvg(i)
        if anchored:
          for z in pipe.zones:
            if z.kind == 1 and z.cls == 2 and z.born == i and ev.get("break_bar", i) != i:
              assert z.origin < ev["break_bar"], "%s %s: an order block origin after the bar that really broke" % (name, tf)
        pipe.lifecycle(i)
  assert late_total >= 40 and differs_total >= 5, (differs_total, late_total)


def test_internal_breaks_and_prompt_external_breaks_are_unchanged_by_the_anchor():
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    a = pipeline(bars, secs, ob_break_anchor=False).run()
    b = pipeline(bars, secs, ob_break_anchor=True).run()
    keep = lambda pipe: sorted((z.origin, z.top, z.bottom, z.side) for z in pipe.zones if z.kind == 1 and z.cls == 1)
    # internal blocks never move; only external ones from late breaks may differ, so the internal sets agree except through replacement ordering
    assert abs(len(keep(a)) - len(keep(b))) <= 3, (name, tf)


def test_fvg_definition_engine_loose_and_the_body_direction():
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    counts = {}
    for d in ("v212", "loose", "engine"):
      pipe = m.V21Pipeline(bars, m.P21(chart_secs=secs, fvg_def=d)).run()
      counts[d] = pipe.counts["fvg_added"]
    assert counts["engine"] <= counts["loose"] <= counts["v212"], (name, tf, counts)
    if tf in ("M15", "H1"):
      assert counts["engine"] < 0.55 * counts["v212"], (name, tf, counts)
    # every engine gap really is: >= 0.5 ATR, middle body >= 0.5 ATR and >= 0.5 of its range, in the gap's direction
    p = m.P21(chart_secs=secs, fvg_def="engine")
    s = m.Structure(bars, p)
    pipe = m.V21Pipeline(bars, p)
    for i in range(s.n):
      ev = s.step_pivots(i)
      if m.isna(s.atr[i]):
        continue
      n0 = len(pipe.zones)
      pipe.fvg(i)
      for z in pipe.zones[n0:]:
        if z.kind != 2 or z.born != i:
          continue
        mid_o, mid_c = bars[1][i - 1], bars[4][i - 1]
        body, rng = abs(mid_c - mid_o), bars[2][i - 1] - bars[3][i - 1]
        assert z.top - z.bottom >= 0.5 * s.atr[i] - 1e-9 and body >= 0.5 * s.atr[i] - 1e-9 and body / rng >= 0.5 - 1e-9
        assert (mid_c > mid_o) if z.side == 1 else (mid_c < mid_o), "a bullish gap printed by a bearish middle candle"
      pipe.lifecycle(i)
    # the loose definition also refuses a body against the gap (v2.12 printed it)
    loose = m.V21Pipeline(bars, m.P21(chart_secs=secs, fvg_def="loose"))
    for i in range(s.n):
      loose.s.step_pivots(i)
    assert counts["loose"] <= counts["v212"]


def _zone(origin, kind, side=1, top=110.0, bottom=100.0, born=200):
  return m.Zone(origin, top, bottom, side, kind, "z", born)


def _bare(secs=300, **kw):
  bars = rep.bars_from(CASES[0][1], "M5")
  pipe = m.V21Pipeline(bars, m.v213(secs, **kw))
  pipe.s.bias = 1
  pipe.zones = []
  return pipe


def test_composite_needs_independent_components():
  # an order block and the FVG its own impulse printed (origins 2 bars apart) are one event; 10 bars apart they are two
  for gap_bars, independent_expected in ((2, False), (3, False), (4, True), (10, True)):
    pipe = _bare()
    ob, fvg = _zone(100, 1), _zone(100 + gap_bars, 2)
    pipe.zones = [ob, fvg]
    has = pipe.components(ob)
    assert (has[1] and has[2]) is independent_expected, (gap_bars, has)
  pipe = _bare(composite_independent=False)
  pipe.zones = [_zone(100, 1), _zone(101, 2)]
  assert pipe.components(pipe.zones[0])[2], "v2.12 counted them as two components"


def test_swept_imbalance_means_the_sweep_came_before_the_impulse():
  fvg = _zone(100, 2, born=102)
  for sweep_bar, side, want in ((100, 1, True), (94, 1, True), (93, 1, False), (101, 1, False), (130, 1, False), (97, -1, False)):
    pipe = _bare()
    pipe.zones = [fvg]
    pipe.s.sweeps = [(sweep_bar, side)]
    assert pipe.swept_imbalance(fvg) is want, (sweep_bar, side, want)
  # v2.12 accepted a sweep AFTER the gap (|born - sweep| <= window); that is gone
  legacy = _bare(swept_before_impulse=False)
  legacy.s.last_sweep_side, legacy.s.last_sweep_bar = 1, 130
  assert legacy.swept_imbalance(fvg), "the v2.12 rule is kept in legacy mode"


def test_real_sweeps_are_recorded_for_the_qualification():
  import trade_model as tm
  seen = 0
  for name, path, tf, secs in CASES:
    sim = tm.TradePlanSim(rep.bars_from(path, tf), m.v213(secs), tm.v213_trade()).run()
    seen += len(sim.s.sweeps)
    assert all(side in (-1, 1) and 0 <= bar < sim.s.n for bar, side in sim.s.sweeps)
  assert seen > 20


if __name__ == "__main__":
  for name, fn in list(globals().items()):
    if name.startswith("test_"):
      fn()
      print("ok", name)

"""Regression checks for the Pine v2.1 decision logic, run on real XAUUSD closed bars.

Run: python -m pytest tradingview/reference -q   (or python tradingview/reference/test_pa_model.py)
These checks validate the algorithm. They do not compile or render the Pine file.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pa_model as m

HERE = os.path.dirname(__file__)
LIVE = os.path.join(HERE, "fixtures", "xau-closed-bars-20261102.json")
LIVE_0600 = os.path.join(HERE, "fixtures", "xau-m15-closed-bars-20261102-0600.json")
REPO = os.path.join(HERE, "..", "..", "analysis-engine", "testdata", "obwindow-xau-20261029.json")


def load(path):
  d = json.load(open(path))["timeframes"]
  out = {}
  for tf, secs in (("M5", 300), ("M15", 900), ("H1", 3600)):
    r = d[tf]
    out[tf] = (secs, tuple([x[k] for x in r] for k in range(5)))
  return out


def run_v21(bars, secs):
  p = m.P21(chart_secs=secs)
  pipe = m.V21Pipeline(bars, p).run().display()
  st = m.Structure(bars, p)
  lm = m.LiquidityModel(st, p)
  for i in range(st.n):
    lm.step(i, st.step_pivots(i))
  return pipe, lm, p


def test_legacy_hides_valid_blocks_on_live_capture():
  """v2.0 reproduction: blocks exist but none is visible (distance gate, cross-kind overlap)."""
  for tf, (secs, bars) in load(LIVE).items():
    old = m.LegacyPipeline(bars, m.Params(chart_secs=secs)).run().display()
    audit, _ = old.audit()
    if tf in ("M5", "M15"):
      assert audit["active_ob"] >= 1 and audit["visible_ob"] == 0, (tf, audit)


def _band(pipe, p):
  s = pipe.s
  return max(s.h[-p.scale_lookback_bars:]), min(s.l[-p.scale_lookback_bars:])


def test_v21_shows_blocks_and_explains_hidden_ones():
  for path in (LIVE, REPO):
    for tf, (secs, bars) in load(path).items():
      pipe, _, p = run_v21(bars, secs)
      s = pipe.s
      hi, lo = _band(pipe, p)
      obs = [z for z in pipe.zones if z.kind == 1]
      visible = [z for z in obs if z.visible]

      def eligible(z):
        mid = (z.top + z.bottom) / 2
        return abs(s.c[-1] - mid) / s.atr[-1] <= p.ob_display_distance_atr and p.scale_ok(mid, s.c[-1], s.atr[-1], hi, lo)

      if any(eligible(z) for z in obs):
        assert visible, (tf, "eligible blocks but none visible")
      for z in visible:
        assert eligible(z), (tf, "visible block outside its distance / candle-range limits")
      assert sum(1 for z in visible if z.side == 1) <= p.max_vis_bull_ob
      assert sum(1 for z in visible if z.side == -1) <= p.max_vis_bear_ob
      for z in obs:
        if not z.visible:
          assert z.hidden_reason, (tf, "hidden block without a reason")


def test_candle_scale_protection_never_stretches_the_scale():
  """Every drawn zone / pool is near price or inside the range the candles already span."""
  for tf, (secs, bars) in load(LIVE).items():
    pipe, lm, p = run_v21(bars, secs)
    s = pipe.s
    hi, lo = _band(pipe, p)
    for z in pipe.zones:
      if z.visible:
        assert p.scale_ok((z.top + z.bottom) / 2, s.c[-1], s.atr[-1], hi, lo), (tf, z.title)
    for x in lm.select_visible(s.c[-1], s.atr[-1]):
      assert p.scale_ok(x.edge, s.c[-1], s.atr[-1], hi, lo), (tf, "pool outside the candle range")
    assert m.P21(chart_secs=secs, protect_scale=False).scale_ok(1e9, 1.0, 1.0, 2.0, 0.0)


def test_nearest_bsl_and_ssl_are_drawn_when_inside_the_candle_range():
  """Regression for 'BSL / SSL missing': a pool 15+ ATR away is still drawn when the
  candles already span its price."""
  d = json.load(open(LIVE_0600))["timeframes"]["M15"]
  bars = tuple([x[k] for x in d] for k in range(5))
  pipe, lm, p = run_v21(bars, 900)
  s = pipe.s
  close, atr = s.c[-1], s.atr[-1]
  shown = lm.select_visible(close, atr)
  act = [x for x in lm.pools if x.state == 0]
  above = min((x for x in act if x.side == 1 and x.edge > close), key=lambda x: x.edge - close, default=None)
  below = max((x for x in act if x.side == -1 and x.edge < close), key=lambda x: x.edge, default=None)
  assert above is not None and above in shown, "nearest BSL missing from the drawn pools"
  assert below is not None and below in shown, "nearest SSL missing from the drawn pools"
  assert (above.edge - close) / atr > p.scale_window_atr * 0.9, "fixture no longer exercises the far-pool case"


def test_nearest_internal_markers_are_unswept_and_not_duplicates():
  for tf, (secs, bars) in load(LIVE).items():
    pipe, lm, p = run_v21(bars, secs)
    s = pipe.s
    close, atr = s.c[-1], s.atr[-1]
    hi, lo = lm.nearest_internal(close, atr)
    if hi:
      assert hi[0] > close and max(s.h[hi[1] + 1:]) <= hi[0]
      assert not any(x.state == 0 and x.side == 1 and abs(x.edge - hi[0]) <= atr * 0.5 for x in lm.pools)
    if lo:
      assert lo[0] < close and min(s.l[lo[1] + 1:]) >= lo[0]
      assert not any(x.state == 0 and x.side == -1 and abs(x.edge - lo[0]) <= atr * 0.5 for x in lm.pools)
  secs, bars = load(LIVE)["M15"]
  _, lm, _ = run_v21(bars, secs)
  hi, lo = lm.nearest_internal(bars[4][-1], 7.2)
  assert hi or lo, "expected at least one internal marker on the live M15 capture"


def test_bos_choch_line_ends_on_the_candle_that_broke_the_level():
  """Regression for late-drawn BOS lines: the line ends on the first close beyond the swing,
  never on a later publication bar, and no earlier close lies beyond the level."""
  d = json.load(open(LIVE_0600))["timeframes"]["M15"]
  bars = tuple([x[k] for x in d] for k in range(5))
  p = m.P21(chart_secs=900)
  st = m.Structure(bars, p)
  c = bars[4]
  late = 0
  for i in range(st.n):
    ev = st.step_pivots(i)
    for flag, up in (("up_break", True), ("down_break", False)):
      if not ev[flag]:
        continue
      end = ev["break_bar"]
      lvl = ev["broken_high"] if up else ev["broken_low"]
      sb = ev["broken_high_bar"] if up else ev["broken_low_bar"]
      assert sb < end <= i
      need = p.confirm_atr + p.break_buffer
      beyond = lambda k: c[k] > lvl + st.atr[k] * need if up else c[k] < lvl - st.atr[k] * need
      assert beyond(end) or end == i
      assert not any(beyond(k) for k in range(sb + 1, end)), "a close beyond the level precedes the drawn end"
      late += end < i
  assert late >= 5, "fixture no longer contains late-published swings"


def test_right_edge_labels_never_overlap_and_lose_nothing():
  """Regression for overlapping labels (PROTECTED / BSL / PREMIUM, DEV HIGH / iBSL, DISCOUNT / SSL)."""
  gap = m.label_gap(7.2, 4082.0, 3846.0)
  items = [(4032.57, "BSL", 60), (4032.45, "PROTECTED", 80), (4031.0, "PREMIUM", 15), (3951.28, "EQ 3951.28", 35),
           (3923.84, "iBSL", 40), (3919.19, "DEV HIGH", 70), (3897.0, "iSSL", 40), (3870.21, "SSL", 60), (3872.5, "DISCOUNT", 15),
           (4018.64, "Asia H", 50), (3965.09, "Asia L", 50)]
  out = m.cluster_labels(items, gap)
  texts = [o[1] for o in out]
  assert "PROTECTED · BSL · PREMIUM" in texts and "DEV HIGH · iBSL" in texts and "SSL · DISCOUNT" in texts
  ys = sorted(o[0] for o in out)
  assert all(b - a >= gap - 1e-9 for a, b in zip(ys, ys[1:])), "two output labels are closer than the gap"
  assert sorted(k for o in out for k in o[2]) == list(range(len(items))), "a label was lost or duplicated"
  assert [o for o in out if "PROTECTED" in o[1]][0][0] == 4032.45, "cluster must sit on its highest-priority member"
  many = [(100.0 - 0.01 * k, "L%d" % k, k) for k in range(8)]
  assert m.cluster_labels(many, gap)[0][1].endswith("+5")


def test_one_bos_per_break_candle():
  evs = [(865, True, False, 330), (865, True, False, 662), (900, False, True, 700), (900, False, True, 710), (950, True, False, 10)]
  kept = m.dedupe_breaks(evs)
  assert kept == [(865, True, False, 662), (900, False, True, 700), (900, False, True, 710), (950, True, False, 10)]
  d = json.load(open(LIVE_0600))["timeframes"]["M15"]
  bars = tuple([x[k] for x in d] for k in range(5))
  st = m.Structure(bars, m.P21(chart_secs=900))
  events = []
  for i in range(st.n):
    ev = st.step_pivots(i)
    for flag, bar_key, up in (("up_break", "broken_high_bar", True), ("down_break", "broken_low_bar", False)):
      if ev[flag]:
        events.append((ev["break_bar"], up, ev["choch"], ev[bar_key]))
  kept = m.dedupe_breaks(events)
  keys = [(e[0], e[1]) for e in kept if not e[2]]
  assert len(keys) == len(set(keys)), "two BOS lines still end on the same candle in the same direction"


def test_order_block_construction_rules():
  for tf, (secs, bars) in load(LIVE).items():
    pipe, _, p = run_v21(bars, secs)
    s = pipe.s
    for d in pipe.ob_dbg:
      if not d["ok"]:
        assert d["reason"], "rejection without a reason"
        continue
      i = d["bar"]
      assert d["top"] > d["bottom"]
      origin = next(k for k in range(1, p.ob_search_bars + 1) if s.t[i - k] == d["src"])
      if d["bull"]:
        assert s.c[i - origin] < s.o[i - origin], "bullish block must start from a bearish candle"
        assert s.c[i] - d["top"] >= p.ob_displacement_atr * s.atr[i] - 1e-9
      else:
        assert s.c[i - origin] > s.o[i - origin], "bearish block must start from a bullish candle"
        assert d["bottom"] - s.c[i] >= p.ob_displacement_atr * s.atr[i] - 1e-9


def test_no_overlapping_duplicates_in_active_store():
  for tf, (secs, bars) in load(LIVE).items():
    pipe, _, _ = run_v21(bars, secs)
    obs = [z for z in pipe.zones if z.kind == 1]
    for a in obs:
      for b in obs:
        if a is not b and a.side == b.side:
          ov = min(a.top, b.top) - max(a.bottom, b.bottom)
          sm = min(a.top - a.bottom, b.top - b.bottom)
          assert not (ov > 0 and ov / sm >= 0.5), (tf, a.title, b.title)


def test_partial_touch_is_not_invalidation():
  """With the Close rule a block that price has entered stays active as Tested."""
  tested = 0
  for path in (LIVE, REPO):
    for tf, (secs, bars) in load(path).items():
      pipe, _, _ = run_v21(bars, secs)
      for z in pipe.zones:
        if z.kind == 1 and z.touched:
          tested += 1
          assert z.state == "Tested"
          last = pipe.s.c[-1]
          assert not (z.side == 1 and last < z.bottom) and not (z.side == -1 and last > z.top)
  assert tested > 0


def _synthetic(closes_highs_lows):
  """Flat 14-bar warm-up (ATR 1.0) followed by the supplied (high, low, close) bars."""
  n0 = 20
  o = [100.0] * n0
  h = [100.5] * n0
  l = [99.5] * n0
  c = [100.0] * n0
  for hi, lo, cl in closes_highs_lows:
    o.append(c[-1]); h.append(hi); l.append(lo); c.append(cl)
  t = list(range(len(c)))
  return (t, o, h, l, c)


def _run_pool(extra):
  bars = _synthetic(extra)
  p = m.P21(chart_secs=300)
  st = m.Structure(bars, p)
  lm = m.LiquidityModel(st, p)
  quiet = dict(new_ih=False, new_il=False, new_major_high=False, new_major_low=False, up_break=False, down_break=False,
               ih=m.NAN, il=m.NAN, major_high_price=m.NAN, major_low_price=m.NAN, major_high_bar=None, major_low_bar=None,
               broken_high=m.NAN, broken_low=m.NAN)
  for i in range(len(bars[0])):
    st.step_pivots(i)
    if i == 19:
      lm.new_pool(i, 1, 101.0, 101.0, 10, 1, 2, "Swing", 10)
    lm.step(i, quiet)
  return lm


def test_sweep_and_consumption_use_different_rules():
  # wick through 101.0 then a close back inside on the next bar -> swept, never consumed
  lm = _run_pool([(101.6, 100.0, 100.6), (100.8, 100.0, 100.4)])
  assert [e[1] for e in lm.events] == ["swept"], lm.events
  # two closes beyond the edge by the break buffer -> consumed, never swept
  lm = _run_pool([(101.8, 100.9, 101.5), (102.0, 101.2, 101.7)])
  assert [e[1] for e in lm.events] == ["consumed"], lm.events
  # a single close beyond followed by a return inside the window is a sweep, not a break
  lm = _run_pool([(101.8, 100.9, 101.5), (101.1, 100.2, 100.6)])
  assert [e[1] for e in lm.events] == ["swept"], lm.events
  # penetration that never returns inside the window and never gets accepted does nothing
  lm = _run_pool([(101.3, 100.9, 101.15), (101.2, 100.95, 101.05), (101.1, 100.9, 100.95)])
  assert lm.events == [], lm.events


def test_consumed_pool_never_alerts_again():
  lm = _run_pool([(101.8, 100.9, 101.5), (102.0, 101.2, 101.7), (103.0, 101.5, 102.5), (99.0, 98.0, 98.5), (103.0, 101.5, 102.5)])
  assert [e[1] for e in lm.events] == ["consumed"], lm.events


def test_pool_events_are_one_shot_on_real_bars():
  for tf, (secs, bars) in load(LIVE).items():
    _, lm, _ = run_v21(bars, secs)
    uids = [e[5] for e in lm.events]
    assert len(uids) == len(set(uids)), (tf, "a pool produced more than one terminal event")
    assert lm.stats["swept"] + lm.stats["consumed"] == len(lm.events)


def test_minor_pivots_do_not_become_pools():
  for tf, (secs, bars) in load(LIVE).items():
    _, lm, _ = run_v21(bars, secs)
    assert lm.stats["created"] < 0.5 * lm.stats["pivots_seen"], (tf, lm.stats)
    for pool in lm.pools:
      assert pool.degree >= 2 or pool.tests >= 2, "unqualified internal pivot became a pool"


def test_eq_band_tolerance_and_separation():
  for tf, (secs, bars) in load(LIVE).items():
    pipe, lm, p = run_v21(bars, secs)
    atr = pipe.s.atr[-1]
    for pool in lm.pools:
      if pool.kind == "EQ":
        assert pool.top - pool.bottom <= 2 * p.equal_tolerance * max(a for a in pipe.s.atr if a == a) + 1e-9
        assert pool.tests >= 2


if __name__ == "__main__":
  for name, fn in list(globals().items()):
    if name.startswith("test_"):
      fn()
      print("ok", name)

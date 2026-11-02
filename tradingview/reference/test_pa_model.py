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
LIVE = os.path.join(HERE, "data", "xau-closed-bars-20261102.json")
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


def test_v21_shows_blocks_and_explains_hidden_ones():
  for path in (LIVE, REPO):
    for tf, (secs, bars) in load(path).items():
      pipe, _, p = run_v21(bars, secs)
      obs = [z for z in pipe.zones if z.kind == 1]
      visible = [z for z in obs if z.visible]
      if obs:
        assert visible, (tf, "active blocks but none visible")
      assert sum(1 for z in visible if z.side == 1) <= p.max_vis_bull_ob
      assert sum(1 for z in visible if z.side == -1) <= p.max_vis_bear_ob
      for z in obs:
        if not z.visible:
          assert z.hidden_reason, (tf, "hidden block without a reason")


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

"""Checks for the v2.2 base-and-impulse supply/demand detector on real XAUUSD closed bars.

Run: python tradingview/reference/test_sd_base_model.py
Validates the algorithm only; it does not compile or draw the Pine file.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import sd_base_model as sd

REPO = os.path.join(os.path.dirname(__file__), "..", "..", "analysis-engine", "testdata", "obwindow-xau-20261029.json")


def real_bars():
  d = json.load(open(REPO))["timeframes"]
  return {tf: tuple([x[k] for x in d[tf]] for k in range(5)) for tf in ("M5", "M15", "H1")}


def synthetic(extra):
  o, h, l, c = [100.0] * 20, [100.5] * 20, [99.5] * 20, [100.0] * 20
  for oo, hh, ll, cc in extra:
    o.append(oo); h.append(hh); l.append(ll); c.append(cc)
  return (list(range(len(c))), o, h, l, c)


DROP = [(100.0, 100.1, 99.4, 99.5), (99.5, 99.6, 98.9, 99.0), (99.0, 99.1, 98.4, 98.5)]
BASE = [(98.5, 98.8, 98.3, 98.5), (98.5, 98.8, 98.3, 98.5)]


def test_clean_dbr_builds_one_demand_zone():
  b = synthetic(DROP + BASE + [(98.5, 100.8, 98.4, 100.6)])
  created, _, active, _ = sd.detect(b)
  assert len(created) == 1 and len(active) == 1, created
  z = created[0]
  assert z.side == 1 and z.pattern == "DBR"
  assert abs(z.top - 98.8) < 1e-9 and abs(z.bottom - 98.3) < 1e-9 and z.base_bars == 2


def test_rbr_is_continuation_and_filters_work():
  rise = [(100.0, 100.6, 99.9, 100.5), (100.5, 101.1, 100.4, 101.0), (101.0, 101.6, 100.9, 101.5)]
  base = [(101.5, 101.8, 101.3, 101.5), (101.5, 101.8, 101.3, 101.5)]
  b = synthetic(rise + base + [(101.5, 103.8, 101.4, 103.6)])
  created, _, _, _ = sd.detect(b)
  assert [z.pattern for z in created] == ["RBR"]
  assert not sd.detect(b, sd.Params(pattern_filter="Reversal only"))[0]
  assert sd.detect(b, sd.Params(pattern_filter="Continuation only"))[0]


def test_weak_departure_and_closed_back_are_rejected():
  weak = synthetic(DROP + BASE + [(98.5, 99.3, 98.4, 99.1)])
  assert not sd.detect(weak)[0]
  back = synthetic(DROP + BASE + [(98.5, 100.8, 98.4, 100.6), (100.6, 100.7, 97.9, 98.0)])
  created, _, active, _ = sd.detect(back)
  assert len(created) == 1 and not active, "a close beyond the distal edge must retire the zone"


def test_wide_or_large_bodied_base_is_not_a_base():
  wide = synthetic(DROP + [(98.5, 99.9, 97.6, 99.0)] + [(99.0, 101.5, 98.9, 101.3)])
  assert not sd.detect(wide)[0]


def test_real_bars_invariants():
  p = sd.Params()
  for tf, b in real_bars().items():
    t, o, h, l, c = b
    atr = sd.rma_atr(h, l, c)
    created, _, active, stats = sd.detect(b, p)
    assert created, tf
    for z in created:
      a = atr[z.born]
      assert z.top > z.bottom
      assert z.top - z.bottom <= p.max_width_atr * a + 1e-9, (tf, "zone wider than the base limit")
      assert z.departure_atr >= p.depart_atr - 1e-9
      assert z.pattern in (("DBR", "RBR") if z.side == 1 else ("RBD", "DBD"))
      lo_i, hi_i = z.born - z.depart_bars + 1, z.born
      for k in range(lo_i, hi_i + 1):
        assert not (z.side == 1 and c[k] < z.bottom) and not (z.side == -1 and c[k] > z.top), (tf, "departure closed back through")
    for z in active:
      assert not (z.side == 1 and c[-1] < z.bottom) and not (z.side == -1 and c[-1] > z.top)
    assert stats["accepted"] == len(created)
    assert len(active) <= p.max_zones


def test_no_overlapping_duplicates_among_active_zones():
  for tf, b in real_bars().items():
    _, _, active, _ = sd.detect(b)
    for x in active:
      for y in active:
        if x is not y and x.side == y.side:
          ov = min(x.top, y.top) - max(x.bottom, y.bottom)
          assert not (ov > 0 and ov / min(x.top - x.bottom, y.top - y.bottom) >= 0.85)


if __name__ == "__main__":
  for name, fn in list(globals().items()):
    if name.startswith("test_"):
      fn()
      print("ok", name)

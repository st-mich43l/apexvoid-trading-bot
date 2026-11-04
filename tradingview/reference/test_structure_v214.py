"""v2.14 structure rules: the higher low that made a higher high is the protected low, a protected-level CHoCH takes the last internal pivot, the developing
leg follows the protected level, and every frame shown in the MTF row is that frame's own chart.

Run: python tradingview/reference/test_structure_v214.py
v2.13 behaviour is kept by `pa_model.v213`; the legacy (v2.12) digests are asserted in test_structure_v213.py.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import mtf
import pa_model as m
import report_v213 as rep

CASES = list(rep.cases())
SCALP = [p for n, p in rep.FIXTURES if n == "scalpwindow-1103"][0]
T0 = 1_800_000_000


def synthetic(waypoints, secs=300, wick=0.4):
  """Closes interpolated between waypoints (bars to reach, price); every candle opens at the previous close and carries a fixed wick."""
  closes = [waypoints[0][1]]
  price = waypoints[0][1]
  for n, target in waypoints[1:]:
    closes += [price + (target - price) * j / n for j in range(1, n + 1)]
    price = target
  t, o, h, l, c = [], [], [], [], []
  for i, cl in enumerate(closes):
    op = closes[i - 1] if i else cl
    t.append(T0 + i * secs)
    o.append(op)
    c.append(cl)
    h.append(max(op, cl) + wick)
    l.append(min(op, cl) - wick)
  return t, o, h, l, c


def m30_bars():
  return mtf.resample(rep.bars_from(SCALP, "M15"), 900, 2)


def run(bars, p, upto=None):
  s = m.Structure(bars, p)
  events = []
  for i in range(s.n if upto is None else upto):
    ev = s.step_pivots(i)
    events.append((i, ev, s.bias, s.protected_high, s.protected_low, s.protected_high_bar, s.protected_low_bar, s.dev_dir, s.dev_high, s.dev_low))
  return s, events


def test_the_ring_keeps_the_last_eight_pivots_and_picks_the_most_recent_in_range():
  ring = [(10, 5.0), (20, 4.0), (30, 6.0)]
  assert m.Structure.ring_pick(ring, 12, 30) == (30, 6.0)
  assert m.Structure.ring_pick(ring, 12, 29) == (20, 4.0)
  assert m.Structure.ring_pick(ring, 20, 29) is None, "the lower bound is exclusive"
  for name, path, tf, secs in CASES:
    s, _ = run(rep.bars_from(path, tf), m.v214(secs))
    assert len(s.i_low_ring) <= 8 and len(s.i_high_ring) <= 8


def test_synthetic_choch_takes_the_higher_low_not_the_leg_low_when_the_protected_high_predates_it():
  # decline from 100 to 60, a rally to 68 that publishes nothing, a pullback to 64 (the internal higher low), then a rally through the protected high
  bars = synthetic([(0, 80), (40, 80), (30, 100), (40, 60), (25, 68), (8, 64), (25, 104), (10, 104)])
  old, old_events = run(bars, m.v213(300))
  new, new_events = run(bars, m.v214(300))
  choch = [e for e in new_events if e[1]["protected_choch"]]
  assert len(choch) == 1 and [e for e in old_events if e[1]["protected_choch"]][0][0] == choch[0][0]
  i, ev, bias, ph, pl, phb, plb = choch[0][:7]
  assert bias == 1 and ev["broken_high"] == 100.4 and ev["broken_high_bar"] < plb <= ev["break_bar"]
  old_pl = [e for e in old_events if e[1]["protected_choch"]][0][4]
  assert old_pl == min(bars[3][ev["broken_high_bar"]:i + 1]) == 59.6, "v2.13: the leg low, two days of decline back"
  assert pl == 63.6 == bars[3][plb], "v2.14: the internal higher low that launched the breaking leg"
  assert i - plb >= new.internal_len, "the pivot's right bars had closed before it was used"


def test_without_a_confirmed_internal_pivot_in_range_the_choch_falls_back_to_the_lowest_low():
  # a V reversal: the rally breaks the protected high two bars after the low, before that low is a confirmed internal pivot (3 right bars)
  bars = synthetic([(0, 80), (40, 80), (30, 100), (40, 60), (2, 104), (10, 104)])
  s = m.Structure(bars, m.v214(300))
  seen = 0
  for i in range(s.n):
    ring_before = list(s.i_low_ring)
    ev = s.step_pivots(i)
    if ev["protected_choch"]:
      seen += 1
      assert m.Structure.ring_pick(ring_before, ev["broken_high_bar"], ev["break_bar"]) is None, "no confirmed pivot between the protected high and the break"
      assert s.protected_low == min(bars[3][ev["broken_high_bar"]:i + 1]), "the fallback is today's lowest low since the protected high"
  assert seen == 1


def test_every_protected_choch_uses_a_pivot_that_was_confirmed_and_in_range_or_the_fallback():
  checked = picked = 0
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    s = m.Structure(bars, m.v214(secs))
    for i in range(s.n):
      ev = s.step_pivots(i)
      if not ev["protected_choch"]:
        continue
      checked += 1
      up = ev["up_break"]
      lbar = ev["broken_high_bar"] if up else ev["broken_low_bar"]
      new, new_bar = (s.protected_low, s.protected_low_bar) if up else (s.protected_high, s.protected_high_bar)
      series = bars[3] if up else bars[2]
      assert new == series[new_bar], "%s %s: the protected level is a real bar extreme" % (name, tf)
      leg = min(bars[3][lbar:i + 1]) if up else max(bars[2][lbar:i + 1])
      assert (new >= leg) if up else (new <= leg), "never further than the old leg extreme"
      if new != leg:
        picked += 1
        assert lbar < new_bar <= ev["break_bar"] and i - new_bar >= s.internal_len, (name, tf, i)
  assert checked >= 20 and picked >= 5, (checked, picked)


def test_3_nov_m30_the_higher_low_is_the_protected_low_after_the_3970_high_and_the_bias_stays_bull():
  bars = m30_bars()
  old, _ = run(bars, m.v213(1800))
  new, events = run(bars, m.v214(1800))
  hit = [e for e in events if e[1]["new_major_high"] and abs(e[1]["major_high_price"] - 3970.3) < 0.1]
  assert hit, "the M30 MAJOR HIGH 3970.3 must be published"
  i, ev, bias, ph, pl, phb, plb = hit[0][:7]
  assert bias == 1 and pl == 3891.2, "protected low %.2f after the 3970.3 high" % pl
  assert bars[0][plb] == int(datetime.datetime(2026, 11, 2, 8, 0, tzinfo=datetime.timezone.utc).timestamp()), "the higher low of 2 Nov 08:00 UTC"
  assert new.bias == 1 and new.protected_low == 3891.2, "bias at the end of the capture"
  assert old.bias == 1 and old.protected_low == 3870.21, "v2.13 left the flip level two days back"
  assert 3870.1 < new.protected_low and abs(new.c[-1] - new.protected_low) / new.atr[-1] < abs(old.c[-1] - old.protected_low) / old.atr[-1]


def test_a_higher_low_promotion_only_moves_toward_price_and_never_before_the_pivot_is_confirmed():
  promoted = 0
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    s = m.Structure(bars, m.v214(secs))
    for i in range(s.n):
      before = (s.bias, s.protected_high, s.protected_low)
      ev = s.step_pivots(i)
      if not (ev["new_major_high"] and before[0] == 1 and s.bias == 1) and not (ev["new_major_low"] and before[0] == -1 and s.bias == -1):
        continue
      if ev["new_major_high"] and not m.isna(before[2]) and s.protected_low != before[2]:
        promoted += 1
        assert s.protected_low > before[2], "%s %s: promoted away from price" % (name, tf)
        assert i - s.protected_low_bar >= s.internal_len and s.protected_low_bar < ev["major_high_bar"]
      if ev["new_major_low"] and not m.isna(before[1]) and s.protected_high != before[1]:
        promoted += 1
        assert s.protected_high < before[1]
        assert i - s.protected_high_bar >= s.internal_len and s.protected_high_bar < ev["major_low_bar"]
  assert promoted >= 5, promoted


def test_v214_is_causal_appending_the_future_changes_nothing():
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    _, full = run(bars, m.v214(secs))
    n = len(full)
    for cut in (n // 3, n // 2, n - 7):
      _, part = run(bars, m.v214(secs), cut)
      for a, b in zip(part, full[:cut]):
        assert a[0] == b[0] and all((x != x and y != y) or x == y for x, y in zip(a[2:], b[2:])), (name, tf, a[0])


def test_the_protected_level_still_only_ratchets_toward_price_between_breaks():
  checked = 0
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    s = m.Structure(bars, m.v214(secs, structure_mode="legacy"))
    prev = (0, m.NAN, m.NAN)
    for i in range(s.n):
      ev = s.step_pivots(i)
      if s.bias == prev[0] and not ev["up_break"] and not ev["down_break"]:
        if s.bias == -1 and not m.isna(prev[1]) and not m.isna(s.protected_high):
          assert s.protected_high <= prev[1] + 1e-9
          checked += 1
        if s.bias == 1 and not m.isna(prev[2]) and not m.isna(s.protected_low):
          assert s.protected_low >= prev[2] - 1e-9
          checked += 1
      prev = (s.bias, s.protected_high, s.protected_low)
  assert checked > 1000


def test_the_developing_leg_follows_a_protected_level_that_ratchets_in_the_bias_direction():
  followed = 0
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    p = m.v214(secs)
    s = m.Structure(bars, p)
    for i in range(s.n):
      ev = s.step_pivots(i)
      if s.ratchet and s.bias == s.ratchet and not ev["up_break"] and not ev["down_break"]:
        followed += 1
        assert s.dev_dir == s.bias, (name, tf, i)
        if s.bias == -1:
          assert s.dev_low == min(bars[3][s.protected_high_bar:i + 1]) and m.isna(s.dev_high)
        else:
          assert s.dev_high == max(bars[2][s.protected_low_bar:i + 1]) and m.isna(s.dev_low)
  assert followed > 20


def test_3_nov_h1_the_phase_is_not_a_counter_trend_rebound_and_dev_is_not_the_protected_price():
  bars = rep.bars_from(SCALP, "H1")
  old, _ = run(bars, m.v213(3600))
  new, _ = run(bars, m.v214(3600))
  assert old.bias == -1 and m.phase_text(old) == "Counter-trend rebound" and old.dev_dir == 1, "the screenshot: DEV HIGH above a bear protected high"
  assert new.bias == -1 and new.dev_dir == -1 and m.phase_text(new) != "Counter-trend rebound"
  assert m.isna(new.dev_high) and new.dev_low != new.protected_high
  hi, lo, _ = new.dealing_range()
  assert hi == new.protected_high and lo == new.dev_low, "range, DEV and PROTECTED are one story"


if __name__ == "__main__":
  for name, fn in list(globals().items()):
    if name.startswith("test_"):
      fn()
      print("ok", name)

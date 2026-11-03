"""v2.13 structure rules on the committed XAU captures: publication on displacement, trailing protected level, CHoCH on it, dealing range.

Run: python tradingview/reference/test_structure_v213.py
Legacy mode must reproduce v2.12 exactly: the digests below were taken from the v2.12 model (git 3805616) over every event and the protected
levels of every bar of every capture and timeframe.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pa_model as m
import report_v213 as rep

V212_DIGESTS = {
  "live-0211/M5": "5fa9896a0693a56b", "live-0211/M15": "f35b42e4899e80e0", "live-0211/H1": "43dbe89bbbfa3d7a",
  "m15-0211/M15": "5c38ed8652fbf02c", "obwindow-1029/M5": "acd7e328f762796f", "obwindow-1029/M15": "e5e5d816c216417e",
  "obwindow-1029/H1": "d8edc9b04a27f6ef", "scalpwindow-1103/M5": "101a73b0aadde1ec", "scalpwindow-1103/M15": "a57b838f33755027",
  "scalpwindow-1103/H1": "273c47115799cb58",
}
CASES = list(rep.cases())


def digest(bars, p):
  s = m.Structure(bars, p)
  h = hashlib.sha256()
  for i in range(s.n):
    ev = s.step_pivots(i)
    for k in sorted(ev):
      v = ev[k]
      if v in (False, None) or (isinstance(v, float) and v != v):
        continue
      if k == "protected_choch":
        continue  # a v2.13 field that is always False in legacy mode
      h.update(("%d|%s|%r;" % (i, k, v)).encode())
    h.update(("%d|b%d|ph%r|pl%r;" % (i, s.bias, s.protected_high, s.protected_low)).encode())
  return h.hexdigest()[:16]


def test_legacy_mode_reproduces_v212_exactly_on_every_capture():
  for name, path, tf, secs in CASES:
    got = digest(rep.bars_from(path, tf), m.P21(chart_secs=secs))
    assert got == V212_DIGESTS["%s/%s" % (name, tf)], "%s %s: legacy mode no longer reproduces v2.12" % (name, tf)


def run_events(bars, p, upto=None):
  s = m.Structure(bars, p)
  out = []
  n = s.n if upto is None else upto
  for i in range(n):
    ev = s.step_pivots(i)
    out.append((i, ev["new_major_high"], ev["new_major_low"], ev["major_high_bar"], ev["major_low_bar"], ev["up_break"], ev["down_break"], ev["choch"],
                s.bias, s.protected_high, s.protected_low))
  return out


def test_publication_is_causal_appending_the_future_changes_nothing():
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    p = m.v213(secs)
    full = run_events(bars, p)
    for cut in (len(full) // 3, len(full) // 2, len(full) - 7):
      assert run_events(bars, p, cut) == full[:cut], "%s %s: a decision depends on bars that had not closed (cut %d)" % (name, tf, cut)


def test_a_swing_is_published_only_after_its_pivot_is_confirmed_and_its_counter_leg_reached_the_threshold():
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    p = m.v213(secs)
    s = m.Structure(bars, p)
    L = s.external_len
    published = 0
    for i in range(s.n):
      ev = s.step_pivots(i)
      for flag, bar_key, price_key, low in (("new_major_low", "major_low_bar", "major_low_price", True), ("new_major_high", "major_high_bar", "major_high_price", False)):
        if not ev[flag]:
          continue
        published += 1
        b, price = ev[bar_key], ev[price_key]
        assert i - b >= L, "%s %s: swing at %d published at %d, before its %d right bars closed" % (name, tf, b, i, L)
        right = bars[3][b + 1:i + 1] if low else bars[2][b + 1:i + 1]
        assert all(x >= price for x in right) if low else all(x <= price for x in right), "%s %s: a bar after the swing went further" % (name, tf)
        a = s.atr[b] if not m.isna(s.atr[b]) else s.atr[i]
        counter = max(bars[2][b + 1:i + 1]) - price if low else price - min(bars[3][b + 1:i + 1])
        assert counter >= a * (p.min_leg_atr + p.confirm_atr) - 1e-9, "%s %s: published before the counter-leg reached the threshold" % (name, tf)
    assert published > 10


def test_published_swings_alternate_and_come_earlier_than_in_v212():
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    s = m.Structure(bars, m.v213(secs))
    kinds = []
    for i in range(s.n):
      ev = s.step_pivots(i)
      if ev["new_major_low"]:
        kinds.append("L")
      if ev["new_major_high"]:
        kinds.append("H")
    assert all(a != b for a, b in zip(kinds, kinds[1:])), "%s %s: swings do not alternate" % (name, tf)
    old = rep.structure_row(bars, m.P21(chart_secs=secs))
    new = rep.structure_row(bars, m.v213(secs))
    assert new["lag_med"] < old["lag_med"] and new["lag_p90"] < old["lag_p90"], (name, tf, old, new)


def test_trailing_protected_level_only_ratchets_toward_price():
  checked = 0
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    s = m.Structure(bars, m.v213(secs, structure_mode="legacy"))
    prev = (0, m.NAN, m.NAN)
    for i in range(s.n):
      ev = s.step_pivots(i)
      if s.bias == prev[0] and not ev["up_break"] and not ev["down_break"]:
        if s.bias == -1 and not m.isna(prev[1]) and not m.isna(s.protected_high):
          assert s.protected_high <= prev[1] + 1e-9, "%s %s: the protected high moved away from price at %d" % (name, tf, i)
          checked += 1
        if s.bias == 1 and not m.isna(prev[2]) and not m.isna(s.protected_low):
          assert s.protected_low >= prev[2] - 1e-9, "%s %s: the protected low moved away from price at %d" % (name, tf, i)
          checked += 1
      prev = (s.bias, s.protected_high, s.protected_low)
  assert checked > 1000


def test_choch_on_the_protected_level_flips_the_bias_even_when_the_last_swing_is_further():
  further = 0
  total = 0
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    p = m.v213(secs)
    s = m.Structure(bars, p)
    for i in range(s.n):
      before = (s.bias, s.protected_high, s.protected_low, s.last_swing_high, s.last_swing_low)
      ev = s.step_pivots(i)
      if not ev["protected_choch"]:
        continue
      total += 1
      thr = s.atr[i] * (p.break_buffer + p.confirm_atr)
      assert ev["choch"] and (ev["up_break"] or ev["down_break"])
      if ev["up_break"]:
        assert before[0] == -1 and bars[4][i] > before[1] + thr and s.bias == 1, (name, tf, i)
        assert ev["broken_high"] == before[1], "the CHoCH level is the protected high, not the last swing"
        assert s.protected_low == min(bars[3][ev["broken_high_bar"]:i + 1]), "protected low = leg low since the protected high"
        further += 1 if before[3] > before[1] + thr and not s.high_broken else 0
        first = ev["break_bar"]
        assert ev["broken_high_bar"] < first <= i and bars[4][first] > before[1] + thr
        assert all(bars[4][k] <= before[1] + thr for k in range(ev["broken_high_bar"] + 1, first)), "the line must end on the FIRST close beyond"
      else:
        assert before[0] == 1 and bars[4][i] < before[2] - thr and s.bias == -1, (name, tf, i)
        assert ev["broken_low"] == before[2]
        assert s.protected_high == max(bars[2][ev["broken_low_bar"]:i + 1])
        further += 1 if before[4] < before[2] - thr and not s.low_broken else 0
  assert total >= 20, "too few protected-level CHoCH events on the captures (%d)" % total
  assert further >= 5, "no CHoCH where the last confirmed swing was further away (%d)" % further


def test_a_break_of_the_last_swing_in_agreeing_bias_stays_a_bos():
  bos = 0
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    s = m.Structure(bars, m.v213(secs))
    for i in range(s.n):
      bias = s.bias
      ev = s.step_pivots(i)
      if (ev["up_break"] and bias == 1) or (ev["down_break"] and bias == -1):
        assert not ev["choch"] and not ev["protected_choch"]
        bos += 1
  assert bos > 20


def test_dealing_range_follows_the_protected_leg_and_contains_price():
  checked = 0
  for name, path, tf, secs in CASES:
    bars = rep.bars_from(path, tf)
    p = m.v213(secs)
    s = m.Structure(bars, p)
    for i in range(s.n):
      s.step_pivots(i)
      if s.bias == 0 or m.isna(s.atr[i]):
        continue
      hi, lo, mid = s.dealing_range()
      if m.isna(hi):
        continue
      tol = s.atr[i] * (p.break_buffer + p.confirm_atr)
      # price can sit beyond the protected level by at most the confirmation buffer before the CHoCH is confirmed
      assert lo - tol - 1e-9 <= s.c[i] <= hi + tol + 1e-9, "%s %s bar %d: price outside the dealing range" % (name, tf, i)
      if s.bias == -1:
        assert hi == s.protected_high and lo == min(bars[3][s.protected_high_bar:i + 1])
      else:
        assert lo == s.protected_low and hi == max(bars[2][s.protected_low_bar:i + 1])
      checked += 1
  assert checked > 5000


def test_3_nov_shape_the_range_does_not_anchor_to_the_pre_decline_major_high():
  """H1 capture of 3 Nov 2026: v2.12 reports 4082.73 / 3870.21 ('Discount 23 %') after a 210 $ decline with no external BOS. The protected leg is the range now."""
  path = [p for n, p in rep.FIXTURES if n == "scalpwindow-1103"][0]
  bars = rep.bars_from(path, "H1")
  old = m.Structure(bars, m.P21(chart_secs=3600))
  new = m.Structure(bars, m.v213(3600))
  for i in range(old.n):
    old.step_pivots(i)
    new.step_pivots(i)
  ohi, olo, _ = old.dealing_range()
  nhi, nlo, nmid = new.dealing_range()
  assert ohi > 4080 and olo < 3880, (ohi, olo)
  assert nhi < 3970 < ohi, "the range must not anchor to the pre-decline high (%.2f)" % nhi
  assert nlo <= new.c[-1] <= nhi
  assert new.bias == -1 and nhi - nlo < 0.35 * (ohi - olo)


if __name__ == "__main__":
  for name, fn in list(globals().items()):
    if name.startswith("test_"):
      fn()
      print("ok", name)

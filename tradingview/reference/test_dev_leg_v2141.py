"""v2.14.1: a confirmed swing in the bias direction keeps the pullback leg it was published after.

A major high is published in a bull bias only after a counter-leg of at least minLegATR + confirmATR, so price is in a pullback and the developing leg is the
pullback (dev_dir == -bias). v2.14 let the higher-low promotion set the ratchet flag, which flipped the leg back to the bias side on that very bar: DEV HIGH sat
on MAJOR HIGH and the phase said trend. Only an internal BOS in the bias direction moves the developing leg now; the promotion still moves the protected level.

Run: python tradingview/reference/test_dev_leg_v2141.py        (also prints the measurement table)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import mtf
import pa_model as m
import report_v213 as rep

FIXTURE_NAMES = ("live-0211", "scalpwindow-1103")
FRAMES = ("M5", "M15", "M30", "H1")
SECS = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600}


def frames():
  """(label, fixture, frame, bars) for M5 / M15 / H1 of both fixtures and M30 resampled from each M15."""
  out = []
  for name, path in rep.FIXTURES:
    if name not in FIXTURE_NAMES:
      continue
    for tf in ("M5", "M15", "H1"):
      if (name, path, tf, SECS[tf]) in [c for c in rep.cases()]:
        bars = rep.bars_from(path, tf)
        out.append((name, tf, bars))
        if tf == "M15":
          out.append((name, "M30", mtf.resample(bars, 900, 2)))
  return out


def measure(bars, params, structure=m.Structure):
  """Confirmed swings in the bias direction (the bias after the publication bar) and how many of them leave the pullback leg hidden;
  the share of biased bars whose developing leg is a counter-trend leg; promotions that moved the protected level on a publication bar."""
  s = structure(bars, params)
  swings = hidden = promoted = biased = counter = 0
  for i in range(s.n):
    prot = (s.protected_high, s.protected_low)
    ev = s.step_pivots(i)
    for flag, want in (("new_major_high", 1), ("new_major_low", -1)):
      if ev[flag] and s.bias == want:
        swings += 1
        hidden += 0 if s.dev_dir == -want else 1
        moved = (s.protected_low != prot[1]) if want == 1 else (s.protected_high != prot[0])
        promoted += 1 if moved and not (m.isna(s.protected_low) and m.isna(prot[1])) else 0
    if s.bias != 0 and not m.isna(s.atr[i]):
      biased += 1
      counter += 1 if s.dev_dir == -s.bias else 0
  return dict(swings=swings, hidden=hidden, promoted=promoted, biased=biased, counter=counter)


def test_a_confirmed_swing_in_the_bias_direction_keeps_its_pullback_leg_on_the_publication_bar():
  swings = 0
  for name, tf, bars in frames():
    r = measure(bars, m.v214(SECS[tf]))
    assert r["hidden"] == 0, "%s %s: %d of %d swings published with the pullback leg hidden" % (name, tf, r["hidden"], r["swings"])
    swings += r["swings"]
  assert swings > 200, swings


def test_the_promotion_still_moves_the_protected_level_but_not_the_developing_leg():
  promoted = 0
  for name, tf, bars in frames():
    p = m.v214(SECS[tf])
    s = m.Structure(bars, p)
    for i in range(s.n):
      before = (s.bias, s.protected_high, s.protected_low, s.protected_high_bar, s.protected_low_bar)
      ev = s.step_pivots(i)
      if s.ratchet:
        continue  # an internal BOS moved the leg on this bar; covered by test_structure_v214
      if ev["new_major_high"] and before[0] == 1 and not ev["up_break"] and not ev["down_break"] and s.protected_low_bar != before[4] and not m.isna(before[2]):
        promoted += 1
        assert s.protected_low > before[2] and s.dev_dir == -1 and m.isna(s.dev_high), (name, tf, i)
      if ev["new_major_low"] and before[0] == -1 and not ev["up_break"] and not ev["down_break"] and s.protected_high_bar != before[3] and not m.isna(before[1]):
        promoted += 1
        assert s.protected_high < before[1] and s.dev_dir == 1 and m.isna(s.dev_low), (name, tf, i)
  assert promoted > 20, "too few promotions to exercise the rule (%d)" % promoted


def test_the_phase_after_a_confirmed_swing_is_a_pullback_not_trend():
  checked = 0
  for name, tf, bars in frames():
    s = m.Structure(bars, m.v214(SECS[tf]))
    for i in range(s.n):
      before = s.bias
      ev = s.step_pivots(i)
      if ((ev["new_major_high"] and before == 1) or (ev["new_major_low"] and before == -1)) and s.bias == before and not ev["up_break"] and not ev["down_break"]:
        assert s.dev_dir == -before
        assert m.phase_text(s) in ("Counter-trend pullback", "Counter-trend rebound", "Structural transition"), m.phase_text(s)
        checked += 1
  assert checked > 100


def test_3_nov_h1_an_internal_bos_still_aligns_dev_and_range_and_the_phase_is_not_a_rebound():
  path = [p for n, p in rep.FIXTURES if n == "scalpwindow-1103"][0]
  bars = rep.bars_from(path, "H1")
  s = m.Structure(bars, m.v214(3600))
  for i in range(s.n):
    s.step_pivots(i)
  assert s.bias == -1 and s.dev_dir == -1 and m.isna(s.dev_high)
  assert s.dev_low != s.protected_high and m.phase_text(s) != "Counter-trend rebound"
  hi, lo, _ = s.dealing_range()
  assert hi == s.protected_high and lo == s.dev_low, "range, DEV and PROTECTED are one story"


def test_v213_never_hid_a_pullback():
  # v2.13 has neither the promotion nor the sync; it is the reference the counter-trend share is compared to
  for name, tf, bars in frames():
    assert measure(bars, m.v213(SECS[tf]))["hidden"] == 0, (name, tf)


def table(structure_old=None):
  rows = []
  for label, fn in (("v2.13", lambda tf: m.v213(SECS[tf])), ("v2.14", None), ("v2.14.1", lambda tf: m.v214(SECS[tf]))):
    if label == "v2.14" and structure_old is None:
      continue
    agg = {tf: dict(swings=0, hidden=0, biased=0, counter=0) for tf in FRAMES}
    for name, tf, bars in frames():
      if label == "v2.14":
        r = measure(bars, structure_old.v214(SECS[tf]), structure_old.Structure)
      else:
        r = measure(bars, fn(tf))
      for k in agg[tf]:
        agg[tf][k] += r[k]
    rows.append((label, agg))
  return rows


if __name__ == "__main__":
  for name, fn in list(globals().items()):
    if name.startswith("test_"):
      fn()
      print("ok", name)
  old = None
  if len(sys.argv) > 1:
    import importlib.util
    spec = importlib.util.spec_from_file_location("pa_model_v214_old", sys.argv[1])
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
  print()
  print("%-8s | %s" % ("", " | ".join("%-26s" % tf for tf in FRAMES)))
  for label, agg in table(old):
    print("%-8s | %s" % (label, " | ".join("%-26s" % ("hidden %d/%d · counter %d%%" % (agg[tf]["hidden"], agg[tf]["swings"], round(100.0 * agg[tf]["counter"] / max(1, agg[tf]["biased"])))) for tf in FRAMES)))

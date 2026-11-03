"""The MTF row: a frame seen from a lower chart is that frame's own chart at its last closed bar.

Run: python tradingview/reference/test_mtf_v214.py
The Pine script requests the chart's own `bias` and `activeProtected` with `[1]` and `lookahead_on` (mtf.seen_from); the parity tests compare what a lower
chart sees with what the frame's own chart shows, bar by bar, on every committed capture that holds both timeframes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import mtf
import pa_model as m
import report_v213 as rep
import report_v214 as r14

PAIRS = (("M5", 300, "M15", 900), ("M15", 900, "H1", 3600), ("M5", 300, "H1", 3600))


def same(a, b):
  return a[0] == b[0] and (a[1] == b[1] or (a[1] != a[1] and b[1] != b[1]))


def pair_cases():
  for name, path in rep.FIXTURES:
    import json
    tfs = json.load(open(path))["timeframes"]
    for low, low_secs, high, high_secs in PAIRS:
      if low in tfs and high in tfs and len(tfs[low]) >= 300 and len(tfs[high]) >= 300:
        yield name, path, low, low_secs, high, high_secs


CASES = list(pair_cases())


def test_every_frame_seen_from_a_lower_chart_equals_that_frames_own_chart():
  checked = mismatches = 0
  for name, path, low, low_secs, high, high_secs in CASES:
    chart = rep.bars_from(path, low)
    frame = rep.bars_from(path, high)
    states = mtf.frame_states(frame, high_secs, m.v214)
    seen = mtf.seen_from(chart[0], frame[0], high_secs, states)
    ftimes = frame[0]
    index = {t: j for j, t in enumerate(ftimes)}
    for k, t in enumerate(chart[0]):
      # the definition: the frame's own state at the last bar that had closed when this chart bar opened
      last = -1
      lo, hi = 0, len(ftimes)
      while lo < hi:
        mid = (lo + hi) // 2
        if ftimes[mid] + high_secs <= t:
          lo = mid + 1
        else:
          hi = mid
      last = lo - 1
      if last < 0 or t - ftimes[last] > 3 * high_secs or seen[k] is None:
        continue  # before the first completed bar, or a market gap
      checked += 1
      if not same(seen[k], states[last]):
        mismatches += 1
    # and at each frame bar close: the first chart bar of the next period sees exactly that bar's state
    for j in range(len(ftimes) - 1):
      nxt = ftimes[j] + high_secs
      if nxt in set(chart[0]) and ftimes[j + 1] == nxt:
        k = chart[0].index(nxt)
        checked += 1
        if seen[k] is None or not same(seen[k], states[j]):
          mismatches += 1
  assert checked > 10000 and mismatches == 0, (checked, mismatches)


def test_a_frame_bar_that_is_still_open_never_changes_what_the_lower_chart_sees():
  for name, path, low, low_secs, high, high_secs in CASES[:3]:
    chart = rep.bars_from(path, low)
    frame = rep.bars_from(path, high)
    full = mtf.seen_from(chart[0], frame[0], high_secs, mtf.frame_states(frame, high_secs, m.v214))
    n = len(frame[0])
    for j in (n // 3, n // 2, n - 40):
      cut = tuple(series[:j] for series in frame)
      part = mtf.seen_from(chart[0], cut[0], high_secs, mtf.frame_states(cut, high_secs, m.v214))
      limit = frame[0][j - 1] + high_secs  # the chart bars up to the close of the last bar kept
      for k, t in enumerate(chart[0]):
        if t < limit and part[k] is not None:
          assert full[k] is not None and same(full[k], part[k]), (name, low, high, j, t)


def test_the_resampled_frames_equal_the_captured_ones_so_the_view_does_not_depend_on_which_feed_built_the_frame():
  for name, path, low, low_secs, high, high_secs in CASES:
    chart = rep.bars_from(path, low)
    frame = rep.bars_from(path, high)
    built = mtf.resample(chart, low_secs, high_secs // low_secs)
    idx = {t: i for i, t in enumerate(frame[0])}
    common = [(i, idx[t]) for i, t in enumerate(built[0]) if t in idx]
    assert len(common) > 20, (name, low, high, len(common))
    for i, j in common:
      assert all(abs(built[k][i] - frame[k][j]) < 1e-6 for k in (1, 2, 3, 4)), (name, low, high, built[0][i])


def test_a_frame_at_or_below_the_chart_is_not_shown():
  assert mtf.usable(300, 900) and mtf.usable(900, 3600)
  assert not mtf.usable(900, 900) and not mtf.usable(3600, 900) and not mtf.usable(3600, 3600)


def test_the_tag_appears_only_when_the_chart_disagrees_with_both_frames():
  bull, bear = (1, 0.0, 1.0), (-1, 0.0, 1.0)
  assert mtf.against_both(1, bear, bear) and mtf.against_both(-1, bull, bull)
  assert not mtf.against_both(1, bear, bull) and not mtf.against_both(1, bull, bull)
  assert not mtf.against_both(0, bear, bear) and not mtf.against_both(1, None, bear)
  assert mtf.name(900) == "M15" and mtf.name(3600) == "H1" and mtf.name(1800) == "M30" and mtf.name(14400) == "H4"


def test_the_agreement_table_of_the_review_is_reproduced_and_improved():
  bars = r14.capture_bars()
  rows = {v: r14.table(bars, fn) for v, fn in r14.VERSIONS}
  assert rows["v2.12"]["samples"] == 228
  assert (rows["v2.12"]["all_four"], rows["v2.12"]["m15_eq_h1"], rows["v2.12"]["m30_alone"]) == (42, 51, 0)
  assert (rows["v2.13"]["all_four"], rows["v2.13"]["m15_eq_h1"], rows["v2.13"]["m30_alone"]) == (26, 66, 30)
  assert rows["v2.12"]["flips"] == {"M5": 1.4, "M15": 0.7, "M30": 0.3, "H1": 0.0}
  assert rows["v2.13"]["flips"] == {"M5": 4.9, "M15": 1.1, "M30": 0.3, "H1": 0.0}
  assert rows["v2.14"]["all_four"] > rows["v2.13"]["all_four"] and rows["v2.14"]["m30_alone"] < rows["v2.13"]["m30_alone"]


if __name__ == "__main__":
  for name, fn in list(globals().items()):
    if name.startswith("test_"):
      fn()
      print("ok", name)

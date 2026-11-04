"""Rule checks for the session helpers of the reference pipeline (Asian range, session boxes) on real XAUUSD closed bars.

Run: python tradingview/reference/test_session_model.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pa_model as m
import pipeline_model as tm

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures", "xau-m15-closed-bars-20261102-0600.json")


def bars_from(path, tf):
  d = json.load(open(path))["timeframes"][tf]
  return tuple([x[k] for x in d] for k in range(5))


def test_live_asian_range_is_the_running_extreme_and_becomes_the_completed_range():
  import datetime
  bars = bars_from(FIX, "M15")
  t, o, h, l, c = bars
  hi, lo, live_hi, live_lo = tm.asia_ranges(t, h, l)
  run_h = run_l = None
  last_live = None
  checked = 0
  for i in range(len(t)):
    hour = datetime.datetime.fromtimestamp(t[i], datetime.UTC).hour
    inside = 0 <= hour < 8
    if inside:
      start = i == 0 or not (0 <= datetime.datetime.fromtimestamp(t[i - 1], datetime.UTC).hour < 8)
      run_h = h[i] if start else max(run_h, h[i])
      run_l = l[i] if start else min(run_l, l[i])
      assert live_hi[i] == run_h and live_lo[i] == run_l, "live range must be the running extreme since the session start"
      last_live = (run_h, run_l)
      checked += 1
    else:
      assert m.isna(live_hi[i]) and m.isna(live_lo[i]), "no live range outside the session"
      if last_live is not None and i > 0 and 0 <= datetime.datetime.fromtimestamp(t[i - 1], datetime.UTC).hour < 8:
        assert (hi[i], lo[i]) == last_live, "the completed range is the last live range"
  assert checked > 100


def test_session_boxes_span_each_session_high_low_in_local_time():
  import datetime
  day = datetime.datetime(2026, 11, 2, tzinfo=datetime.UTC)
  t = [int((day + datetime.timedelta(minutes=15 * k)).timestamp()) for k in range(96 * 2)]
  h = [100 + (k % 7) for k in range(len(t))]
  l = [90 - (k % 5) for k in range(len(t))]
  asia = tm.session_boxes(t, h, l, 0, 8)
  assert len(asia) == 2 and asia[0][0] == t[0] and asia[0][3] == t[31]
  assert asia[0][1] == max(h[0:32]) and asia[0][2] == min(l[0:32])
  # London 08:00-16:30 London time equals UTC in November (GMT); New York 08:00-17:00 is 13:00-22:00 UTC (EST).
  ldn = tm.session_boxes(t, h, l, 8, 16.5, "Europe/London")
  assert ldn[0][0] == t[32] and ldn[0][3] == t[65]
  ny = tm.session_boxes(t, h, l, 8, 17, "America/New_York")
  assert ny[0][0] == t[52] and ny[0][3] == t[87]
  assert ny[0][0] < ldn[0][3], "London and New York overlap by design"


def test_engine_asia_ends_at_london_open_on_both_sides_of_the_clock_change():
  import datetime
  def utc(y, mo, d, h, mi=0):
    return int(datetime.datetime(y, mo, d, h, mi, tzinfo=datetime.UTC).timestamp())
  # Fri 23 Oct 2026 is London summer time (open 07:00 UTC), Tue 27 Oct is winter time (open 08:00 UTC)
  for (y, mo, d), london_open in (((2026, 10, 23), 7), ((2026, 10, 27), 8)):
    start = utc(y, mo, d - 1, 22)  # 07:00 Tokyo
    assert tm.engine_asia(start) and not tm.engine_asia(start - 300)
    assert tm.engine_asia(utc(y, mo, d, london_open) - 300), "the last Asia bar is the one before London open"
    assert not tm.engine_asia(utc(y, mo, d, london_open)), "a bar inside London is never Asia"
  ts = [utc(2026, 10, 22, 21, 0) + k * 300 for k in range(12 * 36)]
  h = [100.0 + (k % 7) for k in range(len(ts))]
  l = [90.0 - (k % 5) for k in range(len(ts))]
  boxes = tm.session_boxes(ts, h, l, 7, 17, "Asia/Tokyo", exclude=tm.ENGINE_LONDON)
  ends = [datetime.datetime.fromtimestamp(b[3] + 300, datetime.UTC) for b in boxes]
  assert ends and ends[0].hour == 7 and ends[0].day == 23, ends
  hi, lo, live_hi, live_lo = tm.asia_ranges(ts, h, l, engine=True)
  first_done = next(k for k in range(len(ts)) if not m.isna(hi[k]))
  assert datetime.datetime.fromtimestamp(ts[first_done], datetime.UTC).hour == 7, "the completed range is published at London open"


if __name__ == "__main__":
  for name, fn in list(globals().items()):
    if name.startswith("test_"):
      fn()
      print("ok", name)

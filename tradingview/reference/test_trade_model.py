"""Rule checks for the v2.8 trade plan on real XAUUSD closed bars.

Run: python tradingview/reference/test_trade_model.py
These checks validate that the implemented rules are followed; they say nothing about whether
the rules make money.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pa_model as m
import trade_model as tm

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures", "xau-m15-closed-bars-20261102-0600.json")
LIVE = os.path.join(HERE, "fixtures", "xau-closed-bars-20261102.json")
REPO = os.path.join(HERE, "..", "..", "analysis-engine", "testdata", "obwindow-xau-20261029.json")


def bars_from(path, tf):
  d = json.load(open(path))["timeframes"][tf]
  return tuple([x[k] for x in d] for k in range(5))


def run(path, tf, secs, tp=None):
  return tm.TradePlanSim(bars_from(path, tf), m.P21(chart_secs=secs), tp).run()


CASES = [(FIX, "M15", 900), (LIVE, "M5", 300), (REPO, "M5", 300), (REPO, "M15", 900)]


def test_every_trade_follows_the_entry_rules():
  total = 0
  for mode, (path, tf, secs) in [(mode, case) for mode in ("Next key level", "Nearest level with minimum R:R") for case in CASES]:
    sim = run(path, tf, secs, tm.TradeParams(target_mode=mode))
    s, tp = sim.s, sim.tp
    for t in sim.trades:
      total += 1
      i, is_long = t.entry_bar, t.dir == 1
      atr = s.atr[i]
      assert t.entry == s.c[i], "entry must be the trigger bar close"
      rng = s.h[i] - s.l[i]
      rej = (s.c[i] > s.o[i] and min(s.o[i], s.c[i]) - s.l[i] >= tp.wick_frac * rng) if is_long else (s.c[i] < s.o[i] and s.h[i] - max(s.o[i], s.c[i]) >= tp.wick_frac * rng)
      eng = (s.c[i - 1] < s.o[i - 1] and s.c[i] > s.o[i] and s.c[i] > s.o[i - 1] and s.o[i] <= s.c[i - 1]) if is_long else (s.c[i - 1] > s.o[i - 1] and s.c[i] < s.o[i] and s.c[i] < s.o[i - 1] and s.o[i] >= s.c[i - 1])
      assert rej or eng, "no trigger candle on the entry bar"
      bottom, top = t.zone_edges
      want_stop = bottom - tp.stop_buffer_atr * atr if is_long else top + tp.stop_buffer_atr * atr
      assert abs(t.stop - want_stop) < 1e-9, "stop must sit beyond the zone's far edge plus the buffer"
      risk = abs(t.entry - t.stop)
      assert tp.min_stop_atr * atr - 1e-9 <= risk <= tp.max_stop_atr * atr + 1e-9
      reward = (t.target - t.entry) if is_long else (t.entry - t.target)
      assert reward >= tp.min_rr * risk - 1e-9 and reward <= tp.max_target_atr * atr + 1e-9
      # technical take-profit: a small buffer before a real key level, and that level is the NEXT one ahead
      assert abs(abs(t.raw_level - t.target) - tp.target_buffer_atr * atr) < 1e-9
      if mode == "Next key level":
        assert abs(abs(t.raw_level - t.entry) - t.nearest_ahead) < 1e-9, "the target skipped over a nearer key level"
      assert abs(t.rr - reward / risk) < 1e-9
      assert t.path != "Candidate"
  assert total >= 20, "fixtures no longer produce enough trades to be meaningful (%d)" % total


def test_outcomes_match_an_independent_replay_of_the_bars():
  for path, tf, secs in CASES:
    sim = run(path, tf, secs)
    s, tp = sim.s, sim.tp
    for t in sim.trades:
      risk, is_long = abs(t.entry - t.stop), t.dir == 1
      result = None
      for k in range(t.entry_bar + 1, s.n):
        stop_hit = s.l[k] <= t.stop if is_long else s.h[k] >= t.stop
        tgt_hit = s.h[k] >= t.target if is_long else s.l[k] <= t.target
        if stop_hit:
          px = min(t.stop, s.o[k]) if is_long else max(t.stop, s.o[k])
          result = (k, "SL", ((px - t.entry) if is_long else (t.entry - px)) / risk)
        elif tgt_hit:
          result = (k, "TP", abs(t.target - t.entry) / risk)
        elif k - t.entry_bar >= tp.timeout_bars:
          result = (k, "TIME", ((s.c[k] - t.entry) if is_long else (t.entry - s.c[k])) / risk)
        if result:
          break
      assert result is not None and result[:2] == (t.exit_bar, t.outcome), (tf, t.entry_bar, result, t.exit_bar, t.outcome)
      assert abs(result[2] - t.r) < 1e-9
      assert t.outcome != "SL" or t.r <= -1.0 + 1e-9, "a stop can only lose one R or more (gap)"


def test_one_trade_at_a_time_and_stats_add_up():
  for path, tf, secs in CASES:
    sim = run(path, tf, secs)
    for a, b in zip(sim.trades, sim.trades[1:]):
      assert b.entry_bar > a.exit_bar, "a new trade opened before the previous one closed"
    st = sim.stats()
    assert abs(st["total_r"] - sum(t.r for t in sim.trades)) < 1e-9
    assert st["tp"] + st["sl"] + st["time"] == st["closed"]
    assert all(t.dir in (1, -1) for t in sim.trades)


def test_a_zone_trades_only_once():
  for path, tf, secs in CASES:
    sim = run(path, tf, secs)
    keys = [(t.zone, t.zone_edges) for t in sim.trades]
    assert len(keys) == len(set(keys)), "a zone produced two trades"


def test_planned_rr_box_is_available_and_well_formed():
  shown = 0
  for path, tf, secs in CASES:
    sim = run(path, tf, secs)
    pv = tm.preview(sim)
    if pv is None:
      continue
    shown += 1
    atr, close = sim.s.atr[-1], sim.s.c[-1]
    bottom, top = pv["edges"]
    long = pv["dir"] == 1
    assert (pv["entry"] == top) if long else (pv["entry"] == bottom), "entry must be the zone's near edge"
    assert pv["stop"] < bottom if long else pv["stop"] > top, "stop must be beyond the far edge"
    assert (pv["target"] > pv["entry"]) if long else (pv["target"] < pv["entry"])
    assert abs(abs(pv["raw_level"] - pv["entry"]) - pv["nearest_ahead"]) < 1e-9, "the plan target skipped a nearer key level"
    assert abs(pv["entry"] - close) <= sim.p.zone_display_distance_atr * atr + 1e-9
  assert shown >= 2, "the planned box should be available on most charts"


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


def test_costs_lower_every_result_by_cost_over_risk():
  path, tf, secs = FIX, "M15", 900
  base = run(path, tf, secs)
  costly = run(path, tf, secs, tm.TradeParams(cost=0.5))
  assert len(base.trades) == len(costly.trades)
  for a, b in zip(base.trades, costly.trades):
    assert abs((a.r - b.r) - 0.5 / abs(a.entry - a.stop)) < 1e-9


def test_min_rr_mode_still_meets_the_minimum_and_uses_real_levels():
  path, tf, secs = FIX, "M15", 900
  far = run(path, tf, secs, tm.TradeParams(target_mode="Nearest level with minimum R:R"))
  assert far.trades
  for t in far.trades:
    risk = abs(t.entry - t.stop)
    assert abs(t.target - t.entry) >= far.tp.min_rr * risk - 1e-9
    assert abs(abs(t.raw_level - t.target) - far.tp.target_buffer_atr * far.s.atr[t.entry_bar]) < 1e-9
  nxt = run(path, tf, secs)
  assert len(nxt.trades) <= len(far.trades) + 3, "technical mode should not take more setups than the min R:R mode"


def test_stricter_filters_never_add_trades_at_the_same_bars():
  path, tf, secs = FIX, "M15", 900
  base = run(path, tf, secs)
  strict = run(path, tf, secs, tm.TradeParams(min_rr=3.0))
  assert all(t.rr >= 3.0 for t in strict.trades)
  assert len(strict.trades) <= len(base.trades) + 3  # a skipped trade can free the slot for a later one


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


# ---- v2.13 ---------------------------------------------------------------------------------------------------------------------------------

def run213(path, tf, secs, **tp_kw):
  return tm.TradePlanSim(bars_from(path, tf), m.v213(secs), tm.v213_trade(**tp_kw)).run()


def _bare_sim(ring_obstacles):
  """A sim whose only key levels are the ones the test plants: one far pool and (optionally) one unswept internal swing."""
  sim = tm.TradePlanSim(bars_from(LIVE, "M5"), m.v213(300), tm.v213_trade(ring_obstacles=ring_obstacles)).run()
  sim.zones, sim.lm.pools, sim.lm.ring = [], [], []
  s = sim.s
  s.last_swing_high = s.last_swing_low = s.previous_swing_high = s.previous_swing_low = m.NAN
  s.bias = 0
  i = s.n - 1
  for arr in (sim.asia_h, sim.asia_l, sim.asia_live_h, sim.asia_live_l):
    arr[i] = m.NAN
  return sim, i


def test_an_unswept_internal_swing_is_a_room_obstacle_and_refuses_a_target_that_pays_too_little():
  for ring_on in (False, True):
    sim, i = _bare_sim(ring_on)
    s = sim.s
    entry, risk = s.c[i], 2.0
    far = entry + 10.0
    sim.lm.pools = [m.Pool(1, far, far, i - 100, i - 90, i - 100, 1, 2, "Swing")]
    for fraction, expect_target in ((0.9, False), (1.1, True)):
      level = entry + fraction * sim.tp.min_rr * risk
      s.h[i] = min(s.h[i], level - 0.01)  # nothing after the pivot traded through it
      sim.lm.ring = [[level, i - 1, 1]]
      target, name, raw, nearest = sim.key_target(i, True, entry, risk)
      if not ring_on:
        assert name == "Swing" or name == "BSL", (name, "the far pool is the only level without the obstacle")
        assert target is not None and raw == far
      elif expect_target:
        assert target is not None and name == "iBSL" and abs(raw - level) < 1e-9, (target, name)
      else:
        assert target is None, "a swing that pays %.1f x the minimum R:R must refuse, not skip to the far pool" % fraction
  # a swing that was traded through is not an obstacle
  sim, i = _bare_sim(True)
  entry = sim.s.c[i]
  sim.s.h[i] = entry + 5.0
  sim.lm.ring = [[entry + 2.0, i - 1, 1]]
  assert sim.key_target(i, True, entry, 2.0)[0] is None


def test_the_pip_cap_refuses_and_never_tightens_the_stop():
  free = run213(FIX, "M15", 900, max_stop_pips=0.0)
  capped = run213(FIX, "M15", 900, max_stop_pips=70.0)
  tight = run213(FIX, "M15", 900, max_stop_pips=1.0)
  assert len(free.trades) >= len(capped.trades) >= 1 and len(tight.trades) == 0
  assert tight.refusals.get("stop over pip cap", 0) > 0
  for sim in (free, capped):
    for t in sim.trades:
      atr = sim.s.atr[t.entry_bar]
      bottom, top = t.zone_edges
      want = bottom - sim.tp.stop_buffer_atr * atr if t.dir == 1 else top + sim.tp.stop_buffer_atr * atr
      assert abs(t.stop - want) < 1e-9, "a stop was moved: the cap refuses, it never tightens"
  for t in capped.trades:
    assert abs(t.entry - t.stop) / capped.tp.pip_size <= 70.0 + 1e-6
  assert any(abs(t.entry - t.stop) / free.tp.pip_size > 70.0 for t in free.trades) or len(free.trades) == len(capped.trades)


def test_every_trigger_bar_that_opened_nothing_is_counted_once_by_its_first_failing_reason():
  allowed = set(tm.REFUSALS) | {"no zone touched"}
  for path, tf, secs in [(LIVE, "M5", 300), (FIX, "M15", 900), (REPO, "M5", 300)]:
    sim = run213(path, tf, secs)
    assert set(sim.refusals) <= allowed, sim.refusals
    assert sum(sim.refusals.values()) + len(sim.trades) + (1 if sim.open is not None else 0) == sim.trigger_bars, (sim.refusals, len(sim.trades), sim.trigger_bars)
    assert sim.refusals, "no refusal recorded on %s %s" % (path, tf)


def test_preview_below_the_minimum_is_flagged():
  seen = {True: 0, False: 0}
  for path, tf, secs in CASES:
    for i_back in (0, 5, 17, 40):
      sim = tm.TradePlanSim(tuple(x[:len(x) - i_back] for x in bars_from(path, tf)), m.v213(secs), tm.v213_trade()).run()
      pv = tm.preview(sim)
      if pv is not None:
        assert pv["below_min"] is (pv["rr"] < sim.tp.min_rr)
        seen[pv["below_min"]] += 1
  assert seen[True] + seen[False] >= 4, seen


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


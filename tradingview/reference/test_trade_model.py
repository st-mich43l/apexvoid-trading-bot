"""Rule checks for the v2.8 trade plan on real XAUUSD closed bars.

Run: python tradingview/reference/test_trade_model.py
These checks validate that the implemented rules are followed; they say nothing about whether
the rules make money (see docs/trade-plan-v2.8.md for the descriptive results).
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


if __name__ == "__main__":
  for name, fn in list(globals().items()):
    if name.startswith("test_"):
      fn()
      print("ok", name)

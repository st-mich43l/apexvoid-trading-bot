"""Before/after report for the v2.13 indicator on the committed XAU captures (counts, not P&L).

Run: python tradingview/reference/report_v213.py
`v2.12` = the model with every v2.13 switch off, `v2.13` = the Pine defaults (pa_model.v213, trade_model.v213_trade).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pa_model as m
import trade_model as tm

HERE = os.path.dirname(__file__)
ENGINE = os.path.join(HERE, "..", "..", "analysis-engine", "testdata")
FIXTURES = [
  ("live-0211", os.path.join(HERE, "fixtures", "xau-closed-bars-20261102.json")),
  ("m15-0211", os.path.join(HERE, "fixtures", "xau-m15-closed-bars-20261102-0600.json")),
  ("obwindow-1029", os.path.join(ENGINE, "obwindow-xau-20261029.json")),
  ("scalpwindow-1103", os.path.join(ENGINE, "scalpwindow-xau-20261103.json")),
]
TFS = (("M5", 300), ("M15", 900), ("H1", 3600))


def cases():
  for name, path in FIXTURES:
    tfs = json.load(open(path))["timeframes"]
    for tf, secs in TFS:
      if tf in tfs and len(tfs[tf]) >= 300:
        yield name, path, tf, secs


def bars_from(path, tf):
  d = json.load(open(path))["timeframes"][tf]
  return tuple([x[k] for x in d] for k in range(5))


def quantile(xs, f):
  xs = sorted(xs)
  return xs[int(f * (len(xs) - 1))] if xs else None


def structure_row(bars, p):
  s = m.Structure(bars, p)
  swings = bos = choch = 0
  lags, dist = [], []
  for i in range(s.n):
    ev = s.step_pivots(i)
    for k, bk in (("new_major_high", "major_high_bar"), ("new_major_low", "major_low_bar")):
      if ev[k]:
        swings += 1
        lags.append(i - ev[bk])
    if ev["up_break"] or ev["down_break"]:
      choch += 1 if ev["choch"] else 0
      bos += 0 if ev["choch"] else 1
    if s.bias != 0 and not m.isna(s.atr[i]):
      pr = s.protected_high if s.bias == -1 else s.protected_low
      if not m.isna(pr):
        dist.append(abs(s.c[i] - pr) / s.atr[i])
  return dict(swings=swings, lag_med=quantile(lags, .5), lag_p90=quantile(lags, .9), bos=bos, choch=choch,
              dist_med=round(quantile(dist, .5), 1) if dist else None, dist_max=round(max(dist), 1) if dist else None)


def zone_row(bars, p):
  pipe = m.V21Pipeline(bars, p)
  pipe.run()
  c = pipe.counts
  return dict(sd_added=c["sd_added"], sd_scan_touched=c.get("sd_scan_touched", 0), sd_scan_invalid=c.get("sd_scan_invalid", 0),
              sd_drawn_tested=c["sd_drawn_tested"], sd_rejected=c["sd_rejected"], fvg_added=c["fvg_added"], ob_accepted=pipe.ob_stats["added"])


def late_ob_origin_differs(bars, secs):
  """External breaks published late (break bar before the publication bar) whose order block origin changes when the search is anchored to the break bar."""
  out = [0, 0]
  runs = []
  for anchored in (False, True):
    p = m.v213(secs, ob_break_anchor=anchored, structure_mode="legacy", protected_mode="legacy", range_mode="v212", fresh_sd=False, fvg_def="v212")
    pipe = m.V21Pipeline(bars, p).run()
    runs.append(pipe.ob_dbg)
  late = 0
  differs = 0
  a, b = runs
  s = m.Structure(bars, m.P21(chart_secs=secs))
  late_bars = set()
  for i in range(s.n):
    ev = s.step_pivots(i)
    if (ev["up_break"] or ev["down_break"]) and ev.get("break_bar", i) != i:
      late_bars.add(i)
  for x, y in zip(a, b):
    if x["bar"] in late_bars and x["cls"] == 2:
      late += 1
      differs += 1 if (x["src"], x["ok"]) != (y["src"], y["ok"]) else 0
  return differs, late


def fvg_counts(bars, secs):
  out = {}
  for name, kw in (("v2.12", dict(fvg_def="v212")), ("loose+dir", dict(fvg_def="loose")), ("engine", dict(fvg_def="engine"))):
    out[name] = zone_row(bars, m.P21(chart_secs=secs, **kw))["fvg_added"]
  return out


class _Births(tm.TradePlanSim):
  """Counts the qualification path of every zone on the bar it is created (the snapshot at the last bar only sees survivors)."""

  def lifecycle(self, i):
    super().lifecycle(i)
    if not hasattr(self, "births"):
      self.births = {}
    for z in self.zones:
      if z.born == i:
        path = self.poi_path(z, not z.touched)
        self.births[path] = self.births.get(path, 0) + 1


def poi_paths(bars, secs, version):
  p = m.v213(secs) if version == "v2.13" else m.P21(chart_secs=secs)
  tp = tm.v213_trade() if version == "v2.13" else tm.TradeParams()
  sim = _Births(bars, p, tp).run()
  return dict(sorted(getattr(sim, "births", {}).items())), sim


def trade_row(bars, secs, version):
  counts, sim = poi_paths(bars, secs, version)
  st = sim.stats()
  return counts, dict(trades=st["closed"], tp=st["tp"], sl=st["sl"], time=st["time"], refusals=dict(sorted(sim.refusals.items(), key=lambda kv: -kv[1])))


def main():
  for name, path, tf, secs in cases():
    bars = bars_from(path, tf)
    print("=== %s %s (%d bars)" % (name, tf, len(bars[0])))
    print("structure v2.12", structure_row(bars, m.P21(chart_secs=secs)))
    print("structure v2.13", structure_row(bars, m.v213(secs)))
    print("zones     v2.12", zone_row(bars, m.P21(chart_secs=secs)))
    print("zones     v2.13", zone_row(bars, m.v213(secs)))
    print("late ob origin differs/late external breaks", late_ob_origin_differs(bars, secs))
    print("fvg", fvg_counts(bars, secs))
    for version in ("v2.12", "v2.13"):
      paths, row = trade_row(bars, secs, version)
      print("poi paths at birth %s" % version, paths)
      print("trades    %s" % version, row)


if __name__ == "__main__":
  main()

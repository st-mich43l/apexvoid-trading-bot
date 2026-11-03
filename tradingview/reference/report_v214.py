"""Before/after report for the v2.14 indicator: multi-timeframe coherence on the 3 Nov XAU capture (counts, not P&L).

Run: python tradingview/reference/report_v214.py
Samples the state of the four frames (M5 / M15 / M30 / H1, each the indicator on its own chart) at every half-hour close of the last 7 days of
analysis-engine/testdata/scalpwindow-xau-20261103.json (M30 resampled from M15) and counts how often they agree, for the v2.12 model, the v2.13 model and
the v2.14 model.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import mtf
import pa_model as m
import report_v213 as rep

FRAMES = (("M5", 300), ("M15", 900), ("M30", 1800), ("H1", 3600))
VERSIONS = (("v2.12", lambda secs: m.P21(chart_secs=secs)), ("v2.13", m.v213), ("v2.14", m.v214))
DAY = 86400


def capture_bars():
  path = [p for n, p in rep.FIXTURES if n == "scalpwindow-1103"][0]
  m5, m15, h1 = (rep.bars_from(path, tf) for tf in ("M5", "M15", "H1"))
  return {"M5": m5, "M15": m15, "M30": mtf.resample(m15, 900, 2), "H1": h1}


def all_states(bars, params_fn):
  return {name: mtf.frame_states(bars[name], secs, params_fn) for name, secs in FRAMES}


def last_closed(times, secs, at):
  """Index of the last bar of a frame whose close is at or before `at`, or -1."""
  lo, hi = 0, len(times)
  while lo < hi:
    mid = (lo + hi) // 2
    if times[mid] + secs <= at:
      lo = mid + 1
    else:
      hi = mid
  return lo - 1


def sample_times(bars, days=7):
  t30 = bars["M30"][0]
  end = t30[-1] + 1800
  return [t + 1800 for t in t30 if t + 1800 > end - days * DAY]


def agreement(bars, states, at):
  out = {}
  for name, secs in FRAMES:
    j = last_closed(bars[name][0], secs, at)
    out[name] = states[name][j] if j >= 0 else None
  return out


def table(bars, params_fn, days=7):
  states = all_states(bars, params_fn)
  times = sample_times(bars, days)
  n = all4 = m15h1 = m30_alone = 0
  for at in times:
    seen = agreement(bars, states, at)
    if any(v is None or v[0] == 0 for v in seen.values()):
      continue
    n += 1
    b = {k: v[0] for k, v in seen.items()}
    all4 += len(set(b.values())) == 1
    m15h1 += b["M15"] == b["H1"]
    m30_alone += b["M15"] == b["H1"] != b["M30"]
  end = bars["M30"][0][-1] + 1800
  flips = {}
  for name, secs in FRAMES:
    t = bars[name][0]
    seq = [(t[i] + secs, states[name][i][0]) for i in range(len(t)) if t[i] + secs > end - days * DAY]
    flips[name] = round(sum(1 for a, b in zip(seq, seq[1:]) if a[1] != b[1] and a[1] != 0) / days, 1)
  return dict(samples=n, all_four=round(100.0 * all4 / n), m15_eq_h1=round(100.0 * m15h1 / n), m30_alone=round(100.0 * m30_alone / n), flips=flips)


def protected_distance(bars, params_fn):
  out = {}
  for name, secs in FRAMES:
    dist = []
    b = bars[name]
    s = m.Structure(b, params_fn(secs))
    for i in range(s.n):
      s.step_pivots(i)
      if s.bias != 0 and not m.isna(s.atr[i]):
        pr = s.protected_high if s.bias == -1 else s.protected_low
        if not m.isna(pr):
          dist.append(abs(s.c[i] - pr) / s.atr[i])
    out[name] = (round(rep.quantile(dist, .5), 1), round(max(dist), 1)) if dist else None
  return out


def main():
  bars = capture_bars()
  print("| | " + " | ".join(v for v, _ in VERSIONS) + " |\n|---|" + "---|" * len(VERSIONS))
  rows = {v: table(bars, fn) for v, fn in VERSIONS}
  print("| samples | " + " | ".join(str(rows[v]["samples"]) for v, _ in VERSIONS) + " |")
  print("| all four agree | " + " | ".join("%d %%" % rows[v]["all_four"] for v, _ in VERSIONS) + " |")
  print("| M15 = H1 | " + " | ".join("%d %%" % rows[v]["m15_eq_h1"] for v, _ in VERSIONS) + " |")
  print("| M30 is the only frame against both M15 and H1 | " + " | ".join("%d %%" % rows[v]["m30_alone"] for v, _ in VERSIONS) + " |")
  print("| bias flips per day M5 / M15 / M30 / H1 | " + " | ".join(" / ".join(str(rows[v]["flips"][n]) for n, _ in FRAMES) for v, _ in VERSIONS) + " |")
  print("\nprotected distance from the close, median / max ATR")
  print("| frame | " + " | ".join(v for v, _ in VERSIONS) + " |\n|---|" + "---|" * len(VERSIONS))
  dist = {v: protected_distance(bars, fn) for v, fn in VERSIONS}
  for name, _ in FRAMES:
    print("| %s | " % name + " | ".join("%s / %s" % dist[v][name] for v, _ in VERSIONS) + " |")


if __name__ == "__main__":
  main()

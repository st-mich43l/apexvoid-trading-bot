"""Multi-timeframe structure, as the indicator shows it (v2.14, panel row MTF).

The Pine script requests the CHART's own `bias` and `activeProtected` series on another timeframe with `[1]` and `lookahead_on`, so a chart bar sees the
state at the close of the last COMPLETED bar of that frame. This module is that view: `frame_states` runs the model on one frame's own bars (what the chart of
that frame shows), `seen_from` aligns it to a lower chart the way the request does. `agreement` and `flips` are the counts of the pull request.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pa_model as m

NAN = m.NAN


def resample(bars, secs, factor):
  """Complete groups of `factor` bars of `secs` seconds aligned to factor*secs (an M30 bar from two M15 bars). The trailing incomplete group is dropped."""
  t, o, h, l, c = bars
  size = secs * factor
  out = ([], [], [], [], [])
  i = 0
  n = len(t)
  while i < n:
    start = t[i] - t[i] % size
    j = i
    while j < n and t[j] - t[j] % size == start:
      j += 1
    if j - i == factor and t[i] == start:
      for k, v in enumerate((start, o[i], max(h[i:j]), min(l[i:j]), c[j - 1])):
        out[k].append(v)
    i = j
  return out


def frame_states(bars, secs, params_fn):
  """[(bias, protected level, ATR)] after each closed bar of the frame's own chart."""
  s = m.Structure(bars, params_fn(secs))
  out = []
  for i in range(s.n):
    s.step_pivots(i)
    prot = s.protected_low if s.bias == 1 else s.protected_high if s.bias == -1 else NAN
    out.append((s.bias, prot, s.atr[i]))
  return out


def seen_from(chart_times, htf_times, htf_secs, states):
  """For every chart bar (open time) the state of the last completed bar of the requested frame: `[1]` + lookahead_on. None before the first completed bar."""
  out = []
  j = 0
  for t in chart_times:
    # the frame bar that contains this chart bar, then the one before it
    while j + 1 < len(htf_times) and htf_times[j + 1] <= t:
      j += 1
    containing = j if htf_times[j] <= t else -1
    out.append(states[containing - 1] if containing >= 1 else None)
  return out


def usable(chart_secs, frame_secs):
  """A frame at or below the chart's own is skipped."""
  return frame_secs > chart_secs


def name(secs):
  return "M%d" % (secs // 60) if secs < 3600 else "H%d" % (secs // 3600) if secs < 86400 else "D%d" % (secs // 86400)


def label(state):
  b, prot, _ = state
  return "Bull" if b == 1 else "Bear" if b == -1 else "—"


def against_both(chart_bias, a, b):
  """The chart frame disagrees with both requested frames (a one-line tag in the panel)."""
  return a is not None and b is not None and chart_bias != 0 and a[0] == b[0] != 0 and a[0] == -chart_bias

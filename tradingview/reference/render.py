"""Render before (v2.0) / after (v2.1) views of the reference model for a closed-bar capture.

These images come from the Python reference model, NOT from TradingView. They show what the
decision logic selects to display; the Pine drawing code itself is unverified until it is
compiled and inspected in TradingView.
"""
import json
import os
import sys
import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

sys.path.insert(0, os.path.dirname(__file__))
import pa_model as m

BG, FG = "#0E1320", "#C9D1E0"
BULL, BEAR, LIQ = "#00C7AE", "#F26B85", "#AEB8CC"
KIND_COLOR = {2: {1: "#5194FF", -1: "#FFA957"}, 3: {1: "#36D399", -1: "#FF7892"}}


def ts(t):
  return datetime.datetime.fromtimestamp(t, datetime.UTC).strftime("%m-%d %H:%M")


def candles(ax, bars, lo, hi):
  t, o, h, l, c = bars
  for i in range(lo, hi):
    col = BULL if c[i] >= o[i] else BEAR
    ax.vlines(i - lo, l[i], h[i], color=col, linewidth=0.7, zorder=3)
    ax.add_patch(Rectangle((i - lo - 0.3, min(o[i], c[i])), 0.6, max(abs(c[i] - o[i]), 1e-6), color=col, zorder=3))


def legacy_pools(bars, p):
  """v2.0 liquidity: one pool per internal pivot, 10 retained FIFO, removed on any penetration."""
  st = m.Structure(bars, p)
  pools = []
  for i in range(st.n):
    ev = st.step_pivots(i)
    atr = st.atr[i]
    if m.isna(atr):
      continue
    for flag, price, side in (("new_ih", ev["ih"], 1), ("new_il", ev["il"], -1)):
      if ev[flag]:
        pools.append([i - st.internal_len, price, side, i])
        if len(pools) > 10:
          pools.pop(0)
    keep = []
    for q in pools:
      if i > q[3]:
        pen = st.h[i] > q[1] + atr * p.sweep_buffer if q[2] == 1 else st.l[i] < q[1] - atr * p.sweep_buffer
        if pen:
          continue
      keep.append(q)
    pools = keep
  return pools


def draw_zone(ax, z, lo, n, v21):
  x0 = max(z.origin - lo, -1)
  w = n - lo + 5 - x0
  if z.kind == 1:
    col = BULL if z.side == 1 else BEAR
    ax.add_patch(Rectangle((x0, z.bottom), w, z.top - z.bottom, facecolor=col, alpha=0.06 if z.touched else 0.16,
                           edgecolor=col, linewidth=0.9, linestyle="--" if (v21 and z.touched) else "-", zorder=2))
    if v21:
      ax.hlines((z.top + z.bottom) / 2, x0, n - lo + 5, colors=col, linestyles=":", linewidth=0.7, alpha=0.7, zorder=2)
    label = ("iOB" if z.cls == 1 else "OB") + (" · T" if z.touched else "") if v21 else z.title
    ax.text(n - lo + 5, (z.top + z.bottom) / 2, label, color=col, fontsize=7, ha="right", va="center", zorder=5)
  else:
    col = KIND_COLOR[z.kind][z.side]
    ax.add_patch(Rectangle((x0, z.bottom), w, z.top - z.bottom, facecolor=col, alpha=0.12, edgecolor=col, linewidth=0.5, zorder=1))
    ax.text(n - lo + 5, (z.top + z.bottom) / 2, z.title, color=col, fontsize=6, ha="right", va="center", alpha=0.8, zorder=5)


def render(bars, secs, name, out, window):
  n = len(bars[0])
  lo = max(0, n - window)
  old = m.LegacyPipeline(bars, m.Params(chart_secs=secs)).run().display()
  new = m.V21Pipeline(bars, m.P21(chart_secs=secs)).run().display()
  p = m.P21(chart_secs=secs)
  st = m.Structure(bars, p)
  lm = m.LiquidityModel(st, p)
  for i in range(st.n):
    lm.step(i, st.step_pivots(i))
  close, atr = bars[4][-1], old.s.atr[-1]
  fig, axes = plt.subplots(1, 2, figsize=(17, 6.2), sharey=True, facecolor=BG)
  hi_v = max(bars[2][lo:n])
  lo_v = min(bars[3][lo:n])
  pad = (hi_v - lo_v) * 0.06
  ob_old = [z for z in old.zones if z.kind == 1]
  ob_new = [z for z in new.zones if z.kind == 1]
  titles = ["v2.0  ·  OB active %d, visible %d" % (len(ob_old), sum(1 for z in ob_old if z.visible)),
            "v2.1  ·  OB active %d, visible %d" % (len(ob_new), sum(1 for z in ob_new if z.visible))]
  for ax, title in zip(axes, titles):
    ax.set_facecolor(BG)
    ax.set_title("XAUUSD %s  ·  %s  ·  to %s" % (name, title, ts(bars[0][-1])), color=FG, fontsize=10, loc="left")
    candles(ax, bars, lo, n)
    ax.set_xlim(-2, n - lo + 14)
    ax.tick_params(colors=FG, labelsize=7)
    for sp in ax.spines.values():
      sp.set_color("#2A3350")
  # before
  ax = axes[0]
  for z in old.zones:
    if z.visible:
      draw_zone(ax, z, lo, n, False)
  for q in legacy_pools(bars, m.Params(chart_secs=secs)):
    x0 = max(q[0] - lo, -1)
    ax.hlines(q[1], x0, n - lo + 5, colors=LIQ, linestyles=":", linewidth=0.8, alpha=0.8, zorder=2)
    ax.text(n - lo + 6, q[1], "BSL" if q[2] == 1 else "SSL", color=LIQ, fontsize=6, va="center")
  # after
  ax = axes[1]
  for z in new.zones:
    if z.visible:
      draw_zone(ax, z, lo, n, True)
  for x in lm.select_visible(close, atr):
    x0 = max(x.birth - lo, -1)
    xr = n - lo + 5 if x.state == 0 else max(x.state_bar - lo, x0 + 1)
    a = 0.85 if x.state == 0 else 0.3
    ax.hlines(x.edge, x0, xr, colors=LIQ, linestyles=":", linewidth=0.9, alpha=a, zorder=2)
    if x.kind == "EQ":
      half = max((x.top - x.bottom) / 2, atr * 0.03)
      ax.add_patch(Rectangle((x0, (x.top + x.bottom) / 2 - half), xr - x0, 2 * half, facecolor=LIQ, alpha=0.18 if x.state == 0 else 0.06, edgecolor="none", zorder=1))
    name_ = ("EQH · BSL" if x.side == 1 else "EQL · SSL") if x.kind == "EQ" else ("BSL" if x.side == 1 else "SSL")
    ax.text(n - lo + 7 if x.state == 0 else xr, x.edge, name_ + ("" if x.state == 0 else " · swept" if x.state == 1 else " · taken"), color=LIQ, fontsize=6.5, va="center", alpha=1.0 if x.state == 0 else 0.5, zorder=5)
  for g in lm.grabs:
    bar, side, e, wb, wt = g
    if e >= lo:
      ax.vlines(e - lo, wb, wt, colors=BEAR if side == 1 else BULL, linewidth=2.4, alpha=0.75, zorder=4)
  axes[0].set_ylim(lo_v - pad, hi_v + pad)
  for ax in axes:
    for txt in ax.texts:
      txt.set_clip_on(True)
  step = max(1, (n - lo) // 8)
  for ax in axes:
    ax.set_xticks(range(0, n - lo, step))
    ax.set_xticklabels([ts(bars[0][lo + k]) for k in range(0, n - lo, step)], rotation=0, fontsize=6)
  fig.tight_layout()
  fig.savefig(out, dpi=110, facecolor=BG)
  plt.close(fig)


def h4(d):
  out, cur = [], None
  for x in d["H1"]:
    b = x[0] - (x[0] % 14400)
    if cur is None or cur[0] != b:
      if cur:
        out.append(cur)
      cur = [b, x[1], x[2], x[3], x[4]]
    else:
      cur[2] = max(cur[2], x[2]); cur[3] = min(cur[3], x[3]); cur[4] = x[4]
  out.append(cur)
  return tuple([x[k] for x in out] for k in range(5))


if __name__ == "__main__":
  src = os.path.join(os.path.dirname(__file__), "data", "xau-closed-bars-20261102.json")
  out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "docs", "img")
  os.makedirs(out_dir, exist_ok=True)
  d = json.load(open(src))["timeframes"]
  tfs = {"M5": (300, 160), "M15": (900, 160), "H1": (3600, 130), "H4": (14400, 90)}
  for tf, (secs, window) in tfs.items():
    bars = h4(d) if tf == "H4" else tuple([x[k] for x in d[tf]] for k in range(5))
    render(bars, secs, tf, os.path.join(out_dir, "xauusd-%s-before-after.png" % tf.lower()), window)
    print("wrote", tf)

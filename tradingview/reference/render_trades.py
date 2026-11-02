"""Render the last N paper trades of the reference trade plan as long/short position boxes.

Reference-model image, not a TradingView screenshot: it shows what the rules select and how
the boxes are laid out (target box above/below the entry, stop box on the other side).
"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

sys.path.insert(0, os.path.dirname(__file__))
import pa_model as m
import trade_model as tm
import render

BG, FG, GREEN, RED = "#0E1320", "#C9D1E0", "#00C7AE", "#F26B85"


def main(path, tf, secs, out, last=5, mode="Nearest level with minimum R:R"):
  d = json.load(open(path))["timeframes"][tf]
  bars = tuple([x[k] for x in d] for k in range(5))
  sim = tm.TradePlanSim(bars, m.P21(chart_secs=secs), tm.TradeParams(target_mode=mode)).run()
  t, o, h, l, c = bars
  shown = sim.trades[-last:]
  cols = min(3, len(shown))
  rows = (len(shown) + cols - 1) // cols
  fig, axes = plt.subplots(rows, cols, figsize=(5.6 * cols, 4.1 * rows), facecolor=BG, squeeze=False)
  for ax in axes.flat:
    ax.set_visible(False)
  for ax, tr in zip(axes.flat, shown):
    ax.set_visible(True)
    ax.set_facecolor(BG)
    lo, hi = max(0, tr.entry_bar - 28), min(len(c), tr.exit_bar + 14)
    render.candles(ax, bars, lo, hi)
    x0, x1 = tr.entry_bar - lo, tr.exit_bar - lo
    up = tr.dir == 1
    ax.add_patch(Rectangle((x0, tr.entry if up else tr.target), x1 - x0, abs(tr.target - tr.entry), facecolor=GREEN, alpha=0.22, edgecolor="none", zorder=1))
    ax.add_patch(Rectangle((x0, tr.stop if up else tr.entry), x1 - x0, abs(tr.entry - tr.stop), facecolor=RED, alpha=0.22, edgecolor="none", zorder=1))
    ax.hlines(tr.entry, x0, x1, colors="white", linewidth=0.9, alpha=0.8, zorder=2)
    ax.axhline(tr.raw_level, color="#AEB8CC", linestyle=":", linewidth=0.8, alpha=0.7)
    ax.text(0.5, 0.97, "%s %s → %s  %.1fR" % ("LONG" if up else "SHORT", tr.zone, tr.target_name, tr.rr), transform=ax.transAxes, color="white", fontsize=7.5, va="top")
    ax.text(x1 + 0.5, tr.target if tr.outcome == "TP" else tr.stop if tr.outcome == "SL" else c[tr.exit_bar], ("+%.1fR" if tr.r >= 0 else "−%.1fR") % abs(tr.r) + (" " + tr.outcome if tr.outcome == "TIME" else ""), color=GREEN if tr.r > 0 else RED, fontsize=8, va="center")
    ys = [tr.stop, tr.target] + h[lo:hi] + l[lo:hi]
    pad = (max(ys) - min(ys)) * 0.06
    ax.set_ylim(min(ys) - pad, max(ys) + pad)
    ax.set_xlim(-1, hi - lo + 6)
    ax.tick_params(colors=FG, labelsize=7)
    for sp in ax.spines.values():
      sp.set_color("#2A3350")
  st = sim.stats()
  fig.suptitle("XAUUSD %s · last %d paper trades of the reference trade plan (%s) · all %d closed: %d%% win, %+.1fR · reference-model render, not a TradingView screenshot" % (tf, len(shown), mode, st["closed"], round(100 * st["win_rate"]), st["total_r"]), color=FG, fontsize=9, x=0.01, ha="left")
  fig.tight_layout(rect=(0, 0, 1, 0.95))
  fig.savefig(out, dpi=105, facecolor=BG)


if __name__ == "__main__":
  here = os.path.dirname(__file__)
  os.makedirs(os.path.join(here, "..", "docs", "img"), exist_ok=True)
  main(os.path.join(here, "fixtures", "xau-m15-closed-bars-20261102-0600.json"), "M15", 900, os.path.join(here, "..", "docs", "img", "xauusd-m15-trade-plan.png"))
  print("wrote")

"""v2.16 key levels: a line-for-line port of analysis-engine/internal/keylevel (cluster.go: Cluster, priceClusters, canJoinCluster, roundLevels, dedupe; role.go: Role)
with the values of config/analysis.yml analysis.key_levels (cluster_atr 0.5, round_step 5.0, minimum_touches 2, maximum_cluster_span_multiple 2.0) and
analysis.strategies.key_level (breakout_accept_bars 2), plus `pine_levels`, the algorithm exactly as the Pine layer computes it (simplified clustering, stride arrays).

The wick-touch re-count of Cluster (withWickTouches) is NOT ported: it needs every bar, and the Pine layer keeps only the pivot ring. The live key_level strategy
does not use it either (legacyread builds SwingLevels with no bars), so the level set and the touches are the same with or without it for that path.
"""
import math

CLUSTER_ATR = 0.5
MIN_TOUCHES = 2
SPAN_MULTIPLE = 2.0
BREAKOUT_BARS = 2
FRACTAL_N = 2
ROUND_ATR = 0.25


def simple_atr(h, l, c, length=14):
  """The engine's canonical ATR (analysis.indicators.atr): the plain mean of the last `length` true ranges, not Wilder's smoothing (Pine: ta.sma(ta.tr, 14))."""
  tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(h))]
  return sum(tr[-length:]) / length


def default_window(chart_secs):
  """The engine's legacy_read window_bars: M5 150, M15 250, H1 400 (<= 5m, <= 15m, otherwise)."""
  return 150 if chart_secs <= 300 else 250 if chart_secs <= 900 else 400


def fractals(h, l, n=FRACTAL_N):
  """Raw fractal pivots of the bars given: the left bars strictly lower (higher for a low), the right bars not higher. Returns (index, price, kind)."""
  out = []
  for i in range(n, len(h) - n):
    hi_ok = all(h[j] < h[i] for j in range(i - n, i)) and h[i] == max(h[i - n:i + n + 1])
    lo_ok = all(l[j] > l[i] for j in range(i - n, i)) and l[i] == min(l[i - n:i + n + 1])
    if hi_ok:
      out.append((i, h[i], "high"))
    if lo_ok:
      out.append((i, l[i], "low"))
  return out


def window_swings(h, l, window, n=FRACTAL_N):
  """The fractals of the last `window` bars (the engine slices the frame to the window first), with absolute bar indices."""
  off = max(0, len(h) - window)
  return [(i + off, p, k) for i, p, k in fractals(h[off:], l[off:], n)]


def can_join(cluster, price, tolerance, max_span):
  lo, hi = min(min(cluster), price), max(max(cluster), price)
  if hi - lo > max_span:
    return False
  return all(abs(c - price) <= tolerance for c in cluster)


def price_clusters(prices, tolerance, max_span):
  clusters = []
  for p in sorted(prices):
    if clusters and can_join(clusters[-1], p, tolerance, max_span):
      clusters[-1].append(p)
    else:
      clusters.append([p])
  return clusters


def round_levels(prices, atr, step, tolerance, min_touches):
  if not prices or step <= 0:
    return []
  low, high = math.floor(min(prices) / step) * step, math.ceil(max(prices) / step) * step
  steps = int((high - low) / step) + 1
  band = max(tolerance, atr * ROUND_ATR)
  out = []
  for k in range(steps):
    price = low + k * step
    touches = sum(1 for p in prices if abs(p - price) <= band)
    if touches >= min_touches:
      out.append(dict(price=price, kind="round", touches=touches, band=tolerance))
  return out


def dedupe(levels, tolerance):
  out = []
  for lvl in sorted(levels, key=lambda x: x["price"]):
    if out and abs(out[-1]["price"] - lvl["price"]) <= max(out[-1]["band"], tolerance):
      prev = out[-1]
      out[-1] = dict(price=(prev["price"] + lvl["price"]) / 2, kind=lvl["kind"] if lvl["touches"] > prev["touches"] else prev["kind"],
                     touches=max(prev["touches"], lvl["touches"]), band=max(prev["band"], lvl["band"]))
    else:
      out.append(dict(lvl))
  return out


def cluster(prices, atr, cluster_atr=CLUSTER_ATR, round_step=5.0, min_touches=MIN_TOUCHES, span_multiple=SPAN_MULTIPLE):
  """keylevel.Cluster without candles: reaction clusters, round levels, deduped. `prices` are the swing prices."""
  tolerance = atr * max(0.0, cluster_atr)
  max_span = tolerance * max(0.0, span_multiple)
  levels = [dict(price=sum(c) / len(c), kind="reaction", touches=len(c), band=tolerance) for c in price_clusters(prices, tolerance, max_span) if len(c) >= min_touches]
  levels += round_levels(prices, atr, round_step, tolerance, min_touches)
  return dedupe(levels, tolerance)


def role(kind, band_low, band_high, closes, required=BREAKOUT_BARS):
  """keylevel.Role: consecutive closes counted from the last bar backwards that lie beyond the band."""
  required = max(1, required)

  def accepted(pred):
    n = 0
    for c in reversed(closes):
      if not pred(c):
        break
      n += 1
    return n

  above, below = accepted(lambda c: c > band_high), accepted(lambda c: c < band_low)
  k = kind.strip().lower()
  if "support" in k or "low" in k:
    return "broken_support" if below >= required else "support"
  if "resist" in k or "high" in k:
    return "broken_resistance" if above >= required else "resistance"
  if above >= required:
    return "broken_resistance"
  if below >= required:
    return "broken_support"
  return "ambiguous"


def pine_levels(pairs, atr, step, window_first_bar, cluster_atr=CLUSTER_ATR):
  """The Pine algorithm step by step. `pairs` = the ring as (bar, price); only bars >= window_first_bar are used. Clustering joins when the candidate is within
  `tol` of the cluster's first (lowest) member: with the prices sorted that is every member, and the span test (span <= 2 tol) is then implied.
  Returns (price, touches, first bar, round?) tuples."""
  tol = atr * cluster_atr
  use = [(b, p) for b, p in pairs if b >= window_first_bar]
  if not use:
    return []
  order = sorted(range(len(use)), key=lambda i: use[i][1])
  raw = []
  start, total, cnt, first = None, 0.0, 0, 0
  for q in range(len(use) + 1):
    b, p = use[order[q]] if q < len(use) else (None, None)
    if cnt > 0 and (p is None or p - start > tol):
      if cnt >= 2:
        raw.append((total / cnt, cnt, first, 0))
      cnt, total = 0, 0.0
    if p is not None:
      if cnt == 0:
        start, first = p, b
      total, cnt, first = total + p, cnt + 1, min(first, b)
  if step > 0:
    low = math.floor(use[order[0]][1] / step) * step
    steps = int((math.ceil(use[order[-1]][1] / step) * step - low) / step) + 1
    for s in range(steps):
      rp, rt, rb = low + s * step, 0, None
      for b, p in use:
        if abs(p - rp) <= max(tol, atr * ROUND_ATR):
          rt += 1
          rb = b if rb is None else min(rb, b)
      if rt >= 2:
        raw.append((rp, rt, rb, 1))
  final = []
  for p, t, b, k in sorted(raw, key=lambda x: x[0]):
    if final and abs(final[-1][0] - p) <= tol:
      fp, ft, fb, fk = final[-1]
      final[-1] = ((fp + p) / 2.0, max(ft, t), min(fb, b), k if t > ft else fk)
    else:
      final.append((p, t, b, k))
  return final


def broken(level_price, last_close, closes, tol, required=BREAKOUT_BARS):
  """Pine's one-directional run: a level above the close is broken when the last `required` closes are below its band, a level at or below the close when they are above it.
  Equal to `role(...)` being broken_* because the other run is impossible on that side."""
  above = level_price > last_close
  run = 0
  for c in reversed(closes):
    if not (c < level_price - tol if above else c > level_price + tol):
      break
    run += 1
  return run >= required


def select(levels, close, closes, tol, per_side, solid_prices, scale_ok=lambda p: True, show_broken=False):
  """The display rules in order: eligible (not broken unless shown, inside the candle-scale window), nearest first, at most `per_side` per side, then the ones within
  `tol` of a stronger object fold into it. Returns (drawn, folded)."""
  eligible = [lv for lv in levels if (show_broken or not broken(lv[0], close, closes, tol)) and scale_ok(lv[0])]
  drawn, folded, up, down = [], [], 0, 0
  for lv in sorted(eligible, key=lambda x: abs(x[0] - close)):
    side_up = lv[0] > close
    if (up if side_up else down) >= per_side:
      continue
    up, down = up + side_up, down + (not side_up)
    (folded if any(abs(s - lv[0]) <= tol for s in solid_prices) else drawn).append(lv)
  return drawn, folded

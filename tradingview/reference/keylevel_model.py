"""v2.16 / v2.16.1 key levels.

v2.16.1 (`swing_levels`, `pine_swing_levels`, `display`) is the definition the live key_level strategy reads, LegacyFrame.SwingLevels (profit_week contract):
legacyread.Stage -> techniquezone.FindSwings (fractal candidates n = 2 + zigzag filter, zigzag_pct 0, zigzag_atr_mult 1.0) -> techniquezone.KeyLevels with the median of
the window's ATR series (ATRScalar) as the tolerance base, the ATR at each swing for the round band, no wick re-count. The window is the last `window` closed bars.

v2.16 (the rest of this file): a line-for-line port of analysis-engine/internal/keylevel (cluster.go: Cluster, priceClusters, canJoinCluster, roundLevels, dedupe; role.go: Role)
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


# ---- v2.16.1: LegacyFrame.SwingLevels (techniquezone.FindSwings + KeyLevels) ---------------------------------------------------------------------------------

ZIGZAG_PCT = 0.0
ZIGZAG_ATR = 1.0


def atr_series(h, l, c, length=14):
  """techniquezone.ATRSeries: the rolling mean of the true range with min_periods = 1 (the first bar's TR is high - low)."""
  tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(h))]
  return [sum(tr[max(0, i - length + 1):i + 1]) / (i - max(0, i - length + 1) + 1) for i in range(len(h))]


def atr_scalar(series, fallback=1.0):
  """techniquezone.ATRScalar: the median of the series (NaN skipped); a non-positive median falls back."""
  clean = sorted(x for x in series if x == x)
  if not clean:
    return fallback
  n = len(clean)
  med = clean[n // 2] if n % 2 else (clean[n // 2 - 1] + clean[n // 2]) / 2
  return med if med > 0 else fallback


def fractal_candidates(h, l, n=FRACTAL_N):
  """techniquezone.fractalCandidates, ordered by index with 'high' before 'low' on one bar. Returns (index, price, kind)."""
  out = []
  for i, p, k in fractals(h, l, n):
    out.append((i, p, k))
  return sorted(out, key=lambda x: (x[0], x[2]))


def zigzag(cands, atr, pct=ZIGZAG_PCT, mult=ZIGZAG_ATR):
  """techniquezone.zigzagFilter: same kind as the last kept swing replaces it when more extreme; the opposite kind is kept only if the move is at least max(|last| x pct, ATR at its bar x mult)."""
  kept = []
  for i, p, k in cands:
    if not kept:
      kept.append((i, p, k))
      continue
    li, lp, lk = kept[-1]
    if k == lk:
      if (k == "high" and p > lp) or (k == "low" and p < lp):
        kept[-1] = (i, p, k)
      continue
    a = atr[i] if atr[i] > 0 else 0.0
    if abs(p - lp) >= max(abs(lp) * max(0.0, pct), a * max(0.0, mult)):
      kept.append((i, p, k))
  return kept


def strategy_round_levels(swings, atr, step, tolerance, min_touches):
  """techniquezone.roundLevels: the touch band is max(tolerance, ATR at the swing x 0.25)."""
  if not swings or step <= 0:
    return []
  prices = [p for _, p, _ in swings]
  low, high = math.floor(min(prices) / step) * step, math.ceil(max(prices) / step) * step
  out = []
  for k in range(int((high - low) / step) + 1):
    price = low + k * step
    touches = sum(1 for i, p, _ in swings if abs(p - price) <= max(tolerance, (atr[i] if atr[i] > 0 else 1.0) * ROUND_ATR))
    if touches >= min_touches:
      out.append(dict(price=price, kind="round", touches=touches, band=tolerance))
  return out


def swing_levels(h, l, c, window, round_step=5.0, cluster_atr=CLUSTER_ATR, min_touches=MIN_TOUCHES, span_multiple=SPAN_MULTIPLE):
  """LegacyFrame.SwingLevels of the last `window` bars. Returns dict(levels, swings (zigzag, absolute indices), tol, median_atr, atr, offset)."""
  off = max(0, len(h) - window)
  hh, ll, cc = h[off:], l[off:], c[off:]
  atr = atr_series(hh, ll, cc)
  swings = zigzag(fractal_candidates(hh, ll), atr) if len(hh) >= 2 * FRACTAL_N + 1 else []
  median = atr_scalar(atr)
  tol = median * max(0.0, cluster_atr)
  prices = [p for _, p, _ in swings]
  levels = [dict(price=sum(cl) / len(cl), kind="reaction", touches=len(cl), band=tol) for cl in price_clusters(prices, tol, tol * max(0.0, span_multiple)) if len(cl) >= min_touches]
  levels += strategy_round_levels(swings, atr, round_step, tol, min_touches)
  levels = dedupe(levels, tol)
  return dict(levels=levels, swings=[(i + off, p, k) for i, p, k in swings], tol=tol, median_atr=median, atr=atr, offset=off)


def pine_swing_levels(h, l, c, window, step):
  """The Pine algorithm for v2.16.1, step by step (window arrays, ATR series, candidates and zigzag in one pass, simplified join, stride dedupe).
  Returns (levels as (price, touches, round?), zigzag swings as (window index, price, kind +1 / -1))."""
  off = max(0, len(h) - window)
  hh, ll, cc = h[off:], l[off:], c[off:]
  w = len(hh)
  tr, av = [], []
  for i in range(w):
    tr.append(hh[i] - ll[i] if i == 0 else max(hh[i] - ll[i], max(abs(hh[i] - cc[i - 1]), abs(ll[i] - cc[i - 1]))))
    lo = max(0, i - 13)
    av.append(sum(tr[lo:i + 1]) / (i - lo + 1))
  sw = []  # [index, price, kind]
  for i in range(2, w - 2):
    cand = []
    is_h = hh[i] >= hh[i + 1] and hh[i] >= hh[i + 2] and hh[i] > hh[i - 1] and hh[i] > hh[i - 2]
    is_l = ll[i] <= ll[i + 1] and ll[i] <= ll[i + 2] and ll[i] < ll[i - 1] and ll[i] < ll[i - 2]
    if is_h:
      cand.append((hh[i], 1))
    if is_l:
      cand.append((ll[i], -1))
    for p, k in cand:
      if not sw:
        sw.append([i, p, k])
      elif k == sw[-1][2]:
        if (p > sw[-1][1]) if k > 0 else (p < sw[-1][1]):
          sw[-1] = [i, p, k]
      elif abs(p - sw[-1][1]) >= av[i]:
        sw.append([i, p, k])
  if not sw:
    return [], []
  med = sorted(av)[w // 2] if w % 2 else (sorted(av)[w // 2 - 1] + sorted(av)[w // 2]) / 2
  tol = med * 0.5
  order = sorted(range(len(sw)), key=lambda q: sw[q][1])
  raw, q = [], 0
  while q < len(order):
    start, total, cnt = sw[order[q]][1], 0.0, 0
    while q < len(order) and sw[order[q]][1] - start <= tol:
      total += sw[order[q]][1]
      cnt += 1
      q += 1
    if cnt >= 2:
      raw.append((total / cnt, cnt, 0))
  if step > 0:
    low = math.floor(sw[order[0]][1] / step) * step
    for s in range(int((math.ceil(sw[order[-1]][1] / step) * step - low) / step) + 1):
      rp = low + s * step
      rt = sum(1 for i, p, _ in sw if abs(p - rp) <= max(tol, av[i] * 0.25))
      if rt >= 2:
        raw.append((rp, rt, 1))
  fin = []
  for price, t, k in sorted(raw, key=lambda x: x[0]):
    if fin and abs(fin[-1][0] - price) <= tol:
      fp, ft, fk = fin[-1]
      fin[-1] = ((fp + price) / 2.0, max(ft, t), k if t > ft else fk)
    else:
      fin.append((price, t, k))
  return fin, sw


def origin(price, swings, tol):
  """(highs, lows, earliest index) of the zigzag swings within tol of the level: the kinds it formed from and its first touch."""
  near = [(i, k) for i, p, k in swings if abs(p - price) <= tol]
  highs, lows = sum(1 for _, k in near if k in ("high", 1)), sum(1 for _, k in near if k in ("low", -1))
  return highs, lows, (min(i for i, _ in near) if near else None)


def display(levels, swings, closes, tol, per_side, scale_ok=lambda p: True):
  """The v2.16.1 display: no level is hidden for being broken. Nearest first, at most `per_side` per side inside the candle-scale window; each selected level
  gets its side, origin, flipped and in-play flags (in play = the engine Role is not broken: the last 2 closes are not both beyond its band)."""
  last = closes[-1]
  chosen, up, down = [], 0, 0
  for lv in sorted(levels, key=lambda x: abs(x["price"] - last)):
    above = lv["price"] > last
    if not scale_ok(lv["price"]) or (up if above else down) >= per_side:
      continue
    up, down = up + above, down + (not above)
    run = 0
    for cl in reversed(closes):
      if not (cl < lv["price"] - tol if above else cl > lv["price"] + tol):
        break
      run += 1
    highs, lows, first = origin(lv["price"], swings, tol)
    formed = "resistance" if highs > lows else "support" if lows > highs else "both"
    side = "resistance" if above else "support"
    chosen.append(dict(price=lv["price"], kind=lv["kind"], touches=lv["touches"], side=side, formed=formed, flipped=formed not in (side, "both"), in_play=run < 2, first=first))
  return chosen


def captions(chosen, labels, gap, step=5.0):
  """Rule 3: a selected level keeps its line always; its caption folds into a stronger right-column label within `gap`, otherwise it is shown.
  `labels` = prices of stronger labels (PROT, pools, EQ, zone captions). Returns (shown, folded) lists of (caption, level)."""
  shown, folded = [], []
  for lv in chosen:
    text = ("KL %s" % ("%g" % (round(lv["price"] / step) * step)) if lv["kind"] == "round" else "KL×%d" % lv["touches"]) + (" ●" if lv["in_play"] else "")
    (folded if any(abs(x - lv["price"]) <= gap for x in labels) else shown).append((text, lv))
  return shown, folded


# ---- v2.17: one code path, two definitions (Engine key_level v6 / Selective v7 preview) -----------------------------------------------------------------------

V6 = dict(name="Engine (key_level v6)", cluster_atr=0.5, step=5.0, merge="average")
V7 = dict(name="Selective (v7 preview)", cluster_atr=0.25, step=10.0, merge="strongest")
MAJOR_STEP = 50.0


def definition_levels(h, l, c, window, mode, step=None):
  """The five steps of the Pine layer on the last `window` closed bars, for one definition (V6 or V7). Only the cluster tolerance, the round step and the merge differ.
  `step` overrides the definition's round step (0 = off). Returns dict(levels, swings, tol, median_atr, atr, offset, lo, hi, coverage). Each level: price, kind, touches, band,
  newest (window index of its newest member swing), origin ('support' / 'resistance' / 'mixed': the majority of the zigzag swings within tol of its price)."""
  off = max(0, len(h) - window)
  hh, ll, cc = h[off:], l[off:], c[off:]
  atr = atr_series(hh, ll, cc)
  swings = zigzag(fractal_candidates(hh, ll), atr) if len(hh) >= 2 * FRACTAL_N + 1 else []
  tol = atr_scalar(atr) * mode["cluster_atr"]
  step = mode["step"] if step is None else step
  levels = []
  for cl in clusters_of(swings, tol):
    if len(cl) >= MIN_TOUCHES:
      levels.append(dict(price=sum(s[1] for s in cl) / len(cl), kind="reaction", touches=len(cl), band=tol, newest=max(s[0] for s in cl)))
  if swings and step > 0:
    lo_p, hi_p = min(s[1] for s in swings), max(s[1] for s in swings)
    low = math.floor(lo_p / step) * step
    for k in range(int((math.ceil(hi_p / step) * step - low) / step) + 1):
      price = low + k * step
      near = [s for s in swings if abs(s[1] - price) <= max(tol, (atr[s[0]] if atr[s[0]] > 0 else 1.0) * ROUND_ATR)]
      if len(near) >= MIN_TOUCHES:
        levels.append(dict(price=price, kind="round", touches=len(near), band=tol, newest=max(s[0] for s in near)))
  out = merge_levels(levels, tol, mode["merge"])
  for v in out:
    highs, lows, _ = origin(v["price"], swings, tol)
    v["origin"] = "resistance" if highs > lows else "support" if lows > highs else "mixed"
  lo, hi = min(ll), max(hh)
  return dict(levels=out, swings=[(i + off, p, k) for i, p, k in swings], tol=tol, median_atr=tol / mode["cluster_atr"], atr=atr, offset=off, lo=lo, hi=hi, coverage=coverage(out, tol, lo, hi))


def merge_levels(levels, tol, merge):
  """Dedupe: price-sorted neighbours within tol merge. 'average' (v6): the average price, touches = max, the merged-in kind (and newest member) unless the previous has at least as many touches.
  'strongest' (v7): the stronger member's exact price (strength: touches, reaction before round, newest member), compared against the one kept (no drift)."""
  out = []
  for v in sorted(levels, key=lambda v: v["price"]):
    if out and abs(out[-1]["price"] - v["price"]) <= tol:
      prev = out[-1]
      if merge == "average":
        out[-1] = dict(price=(prev["price"] + v["price"]) / 2, kind=prev["kind"] if prev["touches"] >= v["touches"] else v["kind"], touches=max(prev["touches"], v["touches"]),
                       band=tol, newest=v["newest"] if v["touches"] > prev["touches"] else prev["newest"])
      else:
        out[-1] = dict(v if strength(v) > strength(prev) else prev)
    else:
      out.append(dict(v))
  return out


def clusters_of(swings, tol):
  """Clusters of zigzag swings sorted by price: a swing joins while it is within tol of every member and the span stays within 2 tol."""
  out = []
  for s in sorted(swings, key=lambda s: s[1]):
    if out:
      ps = [x[1] for x in out[-1]] + [s[1]]
      if max(ps) - min(ps) <= tol * SPAN_MULTIPLE and all(abs(x[1] - s[1]) <= tol for x in out[-1]):
        out[-1].append(s)
        continue
    out.append([s])
  return out


def strength(v):
  """v7 dedupe order: more touches, then reaction before round, then the more recent newest member."""
  return (v["touches"], 1 if v["kind"] == "reaction" else 0, v["newest"])


def coverage(levels, tol, lo, hi):
  """The share of the window's [low, high] inside any level band (price +- tol), all levels."""
  if not hi > lo:
    return float("nan")
  covered, edge = 0.0, lo
  for a, b in sorted((max(lo, v["price"] - tol), min(hi, v["price"] + tol)) for v in levels):
    if b > a and b > edge:
      covered += b - max(a, edge)
      edge = b
  return covered / (hi - lo)


def coverage_text(cov):
  """The wording of the tooltip: low selectivity above 40 %, selective at or below 25 %."""
  pct = "coverage %d %% of the recent range" % round(cov * 100)
  return pct + (": low selectivity" if cov > 0.40 else ": selective" if cov <= 0.25 else "")


def is_major(price, step, gold):
  """A major round level: a $50 / $100 multiple on gold, judged on the caption price (the step price)."""
  caption = round(price / step) * step if step > 0 else price
  return gold and abs(caption - round(caption / MAJOR_STEP) * MAJOR_STEP) < 0.005


def levels217(res, closes, per_side, mode, scale_ok=lambda p: True, gold=True, step=None):
  """The v2.17 display. No level is hidden for being broken. Every level gets its flags (side, origin, flipped, in_play = the engine Role is not broken: the last 2 closes
  are not both beyond its band, and in v7 a flipped level is not in play because the strategy skips it); then, nearest first, at most `per_side` per side inside the
  candle-scale window are drawn (`drawn`), in play counted for its side. A drawn level has its line transparency (in play 45, flipped 88, major round 60, else by touches
  82 / 74 / 66). Returns (every level highest price first, the drawn ones nearest first)."""
  step = mode["step"] if step is None else step
  tol, last, v7 = res["tol"], closes[-1], mode["merge"] == "strongest"
  rows = []
  for lv in res["levels"]:
    above = lv["price"] > last
    run = 0
    for cl in reversed(closes):
      if not (cl < lv["price"] - tol if above else cl > lv["price"] + tol):
        break
      run += 1
    side = "resistance" if above else "support"
    flipped = lv["origin"] not in (side, "mixed")
    in_play = run < 2 and not (v7 and flipped)
    major = lv["kind"] == "round" and is_major(lv["price"], step, gold)
    transparency = 45 if in_play else 88 if flipped else 60 if major else 66 if lv["touches"] >= 4 else 74 if lv["touches"] == 3 else 82
    caption_price = round(lv["price"] / step) * step if step > 0 else lv["price"]
    rows.append(dict(lv, side=side, flipped=flipped, in_play=in_play, major=major, transparency=transparency, caption_price=caption_price, formed=lv["origin"], drawn=False))
  chosen, up, down = [], 0, 0
  for lv in sorted(rows, key=lambda x: abs(x["price"] - last)):
    above = lv["price"] > last
    if scale_ok(lv["price"]) and (up if above else down) < per_side:
      up, down = up + above, down + (not above)
      lv["drawn"] = True
      chosen.append(lv)
  return sorted(rows, key=lambda x: -x["price"]), chosen


def display217(res, closes, per_side, mode, scale_ok=lambda p: True, gold=True, step=None):
  """The drawn levels of `levels217`, nearest first."""
  return levels217(res, closes, per_side, mode, scale_ok, gold, step)[1]


def key_text(lv):
  """The caption of one key level, price first: `3949.12 ×3`, `3950 ×6` where a round level sits on its step price, ` ●` when in play. The price is the line's own."""
  on_step = lv["kind"] == "round" and abs(lv["price"] - lv["caption_price"]) < 0.005
  return ("%g" % lv["caption_price"] if on_step else "%.2f" % lv["price"]) + " ×%d" % lv["touches"] + (" ●" if lv["in_play"] else "")


def register_keys(chosen):
  """The labels a drawn key level registers in the right-edge column: category 4 (key level), tier 2 in play else 3, priority 30."""
  return [dict(y=lv["price"], text=key_text(lv), cat=4, tier=2 if lv["in_play"] else 3, prio=30, tip="key level %.2f" % lv["price"]) for lv in chosen]


def emit_labels(regs, gap):
  """The right-edge column pass (Pine `f_emitCluster`). Labels are clustered highest first (a new cluster starts when the previous label is at least `gap` higher). The
  host of a cluster is its best member that is not a key level (lowest tier, then priority); only members of its category add words (at most 3), the other non-key
  members show as `+n`. Key levels are never folded: their captions follow the host's words as `KL p1 · p2` (highest first), or are the whole label when the cluster has no
  other member. Returns one dict(y, text, members, host) per label."""
  out, cluster = [], []

  def emit(members):
    ranking = sorted(range(len(members)), key=lambda i: -(members[i]["prio"] - members[i]["tier"] * 1000 - (1000000 if members[i]["cat"] == 4 else 0)))
    top = members[ranking[0]]
    words, keys = [], [m["text"] for m in members if m["cat"] == 4]
    for i in ranking:
      if members[i]["cat"] == top["cat"] and top["cat"] != 4 and len(words) < 3:
        words.append(members[i]["text"])
    text = " · ".join(words)
    hidden = len(members) - len(keys) - len(words)
    if hidden > 0:
      text += " +%d" % hidden
    if keys:
      text += (" · " if text else "") + "KL " + " · ".join(keys)
    out.append(dict(y=top["y"], text=text, members=members, host=top))

  prev = None
  for r in sorted(regs, key=lambda x: -x["y"]):
    if cluster and prev - r["y"] >= gap:
      emit(cluster)
      cluster = []
    cluster.append(r)
    prev = r["y"]
  if cluster:
    emit(cluster)
  return out


def panel_row(chosen, limit=40):
  """The `KL` panel value: the drawn prices by side, nearest first (in play marked `●`), truncated with `…`. Above price `▲`, below `▼`."""
  up = " · ".join("%.2f%s" % (lv["price"], "●" if lv["in_play"] else "") for lv in chosen if lv["side"] == "resistance")
  down = " · ".join("%.2f%s" % (lv["price"], "●" if lv["in_play"] else "") for lv in chosen if lv["side"] == "support")
  text = ("▲ " + up if up else "") + ("  " if up and down else "") + ("▼ " + down if down else "")
  return text[:limit - 1] + "…" if len(text) > limit else text


def panel_tooltip(rows, cov):
  """The `KL` panel tooltip: every level found, highest first, one per line, then the coverage line."""
  lines = ["%.2f · %s · %d touches · formed as %s%s%s · %s" % (lv["price"], lv["kind"], lv["touches"], lv["formed"], " · flipped" if lv["flipped"] else "", " · in play" if lv["in_play"] else "", "drawn" if lv["drawn"] else "not drawn") for lv in rows]
  return "\n".join(lines + [coverage_text(cov)])


def tooltip(lv, tol, mode, window, cov):
  """The tooltip of a key level (the Pine string, in the same order)."""
  side = lv["side"]
  parts = ["Key level %.2f ± %.2f" % (lv["price"], tol), lv["kind"], "%d touches" % lv["touches"], side]
  if lv["flipped"]:
    parts.append("formed as %s → flipped" % lv["origin"])
    if mode["merge"] == "strongest":
      parts.append("Break & Retest territory; key_level v7 skips it")
  if lv["in_play"]:
    parts.append("in play: price is still working this level")
  if lv["kind"] == "round" and abs(lv["price"] - lv["caption_price"]) > 0.005:
    parts.append("round %s, engine price merged" % ("%g" % lv["caption_price"]))
  parts.append("definition %s (SwingLevels, window %d)" % (mode["name"], window))
  parts.append(coverage_text(cov))
  return " · ".join(parts)


def pine_definition_levels(h, l, c, window, step, v7):
  """The Pine algorithm for v2.17, step by step (strides [price, touches, round, newest], simplified join, stride dedupe). Returns (levels as (price, touches, round?, newest), swings)."""
  off = max(0, len(h) - window)
  hh, ll, cc = h[off:], l[off:], c[off:]
  w = len(hh)
  tr, av = [], []
  for i in range(w):
    tr.append(hh[i] - ll[i] if i == 0 else max(hh[i] - ll[i], max(abs(hh[i] - cc[i - 1]), abs(ll[i] - cc[i - 1]))))
    lo = max(0, i - 13)
    av.append(sum(tr[lo:i + 1]) / (i - lo + 1))
  sw = []
  for i in range(2, w - 2):
    cand = []
    if hh[i] >= hh[i + 1] and hh[i] >= hh[i + 2] and hh[i] > hh[i - 1] and hh[i] > hh[i - 2]:
      cand.append((hh[i], 1))
    if ll[i] <= ll[i + 1] and ll[i] <= ll[i + 2] and ll[i] < ll[i - 1] and ll[i] < ll[i - 2]:
      cand.append((ll[i], -1))
    for p, k in cand:
      if not sw:
        sw.append([i, p, k])
      elif k == sw[-1][2]:
        if (p > sw[-1][1]) if k > 0 else (p < sw[-1][1]):
          sw[-1] = [i, p, k]
      elif abs(p - sw[-1][1]) >= av[i]:
        sw.append([i, p, k])
  if not sw:
    return [], []
  s_av = sorted(av)
  tol = (s_av[w // 2] if w % 2 else (s_av[w // 2 - 1] + s_av[w // 2]) / 2) * (0.25 if v7 else 0.5)
  order = sorted(range(len(sw)), key=lambda q: sw[q][1])
  raw, q = [], 0
  while q < len(order):
    start, total, cnt, newest = sw[order[q]][1], 0.0, 0, 0
    while q < len(order) and sw[order[q]][1] - start <= tol:
      total += sw[order[q]][1]
      cnt += 1
      newest = max(newest, sw[order[q]][0])
      q += 1
    if cnt >= 2:
      raw.append((total / cnt, cnt, 0, newest))
  if step > 0:
    low = math.floor(sw[order[0]][1] / step) * step
    for s in range(int((math.ceil(sw[order[-1]][1] / step) * step - low) / step) + 1):
      rp = low + s * step
      rt, newest = 0, 0
      for i, p, _ in sw:
        if abs(p - rp) <= max(tol, av[i] * 0.25):
          rt += 1
          newest = max(newest, i)
      if rt >= 2:
        raw.append((rp, rt, 1, newest))
  fin = []
  for price, t, k, nw in sorted(raw, key=lambda x: x[0]):
    if fin and abs(fin[-1][0] - price) <= tol:
      fp, ft, fk, fn = fin[-1]
      if v7:
        if (t, 1 - k, nw) > (ft, 1 - fk, fn):
          fin[-1] = (price, t, k, nw)
      else:
        fin[-1] = ((fp + price) / 2.0, max(ft, t), k if t > ft else fk, nw if t > ft else fn)
    else:
      fin.append((price, t, k, nw))
  return fin, sw

"""Python reference model of the v2.2 base-and-impulse supply/demand detector.

Mirrors f_baseImpulse in apexvoid_price_action.pine bar by bar so the rules can be checked
on real closed bars. It validates the algorithm only; it does not compile or draw the Pine
file. Inputs mirror the Pine defaults.
"""
from dataclasses import dataclass

NAN = float("nan")


def rma_atr(h, l, c, length=14):
  n = len(h)
  tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
  out = [NAN] * n
  if n < length:
    return out
  out[length - 1] = sum(tr[:length]) / length
  for i in range(length, n):
    out[i] = (out[i - 1] * (length - 1) + tr[i]) / length
  return out


@dataclass
class Params:
  base_max_bars: int = 5
  base_bar_range_atr: float = 0.90
  base_bar_body_atr: float = 0.45
  max_width_atr: float = 1.00
  impulse_body_atr: float = 0.80
  depart_bars: int = 3
  depart_atr: float = 1.50
  depart_ratio: float = 2.00
  pattern_filter: str = "All"  # All | Reversal only | Continuation only
  proximal: str = "Wick"  # Wick | Body
  leg_in_bars: int = 3
  max_zones: int = 12


@dataclass
class BaseZone:
  born: int
  origin: int
  top: float
  bottom: float
  side: int
  pattern: str
  base_bars: int
  depart_bars: int
  departure_atr: float
  touched: bool = False


def detect(bars, p=Params(), atr=None):
  """Returns (zones created over time, rejections list, final active zones)."""
  t, o, h, l, c = bars
  n = len(c)
  atr = atr or rma_atr(h, l, c)
  active, created, rejected = [], [], []
  stats = dict(candidates=0, accepted=0, duplicate=0, invalidated=0, evicted=0)
  for i in range(n):
    a = atr[i]
    if a != a:
      continue
    # lifecycle: touched / invalidated (close beyond the distal edge)
    keep = []
    for z in active:
      if i > z.born:
        if z.side == 1 and l[i] <= z.top or z.side == -1 and h[i] >= z.bottom:
          z.touched = True
        if (z.side == 1 and c[i] < z.bottom) or (z.side == -1 and c[i] > z.top):
          stats["invalidated"] += 1
          continue
      keep.append(z)
    active = keep
    for n_dep in range(1, p.depart_bars + 1):
      if i - n_dep - 1 - p.leg_in_bars < 0:
        break
      res = f_base(i, n_dep, bars, atr, p)
      if res is None:
        continue
      if isinstance(res, str):
        if n_dep == 1:
          rejected.append((i, res))
        continue
      stats["candidates"] += 1
      dup = any(z.side == res.side and min(res.top, z.top) - max(res.bottom, z.bottom) > 0 and
                (min(res.top, z.top) - max(res.bottom, z.bottom)) / min(res.top - res.bottom, z.top - z.bottom) >= 0.85 for z in active)
      if dup:
        stats["duplicate"] += 1
        break
      active.append(res)
      created.append(res)
      stats["accepted"] += 1
      if len(active) > p.max_zones:
        active.pop(0)
        stats["evicted"] += 1
      break
  return created, rejected, active, stats


def f_base(i, n_dep, bars, atr, p):
  """Evaluate bar i as the last departure bar of a candidate that left a base n_dep bars wide.
  Returns a BaseZone, a rejection string, or None (no base here)."""
  t, o, h, l, c = bars
  a = atr[i]
  # base: consecutive small candles immediately before the departure bars
  m, hi, lo = 0, -1e18, 1e18
  while m < p.base_max_bars:
    k = i - n_dep - m
    if k < 1:
      break
    small = (h[k] - l[k]) <= p.base_bar_range_atr * a and abs(c[k] - o[k]) <= p.base_bar_body_atr * a
    if not small:
      break
    nh, nl = max(hi, h[k]), min(lo, l[k])
    if nh - nl > p.max_width_atr * a:
      break
    hi, lo, m = nh, nl, m + 1
  if m == 0:
    return None
  height = hi - lo
  first_base = i - n_dep - m + 1  # oldest base bar
  last_base = i - n_dep           # newest base bar
  body_hi = max(max(o[k], c[k]) for k in range(first_base, last_base + 1))
  body_lo = min(min(o[k], c[k]) for k in range(first_base, last_base + 1))
  dep = range(i - n_dep + 1, i + 1)
  up = c[i - n_dep + 1] > hi
  dn = c[i - n_dep + 1] < lo
  if not (up or dn):
    return None
  bull = up
  need = max(p.depart_atr * a, p.depart_ratio * height)
  if bull:
    net = max(c[k] for k in dep) - hi
    body = max(c[k] - o[k] for k in dep)
    broke = any(c[k] < lo for k in dep)
  else:
    net = lo - min(c[k] for k in dep)
    body = max(o[k] - c[k] for k in dep)
    broke = any(c[k] > hi for k in dep)
  if broke:
    return "departure closed back through the base"
  if net < need:
    return "weak departure (%.2f ATR < %.2f)" % (net / a, need / a)
  if body < p.impulse_body_atr * a:
    return "no impulse candle (max body %.2f ATR)" % (body / a)
  leg_in = c[first_base - 1] - c[first_base - 1 - p.leg_in_bars]
  if bull:
    pattern = "DBR" if leg_in < 0 else "RBR"
  else:
    pattern = "RBD" if leg_in > 0 else "DBD"
  reversal = pattern in ("DBR", "RBD")
  if p.pattern_filter == "Reversal only" and not reversal:
    return "continuation filtered"
  if p.pattern_filter == "Continuation only" and reversal:
    return "reversal filtered"
  if bull:
    top, bottom = (hi if p.proximal == "Wick" else body_hi), lo
  else:
    top, bottom = hi, (lo if p.proximal == "Wick" else body_lo)
  return BaseZone(i, first_base, top, bottom, 1 if bull else -1, pattern, m, n_dep, net / a)

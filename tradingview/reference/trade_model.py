"""Python reference model of the v2.8 trade plan (apexvoid_price_action.pine).

Mirrors the Pine engine bar by bar on closed bars: bias-aligned qualified POI, price-action
trigger, stop beyond the zone, target at the nearest key level with at least the minimum
reward : risk, one trade at a time, paper outcomes. It validates the rules on real bars; it does
not compile or draw the Pine file, and its statistics are descriptive, not evidence of an edge.
"""
import math
from dataclasses import dataclass, field

import pa_model as m
import sd_base_model as sdb


@dataclass
class TradeParams:
  zone_kinds: str = "All qualified POIs"
  trigger: str = "Rejection or engulfing"
  trigger_bars: int = 2
  wick_frac: float = 0.40
  chase_atr: float = 0.75
  require_pd: bool = True
  stop_buffer_atr: float = 0.15
  min_stop_atr: float = 0.40
  max_stop_atr: float = 3.00
  target_mode: str = "Next key level"  # or "Nearest level with minimum R:R"
  min_rr: float = 1.5
  target_buffer_atr: float = 0.05
  max_target_atr: float = 25.0
  timeout_bars: int = 96
  cost: float = 0.0
  base_zones: bool = True


@dataclass
class Trade:
  dir: int
  entry_bar: int
  entry: float
  stop: float
  target: float
  zone: str
  zone_edges: tuple
  target_name: str
  rr: float
  path: str
  exit_bar: int = -1
  outcome: str = ""
  r: float = 0.0
  raw_level: float = 0.0
  nearest_ahead: float = 0.0


def asia_ranges(t, h, l):
  """Completed Asian (00:00-08:00 UTC) high/low as known at each bar (mirrors the Pine snapshot)."""
  n = len(t)
  hi, lo = [m.NAN] * n, [m.NAN] * n
  cur_h = cur_l = None
  done_h = done_l = m.NAN
  inside_prev = False
  import datetime
  for i in range(n):
    hour = datetime.datetime.fromtimestamp(t[i], datetime.UTC).hour
    inside = 0 <= hour < 8
    if inside and not inside_prev:
      cur_h, cur_l = h[i], l[i]
    elif inside:
      cur_h, cur_l = max(cur_h, h[i]), min(cur_l, l[i])
    if inside_prev and not inside and cur_h is not None:
      done_h, done_l = cur_h, cur_l
    inside_prev = inside
    hi[i], lo[i] = done_h, done_l
  return hi, lo


class TradePlanSim(m.V21Pipeline):
  def __init__(self, bars, p, tp=None, sdp=None):
    super().__init__(bars, p)
    self.tp = tp or TradeParams()
    self.sdp = sdp or sdb.Params()
    self.lm = m.LiquidityModel(self.s, p)
    self.asia_h, self.asia_l = asia_ranges(self.s.t, self.s.h, self.s.l)
    self.open = None
    self.trades = []
    self.skipped = dict(no_target=0, bad_stop=0)
    self.spent = set()
    self.base_stats = 0

  # ---- zone qualification (mirror of f_poiPath) --------------------------
  def poi_path(self, z, fresh):
    s, p = self.s, self.p
    has = {1: z.kind == 1, 2: z.kind == 2, 3: z.kind == 3}
    for o in self.zones:
      if o is not z and o.side == z.side:
        ov = min(z.top, o.top) - max(z.bottom, o.bottom)
        sm = min(z.top - z.bottom, o.top - o.bottom)
        if ov > 0 and sm > 0 and ov / sm >= 0.25:
          has[o.kind] = True
    if (has[1] and has[2]) or (has[1] and has[3]) or (has[2] and has[3]):
      return "Composite"
    related = s.last_break_bar is not None and z.born >= s.last_break_bar - p.ob_lookback and z.born - s.last_break_bar <= p.poi_structure_window
    if z.kind == 1 and z.cls == 2 and z.side == s.bias and related:
      return "Structure-origin OB"
    if z.kind == 3 and z.cls == 0 and z.side == s.bias:
      return "Displaced swing zone"
    if z.kind == 3 and z.cls == 1 and z.side == s.bias and fresh:
      return "Base-impulse zone"
    if z.kind == 2 and z.side == s.bias and s.last_sweep_side == z.side and s.last_sweep_bar is not None and abs(z.born - s.last_sweep_bar) <= p.poi_structure_window:
      return "Swept imbalance"
    return "Candidate"

  # ---- key-level target (mirror of f_keyTarget) --------------------------
  def key_target(self, i, is_long, entry, risk, enforce_min=True):
    s, tp = self.s, self.tp
    atr = s.atr[i]
    pool_side = 1 if is_long else -1
    levels = []
    for x in self.lm.pools:
      if x.state == 0 and x.side == pool_side:
        name = ("EQH" if is_long else "EQL") if x.kind == "EQ" else ("BSL" if is_long else "SSL") if x.kind == "Swing" else x.kind
        levels.append((x.edge, name))
    levels.append((s.last_swing_high if is_long else s.last_swing_low, "Swing high" if is_long else "Swing low"))
    levels.append((s.previous_swing_high if is_long else s.previous_swing_low, "Prev swing high" if is_long else "Prev swing low"))
    levels.append((self.asia_h[i] if is_long else self.asia_l[i], "Asia H" if is_long else "Asia L"))
    if not m.isna(s.last_swing_high) and not m.isna(s.last_swing_low):
      levels.append(((s.last_swing_high + s.last_swing_low) / 2.0, "EQ"))
    for z in self.zones:
      if z.kind != 2 and z.side == -pool_side:
        levels.append((z.bottom if is_long else z.top, z.title))
    buffer = tp.target_buffer_atr * atr
    min_d, max_d = tp.min_rr * risk, tp.max_target_atr * atr
    next_only = tp.target_mode == "Next key level" or not enforce_min
    best, best_raw, best_adj, best_name, best_level = None, 1e18, 0.0, "", None
    nearest_ahead = 1e18
    for level, name in levels:
      if m.isna(level):
        continue
      raw = (level - entry) if is_long else (entry - level)
      if raw > 0:
        nearest_ahead = min(nearest_ahead, raw)
      adj = raw - buffer
      if raw > 0 and adj <= max_d and (next_only or adj >= min_d) and raw < best_raw:
        best = level - buffer if is_long else level + buffer
        best_raw, best_adj, best_name, best_level = raw, adj, name, level
    if enforce_min and next_only and best is not None and best_adj < min_d:
      best = None
    return best, best_name, best_level, nearest_ahead

  # ---- per bar -----------------------------------------------------------
  def run(self):
    s, p = self.s, self.p
    for i in range(s.n):
      ev = s.step_pivots(i)
      if m.isna(s.atr[i]):
        continue
      self.lm.step(i, ev)
      self.v21_ob(i, ev)
      self.supply_demand(i, ev)
      if self.tp.base_zones and i > 40:
        self.base_impulse(i)
      self.fvg(i)
      self.lifecycle(i)
      self.max_store = max(self.max_store, len(self.zones))
      if i > 40:
        self.engine(i)
    return self

  def base_impulse(self, i):
    bars = (self.s.t, self.s.o, self.s.h, self.s.l, self.s.c)
    for n_dep in range(1, self.sdp.depart_bars + 1):
      if i - n_dep - 1 - self.sdp.leg_in_bars < 0:
        break
      res = sdb.f_base(i, n_dep, bars, self.s.atr, self.sdp)
      if res is None or isinstance(res, str):
        continue
      title = ("Demand · " if res.side == 1 else "Supply · ") + res.pattern
      self.add_zone(res.origin, res.top, res.bottom, res.side, 3, title, i, 1, res.pattern)
      break
    base = [z for z in self.zones if z.kind == 3 and z.cls == 1]
    while len(base) > self.sdp.max_zones:
      self.zones.remove(base.pop(0))

  def engine(self, i):
    s, tp = self.s, self.tp
    atr = s.atr[i]
    o, h, l, c = s.o, s.h, s.l, s.c
    closed_now = False
    t = self.open
    if t is not None and i > t.entry_bar:
      risk = abs(t.entry - t.stop)
      is_long = t.dir == 1
      stop_touched = l[i] <= t.stop if is_long else h[i] >= t.stop
      target_touched = h[i] >= t.target if is_long else l[i] <= t.target
      timed_out = i - t.entry_bar >= tp.timeout_bars
      outcome, exit_price = "", None
      if stop_touched:
        outcome, exit_price = "SL", (min(t.stop, o[i]) if is_long else max(t.stop, o[i]))
      elif target_touched:
        outcome, exit_price = "TP", t.target
      elif timed_out:
        outcome, exit_price = "TIME", c[i]
      if outcome:
        t.r = ((exit_price - t.entry) if is_long else (t.entry - exit_price)) / risk - tp.cost / risk
        t.exit_bar, t.outcome = i, outcome
        self.trades.append(t)
        self.open = None
        closed_now = True
    if self.open is not None or closed_now or s.bias == 0 or not self.zones:
      return
    is_long = s.bias == 1
    rng = h[i] - l[i]
    wick_low, wick_high = min(o[i], c[i]) - l[i], h[i] - max(o[i], c[i])
    rejection = rng > 0 and ((c[i] > o[i] and wick_low >= tp.wick_frac * rng) if is_long else (c[i] < o[i] and wick_high >= tp.wick_frac * rng))
    engulf = (c[i - 1] < o[i - 1] and c[i] > o[i] and c[i] > o[i - 1] and o[i] <= c[i - 1]) if is_long else (c[i - 1] > o[i - 1] and c[i] < o[i] and c[i] < o[i - 1] and o[i] >= c[i - 1])
    triggered = rejection if tp.trigger == "Rejection wick" else engulf if tp.trigger == "Engulfing" else (rejection or engulf)
    if not triggered:
      return
    eq = (s.last_swing_high + s.last_swing_low) / 2.0 if not m.isna(s.last_swing_high) and not m.isna(s.last_swing_low) else m.NAN
    best_rr, pick = 0.0, None
    saw_no_target = saw_bad_stop = False
    for z in self.zones:
      allowed = tp.zone_kinds == "All qualified POIs" or (tp.zone_kinds == "Order blocks and supply/demand" and z.kind != 2) or (tp.zone_kinds == "Order blocks only" and z.kind == 1)
      if (z.born, z.top) in self.spent:
        continue
      if not (allowed and z.side == s.bias and i > z.born and z.first_touch >= 0 and i - z.first_touch <= tp.trigger_bars):
        continue
      near = (c[i] - z.top <= tp.chase_atr * atr) if is_long else (z.bottom - c[i] <= tp.chase_atr * atr)
      on_side = c[i] > z.bottom if is_long else c[i] < z.top
      mid = (z.top + z.bottom) / 2.0
      pd_ok = (not tp.require_pd) or m.isna(eq) or (mid <= eq if is_long else mid >= eq)
      if not (near and on_side and pd_ok):
        continue
      path = self.poi_path(z, True)
      if path == "Candidate":
        continue
      stop = z.bottom - tp.stop_buffer_atr * atr if is_long else z.top + tp.stop_buffer_atr * atr
      risk = abs(c[i] - stop)
      if not (tp.min_stop_atr * atr <= risk <= tp.max_stop_atr * atr):
        saw_bad_stop = True
        continue
      target, name, level, nearest = self.key_target(i, is_long, c[i], risk)
      if target is None:
        saw_no_target = True
        continue
      rr = abs(target - c[i]) / risk
      if rr > best_rr:
        best_rr, pick = rr, (z, stop, target, name, path, level, nearest)
    if pick is None:
      if saw_no_target:
        self.skipped["no_target"] += 1
      elif saw_bad_stop:
        self.skipped["bad_stop"] += 1
      return
    z, stop, target, name, path, level, nearest = pick
    self.spent.add((z.born, z.top))
    self.open = Trade(s.bias, i, c[i], stop, target, z.title, (z.bottom, z.top), name, best_rr, path, raw_level=level, nearest_ahead=nearest)

  # ---- statistics --------------------------------------------------------
  def stats(self):
    rs = [t.r for t in self.trades]
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r < 0]
    eq = peak = dd = 0.0
    for r in rs:
      eq += r
      peak = max(peak, eq)
      dd = max(dd, peak - eq)
    return dict(closed=len(rs), open=self.open is not None, wins=len(wins), win_rate=(len(wins) / len(rs) if rs else m.NAN),
                total_r=sum(rs), avg_r=(sum(rs) / len(rs) if rs else m.NAN), profit_factor=(sum(wins) / sum(losses) if losses else m.NAN),
                max_dd_r=dd, tp=sum(1 for t in self.trades if t.outcome == "TP"), sl=sum(1 for t in self.trades if t.outcome == "SL"),
                time=sum(1 for t in self.trades if t.outcome == "TIME"), skipped=dict(self.skipped))


def preview(sim, i=None):
  """Planned RR box at bar i (default: last bar): nearest qualified POI on the bias side, entry at its
  near edge, stop beyond the far edge, take-profit at the next key level (R:R below the trade minimum
  is allowed). Mirrors f_renderTradePreview. Returns a dict or None."""
  s, tp, p = sim.s, sim.tp, sim.p
  i = s.n - 1 if i is None else i
  if sim.open is not None or s.bias == 0:
    return None
  atr = s.atr[i]
  is_long = s.bias == 1
  eq = (s.last_swing_high + s.last_swing_low) / 2.0 if not m.isna(s.last_swing_high) and not m.isna(s.last_swing_low) else m.NAN
  band_hi, band_lo = max(s.h[-p.scale_lookback_bars:]), min(s.l[-p.scale_lookback_bars:])
  pick, best_gap = None, 1e18
  for z in sim.zones:
    allowed = tp.zone_kinds == "All qualified POIs" or (tp.zone_kinds == "Order blocks and supply/demand" and z.kind != 2) or (tp.zone_kinds == "Order blocks only" and z.kind == 1)
    if (z.born, z.top) in sim.spent or not allowed or z.side != s.bias:
      continue
    mid = (z.top + z.bottom) / 2.0
    edge = z.top if is_long else z.bottom
    gap = abs(s.c[i] - edge)
    right_side = mid <= s.c[i] if is_long else mid >= s.c[i]
    pd_ok = (not tp.require_pd) or m.isna(eq) or (mid <= eq if is_long else mid >= eq)
    if right_side and pd_ok and gap < best_gap and gap <= p.zone_display_distance_atr * atr and p.scale_ok(edge, s.c[i], atr, band_hi, band_lo) and sim.poi_path(z, not z.touched) != "Candidate":
      pick, best_gap = z, gap
  if pick is None:
    return None
  entry = pick.top if is_long else pick.bottom
  stop = pick.bottom - tp.stop_buffer_atr * atr if is_long else pick.top + tp.stop_buffer_atr * atr
  risk = abs(entry - stop)
  if not (tp.min_stop_atr * atr <= risk <= tp.max_stop_atr * atr):
    return None
  target, name, level, nearest = sim.key_target(i, is_long, entry, risk, enforce_min=False)
  if target is None:
    return None
  return dict(dir=s.bias, entry=entry, stop=stop, target=target, rr=abs(target - entry) / risk, zone=pick.title, target_name=name, edges=(pick.bottom, pick.top), nearest_ahead=nearest, raw_level=level)

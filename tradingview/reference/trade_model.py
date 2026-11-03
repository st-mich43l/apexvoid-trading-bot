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
  # v2.13
  ring_obstacles: bool = False   # unswept internal swings count as key levels / room obstacles
  max_stop_pips: float = 0.0     # 0 = off; a stop over the cap refuses, it is never tightened
  pip_size: float = 0.1          # price per pip (XAU 0.1; the indicator derives 10 x mintick elsewhere)
  asia_mode: str = "v212"        # "v212" (00:00-08:00 UTC) | "engine" (07:00 Tokyo to London open)


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
  zone_born: int = -1
  zone_first_touch: int = -1


# The engine session calendar (analysis-engine/internal/session, config analysis.sessions.calendar): Asia opens at 07:00 Tokyo and runs to
# London open; London opens at 08:00 London; New York at 09:00 New York. Session hours are wall-clock in their own zone, so they follow
# daylight saving (London open is 07:00 UTC in BST, 08:00 in GMT).
ENGINE_ASIA = (7.0, 17.0, "Asia/Tokyo")
ENGINE_LONDON = (8.0, 16.5, "Europe/London")
ENGINE_NY = (9.0, 17.0, "America/New_York")


def in_session(ts, window):
  """True when Unix second `ts` is inside (start_hour, end_hour, tz) in that zone's wall clock."""
  import datetime
  import zoneinfo
  start, end, tz = window
  local = datetime.datetime.fromtimestamp(ts, datetime.UTC).astimezone(zoneinfo.ZoneInfo(tz))
  minutes = local.hour * 60 + local.minute
  return start * 60 <= minutes < end * 60


def engine_asia(ts):
  """Asia as the engine draws it: the Tokyo morning, ended early by London open (a bar inside London is never Asia)."""
  return in_session(ts, ENGINE_ASIA) and not in_session(ts, ENGINE_LONDON)


REFUSALS = ("not a POI", "wrong premium/discount side", "entry too far from zone", "stop outside limits", "stop over pip cap", "no target room")


def asia_ranges(t, h, l, engine=False):
  """Completed and live Asian high/low as known at each bar (mirrors the Pine snapshot). engine=False is v2.12 (00:00-08:00 UTC)."""
  n = len(t)
  hi, lo = [m.NAN] * n, [m.NAN] * n
  live_hi, live_lo = [m.NAN] * n, [m.NAN] * n
  cur_h = cur_l = None
  done_h = done_l = m.NAN
  inside_prev = False
  import datetime
  for i in range(n):
    if engine:
      inside = engine_asia(t[i])
    else:
      inside = 0 <= datetime.datetime.fromtimestamp(t[i], datetime.UTC).hour < 8
    if inside and not inside_prev:
      cur_h, cur_l = h[i], l[i]
    elif inside:
      cur_h, cur_l = max(cur_h, h[i]), min(cur_l, l[i])
    if inside_prev and not inside and cur_h is not None:
      done_h, done_l = cur_h, cur_l
    inside_prev = inside
    hi[i], lo[i] = done_h, done_l
    if inside:
      live_hi[i], live_lo[i] = cur_h, cur_l
  return hi, lo, live_hi, live_lo


def session_boxes(t, h, l, start_hour, end_hour, tz="UTC", exclude=None):
  """Session range boxes as the indicator draws them: (first_bar_time, high, low, last_bar_time). `exclude` is an optional
  (start, end, tz) window whose bars never belong to the session (Asia ends early at London open)."""
  import datetime
  import zoneinfo
  zone = zoneinfo.ZoneInfo(tz)
  boxes = []
  current = None
  for i in range(len(t)):
    local = datetime.datetime.fromtimestamp(t[i], datetime.UTC).astimezone(zone)
    minutes = local.hour * 60 + local.minute
    inside = start_hour * 60 <= minutes < end_hour * 60 and not (exclude is not None and in_session(t[i], exclude))
    if inside and current is None:
      current = [t[i], h[i], l[i], t[i]]
    elif inside:
      current[1], current[2], current[3] = max(current[1], h[i]), min(current[2], l[i]), t[i]
    elif current is not None:
      boxes.append(tuple(current))
      current = None
  if current is not None:
    boxes.append(tuple(current))
  return boxes


class TradePlanSim(m.V21Pipeline):
  def __init__(self, bars, p, tp=None, sdp=None):
    super().__init__(bars, p)
    self.tp = tp or TradeParams()
    self.sdp = sdp or sdb.Params()
    self.lm = m.LiquidityModel(self.s, p)
    self.asia_h, self.asia_l, self.asia_live_h, self.asia_live_l = asia_ranges(self.s.t, self.s.h, self.s.l, engine=self.tp.asia_mode == "engine")
    self.refusals = {}
    self.trigger_bars = 0
    self.open = None
    self.trades = []
    self.skipped = dict(no_target=0, bad_stop=0)
    self.spent = set()
    self.base_stats = 0

  # ---- zone qualification (mirror of f_poiPath) --------------------------
  def poi_path(self, z, fresh):
    s, p = self.s, self.p
    has = self.components(z)
    if (has[1] and has[2]) or (has[1] and has[3]) or (has[2] and has[3]):
      return "Composite"
    related = s.last_break_bar is not None and z.born >= s.last_break_bar - p.ob_lookback and z.born - s.last_break_bar <= p.poi_structure_window
    if z.kind == 1 and z.cls == 2 and z.side == s.bias and related:
      return "Structure-origin OB"
    if z.kind == 3 and z.cls == 0 and z.side == s.bias:
      return "Displaced swing zone"
    if z.kind == 3 and z.cls == 1 and z.side == s.bias and fresh:
      return "Base-impulse zone"
    if self.swept_imbalance(z):
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
    levels.append((self.asia_live_h[i] if is_long else self.asia_live_l[i], "Asia H live" if is_long else "Asia L live"))
    levels.append((s.dealing_range()[2], "EQ"))
    for z in self.zones:
      if z.kind != 2 and z.side == -pool_side:
        levels.append((z.bottom if is_long else z.top, z.title))
    if tp.ring_obstacles:
      # v2.13: every unswept internal swing ahead is a room obstacle (the iBSL / iSSL intact test: no later bar traded through it)
      for price, bar, side in self.lm.ring:
        if side == pool_side and ((price > entry) if is_long else (price < entry)) and bar < i:
          if all((s.h[k] <= price) if is_long else (s.l[k] >= price) for k in range(bar + 1, i + 1)):
            levels.append((price, "iBSL" if is_long else "iSSL"))
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

  def can_trigger(self, z, i):
    """A zone can start a trade when it was first touched after it existed, within the trigger window. A swing zone drawn already tested
    (first touch before its publication) is never a trigger: it was retested before the indicator showed it."""
    return i > z.born and z.first_touch >= z.born and z.first_touch >= 0 and i - z.first_touch <= self.tp.trigger_bars

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
    self.trigger_bars += 1
    eq = s.dealing_range()[2]
    best_rr, pick = 0.0, None
    saw_no_target = saw_bad_stop = False
    deepest, deepest_reason = -1, None   # refusal histogram: the zone that got furthest through the checks decides the bar's reason
    for z in self.zones:
      allowed = tp.zone_kinds == "All qualified POIs" or (tp.zone_kinds == "Order blocks and supply/demand" and z.kind != 2) or (tp.zone_kinds == "Order blocks only" and z.kind == 1)
      if (z.born, z.top) in self.spent:
        continue
      if not (allowed and z.side == s.bias and self.can_trigger(z, i)):
        continue
      near = (c[i] - z.top <= tp.chase_atr * atr) if is_long else (z.bottom - c[i] <= tp.chase_atr * atr)
      on_side = c[i] > z.bottom if is_long else c[i] < z.top
      mid = (z.top + z.bottom) / 2.0
      pd_ok = (not tp.require_pd) or m.isna(eq) or (mid <= eq if is_long else mid >= eq)
      path = self.poi_path(z, True)
      # checks in the order of the histogram: not a POI, wrong premium/discount side, entry too far, stop limits, pip cap, no room
      if path == "Candidate":
        stage = 0
      elif not pd_ok:
        stage = 1
      elif not (near and on_side):
        stage = 2
      else:
        stage = -1
      stop = z.bottom - tp.stop_buffer_atr * atr if is_long else z.top + tp.stop_buffer_atr * atr
      risk = abs(c[i] - stop)
      if stage < 0 and not (tp.min_stop_atr * atr <= risk <= tp.max_stop_atr * atr):
        stage, saw_bad_stop = 3, True
      if stage < 0 and tp.max_stop_pips > 0 and risk / tp.pip_size > tp.max_stop_pips + 1e-9:
        stage = 4
      if stage < 0:
        target, name, level, nearest = self.key_target(i, is_long, c[i], risk)
        if target is None:
          stage, saw_no_target = 5, True
      if stage >= 0:
        if stage > deepest:
          deepest, deepest_reason = stage, REFUSALS[stage]
        continue
      rr = abs(target - c[i]) / risk
      if rr > best_rr:
        best_rr, pick = rr, (z, stop, target, name, path, level, nearest)
    if pick is None:
      reason = deepest_reason or "no zone touched"
      self.refusals[reason] = self.refusals.get(reason, 0) + 1
      if saw_no_target:
        self.skipped["no_target"] += 1
      elif saw_bad_stop:
        self.skipped["bad_stop"] += 1
      return
    z, stop, target, name, path, level, nearest = pick
    self.spent.add((z.born, z.top))
    self.open = Trade(s.bias, i, c[i], stop, target, z.title, (z.bottom, z.top), name, best_rr, path, raw_level=level, nearest_ahead=nearest, zone_born=z.born, zone_first_touch=z.first_touch)

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


def v213_trade(**overrides):
  """TradeParams of the v2.13 indicator (the Pine defaults)."""
  kw = dict(ring_obstacles=True, max_stop_pips=70.0, asia_mode="engine")
  kw.update(overrides)
  return TradeParams(**kw)


def plan_off(tp, atr):
  """v2.14: the pip cap is smaller than the smallest possible stop on this frame (minimum stop plus buffer, in ATR): nothing can open and the panel says so.
  Mirrors the Pine `Plan off` text of the trades row (the cap is in pips, the stop in ATR of the chart frame)."""
  return tp.max_stop_pips > 0 and tp.max_stop_pips * tp.pip_size < (tp.min_stop_atr + tp.stop_buffer_atr) * atr

"""Python reference model of the indicator's per-bar pipeline (apexvoid_price_action.pine): structure, liquidity pools, order blocks, supply / demand, base-impulse
zones, FVGs, zone lifecycle, the POI qualification (`poi_path`, a mirror of f_poiPath) and the session helpers (Asian range, session boxes). The v2.8 trade plan that
used to live here was removed with the plan itself in v2.18; this is the harness the other reference tests run on real closed bars. It validates the rules only: it does
not compile or draw the Pine file.
"""
import math

import pa_model as m
import sd_base_model as sdb


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


class Pipeline(m.V21Pipeline):
  def __init__(self, bars, p, sdp=None):
    super().__init__(bars, p)
    self.sdp = sdp or sdb.Params()
    self.lm = m.LiquidityModel(self.s, p)
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
      if i > 40:
        self.base_impulse(i)
      self.fvg(i)
      self.lifecycle(i)
      self.max_store = max(self.max_store, len(self.zones))
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

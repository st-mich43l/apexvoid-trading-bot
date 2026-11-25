"""Offline reference model of ApexVoid | Price Geometry v1.0 (tradingview/apexvoid_price_geometry.pine).

It implements the same rules as the Pine source, bar by bar on closed bars, so the geometry and the lifecycle can be checked against deterministic fixtures
(test_price_geometry.py). It is a reference for the mathematics, not a second indicator: TradingView remains the only authority on what the Pine source does.

Conventions (identical to the Pine source):
- Bars are processed in order; bar t is processed when it has closed. Nothing reads a bar later than t.
- ATR is Wilder's ATR(14) (ta.atr): the RMA of the true range, seeded with the simple mean of the first 14 true ranges.
- A pivot high at bar k with length n is known at bar k + n: high[k] > every high of the n bars before it and >= every high of the n bars after it (lows mirrored).
- Lines are arithmetic: L(t) = P1 + m * (t - B1), m = (P2 - P1) / (B2 - B1).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Params:
  pivot_len: int = 6
  min_swing_atr: float = 1.5
  max_swings: int = 24
  min_sep: int = 10
  touch_tol_atr: float = 0.20
  min_departure_atr: float = 1.0
  break_tol_atr: float = 0.15
  break_closes: int = 1
  max_age: int = 600
  lookback: int = 250
  channel_min_width_atr: float = 2.0
  atr_len: int = 14
  # Internal constants (not user settings in the Pine source).
  max_wick_atr: float = 0.5        # a wick deeper than this beyond the line before publication rejects a candidate
  min_rise_atr: float = 0.5        # the anchors must differ by at least this much (else the line is effectively horizontal)
  max_dist_atr: float = 20.0       # a line farther than this from the close is irrelevant (rejected / retired); wider than the widest channel
  channel_max_width_atr: float = 15.0
  recent_anchors: int = 3          # second anchors tried per search (the most recent swings of the side)
  earlier_anchors: int = 6         # first anchors tried per second anchor


@dataclass
class Swing:
  bar: int
  price: float
  high: bool
  atr: float
  confirmed: int


@dataclass
class Line:
  support: bool
  b1: int
  p1: float
  b2: int
  p2: float
  published: int
  touches: int = 2
  last_touch: int = 0
  touch_bars: list = field(default_factory=list)  # touches counted after publication
  break_count: int = 0
  state: str = "active"  # active | broken | retired
  ended: int | None = None

  @property
  def slope(self) -> float:
    return (self.p2 - self.p1) / (self.b2 - self.b1)

  def at(self, t: float) -> float:
    return self.p1 + self.slope * (t - self.b1)


@dataclass
class Channel:
  primary: Line
  o_bar: int
  o_price: float
  offset: float
  published: int
  state: str = "active"  # active | breached | ended
  opposing_touches: int = 1
  break_count: int = 0
  breached_at: int | None = None

  def boundary(self, t: float) -> float:
    return self.primary.at(t) + self.offset

  def midpoint(self, t: float) -> float:
    return self.primary.at(t) + self.offset / 2.0


def wilder_atr(highs, lows, closes, length=14):
  out = [math.nan] * len(highs)
  trs = []
  value = math.nan
  for i in range(len(highs)):
    tr = highs[i] - lows[i] if i == 0 else max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    trs.append(tr)
    if i + 1 == length:
      value = sum(trs) / length
    elif i + 1 > length:
      value = (value * (length - 1) + tr) / length
    out[i] = value
  return out


class Geometry:
  """The indicator's state machine over closed bars."""

  def __init__(self, bars, params: Params | None = None):
    self.p = params or Params()
    self.o = [b[0] for b in bars]
    self.h = [b[1] for b in bars]
    self.l = [b[2] for b in bars]
    self.c = [b[3] for b in bars]
    self.atr = wilder_atr(self.h, self.l, self.c, self.p.atr_len)
    self.swings: list[Swing] = []
    self.raw_lows: list[int] = []   # bars of confirmed raw pivot lows (touch reactions)
    self.raw_highs: list[int] = []
    self.support: Line | None = None
    self.resistance: Line | None = None
    self.channel: Channel | None = None
    self.history: list[Line] = []   # broken / retired lines, newest last
    self.published: list[Line] = []  # every line ever published, in order
    self.channels: list[Channel] = []
    self.t = -1

  # ---------------------------------------------------------------- helpers
  def _atr(self, k):
    a = self.atr[k]
    return a if a is not None and not math.isnan(a) and a > 0 else None

  def _pivot(self, k, high):
    n = self.p.pivot_len
    if k - n < 0:
      return False
    series = self.h if high else self.l
    v = series[k]
    for j in range(k - n, k):
      if (series[j] >= v) if high else (series[j] <= v):
        return False
    for j in range(k + 1, k + n + 1):
      if (series[j] > v) if high else (series[j] < v):
        return False
    return True

  def _accept_swing(self, high, k, t):
    """Alternating significant swings: a same-side pivot replaces the last swing only when more extreme; an opposite pivot is kept only when it moved
    min_swing_atr ATRs (at the pivot bar) from the last swing. Returns True when the swing list changed on this side."""
    price = self.h[k] if high else self.l[k]
    a = self._atr(k)
    if a is None:
      return False
    s = Swing(k, price, high, a, t)
    if not self.swings:
      self.swings.append(s)
      return True
    last = self.swings[-1]
    if last.high == high:
      if (price > last.price) if high else (price < last.price):
        self.swings[-1] = s
        return True
      return False
    if abs(price - last.price) >= self.p.min_swing_atr * a:
      self.swings.append(s)
      if len(self.swings) > self.p.max_swings:
        self.swings.pop(0)
      return True
    return False

  # ---------------------------------------------------------------- validation
  def _excursion(self, line: Line, k):
    """How far bar k traded away from the line on the side the line holds, in ATR(k)."""
    a = self._atr(k)
    if a is None:
      return 0.0
    return (self.h[k] - line.at(k)) / a if line.support else (line.at(k) - self.l[k]) / a

  def _near(self, line: Line, k):
    a = self._atr(k)
    if a is None:
      return False
    reaction = self.l[k] if line.support else self.h[k]
    return abs(reaction - line.at(k)) <= self.p.touch_tol_atr * a

  def _close_beyond(self, line: Line, k, offset=0.0, opposing=False):
    a = self._atr(k)
    if a is None:
      return False
    level = line.at(k) + offset
    tol = self.p.break_tol_atr * a
    beyond_low = self.c[k] < level - tol
    beyond_high = self.c[k] > level + tol
    if opposing:
      return beyond_high if line.support else beyond_low
    return beyond_low if line.support else beyond_high

  def validate(self, support, a: Swing, b: Swing, t):
    """A two-anchor candidate as of closed bar t. Returns (line, anchor displacement in ATR, distance to the close in ATR) or None."""
    p = self.p
    if b.bar - a.bar < p.min_sep or a.bar < t - p.lookback:
      return None
    rise = b.price - a.price if support else a.price - b.price
    if not rise > 0 or rise < p.min_rise_atr * b.atr:
      return None
    line = Line(support, a.bar, a.price, b.bar, b.price, published=t, last_touch=a.bar)
    raw = set(self.raw_lows if support else self.raw_highs)
    touches, last_touch, exc, anchor_exc, closes_beyond = 1, a.bar, 0.0, 0.0, 0
    for k in range(a.bar + 1, t + 1):
      atr_k = self._atr(k)
      if atr_k is None:
        return None
      lk = line.at(k)
      if self._close_beyond(line, k):
        closes_beyond += 1
        if closes_beyond >= p.break_closes:
          return None
      else:
        closes_beyond = 0
      wick = (lk - self.l[k]) if support else (self.h[k] - lk)
      if wick > p.max_wick_atr * atr_k:
        return None
      if k == b.bar:
        if anchor_exc < p.min_swing_atr:
          return None
        touches, last_touch, exc = touches + 1, k, 0.0
        continue
      if k in raw and self._near(line, k) and k - last_touch >= p.min_sep and exc >= p.min_departure_atr and (k > b.bar or b.bar - k >= p.min_sep):
        touches, last_touch, exc = touches + 1, k, 0.0
        continue
      e = self._excursion(line, k)
      exc = max(exc, e)
      if k < b.bar:
        anchor_exc = max(anchor_exc, e)
    atr_t = self._atr(t)
    if atr_t is None:
      return None
    distance = abs(self.c[t] - line.at(t)) / atr_t
    if distance > p.max_dist_atr:
      return None
    line.touches, line.last_touch = touches, last_touch
    return line, anchor_exc, distance

  @staticmethod
  def _rank(cand):
    line, anchor_exc, distance = cand
    # Selection, lexicographic (no weighted score): more independent touches (3 or more count alike), larger displacement between the anchors in whole ATRs
    # (capped at 4), longer span, the line nearer the close, then the later second and first anchors.
    return (min(line.touches, 3), min(int(anchor_exc), 4), line.b2 - line.b1, -distance, line.b2, line.b1)

  def search(self, support, t):
    side = [s for s in self.swings if s.high != support]
    best = None
    for bi in range(len(side) - 1, max(-1, len(side) - 1 - self.p.recent_anchors), -1):
      b = side[bi]
      for ai in range(bi - 1, max(-1, bi - 1 - self.p.earlier_anchors), -1):
        cand = self.validate(support, side[ai], b, t)
        if cand is not None and (best is None or self._rank(cand) > self._rank(best)):
          best = cand
    return best

  # ---------------------------------------------------------------- channels
  def build_channel(self, line: Line, t):
    p = self.p
    best = None
    for s in self.swings:
      if s.high != line.support or s.bar <= line.b1:
        continue
      d = s.price - line.at(s.bar)
      if (d > 0) != line.support or d == 0:
        continue
      if best is None or abs(d) > abs(best[1]) or (abs(d) == abs(best[1]) and s.bar > best[0].bar):
        best = (s, d)
    if best is None:
      return None
    o, d = best
    atr_t = self._atr(t)
    if atr_t is None or abs(d) < p.channel_min_width_atr * atr_t or abs(d) > p.channel_max_width_atr * atr_t:
      return None
    ch = Channel(line, o.bar, o.price, d, published=t)
    closes_beyond = 0
    for k in range(max(line.b1 + 1, t - p.lookback), t + 1):  # the bounded window
      if self._close_beyond(line, k, offset=d, opposing=True):
        closes_beyond += 1
        if closes_beyond >= p.break_closes:
          return None
      else:
        closes_beyond = 0
    return ch

  # ---------------------------------------------------------------- per-bar
  def _update_line(self, line: Line, t):
    p = self.p
    atr_t = self._atr(t)
    if atr_t is None:
      return
    if self._close_beyond(line, t):
      line.break_count += 1
      if line.break_count >= p.break_closes:
        line.state, line.ended = "broken", t
        return
    else:
      line.break_count = 0
    k = t - p.pivot_len
    raw = self.raw_lows if line.support else self.raw_highs
    if raw and raw[-1] == k and k > line.b2 and self._near(line, k) and k - line.last_touch >= p.min_sep:
      exc = max((self._excursion(line, j) for j in range(line.last_touch + 1, k)), default=0.0)
      if exc >= p.min_departure_atr:
        line.touches += 1
        line.last_touch = k
        line.touch_bars.append(k)
    if t - line.last_touch > p.max_age or abs(self.c[t] - line.at(t)) > p.max_dist_atr * atr_t:
      line.state, line.ended = "retired", t

  def _update_channel(self, t):
    ch = self.channel
    if ch is None:
      return
    if ch.primary.state != "active":
      ch.state = "ended"
      self.channel = None
      return
    if ch.state == "active":
      if self._close_beyond(ch.primary, t, offset=ch.offset, opposing=True):
        ch.break_count += 1
        if ch.break_count >= self.p.break_closes:
          ch.state, ch.breached_at = "breached", t
      else:
        ch.break_count = 0

  def step(self):
    """Process the next closed bar."""
    self.t += 1
    t = self.t
    p = self.p
    changed = {True: False, False: False}  # support side (lows) / resistance side (highs)
    k = t - p.pivot_len
    if k >= p.pivot_len:
      ph, pl = self._pivot(k, True), self._pivot(k, False)
      if pl:
        self.raw_lows.append(k)
        self.raw_lows = self.raw_lows[-64:]
      if ph:
        self.raw_highs.append(k)
        self.raw_highs = self.raw_highs[-64:]
      order = []
      if ph and pl:
        last_high = self.swings[-1].high if self.swings else False
        order = [False, True] if last_high else [True, False]
      elif ph:
        order = [True]
      elif pl:
        order = [False]
      for high in order:
        if self._accept_swing(high, k, t):
          changed[not high] = True
    for support in (True, False):
      line = self.support if support else self.resistance
      if line is not None:
        self._update_line(line, t)
        if line.state != "active":
          self.history.append(line)
          changed[support] = True
          if support:
            self.support = None
          else:
            self.resistance = None
          line = None
      if changed[support]:
        best = self.search(support, t)
        if best is not None:
          cand = best[0]
          if line is None or ((cand.b1, cand.b2) != (line.b1, line.b2) and min(cand.touches, 3) > min(line.touches, 3) and cand.b2 > line.b2):
            if line is not None:
              line.state, line.ended = "retired", t
              self.history.append(line)
            cand.published = t
            if support:
              self.support = cand
            else:
              self.resistance = cand
            self.published.append(cand)
    ended = self.channel is not None and self.channel.primary.state != "active"
    self._update_channel(t)
    if self.channel is None:
      # One channel, on the active line with the later second anchor. It is tried when that line is published, when a swing on the opposite side is confirmed,
      # or when the previous channel ended; it is built once from the swings confirmed by then and never widened.
      lines = [x for x in (self.support, self.resistance) if x is not None]
      if lines:
        line = max(lines, key=lambda x: x.b2)
        if line.published == t or changed[not line.support] or ended:
          ch = self.build_channel(line, t)
          if ch is not None:
            self.channel = ch
            self.channels.append(ch)

  def run(self, until=None):
    end = len(self.c) if until is None else until
    while self.t + 1 < end:
      self.step()
    return self

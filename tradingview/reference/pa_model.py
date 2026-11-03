"""Python reference model of apexvoid_price_action.pine.

Purpose: algorithm-level verification of the Pine indicator on real closed-bar
captures, because TradingView is the only authoritative Pine compiler and is not
available in CI. The model mirrors the Pine decision logic bar by bar. It does
not render anything and it cannot prove that the Pine file compiles.

`legacy` = v2.0 behaviour (used to reproduce the missing-OB defect).
`v21`    = v2.1 behaviour (see tradingview/README.md).
"""
import math
from dataclasses import dataclass, field

NAN = float("nan")


def isna(x):
  return x is None or (isinstance(x, float) and math.isnan(x))


def tf_lengths(chart_secs):
  internal = 3 if chart_secs <= 300 else 4 if chart_secs <= 900 else 5 if chart_secs <= 3600 else 6 if chart_secs <= 14400 else 8
  external = 12 if chart_secs <= 300 else 16 if chart_secs <= 900 else 20 if chart_secs <= 3600 else 24 if chart_secs <= 14400 else 30
  return internal, external


def rma_atr(h, l, c, length=14):
  n = len(h)
  tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
  out = [NAN] * n
  if n < length:
    return out
  seed = sum(tr[:length]) / length
  out[length - 1] = seed
  for i in range(length, n):
    out[i] = (out[i - 1] * (length - 1) + tr[i]) / length
  return out


def pivot_high(h, i, length):
  """ta.pivothigh(high, length, length) evaluated on bar i (returns the pivot at i-length)."""
  c = i - length
  if c - length < 0:
    return NAN
  v = h[c]
  for k in range(1, length + 1):
    if h[c - k] >= v or h[c + k] > v:
      return NAN
  return v


def pivot_low(l, i, length):
  c = i - length
  if c - length < 0:
    return NAN
  v = l[c]
  for k in range(1, length + 1):
    if l[c - k] <= v or l[c + k] < v:
      return NAN
  return v


@dataclass
class Params:
  chart_secs: int = 300
  min_leg_atr: float = 1.5
  confirm_atr: float = 0.15
  break_buffer: float = 0.0
  equal_tolerance: float = 0.15
  sweep_buffer: float = 0.01
  # legacy order block
  ob_lookback: int = 12
  ob_impulse_atr: float = 0.5
  ob_wicks: bool = True
  sd_departure_atr: float = 0.8
  sd_max_width_atr: float = 1.0
  min_gap_atr: float = 0.10
  fvg_impulse_atr: float = 0.5
  max_zones: int = 80
  visible_zones: int = 8
  zone_display_distance_atr: float = 10.0
  actionable_poi_distance_atr: float = 3.0
  poi_structure_window: int = 96
  zone_max_age: int = 600
  # v2.13 switches. Every default reproduces v2.12 exactly; `v213()` below turns them all on.
  structure_mode: str = "legacy"       # "legacy" | "displacement": publish a swing on displacement, not on the next opposite pivot
  protected_mode: str = "legacy"       # "legacy" | "trailing": protected level ratchets with internal BOS, CHoCH on it
  range_mode: str = "v212"             # "v212" | "protected": dealing range follows the protected leg
  fresh_sd: bool = False               # swing supply/demand: scan the bars since the swing before drawing it
  ob_break_anchor: bool = False        # order block built from the real (back-dated) break bar
  fvg_def: str = "v212"                # "v212" | "loose" (v2.12 keys + body direction) | "engine" (fvg v9 contract)
  fvg_engine_atr: float = 0.5          # engine fvg v9 contract: gap and middle body in ATR, body/range ratio (same for every instrument)
  fvg_engine_body_ratio: float = 0.5
  composite_independent: bool = False  # Composite needs components whose origin differs by more than 3 bars
  swept_before_impulse: bool = False   # Swept imbalance = a sweep within 6 bars before the gap's first candle
  # v2.14 switches (trailing protected level only; every default reproduces v2.13 exactly, `v214()` turns them on)
  protected_hl: bool = False           # promote the higher low that made a confirmed higher high; a protected-level CHoCH takes the last internal pivot, not the leg start
  dev_follows_protected: bool = False  # a protected level that ratchets in the bias direction puts the developing leg (DEV, phase) on the bias side


@dataclass
class Zone:
  origin: int
  top: float
  bottom: float
  side: int
  kind: int
  title: str
  born: int
  touched: bool = False
  mitigation: float = 0.0
  poi: bool = False
  visible: bool = False
  cls: int = 0
  state: str = "Fresh"
  event: str = ""
  first_touch: int = -1


class Structure:
  """Internal + external swing state machine (mirrors the Pine file)."""

  def __init__(self, bars, p):
    self.t, self.o, self.h, self.l, self.c = bars
    self.p = p
    self.n = len(self.c)
    self.internal_len, self.external_len = tf_lengths(p.chart_secs)
    self.atr = rma_atr(self.h, self.l, self.c)
    self.reset()

  def reset(self):
    self.last_swing_high = self.last_swing_low = NAN
    self.previous_swing_high = self.previous_swing_low = NAN
    self.last_high_bar = self.last_low_bar = None
    self.high_broken = self.low_broken = False
    self.dev_dir = 0
    self.dev_high = self.dev_low = NAN
    self.dev_high_bar = self.dev_low_bar = None
    self.bias = 0
    self.last_break_bar = None
    self.last_break_choch = False
    self.protected_high = self.protected_low = NAN
    self.cand_type = 0
    self.cand_high = self.cand_low = NAN
    self.cand_high_bar = self.cand_low_bar = None
    self.li_high = self.li_low = NAN
    self.li_high_bar = self.li_low_bar = None
    self.i_high_broken = self.i_low_broken = False
    self.i_bias = 0
    self.last_sweep_side = 0
    self.last_sweep_bar = None
    # v2.13
    self.cur = -1
    self.protected_high_bar = self.protected_low_bar = None
    self.ext_high_swept = self.ext_low_swept = False
    self.sweeps = []              # (bar, side) of every pool / external sweep; side = the reversal it supports
    self.trk = {}                 # displacement-mode zigzag trackers
    self.pub_lags = []            # (publication bar - swing bar) of every external swing
    self.i_low_ring, self.i_high_ring = [], []   # v2.14: the last 8 internal pivot lows / highs as (bar, price), in detection order
    self.ratchet = 0              # v2.14: +1 / -1 when the protected level ratcheted toward price on this bar in the bias direction

  def first_close_beyond(self, i, swing_bar, level, up):
    """Bar of the first close beyond the swing (the candle a BOS/CHoCH line must end on).
    The swing is published late, so this can precede the publication bar i."""
    need = self.p.confirm_atr + self.p.break_buffer
    span = i - swing_bar - 1
    if span < 1 or span > 750:
      return i
    for k in range(swing_bar + 1, i):
      a = self.atr[k] if not isna(self.atr[k]) else self.atr[i]
      if (self.c[k] > level + a * need) if up else (self.c[k] < level - a * need):
        return k
    return i

  def dealing_range(self):
    """Premium/discount range (mirrors dealingHigh/dealingLow in the Pine file): the confirmed swing
    range extended to the live extreme of the current leg. Returns (high, low, mid) or (nan, nan, nan)."""
    if self.p.range_mode == "protected" and self.cur >= 0:
      # v2.13: the range of the protected leg. Bear: protected high down to the lowest low since it; bull: mirror. Price is always inside.
      if self.bias == -1 and not isna(self.protected_high) and self.protected_high_bar is not None:
        lo = min(self.l[self.protected_high_bar:self.cur + 1])
        if self.protected_high > lo:
          return self.protected_high, lo, (self.protected_high + lo) / 2.0
      if self.bias == 1 and not isna(self.protected_low) and self.protected_low_bar is not None:
        hi = max(self.h[self.protected_low_bar:self.cur + 1])
        if hi > self.protected_low:
          return hi, self.protected_low, (hi + self.protected_low) / 2.0
    hi, lo = self.last_swing_high, self.last_swing_low
    if self.dev_dir == -1 and not isna(self.dev_low) and (isna(lo) or self.dev_low < lo):
      lo = self.dev_low
    if self.dev_dir == 1 and not isna(self.dev_high) and (isna(hi) or self.dev_high > hi):
      hi = self.dev_high
    if isna(hi) or isna(lo) or not hi > lo:
      return NAN, NAN, NAN
    return hi, lo, (hi + lo) / 2.0

  def a_at(self, i):
    return self.atr[i] if i >= 0 else NAN

  # ---- v2.13: external swings published on displacement ---------------------------------------
  def _tracker(self, kind):
    return dict(kind=kind, val=NAN, bar=None, opp=NAN, opp_bar=None)

  def _trk_update(self, tr, i):
    """Advance one zigzag tracker with bar i: `kind` "low" follows the running lowest low (strictly lower replaces it, so the earliest
    of equal lows is kept like a pivot) and the highest high strictly after it; "high" is the mirror."""
    low = tr["kind"] == "low"
    price, opp_price = (self.l[i], self.h[i]) if low else (self.h[i], self.l[i])
    if tr["bar"] is None or (price < tr["val"] if low else price > tr["val"]):
      tr["val"], tr["bar"], tr["opp"], tr["opp_bar"] = price, i, NAN, None
    elif isna(tr["opp"]) or (opp_price > tr["opp"] if low else opp_price < tr["opp"]):
      tr["opp"], tr["opp_bar"] = opp_price, i

  def _trk_ready(self, tr, i):
    """Both publication conditions: the right-side pivot condition (external_len closed bars after the candidate, none went further, which
    is what the tracker holding it means) and a counter-leg of at least minLegATR + confirmATR of the ATR at the candidate bar."""
    if tr["bar"] is None or isna(tr["opp"]) or i - tr["bar"] < self.external_len:
      return False
    a = self.a_at(tr["bar"])
    if isna(a):
      a = self.atr[i]
    leg = (tr["opp"] - tr["val"]) if tr["kind"] == "low" else (tr["val"] - tr["opp"])
    return leg >= a * (self.p.min_leg_atr + self.p.confirm_atr)

  def _retrack_after(self, kind, bar, i):
    """New tracker of `kind` whose candidate is the opposite extreme (`bar`) and whose counter-extreme is read from the bars after it."""
    tr = self._tracker(kind)
    price = self.h[bar] if kind == "high" else self.l[bar]
    tr["val"], tr["bar"] = price, bar
    for k in range(bar + 1, i + 1):
      opp = self.l[k] if kind == "high" else self.h[k]
      if isna(tr["opp"]) or (opp < tr["opp"] if kind == "high" else opp > tr["opp"]):
        tr["opp"], tr["opp_bar"] = opp, k
    return tr

  def _step_displacement(self, i, ev):
    """Publish an external swing the first time its pivot is confirmed AND the counter-leg has reached the threshold."""
    if self.cand_type == 0:
      for kind in ("low", "high"):
        if kind not in self.trk:
          self.trk[kind] = self._tracker(kind)
        self._trk_update(self.trk[kind], i)
      ready = [k for k in ("low", "high") if self._trk_ready(self.trk[k], i)]
      if not ready:
        return
      kind = min(ready, key=lambda k: self.trk[k]["bar"])
      tr = self.trk[kind]
      self.cand_type = -1 if kind == "low" else 1
      self.trk = {}
    else:
      kind = "low" if self.cand_type == -1 else "high"
      # the tracker of the side being published is the one NOT held: a candidate LOW is published (and then a HIGH is tracked)
      tr = self.trk.get("pub")
      if tr is None:
        tr = self.trk["pub"] = self._tracker("low" if self.cand_type == -1 else "high")
        # first bar after a publication: the candidate was set by _retrack_after
      self._trk_update(tr, i)
      if not self._trk_ready(tr, i):
        return
      kind = tr["kind"]
    sweep_ext = tr["opp"]
    cb, cp = tr["bar"], tr["val"]
    self.pub_lags.append(i - cb)
    if kind == "low":
      self.previous_swing_low = self.last_swing_low
      self.last_swing_low, self.last_low_bar = cp, cb
      self.low_broken = False
      self.ext_low_swept = False
      ev["new_major_low"], ev["major_low_price"], ev["major_low_bar"] = True, cp, cb
      ev["ext_high"] = sweep_ext
      self.dev_dir = 1
      self.dev_high, self.dev_high_bar = tr["opp"], tr["opp_bar"]
      self.dev_low, self.dev_low_bar = NAN, None
      self._protect_on_swing(1)
      nxt = self._retrack_after("high", tr["opp_bar"], i)
      self.cand_type = 1
    else:
      self.previous_swing_high = self.last_swing_high
      self.last_swing_high, self.last_high_bar = cp, cb
      self.high_broken = False
      self.ext_high_swept = False
      ev["new_major_high"], ev["major_high_price"], ev["major_high_bar"] = True, cp, cb
      ev["ext_low"] = sweep_ext
      self.dev_dir = -1
      self.dev_low, self.dev_low_bar = tr["opp"], tr["opp_bar"]
      self.dev_high, self.dev_high_bar = NAN, None
      self._protect_on_swing(-1)
      nxt = self._retrack_after("low", tr["opp_bar"], i)
      self.cand_type = -1
    # the tracker for the NEXT candidate: its extreme is the counter-extreme just found
    self.trk = {"pub": nxt}
    # the candidate that was just published is no longer tracked; the new one carries the running extreme since it
    if self.cand_type == 1:
      self.cand_high, self.cand_high_bar = nxt["val"], nxt["bar"]
    else:
      self.cand_low, self.cand_low_bar = nxt["val"], nxt["bar"]

  @staticmethod
  def ring_pick(ring, after_bar, upto_bar):
    """The most recent internal pivot (bar, price) with after_bar < bar <= upto_bar, or None."""
    for bar, price in reversed(ring):
      if after_bar < bar <= upto_bar:
        return bar, price
    return None

  def _protect_on_swing(self, side):
    """A newly published swing while the bias agrees refreshes the protected level: legacy overwrites it, trailing only moves it toward price."""
    if self.p.protected_mode == "trailing" and self.p.protected_hl:
      # v2.14: a confirmed higher high in a bull bias (lower low in a bear bias) promotes the higher low (lower high) that produced it
      if side == -1 and self.bias == 1 and self.protected_low_bar is not None and self.last_high_bar is not None:
        hit = self.ring_pick(self.i_low_ring, self.protected_low_bar, self.last_high_bar - 1)
        if hit and (isna(self.protected_low) or hit[1] > self.protected_low):
          self.protected_low, self.protected_low_bar = hit[1], hit[0]
          self.ratchet = 1
      if side == 1 and self.bias == -1 and self.protected_high_bar is not None and self.last_low_bar is not None:
        hit = self.ring_pick(self.i_high_ring, self.protected_high_bar, self.last_low_bar - 1)
        if hit and (isna(self.protected_high) or hit[1] < self.protected_high):
          self.protected_high, self.protected_high_bar = hit[1], hit[0]
          self.ratchet = -1
    if side == 1 and self.bias == 1:
      if self.p.protected_mode != "trailing" or isna(self.protected_low) or self.last_swing_low > self.protected_low:
        self.protected_low, self.protected_low_bar = self.last_swing_low, self.last_low_bar
    if side == -1 and self.bias == -1:
      if self.p.protected_mode != "trailing" or isna(self.protected_high) or self.last_swing_high < self.protected_high:
        self.protected_high, self.protected_high_bar = self.last_swing_high, self.last_high_bar

  def step_pivots(self, i):
    """Returns dict of per-bar events; mutates swing state."""
    p = self.p
    atr = self.atr[i]
    self.cur = i
    self.ratchet = 0
    ev = dict(new_ih=False, new_il=False, ih=NAN, il=NAN, new_major_high=False, new_major_low=False,
              major_high_price=NAN, major_low_price=NAN, major_high_bar=None, major_low_bar=None,
              up_break=False, down_break=False, choch=False, i_up_break=False, i_down_break=False,
              i_choch=False, ext_high=NAN, ext_low=NAN, broken_high=NAN, broken_low=NAN,
              broken_high_bar=None, broken_low_bar=None, i_broken_high=NAN, i_broken_low=NAN,
              i_broken_high_bar=None, i_broken_low_bar=None, protected_choch=False)
    il_ = self.internal_len
    el_ = self.external_len
    ih = pivot_high(self.h, i, il_)
    il = pivot_low(self.l, i, il_)
    eh = pivot_high(self.h, i, el_)
    el = pivot_low(self.l, i, el_)
    ev["ext_high"], ev["ext_low"] = eh, el
    if not isna(ih):
      ev["new_ih"], ev["ih"] = True, ih
      self.li_high, self.li_high_bar, self.i_high_broken = ih, i - il_, False
      self.i_high_ring = (self.i_high_ring + [(i - il_, ih)])[-8:]
    if not isna(il):
      ev["new_il"], ev["il"] = True, il
      self.li_low, self.li_low_bar, self.i_low_broken = il, i - il_, False
      self.i_low_ring = (self.i_low_ring + [(i - il_, il)])[-8:]
    if isna(atr):
      return ev
    c = self.c[i]
    # internal breaks
    if not isna(self.li_high) and not self.i_high_broken and c > self.li_high + atr * p.break_buffer:
      ev["i_up_break"] = True
      ev["i_choch"] = self.i_bias == -1
      ev["i_broken_high"], ev["i_broken_high_bar"] = self.li_high, self.li_high_bar
      if p.protected_mode == "trailing" and self.bias == 1 and self.li_high_bar is not None:
        # the pullback low the bullish break came from: the lowest low of (internal high bar, break bar]; it only ratchets toward price
        seg = self.l[self.li_high_bar + 1:i + 1]
        if seg:
          lo = min(seg)
          if isna(self.protected_low) or lo > self.protected_low:
            self.protected_low, self.protected_low_bar = lo, self.li_high_bar + 1 + seg.index(lo)
            self.ratchet = 1
      self.i_high_broken = True
      self.i_bias = 1
    if not isna(self.li_low) and not self.i_low_broken and c < self.li_low - atr * p.break_buffer:
      ev["i_down_break"] = True
      ev["i_choch"] = self.i_bias == 1
      ev["i_broken_low"], ev["i_broken_low_bar"] = self.li_low, self.li_low_bar
      if p.protected_mode == "trailing" and self.bias == -1 and self.li_low_bar is not None:
        seg = self.h[self.li_low_bar + 1:i + 1]
        if seg:
          hi = max(seg)
          if isna(self.protected_high) or hi < self.protected_high:
            self.protected_high, self.protected_high_bar = hi, self.li_low_bar + 1 + seg.index(hi)
            self.ratchet = -1
      self.i_low_broken = True
      self.i_bias = -1
    # external candidate state machine
    if p.structure_mode == "displacement":
      self._step_displacement(i, ev)
    elif not isna(eh):
      cb = i - el_
      if self.cand_type == 0:
        self.cand_high, self.cand_high_bar, self.cand_type = eh, cb, 1
      elif self.cand_type == 1:
        if eh > self.cand_high:
          self.cand_high, self.cand_high_bar = eh, cb
      elif eh - self.cand_low >= self.a_at(i - el_) * (p.min_leg_atr + p.confirm_atr):
        self.previous_swing_low = self.last_swing_low
        self.last_swing_low, self.last_low_bar = self.cand_low, self.cand_low_bar
        self.low_broken = False
        self.ext_low_swept = False
        ev["new_major_low"], ev["major_low_price"], ev["major_low_bar"] = True, self.cand_low, self.cand_low_bar
        self.dev_dir = 1
        self.dev_high, self.dev_high_bar = max(eh, self.h[i]), (cb if eh >= self.h[i] else i)
        self.dev_low, self.dev_low_bar = NAN, None
        self._protect_on_swing(1)
        self.cand_high, self.cand_high_bar, self.cand_type = eh, cb, 1
    if p.structure_mode != "displacement" and not isna(el):
      cb = i - el_
      if self.cand_type == 0:
        self.cand_low, self.cand_low_bar, self.cand_type = el, cb, -1
      elif self.cand_type == -1:
        if el < self.cand_low:
          self.cand_low, self.cand_low_bar = el, cb
      elif self.cand_high - el >= self.a_at(i - el_) * (p.min_leg_atr + p.confirm_atr):
        self.previous_swing_high = self.last_swing_high
        self.last_swing_high, self.last_high_bar = self.cand_high, self.cand_high_bar
        self.high_broken = False
        self.ext_high_swept = False
        ev["new_major_high"], ev["major_high_price"], ev["major_high_bar"] = True, self.cand_high, self.cand_high_bar
        self.dev_dir = -1
        self.dev_low, self.dev_low_bar = min(el, self.l[i]), (cb if el <= self.l[i] else i)
        self.dev_high, self.dev_high_bar = NAN, None
        self._protect_on_swing(-1)
        self.cand_low, self.cand_low_bar, self.cand_type = el, cb, -1
    if self.dev_dir == 1 and (isna(self.dev_high) or self.h[i] > self.dev_high):
      self.dev_high, self.dev_high_bar = self.h[i], i
    if self.dev_dir == -1 and (isna(self.dev_low) or self.l[i] < self.dev_low):
      self.dev_low, self.dev_low_bar = self.l[i], i
    if self.ratchet and p.dev_follows_protected:
      # v2.14: the developing leg is the protected leg: the extreme since the protected bar, the same value the dealing range uses
      if self.ratchet == 1 and not isna(self.protected_low) and self.protected_low_bar is not None:
        seg = self.h[self.protected_low_bar:i + 1]
        self.dev_dir, self.dev_high, self.dev_high_bar = 1, max(seg), self.protected_low_bar + seg.index(max(seg))
        self.dev_low, self.dev_low_bar = NAN, None
      if self.ratchet == -1 and not isna(self.protected_high) and self.protected_high_bar is not None:
        seg = self.l[self.protected_high_bar:i + 1]
        self.dev_dir, self.dev_low, self.dev_low_bar = -1, min(seg), self.protected_high_bar + seg.index(min(seg))
        self.dev_high, self.dev_high_bar = NAN, None
    thr = atr * (p.break_buffer + p.confirm_atr)
    if p.swept_before_impulse:
      # external sweeps (events only): a wick beyond the unbroken swing that closes back inside, once per swing
      lsh, lsl = self.last_swing_high, self.last_swing_low
      if not isna(lsh) and not self.high_broken and not self.ext_high_swept and self.h[i] > lsh + atr * p.sweep_buffer and c < lsh:
        self.ext_high_swept = True
        self.sweeps.append((i, -1))
      if not isna(lsl) and not self.low_broken and not self.ext_low_swept and self.l[i] < lsl - atr * p.sweep_buffer and c > lsl:
        self.ext_low_swept = True
        self.sweeps.append((i, 1))
    up = not isna(self.last_swing_high) and not self.high_broken and c > self.last_swing_high + thr
    down = not isna(self.last_swing_low) and not self.low_broken and c < self.last_swing_low - thr
    # v2.13: a close beyond the TRAILING protected level is the CHoCH, even when the last confirmed swing is further away
    prot_up = prot_down = False
    if p.protected_mode == "trailing":
      if self.bias == -1 and not isna(self.protected_high) and self.protected_high_bar is not None and c > self.protected_high + thr:
        prot_up = True
      elif self.bias == 1 and not isna(self.protected_low) and self.protected_low_bar is not None and c < self.protected_low - thr:
        prot_down = True
    if prot_up or up:
      level, lbar = (self.protected_high, self.protected_high_bar) if prot_up else (self.last_swing_high, self.last_high_bar)
      choch = prot_up or (self.bias == -1 and not isna(self.protected_high))
      ev["up_break"], ev["choch"], ev["protected_choch"] = True, choch, prot_up
      ev["broken_high"], ev["broken_high_bar"] = level, lbar
      ev["break_bar"] = self.first_close_beyond(i, lbar, level, True)
      if up:
        self.high_broken = True
      prev_bias = self.bias
      self.bias = 1
      self.dev_dir, self.dev_high, self.dev_high_bar, self.dev_low, self.dev_low_bar = 1, self.h[i], i, NAN, None
      self.last_break_bar, self.last_break_choch = i, choch
      if prot_up:
        hit = self.ring_pick(self.i_low_ring, lbar, ev["break_bar"]) if p.protected_hl else None
        if hit:
          self.protected_low, self.protected_low_bar = hit[1], hit[0]
        else:
          seg = self.l[lbar:i + 1]
          self.protected_low, self.protected_low_bar = min(seg), lbar + seg.index(min(seg))
      elif p.protected_mode != "trailing" or prev_bias != 1 or isna(self.protected_low) or self.last_swing_low > self.protected_low:
        self.protected_low, self.protected_low_bar = self.last_swing_low, self.last_low_bar
    elif prot_down or down:
      level, lbar = (self.protected_low, self.protected_low_bar) if prot_down else (self.last_swing_low, self.last_low_bar)
      choch = prot_down or (self.bias == 1 and not isna(self.protected_low))
      ev["down_break"], ev["choch"], ev["protected_choch"] = True, choch, prot_down
      ev["broken_low"], ev["broken_low_bar"] = level, lbar
      ev["break_bar"] = self.first_close_beyond(i, lbar, level, False)
      if down:
        self.low_broken = True
      prev_bias = self.bias
      self.bias = -1
      self.dev_dir, self.dev_low, self.dev_low_bar, self.dev_high, self.dev_high_bar = -1, self.l[i], i, NAN, None
      self.last_break_bar, self.last_break_choch = i, choch
      if prot_down:
        hit = self.ring_pick(self.i_high_ring, lbar, ev["break_bar"]) if p.protected_hl else None
        if hit:
          self.protected_high, self.protected_high_bar = hit[1], hit[0]
        else:
          seg = self.h[lbar:i + 1]
          self.protected_high, self.protected_high_bar = max(seg), lbar + seg.index(max(seg))
      elif p.protected_mode != "trailing" or prev_bias != -1 or isna(self.protected_high) or self.last_swing_high < self.protected_high:
        self.protected_high, self.protected_high_bar = self.last_swing_high, self.last_high_bar
    return ev


class LegacyPipeline:
  """v2.0 zone pipeline: OB only on external breaks, shared FIFO zone store,
  shared visible budget with POI-first ranking."""

  def __init__(self, bars, p):
    self.s = Structure(bars, p)
    self.p = p
    self.zones = []
    self.ob_log = []  # per external-break OB diagnostics
    self.ob_life = []  # (bars alive, end reason, touched) for retired OBs
    self.max_store = 0
    self.counts = dict(ob_added=0, ob_rejected=0, evicted_ob=0, evicted_other=0, invalidated_ob=0, aged_ob=0,
                       fvg_added=0, sd_added=0, sd_rejected=0, sd_rejected_invalid=0, sd_drawn_tested=0)

  # ---- zone store -------------------------------------------------------
  def add_zone(self, origin, top, bottom, side, kind, title, born):
    if isna(top) or isna(bottom) or not top > bottom:
      return None
    for z in self.zones:
      if z.kind == kind and z.side == side:
        overlap = min(top, z.top) - max(bottom, z.bottom)
        smaller = min(top - bottom, z.top - z.bottom)
        if overlap > 0 and smaller > 0 and overlap / smaller >= 0.85:
          return None
    z = Zone(origin, top, bottom, side, kind, title, born)
    self.zones.append(z)
    if len(self.zones) > self.p.max_zones:
      old = self.zones.pop(0)
      if old.kind == 1:
        self.counts["evicted_ob"] += 1
      else:
        self.counts["evicted_other"] += 1
    return z

  # ---- per-bar ----------------------------------------------------------
  def run(self):
    s, p = self.s, self.p
    for i in range(s.n):
      ev = s.step_pivots(i)
      atr = s.atr[i]
      if isna(atr):
        continue
      self.legacy_ob(i, ev)
      self.supply_demand(i, ev)
      self.fvg(i)
      self.lifecycle(i)
      self.max_store = max(self.max_store, len(self.zones))
    return self

  def legacy_ob(self, i, ev):
    s, p = self.s, self.p
    atr = s.atr[i]
    for bull in (True, False):
      if not (ev["up_break"] if bull else ev["down_break"]):
        continue
      reasons = []
      added = None
      for k in range(1, p.ob_lookback + 1):
        if i - k < 0:
          break
        rng = s.h[i - k] - s.l[i - k]
        disp = (s.c[i] - s.h[i - k]) if bull else (s.l[i - k] - s.c[i])
        opp = (s.c[i - k] < s.o[i - k]) if bull else (s.c[i - k] > s.o[i - k])
        a_k = s.atr[i - k] if not isna(s.atr[i - k]) else atr
        if not opp:
          reasons.append("k=%d same-colour" % k)
          continue
        if disp < atr * p.ob_impulse_atr:
          reasons.append("k=%d disp %.2f<%.2f ATR" % (k, disp / atr, p.ob_impulse_atr))
          continue
        if rng > a_k * 2.5:
          reasons.append("k=%d range %.2f>2.5 ATR" % (k, rng / a_k))
          continue
        up = s.h[i - k] if p.ob_wicks else max(s.o[i - k], s.c[i - k])
        lo = s.l[i - k] if p.ob_wicks else min(s.o[i - k], s.c[i - k])
        added = self.add_zone(i - k, up, lo, 1 if bull else -1, 1, "Bull OB" if bull else "Bear OB", i)
        if added is None:
          reasons.append("k=%d duplicate" % k)
        break
      self.ob_log.append(dict(bar=i, t=s.t[i], bull=bull, event="CHoCH" if ev["choch"] else "BOS",
                              ok=added is not None, reasons=reasons[:3], span_bars=(i - (ev["broken_high_bar"] if bull else ev["broken_low_bar"])) if (ev["broken_high_bar"] if bull else ev["broken_low_bar"]) is not None else None))
      if added is not None:
        self.counts["ob_added"] += 1
      else:
        self.counts["ob_rejected"] += 1

  def fresh_scan(self, side, top, bottom, j, i):
    """Bars j + external_len + 1 .. i against a swing zone: (closed beyond the far edge, first touching bar or -1, deepest penetration 0..1)."""
    s = self.s
    height = top - bottom
    first, depth = -1, 0.0
    for k in range(j + s.external_len + 1, i + 1):
      if (s.c[k] < bottom) if side == 1 else (s.c[k] > top):
        return True, first, depth
      pen = ((top - s.l[k]) / height) if side == 1 else ((s.h[k] - bottom) / height)
      if pen > 0:
        if first < 0:
          first = k
        depth = max(depth, min(1.0, pen))
    return False, first, depth

  def _add_swing_sd(self, i, j, top, bottom, side, title):
    p = self.p
    first, depth = -1, 0.0
    invalid, first, depth = self.fresh_scan(side, top, bottom, j, i)
    # diagnostic (every mode): how many swing zones had already been closed through / retested when they were published
    self.counts["sd_scan_invalid"] = self.counts.get("sd_scan_invalid", 0) + (1 if invalid else 0)
    self.counts["sd_scan_touched"] = self.counts.get("sd_scan_touched", 0) + (1 if (not invalid and first >= 0) else 0)
    if not p.fresh_sd:
      first, depth = -1, 0.0
    elif invalid:
      self.counts["sd_rejected"] += 1
      self.counts["sd_rejected_invalid"] += 1
      return
    z = self.add_zone(j, top, bottom, side, 3, title, i)
    if z is not None:
      self.counts["sd_added"] += 1
      if first >= 0:
        # drawn as it is: already tested, never Fresh, never actionable, never a trade trigger
        z.touched, z.state, z.first_touch, z.mitigation = True, "Tested", first, depth
        self.counts["sd_drawn_tested"] += 1

  def supply_demand(self, i, ev):
    s, p = self.s, self.p
    if ev["new_major_low"]:
      j = ev["major_low_bar"]
      a_sw = s.atr[j] if p.structure_mode == "displacement" else s.atr[i - s.external_len]
      if isna(a_sw):
        a_sw = s.atr[i]
      off = i - j
      if off <= 799:
        upper = min(max(s.o[j], s.c[j]), ev["major_low_price"] + a_sw * p.sd_max_width_atr)
        if ev["ext_high"] - ev["major_low_price"] >= a_sw * p.sd_departure_atr:
          self._add_swing_sd(i, j, upper, ev["major_low_price"], 1, "Demand")
    if ev["new_major_high"]:
      j = ev["major_high_bar"]
      a_sw = s.atr[j] if p.structure_mode == "displacement" else s.atr[i - s.external_len]
      if isna(a_sw):
        a_sw = s.atr[i]
      lower = max(min(s.o[j], s.c[j]), ev["major_high_price"] - a_sw * p.sd_max_width_atr)
      if ev["major_high_price"] - ev["ext_low"] >= a_sw * p.sd_departure_atr:
        self._add_swing_sd(i, j, ev["major_high_price"], lower, -1, "Supply")

  def fvg(self, i):
    s, p = self.s, self.p
    if i < 2:
      return
    atr = s.atr[i]
    a1 = s.atr[i - 1] if not isna(s.atr[i - 1]) else atr
    body = abs(s.c[i - 1] - s.o[i - 1])
    rng = s.h[i - 1] - s.l[i - 1]
    bull_mid, bear_mid = s.c[i - 1] > s.o[i - 1], s.c[i - 1] < s.o[i - 1]
    for bull in (True, False):
      gap = (s.l[i] - s.h[i - 2]) if bull else (s.l[i - 2] - s.h[i])
      if not gap > 0:
        continue
      if p.fvg_def == "engine":
        ok = gap >= p.fvg_engine_atr * atr and body >= p.fvg_engine_atr * atr and rng > 0 and body / rng >= p.fvg_engine_body_ratio and (bull_mid if bull else bear_mid)
      else:
        ok = gap >= atr * p.min_gap_atr and body >= a1 * p.fvg_impulse_atr
        if p.fvg_def == "loose":
          ok = ok and (bull_mid if bull else bear_mid)
      if ok:
        z = self.add_zone(i - 2, s.l[i] if bull else s.l[i - 2], s.h[i - 2] if bull else s.h[i], 1 if bull else -1, 2, "Bull FVG" if bull else "Bear FVG", i)
        if z:
          self.counts["fvg_added"] += 1

  def lifecycle(self, i):
    s, p = self.s, self.p
    keep = []
    for z in self.zones:
      if i > z.born:
        h = z.top - z.bottom
        pen = (z.top - s.l[i]) / h if z.side == 1 else (s.h[i] - z.bottom) / h
        z.mitigation = max(z.mitigation, max(0.0, min(1.0, pen)))
        invalid = (z.mitigation >= 1.0) if z.kind == 2 else ((s.c[i] < z.bottom) if z.side == 1 else (s.c[i] > z.top))
        aged = i - z.born > p.zone_max_age
        if invalid or aged:
          if z.kind == 1:
            self.counts["invalidated_ob" if invalid else "aged_ob"] += 1
            self.ob_life.append((i - z.born, "invalidated" if invalid else "aged", z.touched))
          continue
        if not z.touched and z.mitigation > 0:
          z.touched = True
      keep.append(z)
    self.zones = keep

  # ---- display selection (last bar) ------------------------------------
  def display(self):
    s, p = self.s, self.p
    i = s.n - 1
    atr, close = s.atr[i], s.c[i]
    bias = s.bias
    for z in self.zones:
      z.visible = False
    # POI qualification
    for z in self.zones:
      has = {1: z.kind == 1, 2: z.kind == 2, 3: z.kind == 3}
      for o in self.zones:
        if o is not z and o.side == z.side:
          ov = min(z.top, o.top) - max(z.bottom, o.bottom)
          sm = min(z.top - z.bottom, o.top - o.bottom)
          if ov > 0 and sm > 0 and ov / sm >= 0.25:
            has[o.kind] = True
      comp = (has[1] and has[2]) or (has[1] and has[3]) or (has[2] and has[3])
      related = s.last_break_bar is not None and z.born >= s.last_break_bar - p.ob_lookback and z.born - s.last_break_bar <= p.poi_structure_window
      struct_ob = z.kind == 1 and z.side == bias and related
      disp_sd = z.kind == 3 and z.side == bias
      swept = z.kind == 2 and z.side == bias and s.last_sweep_side == z.side and s.last_sweep_bar is not None and abs(z.born - s.last_sweep_bar) <= p.poi_structure_window
      z.poi = comp or struct_ob or disp_sd or swept
    trans = s.last_break_choch and s.last_break_bar is not None and i - s.last_break_bar <= s.external_len * 2
    expansion = (bias == 1 and not isna(s.last_swing_high) and close > s.last_swing_high) or (bias == -1 and not isna(s.last_swing_low) and close < s.last_swing_low)
    rng = (not trans) and (not expansion) and not isna(s.last_swing_high) and not isna(s.last_swing_low) and s.last_swing_low <= close <= s.last_swing_high
    for pick in range(p.visible_zones):
      best, best_key = None, None
      has_bull = any(z.visible and z.side == 1 for z in self.zones)
      has_bear = any(z.visible and z.side == -1 for z in self.zones)
      for z in self.zones:
        if z.visible:
          continue
        mid = (z.top + z.bottom) / 2
        dist = abs(close - mid) / max(atr, 1e-9)
        if dist > p.zone_display_distance_atr:
          continue
        redundant = False
        for o in self.zones:
          if o.visible and o.side == z.side:
            ov = min(z.top, o.top) - max(z.bottom, o.bottom)
            sm = min(z.top - z.bottom, o.top - o.bottom)
            if ov > 0 and sm > 0 and ov / sm >= 0.60:
              redundant = True
        if redundant:
          continue
        fresh = not z.touched
        aligned = z.side == bias
        adds_missing = pick > 0 and ((z.side == 1 and not has_bull) or (z.side == -1 and not has_bear))
        ctx = (expansion and aligned) or (rng and z.kind == 3) or (trans and z.poi)
        age = i - z.born
        key = (z.poi, adds_missing, fresh, ctx, aligned, -dist, -age)
        if best is None or key > best_key:
          best, best_key = z, key
      if best is None:
        break
      best.visible = True
    return self

  def audit(self):
    s, p = self.s, self.p
    i = s.n - 1
    atr, close = s.atr[i], s.c[i]
    out = dict(active_ob=0, visible_ob=0, far_ob=0, ranked_out_ob=0, active_fvg=0, active_sd=0)
    rows = []
    for z in self.zones:
      mid = (z.top + z.bottom) / 2
      dist = abs(close - mid) / atr
      if z.kind == 1:
        out["active_ob"] += 1
        out["visible_ob"] += 1 if z.visible else 0
        if not z.visible:
          if dist > p.zone_display_distance_atr:
            out["far_ob"] += 1
          else:
            out["ranked_out_ob"] += 1
        rows.append((z.title, z.born, z.origin, z.bottom, z.top, round(dist, 1), z.visible, z.touched))
      elif z.kind == 2:
        out["active_fvg"] += 1
      else:
        out["active_sd"] += 1
    return out, rows


# ---------------------------------------------------------------------------
# v2.1
# ---------------------------------------------------------------------------
@dataclass
class P21(Params):
  ob_search_bars: int = 40
  ob_displacement_atr: float = 1.0
  ob_impulse_body_atr: float = 0.6
  ob_max_range_atr: float = 2.5
  ob_base_tol_atr: float = 0.50
  ob_mitigation: str = "Close"  # Close | Wick | Midpoint
  show_internal_ob: bool = True
  max_vis_bull_ob: int = 2
  max_vis_bear_ob: int = 2
  ob_display_distance_atr: float = 25.0
  # liquidity
  eq_min_separation: int = 8
  eq_min_pullback_atr: float = 0.5
  sweep_rule: str = "Within N bars"
  sweep_return_bars: int = 2
  liq_break_buffer_atr: float = 0.10
  liq_accept_closes: int = 2
  consumed_policy: str = "Fade, then retire"
  consumed_fade_bars: int = 40
  liq_max_age: int = 1500
  max_liquidity: int = 24
  visible_liquidity: int = 6
  liq_display_distance_atr: float = 30.0
  grab_min_wick_atr: float = 0.20
  ring_size: int = 80
  scale_lookback_bars: int = 300
  protect_scale: bool = True
  scale_window_atr: float = 15.0

  def scale_ok(self, price, close, atr, band_hi, band_lo):
    """Candle-scale protection: near price (ATR window) or inside the range the candles already span."""
    if not self.protect_scale:
      return True
    return abs(price - close) / atr <= self.scale_window_atr or (band_lo - atr <= price <= band_hi + atr)


class V21Pipeline(LegacyPipeline):
  def __init__(self, bars, p):
    super().__init__(bars, p)
    self.ob_stats = dict(added=0, rejected=0, replaced=0, covered=0, mitigated=0, invalidated=0, aged=0)
    self.ob_dbg = []  # per-candidate diagnostics (accepted and rejected)
    self.ob_deaths = []

  # eviction prefers FVG, then S/D, then OB so imbalance volume cannot erase blocks
  def add_zone(self, origin, top, bottom, side, kind, title, born, cls=0, event=""):
    if isna(top) or isna(bottom) or not top > bottom:
      return None
    for z in self.zones:
      if z.kind == kind and z.side == side and kind != 1:
        overlap = min(top, z.top) - max(bottom, z.bottom)
        smaller = min(top - bottom, z.top - z.bottom)
        if overlap > 0 and smaller > 0 and overlap / smaller >= 0.85:
          return None
    z = Zone(origin, top, bottom, side, kind, title, born, cls=cls, event=event)
    self.zones.append(z)
    if len(self.zones) > self.p.max_zones:
      for k in (2, 3, 1):
        idx = next((j for j, q in enumerate(self.zones) if q.kind == k), None)
        if idx is not None:
          old = self.zones.pop(idx)
          self.counts["evicted_ob" if old.kind == 1 else "evicted_other"] += 1
          break
    return z

  def run(self):
    s = self.s
    for i in range(s.n):
      ev = s.step_pivots(i)
      if isna(s.atr[i]):
        continue
      self.v21_ob(i, ev)
      self.supply_demand(i, ev)
      self.fvg(i)
      self.lifecycle(i)
      self.max_store = max(self.max_store, len(self.zones))
    return self

  def legacy_ob(self, i, ev):  # disabled
    pass

  # ---- order block construction ----------------------------------------
  def v21_ob(self, i, ev):
    srcs = []
    if ev["up_break"]:
      srcs.append((True, 2, "External " + ("CHoCH" if ev["choch"] else "BOS"), ev["broken_high_bar"], ev.get("break_bar", i)))
    if ev["down_break"]:
      srcs.append((False, 2, "External " + ("CHoCH" if ev["choch"] else "BOS"), ev["broken_low_bar"], ev.get("break_bar", i)))
    if self.p.show_internal_ob:
      if ev["i_up_break"]:
        srcs.append((True, 1, "Internal " + ("CHoCH" if ev["i_choch"] else "BOS"), ev["i_broken_high_bar"], i))
      if ev["i_down_break"]:
        srcs.append((False, 1, "Internal " + ("CHoCH" if ev["i_choch"] else "BOS"), ev["i_broken_low_bar"], i))
    for bull, cls, event, anchor, break_bar in srcs:
      self.build_ob(i, bull, cls, event, anchor, break_bar)

  def build_ob(self, i, bull, cls, event, anchor, break_bar=None):
    s, p = self.s, self.p
    atr = s.atr[i]
    # v2.13: an external break is published late (after its swing was confirmed), so the block is searched from the bar that really broke
    off = (i - break_bar) if (p.ob_break_anchor and break_bar is not None) else 0
    max_back = min(p.ob_search_bars, i - off - 1)
    window = max_back if anchor is None else min(max_back, (i - off) - anchor - 1)
    d = dict(bar=i, t=s.t[i], bull=bull, cls=cls, event=event, ok=False, reason="", src=None, top=NAN, bottom=NAN)
    self.ob_dbg.append(d)
    if window < 2:
      d["reason"] = "window <2 bars"
      self.ob_stats["rejected"] += 1
      return
    extreme = min(s.l[i - k] for k in range(off + 1, off + window + 1)) if bull else max(s.h[i - k] for k in range(off + 1, off + window + 1))
    best_k, best_val = -1, NAN
    for k in range(off + 1, off + window + 1):
      opp = s.c[i - k] < s.o[i - k] if bull else s.c[i - k] > s.o[i - k]
      if opp:
        v = s.l[i - k] if bull else s.h[i - k]
        if best_k < 0 or (v < best_val if bull else v > best_val):
          best_k, best_val = k, v
    if best_k < 0:
      d["reason"] = "no opposite-colour candle in window"
      self.ob_stats["rejected"] += 1
      return
    gap = (best_val - extreme) if bull else (extreme - best_val)
    d["src"] = s.t[i - best_k]
    hi, lo = s.h[i - best_k], s.l[i - best_k]
    if gap > p.ob_base_tol_atr * atr:
      d["reason"] = "base is not an opposite candle (%.2f ATR from leg extreme)" % (gap / atr)
      self.ob_stats["rejected"] += 1
      return
    if hi - lo > p.ob_max_range_atr * atr:
      d["reason"] = "origin too wide (%.2f ATR)" % ((hi - lo) / atr)
      self.ob_stats["rejected"] += 1
      return
    net = (s.c[i - off] - hi) if bull else (lo - s.c[i - off])
    if net < p.ob_displacement_atr * atr:
      d["reason"] = "weak displacement (%.2f ATR < %.2f)" % (net / atr, p.ob_displacement_atr)
      self.ob_stats["rejected"] += 1
      return
    body = max(((s.c[i - k] - s.o[i - k]) if bull else (s.o[i - k] - s.c[i - k])) for k in range(off, best_k))
    if body < p.ob_impulse_body_atr * atr:
      d["reason"] = "no impulse candle (max body %.2f ATR < %.2f)" % (body / atr, p.ob_impulse_body_atr)
      self.ob_stats["rejected"] += 1
      return
    top, bottom = hi, lo  # wick-inclusive (obWicks default)
    mid = (top + bottom) / 2
    for k in range(0, best_k):
      if bull:
        dead = s.c[i - k] < bottom
        mit = s.l[i - k] <= bottom if p.ob_mitigation == "Wick" else s.l[i - k] <= mid if p.ob_mitigation == "Midpoint" else dead
      else:
        dead = s.c[i - k] > top
        mit = s.h[i - k] >= top if p.ob_mitigation == "Wick" else s.h[i - k] >= mid if p.ob_mitigation == "Midpoint" else dead
      if dead or mit:
        d["reason"] = "already %s before detection" % ("invalidated" if dead else "mitigated")
        self.ob_stats["rejected"] += 1
        return
    side = 1 if bull else -1
    # prefer the newest block; a swing block is never replaced by an internal one
    for z in list(self.zones):
      if z.kind == 1 and z.side == side:
        ov = min(top, z.top) - max(bottom, z.bottom)
        sm = min(top - bottom, z.top - z.bottom)
        if ov > 0 and sm > 0 and ov / sm >= 0.5:
          if cls < z.cls:
            d["reason"] = "covered by existing swing OB"
            self.ob_stats["covered"] += 1
            return
          self.zones.remove(z)
          self.ob_stats["replaced"] += 1
          cls = max(cls, z.cls)
    z = self.add_zone(i - best_k, top, bottom, side, 1, ("Bull " if bull else "Bear ") + ("OB" if cls == 2 else "iOB"), i, cls, event)
    d.update(ok=True, top=top, bottom=bottom, reason="accepted")
    self.ob_stats["added"] += 1

  # ---- POI components (shared by the display pass and the trade plan) -----------------------------
  def components(self, z):
    """Kinds (1 OB, 2 FVG, 3 S/D) present in `z` and in the same-side zones overlapping it by at least a quarter. With composite_independent
    an overlapping zone counts only when its origin differs by more than 3 bars: an OB and the FVG its own impulse printed are one event."""
    has = {1: z.kind == 1, 2: z.kind == 2, 3: z.kind == 3}
    for o in self.zones:
      if o is z or o.side != z.side:
        continue
      ov = min(z.top, o.top) - max(z.bottom, o.bottom)
      sm = min(z.top - z.bottom, o.top - o.bottom)
      if ov > 0 and sm > 0 and ov / sm >= 0.25:
        if self.p.composite_independent and abs(o.origin - z.origin) <= 3:
          continue
        has[o.kind] = True
    return has

  def swept_imbalance(self, z):
    """An FVG on the bias side whose impulse followed a sweep in its favour."""
    s, p = self.s, self.p
    if not (z.kind == 2 and z.side == s.bias):
      return False
    if p.swept_before_impulse:
      return any(side == z.side and z.origin - 6 <= bar <= z.origin for bar, side in s.sweeps)
    return s.last_sweep_side == z.side and s.last_sweep_bar is not None and abs(z.born - s.last_sweep_bar) <= p.poi_structure_window

  def lifecycle(self, i):
    s, p = self.s, self.p
    keep = []
    for z in self.zones:
      if i > z.born:
        h = z.top - z.bottom
        pen = (z.top - s.l[i]) / h if z.side == 1 else (s.h[i] - z.bottom) / h
        z.mitigation = max(z.mitigation, max(0.0, min(1.0, pen)))
        mid = (z.top + z.bottom) / 2
        far_close = (s.c[i] < z.bottom) if z.side == 1 else (s.c[i] > z.top)
        if z.kind == 1:
          if p.ob_mitigation == "Wick":
            mit = (s.l[i] <= z.bottom) if z.side == 1 else (s.h[i] >= z.top)
          elif p.ob_mitigation == "Midpoint":
            mit = (s.l[i] <= mid) if z.side == 1 else (s.h[i] >= mid)
          else:
            mit = far_close
          invalid = far_close or mit
        elif z.kind == 2:
          invalid = z.mitigation >= 1.0
        else:
          invalid = far_close
        aged = i - z.born > p.zone_max_age
        if invalid or aged:
          if z.kind == 1:
            key = "invalidated" if far_close else "mitigated" if invalid else "aged"
            self.ob_stats[key] += 1
            self.ob_deaths.append((z, i, key))
          continue
        if not z.touched and z.mitigation > 0:
          z.touched = True
          z.state = "Tested"
          z.first_touch = i
      keep.append(z)
    self.zones = keep

  # ---- display ----------------------------------------------------------
  def display(self):
    s, p = self.s, self.p
    # reuse legacy ranking for non-OB kinds
    obs = [z for z in self.zones if z.kind == 1]
    others = self.zones
    self.zones = [z for z in self.zones if z.kind != 1] + obs  # keep OBs for POI overlap purposes
    super_hidden = obs
    i = s.n - 1
    atr, close = s.atr[i], s.c[i]
    bias = s.bias
    band_hi = max(s.h[-p.scale_lookback_bars:])
    band_lo = min(s.l[-p.scale_lookback_bars:])
    # OB-specific selection
    for z in obs:
      z.visible = False
      z.hidden_reason = ""
    n_side = {1: 0, -1: 0}
    budget = {1: p.max_vis_bull_ob, -1: p.max_vis_bear_ob}
    pool = list(obs)
    for _ in range(p.max_vis_bull_ob + p.max_vis_bear_ob):
      best, best_score = None, None
      for z in pool:
        if z.visible or n_side[z.side] >= budget[z.side]:
          continue
        dist = abs(close - (z.top + z.bottom) / 2) / atr
        if dist > p.ob_display_distance_atr or not p.scale_ok((z.top + z.bottom) / 2, close, atr, band_hi, band_lo):
          continue
        if any(o.visible and o.side == z.side and min(z.top, o.top) - max(z.bottom, o.bottom) > 0 and (min(z.top, o.top) - max(z.bottom, o.bottom)) / min(z.top - z.bottom, o.top - o.bottom) >= 0.5 for o in pool):
          continue
        score = z.cls * 10000 + (5000 if not z.touched else 0) + (1000 if z.side == bias else 0) + max(0.0, 500 - dist * 10) - (i - z.born) * 0.001
        if best is None or score > best_score:
          best, best_score = z, score
      if best is None:
        break
      best.visible = True
      n_side[best.side] += 1
    for z in obs:
      if not z.visible:
        dist = abs(close - (z.top + z.bottom) / 2) / atr
        z.hidden_reason = ("far (%.1f ATR > %.0f)" % (dist, p.ob_display_distance_atr) if dist > p.ob_display_distance_atr else "outside the candle range") if (dist > p.ob_display_distance_atr or not p.scale_ok((z.top + z.bottom) / 2, close, atr, band_hi, band_lo)) else ("overlap" if any(o.visible and o.side == z.side and min(z.top, o.top) > max(z.bottom, o.bottom) for o in obs) else "side budget")
    # generic zones (FVG, S/D): legacy rule, OBs excluded from the shared budget
    saved = self.zones
    self.zones = [z for z in saved if z.kind != 1]
    # POI qualification needs OBs; compute on the full list first
    full = saved
    self.zones = full
    # emulate legacy generic pass by temporarily hiding OBs from the candidate pool
    for z in full:
      if z.kind != 1:
        z.visible = False
    self._generic_pick(full, obs)
    return self

  def _generic_pick(self, full, obs):
    s, p = self.s, self.p
    i = s.n - 1
    atr, close, bias = s.atr[i], s.c[i], s.bias
    band_hi = max(s.h[-p.scale_lookback_bars:])
    band_lo = min(s.l[-p.scale_lookback_bars:])
    for z in full:
      has = self.components(z)
      comp = (has[1] and has[2]) or (has[1] and has[3]) or (has[2] and has[3])
      related = s.last_break_bar is not None and z.born >= s.last_break_bar - p.ob_lookback and z.born - s.last_break_bar <= p.poi_structure_window
      z.poi = comp or (z.kind == 1 and z.side == bias and related) or (z.kind == 3 and z.side == bias) or self.swept_imbalance(z)
    trans = s.last_break_choch and s.last_break_bar is not None and i - s.last_break_bar <= s.external_len * 2
    expansion = (bias == 1 and not isna(s.last_swing_high) and close > s.last_swing_high) or (bias == -1 and not isna(s.last_swing_low) and close < s.last_swing_low)
    rng = (not trans) and (not expansion) and not isna(s.last_swing_high) and not isna(s.last_swing_low) and s.last_swing_low <= close <= s.last_swing_high
    for pick in range(p.visible_zones):
      best, best_key = None, None
      has_bull = any(z.visible and z.side == 1 for z in full)
      has_bear = any(z.visible and z.side == -1 for z in full)
      for z in full:
        if z.visible or z.kind == 1:
          continue
        mid = (z.top + z.bottom) / 2
        dist = abs(close - mid) / max(atr, 1e-9)
        if dist > p.zone_display_distance_atr or not p.scale_ok(mid, close, atr, band_hi, band_lo):
          continue
        if any(o.visible and o.side == z.side and min(z.top, o.top) - max(z.bottom, o.bottom) > 0 and (min(z.top, o.top) - max(z.bottom, o.bottom)) / min(z.top - z.bottom, o.top - o.bottom) >= 0.60 for o in full):
          continue
        fresh = not z.touched
        aligned = z.side == bias
        adds_missing = pick > 0 and ((z.side == 1 and not has_bull) or (z.side == -1 and not has_bear))
        ctx = (expansion and aligned) or (rng and z.kind == 3) or (trans and z.poi)
        key = (z.poi, adds_missing, fresh, ctx, aligned, -dist, -(i - z.born))
        if best is None or key > best_key:
          best, best_key = z, key
      if best is None:
        break
      best.visible = True

  def audit(self):
    s, p = self.s, self.p
    i = s.n - 1
    atr, close = s.atr[i], s.c[i]
    obs = [z for z in self.zones if z.kind == 1]
    out = dict(active_ob=len(obs), visible_ob=sum(1 for z in obs if z.visible), bull=sum(1 for z in obs if z.side == 1), bear=sum(1 for z in obs if z.side == -1),
               hidden=[(z.title, getattr(z, "hidden_reason", "")) for z in obs if not z.visible])
    rows = [(z.title, z.cls, z.event, s.t[z.origin], s.t[z.born], z.bottom, z.top, "Tested" if z.touched else "Fresh", z.visible, getattr(z, "hidden_reason", "")) for z in obs]
    return out, rows


# ---------------------------------------------------------------------------
# v2.1 liquidity pool model
# ---------------------------------------------------------------------------
@dataclass
class Pool:
  side: int          # +1 BSL (above), -1 SSL (below)
  top: float
  bottom: float
  birth: int         # bar of the first qualifying pivot
  confirm: int       # bar at which the pool qualified
  last_test: int
  tests: int
  degree: int        # 1 internal EQ, 2 external swing, 3 external + EQ / session
  kind: str
  state: int = 0     # 0 active, 1 swept, 2 consumed
  state_bar: int = -1
  pending: int = -1
  extreme: float = NAN
  extreme_bar: int = -1
  beyond: int = 0
  uid: int = 0

  @property
  def edge(self):
    return self.top if self.side == 1 else self.bottom


class LiquidityModel:
  """Level-first BSL/SSL pools: external swings, EQH/EQL bands, sweep vs consumed states."""

  def __init__(self, st, p):
    self.s, self.p = st, p
    self.pools = []
    self.ring = []  # unpromoted internal pivots: [price, bar, side]
    self.events = []  # (bar, kind, side, price, degree)
    self.stats = dict(created=0, merged=0, swept=0, consumed=0, retired=0, pivots_seen=0, grab_marks=0)
    self.grabs = []  # (sweep bar, side, extreme bar, wick bottom, wick top) for rejection-wick highlights

  def pullback_ok(self, i, side, p_bar, n_bar, a_price, b_price, tol, atr):
    lo_i, hi_i = i - n_bar, i - p_bar  # offsets back from current bar
    s = self.s
    seg = range(p_bar, n_bar + 1)
    if side == 1:
      mx = max(s.h[k] for k in seg)
      mn = min(s.l[k] for k in seg)
      return mx <= max(a_price, b_price) + tol and mn <= min(a_price, b_price) - self.p.eq_min_pullback_atr * atr
    mx = max(s.h[k] for k in seg)
    mn = min(s.l[k] for k in seg)
    return mn >= min(a_price, b_price) - tol and mx >= max(a_price, b_price) + self.p.eq_min_pullback_atr * atr

  def ingest(self, i, price, bar, side, degree, tol, atr):
    p = self.p
    # 1) same-test / merge with an existing active pool
    for pool in self.pools:
      if pool.state != 0 or pool.side != side:
        continue
      ref = pool.edge
      if abs(price - ref) <= tol:
        if bar - pool.last_test < p.eq_min_separation:
          return "same-test"
        if not self.pullback_ok(i, side, pool.last_test, bar, ref, price, tol, atr):
          return "no-pullback"
        newtop, newbot = max(pool.top, price), min(pool.bottom, price)
        if newtop - newbot <= 2 * tol:
          pool.top, pool.bottom = newtop, newbot
          pool.tests += 1
          pool.last_test = bar
          pool.degree = 3 if (degree >= 2 or pool.degree >= 2) else 1
          pool.kind = "EQ"
          self.stats["merged"] += 1
          return "merged"
    # 2) ring partner
    for r in reversed(list(self.ring)):
      if r[2] != side or abs(price - r[0]) > tol:
        continue
      if bar - r[1] < p.eq_min_separation:
        if degree >= 2:
          self.ring.remove(r)
        continue
      if i - r[1] > 400:
        continue
      if not self.pullback_ok(i, side, r[1], bar, r[0], price, tol, atr):
        continue
      self.ring.remove(r)
      self.new_pool(i, side, max(price, r[0]), min(price, r[0]), r[1], 2, 3 if degree >= 2 else 1, "EQ", bar)
      return "eq-created"
    if degree >= 2:
      self.new_pool(i, side, price, price, bar, 1, 2, "Swing", bar)
      return "swing-created"
    self.ring.append([price, bar, side])
    if len(self.ring) > p.ring_size:
      self.ring.pop(0)
    return "ring"

  def new_pool(self, i, side, top, bottom, birth, tests, degree, kind, last_test):
    self.uid_seq = getattr(self, "uid_seq", 0) + 1
    self.pools.append(Pool(side, top, bottom, birth, i, last_test, tests, degree, kind, uid=self.uid_seq))
    self.stats["created"] += 1
    if len(self.pools) > self.p.max_liquidity:
      # evict consumed first, then lowest degree, oldest
      idx = min(range(len(self.pools)), key=lambda j: (self.pools[j].state == 0, self.pools[j].degree, self.pools[j].confirm))
      self.pools.pop(idx)

  def step(self, i, ev):
    s, p = self.s, self.p
    atr = s.atr[i]
    if isna(atr):
      return
    if ev["new_ih"]:
      self.stats["pivots_seen"] += 1
      self.ingest(i, ev["ih"], i - s.internal_len, 1, 1, s.a_at(i - s.internal_len) * p.equal_tolerance if not isna(s.a_at(i - s.internal_len)) else atr * p.equal_tolerance, atr)
    if ev["new_il"]:
      self.stats["pivots_seen"] += 1
      self.ingest(i, ev["il"], i - s.internal_len, -1, 1, s.a_at(i - s.internal_len) * p.equal_tolerance if not isna(s.a_at(i - s.internal_len)) else atr * p.equal_tolerance, atr)
    if ev["new_major_high"]:
      tol = (s.a_at(i - s.external_len) if not isna(s.a_at(i - s.external_len)) else atr) * p.equal_tolerance
      self.ingest(i, ev["major_high_price"], ev["major_high_bar"], 1, 2, tol, atr)
    if ev["new_major_low"]:
      tol = (s.a_at(i - s.external_len) if not isna(s.a_at(i - s.external_len)) else atr) * p.equal_tolerance
      self.ingest(i, ev["major_low_price"], ev["major_low_bar"], -1, 2, tol, atr)
    # a close-confirmed structural break consumes the pool resting on the broken external swing
    for flag, side, level in (("up_break", 1, ev["broken_high"]), ("down_break", -1, ev["broken_low"])):
      if ev[flag]:
        for pool in self.pools:
          if pool.state == 0 and pool.side == side and abs(pool.edge - level) <= atr * p.equal_tolerance and pool.confirm < i:
            pool.state, pool.state_bar = 2, i
            self.stats["consumed"] += 1
            self.events.append((i, "consumed-bos", side, pool.edge, pool.degree, pool.uid))
    # state machine
    for pool in list(self.pools):
      age_since = i - pool.state_bar
      if pool.state != 0:
        if p.consumed_policy == "Retire immediately" or (p.consumed_policy == "Fade, then retire" and age_since > p.consumed_fade_bars):
          self.pools.remove(pool)
          self.stats["retired"] += 1
        continue
      if i - pool.birth > p.liq_max_age:
        self.pools.remove(pool)
        self.stats["retired"] += 1
        continue
      if i <= pool.confirm:
        continue
      edge = pool.edge
      side = pool.side
      pen = (s.h[i] > edge + atr * p.sweep_buffer) if side == 1 else (s.l[i] < edge - atr * p.sweep_buffer)
      beyond_close = (s.c[i] > edge + atr * p.liq_break_buffer_atr) if side == 1 else (s.c[i] < edge - atr * p.liq_break_buffer_atr)
      pool.beyond = pool.beyond + 1 if beyond_close else 0
      if pen:
        if pool.pending < 0:
          pool.pending = i
          pool.extreme, pool.extreme_bar = (s.h[i], i) if side == 1 else (s.l[i], i)
        else:
          if side == 1 and s.h[i] > pool.extreme:
            pool.extreme, pool.extreme_bar = s.h[i], i
          if side == -1 and s.l[i] < pool.extreme:
            pool.extreme, pool.extreme_bar = s.l[i], i
      returned = (s.c[i] < edge) if side == 1 else (s.c[i] > edge)
      window_ok = pool.pending >= 0 and (pool.pending == i if p.sweep_rule == "Same bar" else i - pool.pending < p.sweep_return_bars)
      if pool.beyond >= p.liq_accept_closes:
        pool.state, pool.state_bar = 2, i
        self.stats["consumed"] += 1
        self.events.append((i, "consumed", side, edge, pool.degree, pool.uid))
      elif pool.pending >= 0 and returned and window_ok:
        pool.state, pool.state_bar = 1, i
        self.stats["swept"] += 1
        self.events.append((i, "swept", side, edge, pool.degree, pool.uid))
        s.sweeps.append((pool.pending, -side))  # the bar that traded through the pool, and the reversal the sweep supports
        # rejection wick on the extreme bar
        e = pool.extreme_bar
        if side == 1:
          wick = s.h[e] - max(s.o[e], s.c[e])
        else:
          wick = min(s.o[e], s.c[e]) - s.l[e]
        if wick >= p.grab_min_wick_atr * atr:
          self.stats["grab_marks"] += 1
          if side == 1:
            self.grabs.append((i, side, e, max(s.o[e], s.c[e]), s.h[e]))
          else:
            self.grabs.append((i, side, e, s.l[e], min(s.o[e], s.c[e])))
      elif pool.pending >= 0 and not window_ok:
        pool.pending = -1

  # ---- display (Balanced profile) ---------------------------------------
  def select_visible(self, close, atr, budget=None):
    """Mirror of the Pine islast selection: nearest active BSL above / SSL below first,
    then structural degree, active state and proximity."""
    p = self.p
    budget = min(p.visible_liquidity, 8) if budget is None else budget
    shown = []
    band_hi = max(self.s.h[-p.scale_lookback_bars:])
    band_lo = min(self.s.l[-p.scale_lookback_bars:])
    near_b = min((x for x in self.pools if x.state == 0 and x.side == 1 and x.edge > close), key=lambda x: x.edge - close, default=None)
    near_s = min((x for x in self.pools if x.state == 0 and x.side == -1 and x.edge < close), key=lambda x: close - x.edge, default=None)
    for cand in (near_b, near_s):
      if cand is not None and abs(cand.edge - close) / atr <= p.liq_display_distance_atr * 2 and p.scale_ok(cand.edge, close, atr, band_hi, band_lo) and len(shown) < budget:
        shown.append(cand)
    while len(shown) < budget:
      best, best_score = None, None
      for x in self.pools:
        if x in shown:
          continue
        dist = abs(close - x.edge) / atr
        if dist > p.liq_display_distance_atr or not p.scale_ok(x.edge, close, atr, band_hi, band_lo):
          continue
        score = x.degree * 1000 + (500 if x.state == 0 else 0) - dist * 10
        if best is None or score > best_score:
          best, best_score = x, score
      if best is None:
        break
      shown.append(best)
    return shown

  # ---- nearest unswept internal swing (iBSL / iSSL markers) -------------
  def nearest_internal(self, close, atr):
    """Nearest internal swing high above / low below price that no later bar traded through
    and that is not already an active pool. Markers only: no state, no alerts."""
    s, p = self.s, self.p
    n = s.n
    band_hi = max(s.h[-p.scale_lookback_bars:])
    band_lo = min(s.l[-p.scale_lookback_bars:])
    best = {1: None, -1: None}
    for price, bar, side in self.ring:
      if not ((side == 1 and price > close) or (side == -1 and price < close)):
        continue
      if not p.scale_ok(price, close, atr, band_hi, band_lo) or n - 1 - bar < 1 or n - 1 - bar > 750:
        continue
      if best[side] is not None and abs(price - close) >= abs(best[side][0] - close):
        continue
      swept = any((s.h[k] > price) if side == 1 else (s.l[k] < price) for k in range(bar + 1, n))
      dup = any(x.state == 0 and x.side == side and abs(x.edge - price) <= atr * 0.5 for x in self.pools)
      if not swept and not dup:
        best[side] = (price, bar)
    return best[1], best[-1]


# ---------------------------------------------------------------------------
# v2.7: right-edge label column and BOS de-duplication (mirrors the Pine code)
# ---------------------------------------------------------------------------
def label_gap(atr, band_hi, band_lo, annotation_atr_spacing=0.30):
  """Merge distance: at least the annotation spacing and about one label height
  (1/48 of the candle range the chart already shows)."""
  return max(annotation_atr_spacing * atr, (band_hi - band_lo) / 48.0)


def cluster_labels(items, gap):
  """items: [(y, text, priority)]. Sort by price descending and chain labels closer than `gap`
  into one cluster; each cluster becomes one label at the y of its highest-priority member with
  up to three texts joined by ' · ' (then '+n'). Returns [(y, text, member_indices)]."""
  order = sorted(range(len(items)), key=lambda k: -items[k][0])
  clusters, cur, prev = [], [], None
  for k in order:
    y = items[k][0]
    if cur and prev - y >= gap:
      clusters.append(cur)
      cur = []
    cur.append(k)
    prev = y
  if cur:
    clusters.append(cur)
  out = []
  for members in clusters:
    ranked = sorted(members, key=lambda k: -items[k][2])  # stable: ties keep price order
    text = " · ".join(items[k][1] for k in ranked[:3]) + (" +%d" % (len(ranked) - 3) if len(ranked) > 3 else "")
    out.append((items[ranked[0]][0], text, members))
  return out


def dedupe_breaks(events):
  """events: [(break_bar, up, choch, swing_bar)] in publication order. One candle that closes
  beyond several swings is one BOS: keep the most recently published (most recent swing)."""
  kept = []
  for ev in events:
    bar, up, choch, swing = ev
    if not choch and kept and kept[-1][0] == bar and kept[-1][1] == up and not kept[-1][2]:
      kept.pop()
    kept.append(ev)
  return kept


def place_zone_captions(zones, gap):
  """zones: [(mid, height, suppressed)] in drawing order. Mirrors f_renderZones: a caption goes inside its
  box only if the box is at least 1.4 label heights tall, is not suppressed by an overlapping POI, and no
  other inside text sits within one label height; otherwise it joins the right-edge column (unless
  suppressed). Returns a list of 'inside' | 'column' | 'none'."""
  placed, out = [], []
  for mid, height, suppressed in zones:
    free = all(abs(mid - y) >= gap for y in placed)
    inside = height >= gap * 1.4 and not suppressed and free
    if inside:
      placed.append(mid)
      out.append("inside")
    else:
      out.append("none" if suppressed else "column")
  return out


def v213(chart_secs=300, **overrides):
  """Params of the v2.13 indicator: every v2.13 behaviour on (the Pine defaults)."""
  kw = dict(structure_mode="displacement", protected_mode="trailing", range_mode="protected", fresh_sd=True, ob_break_anchor=True,
            fvg_def="engine", composite_independent=True, swept_before_impulse=True)
  kw.update(overrides)
  return P21(chart_secs=chart_secs, **kw)


def v214(chart_secs=300, **overrides):
  """Params of the v2.14 indicator: v2.13 plus the higher-low promotion, the internal-pivot CHoCH reset and the developing leg that follows the protected level."""
  kw = dict(protected_hl=True, dev_follows_protected=True)
  kw.update(overrides)
  return v213(chart_secs, **kw)


def phase_text(s):
  """The panel's Market Phase after the last stepped bar (mirrors phaseText in the Pine file)."""
  i, bias, close = s.cur, s.bias, s.c[s.cur]
  hi, lo = s.last_swing_high, s.last_swing_low
  transition = s.last_break_choch and s.last_break_bar is not None and i - s.last_break_bar <= s.external_len * 2
  expansion = (bias == 1 and not isna(hi) and close > hi) or (bias == -1 and not isna(lo) and close < lo)
  in_range = (not transition) and (not expansion) and not isna(hi) and not isna(lo) and lo <= close <= hi
  if transition:
    return "Structural transition"
  if expansion:
    return "Bull expansion" if bias == 1 else "Bear expansion"
  if bias != 0 and s.dev_dir == -bias:
    return "Counter-trend rebound" if bias == -1 else "Counter-trend pullback"
  if in_range:
    return "Range / consolidation"
  if bias == 0:
    return "Range / discovery"
  return "Corrective pullback" if s.i_bias == -bias else "Trend / balance"

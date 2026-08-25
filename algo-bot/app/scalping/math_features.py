"""Pure ATR-normalized scalp feature math (no live path side effects).

Implements the XAU scalp state vector pieces:

  X_t = [L, V, M, S, R, Q, C] (+ exhaustion E, session, VR)

Hard gates belong in strategy modules; this module only measures.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


# Volatility ratio bins (ATR_short / ATR_long)
VR_QUIET = "quiet"
VR_NORMAL = "normal"
VR_ACTIVE = "active"
VR_EXTREME = "extreme"

SESSION_ASIA = "asia"
SESSION_LONDON_OPEN = "london_open"
SESSION_LONDON = "london"
SESSION_NY_OPEN = "ny_open"
SESSION_OVERLAP = "london_ny_overlap"
SESSION_NY_AFTERNOON = "ny_afternoon"
SESSION_ROLLOVER = "rollover"
SESSION_UNKNOWN = "unknown"


@dataclass(frozen=True)
class CandleGeometry:
  wick_lower_frac: float
  wick_upper_frac: float
  body_frac: float
  close_location: float  # (close - low) / (high - low)


@dataclass(frozen=True)
class ScalpFeatureVector:
  """Explainable approximation inputs for P(successful scalp | X_t)."""

  range_position: float | None
  location_buy: float | None
  location_sell: float | None
  distance_atr: float | None
  zone_width_atr: float | None
  momentum_atr: float | None
  impulse_atr: float | None
  retracement: float | None
  room_atr: float | None
  room_net_price: float | None
  room_net_atr: float | None
  trigger_quality: float | None
  execution_cost_atr: float | None
  exhaustion: float | None
  volatility_ratio: float | None
  volatility_regime: str
  session: str
  atr: float

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)


def safe_div(numer: float, denom: float, *, default: float | None = None) -> float | None:
  if denom is None or abs(float(denom)) < 1e-12:
    return default
  return float(numer) / float(denom)


def range_position(price: float, low: float, high: float) -> float | None:
  """p = (P - L) / (H - L) clipped to [0, 1]."""
  span = float(high) - float(low)
  if span <= 0:
    return None
  raw = (float(price) - float(low)) / span
  return max(0.0, min(1.0, raw))


def location_scores(position: float | None) -> tuple[float | None, float | None]:
  """BUY prefers discount (1-p); SELL prefers premium (p)."""
  if position is None:
    return None, None
  p = float(position)
  return 1.0 - p, p


def distance_atr(price: float, level: float, atr: float) -> float | None:
  return safe_div(abs(float(price) - float(level)), atr)


def zone_width_atr(zone_high: float, zone_low: float, atr: float) -> float | None:
  return safe_div(float(zone_high) - float(zone_low), atr)


def momentum_atr(close: float, close_k: float, atr: float) -> float | None:
  """M_t = (C_t - C_{t-k}) / ATR."""
  return safe_div(float(close) - float(close_k), atr)


def impulse_atr(close: float, origin: float, atr: float) -> float | None:
  """I_t = |C_t - C_origin| / ATR."""
  return safe_div(abs(float(close) - float(origin)), atr)


def retracement_ratio(
  current: float,
  extreme: float,
  origin: float,
) -> float | None:
  """r = |P_extreme - P_current| / |P_extreme - P_origin|."""
  impulse = abs(float(extreme) - float(origin))
  if impulse <= 1e-12:
    return None
  return abs(float(extreme) - float(current)) / impulse


def structural_room_price(
  direction: str,
  entry: float,
  barrier: float | None,
) -> float | None:
  if barrier is None:
    return None
  if direction.upper() == "BUY":
    return float(barrier) - float(entry)
  return float(entry) - float(barrier)


def room_net_price(
  room: float | None,
  *,
  spread: float = 0.0,
  slippage: float = 0.0,
  buffer: float = 0.0,
) -> float | None:
  if room is None:
    return None
  return float(room) - float(spread) - float(slippage) - float(buffer)


def room_sufficient(room_net: float | None, target_min: float) -> bool:
  if room_net is None:
    return False
  return float(room_net) >= float(target_min)


def candle_geometry(
  open_: float,
  high: float,
  low: float,
  close: float,
) -> CandleGeometry:
  span = float(high) - float(low)
  if span <= 1e-12:
    return CandleGeometry(0.0, 0.0, 0.0, 0.5)
  body = abs(float(close) - float(open_))
  upper = float(high) - max(float(open_), float(close))
  lower = min(float(open_), float(close)) - float(low)
  return CandleGeometry(
    wick_lower_frac=max(0.0, lower / span),
    wick_upper_frac=max(0.0, upper / span),
    body_frac=max(0.0, min(1.0, body / span)),
    close_location=max(0.0, min(1.0, (float(close) - float(low)) / span)),
  )


def trigger_quality_buy(
  geometry: CandleGeometry,
  *,
  reclaim: bool = False,
  w_wick: float = 0.35,
  w_body: float = 0.20,
  w_close: float = 0.30,
  w_reclaim: float = 0.15,
) -> float:
  reclaim_score = 1.0 if reclaim else 0.0
  raw = (
    w_wick * geometry.wick_lower_frac
    + w_body * geometry.body_frac
    + w_close * geometry.close_location
    + w_reclaim * reclaim_score
  )
  return max(0.0, min(1.0, raw))


def trigger_quality_sell(
  geometry: CandleGeometry,
  *,
  reclaim: bool = False,
  w_wick: float = 0.35,
  w_body: float = 0.20,
  w_close: float = 0.30,
  w_reclaim: float = 0.15,
) -> float:
  reclaim_score = 1.0 if reclaim else 0.0
  # SELL wants upper wick + close near low.
  close_for_sell = 1.0 - geometry.close_location
  raw = (
    w_wick * geometry.wick_upper_frac
    + w_body * geometry.body_frac
    + w_close * close_for_sell
    + w_reclaim * reclaim_score
  )
  return max(0.0, min(1.0, raw))


def execution_cost_atr(
  spread: float,
  slippage: float,
  atr: float,
) -> float | None:
  return safe_div(float(spread) + float(slippage), atr, default=None)


def exhaustion_score(impulse_atr_value: float | None, *, soft_cap: float = 2.0) -> float:
  """Map extension in ATR units into [0, 1] penalty mass."""
  if impulse_atr_value is None or impulse_atr_value <= 0:
    return 0.0
  return max(0.0, min(1.0, float(impulse_atr_value) / soft_cap))


def volatility_ratio(atr_short: float, atr_long: float) -> float | None:
  return safe_div(atr_short, atr_long)


def classify_volatility_regime(vr: float | None) -> str:
  if vr is None:
    return VR_NORMAL
  if vr < 0.75:
    return VR_QUIET
  if vr < 1.25:
    return VR_NORMAL
  if vr < 1.75:
    return VR_ACTIVE
  return VR_EXTREME


def classify_session_utc_hour(hour: int) -> str:
  """Coarse XAU session buckets on UTC clock (broker-agnostic research default)."""
  h = int(hour) % 24
  if h in (21, 22):
    return SESSION_ROLLOVER
  if 0 <= h < 7:
    return SESSION_ASIA
  if h == 7:
    return SESSION_LONDON_OPEN
  if 8 <= h < 12:
    return SESSION_LONDON
  if h == 12:
    return SESSION_NY_OPEN
  if 13 <= h < 16:
    return SESSION_OVERLAP
  if 16 <= h < 21:
    return SESSION_NY_AFTERNOON
  return SESSION_UNKNOWN


DEFAULT_SCORE_WEIGHTS: Mapping[str, float] = {
  "location": 0.25,
  "trigger": 0.20,
  "momentum": 0.15,
  "structure": 0.15,
  "room": 0.15,
  "cost": 0.05,
  "exhaustion": 0.05,
}


def unified_scalp_score(
  *,
  location: float,
  trigger: float,
  momentum: float,
  structure: float,
  room: float,
  cost: float,
  exhaustion: float,
  weights: Mapping[str, float] | None = None,
) -> float:
  """Score = Σ w_i F_i - w_C C - w_E E  (cost/exhaustion already subtracted via weights).

  Call only after hard gates pass. Inputs expected in [0, 1].
  """
  w = dict(DEFAULT_SCORE_WEIGHTS if weights is None else weights)
  total = (
    w.get("location", 0.0) * _clamp01(location)
    + w.get("trigger", 0.0) * _clamp01(trigger)
    + w.get("momentum", 0.0) * _clamp01(momentum)
    + w.get("structure", 0.0) * _clamp01(structure)
    + w.get("room", 0.0) * _clamp01(room)
    - w.get("cost", 0.0) * _clamp01(cost)
    - w.get("exhaustion", 0.0) * _clamp01(exhaustion)
  )
  return round(max(0.0, min(1.0, total)), 4)


def build_feature_vector(
  *,
  price: float,
  atr: float,
  range_low: float | None = None,
  range_high: float | None = None,
  level: float | None = None,
  zone_low: float | None = None,
  zone_high: float | None = None,
  close: float | None = None,
  close_k: float | None = None,
  impulse_origin: float | None = None,
  impulse_extreme: float | None = None,
  direction: str = "BUY",
  barrier: float | None = None,
  spread: float = 0.0,
  slippage: float = 0.0,
  buffer: float = 0.0,
  open_: float | None = None,
  high: float | None = None,
  low: float | None = None,
  reclaim: bool = False,
  atr_short: float | None = None,
  atr_long: float | None = None,
  utc_hour: int | None = None,
  session: str | None = None,
) -> ScalpFeatureVector:
  atr_v = float(atr) if atr and atr > 0 else 0.0
  pos = None
  if range_low is not None and range_high is not None:
    pos = range_position(price, range_low, range_high)
  loc_buy, loc_sell = location_scores(pos)

  dist = distance_atr(price, level, atr_v) if level is not None and atr_v > 0 else None
  width = None
  if zone_low is not None and zone_high is not None and atr_v > 0:
    width = zone_width_atr(zone_high, zone_low, atr_v)

  mom = None
  if close is not None and close_k is not None and atr_v > 0:
    mom = momentum_atr(close, close_k, atr_v)

  impulse = None
  if close is not None and impulse_origin is not None and atr_v > 0:
    impulse = impulse_atr(close, impulse_origin, atr_v)

  retr = None
  if (
    impulse_extreme is not None
    and impulse_origin is not None
    and close is not None
  ):
    retr = retracement_ratio(close, impulse_extreme, impulse_origin)

  room = structural_room_price(direction, price, barrier)
  room_net = room_net_price(room, spread=spread, slippage=slippage, buffer=buffer)
  room_net_a = safe_div(room_net, atr_v) if room_net is not None and atr_v > 0 else None
  room_a = safe_div(room, atr_v) if room is not None and atr_v > 0 else None

  q = None
  if None not in (open_, high, low, close):
    geom = candle_geometry(open_, high, low, close)  # type: ignore[arg-type]
    q = (
      trigger_quality_buy(geom, reclaim=reclaim)
      if direction.upper() == "BUY"
      else trigger_quality_sell(geom, reclaim=reclaim)
    )

  cost = execution_cost_atr(spread, slippage, atr_v) if atr_v > 0 else None
  exh = exhaustion_score(impulse)
  vr = None
  if atr_short is not None and atr_long is not None:
    vr = volatility_ratio(atr_short, atr_long)
  sess = session or (
    classify_session_utc_hour(utc_hour) if utc_hour is not None else SESSION_UNKNOWN
  )

  return ScalpFeatureVector(
    range_position=pos,
    location_buy=loc_buy,
    location_sell=loc_sell,
    distance_atr=dist,
    zone_width_atr=width,
    momentum_atr=mom,
    impulse_atr=impulse,
    retracement=retr,
    room_atr=room_a,
    room_net_price=room_net,
    room_net_atr=room_net_a,
    trigger_quality=q,
    execution_cost_atr=cost,
    exhaustion=exh,
    volatility_ratio=vr,
    volatility_regime=classify_volatility_regime(vr),
    session=sess,
    atr=atr_v,
  )


def _clamp01(value: float) -> float:
  return max(0.0, min(1.0, float(value)))

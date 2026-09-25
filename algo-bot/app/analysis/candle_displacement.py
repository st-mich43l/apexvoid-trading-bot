"""Candle Confirmation V2 — evidence family B: DISPLACEMENT (§7/§8/§9).

Answers "did buyers or sellers actually take control after the reaction?"
Generally carries more weight than rejection geometry alone — this is
actual directional control, not just a wick. Pattern labels (engulfing,
strong_close, body_close, strong_reclaim, displacement_candle) are derived
from the same ``CandleGeometry``, never independently scored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analysis.candle_geometry import CandleGeometry, atr_quality, clamp01, fraction_quality
from app.scalping.math_features import safe_div

# Provisional defaults (§29/§48) — matches structural_reaction_support.py's
# pre-existing engulfing_minimum_range_atr=0.5 default where an equivalent
# concept already existed; the rest are freshly provisional.
_BODY_MINIMUM_ATR = 0.30
_RANGE_MINIMUM_ATR = 0.40
_BODY_DOMINANCE_MINIMUM = 0.55
_STRONG_CLOSE_BUY_MINIMUM_LOCATION = 0.70
_STRONG_CLOSE_SELL_MAXIMUM_LOCATION = 0.30
_ENGULFING_MINIMUM_RANGE_ATR = 0.50


def _displacement_cfg(cfg: Any | None) -> Any | None:
  analysis = getattr(cfg, "analysis", None)
  section = getattr(analysis, "candle_confirmation", None)
  return getattr(section, "displacement", None)


def _body_minimum_atr(cfg: Any | None) -> float:
  section = getattr(_displacement_cfg(cfg), "body", None)
  return float(getattr(section, "minimum_atr", _BODY_MINIMUM_ATR) or _BODY_MINIMUM_ATR)


def _range_minimum_atr(cfg: Any | None) -> float:
  section = getattr(_displacement_cfg(cfg), "range", None)
  return float(getattr(section, "minimum_atr", _RANGE_MINIMUM_ATR) or _RANGE_MINIMUM_ATR)


def _body_dominance_minimum(cfg: Any | None) -> float:
  section = getattr(_displacement_cfg(cfg), "body_dominance", None)
  return float(getattr(section, "minimum", _BODY_DOMINANCE_MINIMUM) or _BODY_DOMINANCE_MINIMUM)


def _strong_close_buy_minimum_location(cfg: Any | None) -> float:
  section = getattr(_displacement_cfg(cfg), "strong_close", None)
  return float(
    getattr(section, "buy_minimum_location", _STRONG_CLOSE_BUY_MINIMUM_LOCATION)
    or _STRONG_CLOSE_BUY_MINIMUM_LOCATION
  )


def _strong_close_sell_maximum_location(cfg: Any | None) -> float:
  section = getattr(_displacement_cfg(cfg), "strong_close", None)
  return float(
    getattr(section, "sell_maximum_location", _STRONG_CLOSE_SELL_MAXIMUM_LOCATION)
    or _STRONG_CLOSE_SELL_MAXIMUM_LOCATION
  )


def _engulfing_enabled(cfg: Any | None) -> bool:
  section = getattr(_displacement_cfg(cfg), "engulfing", None)
  return bool(getattr(section, "enabled", True))


def _engulfing_minimum_range_atr(cfg: Any | None) -> float:
  section = getattr(_displacement_cfg(cfg), "engulfing", None)
  return float(
    getattr(section, "minimum_range_atr", _ENGULFING_MINIMUM_RANGE_ATR)
    or _ENGULFING_MINIMUM_RANGE_ATR
  )


def _close_beyond_level_enabled(cfg: Any | None) -> bool:
  section = getattr(_displacement_cfg(cfg), "close_beyond_level", None)
  return bool(getattr(section, "enabled", True))


@dataclass(frozen=True)
class DisplacementEvidence:
  score: float
  patterns: tuple[str, ...]
  body_atr: float | None
  range_atr: float | None
  body_dominance: float
  close_location: float
  reclaim: bool
  reclaim_depth_atr: float | None
  engulfing: bool
  engulfing_quality: float | None

  def to_dict(self) -> dict[str, Any]:
    return {
      "score": round(float(self.score), 4),
      "patterns": list(self.patterns),
      "body_atr": self.body_atr,
      "range_atr": self.range_atr,
      "body_dominance": round(float(self.body_dominance), 4),
      "close_location": round(float(self.close_location), 4),
      "reclaim": self.reclaim,
      "reclaim_depth_atr": self.reclaim_depth_atr,
      "engulfing": self.engulfing,
      "engulfing_quality": self.engulfing_quality,
    }


def engulfing_quality(
  geo: CandleGeometry,
  prior: CandleGeometry | None,
  *,
  direction: str,
  cfg: Any | None = None,
) -> tuple[bool, float | None]:
  """§9 — body-size × directional-close × prior-opposition, not a bare
  containment boolean. A tiny engulfing candle is not equivalent to a
  strong displacement engulfing candle."""
  if prior is None or prior.range_price <= 0 or not _engulfing_enabled(cfg):
    return False, None
  body_low = min(geo.open, geo.close)
  body_high = max(geo.open, geo.close)
  prior_body_low = min(prior.open, prior.close)
  prior_body_high = max(prior.open, prior.close)
  contains = body_low <= prior_body_low and body_high >= prior_body_high
  directional = geo.is_bullish if direction == "BUY" else geo.is_bearish
  if not (contains and directional):
    return False, None

  minimum_range_atr = _engulfing_minimum_range_atr(cfg)
  range_atr_value = geo.range_atr
  if range_atr_value is not None and range_atr_value < minimum_range_atr:
    return False, None

  body_size_quality = clamp01(
    safe_div(geo.body_price, max(prior.body_price, 1e-9), default=1.0) / 2.0,
  )
  directional_close_quality = (
    geo.close_location if direction == "BUY" else 1.0 - geo.close_location
  )
  prior_bullish = prior.is_bullish
  prior_bearish = prior.is_bearish
  prior_opposite = (
    prior.body_fraction < 0.1
    or (direction == "BUY" and prior_bearish)
    or (direction == "SELL" and prior_bullish)
  )
  prior_opposition_quality = 1.0 if prior_opposite else 0.4
  quality = clamp01(
    body_size_quality * directional_close_quality * prior_opposition_quality,
  )
  return True, quality


def _displacement_patterns(
  geo: CandleGeometry,
  *,
  direction: str,
  is_engulfing: bool,
  reclaim: bool,
  cfg: Any | None,
) -> tuple[str, ...]:
  labels: list[str] = []
  close_for_direction = (
    geo.close_location if direction == "BUY" else 1.0 - geo.close_location
  )
  strong_close_minimum = (
    _strong_close_buy_minimum_location(cfg) if direction == "BUY"
    else (1.0 - _strong_close_sell_maximum_location(cfg))
  )
  directional = geo.is_bullish if direction == "BUY" else geo.is_bearish

  if directional and close_for_direction >= strong_close_minimum:
    labels.append("strong_close")

  if directional and (geo.range_atr or 0.0) >= _RANGE_MINIMUM_ATR:
    labels.append("body_close")

  if is_engulfing:
    labels.append("engulfing")

  if reclaim:
    labels.append("strong_reclaim")

  if (
    directional
    and geo.body_fraction >= _body_dominance_minimum(cfg)
    and (geo.body_atr or 0.0) >= _body_minimum_atr(cfg)
  ):
    labels.append("displacement_candle")

  return tuple(labels)


def evaluate_displacement(
  geo: CandleGeometry,
  prior: CandleGeometry | None,
  *,
  direction: str,
  level: float | None = None,
  atr: float = 0.0,
  cfg: Any | None = None,
) -> DisplacementEvidence | None:
  """§7 — direction-aware displacement score. ``level`` (optional) is the
  structural edge a reclaim/close-beyond-level check measures against;
  omitted when the caller has no single relevant level (e.g. a pure
  momentum continuation bar)."""
  direction = str(direction).upper()
  if direction not in {"BUY", "SELL"}:
    return None

  is_engulfing, engulf_quality = engulfing_quality(geo, prior, direction=direction, cfg=cfg)

  reclaimed_depth: float | None = None
  if level is not None and atr > 0 and _close_beyond_level_enabled(cfg):
    from app.analysis.candle_rejection import reclaim_depth_atr
    reclaimed_depth = reclaim_depth_atr(
      direction=direction, close=geo.close, level=level, atr=atr,
    )

  patterns = _displacement_patterns(
    geo,
    direction=direction,
    is_engulfing=is_engulfing,
    reclaim=reclaimed_depth is not None,
    cfg=cfg,
  )
  if not patterns:
    return None

  directional = geo.is_bullish if direction == "BUY" else geo.is_bearish
  close_for_direction = (
    geo.close_location if direction == "BUY" else 1.0 - geo.close_location
  )
  strong_close_minimum = (
    _strong_close_buy_minimum_location(cfg) if direction == "BUY"
    else (1.0 - _strong_close_sell_maximum_location(cfg))
  )

  body_dominance = geo.body_fraction if directional else 0.0
  body_dominance_quality = fraction_quality(
    body_dominance, minimum=_body_dominance_minimum(cfg), strong=0.85,
  )
  displacement_strength = atr_quality(geo.range_atr, minimum=_range_minimum_atr(cfg))
  close_quality = fraction_quality(
    close_for_direction if directional else 0.0,
    minimum=strong_close_minimum,
    strong=1.0,
  )
  reclaim_quality = atr_quality(reclaimed_depth, minimum=0.05)
  engulf_score = engulf_quality if engulf_quality is not None else 0.0

  score = clamp01(
    0.30 * body_dominance_quality
    + 0.25 * displacement_strength
    + 0.20 * close_quality
    + 0.15 * reclaim_quality
    + 0.10 * engulf_score
  )

  return DisplacementEvidence(
    score=score,
    patterns=patterns,
    body_atr=geo.body_atr,
    range_atr=geo.range_atr,
    body_dominance=body_dominance,
    close_location=geo.close_location,
    reclaim=reclaimed_depth is not None,
    reclaim_depth_atr=reclaimed_depth,
    engulfing=is_engulfing,
    engulfing_quality=engulf_quality,
  )

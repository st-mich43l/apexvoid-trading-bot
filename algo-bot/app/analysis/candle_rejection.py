"""Candle Confirmation V2 — evidence family A: REJECTION (§5/§6/§25/§26/§27).

Answers "did price interact with structure/liquidity and get rejected?"
Pattern labels (hammer, shooting_star, pin_bar, inverted_hammer,
wick_rejection, sweep_reclaim) are derived from ONE measured
``CandleGeometry`` plus sweep/reclaim quality against a structural level —
never independently-scored detectors (§3/§18). The score is a weighted sum
of wick/close/body/reclaim/sweep quality, always in ``[0, 1]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analysis.candle_geometry import CandleGeometry, atr_quality, clamp01, fraction_quality
from app.analysis.structural_reaction_support import band_touched
from app.scalping.math_features import safe_div

# Provisional defaults (§29/§48) — carried forward from the pre-existing
# hardcoded constants in m1_trigger.py where an equivalent concept already
# existed (wick_fraction, body maximum), or freshly provisional (sweep/
# reclaim minimums, wick_to_body ratio) pending replay evidence.
_WICK_MINIMUM_FRACTION = 0.30
_WICK_STRONG_FRACTION = 0.55
_BODY_MAXIMUM_FRACTION = 0.45
_CLOSE_BUY_MINIMUM_LOCATION = 0.65
_CLOSE_SELL_MAXIMUM_LOCATION = 0.35
_WICK_TO_BODY_MINIMUM_RATIO = 1.5
_SWEEP_MINIMUM_PENETRATION_ATR = 0.05
_RECLAIM_MINIMUM_DEPTH_ATR = 0.05

# Hammer/pin-bar shape thresholds (§32) — the pre-existing m1_trigger.py
# _HAMMER_*/_PIN_BAR_* constants, carried forward unchanged.
_HAMMER_WICK_MULTIPLE = 2.0
_HAMMER_BODY_FRACTION = 0.3
_HAMMER_OPPOSITE_WICK_FRACTION = 0.25
_PIN_BAR_WICK_FRACTION = 0.66
_PIN_BAR_BODY_FRACTION = 0.2
_PIN_BAR_OPPOSITE_WICK_FRACTION = 0.15


def _rejection_cfg(cfg: Any | None) -> Any | None:
  analysis = getattr(cfg, "analysis", None)
  section = getattr(analysis, "candle_confirmation", None)
  return getattr(section, "rejection", None)


def _wick_minimum_fraction(cfg: Any | None) -> float:
  section = getattr(_rejection_cfg(cfg), "wick", None)
  return float(getattr(section, "minimum_fraction", _WICK_MINIMUM_FRACTION) or _WICK_MINIMUM_FRACTION)


def _wick_strong_fraction(cfg: Any | None) -> float:
  section = getattr(_rejection_cfg(cfg), "wick", None)
  return float(getattr(section, "strong_fraction", _WICK_STRONG_FRACTION) or _WICK_STRONG_FRACTION)


def _body_maximum_fraction(cfg: Any | None) -> float:
  section = getattr(_rejection_cfg(cfg), "body", None)
  return float(getattr(section, "maximum_fraction", _BODY_MAXIMUM_FRACTION) or _BODY_MAXIMUM_FRACTION)


def _close_buy_minimum_location(cfg: Any | None) -> float:
  section = getattr(_rejection_cfg(cfg), "close", None)
  return float(
    getattr(section, "buy_minimum_location", _CLOSE_BUY_MINIMUM_LOCATION)
    or _CLOSE_BUY_MINIMUM_LOCATION
  )


def _close_sell_maximum_location(cfg: Any | None) -> float:
  section = getattr(_rejection_cfg(cfg), "close", None)
  return float(
    getattr(section, "sell_maximum_location", _CLOSE_SELL_MAXIMUM_LOCATION)
    or _CLOSE_SELL_MAXIMUM_LOCATION
  )


def _wick_to_body_minimum_ratio(cfg: Any | None) -> float:
  section = getattr(_rejection_cfg(cfg), "wick_to_body", None)
  return float(
    getattr(section, "minimum_ratio", _WICK_TO_BODY_MINIMUM_RATIO)
    or _WICK_TO_BODY_MINIMUM_RATIO
  )


def _sweep_enabled(cfg: Any | None) -> bool:
  section = getattr(_rejection_cfg(cfg), "sweep", None)
  return bool(getattr(section, "enabled", True))


def _sweep_minimum_penetration_atr(cfg: Any | None) -> float:
  section = getattr(_rejection_cfg(cfg), "sweep", None)
  return float(
    getattr(section, "minimum_penetration_atr", _SWEEP_MINIMUM_PENETRATION_ATR)
    or _SWEEP_MINIMUM_PENETRATION_ATR
  )


def _reclaim_enabled(cfg: Any | None) -> bool:
  section = getattr(_rejection_cfg(cfg), "reclaim", None)
  return bool(getattr(section, "enabled", True))


@dataclass(frozen=True)
class RejectionEvidence:
  score: float
  patterns: tuple[str, ...]
  wick_fraction: float
  body_fraction: float
  close_location: float
  sweep: bool
  sweep_penetration_atr: float | None
  reclaim: bool
  reclaim_depth_atr: float | None

  def to_dict(self) -> dict[str, Any]:
    return {
      "score": round(float(self.score), 4),
      "patterns": list(self.patterns),
      "wick_fraction": round(float(self.wick_fraction), 4),
      "body_fraction": round(float(self.body_fraction), 4),
      "close_location": round(float(self.close_location), 4),
      "sweep": self.sweep,
      "sweep_penetration_atr": self.sweep_penetration_atr,
      "reclaim": self.reclaim,
      "reclaim_depth_atr": self.reclaim_depth_atr,
    }


def sweep_penetration_atr(
  *, direction: str, bar_low: float, bar_high: float, level: float, atr: float,
) -> float | None:
  """§27 — only positive penetration counts (a level never crossed is not
  a sweep). BUY: support swept from above; SELL: resistance swept from
  below."""
  if direction == "BUY":
    value = safe_div(float(level) - float(bar_low), atr)
  else:
    value = safe_div(float(bar_high) - float(level), atr)
  if value is None or value <= 0:
    return None
  return value


def reclaim_depth_atr(
  *, direction: str, close: float, level: float, atr: float,
) -> float | None:
  """§26 — only positive depth counts (a level not reclaimed is not a
  reclaim)."""
  if direction == "BUY":
    value = safe_div(float(close) - float(level), atr)
  else:
    value = safe_div(float(level) - float(close), atr)
  if value is None or value <= 0:
    return None
  return value


def _rejection_patterns(
  geo: CandleGeometry,
  *,
  direction: str,
  zone_touch: bool,
  sweep: bool,
  reclaim: bool,
  cfg: Any | None,
) -> tuple[str, ...]:
  """Derive every applicable label from the SAME geometry (§3/§18) — a
  candle qualifying as multiple labels keeps all of them, never fires a
  triple bonus."""
  labels: list[str] = []
  directional_wick = geo.lower_wick_fraction if direction == "BUY" else geo.upper_wick_fraction
  opposite_wick = geo.upper_wick_fraction if direction == "BUY" else geo.lower_wick_fraction
  wick_min = _wick_minimum_fraction(cfg)

  if zone_touch and directional_wick >= wick_min:
    labels.append("wick_rejection")

  if (
    zone_touch
    and geo.body_fraction <= _HAMMER_BODY_FRACTION
    and geo.body_price > 0
    and directional_wick / max(geo.body_fraction, 1e-9) >= _HAMMER_WICK_MULTIPLE
    and opposite_wick <= _HAMMER_OPPOSITE_WICK_FRACTION
  ):
    labels.append("hammer" if direction == "BUY" else "shooting_star")

  if (
    zone_touch
    and geo.body_fraction <= _PIN_BAR_BODY_FRACTION
    and directional_wick >= _PIN_BAR_WICK_FRACTION
    and opposite_wick <= _PIN_BAR_OPPOSITE_WICK_FRACTION
  ):
    labels.append("pin_bar")

  # Inverted hammer (§32): deliberately weak/research-only — a long
  # OPPOSITE-side wick with a small body is only credited when it also
  # shows a same-bar strong reclaim at the structure (no future-bar
  # confirmation is available causally at evaluation time).
  inverted_wick = opposite_wick
  same_wick = directional_wick
  if (
    zone_touch
    and geo.body_fraction <= _HAMMER_BODY_FRACTION
    and inverted_wick / max(geo.body_fraction, 1e-9) >= _HAMMER_WICK_MULTIPLE
    and same_wick <= _HAMMER_OPPOSITE_WICK_FRACTION
    and reclaim
  ):
    labels.append("inverted_hammer")

  if sweep and reclaim:
    labels.append("sweep_reclaim")

  return tuple(labels)


def evaluate_rejection(
  geo: CandleGeometry,
  *,
  direction: str,
  atr: float,
  level: float,
  zone_low: float | None = None,
  zone_high: float | None = None,
  cfg: Any | None = None,
) -> RejectionEvidence | None:
  """§5 — direction-aware rejection score from one candle's geometry plus
  its sweep/reclaim relationship to ``level`` (the near structural edge:
  support for BUY, resistance for SELL). Returns ``None`` when the bar
  never interacts with the given zone at all (§25 — pattern outside the
  relevant structure is not confirmation).
  """
  direction = str(direction).upper()
  if direction not in {"BUY", "SELL"}:
    return None

  zone_touch = True
  if zone_low is not None and zone_high is not None:
    zone_touch = band_touched(
      {"low": geo.low, "high": geo.high}, float(zone_low), float(zone_high),
    )
  if not zone_touch:
    return None

  swept = sweep_penetration_atr(
    direction=direction, bar_low=geo.low, bar_high=geo.high, level=level, atr=atr,
  ) if _sweep_enabled(cfg) else None
  reclaimed = reclaim_depth_atr(
    direction=direction, close=geo.close, level=level, atr=atr,
  ) if _reclaim_enabled(cfg) else None

  patterns = _rejection_patterns(
    geo,
    direction=direction,
    zone_touch=zone_touch,
    sweep=swept is not None,
    reclaim=reclaimed is not None,
    cfg=cfg,
  )
  if not patterns:
    return None

  directional_wick = geo.lower_wick_fraction if direction == "BUY" else geo.upper_wick_fraction
  close_for_direction = (
    geo.close_location if direction == "BUY" else 1.0 - geo.close_location
  )
  close_minimum = (
    _close_buy_minimum_location(cfg) if direction == "BUY"
    else (1.0 - _close_sell_maximum_location(cfg))
  )

  wick_quality = fraction_quality(
    directional_wick, minimum=_wick_minimum_fraction(cfg), strong=_wick_strong_fraction(cfg),
  )
  close_quality = fraction_quality(close_for_direction, minimum=close_minimum, strong=1.0)
  body_quality = clamp01(1.0 - geo.body_fraction / max(1e-9, _body_maximum_fraction(cfg)))
  reclaim_quality = atr_quality(reclaimed, minimum=_RECLAIM_MINIMUM_DEPTH_ATR)
  sweep_quality = atr_quality(swept, minimum=_SWEEP_MINIMUM_PENETRATION_ATR)

  score = clamp01(
    0.30 * wick_quality
    + 0.20 * close_quality
    + 0.15 * body_quality
    + 0.20 * reclaim_quality
    + 0.15 * sweep_quality
  )

  return RejectionEvidence(
    score=score,
    patterns=patterns,
    wick_fraction=directional_wick,
    body_fraction=geo.body_fraction,
    close_location=geo.close_location,
    sweep=swept is not None,
    sweep_penetration_atr=swept,
    reclaim=reclaimed is not None,
    reclaim_depth_atr=reclaimed,
  )

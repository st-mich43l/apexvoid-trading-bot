"""Candle Confirmation V2 — shared candle geometry (§4).

One geometry model, computed once, for the whole V2 evidence pipeline
(rejection / displacement / sequence families). Candlestick pattern names
are derived labels; this module is the actual measured evidence they're
derived from. Every division routes through ``app.scalping.math_features.
safe_div`` so ATR-normalization math is never duplicated (§20/§47).

This is deliberately a *new*, additional geometry model — it does not
replace ``app.scalping.math_features.CandleGeometry``/``candle_geometry()``
(the scalping M1 shadow math-gate's own continuous trigger-quality score,
a different consumer with a different shape of output) or
``app.analysis.m1_trigger._BarGeometry`` (refactored in this same change to
build on top of this module instead of duplicating the same four
fractions a third time).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.scalping.math_features import safe_div


@dataclass(frozen=True)
class CandleGeometry:
  open: float
  high: float
  low: float
  close: float

  range_price: float
  body_price: float

  upper_wick_price: float
  lower_wick_price: float

  body_fraction: float
  upper_wick_fraction: float
  lower_wick_fraction: float

  close_location: float
  body_atr: float | None
  range_atr: float | None

  @property
  def is_bullish(self) -> bool:
    return self.close > self.open

  @property
  def is_bearish(self) -> bool:
    return self.close < self.open

  @property
  def is_zero_range(self) -> bool:
    return self.range_price <= 0.0


def clamp01(value: float) -> float:
  return max(0.0, min(1.0, float(value)))


def fraction_quality(value: float, *, minimum: float, strong: float) -> float:
  """0 at/below ``minimum``, 1 at/above ``strong``, linear between — the
  shared scaling used across every V2 family score (§5/§7/§15) so a
  wick/body/close-location fraction is never turned into a quality score
  two different ways."""
  if strong <= minimum:
    return 1.0 if value >= minimum else 0.0
  return clamp01((value - minimum) / (strong - minimum))


def atr_quality(value: float | None, *, minimum: float) -> float:
  """Scale a positive ATR-normalized distance (penetration/reclaim/
  displacement) against its configured minimum — a poke right at the
  minimum scores low, ~4x the minimum scores ~1.0. Shared so sweep,
  reclaim, and displacement-strength quality all scale identically."""
  if value is None or value <= 0:
    return 0.0
  return clamp01(float(value) / max(1e-9, minimum * 4.0))


def candle_geometry(
  open_: float,
  high: float,
  low: float,
  close: float,
  *,
  atr: float | None = None,
) -> CandleGeometry:
  """Build a ``CandleGeometry`` from raw OHLC (§4's exact formulas).

  A zero-range bar (``high <= low``, e.g. a data glitch or an illiquid
  print) cannot safely produce fractions — returns all fractions at 0.0
  and ``close_location`` at the neutral 0.5, matching the zero-range
  convention already established in ``math_features.candle_geometry``,
  rather than raising or dividing by zero.
  """
  o, h, l, c = float(open_), float(high), float(low), float(close)
  range_price = h - l
  body_price = abs(c - o)
  upper_wick_price = h - max(o, c)
  lower_wick_price = min(o, c) - l
  atr_v = float(atr) if atr and atr > 0 else None

  if range_price <= 0.0:
    return CandleGeometry(
      open=o, high=h, low=l, close=c,
      range_price=max(0.0, range_price),
      body_price=body_price,
      upper_wick_price=max(0.0, upper_wick_price),
      lower_wick_price=max(0.0, lower_wick_price),
      body_fraction=0.0,
      upper_wick_fraction=0.0,
      lower_wick_fraction=0.0,
      close_location=0.5,
      body_atr=safe_div(body_price, atr_v),
      range_atr=safe_div(max(0.0, range_price), atr_v),
    )

  return CandleGeometry(
    open=o, high=h, low=l, close=c,
    range_price=range_price,
    body_price=body_price,
    upper_wick_price=max(0.0, upper_wick_price),
    lower_wick_price=max(0.0, lower_wick_price),
    body_fraction=clamp01(safe_div(body_price, range_price, default=0.0)),
    upper_wick_fraction=clamp01(
      safe_div(max(0.0, upper_wick_price), range_price, default=0.0),
    ),
    lower_wick_fraction=clamp01(
      safe_div(max(0.0, lower_wick_price), range_price, default=0.0),
    ),
    close_location=clamp01(safe_div(c - l, range_price, default=0.5)),
    body_atr=safe_div(body_price, atr_v),
    range_atr=safe_div(range_price, atr_v),
  )


def candle_geometry_from_bar(bar: Any, *, atr: float | None = None) -> CandleGeometry:
  """Convenience builder from a pandas-Series-like OHLC bar."""
  return candle_geometry(
    bar["open"], bar["high"], bar["low"], bar["close"], atr=atr,
  )

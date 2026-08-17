"""Structure-native Fibonacci ladders from confirmed swing ranges."""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.types import Swing

RETRACEMENT_RATIOS: tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786)
EXTENSION_RATIOS: tuple[float, ...] = (1.0, 1.272, 1.618)


@dataclass(frozen=True)
class FibLevel:
  ratio: float
  price: float
  kind: str = "retracement"  # retracement | extension


def fib_ladder(
  low: float,
  high: float,
  *,
  include_extensions: bool = True,
) -> list[FibLevel]:
  """Classic retracements from high→low; optional upside extensions past high."""
  low_f = float(low)
  high_f = float(high)
  span = high_f - low_f
  if span <= 0:
    return []
  levels = [
    FibLevel(ratio=r, price=high_f - r * span, kind="retracement")
    for r in RETRACEMENT_RATIOS
  ]
  if include_extensions:
    levels.extend(
      FibLevel(ratio=e, price=high_f + e * span, kind="extension")
      for e in EXTENSION_RATIOS
    )
  return levels


def fib_from_swings(
  swings: list[Swing],
  price: float,
  *,
  include_extensions: bool = True,
) -> list[FibLevel]:
  # Local import avoids circular import with dealing_range → fib_zone_label.
  from app.analysis.dealing_range import swing_range_pair

  pair = swing_range_pair(swings, price)
  if pair is None:
    return []
  low, high = pair
  return fib_ladder(low, high, include_extensions=include_extensions)


def nearest_fib(
  levels: list[FibLevel],
  price: float,
  atr: float,
  epsilon_atr: float = 0.15,
  *,
  kinds: tuple[str, ...] = ("retracement",),
) -> FibLevel | None:
  if not levels or atr <= 0:
    return None
  band = max(0.0, float(epsilon_atr)) * float(atr)
  if band <= 0:
    return None
  price_f = float(price)
  best: FibLevel | None = None
  best_dist = float("inf")
  allowed = set(kinds)
  for level in levels:
    if level.kind not in allowed:
      continue
    dist = abs(float(level.price) - price_f)
    if dist <= band and dist < best_dist:
      best = level
      best_dist = dist
  return best


def fib_zone_label(
  position: float,
  *,
  deep_discount: float = 0.382,
  deep_premium: float = 0.618,
  eq_half_band: float = 0.05,
) -> str:
  """Fine PD label: deep_discount / discount / eq / premium / deep_premium."""
  pos = min(1.0, max(0.0, float(position)))
  if abs(pos - 0.5) <= max(0.0, float(eq_half_band)):
    return "eq"
  if pos <= float(deep_discount):
    return "deep_discount"
  if pos < 0.5:
    return "discount"
  if pos >= float(deep_premium):
    return "deep_premium"
  return "premium"

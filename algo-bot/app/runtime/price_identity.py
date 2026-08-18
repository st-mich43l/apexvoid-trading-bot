"""Stable price tokens for identities spanning instruments with unlike scales."""

from __future__ import annotations

from decimal import Decimal
import math


def pip_price_digits(pip_size: float) -> int:
  """Return broker-style digits: one fractional tick beyond one pip."""
  pip = Decimal(str(float(pip_size))).normalize()
  pip_decimals = max(0, -pip.as_tuple().exponent)
  return min(12, pip_decimals + 1)


def rounded_price(value: float, pip_size: float) -> float:
  """Round a price to the broker-style tick precision for one pip size."""
  return round(float(value), pip_price_digits(pip_size))


def price_token(
  value: float,
  *,
  pip_size: float | None = None,
  digits: int | None = None,
) -> str:
  """Serialize a price without the old two-decimal FX collisions."""
  number = float(value)
  if not math.isfinite(number):
    raise ValueError(f"price identity requires a finite value, got {value!r}")
  if digits is None and pip_size is not None:
    digits = pip_price_digits(pip_size)
  if digits is None:
    return format(number, ".12g")
  return f"{number:.{max(0, int(digits))}f}"

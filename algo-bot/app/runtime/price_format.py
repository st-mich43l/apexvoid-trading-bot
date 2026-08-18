"""Price formatting that must use instrument digits and fail closed."""

from __future__ import annotations

from app.core.symbols import digits_for


def format_price(
  symbol: str,
  value: float,
  *,
  grouped: bool = False,
  digits: int | None = None,
) -> str:
  """Format ``value`` using the instrument's configured price digits.

  Unknown symbols raise — never default to two digits.
  """
  precision = digits_for(symbol) if digits is None else max(0, int(digits))
  spec = f",.{precision}f" if grouped else f".{precision}f"
  return f"{value:{spec}}".rstrip("0").rstrip(".")

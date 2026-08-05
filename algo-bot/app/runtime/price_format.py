"""Price formatting that must use instrument digits and fail closed."""

from __future__ import annotations

from app.core.symbols import digits_for


def format_price(symbol: str, value: float, *, grouped: bool = False) -> str:
  """Format ``value`` using the instrument's configured price digits.

  Unknown symbols raise — never default to two digits.
  """
  digits = digits_for(symbol)
  spec = f",.{digits}f" if grouped else f".{digits}f"
  return f"{value:{spec}}".rstrip("0").rstrip(".")

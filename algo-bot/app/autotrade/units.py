"""Shared price and pip units for the auto-trade Redis contract."""

from app.core.symbols import pip_for


def pip_size(symbol: str) -> float:
  """Pip size for a symbol, resolving broker aliases (XAUUSD -> XAU).

  Delegates to app.core.symbols.pip_for - the single canonical symbol
  registry - instead of keeping a second, independently-maintained pip
  table here. A symbol with no configured entry raises KeyError rather
  than silently returning a generic 1.0, which would otherwise be a 10x
  error in any room/target/sizing calculation downstream.
  """
  return pip_for(symbol)

"""Shared price and pip units for the auto-trade Redis contract."""

PIP_SIZE: dict[str, float] = {"XAU": 0.1}


def pip_size(symbol: str) -> float:
  return PIP_SIZE.get(symbol.upper(), 1.0)

"""Per-instrument price geometry (merge widths, round step, opposing gap).

XAU values are dollars. FX must not inherit them — 6.0 on EURUSD is 60k pips.
"""

from __future__ import annotations

from app.core.config import runtime_config

_FX_CANONICAL = frozenset({"EURUSD", "GBPJPY"})
FX_REWARD_RISK = 2.0


def _effective(symbol: str):
  return runtime_config.for_instrument(symbol)


def is_fx(symbol: str) -> bool:
  """True for demo-live FX majors (EURUSD, GBPJPY)."""
  key = (symbol or "").strip().upper()
  if key in _FX_CANONICAL:
    return True
  try:
    return _effective(symbol).identity.canonical_symbol in _FX_CANONICAL
  except Exception:
    return False


def execution(symbol: str):
  """Per-instrument execution slice (FX stop/RR overrides live here)."""
  return _effective(symbol).execution


def price_digits(symbol: str) -> int:
  return int(_effective(symbol).units.price_digits)


def lot_multiplier(symbol: str) -> float:
  try:
    value = float(_effective(symbol).units.lot_multiplier)
  except Exception:
    value = 1.0
  return value if value > 0 else 1.0


def one_to_two_targets(stop_pips: float) -> tuple[int, ...]:
  """Single full-close target at 2R of the planned stop."""
  pips = max(1, int(round(float(stop_pips) * FX_REWARD_RISK)))
  return (pips,)


def analysis_runtime(symbol: str):
  return _effective(symbol).analysis.runtime


def merge_max_width(symbol: str) -> float:
  return float(analysis_runtime(symbol).zones.merge_max_width)


def merge_gap_price(symbol: str) -> float:
  return float(analysis_runtime(symbol).zones.confluence.merge_gap_price)


def round_step(symbol: str) -> float:
  return float(analysis_runtime(symbol).levels.round_step)


def opposing_minimum_separation_price(symbol: str) -> float:
  return float(
    _effective(symbol).risk.exposure.opposing_minimum_separation_price
  )


def pip_value_per_lot(symbol: str) -> float:
  return float(_effective(symbol).units.pip_value_per_lot)


def fvg_entry_max_width_price(symbol: str) -> float:
  return float(
    _effective(symbol).strategies.technique.fvg.entry_max_width_price
  )


def plan_max_volume(symbol: str) -> int:
  """Per-instrument cTrader volume ceiling for TradePlan.risk.max_volume.

  FX majors use 10_000_000 units/lot, not the XAU 10_000 / 100_000 cap.
  """
  return _effective(symbol).units.plan_max_volume()

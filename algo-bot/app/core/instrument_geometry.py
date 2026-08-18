"""Per-instrument price geometry (merge widths, round step, opposing gap).

XAU values are dollars. FX must not inherit them — 6.0 on EURUSD is 60k pips.
"""

from __future__ import annotations

from app.configuration.models.instruments import InstrumentTargetMode
from app.core.config import runtime_config


def _effective(symbol: str):
  return runtime_config.for_instrument(symbol)


def execution(symbol: str):
  """Per-instrument execution slice (FX stop/RR overrides live here)."""
  return _effective(symbol).execution


def price_digits(symbol: str) -> int:
  return int(_effective(symbol).units.price_digits)


def fixed_reward_risk(symbol: str, cfg=None) -> float | None:
  """Configured fixed-RR target, or ``None`` for pip-ladder instruments."""
  source = runtime_config if cfg is None else cfg
  targeting = getattr(source, "targeting", None)
  if targeting is None:
    resolver = getattr(source, "for_instrument", None)
    if not callable(resolver):
      return None
    targeting = resolver(symbol).targeting
  mode = getattr(targeting.mode, "value", targeting.mode)
  if str(mode) != InstrumentTargetMode.FIXED_RR.value:
    return None
  ratio = float(targeting.reward_risk or 0.0)
  return ratio if ratio > 0 else None


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

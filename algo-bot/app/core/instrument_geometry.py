"""Per-instrument price geometry (merge widths, round step, opposing gap).

XAU values are dollars. FX must not inherit them — 6.0 on EURUSD is 60k pips.
"""

from __future__ import annotations

from app.core.config import runtime_config


def _effective(symbol: str):
  return runtime_config.for_instrument(symbol)


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

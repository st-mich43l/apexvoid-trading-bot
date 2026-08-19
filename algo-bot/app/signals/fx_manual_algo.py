"""Owner FX manual /algo defaults aligned with ``fx_fixed_2r_v1`` policy."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.configuration.models.instruments import InstrumentTargetMode
from app.core.config import runtime_config
from app.core.symbols import digits_for


def fx_manual_symbols() -> tuple[str, ...]:
  """Enabled instruments on fixed_rr targeting (EURUSD, GBPJPY, …)."""
  symbols: list[str] = []
  for instrument_id in runtime_config.enabled_instruments():
    try:
      effective = runtime_config.for_instrument(instrument_id)
    except Exception:
      continue
    if effective.targeting.mode is InstrumentTargetMode.FIXED_RR:
      symbols.append(instrument_id.upper())
  return tuple(symbols)


def round_price(symbol: str, price: float) -> float:
  digits = digits_for(symbol)
  quant = Decimal(10) ** -digits
  return float(Decimal(str(price)).quantize(quant, rounding=ROUND_HALF_UP))


def close_ratio_weights(symbol: str) -> list[int]:
  """Percent weights (sum 100) from instrument close_ratios."""
  targeting = runtime_config.for_instrument(symbol).targeting
  ratios = [float(value) for value in targeting.close_ratios]
  if not ratios:
    return [100]
  raw = [int(round(ratio * 100)) for ratio in ratios]
  delta = 100 - sum(raw)
  if delta and raw:
    raw[-1] += delta
  return raw


def default_stop_pips(symbol: str) -> float:
  effective = runtime_config.for_instrument(symbol)
  reaction = effective.execution.reaction
  min_pips = float(reaction.stop_min_pips)
  max_pips = float(reaction.stop_max_pips)
  if min_pips <= 0 or max_pips <= 0:
    raise ValueError(f"{symbol} reaction stop envelope must be positive")
  return (min_pips + max_pips) / 2.0


def build_fx_manual_contract(
  symbol: str,
  action: str,
  entry: float,
  *,
  sl: float | None = None,
  tps: list[float] | None = None,
) -> dict:
  """Return SL, 1R/1.5R/2R TPs, and partial weights for one FX /algo entry."""
  action = action.upper()
  symbol = symbol.upper()
  pip = runtime_config.for_instrument(symbol).units.pip_size
  entry = round_price(symbol, entry)
  if sl is None:
    stop_pips = default_stop_pips(symbol)
    offset = stop_pips * pip
    sl = round_price(
      symbol,
      entry - offset if action == "BUY" else entry + offset,
    )
  else:
    sl = round_price(symbol, sl)
  risk = abs(entry - sl)
  if risk <= 0:
    raise ValueError("FX manual stop must be on the losing side of entry")
  if tps is None:
    multiples = [
      float(value)
      for value in runtime_config.for_instrument(symbol).targeting.target_r_multiples
    ]
    if not multiples:
      multiples = [1.0, 1.5, 2.0]
    tps = [
      round_price(
        symbol,
        entry + risk * multiple if action == "BUY" else entry - risk * multiple,
      )
      for multiple in multiples
    ]
  else:
    tps = [round_price(symbol, value) for value in tps]
  return {
    "symbol": symbol,
    "entry": entry,
    "entry_end": entry,
    "sl": sl,
    "tps": tps,
    "target_weights": close_ratio_weights(symbol),
    "manual_single_entry": True,
  }

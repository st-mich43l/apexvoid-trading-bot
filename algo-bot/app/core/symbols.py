"""Instrument metadata and Telegram channel routing."""

from __future__ import annotations

from app.configuration.effective_instrument import EffectiveInstrumentError
from app.core.config import runtime_config


def _symbol_units(symbol: str) -> dict[str, float | int]:
  """Resolve pip/digits from the effective instrument context.

  Unknown instruments raise KeyError — never fall back to XAU or pip=1.0.
  """
  try:
    effective = runtime_config.for_instrument(symbol)
  except EffectiveInstrumentError as exc:
    raise KeyError(str(exc)) from None
  return {
    "pip": effective.units.pip_size,
    "digits": effective.units.price_digits,
  }


def _build_symbols_map() -> dict[str, dict[str, float | int]]:
  """Dynamic SYMBOLS map from enabled/live instruments (currently XAU)."""
  mapping: dict[str, dict[str, float | int]] = {}
  for instrument_id in runtime_config.enabled_instruments():
    mapping[instrument_id] = _symbol_units(instrument_id)
  # Preserve at least XAU for the current production helpers.
  if "XAU" not in mapping:
    mapping["XAU"] = _symbol_units("XAU")
  return mapping


# Compatibility mapping retained for callers that still index SYMBOLS directly.
# Values are sourced from the resolved instrument context, not hard-coded units.
SYMBOLS = _build_symbols_map()

# Broker-facing aliases that must resolve to the same logical instrument as
# the internal SYMBOLS key. CTRADER_SYMBOL is configured as "XAUUSD" while
# every internal candidate/analysis payload uses "XAU". The instrument context
# also recognizes XAUUSD for XAU; this map preserves the previous helper API.
_SYMBOL_ALIASES = {
  "XAUUSD": "XAU",
}


def canonical_symbol(symbol: str) -> str:
  upper = symbol.upper()
  try:
    return runtime_config.instrument_for_broker_symbol(upper).identity.canonical_symbol
  except EffectiveInstrumentError:
    return _SYMBOL_ALIASES.get(upper, upper)


CHANNELS = [
  {
    "symbol": "XAU",
    "tier": "vip",
    "channel_id": runtime_config.delivery.telegram.telegram_channel_id,
  },
  {
    "symbol": "XAU",
    "tier": "public",
    "channel_id": runtime_config.delivery.telegram.signal_public_channel_id,
  },
]


def pip_for(symbol: str) -> float:
  return float(_symbol_units(symbol)["pip"])


def digits_for(symbol: str) -> int:
  return int(_symbol_units(symbol)["digits"])


def contract_units_for(symbol: str) -> float:
  try:
    return float(runtime_config.for_instrument(symbol).units.contract_units_per_lot)
  except EffectiveInstrumentError as exc:
    raise KeyError(str(exc)) from None


def symbol_for_channel(chat_id: int | str) -> str | None:
  target = int(chat_id)
  return next(
    (
      channel["symbol"]
      for channel in CHANNELS
      if (
        channel["channel_id"] is not None
        and int(channel["channel_id"]) == target
      )
    ),
    None,
  )


def tier_for_channel(chat_id: int | str) -> str | None:
  target = int(chat_id)
  return next(
    (
      channel["tier"]
      for channel in CHANNELS
      if (
        channel["channel_id"] is not None
        and int(channel["channel_id"]) == target
      )
    ),
    None,
  )


def channels_for(symbol: str, visibility: str) -> list[dict]:
  try:
    symbol = canonical_symbol(symbol)
  except KeyError as exc:
    raise KeyError(
      f"no Telegram delivery routes configured for unknown symbol {symbol!r}"
    ) from exc
  matched = [
    dict(channel)
    for channel in CHANNELS
    if (
      channel["symbol"] == symbol
      and channel["channel_id"] is not None
      and (visibility == "both" or channel["tier"] == "vip")
    )
  ]
  if not matched and symbol != "XAU":
    raise KeyError(
      f"no Telegram channel configured for {symbol}; "
      "missing channel configuration must not fall back to XAU"
    )
  return matched


def targets_for(sig: dict) -> list[int]:
  return [
    int(channel["channel_id"])
    for channel in channels_for(
      sig["symbol"],
      sig.get("visibility", "both"),
    )
  ]


def channel_for_symbol(symbol: str) -> int:
  channels = channels_for(symbol, "vip")
  if not channels:
    raise KeyError(f"No VIP channel configured for {symbol}")
  return int(channels[0]["channel_id"])

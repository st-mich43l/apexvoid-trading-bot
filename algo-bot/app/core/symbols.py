"""Instrument metadata and Telegram channel routing."""

from __future__ import annotations

from app.core.config import runtime_config
from app.runtime.instruments import EffectiveInstrumentError, enabled_instruments
from app.runtime.instruments import for_instrument, instrument_for_broker_symbol, live_instruments


def _symbol_units(symbol: str) -> dict[str, float | int]:
  """Resolve pip/digits from the effective instrument context.

  Unknown instruments raise KeyError — never fall back to XAU or pip=1.0.
  """
  # Keep the public SYMBOLS mapping useful to callers that deliberately add a
  # temporary instrument (notably command/test harnesses).  Normal production
  # entries are still materialized from native YAML below; this is not a
  # second configuration source.
  materialized = globals().get("SYMBOLS")
  if isinstance(materialized, dict) and symbol.upper() in materialized:
    return dict(materialized[symbol.upper()])
  try:
    effective = for_instrument(runtime_config, symbol)
  except EffectiveInstrumentError as exc:
    raise KeyError(str(exc)) from None
  return {
    "pip": effective.units.pip_size,
    "digits": effective.units.price_digits,
  }


def _build_symbols_map() -> dict[str, dict[str, float | int]]:
  """Dynamic SYMBOLS map from enabled instruments."""
  mapping: dict[str, dict[str, float | int]] = {}
  for instrument_id in enabled_instruments(runtime_config):
    mapping[instrument_id] = _symbol_units(instrument_id)
  # Preserve at least XAU for the current production helpers.
  if "XAU" not in mapping:
    mapping["XAU"] = _symbol_units("XAU")
  return mapping


def __getattr__(name: str):
  if name == "SYMBOLS":
    return _build_symbols_map()
  if name == "CHANNELS":
    return _build_channels()
  raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
    return instrument_for_broker_symbol(runtime_config, upper).identity.canonical_symbol
  except EffectiveInstrumentError:
    return _SYMBOL_ALIASES.get(upper, upper)


def _build_channels() -> list[dict]:
  vip = runtime_config.telegram.telegram_channel_id
  public = runtime_config.telegram.signal_public_channel_id
  channels: list[dict] = []
  for symbol in live_instruments(runtime_config) or ("XAU",):
    channels.append({"symbol": symbol, "tier": "vip", "channel_id": vip})
    channels.append({"symbol": symbol, "tier": "public", "channel_id": public})
  return channels


def channels_list() -> list[dict]:
  """Return Telegram delivery routes for every live instrument."""
  return list(CHANNELS)


# These are public runtime maps, not a second configuration source. Keeping
# them as materialized views preserves the small helper API used by command and
# delivery code while the values themselves still come from native YAML.
SYMBOLS = _build_symbols_map()
CHANNELS = _build_channels()


def is_known_symbol(symbol: str) -> bool:
  """True when ``symbol`` resolves to an enabled instrument."""
  try:
    for_instrument(runtime_config, canonical_symbol(symbol))
  except (KeyError, EffectiveInstrumentError):
    return False
  return True


def resolve_command_symbol(token: str) -> str | None:
  """Map a command token (``xauusd``, ``EURUSD``) to the canonical id."""
  cleaned = str(token or "").strip()
  if not cleaned:
    return None
  if not is_known_symbol(cleaned):
    return None
  return canonical_symbol(cleaned)


def pip_for(symbol: str) -> float:
  return float(_symbol_units(symbol)["pip"])


def digits_for(symbol: str) -> int:
  return int(_symbol_units(symbol)["digits"])


def pip_value_per_lot(symbol: str) -> float:
  try:
    return float(for_instrument(runtime_config, symbol).units.pip_value_per_lot)
  except EffectiveInstrumentError as exc:
    raise KeyError(str(exc)) from None


def symbol_for_channel(chat_id: int | str) -> str | None:
  target = int(chat_id)
  return next(
    (
      channel["symbol"]
      for channel in channels_list()
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
      for channel in channels_list()
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
    for channel in channels_list()
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

"""Instrument metadata and Telegram channel routing."""

from app.config import settings


SYMBOLS = {
  "XAU": {
    "pip": 0.1,
    "digits": 2,
  },
  # "US30": {"pip": 1.0, "digits": 1},
  # "EURUSD": {"pip": 0.0001, "digits": 5},
}

CHANNELS = [
  {
    "symbol": "XAU",
    "tier": "vip",
    "channel_id": settings.signal_vip_channel_id,
  },
  {
    "symbol": "XAU",
    "tier": "public",
    "channel_id": settings.signal_public_channel_id,
  },
]


def pip_for(symbol: str) -> float:
  return float(SYMBOLS[symbol.upper()]["pip"])


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
  symbol = symbol.upper()
  return [
    dict(channel)
    for channel in CHANNELS
    if (
      channel["symbol"] == symbol
      and channel["channel_id"] is not None
      and (visibility == "both" or channel["tier"] == "vip")
    )
  ]


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

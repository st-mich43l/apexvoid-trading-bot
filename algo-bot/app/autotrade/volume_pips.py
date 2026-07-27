"""Volume-weighted pip PnL from broker-confirmed fill data.

Stats and Telegram must use filled volume, average entry/exit, and pip size —
never planned TP %, configured close %, estimated pips, or broker money PnL.
"""

from __future__ import annotations

from typing import Iterable, Sequence


def round_pips(value: float) -> float:
  return round(float(value), 1)


def round_percent(value: float) -> float:
  return round(float(value), 1)


def volume_percent(part_volume: float, initial_volume: float) -> float:
  if initial_volume <= 0:
    return 0.0
  return round_percent(part_volume / initial_volume * 100.0)


def broker_volume_to_lots(volume: float, lot_size: float) -> float:
  if lot_size <= 0:
    return 0.0
  return float(volume) / float(lot_size)


def format_lots(lots: float) -> str:
  text = f"{float(lots):.8f}".rstrip("0").rstrip(".")
  return text if text else "0"


def format_signed_pips(pips: float) -> str:
  value = round_pips(pips)
  return f"{value:+.1f}"


def leg_pips(
  direction: str,
  average_entry_price: float,
  average_exit_price: float,
  pip_size: float,
) -> float:
  if pip_size <= 0:
    return 0.0
  side = str(direction or "").strip().upper()
  if side in {"SELL", "S", "SHORT"}:
    raw = (average_entry_price - average_exit_price) / pip_size
  else:
    raw = (average_exit_price - average_entry_price) / pip_size
  return round_pips(raw)


def trade_net_pips(
  legs: Sequence[tuple[float, float]],
  initial_filled_volume: float,
) -> float:
  """Volume-weighted net pips across partial legs.

  tradeNetPips = SUM(legPips * actualClosedVolume) / initialFilledVolume
  """
  if initial_filled_volume <= 0:
    return 0.0
  pip_volume = sum(float(pips) * float(volume) for pips, volume in legs)
  return round_pips(pip_volume / float(initial_filled_volume))


def accumulate_pip_volume(
  existing_pip_volume: float,
  leg_pips_value: float,
  actual_closed_volume: float,
) -> float:
  return float(existing_pip_volume) + float(leg_pips_value) * float(
    actual_closed_volume
  )


def remaining_after_close(
  initial_filled_volume: float,
  closed_volumes: Iterable[float],
) -> float:
  closed = sum(float(item) for item in closed_volumes)
  return max(0.0, float(initial_filled_volume) - closed)

"""Cross-strategy same-direction entry-zone overlap guard for Go-origin plans.

Every Go strategy owns its own thesis identity, so the per-thesis claim can
never stop two different strategies from opening a plan on the same price
band, and Go-origin plans skip the zone claim and the stop-out cooldown.
Production 2026-09-30 (XAU): Order Block, Key Level and two Range Sweep
scalps all bought the same ~7 pip band inside 35 minutes, four of them at a
full stop.

A plan reserves its entry zone when it is admitted and holds it for
WINDOW_SECONDS, whether or not the position is still open. Reserving at
admission (not at fill) is what closes the stop-out re-entry hole.
Opposite-direction overlap is deliberately not handled here: scalp hedging
is an explicit owner decision enforced in active_exposure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

WINDOW_SECONDS = 45 * 60
PAD_ATR = 1.0
_MAX_RESERVATIONS = 64


@dataclass(frozen=True)
class EntryReservation:
  setup_id: str
  strategy: str
  direction: str
  low: float
  high: float
  reserved_at: int


def entry_overlap_key(symbol: str) -> str:
  return f"autotrade:entry_overlap:{symbol.upper()}"


def find_overlap(
  reservations: list[EntryReservation],
  *,
  setup_id: str,
  direction: str,
  low: float,
  high: float,
  atr: float,
  now: int,
) -> EntryReservation | None:
  """First live same-direction reservation whose zone lies within PAD_ATR of
  the candidate zone, or None."""
  pad = PAD_ATR * max(0.0, float(atr))
  wanted = direction.upper()
  for item in reservations:
    if item.setup_id == setup_id or item.direction != wanted:
      continue
    if now - item.reserved_at > WINDOW_SECONDS:
      continue
    if low - pad <= item.high and high + pad >= item.low:
      return item
  return None


def _decode(raw: Any) -> list[EntryReservation]:
  if raw is None:
    return []
  try:
    rows = json.loads(raw)
    return [
      EntryReservation(
        setup_id=str(row["setup_id"]),
        strategy=str(row["strategy"]),
        direction=str(row["direction"]).upper(),
        low=float(row["low"]),
        high=float(row["high"]),
        reserved_at=int(row["reserved_at"]),
      )
      for row in rows
    ]
  except (TypeError, ValueError, KeyError):
    return []


def _encode(reservations: list[EntryReservation]) -> str:
  return json.dumps([
    {
      "setup_id": item.setup_id,
      "strategy": item.strategy,
      "direction": item.direction,
      "low": item.low,
      "high": item.high,
      "reserved_at": item.reserved_at,
    }
    for item in reservations
  ])


async def reserve_entry_zone(
  client: Any,
  *,
  symbol: str,
  setup_id: str,
  strategy: str,
  direction: str,
  low: float,
  high: float,
  atr: float,
  now: int,
) -> EntryReservation | None:
  """Reserve the zone, or return the reservation that blocks it.

  Plan admission is serial per worker, so the read-modify-write below is
  not raced by a second admission.
  """
  key = entry_overlap_key(symbol)
  live = [
    item for item in _decode(await client.get(key))
    if now - item.reserved_at <= WINDOW_SECONDS
  ]
  blocker = find_overlap(
    live, setup_id=setup_id, direction=direction,
    low=low, high=high, atr=atr, now=now,
  )
  if blocker is not None:
    return blocker
  live = [item for item in live if item.setup_id != setup_id]
  live.append(EntryReservation(
    setup_id=setup_id, strategy=strategy, direction=direction.upper(),
    low=low, high=high, reserved_at=now,
  ))
  await client.set(
    key, _encode(live[-_MAX_RESERVATIONS:]), ex=WINDOW_SECONDS + 60,
  )
  return None


async def release_entry_zone(client: Any, *, symbol: str, setup_id: str) -> None:
  """Drop this setup's reservation when its plan never reached the broker."""
  key = entry_overlap_key(symbol)
  live = _decode(await client.get(key))
  kept = [item for item in live if item.setup_id != setup_id]
  if len(kept) == len(live):
    return
  if kept:
    await client.set(key, _encode(kept), ex=WINDOW_SECONDS + 60)
  else:
    await client.delete(key)

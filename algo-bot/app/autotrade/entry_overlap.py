"""Same-thesis entry corridor: identification and atomic reservation.

Every Go strategy owns its own thesis identity, so a per-thesis claim can never
stop two different strategies from opening a plan on the same price band.
Production 2026-09-30 (XAU): Order Block, Key Level and two Range Sweep scalps
all bought the same ~7 pip band inside 35 minutes, four of them at a full stop.

Two opportunities compete for one executable thesis when they share symbol and
direction and either carry the same Go thesis or structural identity, or their
entry zones lie within PAD_ATR of each other. Cycle arbitration
(``arbitration.arbitrate_execution_intents``) selects the best of a competing
group; the winner then reserves the corridor here, atomically, and holds it for
WINDOW_SECONDS whether or not the position is still open. Reserving at
admission (not at fill) closes the stop-out re-entry hole.

Opposite-direction overlap is deliberately not handled here: it is governed by
the instrument exposure policy in ``active_exposure``.
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
  quality: float = 0.0


class ReservationUnavailable(RuntimeError):
  """The atomic reservation could not run; admission must fail closed."""


def entry_overlap_key(symbol: str) -> str:
  return f"autotrade:entry_overlap:{symbol.upper()}"


def corridors_overlap(
  low: float, high: float, other_low: float, other_high: float, atr: float,
) -> bool:
  """True when two entry zones intersect after padding one by PAD_ATR."""
  pad = PAD_ATR * max(0.0, float(atr))
  return low - pad <= other_high and high + pad >= other_low


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
  """First live same-direction reservation whose corridor overlaps, or None."""
  wanted = direction.upper()
  for item in reservations:
    if item.setup_id == setup_id or item.direction != wanted:
      continue
    if now - item.reserved_at > WINDOW_SECONDS:
      continue
    if corridors_overlap(low, high, item.low, item.high, atr):
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
        quality=float(row.get("quality", 0.0)),
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
      "quality": item.quality,
    }
    for item in reservations
  ])


# One Redis round trip decides and writes: two workers can never both observe
# a free corridor. Returns the blocking reservation as JSON, or false.
_RESERVE_LUA = """
local raw = redis.call('GET', KEYS[1])
local rows = {}
if raw then
  local ok, decoded = pcall(cjson.decode, raw)
  if ok and type(decoded) == 'table' then rows = decoded end
end
local setup_id, direction = ARGV[1], ARGV[3]
local low, high, pad = tonumber(ARGV[4]), tonumber(ARGV[5]), tonumber(ARGV[6])
local now, window, limit = tonumber(ARGV[7]), tonumber(ARGV[8]), tonumber(ARGV[9])
local live = {}
for _, row in ipairs(rows) do
  if now - tonumber(row.reserved_at) <= window then table.insert(live, row) end
end
for _, row in ipairs(live) do
  if row.setup_id ~= setup_id and row.direction == direction
     and low - pad <= tonumber(row.high) and high + pad >= tonumber(row.low) then
    return cjson.encode(row)
  end
end
local kept = {}
for _, row in ipairs(live) do
  if row.setup_id ~= setup_id then table.insert(kept, row) end
end
table.insert(kept, {
  setup_id = setup_id, strategy = ARGV[2], direction = direction,
  low = low, high = high, reserved_at = now, quality = tonumber(ARGV[10]),
})
while #kept > limit do table.remove(kept, 1) end
redis.call('SET', KEYS[1], cjson.encode(kept), 'EX', window + 60)
return false
"""


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
  quality: float = 0.0,
) -> EntryReservation | None:
  """Atomically reserve the corridor, or return the reservation blocking it."""
  key = entry_overlap_key(symbol)
  wanted = direction.upper()
  pad = PAD_ATR * max(0.0, float(atr))
  try:
    blocker = await client.eval(
      _RESERVE_LUA, 1, key,
      setup_id, strategy, wanted, repr(float(low)), repr(float(high)),
      repr(float(pad)), int(now), WINDOW_SECONDS, _MAX_RESERVATIONS,
      repr(float(quality)),
    )
  except Exception as exc:
    # Local import: cycle_publish imports arbitration, which imports this module.
    from app.autotrade.cycle_publish import explicit_test_fallback_enabled

    if not explicit_test_fallback_enabled(client):
      raise ReservationUnavailable(
        "entry corridor reservation could not run atomically",
      ) from exc
    return await _reserve_non_atomic(
      client, key=key, setup_id=setup_id, strategy=strategy, direction=wanted,
      low=low, high=high, atr=atr, now=now, quality=quality,
    )
  if not blocker:
    return None
  decoded = _decode(f"[{blocker.decode() if isinstance(blocker, bytes) else blocker}]")
  return decoded[0] if decoded else None


async def _reserve_non_atomic(
  client: Any, *, key: str, setup_id: str, strategy: str, direction: str,
  low: float, high: float, atr: float, now: int, quality: float,
) -> EntryReservation | None:
  """Test-double path for a Redis without scripting (never used in production)."""
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
    setup_id=setup_id, strategy=strategy, direction=direction,
    low=low, high=high, reserved_at=now, quality=quality,
  ))
  await client.set(key, _encode(live[-_MAX_RESERVATIONS:]), ex=WINDOW_SECONDS + 60)
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

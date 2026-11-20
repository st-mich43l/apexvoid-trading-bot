"""Currency exposure across FX pairs, measured and atomic (FX1).

``group_exposure`` bounds exposure per symbol and direction: one group at a time per pair. It cannot see that three pairs are one bet. SELL EURUSD, SELL GBPUSD and
BUY USDJPY are the same position (long USD); they stop out together. Production 2026-11-20: USDJPY BUY was stopped out four times in five hours while a second
long-USD trade was open each time.

When an FX pack declares ``exposure.currency: {max_groups_per_currency_direction: N}`` (config/instruments.yml) a new group is admitted only if, counting every
pending, submitted, partially filled and open group of every FX pair (a group keeps counting after TP1 or TP2), it puts no more than N groups on the same side of
any currency. A group expresses its direction in BOTH currencies of its pair:

* BUY EURUSD = long EUR, short USD;     SELL EURUSD = short EUR, long USD;
* BUY USDJPY = long USD, short JPY;     SELL USDJPY = short USD, long JPY;
* BUY GBPJPY = long GBP, short JPY.

Admission is atomic across the pairs that share a currency: one Lua script counts the in-flight reservations of both of the incoming group's currency sides together
with the runtime groups the caller measured, and writes the new reservation to both, so two workers can never both take the last slot of a currency. A reservation is
superseded once its plan appears in the executor runtime, released explicitly when publication fails, and expires on its own TTL.

Unknown exposure (an unparsable runtime state, a stale executor snapshot) fails closed, as in ``group_exposure``. XAU is in no currency group. With no
``exposure.currency`` nothing here runs.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.autotrade.active_exposure import _as_int, normalize_direction, normalize_symbol
from app.autotrade.group_exposure import (
  EXPOSURE_UNKNOWN,
  GroupDecision,
  GroupExposure,
  GroupExposureUnavailable,
  IncomingGroup,
  SymbolExposure,
  _plan_is_dead,
  load_symbol_exposure,
)
from app.runtime.instruments import CurrencyExposurePolicy, SameDirectionLimits, SameDirectionPolicy

log = logging.getLogger(__name__)

CURRENCY_EXPOSURE_LIMIT = "currency_exposure_limit"
LONG, SHORT = "LONG", "SHORT"
FX_PAIRS = ("EURUSD", "GBPUSD", "USDJPY", "GBPJPY")


def currency_sides(symbol: str, direction: str) -> dict[str, str]:
  """The side of each currency a group of ``symbol`` in ``direction`` takes; empty for anything that is not an FX pair (XAU) or has no direction."""
  pair = normalize_symbol(symbol)
  side = normalize_direction(direction)
  if pair not in FX_PAIRS or side not in ("BUY", "SELL"):
    return {}
  base, quote = pair[:3], pair[3:]
  return {base: LONG, quote: SHORT} if side == "BUY" else {base: SHORT, quote: LONG}


def _group_on_side(group: GroupExposure, currency: str, side: str) -> bool:
  """True when ``group`` holds ``side`` of ``currency``. A group of unknown direction could be on either side: it counts on both."""
  sides = currency_sides(group.symbol, group.direction)
  if sides:
    return sides.get(currency) == side
  pair = normalize_symbol(group.symbol)
  return pair in FX_PAIRS and currency in (pair[:3], pair[3:])


def reservation_key(currency: str, side: str) -> str:
  return f"autotrade:currency_exposure:{currency}:{side}"


def evaluate_currency_exposure(
  policy: CurrencyExposurePolicy,
  incoming: IncomingGroup,
  groups: Iterable[GroupExposure],
  *,
  unknown_reasons: Iterable[str] = (),
) -> GroupDecision:
  """Decide whether ``incoming`` may be admitted next to the live ``groups`` of every FX pair.

  Pure: the same function drives live admission accounting and the offline policy replay. ``groups`` are runtime groups (reservations are counted by the atomic
  script); the incoming group's own setup is ignored.
  """
  sides = currency_sides(incoming.symbol, incoming.direction)
  unknown_reasons = list(unknown_reasons)
  measured: dict[str, Any] = {
    "symbol": incoming.symbol, "direction": incoming.direction, "strategy": incoming.strategy,
    "sides": dict(sides), "max_groups_per_currency_direction": policy.max_groups_per_currency_direction, "unknown_reasons": unknown_reasons,
  }
  if not sides:
    return GroupDecision(True, None, "", measured)  # not an FX pair: never affected
  if unknown_reasons:
    return GroupDecision(
      False, EXPOSURE_UNKNOWN, f"{incoming.symbol} currency exposure cannot be established ({', '.join(unknown_reasons[:3])}); failing closed", measured,
    )
  mine = [g for g in groups if g.setup_id != incoming.setup_id and g.source != "reservation"]
  counts: dict[str, int] = {}
  for currency, side in sides.items():
    on_side = [g for g in mine if _group_on_side(g, currency, side)]
    counts[f"{currency}:{side}"] = len(on_side)
    if len(on_side) + 1 > policy.max_groups_per_currency_direction:
      measured.update(counts=dict(counts), currency=currency, currency_side=side, existing_plan_ids=[g.plan_id for g in on_side])
      return GroupDecision(
        False, CURRENCY_EXPOSURE_LIMIT,
        f"{incoming.symbol} {incoming.direction}: {len(on_side)} group(s) already {side.lower()} {currency}, limit {policy.max_groups_per_currency_direction}", measured,
      )
  measured["counts"] = counts
  return GroupDecision(True, None, "", measured)


# One round trip counts the in-flight reservations of BOTH currency sides together with the runtime groups the caller measured, and writes the new reservation to
# both. Reservations are delimited strings in a hash keyed by setup id: expires|created|plan_id|symbol.
_RESERVE_LUA = """
local now = tonumber(ARGV[1])
local setup_id = ARGV[2]
local max_groups = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local record = ARGV[5]
local function parse(value)
  local f = {}
  for part in string.gmatch(value .. '|', '([^|]*)|') do table.insert(f, part) end
  return f
end
local counts = {}
for i = 1, 2 do
  local key = KEYS[i]
  local rt = tonumber(ARGV[3 + i * 3])
  local known = ARGV[4 + i * 3]
  local dead = ARGV[5 + i * 3]
  local res = 0
  local flat = redis.call('HGETALL', key)
  for j = 1, #flat, 2 do
    local field, value = flat[j], flat[j + 1]
    local f = parse(value)
    local expires = tonumber(f[1]) or 0
    local created = tonumber(f[2]) or 0
    local in_runtime = string.find(known, ',' .. field .. ',', 1, true)
    if expires <= now or string.find(dead, ',' .. field .. ',', 1, true) or (in_runtime and now - created > 120) then
      redis.call('HDEL', key, field)
    elseif in_runtime then
      -- counted through the runtime
    elseif field ~= setup_id then
      res = res + 1
    end
  end
  counts[i] = rt + res
end
for i = 1, 2 do
  if max_groups >= 0 and counts[i] + 1 > max_groups then
    return {0, i, tostring(counts[1]), tostring(counts[2])}
  end
end
for i = 1, 2 do
  redis.call('HSET', KEYS[i], setup_id, record)
  redis.call('EXPIRE', KEYS[i], ttl + 60)
end
return {1, 0, tostring(counts[1]), tostring(counts[2])}
"""


def _encode(expires_at: int, created_at: int, incoming: IncomingGroup) -> str:
  fields = [str(int(expires_at)), str(int(created_at)), incoming.plan_id, normalize_symbol(incoming.symbol) or incoming.symbol]
  return "|".join(field_.replace("|", "/") for field_ in fields)


async def _read_reservations(client: Any, keys: Iterable[str]) -> dict[str, tuple[str, str]]:
  """setup id -> (record, plan id) over the given reservation hashes."""
  out: dict[str, tuple[str, str]] = {}
  for key in keys:
    raw = await client.hgetall(key)
    for field_, value in (raw or {}).items():
      field_ = field_.decode() if isinstance(field_, bytes) else str(field_)
      value = value.decode() if isinstance(value, bytes) else str(value)
      parts = value.split("|")
      if len(parts) >= 3:
        out[field_] = (value, parts[2])
  return out


async def reserve_currency_slot(
  client: Any,
  *,
  policy: CurrencyExposurePolicy,
  incoming: IncomingGroup,
  runtime_groups: Iterable[GroupExposure],
  now: int | None = None,
) -> GroupDecision:
  """Atomically admit ``incoming`` and reserve its slot on both currency sides, or say why not."""
  now = int(time.time()) if now is None else int(now)
  sides = currency_sides(incoming.symbol, incoming.direction)
  if not sides:
    return GroupDecision(True, None, "", {"symbol": incoming.symbol})
  pairs = list(sides.items())  # exactly two currencies
  runtime = [g for g in runtime_groups if g.source != "reservation" and g.setup_id != incoming.setup_id]
  keys = [reservation_key(currency, side) for currency, side in pairs]
  existing = await _read_reservations(client, keys)
  known_all = {g.setup_id for g in runtime}
  dead = []
  for setup_id, (value, plan_id) in existing.items():
    if setup_id in known_all or setup_id == incoming.setup_id:
      continue
    created = _as_int(value.split("|")[1]) if len(value.split("|")) > 1 else None
    if await _plan_is_dead(client, plan_id, created, now):
      dead.append(setup_id)
  args: list[Any] = [now, incoming.setup_id, policy.max_groups_per_currency_direction, policy.reservation_ttl_seconds, _encode(now + policy.reservation_ttl_seconds, now, incoming)]
  for currency, side in pairs:
    on_side = [g for g in runtime if _group_on_side(g, currency, side)]
    args.extend([len(on_side), "," + ",".join(sorted({g.setup_id for g in on_side})) + ",", "," + ",".join(dead) + ","])
  try:
    raw = await client.eval(_RESERVE_LUA, 2, keys[0], keys[1], *args)
  except Exception as exc:
    raise GroupExposureUnavailable("currency exposure reservation could not run atomically") from exc
  ok, which, first, second = int(raw[0]), int(raw[1]), raw[2], raw[3]
  measured = {
    "symbol": incoming.symbol, "direction": incoming.direction, "strategy": incoming.strategy, "sides": dict(sides),
    "max_groups_per_currency_direction": policy.max_groups_per_currency_direction,
    "counts": {f"{pairs[0][0]}:{pairs[0][1]}": float(first), f"{pairs[1][0]}:{pairs[1][1]}": float(second)},
  }
  if ok == 1:
    return GroupDecision(True, None, "", measured)
  currency, side = pairs[which - 1]
  measured.update(currency=currency, currency_side=side)
  return GroupDecision(False, CURRENCY_EXPOSURE_LIMIT, f"{incoming.symbol} {incoming.direction}: currency cap reached on {side.lower()} {currency}", measured)


async def release_currency_slot(client: Any, *, symbol: str, direction: str, setup_id: str) -> None:
  """Drop the reservations of a plan that never reached the executor."""
  for currency, side in currency_sides(symbol, direction).items():
    try:
      await client.hdel(reservation_key(currency, side), setup_id)
    except Exception:
      log.exception("currency exposure release failed symbol=%s setup_id=%s", symbol, setup_id)


def _default_same_direction(symbol: str) -> SameDirectionPolicy:
  return SameDirectionPolicy(symbol=symbol, limits=SameDirectionLimits())


async def load_fx_exposures(client: Any, *, now: float | None = None) -> dict[str, SymbolExposure]:
  """Measure the live groups of every FX pair (runtime plus broker snapshot), as ``group_exposure`` does for one symbol."""
  from app.core import instrument_geometry

  out: dict[str, SymbolExposure] = {}
  for pair in FX_PAIRS:
    policy = instrument_geometry.same_direction_policy(pair) or _default_same_direction(pair)
    units = instrument_geometry.instrument_runtime(pair).units
    out[pair] = await load_symbol_exposure(client, symbol=pair, units=units, policy=policy, now=now)
  return out


async def admit_currency_group(
  client: Any,
  *,
  policy: CurrencyExposurePolicy,
  incoming: IncomingGroup,
  now: int | None = None,
  exposures: Mapping[str, SymbolExposure] | None = None,
) -> GroupDecision:
  """Measure every FX pair's exposure and atomically admit ``incoming`` or refuse it.

  Unknown exposure refuses before any reservation is attempted. Raises ``GroupExposureUnavailable`` when Redis cannot run the reservation: the caller must treat
  that as a refusal, never as permission.
  """
  if not currency_sides(incoming.symbol, incoming.direction):
    return GroupDecision(True, None, "", {"symbol": incoming.symbol})
  measured_at = None if now is None else float(now)
  exposures = dict(exposures) if exposures is not None else await load_fx_exposures(client, now=measured_at)
  unknown = [reason for exposure in exposures.values() for reason in exposure.unknown_reasons]
  groups = [g for exposure in exposures.values() for g in exposure.groups]
  pure = evaluate_currency_exposure(policy, incoming, groups, unknown_reasons=unknown)
  if not pure.allowed:
    return pure
  return await reserve_currency_slot(client, policy=policy, incoming=incoming, runtime_groups=groups, now=now)

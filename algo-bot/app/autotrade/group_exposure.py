"""Same-direction group and cumulative-risk exposure, measured and atomic.

Production 2026-10-12 (USDJPY): four Range Edge Scalp SELLs were admitted
between 06:51 and 09:01 against the same rising resistance. Each was a
different opportunity, so the thesis claim passed; the entry-corridor guard
covers only 45 minutes after admission and the plan that first claimed the
corridor was still open; and a scalp may stack in the same direction without
any count (``allow_same_direction_stack = candidate_is_scalp``). All four
stopped out together.

This module is the instrument-owned portfolio protection that closes that
hole. When an instrument declares ``exposure.same_direction`` (config/
instruments.yml) a new group is admitted only if, counting every pending,
submitted, partially filled and open group of the symbol:

* the number of concurrent groups in its direction stays within ``max_groups``;
* the sum of every group's worst-case remaining loss plus the new group's
  planned loss stays within ``max_cumulative_risk`` (this direction) and
  ``max_symbol_risk`` (both directions), in account currency;
* no group already owns the same thesis or structure.

Risk is money, not pips: lots x distance to the stop x pip value. A group's
risk shrinks as volume is closed and as its stop moves, but a group keeps
counting toward ``max_groups`` while any of it is open, so booking TP1 or TP2
never reopens the slot. Anything whose existence or risk cannot be established
(an unparsable runtime state, a broker group with no runtime, a stale executor
snapshot, an unsized plan with no estimate) is unknown exposure and fails
closed; it is never treated as zero.

Admission is atomic per ``symbol + direction``: one Lua script counts the
in-flight reservations together with the runtime exposure the caller measured
and writes the new reservation, so two workers can never both claim the last
slot. A reservation is superseded once its plan appears in the executor runtime,
released explicitly when publication fails, and expires on its own TTL so a
crashed worker cannot hold a slot forever.

Nothing here runs unless the instrument declares the policy: with no
``exposure.same_direction`` the legacy behaviour is unchanged.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

from app.autotrade.active_exposure import (
  _OPEN_TRADE_PLAN_GROUP_STAGES,
  _OPEN_TRADE_PLAN_STAGES,
  _PENDING_TRADE_PLAN_GROUP_STAGES,
  _PENDING_TRADE_PLAN_STAGES,
  _as_float,
  _as_int,
  _is_non_live_trade_plan,
  _mget_or_get,
  _payload_get,
  normalize_direction,
  normalize_symbol,
)
from app.runtime.instruments import SameDirectionLimits, SameDirectionPolicy

log = logging.getLogger(__name__)

SAME_DIRECTION_GROUP_LIMIT = "same_direction_group_limit"
SAME_DIRECTION_RISK_BUDGET = "same_direction_risk_budget"
SYMBOL_RISK_BUDGET = "symbol_risk_budget"
SAME_THESIS_ACTIVE = "same_thesis_active"
EXPOSURE_UNKNOWN = "exposure_unknown"
RESERVATION_UNAVAILABLE = "group_exposure_reservation_unavailable"

_EPSILON = 1e-9
_DEAD_PLAN_GRACE_SECONDS = 90
_TERMINAL_LEG_STAGES = frozenset({"cancelled", "expired", "rejected", "closed"})
_FILLED_LEG_STAGES = frozenset({"filled", "managing"})

_BUY, _SELL = "BUY", "SELL"


# ---------------------------------------------------------------------------
# Sizing mirror: what the executor will size a plan to (VolumePlanner.cs)
# ---------------------------------------------------------------------------


def _round_half_up(value: Decimal, places: int = 2) -> Decimal:
  return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def lots_for_equity(equity: float, *, fx: bool) -> float:
  """Owner equity -> lots table (``VolumePlanner.LotsForEquity``)."""
  value = Decimal(str(equity))
  if value < 200:
    return 0.0
  if fx and value >= 2_000:
    if value >= 5_000:
      lots = Decimal("0.30")
    elif value >= 3_000:
      lots = Decimal("0.20") + (value - 3_000) * Decimal("0.10") / 2_000
    else:
      lots = Decimal("0.15") + (value - 2_000) * Decimal("0.05") / 1_000
    return float(_round_half_up(lots))
  if value >= 5_000:
    lots = Decimal("0.30")
  elif value >= 3_000:
    lots = Decimal("0.25") + (value - 3_000) * Decimal("0.05") / 2_000
  elif value >= 2_000:
    lots = Decimal("0.15")
  elif value > 1_000:
    lots = Decimal("0.12")
  elif value >= 600:
    lots = Decimal("0.10")
  else:
    lots = Decimal("0.02") + (value - 200) * Decimal("0.04") / 700
  return float(_round_half_up(lots))


_FX_SYMBOLS = frozenset({"EURUSD", "GBPUSD", "GBPJPY", "USDJPY"})


def planned_lots(
  *,
  symbol: str,
  equity: float | None,
  sizing_mode: str,
  risk_multiplier: float,
  risk_percent: float,
  stop_pips: float,
  pip_value_per_lot: float,
) -> float | None:
  """Lots the executor will size the plan to, or ``None`` when not estimable."""
  if equity is None or not equity > 0:
    return None
  if sizing_mode == "risk":
    if not (risk_percent > 0 and stop_pips > 0 and pip_value_per_lot > 0):
      return None
    return float(equity) * risk_percent / 100.0 / (stop_pips * pip_value_per_lot)
  if sizing_mode != "equity_table":
    return None
  table = lots_for_equity(float(equity), fx=normalize_symbol(symbol) in _FX_SYMBOLS)
  if table <= 0:
    return None
  multiplier = risk_multiplier if risk_multiplier > 0 else 1.0
  if multiplier > 1.0 and float(equity) < 2_000:
    multiplier = 1.5
  return float(_round_half_up(Decimal(str(table)) * Decimal(str(multiplier))))


def stop_distance_pips(direction: str, prices: Iterable[float], stop: float, pip_size: float) -> float:
  """Worst-case distance from any entry price to the stop (the fill that loses most)."""
  prices = [price for price in prices if price and price > 0]
  if not prices or not stop or pip_size <= 0:
    return 0.0
  if normalize_direction(direction) == _BUY:
    worst = max(prices)
    return max(0.0, (worst - stop) / pip_size)
  worst = min(prices)
  return max(0.0, (stop - worst) / pip_size)


def planned_plan_risk(
  plan: Any,
  *,
  equity: float | None,
  units: Any,
) -> float | None:
  """Planned worst-case loss of a freshly built TradePlan, in account currency."""
  try:
    direction = str(plan.analysis.direction)
    stop = float(plan.stop.price)
    prices = [float(price) for price in plan.entry.entry_prices()]
    pips = stop_distance_pips(direction, prices, stop, float(units.pip_size))
    lots = planned_lots(
      symbol=str(plan.symbol),
      equity=equity,
      sizing_mode=str(plan.sizing.mode),
      risk_multiplier=float(plan.risk.risk_multiplier),
      risk_percent=float(plan.risk.risk_percent),
      stop_pips=pips,
      pip_value_per_lot=float(units.pip_value_per_lot),
    )
  except (AttributeError, TypeError, ValueError):
    return None
  if lots is None or not pips > 0:
    return None
  return float(lots) * pips * float(units.pip_value_per_lot)


# ---------------------------------------------------------------------------
# Exposure records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupExposure:
  """One pending, submitted, partially filled or open group of a symbol."""

  plan_id: str
  setup_id: str
  symbol: str
  direction: str
  source: str  # runtime | reservation | broker_snapshot
  thesis_id: str | None = None
  structural_id: str | None = None
  strategy: str | None = None
  stage: str = ""
  # Worst-case remaining loss in account currency; ``None`` = unknown.
  risk_amount: float | None = None
  risk_basis: str = "unknown"
  highest_booked_target_index: int | None = None
  remaining_lots: float | None = None


@dataclass(frozen=True)
class IncomingGroup:
  setup_id: str
  plan_id: str
  symbol: str
  direction: str
  strategy: str
  thesis_id: str | None
  structural_id: str | None
  risk_amount: float | None


@dataclass(frozen=True)
class GroupDecision:
  allowed: bool
  reason_code: str | None = None
  message: str = ""
  measured: dict[str, Any] = field(default_factory=dict)


def _setup_id(value: str) -> str:
  text = str(value or "")
  return text[3:] if text.startswith("v8:") else text


def _leg_price(leg: dict[str, Any], *keys: str) -> float | None:
  price = _as_float(_payload_get(leg, *keys))
  return price if price is not None and price > 0 else None


def runtime_group_risk(
  payload: dict[str, Any],
  *,
  pip_size: float,
  pip_value_per_lot: float,
  volume_units_per_lot: float,
) -> tuple[float | None, float | None]:
  """Worst-case remaining loss of one runtime plan state: (risk, remaining lots).

  Open volume loses (fill - stop) at its stop; an unfilled leg that can still
  fill loses (intended price - stop). Closed, cancelled, expired and rejected
  legs contribute nothing. ``None`` when the volume or the stop cannot be read.
  """
  direction = normalize_direction(_payload_get(payload, "direction", "Direction"))
  if direction is None or pip_size <= 0 or pip_value_per_lot <= 0 or volume_units_per_lot <= 0:
    return None, None
  stop = None
  for key in ("current_stop", "CurrentStop", "group_absolute_stop", "GroupAbsoluteStop"):
    value = _as_float(payload.get(key))
    if value is not None and value > 0:
      stop = value
      break
  if stop is None:
    return None, None

  def loss_pips(price: float) -> float:
    return max(0.0, (price - stop) / pip_size) if direction == _BUY else max(0.0, (stop - price) / pip_size)

  legs = _payload_get(payload, "legs", "Legs")
  exposed: list[tuple[float, float]] = []  # (volume, price)
  if isinstance(legs, list) and legs:
    for leg in legs:
      if not isinstance(leg, dict):
        return None, None
      stage = str(_payload_get(leg, "stage", "Stage") or "").strip().lower()
      if stage in _TERMINAL_LEG_STAGES:
        continue
      intended = _as_float(_payload_get(leg, "intended_volume", "IntendedVolume")) or 0.0
      filled = _as_float(_payload_get(leg, "filled_volume", "FilledVolume")) or 0.0
      remaining = _as_float(_payload_get(leg, "remaining_volume", "RemainingVolume"))
      fill_price = _leg_price(leg, "fill_price", "FillPrice")
      intended_price = _leg_price(leg, "intended_price", "IntendedPrice")
      if filled > 0:
        open_volume = remaining if remaining is not None else filled
        price = fill_price or intended_price
        if open_volume > 0:
          if price is None:
            return None, None
          exposed.append((open_volume, price))
      if stage not in _FILLED_LEG_STAGES and intended - filled > 0:
        if intended_price is None:
          return None, None
        exposed.append((intended - filled, intended_price))
  else:
    remaining = _as_float(_payload_get(payload, "remaining_volume", "RemainingVolume")) or 0.0
    intended = _as_float(_payload_get(payload, "total_intended_volume", "TotalIntendedVolume")) or 0.0
    filled = _as_float(_payload_get(payload, "total_filled_volume", "TotalFilledVolume")) or 0.0
    fill_price = None
    for key in ("group_weighted_fill_price", "GroupWeightedFillPrice", "entry_fill_price", "EntryFillPrice"):
      value = _as_float(payload.get(key))
      if value is not None and value > 0:
        fill_price = value
        break
    intended_price = _as_float(_payload_get(payload, "intended_entry_price", "IntendedEntryPrice"))
    if remaining > 0:
      if fill_price is None:
        return None, None
      exposed.append((remaining, fill_price))
    if intended - filled > 0 and intended_price and intended_price > 0:
      exposed.append((intended - filled, intended_price))
  if not exposed:
    return None, None
  lots = sum(volume for volume, _ in exposed) / volume_units_per_lot
  risk = sum(volume / volume_units_per_lot * loss_pips(price) * pip_value_per_lot for volume, price in exposed)
  return risk, lots


@dataclass
class SymbolExposure:
  """Everything the policy needs to know about a symbol's live groups."""

  symbol: str
  groups: list[GroupExposure] = field(default_factory=list)
  # Reasons the picture could not be established; any of them fails closed.
  unknown_reasons: list[str] = field(default_factory=list)
  snapshot_age_seconds: float | None = None


async def load_symbol_exposure(
  client: Any,
  *,
  symbol: str,
  units: Any,
  policy: SameDirectionPolicy,
  now: float | None = None,
) -> SymbolExposure:
  """Measure the symbol's live groups from the executor runtime and snapshot.

  The runtime plan states give the volumes and stops; the executor snapshot is
  the broker-side list of groups, used only to prove nothing live is missing
  from the runtime picture. A plan counted from both is one group (keyed by
  plan id).
  """
  canonical = normalize_symbol(symbol) or str(symbol).upper()
  now = time.time() if now is None else now
  exposure = SymbolExposure(symbol=canonical)

  snapshot_groups: set[str] = set()
  raw_snapshot = await client.get(f"auto_trade:executor_snapshot:{canonical}")
  if raw_snapshot is None:
    exposure.unknown_reasons.append("executor_snapshot_missing")
  else:
    try:
      snapshot = json.loads(raw_snapshot.decode() if isinstance(raw_snapshot, bytes) else raw_snapshot)
      updated = float(snapshot.get("updated_at"))
      snapshot_groups = {str(item) for item in snapshot.get("group_ids") or []}
      exposure.snapshot_age_seconds = max(0.0, now - updated)
      if exposure.snapshot_age_seconds > policy.snapshot_max_age_seconds:
        exposure.unknown_reasons.append(
          f"executor_snapshot_stale:{exposure.snapshot_age_seconds:.0f}s"
        )
      if snapshot.get("ready") is False:
        exposure.unknown_reasons.append("executor_not_ready")
    except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
      exposure.unknown_reasons.append("executor_snapshot_unreadable")

  raw_ids = await client.get("execution:trade_plan_runtime_ids")
  text = "" if not raw_ids else (raw_ids.decode() if isinstance(raw_ids, bytes) else str(raw_ids))
  plan_ids = [item.strip() for item in text.split(",") if item.strip()]
  states = await _mget_or_get(client, [f"execution:plan_runtime:{plan_id}" for plan_id in plan_ids])
  seen: set[str] = set()
  for plan_id, raw in zip(plan_ids, states, strict=False):
    if not raw:
      continue
    try:
      payload = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
      # A state that cannot be read may be a live group of any symbol.
      if plan_id in snapshot_groups or not snapshot_groups:
        exposure.unknown_reasons.append(f"runtime_unreadable:{plan_id[:16]}")
      continue
    if not isinstance(payload, dict):
      continue
    if normalize_symbol(_payload_get(payload, "symbol", "Symbol")) != canonical:
      continue
    stage = str(_payload_get(payload, "stage", "Stage") or "")
    group_stage = str(_payload_get(payload, "group_stage", "GroupStage") or "")
    terminal = str(_payload_get(payload, "terminal_reason", "TerminalReason") or "")
    if _is_non_live_trade_plan(stage=stage, group_stage=group_stage, terminal_reason=terminal):
      continue
    pending = stage in _PENDING_TRADE_PLAN_STAGES or group_stage in _PENDING_TRADE_PLAN_GROUP_STAGES
    opened = stage in _OPEN_TRADE_PLAN_STAGES or group_stage in _OPEN_TRADE_PLAN_GROUP_STAGES
    if not pending and not opened:
      continue
    direction = normalize_direction(_payload_get(payload, "direction", "Direction"))
    plan_key = str(_payload_get(payload, "plan_id", "PlanId") or plan_id)
    if plan_key in seen:
      continue  # the same plan listed twice is one group
    seen.add(plan_key)
    setup_id = _setup_id(str(_payload_get(payload, "setup_id", "SetupId") or plan_key))
    if direction is None:
      exposure.unknown_reasons.append(f"runtime_direction_unreadable:{plan_key[:16]}")
      continue
    risk, lots = runtime_group_risk(
      payload,
      pip_size=float(units.pip_size),
      pip_value_per_lot=float(units.pip_value_per_lot),
      volume_units_per_lot=float(units.volume_units_per_lot),
    )
    booked = _as_int(_payload_get(payload, "highest_booked_target_index", "HighestBookedTargetIndex"))
    if booked is not None and booked < 0:
      booked = None
    exposure.groups.append(GroupExposure(
      plan_id=plan_key,
      setup_id=setup_id,
      symbol=canonical,
      direction=direction,
      source="runtime",
      thesis_id=str(_payload_get(payload, "thesis_id", "ThesisId") or "") or None,
      stage=group_stage or stage,
      risk_amount=risk,
      risk_basis="runtime" if risk is not None else "unknown",
      highest_booked_target_index=booked,
      remaining_lots=lots,
    ))
  # A broker group the runtime cannot account for is live exposure of unknown
  # size and direction.
  for group_id in sorted(snapshot_groups - seen):
    exposure.groups.append(GroupExposure(
      plan_id=group_id,
      setup_id=_setup_id(group_id),
      symbol=canonical,
      direction="UNKNOWN",
      source="broker_snapshot",
      stage="unreconciled",
    ))
    exposure.unknown_reasons.append(f"broker_group_without_runtime:{group_id[:16]}")
  return exposure


# ---------------------------------------------------------------------------
# Pure decision
# ---------------------------------------------------------------------------


def _counts_toward_groups(policy: SameDirectionPolicy, group: GroupExposure) -> bool:
  if policy.risk_free_groups_count:
    return True
  return not (group.risk_amount is not None and group.risk_amount <= _EPSILON and group.highest_booked_target_index is not None)


def _same_side(group: GroupExposure, direction: str) -> bool:
  # A group of unknown direction could be on either side: it counts on both.
  return group.direction in (direction, "UNKNOWN")


def evaluate_group_exposure(
  policy: SameDirectionPolicy,
  incoming: IncomingGroup,
  groups: Iterable[GroupExposure],
  *,
  unknown_reasons: Iterable[str] = (),
) -> GroupDecision:
  """Decide whether ``incoming`` may be admitted next to ``groups``.

  ``groups`` are every live and in-flight group of the symbol (runtime plans
  and reservations, already deduplicated by setup). Pure: the same function
  drives live admission accounting and the offline policy replay.
  """
  limits = policy.limits_for(incoming.strategy)
  direction = normalize_direction(incoming.direction) or incoming.direction
  mine = [g for g in groups if g.setup_id != incoming.setup_id]
  same = [g for g in mine if _same_side(g, direction)]
  counted = [g for g in same if _counts_toward_groups(policy, g)]
  unknown_reasons = list(unknown_reasons)

  money = limits.max_cumulative_risk is not None or limits.max_symbol_risk is not None
  unknown_groups = [g for g in mine if g.risk_amount is None] if money else []
  same_risk = sum(g.risk_amount or 0.0 for g in same)
  symbol_risk = sum(g.risk_amount or 0.0 for g in mine)
  incoming_risk = incoming.risk_amount
  measured: dict[str, Any] = {
    "symbol": incoming.symbol,
    "direction": direction,
    "strategy": incoming.strategy,
    "same_direction_groups": len(counted),
    "max_groups": limits.max_groups,
    "same_direction_risk": round(same_risk, 4),
    "max_cumulative_risk": limits.max_cumulative_risk,
    "symbol_risk": round(symbol_risk, 4),
    "max_symbol_risk": limits.max_symbol_risk,
    "incoming_risk": None if incoming_risk is None else round(incoming_risk, 4),
    "existing_plan_ids": [g.plan_id for g in same],
    "existing_sources": [g.source for g in same],
    "existing_risk_bases": [g.risk_basis for g in same],
    "unknown_reasons": unknown_reasons,
  }

  def block(code: str, message: str) -> GroupDecision:
    return GroupDecision(False, code, message, measured)

  if unknown_reasons:
    return block(
      EXPOSURE_UNKNOWN,
      f"{incoming.symbol} exposure cannot be established ({', '.join(unknown_reasons[:3])}); failing closed",
    )
  if money and (incoming_risk is None or unknown_groups):
    which = "incoming group" if incoming_risk is None else f"{len(unknown_groups)} live group(s)"
    return block(
      EXPOSURE_UNKNOWN,
      f"{incoming.symbol} {direction}: risk of the {which} is unknown; failing closed",
    )
  if policy.duplicate_thesis_block:
    for group in same:
      shared = (
        incoming.thesis_id and group.thesis_id == incoming.thesis_id
        or incoming.structural_id and group.structural_id == incoming.structural_id
      )
      if shared:
        return block(
          SAME_THESIS_ACTIVE,
          f"{incoming.symbol} {direction}: group {group.plan_id[:20]} already owns this thesis",
        )
  if limits.max_groups is not None and len(counted) + 1 > limits.max_groups:
    return block(
      SAME_DIRECTION_GROUP_LIMIT,
      f"{incoming.symbol} {direction}: {len(counted)} group(s) already active, limit {limits.max_groups}",
    )
  if limits.max_cumulative_risk is not None and same_risk + (incoming_risk or 0.0) > limits.max_cumulative_risk + _EPSILON:
    return block(
      SAME_DIRECTION_RISK_BUDGET,
      f"{incoming.symbol} {direction}: risk {same_risk:.2f} + {incoming_risk:.2f} exceeds {limits.max_cumulative_risk:.2f}",
    )
  if limits.max_symbol_risk is not None and symbol_risk + (incoming_risk or 0.0) > limits.max_symbol_risk + _EPSILON:
    return block(
      SYMBOL_RISK_BUDGET,
      f"{incoming.symbol}: risk {symbol_risk:.2f} + {incoming_risk:.2f} exceeds {limits.max_symbol_risk:.2f}",
    )
  return GroupDecision(True, None, "", measured)


# ---------------------------------------------------------------------------
# Atomic reservation
# ---------------------------------------------------------------------------


class GroupExposureUnavailable(RuntimeError):
  """The atomic reservation could not run; admission must fail closed."""


def reservation_key(symbol: str, direction: str) -> str:
  return f"autotrade:group_exposure:{normalize_symbol(symbol)}:{normalize_direction(direction)}"


def _encode(expires_at: int, created_at: int, risk: float | None, incoming: IncomingGroup) -> str:
  fields = [
    str(int(expires_at)), str(int(created_at)), "-1" if risk is None else repr(float(risk)),
    incoming.plan_id, incoming.thesis_id or "", incoming.structural_id or "", incoming.strategy,
  ]
  return "|".join(field_.replace("|", "/") for field_ in fields)


def _decode(setup_id: str, raw: str, symbol: str, direction: str) -> GroupExposure | None:
  parts = raw.split("|")
  if len(parts) < 7:
    return None
  try:
    risk = float(parts[2])
  except ValueError:
    risk = -1.0
  return GroupExposure(
    plan_id=parts[3],
    setup_id=setup_id,
    symbol=symbol,
    direction=direction,
    source="reservation",
    thesis_id=parts[4] or None,
    structural_id=parts[5] or None,
    strategy=parts[6] or None,
    stage="reserved",
    risk_amount=None if risk < 0 else risk,
    risk_basis="reservation" if risk >= 0 else "unknown",
  )


# One round trip counts the in-flight reservations together with the runtime
# exposure the caller measured and writes the new reservation, so two workers
# can never both observe a free slot. No cjson: reservations are delimited
# strings in a hash keyed by setup id.
_RESERVE_LUA = """
local now = tonumber(ARGV[1])
local setup_id = ARGV[2]
local new_risk = tonumber(ARGV[3])
local max_groups, max_cum, max_sym = tonumber(ARGV[4]), tonumber(ARGV[5]), tonumber(ARGV[6])
local rt_groups, rt_risk, rt_sym_risk = tonumber(ARGV[7]), tonumber(ARGV[8]), tonumber(ARGV[9])
local rt_unknown_same, rt_unknown_all = tonumber(ARGV[10]), tonumber(ARGV[11])
local known, dead = ARGV[12], ARGV[13]
local thesis, structural, dup_block = ARGV[14], ARGV[15], ARGV[16] == '1'
local rt_thesis, rt_struct = ARGV[17], ARGV[18]
local ttl = tonumber(ARGV[19])
local record = ARGV[20]
local money = (max_cum >= 0) or (max_sym >= 0)

local function parse(value)
  local f = {}
  for part in string.gmatch(value .. '|', '([^|]*)|') do table.insert(f, part) end
  return f
end

local res_groups, res_risk, res_unknown = 0, 0, 0
local sym_risk_extra, sym_unknown_extra = 0, 0
local dup_reason = nil
local function scan(key, same)
  local flat = redis.call('HGETALL', key)
  for i = 1, #flat, 2 do
    local field, value = flat[i], flat[i + 1]
    local f = parse(value)
    local expires = tonumber(f[1]) or 0
    if expires <= now or string.find(known, ',' .. field .. ',', 1, true) or string.find(dead, ',' .. field .. ',', 1, true) then
      redis.call('HDEL', key, field)
    elseif field ~= setup_id then
      local risk = tonumber(f[3]) or -1
      if same then res_groups = res_groups + 1 end
      if same and risk >= 0 then res_risk = res_risk + risk end
      if same and risk < 0 then res_unknown = res_unknown + 1 end
      if risk >= 0 then sym_risk_extra = sym_risk_extra + risk else sym_unknown_extra = sym_unknown_extra + 1 end
      if same and dup_block and not dup_reason then
        if (thesis ~= '' and f[5] == thesis) or (structural ~= '' and f[6] == structural) then dup_reason = field end
      end
    end
  end
end
scan(KEYS[1], true)
scan(KEYS[2], false)

local function result(ok, reason)
  return {ok, reason, tostring(rt_groups + res_groups), tostring(rt_risk + res_risk), tostring(rt_sym_risk + sym_risk_extra + 0)}
end

if money and (new_risk < 0 or rt_unknown_all + res_unknown + sym_unknown_extra > 0) then
  return result(0, 'exposure_unknown')
end
if dup_block then
  if dup_reason or (thesis ~= '' and string.find(rt_thesis, ',' .. thesis .. ',', 1, true))
     or (structural ~= '' and string.find(rt_struct, ',' .. structural .. ',', 1, true)) then
    return result(0, 'same_thesis_active')
  end
end
if max_groups >= 0 and rt_groups + res_groups + 1 > max_groups then
  return result(0, 'same_direction_group_limit')
end
local add = new_risk
if add < 0 then add = 0 end
if max_cum >= 0 and rt_risk + res_risk + add > max_cum + 1e-9 then
  return result(0, 'same_direction_risk_budget')
end
if max_sym >= 0 and rt_sym_risk + sym_risk_extra + add > max_sym + 1e-9 then
  return result(0, 'symbol_risk_budget')
end
redis.call('HSET', KEYS[1], setup_id, record)
redis.call('EXPIRE', KEYS[1], ttl + 60)
return result(1, '')
"""


@dataclass(frozen=True)
class ReservationOutcome:
  admitted: bool
  reason_code: str | None
  groups: float
  same_direction_risk: float
  symbol_risk: float


async def _read_reservations(client: Any, symbol: str) -> dict[str, tuple[str, GroupExposure]]:
  out: dict[str, tuple[str, GroupExposure]] = {}
  for direction in (_BUY, _SELL):
    raw = await client.hgetall(reservation_key(symbol, direction))
    for field_, value in (raw or {}).items():
      field_ = field_.decode() if isinstance(field_, bytes) else str(field_)
      value = value.decode() if isinstance(value, bytes) else str(value)
      group = _decode(field_, value, normalize_symbol(symbol) or symbol, direction)
      if group is not None:
        out[field_] = (value, group)
  return out


_DEAD_ACK_STATES = frozenset({"rejected", "cancelled", "expired", "failed"})


async def _plan_is_dead(client: Any, plan_id: str, created: int | None, now: int) -> bool:
  """True when a reserved plan will never occupy the executor.

  The executor rejected, cancelled or expired it, or it never reached the plan
  store within the grace window. A plan that is merely slow is not dead: its
  reservation keeps counting until its TTL.
  """
  raw = await client.get(f"execution:plan_ack:{plan_id}")
  if raw:
    try:
      ack = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
      state = str(ack.get("state") or ack.get("State") or "").strip().lower()
      if state in _DEAD_ACK_STATES:
        return True
    except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
      pass
  if created is None or now - created <= _DEAD_PLAN_GRACE_SECONDS:
    return False
  return not await client.exists(f"execution:plan:{plan_id}")


async def reserve_group_slot(
  client: Any,
  *,
  policy: SameDirectionPolicy,
  incoming: IncomingGroup,
  exposure: SymbolExposure,
  now: int | None = None,
) -> GroupDecision:
  """Atomically admit ``incoming`` and reserve its slot, or say why not.

  The caller supplies the runtime exposure it just measured; reservations still
  in flight are counted by the script in the same round trip, and reservations
  whose plan is already in the runtime are not counted twice.
  """
  now = int(time.time()) if now is None else int(now)
  direction = normalize_direction(incoming.direction) or incoming.direction
  limits = policy.limits_for(incoming.strategy)
  runtime = [g for g in exposure.groups if g.source != "reservation" and g.setup_id != incoming.setup_id]
  same = [g for g in runtime if _same_side(g, direction)]
  counted = [g for g in same if _counts_toward_groups(policy, g)]
  known = sorted({g.setup_id for g in runtime})

  # Reservations whose plan never reached the stream are dead weight: free them
  # before counting (their TTL is the backstop).
  dead: list[str] = []
  existing = await _read_reservations(client, incoming.symbol)
  for setup_id, (value, group) in existing.items():
    if setup_id in known or setup_id == incoming.setup_id:
      continue
    created = _as_int(value.split("|")[1]) if len(value.split("|")) > 1 else None
    if await _plan_is_dead(client, group.plan_id, created, now):
      dead.append(setup_id)

  args = [
    now, incoming.setup_id, -1 if incoming.risk_amount is None else repr(float(incoming.risk_amount)),
    -1 if limits.max_groups is None else limits.max_groups,
    -1 if limits.max_cumulative_risk is None else repr(float(limits.max_cumulative_risk)),
    -1 if limits.max_symbol_risk is None else repr(float(limits.max_symbol_risk)),
    len(counted),
    repr(sum(g.risk_amount or 0.0 for g in same)),
    repr(sum(g.risk_amount or 0.0 for g in runtime)),
    sum(1 for g in same if g.risk_amount is None),
    sum(1 for g in runtime if g.risk_amount is None),
    "," + ",".join(known) + ",",
    "," + ",".join(dead) + ",",
    incoming.thesis_id or "",
    incoming.structural_id or "",
    "1" if policy.duplicate_thesis_block else "0",
    "," + ",".join(g.thesis_id for g in same if g.thesis_id) + ",",
    "," + ",".join(g.structural_id for g in same if g.structural_id) + ",",
    policy.reservation_ttl_seconds,
    _encode(now + policy.reservation_ttl_seconds, now, incoming.risk_amount, incoming),
  ]
  key_same = reservation_key(incoming.symbol, direction)
  key_other = reservation_key(incoming.symbol, _SELL if direction == _BUY else _BUY)
  try:
    raw = await client.eval(_RESERVE_LUA, 2, key_same, key_other, *args)
  except Exception as exc:
    raise GroupExposureUnavailable("group exposure reservation could not run atomically") from exc
  ok, reason, groups, same_risk, symbol_risk = (
    raw[0], raw[1], raw[2], raw[3], raw[4]
  )
  ok = int(ok) == 1
  reason = reason.decode() if isinstance(reason, bytes) else str(reason or "")
  measured = {
    "symbol": incoming.symbol,
    "direction": direction,
    "strategy": incoming.strategy,
    "same_direction_groups": float(groups),
    "same_direction_risk": float(same_risk),
    "symbol_risk": float(symbol_risk),
    "incoming_risk": incoming.risk_amount,
    "max_groups": limits.max_groups,
    "max_cumulative_risk": limits.max_cumulative_risk,
    "max_symbol_risk": limits.max_symbol_risk,
    "unknown_reasons": list(exposure.unknown_reasons),
  }
  if ok:
    return GroupDecision(True, None, "", measured)
  return GroupDecision(
    False, reason or EXPOSURE_UNKNOWN,
    f"{incoming.symbol} {direction}: admission refused ({reason})", measured,
  )


async def release_group_slot(client: Any, *, symbol: str, direction: str, setup_id: str) -> None:
  """Drop a reservation whose plan never reached the executor."""
  try:
    await client.hdel(reservation_key(symbol, direction), setup_id)
  except Exception:
    log.exception("group exposure release failed symbol=%s setup_id=%s", symbol, setup_id)


async def admit_group(
  client: Any,
  *,
  policy: SameDirectionPolicy,
  incoming: IncomingGroup,
  units: Any,
  now: int | None = None,
) -> GroupDecision:
  """Measure the symbol's exposure and atomically admit ``incoming`` or refuse it.

  Unknown exposure refuses before any reservation is attempted. Raises
  ``GroupExposureUnavailable`` when Redis cannot run the reservation: the caller
  must treat that as a refusal, never as permission.
  """
  exposure = await load_symbol_exposure(
    client, symbol=incoming.symbol, units=units, policy=policy,
    now=None if now is None else float(now),
  )
  if exposure.unknown_reasons:
    decision = evaluate_group_exposure(
      policy, incoming, exposure.groups, unknown_reasons=exposure.unknown_reasons,
    )
    return decision
  return await reserve_group_slot(
    client, policy=policy, incoming=incoming, exposure=exposure, now=now,
  )

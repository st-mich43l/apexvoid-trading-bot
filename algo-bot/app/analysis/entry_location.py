"""Typed entry-location decision (premium/discount execution authority)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.autotrade.execution_policy import (
  FAMILY_BREAKOUT_RETEST,
  FAMILY_LIQUIDITY_REVERSAL,
  FAMILY_MAPPED_ZONE_REACTION,
  FAMILY_RANGE_REVERSION,
  FAMILY_TREND_PULLBACK,
  strategy_family,
)
from app.autotrade.strategy_taxonomy import (
  is_liquidity_strategy,
  is_range_strategy,
  is_reaction_strategy,
  is_technique_or_confluence,
  is_zone_strategy,
)


ARCHETYPE_REVERSAL = "reversal"
ARCHETYPE_RANGE_REVERSION = "range_reversion"
ARCHETYPE_TREND_PULLBACK = "trend_pullback"
ARCHETYPE_BREAKOUT_RETEST = "breakout_retest"
ARCHETYPE_MOMENTUM = "momentum"
ARCHETYPE_UNKNOWN = "unknown"

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"


@dataclass(frozen=True)
class EntryLocationContext:
  execution_price: float

  h1_range_low: float | None
  h1_range_high: float | None
  h1_range_position: float | None

  m15_range_low: float | None
  m15_range_high: float | None
  m15_range_position: float | None

  m5_range_low: float | None
  m5_range_high: float | None
  m5_range_position: float | None

  effective_range_low: float | None
  effective_range_high: float | None
  effective_range_position: float | None
  effective_range_position_raw: float | None
  effective_range_source: str | None

  zone_position: float | None
  range_available: bool


@dataclass(frozen=True)
class EntryLocationDecision:
  allowed: bool
  reason_code: str
  hard_block: bool
  archetype: str
  would_block: bool
  measured: dict[str, Any]


def range_position(price: float, low: float, high: float) -> float | None:
  """Raw dealing-range position. Does not clamp outside 0..1."""
  span = float(high) - float(low)
  if span <= 0:
    return None
  return (float(price) - float(low)) / span


def clamped_position(raw: float | None) -> float | None:
  if raw is None:
    return None
  return min(1.0, max(0.0, float(raw)))


def location_archetype(strategy: str) -> str:
  name = str(strategy or "")
  family = strategy_family(name)
  if family == FAMILY_RANGE_REVERSION or is_range_strategy(name):
    return ARCHETYPE_RANGE_REVERSION
  if family == FAMILY_TREND_PULLBACK or name == "Trend Pullback":
    return ARCHETYPE_TREND_PULLBACK
  if family == FAMILY_BREAKOUT_RETEST or name in {"Break & Retest", "Box Breakout"}:
    return ARCHETYPE_BREAKOUT_RETEST
  if family in {
    FAMILY_LIQUIDITY_REVERSAL,
    FAMILY_MAPPED_ZONE_REACTION,
  } or is_liquidity_strategy(name) or is_reaction_strategy(name) or is_zone_strategy(name):
    return ARCHETYPE_REVERSAL
  if name in {"Breakout Continuation", "Momentum Ride"}:
    return ARCHETYPE_MOMENTUM
  # Demand/Supply legacy display names land as zone family above.
  if "Reaction" in name or "Fade" in name:
    return ARCHETYPE_REVERSAL
  return ARCHETYPE_UNKNOWN


def _valid_bounds(low: float | None, high: float | None) -> bool:
  if low is None or high is None:
    return False
  return float(high) > float(low)


def _brackets(price: float, low: float, high: float) -> bool:
  return float(low) <= float(price) <= float(high)


def select_effective_range(
  *,
  price: float,
  h1_low: float | None,
  h1_high: float | None,
  m15_low: float | None,
  m15_high: float | None,
  m5_low: float | None,
  m5_high: float | None,
) -> tuple[str | None, float | None, float | None]:
  """Prefer M15 → H1 → M5 among ranges that bracket price, else first valid."""
  candidates: list[tuple[str, float, float]] = []
  for source, low, high in (
    ("M15", m15_low, m15_high),
    ("H1", h1_low, h1_high),
    ("M5", m5_low, m5_high),
  ):
    if _valid_bounds(low, high):
      candidates.append((source, float(low), float(high)))
  if not candidates:
    return None, None, None
  for source, low, high in candidates:
    if _brackets(price, low, high):
      return source, low, high
  source, low, high = candidates[0]
  return source, low, high


def build_entry_location_context(
  *,
  execution_price: float,
  direction: str,
  ask: float | None = None,
  bid: float | None = None,
  h1_range_low: float | None = None,
  h1_range_high: float | None = None,
  m15_range_low: float | None = None,
  m15_range_high: float | None = None,
  m5_range_low: float | None = None,
  m5_range_high: float | None = None,
  zone_low: float | None = None,
  zone_high: float | None = None,
) -> EntryLocationContext:
  """Build context using ask for BUY and bid for SELL when quotes exist."""
  side = str(direction or "").upper()
  price = float(execution_price)
  if side == "BUY" and ask is not None:
    price = float(ask)
  elif side == "SELL" and bid is not None:
    price = float(bid)

  def _pos(low: float | None, high: float | None) -> float | None:
    if not _valid_bounds(low, high):
      return None
    return range_position(price, float(low), float(high))

  source, eff_low, eff_high = select_effective_range(
    price=price,
    h1_low=h1_range_low,
    h1_high=h1_range_high,
    m15_low=m15_range_low,
    m15_high=m15_range_high,
    m5_low=m5_range_low,
    m5_high=m5_range_high,
  )
  raw = None if eff_low is None else range_position(price, eff_low, eff_high)
  zone_pos = None
  if _valid_bounds(zone_low, zone_high):
    zone_pos = range_position(price, float(zone_low), float(zone_high))

  return EntryLocationContext(
    execution_price=price,
    h1_range_low=h1_range_low,
    h1_range_high=h1_range_high,
    h1_range_position=_pos(h1_range_low, h1_range_high),
    m15_range_low=m15_range_low,
    m15_range_high=m15_range_high,
    m15_range_position=_pos(m15_range_low, m15_range_high),
    m5_range_low=m5_range_low,
    m5_range_high=m5_range_high,
    m5_range_position=_pos(m5_range_low, m5_range_high),
    effective_range_low=eff_low,
    effective_range_high=eff_high,
    effective_range_position=clamped_position(raw),
    effective_range_position_raw=raw,
    effective_range_source=source,
    zone_position=zone_pos,
    range_available=source is not None and raw is not None,
  )


def _cfg_section(cfg: Any, *path: str) -> Any:
  cursor = cfg
  for part in path:
    if cursor is None:
      return None
    cursor = getattr(cursor, part, None)
  return cursor


def _mode(cfg: Any) -> str:
  section = _cfg_section(cfg, "actionability", "entry_location")
  if section is None:
    return MODE_OFF
  mode = str(getattr(section, "mode", MODE_OFF) or MODE_OFF).strip().lower()
  return mode if mode in {MODE_OFF, MODE_SHADOW, MODE_ENFORCE} else MODE_OFF


def _float_cfg(section: Any, name: str, default: float) -> float:
  if section is None:
    return default
  value = getattr(section, name, default)
  try:
    return float(value)
  except (TypeError, ValueError):
    return default


def _has_accepted_breakout_evidence(evidence: Mapping[str, Any] | None) -> bool:
  if not evidence:
    return False
  return bool(
    evidence.get("accepted_break")
    and evidence.get("correct_key_level_role")
    and evidence.get("retest_of_broken_level")
    and evidence.get("directionally_valid_close")
    and evidence.get("target_room_beyond_breakout")
  )


def evaluate_entry_location(
  *,
  strategy: str,
  direction: str,
  context: EntryLocationContext,
  cfg: Any | None = None,
  breakout_evidence: Mapping[str, Any] | None = None,
) -> EntryLocationDecision:
  """Pure location eligibility. Shadow never hard-blocks execution."""
  if cfg is None:
    from app.core.config import runtime_config
    cfg = runtime_config

  mode = _mode(cfg)
  archetype = location_archetype(strategy)
  measured: dict[str, Any] = {
    "mode": mode,
    "archetype": archetype,
    "execution_price": context.execution_price,
    "effective_range_source": context.effective_range_source,
    "effective_range_low": context.effective_range_low,
    "effective_range_high": context.effective_range_high,
    "effective_range_position": context.effective_range_position,
    "effective_range_position_raw": context.effective_range_position_raw,
    "h1_range_position": context.h1_range_position,
    "m15_range_position": context.m15_range_position,
    "m5_range_position": context.m5_range_position,
    "zone_position": context.zone_position,
    "direction": str(direction or "").upper(),
  }

  if mode == MODE_OFF:
    return EntryLocationDecision(
      allowed=True,
      reason_code="entry_location_off",
      hard_block=False,
      archetype=archetype,
      would_block=False,
      measured=measured,
    )

  if (
    archetype == ARCHETYPE_BREAKOUT_RETEST
    and _has_accepted_breakout_evidence(breakout_evidence)
  ):
    measured["location_override"] = "accepted_breakout_retest"
    return EntryLocationDecision(
      allowed=True,
      reason_code="location_override_accepted_breakout_retest",
      hard_block=False,
      archetype=archetype,
      would_block=False,
      measured=measured,
    )

  if not context.range_available or context.effective_range_position_raw is None:
    missing_policy = str(
      getattr(
        _cfg_section(cfg, "actionability", "entry_location"),
        "missing_context_policy",
        "block",
      )
      or "block"
    ).strip().lower()
    measured["missing_context_policy"] = missing_policy
    if mode == MODE_SHADOW or missing_policy != "block":
      return EntryLocationDecision(
        allowed=True,
        reason_code="entry_location_context_missing",
        hard_block=False,
        archetype=archetype,
        would_block=True,
        measured=measured,
      )
    # enforce + block
    if archetype in {
      ARCHETYPE_REVERSAL,
      ARCHETYPE_RANGE_REVERSION,
      ARCHETYPE_TREND_PULLBACK,
    }:
      return EntryLocationDecision(
        allowed=False,
        reason_code="entry_location_context_missing",
        hard_block=True,
        archetype=archetype,
        would_block=True,
        measured=measured,
      )
    return EntryLocationDecision(
      allowed=True,
      reason_code="entry_location_context_missing",
      hard_block=False,
      archetype=archetype,
      would_block=False,
      measured=measured,
    )

  pos = float(context.effective_range_position_raw)
  side = str(direction or "").upper()
  root = _cfg_section(cfg, "actionability", "entry_location")
  reason = "entry_location_allowed"
  would_block = False

  if archetype == ARCHETYPE_RANGE_REVERSION:
    section = getattr(root, "range_reversion", None) if root else None
    buy_max = _float_cfg(section, "buy_maximum_position", 0.40)
    sell_min = _float_cfg(section, "sell_minimum_position", 0.60)
    eq_width = _float_cfg(section, "equilibrium_exclusion_width", 0.20)
    half = max(0.0, eq_width) / 2.0
    if abs(pos - 0.5) <= half:
      reason = "range_entry_near_equilibrium"
      would_block = True
    elif side == "BUY" and pos > buy_max:
      reason = "range_buy_not_at_discount_edge"
      would_block = True
    elif side == "SELL" and pos < sell_min:
      reason = "range_sell_not_at_premium_edge"
      would_block = True

  elif archetype == ARCHETYPE_TREND_PULLBACK:
    section = getattr(root, "trend_pullback", None) if root else None
    buy_max = _float_cfg(section, "buy_maximum_position", 0.70)
    sell_min = _float_cfg(section, "sell_minimum_position", 0.30)
    if side == "BUY" and pos > buy_max:
      reason = "buy_at_range_extreme"
      would_block = True
    elif side == "SELL" and pos < sell_min:
      reason = "sell_at_range_extreme"
      would_block = True

  elif archetype in {ARCHETYPE_REVERSAL, ARCHETYPE_UNKNOWN}:
    section = getattr(root, "reversal", None) if root else None
    buy_max = _float_cfg(section, "buy_maximum_position", 0.50)
    sell_min = _float_cfg(section, "sell_minimum_position", 0.50)
    extreme_buy = _float_cfg(section, "extreme_buy_block_position", 0.65)
    extreme_sell = _float_cfg(section, "extreme_sell_block_position", 0.35)
    # Techniques / confluence often form at dealing-range extremes by
    # design — allow the extreme band; keep mid-range premium/discount.
    skip_extremes = is_technique_or_confluence(strategy)
    if side == "BUY":
      if pos >= extreme_buy:
        if not skip_extremes:
          reason = "buy_at_range_extreme"
          would_block = True
      elif pos > buy_max:
        reason = "buy_in_premium"
        would_block = True
    elif side == "SELL":
      if pos <= extreme_sell:
        if not skip_extremes:
          reason = "sell_at_range_extreme"
          would_block = True
      elif pos < sell_min:
        reason = "sell_in_discount"
        would_block = True

  elif archetype == ARCHETYPE_BREAKOUT_RETEST:
    # Without explicit accepted-breakout evidence, treat as reverse location.
    section = getattr(root, "reversal", None) if root else None
    buy_max = _float_cfg(section, "buy_maximum_position", 0.50)
    sell_min = _float_cfg(section, "sell_minimum_position", 0.50)
    if side == "BUY" and pos > buy_max:
      reason = "buy_in_premium"
      would_block = True
    elif side == "SELL" and pos < sell_min:
      reason = "sell_in_discount"
      would_block = True

  measured["would_block"] = would_block
  measured["reason_code"] = reason

  if not would_block:
    return EntryLocationDecision(
      allowed=True,
      reason_code=reason,
      hard_block=False,
      archetype=archetype,
      would_block=False,
      measured=measured,
    )

  if mode == MODE_SHADOW:
    return EntryLocationDecision(
      allowed=True,
      reason_code=reason,
      hard_block=False,
      archetype=archetype,
      would_block=True,
      measured=measured,
    )

  return EntryLocationDecision(
    allowed=False,
    reason_code=reason,
    hard_block=True,
    archetype=archetype,
    would_block=True,
    measured=measured,
  )

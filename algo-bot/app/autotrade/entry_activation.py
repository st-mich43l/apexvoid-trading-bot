"""Archetype-aware zone-entry activation (reaction trigger gating)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.analysis.entry_location import EntryLocationDecision
from app.analysis.m1_trigger import M1TriggerResult
from app.autotrade.execution_policy import (
  FAMILY_BREAKOUT_RETEST,
  FAMILY_LIQUIDITY_REVERSAL,
  FAMILY_MAPPED_ZONE_REACTION,
  FAMILY_MOMENTUM_CONTINUATION,
  FAMILY_RANGE_REVERSION,
  FAMILY_TREND_PULLBACK,
  strategy_family,
)
from app.autotrade.strategy_taxonomy import (
  is_liquidity_strategy,
  is_range_strategy,
  is_reaction_strategy,
  is_zone_strategy,
)


ACTIVATION_REACTION = "reaction_reversal"
ACTIVATION_BREAKOUT_RETEST = "breakout_retest"
ACTIVATION_TREND_PULLBACK = "trend_pullback"
ACTIVATION_MOMENTUM = "momentum_continuation"
ACTIVATION_UNKNOWN = "unknown"

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"

_SECONDS_PER_M1_BAR = 60


@dataclass(frozen=True)
class EntryActivationDecision:
  allowed: bool
  reason_code: str
  hard_block: bool
  requires_trigger: bool
  trigger_type: str | None
  would_block: bool
  measured: dict[str, Any]


def activation_archetype(strategy: str) -> str:
  name = str(strategy or "")
  family = strategy_family(name)
  if family == FAMILY_BREAKOUT_RETEST or name in {"Break & Retest", "Box Breakout"}:
    return ACTIVATION_BREAKOUT_RETEST
  if family == FAMILY_TREND_PULLBACK or name == "Trend Pullback":
    return ACTIVATION_TREND_PULLBACK
  if family == FAMILY_MOMENTUM_CONTINUATION or name in {
    "Breakout Continuation",
    "Momentum Ride",
  }:
    return ACTIVATION_MOMENTUM
  if (
    family in {
      FAMILY_RANGE_REVERSION,
      FAMILY_LIQUIDITY_REVERSAL,
      FAMILY_MAPPED_ZONE_REACTION,
    }
    or is_range_strategy(name)
    or is_liquidity_strategy(name)
    or is_reaction_strategy(name)
    or is_zone_strategy(name)
    or "Reaction" in name
    or "Fade" in name
  ):
    return ACTIVATION_REACTION
  return ACTIVATION_UNKNOWN


def _mode(cfg: Any) -> str:
  section = getattr(getattr(cfg, "execution", None), "activation", None)
  if section is None:
    return MODE_OFF
  mode = str(getattr(section, "mode", MODE_OFF) or MODE_OFF).strip().lower()
  return mode if mode in {MODE_OFF, MODE_SHADOW, MODE_ENFORCE} else MODE_OFF


def _max_age_bars(cfg: Any) -> int:
  section = getattr(getattr(cfg, "execution", None), "activation", None)
  try:
    return max(1, int(getattr(section, "reaction_trigger_maximum_age_bars", 2) or 2))
  except (TypeError, ValueError):
    return 2


def _trigger_ts(trigger: M1TriggerResult | None) -> int | None:
  if trigger is None:
    return None
  try:
    return int(trigger.bar_ts)
  except (TypeError, ValueError):
    return None


def evaluate_entry_activation(
  *,
  strategy: str,
  direction: str,
  zone_entered_at: int | None,
  quote_inside: bool,
  decisive_break: bool,
  trigger: M1TriggerResult | None,
  location_decision: EntryLocationDecision,
  now: int,
  cfg: Any | None = None,
  breakout_evidence: Mapping[str, Any] | None = None,
  continuation_evidence: Mapping[str, Any] | None = None,
) -> EntryActivationDecision:
  """Pure activation decision. Does not touch Redis.

  In shadow/off modes, reaction strategies that would wait on a missing trigger
  are reported via would_block but still allowed so production behaviour stays
  unchanged until enforce is enabled.
  """
  if cfg is None:
    from app.core.config import runtime_config
    cfg = runtime_config

  mode = _mode(cfg)
  archetype = activation_archetype(strategy)
  requires_trigger = archetype == ACTIVATION_REACTION
  measured: dict[str, Any] = {
    "mode": mode,
    "archetype": archetype,
    "strategy": strategy,
    "direction": str(direction or "").upper(),
    "quote_inside": bool(quote_inside),
    "zone_entered_at": zone_entered_at,
    "trigger": None if trigger is None else str(trigger.pattern),
    "trigger_bar_ts": _trigger_ts(trigger),
    "location_reason": location_decision.reason_code,
    "location_allowed": location_decision.allowed,
    "now": int(now),
  }

  def _result(
    *,
    reason: str,
    would_block: bool,
    hard: bool = False,
  ) -> EntryActivationDecision:
    measured["reason_code"] = reason
    measured["would_block"] = would_block
    if mode == MODE_OFF:
      return EntryActivationDecision(
        allowed=True,
        reason_code="entry_activation_off",
        hard_block=False,
        requires_trigger=requires_trigger,
        trigger_type=None if trigger is None else str(trigger.pattern),
        would_block=False,
        measured=measured,
      )
    if mode == MODE_SHADOW or not would_block:
      return EntryActivationDecision(
        allowed=True,
        reason_code=reason if would_block or reason else "entry_activation_allowed",
        hard_block=False,
        requires_trigger=requires_trigger,
        trigger_type=None if trigger is None else str(trigger.pattern),
        would_block=would_block,
        measured=measured,
      )
    return EntryActivationDecision(
      allowed=False,
      reason_code=reason,
      hard_block=hard or True,
      requires_trigger=requires_trigger,
      trigger_type=None if trigger is None else str(trigger.pattern),
      would_block=True,
      measured=measured,
    )

  if decisive_break:
    return _result(reason="zone_decisively_broken", would_block=True, hard=True)

  if not location_decision.allowed:
    return _result(
      reason=location_decision.reason_code or "entry_location_blocked",
      would_block=True,
      hard=location_decision.hard_block,
    )

  if not quote_inside:
    return _result(reason="quote_outside_zone", would_block=True, hard=True)

  if archetype == ACTIVATION_BREAKOUT_RETEST:
    evidence = breakout_evidence or {}
    accepted = bool(
      evidence.get("accepted_break")
      and evidence.get("retest_of_broken_level")
      and evidence.get("directionally_valid_close")
    )
    if not accepted:
      return _result(
        reason="breakout_retest_evidence_missing",
        would_block=True,
        hard=True,
      )
    return _result(reason="entry_activation_allowed", would_block=False)

  if archetype == ACTIVATION_TREND_PULLBACK:
    evidence = continuation_evidence or {}
    if not bool(evidence.get("pullback_continuation")):
      # Without explicit continuation payloads yet, do not invent a block in
      # shadow/off; enforce still requires evidence when provided path is used.
      if mode == MODE_ENFORCE and continuation_evidence is not None:
        return _result(
          reason="trend_pullback_evidence_missing",
          would_block=True,
          hard=True,
        )
    return _result(reason="entry_activation_allowed", would_block=False)

  if archetype == ACTIVATION_MOMENTUM:
    # Momentum must not reuse a reversal trigger pattern alone.
    if trigger is not None and str(trigger.pattern) in {
      "wick_rejection",
      "hammer",
      "pin_bar",
    }:
      return _result(
        reason="momentum_cannot_reuse_reversal_trigger",
        would_block=True,
        hard=True,
      )
    evidence = continuation_evidence or {}
    if mode == MODE_ENFORCE and continuation_evidence is not None:
      if not bool(evidence.get("continuation")):
        return _result(
          reason="momentum_continuation_evidence_missing",
          would_block=True,
          hard=True,
        )
    return _result(reason="entry_activation_allowed", would_block=False)

  # Reaction / reversal / range / mapped / liquidity — require fresh M1.
  if requires_trigger:
    if trigger is None:
      return _result(reason="reaction_trigger_missing", would_block=True, hard=True)

    bar_ts = _trigger_ts(trigger)
    if bar_ts is None:
      return _result(reason="reaction_trigger_missing", would_block=True, hard=True)

    if zone_entered_at is not None and bar_ts <= int(zone_entered_at):
      return _result(
        reason="reaction_trigger_before_zone_touch",
        would_block=True,
        hard=True,
      )

    max_age = _max_age_bars(cfg)
    age_seconds = int(now) - bar_ts
    if age_seconds > max_age * _SECONDS_PER_M1_BAR:
      return _result(reason="reaction_trigger_stale", would_block=True, hard=True)

    if str(trigger.direction).upper() != str(direction).upper():
      return _result(
        reason="reaction_trigger_wrong_direction",
        would_block=True,
        hard=True,
      )

  return _result(reason="entry_activation_allowed", would_block=False)


def apply_trigger_to_match(match: Any, trigger: M1TriggerResult | None) -> Any:
  """Stamp optional activation fields onto a StrategyMatch."""
  from dataclasses import replace

  if trigger is None:
    return match
  kwargs: dict[str, Any] = {
    "confirmation_bar_ts": str(int(trigger.bar_ts)),
    "reaction_type": str(trigger.pattern),
    "entry_activation_trigger": str(trigger.pattern),
    "entry_activation_trigger_ts": str(int(trigger.bar_ts)),
  }
  entered = getattr(match, "touch_bar_ts", None)
  if not entered:
    kwargs["touch_bar_ts"] = str(int(trigger.bar_ts))
  return replace(match, **kwargs)

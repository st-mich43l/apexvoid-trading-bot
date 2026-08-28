"""Archetype-aware zone-entry activation (reaction trigger gating)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import logging

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
  is_technique_or_confluence,
  is_zone_strategy,
)


log = logging.getLogger(__name__)


ACTIVATION_REACTION = "reaction_reversal"
ACTIVATION_BREAKOUT_RETEST = "breakout_retest"
ACTIVATION_TREND_PULLBACK = "trend_pullback"
ACTIVATION_MOMENTUM = "momentum_continuation"
ACTIVATION_UNKNOWN = "unknown"

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"

_SECONDS_PER_M1_BAR = 60
_SECONDS_PER_M5_BAR = 300

M5_FALLBACK_OFF = "off"
M5_FALLBACK_QUOTE_INSIDE = "quote_inside"
M5_FALLBACK_QUOTE_INSIDE_FRESH = "quote_inside_fresh"


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


def _m5_fallback_mode(cfg: Any) -> str:
  section = getattr(getattr(cfg, "execution", None), "activation", None)
  if section is None:
    return M5_FALLBACK_OFF
  mode = str(
    getattr(section, "m5_authoritative_fallback", M5_FALLBACK_OFF)
    or M5_FALLBACK_OFF
  ).strip().lower()
  allowed = {
    M5_FALLBACK_OFF,
    M5_FALLBACK_QUOTE_INSIDE,
    M5_FALLBACK_QUOTE_INSIDE_FRESH,
  }
  return mode if mode in allowed else M5_FALLBACK_OFF


def _m5_max_age_bars(cfg: Any) -> int:
  section = getattr(getattr(cfg, "execution", None), "activation", None)
  try:
    return max(
      1,
      int(getattr(section, "m5_confirmation_maximum_age_bars", 6) or 6),
    )
  except (TypeError, ValueError):
    return 6


def _parse_confirmation_ts(value: Any) -> int | None:
  if value is None:
    return None
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def _trigger_ts(trigger: M1TriggerResult | None) -> int | None:
  if trigger is None:
    return None
  try:
    return int(trigger.bar_ts)
  except (TypeError, ValueError):
    return None


def detect_impulse_against(
  *,
  direction: str,
  bars: list[Mapping[str, Any]] | None,
  zone_low: float | None = None,
  zone_high: float | None = None,
  lookback: int = 5,
) -> bool:
  """True when recent M1 is expanding through the zone against the thesis."""
  if not bars or len(bars) < 3:
    return False
  chunk = list(bars)[-max(3, lookback):]
  try:
    closes = [float(bar.get("c", bar.get("close"))) for bar in chunk]
    highs = [float(bar.get("h", bar.get("high"))) for bar in chunk]
    lows = [float(bar.get("l", bar.get("low"))) for bar in chunk]
  except (TypeError, ValueError):
    return False
  side = str(direction or "").upper()
  if side == "SELL":
    expanding = highs[-1] > highs[0] and closes[-1] > closes[0]
    up_closes = sum(
      1 for idx in range(1, len(closes)) if closes[idx] >= closes[idx - 1]
    )
    through = zone_low is None or closes[-1] >= float(zone_low)
    return expanding and up_closes >= 2 and through
  if side == "BUY":
    expanding = lows[-1] < lows[0] and closes[-1] < closes[0]
    down_closes = sum(
      1 for idx in range(1, len(closes)) if closes[idx] <= closes[idx - 1]
    )
    through = zone_high is None or closes[-1] <= float(zone_high)
    return expanding and down_closes >= 2 and through
  return False


def detect_expanding_up(
  bars: list[Mapping[str, Any]] | None,
  *,
  lookback: int = 5,
) -> bool:
  """True when last M1 bars are making HH with rising closes (#53 / Key-BUY-into-bid)."""
  if not bars or len(bars) < 3:
    return False
  chunk = list(bars)[-max(3, lookback):]
  try:
    closes = [float(bar.get("c", bar.get("close"))) for bar in chunk]
    highs = [float(bar.get("h", bar.get("high"))) for bar in chunk]
  except (TypeError, ValueError):
    return False
  expanding = highs[-1] > highs[0] and closes[-1] > closes[0]
  up_closes = sum(
    1 for idx in range(1, len(closes)) if closes[idx] >= closes[idx - 1]
  )
  return expanding and up_closes >= 2


def demand_swept_and_reclaimed(
  *,
  bars: list[Mapping[str, Any]] | None,
  zone_low: float | None,
  zone_high: float | None,
) -> bool:
  """Demand BUY: wick through/at zone low, last close back in the box (#42)."""
  if not bars or zone_low is None or zone_high is None:
    return False
  low = float(zone_low)
  high = float(zone_high)
  try:
    lows = [float(bar.get("l", bar.get("low"))) for bar in bars]
    close = float(bars[-1].get("c", bars[-1].get("close")))
  except (TypeError, ValueError):
    return False
  swept = any(value <= low + 0.05 for value in lows)
  return swept and low <= close <= high + 0.15


def sell_quote_is_proximal(
  *,
  price: float | None,
  zone_low: float | None,
  zone_high: float | None,
) -> bool:
  """Key/FVG SELL fills at the cheap half of the box (#44/#46/#49/#51/#39)."""
  if price is None or zone_low is None or zone_high is None:
    return True
  low = float(zone_low)
  high = float(zone_high)
  if high <= low:
    return True
  mid = low + (high - low) / 2.0
  return float(price) <= mid + 0.02


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
  chase_pips: float | None = None,
  maximum_chase_pips: float | None = None,
  m5_authoritative: bool = False,
  m5_confirmation_bar_ts: str | int | None = None,
  impulse_bars: list[Mapping[str, Any]] | None = None,
  zone_low: float | None = None,
  zone_high: float | None = None,
  execution_price: float | None = None,
) -> EntryActivationDecision:
  """Pure activation decision. Does not touch Redis.

  In shadow/off modes, reaction strategies that would wait on a missing trigger
  are reported via would_block but still allowed so production behaviour stays
  unchanged until enforce is enabled.

  Under technique.enforce, reaction families require a fresh post-tap M1
  trigger unless ``execution.activation.m5_authoritative_fallback`` allows
  M5 structural confirmation in-zone (quote_inside or quote_inside_fresh).
  Instruments may keep sweep/body confirmation as a hard requirement
  or soften it to a quality preference.
  """
  if cfg is None:
    from app.core.config import runtime_config
    cfg = runtime_config

  mode = _mode(cfg)
  archetype = activation_archetype(strategy)
  requires_trigger = archetype == ACTIVATION_REACTION
  chase_ok = False
  try:
    chase_value = None if chase_pips is None else float(chase_pips)
  except (TypeError, ValueError):
    chase_value = None
  try:
    chase_cap = (
      None if maximum_chase_pips is None else float(maximum_chase_pips)
    )
  except (TypeError, ValueError):
    chase_cap = None
  if (
    is_range_strategy(strategy)
    and chase_value is not None
    and chase_cap is not None
    and chase_value > 0
    and chase_value <= chase_cap + 1e-9
  ):
    chase_ok = True
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
    "chase_pips": chase_value,
    "maximum_chase_pips": chase_cap,
    "chase_entry": chase_ok,
    "m5_authoritative": bool(m5_authoritative),
    "m5_confirmation_bar_ts": m5_confirmation_bar_ts,
    "m5_authoritative_fallback": _m5_fallback_mode(cfg),
    "impulse_against": False,
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

  if not quote_inside and not chase_ok:
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

  # Reaction / reversal / range / mapped / liquidity — prefer fresh M1;
  # optionally fall back to in-zone M5-authoritative confirmation when M1 fails.
  from app.autotrade.killzone import (
    confirmation_is_sweep_body,
    technique_enforce,
    technique_require_sweep_body,
  )

  if requires_trigger:
    side = str(direction or "").upper()
    name = str(strategy or "")

    if (
      technique_enforce(cfg)
      and side == "BUY"
      and (is_zone_strategy(name) or "Demand" in name)
      and not is_reaction_strategy(name)
    ):
      pattern = "" if trigger is None else str(trigger.pattern or "")
      if pattern not in {"sweep_reclaim", "strong_reclaim"} and not demand_swept_and_reclaimed(
        bars=impulse_bars,
        zone_low=zone_low,
        zone_high=zone_high,
      ):
        return _result(
          reason="demand_requires_sweep_reclaim",
          would_block=True,
          hard=True,
        )

    if (
      technique_enforce(cfg)
      and side == "SELL"
      and (
        is_reaction_strategy(name)
        or is_technique_or_confluence(name)
      )
      and not sell_quote_is_proximal(
        price=execution_price,
        zone_low=zone_low,
        zone_high=zone_high,
      )
    ):
      return _result(
        reason="sell_not_proximal",
        would_block=True,
        hard=True,
      )

    if (
      technique_enforce(cfg)
      and side == "BUY"
      and is_reaction_strategy(name)
      and detect_expanding_up(impulse_bars)
    ):
      measured["impulse_against"] = True
      return _result(
        reason="key_buy_into_impulse",
        would_block=True,
        hard=True,
      )

    if (
      technique_enforce(cfg)
      and detect_impulse_against(
        direction=direction,
        bars=impulse_bars,
        zone_low=zone_low,
        zone_high=zone_high,
      )
    ):
      measured["impulse_against"] = True
      return _result(
        reason="impulse_against_block",
        would_block=True,
        hard=True,
      )

    m1_fail_reason: str | None = None
    if trigger is None:
      m1_fail_reason = "reaction_trigger_missing"
    else:
      bar_ts = _trigger_ts(trigger)
      if bar_ts is None:
        m1_fail_reason = "reaction_trigger_missing"
      elif zone_entered_at is not None and bar_ts <= int(zone_entered_at):
        m1_fail_reason = "reaction_trigger_before_zone_touch"
      else:
        max_age = _max_age_bars(cfg)
        age_seconds = int(now) - bar_ts
        if age_seconds > max_age * _SECONDS_PER_M1_BAR:
          m1_fail_reason = "reaction_trigger_stale"
        elif str(trigger.direction).upper() != str(direction).upper():
          m1_fail_reason = "reaction_trigger_wrong_direction"
        else:
          pattern = str(trigger.pattern or "")
          sweep_body_required = technique_require_sweep_body(cfg)
          sweep_body_confirmed = confirmation_is_sweep_body(pattern)
          measured.update({
            "sweep_body_required": sweep_body_required,
            "sweep_body_confirmed": sweep_body_confirmed,
          })
          if sweep_body_required and not sweep_body_confirmed:
            m1_fail_reason = "confirmation_requires_sweep_body"
          elif not sweep_body_required and not sweep_body_confirmed:
            measured.update({
              "sweep_body_soft_miss": True,
              "preference_telemetry": True,
              "preference_reason_code": "confirmation_without_sweep_body",
            })

    if m1_fail_reason is None:
      return _result(reason="entry_activation_allowed", would_block=False)

    fallback = _m5_fallback_mode(cfg)
    m5_ts = _parse_confirmation_ts(m5_confirmation_bar_ts)
    measured["m5_fallback_mode"] = fallback
    if (
      fallback != M5_FALLBACK_OFF
      and bool(m5_authoritative)
      and bool(quote_inside)
    ):
      fresh_ok = True
      if fallback == M5_FALLBACK_QUOTE_INSIDE_FRESH:
        if m5_ts is None:
          fresh_ok = False
        else:
          max_m5_age = _m5_max_age_bars(cfg)
          m5_age_seconds = int(now) - m5_ts
          fresh_ok = m5_age_seconds <= max_m5_age * _SECONDS_PER_M5_BAR
          measured["m5_confirmation_age_seconds"] = m5_age_seconds
          measured["m5_confirmation_max_age_bars"] = max_m5_age
      if fresh_ok:
        measured["m1_fallback_reason"] = m1_fail_reason
        return _result(
          reason="reaction_m5_authoritative_in_zone",
          would_block=False,
        )

    return _result(reason=m1_fail_reason, would_block=True, hard=True)

  return _result(reason="entry_activation_allowed", would_block=False)


ACTIVATION_BLOCK_REASON_CODES = frozenset({
  "zone_decisively_broken",
  "entry_location_context_missing",
  "entry_location_htf_range_missing",
  "buy_in_premium",
  "sell_in_discount",
  "buy_at_range_extreme",
  "sell_at_range_extreme",
  "range_entry_near_equilibrium",
  "range_buy_not_at_discount_edge",
  "range_sell_not_at_premium_edge",
  "quote_outside_zone",
  "reaction_trigger_missing",
  "reaction_trigger_before_zone_touch",
  "reaction_trigger_stale",
  "reaction_trigger_wrong_direction",
  "confirmation_requires_sweep_body",
  "demand_requires_sweep_reclaim",
  "sell_not_proximal",
  "key_buy_into_impulse",
  "impulse_against_block",
  "breakout_retest_evidence_missing",
  "trend_pullback_evidence_missing",
  "momentum_continuation_evidence_missing",
  "momentum_cannot_reuse_reversal_trigger",
})


async def emit_activation_gate_metrics(
  client: Any,
  *,
  symbol: str,
  strategy: str | None,
  direction: str | None,
  decision: EntryActivationDecision | EntryLocationDecision,
  allowed: bool,
) -> None:
  """Increment activation allow/block counters with strategy/direction tags."""
  from app.autotrade.lifecycle import increment_metric

  reason = str(decision.reason_code or "unknown")
  dimensions = {
    "strategy": str(strategy or ""),
    "direction": str(direction or "").upper(),
  }
  measured = getattr(decision, "measured", None) or {}
  if reason == "reaction_m5_authoritative_in_zone":
    m1_fail = measured.get("m1_fallback_reason")
    if m1_fail:
      dimensions["m1_fail_reason"] = str(m1_fail)
  try:
    if allowed:
      await increment_metric(
        client, "activation_allowed", symbol=symbol, dimensions=dimensions,
      )
      await increment_metric(
        client,
        f"activation_allowed:{reason}",
        symbol=symbol,
        dimensions=dimensions,
      )
      return
    if not getattr(decision, "hard_block", False):
      return
    await increment_metric(
      client, "activation_blocked", symbol=symbol, dimensions=dimensions,
    )
    await increment_metric(
      client,
      f"activation_blocked:{reason}",
      symbol=symbol,
      dimensions=dimensions,
    )
  except Exception:
    log.exception(
      "activation gate metrics failed symbol=%s reason=%s allowed=%s",
      symbol,
      reason,
      allowed,
    )


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

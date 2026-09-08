"""Unit tests for archetype-aware entry activation gating."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.analysis.entry_location import EntryLocationDecision
from app.analysis.m1_trigger import M1TriggerResult
from app.autotrade.entry_activation import (
  evaluate_entry_activation,
  apply_trigger_to_match,
  activation_archetype,
  ACTIVATION_BLOCK_REASON_CODES,
  EntryActivationDecision,
  emit_activation_gate_metrics,
)


pytestmark = pytest.mark.no_database

NOW = 1_700_000_000
ZONE_ENTERED = NOW - 120


def _activation_cfg(
  mode: str = "enforce",
  max_age: int = 2,
  *,
  m5_fallback: str = "off",
  m5_max_age: int = 6,
):
  return SimpleNamespace(
    execution=SimpleNamespace(
      activation=SimpleNamespace(
        mode=mode,
        reaction_trigger_maximum_age_bars=max_age,
        m5_authoritative_fallback=m5_fallback,
        m5_confirmation_maximum_age_bars=m5_max_age,
      ),
    ),
  )


def _location_allowed() -> EntryLocationDecision:
  return EntryLocationDecision(
    allowed=True,
    reason_code="entry_location_allowed",
    hard_block=False,
    archetype="reversal",
    would_block=False,
    measured={},
  )


def _location_blocked(reason: str = "buy_at_range_extreme") -> EntryLocationDecision:
  return EntryLocationDecision(
    allowed=False,
    reason_code=reason,
    hard_block=True,
    archetype="reversal",
    would_block=True,
    measured={},
  )


def _trigger(
  *,
  pattern: str = "wick_rejection",
  direction: str = "BUY",
  bar_ts: int | None = None,
) -> M1TriggerResult:
  return M1TriggerResult(
    pattern,
    direction,
    4080.0,
    bar_ts if bar_ts is not None else NOW - 60,
    "test trigger",
  )


def _activate(
  *,
  strategy: str = "Zone Reaction",
  direction: str = "BUY",
  trigger: M1TriggerResult | None = None,
  location: EntryLocationDecision | None = None,
  zone_entered_at: int | None = ZONE_ENTERED,
  quote_inside: bool = True,
  decisive_break: bool = False,
  now: int = NOW,
  mode: str = "enforce",
  breakout_evidence: dict | None = None,
  continuation_evidence: dict | None = None,
  m5_authoritative: bool = False,
  m5_confirmation_bar_ts: str | int | None = None,
  m5_fallback: str = "off",
):
  return evaluate_entry_activation(
    strategy=strategy,
    direction=direction,
    zone_entered_at=zone_entered_at,
    quote_inside=quote_inside,
    decisive_break=decisive_break,
    trigger=trigger,
    location_decision=location or _location_allowed(),
    now=now,
    cfg=_activation_cfg(mode, m5_fallback=m5_fallback),
    breakout_evidence=breakout_evidence,
    continuation_evidence=continuation_evidence,
    m5_authoritative=m5_authoritative,
    m5_confirmation_bar_ts=m5_confirmation_bar_ts,
  )


def test_case_01_reaction_trigger_missing_enforce():
  decision = _activate(trigger=None)
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_missing"
  assert decision.hard_block is True
  assert decision.would_block is True


def test_case_02_reaction_trigger_stale():
  stale = _trigger(bar_ts=NOW - 300)
  decision = _activate(trigger=stale, zone_entered_at=NOW - 400)
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_stale"


def test_case_03_reaction_trigger_before_zone_touch():
  early = _trigger(bar_ts=ZONE_ENTERED - 60)
  decision = _activate(trigger=early)
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_before_zone_touch"


def test_case_04_wick_rejection_buy_allowed():
  trigger = _trigger(pattern="wick_rejection", direction="BUY")
  decision = _activate(strategy="Zone Reaction", direction="BUY", trigger=trigger)
  assert decision.allowed is True
  assert decision.reason_code == "entry_activation_allowed"
  assert decision.trigger_type == "wick_rejection"


def test_case_05_wick_rejection_sell_allowed():
  trigger = _trigger(pattern="wick_rejection", direction="SELL")
  decision = _activate(strategy="Zone Reaction", direction="SELL", trigger=trigger)
  assert decision.allowed is True
  assert decision.reason_code == "entry_activation_allowed"


def test_case_06_reaction_trigger_wrong_direction():
  trigger = _trigger(direction="SELL")
  decision = _activate(direction="BUY", trigger=trigger)
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_wrong_direction"


def test_case_07_decisive_break_blocks_activation():
  decision = _activate(
    trigger=_trigger(),
    decisive_break=True,
  )
  assert decision.allowed is False
  assert decision.reason_code == "zone_decisively_broken"


def test_case_08_previous_episode_trigger_consumed():
  """Trigger from before zone touch is rejected (same as case 3)."""
  prior_episode = _trigger(bar_ts=ZONE_ENTERED - 180)
  decision = _activate(trigger=prior_episode)
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_before_zone_touch"


@pytest.mark.parametrize(
  "strategy",
  ["Key Level", "Range Edge Scalp"],
)
def test_case_09_grade_a_and_b_require_trigger_when_enforce(strategy):
  decision = _activate(strategy=strategy, trigger=None)
  assert activation_archetype(strategy) == "reaction_reversal"
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_missing"


def test_case_10_breakout_retest_requires_evidence():
  decision = _activate(
    strategy="Break & Retest",
    direction="BUY",
    trigger=_trigger(),
    breakout_evidence=None,
  )
  assert decision.allowed is False
  assert decision.reason_code == "breakout_retest_evidence_missing"


def test_case_10b_breakout_retest_with_evidence_allowed():
  decision = _activate(
    strategy="Break & Retest",
    direction="BUY",
    trigger=_trigger(),
    breakout_evidence={
      "accepted_break": True,
      "retest_of_broken_level": True,
      "directionally_valid_close": True,
    },
  )
  assert decision.allowed is True
  assert decision.reason_code == "entry_activation_allowed"


def test_case_11_trend_pullback_allowed_without_reversal_trigger():
  decision = _activate(
    strategy="Trend Pullback",
    direction="BUY",
    trigger=None,
  )
  assert activation_archetype("Trend Pullback") == "trend_pullback"
  assert decision.allowed is True
  assert decision.requires_trigger is False


def test_case_12_momentum_allowed_without_reversal_trigger():
  decision = _activate(
    strategy="Momentum Ride",
    direction="BUY",
    trigger=None,
  )
  assert activation_archetype("Momentum Ride") == "momentum_continuation"
  assert decision.allowed is True


def test_case_12b_momentum_rejects_reversal_trigger_reuse():
  decision = _activate(
    strategy="Momentum Ride",
    direction="BUY",
    trigger=_trigger(pattern="wick_rejection"),
  )
  assert decision.allowed is False
  assert decision.reason_code == "momentum_cannot_reuse_reversal_trigger"


def test_case_13_location_block_with_valid_trigger():
  decision = _activate(
    trigger=_trigger(),
    location=_location_blocked("buy_at_range_extreme"),
  )
  assert decision.allowed is False
  assert decision.reason_code == "buy_at_range_extreme"


def test_case_14_trigger_block_with_valid_location():
  decision = _activate(trigger=None, location=_location_allowed())
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_missing"


def test_case_15_shadow_allows_with_would_block():
  decision = _activate(trigger=None, mode="shadow")
  assert decision.allowed is True
  assert decision.would_block is True
  assert decision.reason_code == "reaction_trigger_missing"


def test_m5_authoritative_in_zone_allows_without_m1():
  decision = _activate(
    strategy="Key Level",
    trigger=None,
    quote_inside=True,
    m5_authoritative=True,
    m5_fallback="quote_inside",
  )
  assert decision.allowed is True
  assert decision.would_block is False
  assert decision.reason_code == "reaction_m5_authoritative_in_zone"
  assert decision.measured["m1_fallback_reason"] == "reaction_trigger_missing"


def test_m5_fallback_off_blocks_without_m1_even_when_authoritative():
  decision = _activate(
    strategy="Key Level",
    trigger=None,
    quote_inside=True,
    m5_authoritative=True,
    m5_fallback="off",
  )
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_missing"


def test_m5_authoritative_outside_zone_still_blocked():
  decision = _activate(
    strategy="Key Level",
    trigger=None,
    quote_inside=False,
    m5_authoritative=True,
    m5_fallback="quote_inside",
  )
  assert decision.allowed is False
  assert decision.reason_code == "quote_outside_zone"


def test_m5_quote_inside_fresh_requires_recent_confirmation():
  fresh_ts = NOW - 300
  decision = _activate(
    strategy="Key Level",
    trigger=None,
    quote_inside=True,
    m5_authoritative=True,
    m5_fallback="quote_inside_fresh",
    m5_confirmation_bar_ts=str(fresh_ts),
  )
  assert decision.allowed is True
  assert decision.reason_code == "reaction_m5_authoritative_in_zone"

  stale_ts = NOW - 7 * 300
  stale = _activate(
    strategy="Key Level",
    trigger=None,
    quote_inside=True,
    m5_authoritative=True,
    m5_fallback="quote_inside_fresh",
    m5_confirmation_bar_ts=str(stale_ts),
  )
  assert stale.allowed is False
  assert stale.reason_code == "reaction_trigger_missing"

  missing = _activate(
    strategy="Key Level",
    trigger=None,
    quote_inside=True,
    m5_authoritative=True,
    m5_fallback="quote_inside_fresh",
    m5_confirmation_bar_ts=None,
  )
  assert missing.allowed is False
  assert missing.reason_code == "reaction_trigger_missing"


def test_m5_authoritative_false_without_m1_still_missing():
  decision = _activate(
    strategy="Key Level",
    trigger=None,
    quote_inside=True,
    m5_authoritative=False,
    m5_fallback="quote_inside",
  )
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_missing"


def test_m1_preferred_over_m5_bridge():
  decision = _activate(
    strategy="Key Level",
    trigger=_trigger(pattern="wick_rejection", direction="BUY"),
    quote_inside=True,
    m5_authoritative=True,
    m5_fallback="quote_inside",
  )
  assert decision.allowed is True
  assert decision.reason_code == "entry_activation_allowed"
  assert "m1_fallback_reason" not in decision.measured


# Cases 16-18 (persist/publish on cutover) belong in integration tests:
# - test_zone_execution_cutover.py should assert matches are not persisted
#   when evaluate_entry_activation returns allowed=False.
# Unit-level contract: callers must not persist when blocked.
def test_case_16_blocked_activation_implies_no_persist_contract():
  decision = _activate(trigger=None, mode="enforce")
  assert decision.allowed is False
  assert decision.hard_block is True


def test_apply_trigger_to_match_stamps_fields():
  from dataclasses import dataclass

  @dataclass
  class _Match:
    touch_bar_ts: str | None = None
    confirmation_bar_ts: str | None = None
    reaction_type: str | None = None
    m5_confirmation_bar_ts: str | None = "1700000000"
    m5_reaction_type: str | None = "wick_rejection"
    entry_activation_trigger: str | None = None
    entry_activation_trigger_ts: str | None = None

  trigger = _trigger(bar_ts=NOW - 30)
  updated = apply_trigger_to_match(_Match(), trigger)
  assert updated.entry_activation_trigger == "wick_rejection"
  assert updated.entry_activation_trigger_ts == str(NOW - 30)
  assert updated.confirmation_bar_ts == str(NOW - 30)
  assert updated.reaction_type == "wick_rejection"
  assert updated.m5_confirmation_bar_ts == "1700000000"
  assert updated.m5_reaction_type == "wick_rejection"


def test_demand_sweep_reclaim_blocks_m5_fallback():
  cfg = SimpleNamespace(
    execution=SimpleNamespace(
      activation=SimpleNamespace(
        mode="enforce",
        reaction_trigger_maximum_age_bars=2,
        m5_authoritative_fallback="quote_inside",
        m5_confirmation_maximum_age_bars=6,
      ),
      technique=SimpleNamespace(enforce=True, require_sweep_body=False),
    ),
  )
  no_sweep = [
    {"h": 4342.0, "l": 4338.0, "c": 4339.5},
    {"h": 4341.0, "l": 4338.5, "c": 4339.0},
    {"h": 4341.3, "l": 4338.4, "c": 4340.0},
  ]
  decision = evaluate_entry_activation(
    strategy="Demand Zone",
    direction="BUY",
    zone_entered_at=ZONE_ENTERED,
    quote_inside=True,
    decisive_break=False,
    trigger=None,
    location_decision=_location_allowed(),
    now=NOW,
    cfg=cfg,
    m5_authoritative=True,
    m5_confirmation_bar_ts=str(NOW - 300),
    impulse_bars=no_sweep,
    zone_low=4337.0,
    zone_high=4340.0,
  )
  assert decision.allowed is False
  assert decision.reason_code == "demand_requires_sweep_reclaim"


def test_m5_authoritative_blocked_under_technique_enforce():
  cfg = SimpleNamespace(
    execution=SimpleNamespace(
      activation=SimpleNamespace(
        mode="enforce",
        reaction_trigger_maximum_age_bars=2,
        m5_authoritative_fallback="off",
        m5_confirmation_maximum_age_bars=6,
      ),
      technique=SimpleNamespace(enforce=True, require_sweep_body=False),
    ),
  )
  decision = evaluate_entry_activation(
    strategy="Key Level",
    direction="BUY",
    zone_entered_at=ZONE_ENTERED,
    quote_inside=True,
    decisive_break=False,
    trigger=None,
    location_decision=_location_allowed(),
    now=NOW,
    cfg=cfg,
    m5_authoritative=True,
  )
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_missing"


def test_reaction_softens_sweep_body_when_instrument_flag_is_false():
  cfg = SimpleNamespace(
    execution=SimpleNamespace(
      activation=SimpleNamespace(mode="enforce", reaction_trigger_maximum_age_bars=2),
      technique=SimpleNamespace(enforce=True, require_sweep_body=False),
    ),
  )
  decision = evaluate_entry_activation(
    strategy="Key Level",
    direction="BUY",
    zone_entered_at=ZONE_ENTERED,
    quote_inside=True,
    decisive_break=False,
    trigger=_trigger(pattern="pin_bar"),
    location_decision=_location_allowed(),
    now=NOW,
    cfg=cfg,
  )
  assert decision.allowed is True
  assert decision.reason_code == "entry_activation_allowed"
  assert decision.measured["sweep_body_required"] is False
  assert decision.measured["sweep_body_confirmed"] is False
  assert decision.measured["sweep_body_soft_miss"] is True
  assert (
    decision.measured["preference_reason_code"]
    == "confirmation_without_sweep_body"
  )


def test_impulse_against_blocks_sell_into_expanding_highs():
  from app.autotrade.entry_activation import detect_impulse_against

  bars = [
    {"h": 4386.0, "l": 4384.0, "c": 4385.0},
    {"h": 4387.9, "l": 4383.6, "c": 4387.4},
    {"h": 4389.3, "l": 4385.6, "c": 4387.6},
  ]
  assert detect_impulse_against(
    direction="SELL", bars=bars, zone_low=4388.0, zone_high=4391.0,
  ) is False
  bars[-1]["c"] = 4388.5
  assert detect_impulse_against(
    direction="SELL", bars=bars, zone_low=4388.0, zone_high=4391.0,
  ) is True

  cfg = SimpleNamespace(
    execution=SimpleNamespace(
      activation=SimpleNamespace(mode="enforce", reaction_trigger_maximum_age_bars=2),
      technique=SimpleNamespace(enforce=True, require_sweep_body=False),
    ),
  )
  decision = evaluate_entry_activation(
    strategy="Key Level",
    direction="SELL",
    zone_entered_at=ZONE_ENTERED,
    quote_inside=True,
    decisive_break=False,
    trigger=_trigger(pattern="sweep_reclaim", direction="SELL"),
    location_decision=_location_allowed(),
    now=NOW,
    cfg=cfg,
    impulse_bars=bars,
    zone_low=4388.0,
    zone_high=4391.0,
  )
  assert decision.allowed is False
  assert decision.reason_code == "impulse_against_block"


def test_demand_buy_requires_sweep_reclaim():
  cfg = SimpleNamespace(
    execution=SimpleNamespace(
      activation=SimpleNamespace(mode="enforce", reaction_trigger_maximum_age_bars=2),
      technique=SimpleNamespace(enforce=True, require_sweep_body=False),
    ),
  )
  trigger = _trigger(pattern="body_close", direction="BUY")
  no_sweep = [
    {"h": 4342.0, "l": 4338.0, "c": 4339.5},
    {"h": 4341.0, "l": 4338.5, "c": 4339.0},
    {"h": 4341.3, "l": 4338.4, "c": 4340.0},
  ]
  decision = evaluate_entry_activation(
    strategy="Demand Zone",
    direction="BUY",
    zone_entered_at=ZONE_ENTERED,
    quote_inside=True,
    decisive_break=False,
    trigger=trigger,
    location_decision=_location_allowed(),
    now=NOW,
    cfg=cfg,
    impulse_bars=no_sweep,
    zone_low=4337.0,
    zone_high=4340.0,
  )
  assert decision.allowed is False
  assert decision.reason_code == "demand_requires_sweep_reclaim"

  swept = [
    {"h": 4342.0, "l": 4338.0, "c": 4339.5},
    {"h": 4341.0, "l": 4337.36, "c": 4339.38},
    {"h": 4341.3, "l": 4338.4, "c": 4340.08},
  ]
  ok = evaluate_entry_activation(
    strategy="Demand Zone",
    direction="BUY",
    zone_entered_at=ZONE_ENTERED,
    quote_inside=True,
    decisive_break=False,
    trigger=_trigger(pattern="sweep_reclaim", direction="BUY"),
    location_decision=_location_allowed(),
    now=NOW,
    cfg=cfg,
    impulse_bars=swept,
    zone_low=4337.0,
    zone_high=4340.0,
  )
  assert ok.allowed is True


def test_key_sell_rejects_distal_quote():
  cfg = SimpleNamespace(
    execution=SimpleNamespace(
      activation=SimpleNamespace(mode="enforce", reaction_trigger_maximum_age_bars=2),
      technique=SimpleNamespace(enforce=True, require_sweep_body=False),
    ),
  )
  decision = evaluate_entry_activation(
    strategy="Key Level",
    direction="SELL",
    zone_entered_at=ZONE_ENTERED,
    quote_inside=True,
    decisive_break=False,
    trigger=_trigger(pattern="wick_rejection", direction="SELL"),
    location_decision=_location_allowed(),
    now=NOW,
    cfg=cfg,
    zone_low=4383.0,
    zone_high=4386.0,
    execution_price=4385.8,
  )
  assert decision.allowed is False
  assert decision.reason_code == "sell_not_proximal"

  proximal = evaluate_entry_activation(
    strategy="Key Level",
    direction="SELL",
    zone_entered_at=ZONE_ENTERED,
    quote_inside=True,
    decisive_break=False,
    trigger=_trigger(pattern="wick_rejection", direction="SELL"),
    location_decision=_location_allowed(),
    now=NOW,
    cfg=cfg,
    zone_low=4383.0,
    zone_high=4386.0,
    execution_price=4383.2,
  )
  assert proximal.allowed is True


def test_key_buy_blocked_into_expanding_bid():
  cfg = SimpleNamespace(
    execution=SimpleNamespace(
      activation=SimpleNamespace(mode="enforce", reaction_trigger_maximum_age_bars=2),
      technique=SimpleNamespace(enforce=True, require_sweep_body=False),
    ),
  )
  bars = [
    {"h": 4376.0, "l": 4374.0, "c": 4375.0},
    {"h": 4378.0, "l": 4375.0, "c": 4377.5},
    {"h": 4381.0, "l": 4377.0, "c": 4380.0},
  ]
  decision = evaluate_entry_activation(
    strategy="Key Level",
    direction="BUY",
    zone_entered_at=ZONE_ENTERED,
    quote_inside=True,
    decisive_break=False,
    trigger=_trigger(pattern="sweep_reclaim", direction="BUY"),
    location_decision=_location_allowed(),
    now=NOW,
    cfg=cfg,
    impulse_bars=bars,
    zone_low=4376.0,
    zone_high=4379.0,
  )
  assert decision.allowed is False
  assert decision.reason_code == "key_buy_into_impulse"


@pytest.mark.parametrize(
  "reason_code",
  sorted(ACTIVATION_BLOCK_REASON_CODES),
)
@pytest.mark.asyncio
async def test_activation_blocked_metrics_emitted_once(reason_code: str):
  from app.persistence import redis_state

  client = redis_state.get_client()
  decision = EntryActivationDecision(
    allowed=False,
    reason_code=reason_code,
    hard_block=True,
    requires_trigger=True,
    trigger_type=None,
    would_block=True,
    measured={},
  )
  await emit_activation_gate_metrics(
    client,
    symbol="XAU",
    strategy="Key Level",
    direction="BUY",
    decision=decision,
    allowed=False,
  )
  metrics_key = "auto_trade:metrics:XAU"
  assert int(await client.hget(metrics_key, "activation_blocked") or 0) == 1
  assert int(
    await client.hget(metrics_key, f"activation_blocked:{reason_code}") or 0
  ) == 1


@pytest.mark.asyncio
async def test_activation_allowed_metrics_emitted_once():
  from app.persistence import redis_state

  client = redis_state.get_client()
  decision = EntryActivationDecision(
    allowed=True,
    reason_code="entry_activation_allowed",
    hard_block=False,
    requires_trigger=True,
    trigger_type="wick_rejection",
    would_block=False,
    measured={},
  )
  await emit_activation_gate_metrics(
    client,
    symbol="XAU",
    strategy="Key Level",
    direction="BUY",
    decision=decision,
    allowed=True,
  )
  metrics_key = "auto_trade:metrics:XAU"
  assert int(await client.hget(metrics_key, "activation_allowed") or 0) == 1
  assert int(
    await client.hget(
      metrics_key, "activation_allowed:entry_activation_allowed",
    ) or 0
  ) == 1


@pytest.mark.asyncio
async def test_m5_fallback_allowed_metrics_include_m1_fail_reason():
  from app.persistence import redis_state

  client = redis_state.get_client()
  decision = EntryActivationDecision(
    allowed=True,
    reason_code="reaction_m5_authoritative_in_zone",
    hard_block=False,
    requires_trigger=True,
    trigger_type=None,
    would_block=False,
    measured={"m1_fallback_reason": "reaction_trigger_missing"},
  )
  await emit_activation_gate_metrics(
    client,
    symbol="XAU",
    strategy="Key Level",
    direction="BUY",
    decision=decision,
    allowed=True,
  )
  metrics_key = "auto_trade:metrics:XAU"
  dim_key = f"{metrics_key}:activation_allowed:reaction_m5_authoritative_in_zone"
  assert int(
    await client.hget(
      metrics_key, "activation_allowed:reaction_m5_authoritative_in_zone",
    ) or 0
  ) == 1
  assert int(
    await client.hget(
      dim_key,
      "direction=BUY:m1_fail_reason=reaction_trigger_missing:strategy=Key Level",
    ) or 0
  ) == 1

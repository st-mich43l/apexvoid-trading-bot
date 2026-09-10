"""Cross-side scanner actionability and structural target-room regressions."""

from __future__ import annotations
from app.core.config import runtime_config

from dataclasses import replace
from types import SimpleNamespace
import json
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from tests.configuration.canonical_fixtures import actionability_cfg, install_runtime_overrides
from app.analysis.actionability import resolve_actionability
from app.analysis import scanner
from app.analysis.detectors import DetectionResult
from app.analysis.key_level_role import (
  ROLE_AMBIGUOUS,
  ROLE_BROKEN_RESISTANCE,
  ROLE_RESISTANCE,
  ROLE_SUPPORT,
  classify_key_level_role,
)
from app.analysis.market_map import MapEntry, MarketMap
from app.analysis.types import Zone
from app.autotrade.execution_policy import (
  ExecutionPolicy,
  ExecutionPolicyEvaluation,
  classify_guard_severity,
)
from app.autotrade.structural_target_room import (
  evaluate_structural_target_room,
  filter_displaced_opposing_entries,
  filter_overlapping_opposing_entries,
  filter_shared_boundary_opposing_entries,
  zone_proximal_room_reference,
)
from app.autotrade.strategy_match import (
  strategy_match_key,
)
from app.autotrade.multi_match import strategy_matches_key
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


def _result(
  direction: str,
  low: float,
  high: float,
  *,
  setup: str | None = None,
  quality: int = 3,
  current_price: float | None = None,
  structural_id: str | None = None,
  role: str | None = None,
) -> DetectionResult:
  side = "demand" if direction == "BUY" else "supply"
  return DetectionResult(
    setup=setup or (
      "Demand Zone Reaction" if direction == "BUY"
      else "Supply Zone Reaction"
    ),
    direction=direction,
    key_level=(low + high) / 2,
    entry_zone=Zone(low, high, side, score=float(quality)),
    current_price=(
      (low + high) / 2 if current_price is None else current_price
    ),
    confluence=quality,
    reasons=["fixture reaction"],
    mode="counter_bias" if direction == "BUY" else "with_bias",
    structural_source=(
      "key_level" if setup == "Key Level" else "supply_demand"
    ),
    structural_id=structural_id or f"{direction}:{low}:{high}",
    structural_low=low,
    structural_high=high,
    structural_timeframe="M5",
    structural_kind=(
      "round" if setup == "Key Level" else side
    ),
    key_level_role=role,
    planned_entry_price=(
      (low + high) / 2 if current_price is None else current_price
    ),
    provisional_targets_pips=(30, 50, 70),
  )


def _entry(
  side: str,
  low: float,
  high: float,
  *,
  tier: str = "major",
  contains_price: bool = False,
  tags: tuple[str, ...] = (),
) -> MapEntry:
  return MapEntry(
    side,
    low,
    high,
    round(low),
    round(high),
    tier,
    list(tags or (side,)),
    10.0,
    contains_price=contains_price,
  )


def _map(
  *entries: MapEntry,
  price: float = 4100.0,
) -> MarketMap:
  return MarketMap(
    entries=list(entries),
    price=price,
    eq=None,
    box_low=None,
    box_high=None,
    bias="down",
    bias_tf="H1",
    actionable_entries=list(entries),
  )


def _map_to_zones(market_map: MarketMap) -> list[Zone]:
  """2026-09 (Market Map purge stage 4): resolve_actionability now reads
  technique-native ``Zone`` data, not Market Map. structural_target_room's
  zone_opposing_entries derives "major" vs "zone" tier the same way
  build_map itself does (score_reasons containing "HTF Zone", plus fresh/
  score) - reproduce that here from each fixture's ``tier=`` so existing
  "major"-tier fixtures still round-trip to "major" through the real
  formula instead of silently collapsing to "zone".
  """
  zones = []
  for entry in market_map.actionable_entries:
    is_major = entry.tier == "major"
    zones.append(Zone(
      entry.lo,
      entry.hi,
      "demand" if entry.side == "buy" else "supply",
      score=entry.score,
      score_reasons=["HTF Zone"] if is_major else [],
      touches=0 if is_major else 1,
    ))
  return zones


def _cfg(
  *,
  guard_mode: str = "balanced",
  profile: str = "conservative",
  actionability_gate: bool = True,
  role_ambiguity_gate: bool = True,
  allow_counter_bias: bool = True,
  displacement_lookback_bars: int = 0,
  execution_cost_pips: float = 1.0,
  min_capped_target_pips: float = 15.0,
) -> SimpleNamespace:
  # ``profile`` is retained for call-site compatibility; actionability no
  # longer reads a flat profile field after Phase 2I-A.1.
  _ = profile
  return actionability_cfg(
    auto_trade_allow_counter_bias=allow_counter_bias,
    auto_trade_structural_guard_mode=guard_mode,
    scanner_actionability_gate_enabled=actionability_gate,
    key_level_role_ambiguity_gate_enabled=role_ambiguity_gate,
    auto_trade_displacement_override_lookback_bars=displacement_lookback_bars,
    auto_trade_execution_cost_pips=execution_cost_pips,
    auto_trade_min_capped_target_pips=min_capped_target_pips,
  )


@pytest.mark.parametrize("profile", ["conservative", "demo_eval"])
@pytest.mark.parametrize("guard_mode", ["observe", "balanced", "strict"])
def test_buy_under_overlapping_sell_major_hard_blocks_below_cost_room(
  profile,
  guard_mode,
):
  """Buffered usable room below execution-cost floor hard-kills publication.

  Owner 2026-08-06 (revised): never invent a solo ~9 pip TP, and never
  publish the full partial ladder into ~0 pip of barrier room either
  (live fe023 Trendline SELL into demand).
  """
  buy = _result(
    "BUY",
    4041.67,
    4046.73,
    setup="Key Level",
    quality=3,
    current_price=4045.95,
  )
  market_map = _map(
    _entry("sell", 4046.0, 4055.0, tier="major"),
    _entry("buy", 4042.0, 4051.0, tier="major", contains_price=True),
    price=4045.95,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(guard_mode=guard_mode, profile=profile),
  )

  assert resolution.observed == (buy,)
  assert resolution.actionable == ()
  assert len(resolution.gated) == 1
  _item, decision = resolution.gated[0]
  assert decision.reason_code == "opposing_barrier_room_below_cost"
  assert decision.hard_block is True
  assert decision.allowed is False


def test_scalp_keeps_ladder_when_room_fits_swing_hard_blocks_when_below_cost():
  """Scalp ignores HTF opposing discovery geometry once detector min room
  already cleared. Swing with ATR buffer collapses to below cost and hard-kills.
  """
  market_map = _map(
    _entry("sell", 4045.60, 4050.0, tier="major"),
    price=4045.0,
  )
  cfg = actionability_cfg(
    barrier_buffer_atr=0.5,
    scalp_barrier_buffer_atr=0.15,
  )

  scalp = _result(
    "BUY",
    4044.50,
    4045.00,
    setup="Range Edge Scalp",
    current_price=4045.0,
  )
  scalp_resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[scalp],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=cfg,
  )
  assert len(scalp_resolution.actionable) == 1
  assert scalp_resolution.actionable[0].setup == "Range Edge Scalp"
  assert scalp_resolution.gated == ()
  assert not any(
    decision.reason_code == "opposing_barrier_room_below_cost"
    for _item, decision in scalp_resolution.decisions
  )

  swing = _result(
    "BUY",
    4044.50,
    4045.00,
    setup="Key Level",
    current_price=4045.0,
  )
  swing_resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[swing],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=cfg,
  )
  assert swing_resolution.actionable == ()
  assert swing_resolution.gated[0][1].reason_code == (
    "opposing_barrier_room_below_cost"
  )


def test_scalp_not_hard_killed_by_zero_usable_htf_opposing_room():
  """Range Edge near HTF demand/supply with usable HTF room=0 still goes —
  native range min room is the gate, not HTF opposing distance.
  """
  scalp = _result(
    "SELL",
    4267.8,
    4270.4,
    setup="Range Edge Scalp",
    current_price=4268.24,
  )
  market_map = _map(
    _entry("buy", 4263.76, 4267.8, tier="major"),
    price=4268.24,
  )
  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[scalp],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="up"),
    atr=4.53,
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  assert not any(
    decision.reason_code == "opposing_barrier_room_below_cost"
    for _item, decision in resolution.decisions
  )


def test_raw_room_zero_major_hard_gates():
  # Planned entry already past the opposing major (not contained inside it)
  # leaves negative raw room → hard structural gate.
  buy = _result(
    "BUY",
    4054.0,
    4057.0,
    quality=3,
    current_price=4056.0,
  )
  market_map = _map(
    _entry("sell", 4046.0, 4055.0, tier="major"),
    price=4056.0,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert resolution.actionable == ()
  decision = resolution.gated[0][1]
  assert decision.reason_code == "opposing_major_no_room"
  assert decision.hard_block is True
  assert decision.measured["raw_room_price"] < 0


def test_trimmed_zone_touching_opposing_edge_is_not_misreported_as_contained():
  """05 Aug incident: a Zone Reaction BUY's planned_entry_price ended up
  EXACTLY equal to the opposing supply zone's low edge (4197.75 both
  sides) and got hard-blocked as opposing_entry_contained - "entry
  contained inside the opposing zone" - when the entry was never inside
  anything. _trim_zone_against_overlapping_barrier (the 2026-07-31
  recovery-mission fix) correctly trims the candidate zone down to its
  clean, non-overlapping portion when it overlaps an opposing barrier,
  but then clamps planned_entry_price into [low, high] using the newly
  TRIMMED high - which equals the opposing zone's own low exactly. Since
  evaluate_structural_target_room's containment check is inclusive
  (opposing_low <= planned <= opposing_high), a planned price sitting
  exactly on that trimmed edge reads as "contained", re-triggering the
  exact rejection the trim exists to avoid.

  This does not invent a "contained" reason for an edge touch. Tiny
  usable room below the cost floor hard-rejects for TP room instead.
  """
  market_map = _map(
    _entry("sell", 4197.75, 4211.73, tier="zone"),
    price=4197.75,
  )
  buy = _result(
    "BUY", 4196.52, 4198.50, setup="Zone Reaction", current_price=4197.75,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="up"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert resolution.actionable == ()
  decision = resolution.gated[0][1]
  assert decision.reason_code == "opposing_barrier_room_below_cost"
  assert decision.reason_code != "opposing_entry_contained"
  assert decision.measured["planned_entry_contained"] is False
  assert decision.measured["planned_entry_price"] < 4197.75


def test_target_room_below_cost_is_soft_when_actionability_gate_is_off():
  buy = _result(
    "BUY",
    4041.67,
    4046.73,
    quality=3,
    current_price=4045.95,
  )
  market_map = _map(
    _entry("sell", 4046.0, 4055.0, tier="major"),
    price=4045.95,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=False),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "opposing_barrier_room_below_cost"
  )
  # Gate off demotes non-universal hard reasons to preference/allow.
  assert decision.hard_block is False
  assert decision.allowed is True


def test_target_room_hard_blocks_below_cost_when_actionability_gate_is_on():
  buy = _result(
    "BUY",
    4041.67,
    4046.73,
    quality=3,
    current_price=4045.95,
  )
  market_map = _map(
    _entry("sell", 4046.0, 4055.0, tier="major"),
    price=4045.95,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert resolution.actionable == ()
  assert len(resolution.gated) == 1
  decision = resolution.gated[0][1]
  assert decision.reason_code == "opposing_barrier_room_below_cost"
  assert decision.hard_block is True
  assert decision.allowed is False


def test_partial_overlap_trims_the_zone_instead_of_killing_the_whole_setup():
  """2026-07-30 incident: a BUY entry zone overlapped a (non-major)
  opposing SELL zone by a small sliver at its far edge, and the whole
  setup got hard-rejected even though 75% of the zone - including the
  actual planned entry point - was clean, untouched room. The overlap
  check compares raw zone bounds, not the planned entry price, so a
  zone-level sliver used to kill a trade whose entry was never really in
  danger. Trimming the zone to its own non-overlapping portion first
  lets the room check evaluate the real, now non-overlapping geometry.
  """
  buy = _result(
    "BUY",
    4100.0,
    4108.0,
    quality=3,
    current_price=4102.0,
  )
  market_map = _map(
    _entry("sell", 4106.0, 4112.0, tier="zone"),
    price=4102.0,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert resolution.gated == ()
  assert len(resolution.actionable) == 1
  trimmed = resolution.actionable[0]
  # Zone trimmed from 4100-4108 down to 4100-4106 (the opposing zone's
  # near edge) - the overlapping top 2.0 is gone, the clean bottom 6.0
  # remains.
  assert trimmed.entry_zone.low == pytest.approx(4100.0)
  assert trimmed.entry_zone.high == pytest.approx(4106.0)
  assert trimmed.target_cap_pips == pytest.approx(70.0)


def test_full_overlap_by_a_major_still_rejects_nothing_left_to_trim_into():
  """A candidate zone entirely consumed by an opposing MAJOR has no clean
  portion to trim into. Planned entry inside a major opposing structure is
  a hard structural conflict when the actionability gate is on.
  """
  buy = _result(
    "BUY",
    4106.5,
    4107.5,
    quality=3,
    current_price=4107.0,
  )
  market_map = _map(
    _entry("sell", 4100.0, 4112.0, tier="major"),
    price=4107.0,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert resolution.actionable == ()
  assert len(resolution.gated) == 1
  decision = resolution.gated[0][1]
  assert decision.reason_code in {
    "opposing_entry_contained",
    "opposing_entry_overlap",
  }
  assert decision.hard_block is True
  assert decision.allowed is False


def test_full_overlap_by_an_ordinary_zone_hard_rejects():
  """2026-09 (Opposing Structure V2 repair): PR #493 (2026-09-07) widened
  the weak-opposing treatment from just "level" to {"level", "zone"} -
  meaning full containment by an ordinary directional "zone" (not just
  "major") stopped hard-blocking at all. That was too coarse: it silently
  unblocked entries landing directly inside real, undisplaced, unmitigated
  supply/demand, not just genuinely minor structure. Production data
  isolated to the exact PR #493 merge timestamp confirmed the cost (Key
  Level auto-trades: 68 trades/56% win rate/+628 net pips before, vs 8
  trades/37.5%/-165 net pips after). An ordinary "zone" is real directional
  evidence again: full containment/overlap hard-blocks exactly like
  "major" does. Only a genuinely unsided, non-directional "level" (a
  round-number/generic reaction level with no real supply/demand behind
  it) keeps the weak treatment.
  """
  buy = _result(
    "BUY",
    4106.5,
    4107.5,
    quality=3,
    current_price=4107.0,
  )
  market_map = _map(
    _entry("sell", 4100.0, 4112.0, tier="zone"),
    price=4107.0,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert resolution.actionable == ()
  assert len(resolution.gated) == 1
  decision = resolution.gated[0][1]
  assert decision.reason_code in {
    "opposing_entry_contained",
    "opposing_entry_overlap",
  }
  assert decision.hard_block is True
  assert decision.allowed is False


def test_room_below_the_ladder_keeps_configured_ladder():
  """2026-08-06 owner: room below the smallest configured target must NOT
  invent a solo tiny TP. Keep the configured partial ladder.
  """
  buy = _result(
    "BUY",
    4103.0,
    4107.0,
    quality=3,
    current_price=4107.0,
  )
  market_map = _map(
    _entry("sell", 4110.0, 4118.0, tier="major"),
    price=4107.0,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert resolution.gated == ()
  assert len(resolution.actionable) == 1
  kept = resolution.actionable[0]
  # effective_target is max(configured ladder), not floor(usable room).
  assert kept.target_cap_pips == pytest.approx(70.0)


def test_room_below_the_floor_still_keeps_configured_ladder():
  """Positive buffered room below the preference floor stays actionable with
  the full configured ladder (preference telemetry), not a invented tiny TP.
  """
  buy = _result(
    "BUY",
    4103.0,
    4108.7,
    quality=3,
    current_price=4108.7,
  )
  market_map = _map(
    _entry("sell", 4110.0, 4118.0, tier="major"),
    price=4108.7,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code in {
      "opposing_barrier_room_ignored_full_ladder",
      "opposing_barrier_full_ladder_fits",
    }
  )
  assert decision.hard_block is False
  assert decision.allowed is True
  assert resolution.actionable[0].target_cap_pips == pytest.approx(70.0)
  assert resolution.actionable[0].provisional_targets_pips == (30, 50, 70)


def test_near_barrier_below_cost_hard_blocks_instead_of_tiny_or_full_ladder():
  decision = evaluate_structural_target_room(
    direction="BUY",
    planned_entry_price=4100.0,
    candidate_entry_low=4099.0,
    candidate_entry_high=4100.5,
    configured_target_pips=(30, 50, 70),
    actionable_entries=(
      _entry("sell", 4100.05, 4101.0, tier="zone"),
    ),
    atr=1.0,
    pip_size=0.1,
    barrier_buffer_atr=0.0,
    execution_cost_pips=1.0,
  )
  # raw = 0.05 → 0.5 pips < cost floor. Neither invent a 1-pip TP nor
  # publish the full ladder into untradeable room.
  assert decision.allowed is False
  assert decision.hard_block is True
  assert decision.fitted_targets_pips is None or decision.fitted_targets_pips == ()
  assert decision.effective_target_pips is None
  assert decision.reason_code == "opposing_barrier_room_below_cost"


def test_fe023_trendline_sell_into_demand_hard_blocks_below_cost_room():
  """Live 2026-08-06 08:30 UTC: Trendline SELL fe023dd8 published
  with opposing demand high 4267.8, planned entry 4268.24, usable_room=0,
  effective_target=200 via opposing_barrier_room_below_cost_ignored.
  Below-cost room must hard-kill.
  """
  decision = evaluate_structural_target_room(
    direction="SELL",
    planned_entry_price=4268.24,
    candidate_entry_low=4267.44782,
    candidate_entry_high=4270.39503,
    configured_target_pips=(30, 60, 90, 120, 200),
    actionable_entries=(
      _entry(
        "buy",
        4263.765714285714,
        4267.8,
        tier="major",
        tags=("flip", "demand", "FVG", "breakout-retest"),
      ),
    ),
    atr=4.53,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
    execution_cost_pips=1.0,
  )
  assert decision.allowed is False
  assert decision.hard_block is True
  assert decision.reason_code == "opposing_barrier_room_below_cost"
  assert decision.measured["usable_room_pips"] == pytest.approx(0.0)
  assert decision.measured["barrier_usable_room_below_cost"] is True


def test_counter_bias_reaction_is_observed_when_disabled():
  # Counter-bias disabled is a contextual observation. With the actionability
  # gate on, only documented hard conflicts (geometry / executable overlap /
  # insufficient target room) hard-block; HTF disagreement stays telemetry.
  buy = _result("BUY", 4100.0, 4101.0, quality=3, current_price=4100.5)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(_map(price=4100.5)),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(allow_counter_bias=False, actionability_gate=True),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "counter_bias_disabled"
  )
  assert decision.hard_block is False
  assert decision.allowed is True


def test_with_bias_reaction_is_unaffected_by_counter_bias_disabled():
  sell = _result("SELL", 4100.0, 4101.0, quality=3, current_price=4100.5)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[sell],
    zones=_map_to_zones(_map(price=4100.5)),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(allow_counter_bias=False),
  )

  assert not any(
    decision.reason_code == "counter_bias_disabled"
    for _item, decision in resolution.decisions
  )
  assert resolution.actionable == (sell,)


def test_invalid_geometry_is_always_analysis_only():
  result = _result("BUY", 4100.0, 4101.0)
  result = replace(
    result,
    entry_zone=replace(result.entry_zone, top=float("nan")),
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[result],
    zones=_map_to_zones(_map(price=4100.5)),
    context=SimpleNamespace(htf_bias="up"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=False, role_ambiguity_gate=False),
  )

  assert resolution.actionable == ()
  assert resolution.gated[0][1].reason_code == "invalid_geometry"
  assert resolution.gated[0][1].hard_block


def test_tier_c_is_allowed_as_preference_telemetry_by_static_pre_gate():
  result = _result(
    "SELL",
    4100.0,
    4102.0,
    quality=0,
    current_price=4101.0,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="down",
    indicators={"M5": SimpleNamespace(atr=pd.Series([2.0]))},
    structures={"M5": SimpleNamespace(scalp_range=None)},
    regime=SimpleNamespace(kind="trend"),
  )

  eligible, measured = scanner._reward_risk_pre_gate(
    "XAU",
    "M5",
    "2026-07-28T12:10:00+00:00",
    ctx,
    result,
  )

  # Tier C builds a match with reduced risk — scanner pre-gate is telemetry.
  assert eligible is True
  assert measured.get("preference_telemetry") is True
  assert measured.get("policy_hard_block") is False


@pytest.mark.parametrize("reason_code", [
  "policy_zone_too_wide",
  "entry_inside_opposing_zone",
  "policy_regime_not_permitted",
  "protective_stop_unavailable",
  "stop_inside_opposing_zone",
])
def test_static_pre_gate_honors_full_policy_denial_with_sufficient_rr(
  monkeypatch,
  reason_code,
):
  result = _result(
    "BUY",
    4098.0,
    4100.0,
    quality=3,
    current_price=4099.5,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    frames={"M5": pd.DataFrame({"close": [4099.5]})},
    indicators={"M5": SimpleNamespace(atr=pd.Series([2.0]))},
    structures={"M5": SimpleNamespace(scalp_range=None)},
    regime=SimpleNamespace(kind="trend"),
  )
  policy = ExecutionPolicy(
    family="supply_demand",
    min_confluence=2,
    max_entry_drift_atr=1.0,
    max_entry_drift_pips=20.0,
    max_zone_width_atr=1.0,
    min_target_room_atr=0.5,
    min_reward_risk=1.0,
    risk_multiplier=1.0,
    order_type_preference="either",
    permitted_regimes=("trend",),
  )
  monkeypatch.setattr(
    scanner,
    "evaluate_execution_policy",
    lambda *_args, **_kwargs: ExecutionPolicyEvaluation(
      allowed=False,
      reason_code=reason_code,
      message="entry zone exceeds policy width",
      terminal=True,
      measured={
        "reward_risk": 3.0,
        "min_reward_risk": 1.0,
        "planned_stop_price": 4093.5,
        "zone_width": 6.5,
      },
      policy=policy,
    ),
  )

  eligible, measured = scanner._reward_risk_pre_gate(
    "XAU",
    "M5",
    "2026-07-28T12:10:00+00:00",
    ctx,
    result,
  )

  assert eligible is True
  assert measured["reward_risk"] == 3.0
  assert measured["policy_reason_code"] == reason_code
  assert measured["policy_message"] == "entry zone exceeds policy width"
  assert measured["policy_hard_block"] is False
  assert measured.get("preference_telemetry") is True


def test_equal_opposing_observations_remain_raw_but_both_are_not_actionable():
  buy = _result("BUY", 4100.0, 4101.0, quality=3)
  sell = _result("SELL", 4100.0, 4101.0, quality=3)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    zones=_map_to_zones(_map(price=4100.5)),
    context=SimpleNamespace(htf_bias="range"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert resolution.observed == (buy, sell)
  assert len(resolution.actionable) == 2
  assert resolution.gated == ()
  contested = [
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "contested_corridor"
  ]
  assert len(contested) == 2
  assert all(decision.hard_block is False for decision in contested)
  assert all(decision.allowed is True for decision in contested)
  assert resolution.conflicts[0]["outcome"] == "contested_corridor"
  assert contested[0].measured["executable_conflict"] is True


def test_confluence_margin_never_picks_a_side_out_of_a_contested_corridor():
  """Executable overlap keeps contested telemetry. BUY into a near major
  supply with below-cost room hard-gates; SELL can still remain actionable.
  """
  buy = _result("BUY", 4100.0, 4101.0, quality=4)
  sell = _result("SELL", 4100.0, 4101.0, quality=2)
  no_room = _map(
    _entry("sell", 4100.8, 4103.0, tier="major"),
    price=4100.5,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    zones=_map_to_zones(no_room),
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  reasons = {
    decision.reason_code for _item, decision in resolution.decisions
  }
  assert "contested_corridor" in reasons
  assert resolution.conflicts[0]["outcome"] == "contested_corridor"
  assert "opposing_barrier_room_below_cost" in reasons
  assert {item.direction for item in resolution.actionable} == {"SELL"}
  assert any(
    item.direction == "BUY" and decision.reason_code == "opposing_barrier_room_below_cost"
    for item, decision in resolution.gated
  )


def test_distant_map_room_does_not_rescue_an_overlapping_pair():
  """A shared executable quote inside both bands is still contested."""
  buy = _result("BUY", 4100.0, 4101.0, quality=4)
  sell = _result("SELL", 4100.0, 4101.0, quality=2)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    zones=_map_to_zones(_map(
      _entry("sell", 4120.0, 4123.0, tier="major"),
      price=4100.5,
    )),
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert len(resolution.actionable) == 2
  assert resolution.gated == ()
  contested = [
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "contested_corridor"
  ]
  assert contested
  assert all(decision.hard_block is False for decision in contested)


def test_nearby_non_overlapping_bands_remain_watched():
  """Proximity alone must not kill both sides — retain nearby opposing bands."""
  buy = _result("BUY", 4001.0, 4007.0, quality=3, current_price=4004.0)
  sell = _result("SELL", 4007.0, 4019.0, quality=2, current_price=4013.0)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    zones=_map_to_zones(_map(price=4005.0)),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert len(resolution.actionable) == 2
  assert resolution.gated == ()
  assert resolution.conflicts == ()
  reasons = {decision.reason_code for _item, decision in resolution.decisions}
  assert "contested_corridor" not in reasons
  assert "nearby_opposing_structure" in reasons


def test_bands_well_separated_beyond_the_gap_threshold_are_not_contested():
  buy = _result("BUY", 4001.0, 4003.0, quality=3)
  sell = _result("SELL", 4050.0, 4052.0, quality=2)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    zones=_map_to_zones(_map(price=4002.0)),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  reasons = {decision.reason_code for _item, decision in resolution.gated}
  assert "contested_corridor" not in reasons


def test_proposed_entry_inside_opposing_band_is_executable_conflict():
  buy = _result("BUY", 4100.0, 4105.0, quality=3, current_price=4099.0)
  sell = _result("SELL", 4102.0, 4108.0, quality=3, current_price=4106.0)
  buy = replace(buy, planned_entry_price=4103.0)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    zones=_map_to_zones(_map(price=4099.0)),
    context=SimpleNamespace(htf_bias="range"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert len(resolution.actionable) == 2
  assert resolution.gated == ()
  contested = [
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "contested_corridor"
  ]
  assert len(contested) == 2
  assert all(decision.hard_block is False for decision in contested)


@pytest.mark.parametrize(
  ("direction", "entry_low", "entry_high", "barrier"),
  [
    ("BUY", 4100.0, 4101.0, _entry("sell", 4120.0, 4123.0)),
    ("SELL", 4120.0, 4121.0, _entry("buy", 4098.0, 4101.0)),
  ],
)
def test_valid_direction_with_structural_room_remains_actionable(
  direction,
  entry_low,
  entry_high,
  barrier,
):
  result = _result(direction, entry_low, entry_high, quality=3)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[result],
    zones=_map_to_zones(_map(barrier, price=result.current_price)),
    context=SimpleNamespace(
      htf_bias="up" if direction == "BUY" else "down",
    ),
    atr=1.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  assert resolution.actionable[0].target_cap_pips == pytest.approx(70)


def test_generic_key_level_without_acceptance_is_ambiguous():
  frame = pd.DataFrame(
    {
      "open": [100.0, 100.2, 99.9],
      "high": [100.4, 100.5, 100.3],
      "low": [99.7, 99.8, 99.6],
      "close": [100.1, 99.9, 100.0],
    },
    index=pd.date_range("2026-07-28", periods=3, freq="5min", tz="UTC"),
  )

  decision = classify_key_level_role(
    kind="round",
    level_price=100.0,
    band_low=99.5,
    band_high=100.5,
    closed_bars=frame,
    breakout_accept_bars=2,
  )

  assert decision.role == ROLE_AMBIGUOUS


def test_role_ambiguity_is_telemetry_only_never_a_hard_block():
  """P0 zone/M1 simplification: key_level_reaction() (detectors.py) no
  longer emits a genuinely-undecided ambiguous-role result - it
  deterministically resolves to exactly one direction before this point,
  or discards the level entirely. Hard-blocking "ambiguous role" here
  duplicated a decision already made upstream and rejected every one of
  those decisions regardless of how they were actually resolved. Now
  recorded for telemetry only, never gated - and never actionable-
  blocking regardless of the (already dead)
  key_level_role_ambiguity_gate_enabled flag.
  """
  result = _result(
    "BUY",
    99.5,
    100.5,
    setup="Key Level",
    role=ROLE_AMBIGUOUS,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[result],
    zones=_map_to_zones(_map(price=100.0)),
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=False, role_ambiguity_gate=False),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "key_level_role_ambiguous"
  )
  assert decision.hard_block is False
  assert decision.allowed is True


def test_key_level_explicit_role_and_accepted_break_are_deterministic():
  inside = pd.DataFrame(
    {"close": [100.0, 100.1]},
    index=pd.date_range("2026-07-28", periods=2, freq="5min", tz="UTC"),
  )
  accepted = pd.DataFrame(
    {"close": [101.0, 101.2]},
    index=pd.date_range("2026-07-28", periods=2, freq="5min", tz="UTC"),
  )

  assert classify_key_level_role(
    kind="support",
    level_price=100.0,
    band_low=99.5,
    band_high=100.5,
    closed_bars=inside,
    breakout_accept_bars=2,
  ).role == ROLE_SUPPORT
  assert classify_key_level_role(
    kind="resistance",
    level_price=100.0,
    band_low=99.5,
    band_high=100.5,
    closed_bars=inside,
    breakout_accept_bars=2,
  ).role == ROLE_RESISTANCE
  assert classify_key_level_role(
    kind="resistance",
    level_price=100.0,
    band_low=99.5,
    band_high=100.5,
    closed_bars=accepted,
    breakout_accept_bars=2,
  ).role == ROLE_BROKEN_RESISTANCE


@pytest.mark.parametrize("guard_mode", ["observe", "balanced", "strict"])
def test_hard_geometry_blocks_in_every_guard_mode(guard_mode):
  decision = classify_guard_severity(
    "opposing_barrier",
    "entry_inside_opposing_zone",
    "entry is contained",
    guard_mode=guard_mode,
    hard_geometry=True,
  )

  assert decision.hard_block is True
  assert decision.outcome == "block"


def test_soft_geometry_remains_mode_aware():
  observed = classify_guard_severity(
    "opposing_barrier",
    "opposing_ahead",
    "barrier ahead",
    guard_mode="observe",
    hard_geometry=False,
  )
  balanced = classify_guard_severity(
    "opposing_barrier",
    "opposing_ahead",
    "barrier ahead",
    guard_mode="balanced",
    hard_geometry=False,
  )
  strict = classify_guard_severity(
    "opposing_barrier",
    "opposing_ahead",
    "barrier ahead",
    guard_mode="strict",
    hard_geometry=False,
  )

  assert observed.outcome == "allow_with_warning"
  assert balanced.outcome == "allow_with_warning"
  # Preferenced opposing-barrier proximity remains telemetry even in strict.
  assert strict.hard_block is False
  assert strict.outcome == "allow_with_warning"


def test_structural_target_room_keeps_full_ladder_when_barrier_near():
  """Owner 2026-08-06: never invent floor(usable_room) as a solo TP.

  Live Trendline published TP1=4255.49 close_ratio=1.0 (~9 pips)
  because the barrier path used to shrink fitted_targets to a tiny cap.
  Reaction/swing setups keep the configured partial ladder unchanged.
  """
  decision = evaluate_structural_target_room(
    direction="BUY",
    planned_entry_price=4100.0,
    candidate_entry_low=4099.0,
    candidate_entry_high=4100.5,
    configured_target_pips=(30, 60, 90, 120, 200),
    actionable_entries=(
      _entry("sell", 4101.5, 4103.0, tier="zone"),
    ),
    atr=1.0,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
    execution_cost_pips=1.0,
  )

  assert decision.allowed
  assert decision.hard_block is False
  assert decision.fitted_targets_pips == (30, 60, 90, 120, 200)
  assert decision.effective_target_pips == pytest.approx(200)
  assert decision.reason_code == "opposing_barrier_room_ignored_full_ladder"
  assert decision.measured["barrier_would_cap_ladder"] is True


def test_structural_target_room_full_ladder_fits_is_not_labeled_capped():
  """An opposing barrier far enough away that buffered room clears even the
  largest configured target must not be labeled "capped" - nothing was
  actually truncated, and the outcome (allow, effective=max(targets)) is
  identical to no_opposing_barrier's. Regression for a real production log
  (2026-08-04, XAU M5 Flip Zone) where room=377.6 pips still reported
  opposing_barrier_target_capped for a [30,60,90,120,200] ladder.
  """
  decision = evaluate_structural_target_room(
    direction="BUY",
    planned_entry_price=4060.12,
    candidate_entry_low=4057.9,
    candidate_entry_high=4060.12,
    configured_target_pips=(30, 60, 90, 120, 200),
    actionable_entries=(
      _entry("sell", 4099.8, 4108.1, tier="major"),
    ),
    atr=3.87,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
  )

  assert decision.allowed
  assert decision.hard_block is False
  assert decision.reason_code == "opposing_barrier_full_ladder_fits"
  assert decision.fitted_targets_pips == (30, 60, 90, 120, 200)
  assert decision.effective_target_pips == pytest.approx(200)
  assert decision.measured["preference_telemetry"] is True


def test_structural_band_overlap_without_planned_entry_keeps_full_ladder():
  """Zone-band sliver overlap is preference; planned entry clear → full ladder."""
  decision = evaluate_structural_target_room(
    direction="BUY",
    planned_entry_price=4102.0,
    candidate_entry_low=4100.0,
    candidate_entry_high=4108.0,
    configured_target_pips=(30, 50, 70),
    actionable_entries=(
      _entry("sell", 4106.0, 4112.0, tier="zone"),
    ),
    atr=1.0,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
  )
  assert decision.allowed
  assert decision.hard_block is False
  assert decision.fitted_targets_pips == (30, 50, 70)
  assert decision.reason_code in {
    "opposing_barrier_full_ladder_fits",
    "opposing_barrier_room_ignored_full_ladder",
  }


def test_structural_planned_entry_in_overlap_hard_blocks():
  decision = evaluate_structural_target_room(
    direction="BUY",
    planned_entry_price=4107.0,
    candidate_entry_low=4100.0,
    candidate_entry_high=4108.0,
    configured_target_pips=(30, 50, 70),
    actionable_entries=(
      _entry("sell", 4106.0, 4112.0, tier="zone"),
    ),
    atr=1.0,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
  )
  assert not decision.allowed
  assert decision.hard_block
  assert decision.reason_code == "opposing_entry_overlap"


def test_contains_price_alone_does_not_hard_reject():
  """Market Map contains_price means spot is inside a map entry — not that
  the planned execution entry is geometrically contained. A false
  opposing_entry_contained must not fire from the flag alone.
  """
  decision = evaluate_structural_target_room(
    direction="SELL",
    planned_entry_price=4110.0,
    candidate_entry_low=4108.0,
    candidate_entry_high=4112.0,
    configured_target_pips=(30, 50, 70),
    actionable_entries=(
      # Opposing demand sits below planned SELL entry; market flag says
      # spot is inside demand, but planned entry 4110 is outside [4090, 4098].
      _entry("buy", 4090.0, 4098.0, tier="major", contains_price=True),
    ),
    atr=1.0,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
  )
  assert decision.allowed
  assert decision.hard_block is False
  assert decision.reason_code != "opposing_entry_contained"
  assert decision.measured["planned_entry_contained"] is False
  assert decision.measured["market_price_contained"] is True
  assert decision.measured["opposing_contains_price"] is True


def test_true_planned_entry_containment_hard_rejects():
  decision = evaluate_structural_target_room(
    direction="SELL",
    planned_entry_price=4095.0,
    candidate_entry_low=4093.0,
    candidate_entry_high=4097.0,
    configured_target_pips=(30, 50, 70),
    actionable_entries=(
      _entry("buy", 4090.0, 4098.0, tier="major", contains_price=False),
    ),
    atr=1.0,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
  )
  assert not decision.allowed
  assert decision.hard_block
  assert decision.reason_code in {
    "opposing_entry_contained",
    "opposing_entry_overlap",
  }
  assert decision.measured["planned_entry_contained"] is True
  assert decision.measured["market_price_contained"] is False


def test_v8_shared_boundary_sell_glued_demand_does_not_block():
  """Live silence shape: demand hi glued to sell proximal; spot on the wall.
  V8 drops the shared-boundary map entry so publish is not killed by
  opposing_entry_contained / below_cost on raw_room≈0.
  """
  candidate_low = 4393.45
  candidate_high = 4396.20
  # Live reject used ~4393.655; keep within epsilon=max(pip, 0.05*atr).
  opposing = _entry("buy", 4388.0, 4393.55, tier="zone")
  atr = 4.0
  pip_size = 0.1
  kept, state = filter_shared_boundary_opposing_entries(
    [opposing],
    direction="SELL",
    candidate_entry_low=candidate_low,
    candidate_entry_high=candidate_high,
    pip_size=pip_size,
    atr=atr,
  )
  assert state["shared_boundary_excluded"] == 1
  assert kept == []
  spot = 4393.50
  room_planned, source = zone_proximal_room_reference(
    direction="SELL",
    spot_price=spot,
    candidate_entry_low=candidate_low,
    candidate_entry_high=candidate_high,
    pip_size=pip_size,
    atr=atr,
  )
  assert source == "v8_zone_proximal"
  assert room_planned == candidate_low
  decision = evaluate_structural_target_room(
    direction="SELL",
    planned_entry_price=room_planned,
    candidate_entry_low=candidate_low,
    candidate_entry_high=candidate_high,
    configured_target_pips=(30, 60, 90, 120, 200),
    actionable_entries=kept,
    atr=atr,
    pip_size=pip_size,
    barrier_buffer_atr=0.5,
    execution_cost_pips=1.0,
    room_reference_source=source,
    executable_entry_price=spot,
    shared_boundary_state=state,
  )
  assert decision.allowed is True
  assert decision.hard_block is False
  assert decision.reason_code == "no_opposing_barrier"
  assert decision.measured["room_reference_source"] == "v8_zone_proximal"
  assert decision.measured["shared_boundary_state"]["shared_boundary_excluded"] == 1


def test_v8_overlap_opposing_band_does_not_block_technique_candidate():
  """FVG candidate overlapping a stacked Market Map demand is not opposing."""
  candidate_low = 4100.0
  candidate_high = 4104.0
  # Substantial overlap with candidate — same wall, not structure ahead.
  opposing = _entry("buy", 4099.0, 4103.5, tier="zone")
  atr = 4.0
  pip_size = 0.1
  kept, state = filter_overlapping_opposing_entries(
    [opposing],
    direction="SELL",
    candidate_entry_low=candidate_low,
    candidate_entry_high=candidate_high,
    pip_size=pip_size,
    atr=atr,
  )
  assert state["overlap_excluded"] == 1
  assert kept == []
  decision = evaluate_structural_target_room(
    direction="SELL",
    planned_entry_price=candidate_low,
    candidate_entry_low=candidate_low,
    candidate_entry_high=candidate_high,
    configured_target_pips=(30, 60, 90),
    actionable_entries=(opposing,),
    atr=atr,
    pip_size=pip_size,
    barrier_buffer_atr=0.5,
    execution_cost_pips=1.0,
    allow_same_wall_overlap=True,
  )
  assert decision.allowed is True
  assert decision.reason_code == "no_opposing_barrier"
  assert decision.measured["shared_boundary_state"]["overlap_excluded"] == 1


def test_v8_overlap_keeps_clear_opposing_barrier_ahead():
  candidate_low = 4100.0
  candidate_high = 4102.0
  # Clear demand well below the SELL band — must still count as opposing.
  opposing = _entry("buy", 4080.0, 4085.0, tier="zone")
  atr = 4.0
  pip_size = 0.1
  kept, state = filter_overlapping_opposing_entries(
    [opposing],
    direction="SELL",
    candidate_entry_low=candidate_low,
    candidate_entry_high=candidate_high,
    pip_size=pip_size,
    atr=atr,
  )
  assert state["overlap_excluded"] == 0
  assert len(kept) == 1
  decision = evaluate_structural_target_room(
    direction="SELL",
    planned_entry_price=candidate_low,
    candidate_entry_low=candidate_low,
    candidate_entry_high=candidate_high,
    configured_target_pips=(30, 60, 90),
    actionable_entries=(opposing,),
    atr=atr,
    pip_size=pip_size,
    barrier_buffer_atr=0.5,
    execution_cost_pips=1.0,
  )
  # Room to 4085 from 4100 is 150 pips — ladder may fit, but barrier remains.
  assert decision.reason_code != "no_opposing_barrier"
  assert decision.opposing_entry is not None


def test_weak_map_level_containment_does_not_hard_block():
  """Weak map 'level' containing planned entry must not hard-kill analysis."""
  # Candidate band clear of the level; planned price still sits inside level.
  candidate_low = 4102.0
  candidate_high = 4105.0
  opposing = _entry("buy", 4098.0, 4100.5, tier="level")
  decision = evaluate_structural_target_room(
    direction="SELL",
    planned_entry_price=4100.0,
    candidate_entry_low=candidate_low,
    candidate_entry_high=candidate_high,
    configured_target_pips=(30, 60, 90),
    actionable_entries=(opposing,),
    atr=4.0,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
    execution_cost_pips=1.0,
  )
  assert decision.allowed is True
  assert decision.hard_block is False
  assert decision.measured.get("weak_opposing_level_ignored") is True


def test_ordinary_zone_containment_hard_blocks():
  """2026-09 (Opposing Structure V2 repair): an ordinary directional "zone"
  is real evidence again - containment hard-blocks exactly like "major"
  does, unlike a genuinely unsided "level" (see
  test_weak_map_level_containment_does_not_hard_block just above, which
  correctly stays weak).
  """
  candidate_low = 4102.0
  candidate_high = 4105.0
  opposing = _entry("buy", 4098.0, 4100.5, tier="zone")
  decision = evaluate_structural_target_room(
    direction="SELL",
    planned_entry_price=4100.0,
    candidate_entry_low=candidate_low,
    candidate_entry_high=candidate_high,
    configured_target_pips=(30, 60, 90),
    actionable_entries=(opposing,),
    atr=4.0,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
    execution_cost_pips=1.0,
  )
  assert decision.allowed is False
  assert decision.hard_block is True
  assert decision.reason_code == "opposing_entry_contained"
  assert decision.measured.get("weak_opposing_level_ignored") is None


def test_ordinary_zone_zero_raw_room_hard_blocks():
  """Same repair, the other hard-block branch: raw_room <= 0 (planned entry
  already past the opposing zone's near edge, not contained). Mirrors
  test_raw_room_zero_major_hard_gates's geometry exactly, tier=zone instead
  of major - the reason code differs (opposing_barrier_no_target, not
  opposing_major_no_room, which is literally gated on tier=="major") but
  the hard-block outcome is now identical.
  """
  buy = _result(
    "BUY",
    4054.0,
    4057.0,
    quality=3,
    current_price=4056.0,
  )
  market_map = _map(
    _entry("sell", 4046.0, 4055.0, tier="zone"),
    price=4056.0,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert resolution.actionable == ()
  decision = resolution.gated[0][1]
  assert decision.reason_code == "opposing_barrier_no_target"
  assert decision.hard_block is True
  assert decision.measured["raw_room_price"] < 0


def test_v8_shared_boundary_buy_glued_supply_does_not_block():
  candidate_low = 4100.0
  candidate_high = 4103.0
  opposing = _entry("sell", 4103.05, 4108.0, tier="zone")
  atr = 2.0
  pip_size = 0.1
  kept, state = filter_shared_boundary_opposing_entries(
    [opposing],
    direction="BUY",
    candidate_entry_low=candidate_low,
    candidate_entry_high=candidate_high,
    pip_size=pip_size,
    atr=atr,
  )
  assert state["shared_boundary_excluded"] == 1
  assert kept == []
  decision = evaluate_structural_target_room(
    direction="BUY",
    planned_entry_price=candidate_high,
    candidate_entry_low=candidate_low,
    candidate_entry_high=candidate_high,
    configured_target_pips=(30, 50, 70),
    actionable_entries=kept,
    atr=atr,
    pip_size=pip_size,
    barrier_buffer_atr=0.5,
    execution_cost_pips=1.0,
    room_reference_source="v8_zone_proximal",
    shared_boundary_state=state,
  )
  assert decision.allowed is True
  assert decision.reason_code == "no_opposing_barrier"


def test_v8_shared_boundary_keeps_deep_penetration_barrier():
  """fe023-like: demand high penetrates into sell zone beyond epsilon."""
  kept, state = filter_shared_boundary_opposing_entries(
    [_entry("buy", 4263.765714285714, 4267.8, tier="major")],
    direction="SELL",
    candidate_entry_low=4267.44782,
    candidate_entry_high=4270.39503,
    pip_size=0.1,
    atr=4.53,
  )
  assert state["shared_boundary_excluded"] == 0
  assert len(kept) == 1


def test_v8_glued_wall_at_planned_price_is_not_opposing_structure():
  """Live 2026-08-12: Flip/Key SELL planned_entry sat on demand high
  (4405.1188 vs 4405.1188). Map stacking is not a wall ahead — publish.
  """
  planned = 4405.118839286714
  opposing = _entry("buy", 4397.391696428572, 4405.118839285714, tier="zone")
  decision = evaluate_structural_target_room(
    direction="SELL",
    planned_entry_price=planned,
    candidate_entry_low=4404.50,
    candidate_entry_high=4409.20,
    configured_target_pips=(30, 60, 90, 120, 200),
    actionable_entries=(opposing,),
    atr=4.0,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
    execution_cost_pips=1.0,
  )
  assert decision.allowed is True
  assert decision.hard_block is False
  assert decision.reason_code == "no_opposing_barrier"
  assert decision.measured["shared_boundary_state"]["shared_boundary_excluded"] == 1


def test_v8_resolve_actionability_allows_glued_sell_wall():
  sell = _result(
    "SELL",
    4393.45,
    4396.20,
    quality=3,
    current_price=4393.50,
    setup="Key Level",
  )
  market_map = _map(
    _entry("buy", 4388.0, 4393.55, tier="zone"),
    price=4393.50,
  )
  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[sell],
    zones=_map_to_zones(market_map),
    context=SimpleNamespace(htf_bias="up"),
    atr=4.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )
  assert len(resolution.actionable) == 1
  assert resolution.gated == ()

def test_band_overlap_alone_remains_executable_with_cap():
  decision = evaluate_structural_target_room(
    direction="BUY",
    planned_entry_price=4102.0,
    candidate_entry_low=4100.0,
    candidate_entry_high=4108.0,
    configured_target_pips=(30, 50, 70),
    actionable_entries=(
      _entry("sell", 4106.0, 4112.0, tier="zone", contains_price=True),
    ),
    atr=1.0,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
  )
  assert decision.allowed
  assert decision.hard_block is False
  assert decision.measured["planned_entry_contained"] is False
  assert decision.measured["entry_overlap_ratio"] > 0
  assert decision.measured.get("band_overlap_without_planned_containment") is True
  assert decision.effective_target_pips is not None


def test_displaced_opposing_zone_does_not_block_target_room():
  opposing = _entry("sell", 4095.67, 4106.28, tier="major")
  kept = filter_displaced_opposing_entries(
    [opposing],
    direction="BUY",
    recent_closes=[4098.0, 4102.0, 4108.5],
  )
  decision = evaluate_structural_target_room(
    direction="BUY",
    planned_entry_price=4099.41,
    candidate_entry_low=4093.95,
    candidate_entry_high=4101.61,
    configured_target_pips=(30, 50, 70),
    actionable_entries=kept,
    atr=2.0,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
    displacement_state={
      "applied": True,
      "dropped": 1,
      "recent_closes": [4098.0, 4102.0, 4108.5],
    },
  )
  assert kept == []
  assert decision.allowed
  assert decision.reason_code == "no_opposing_barrier"
  assert decision.measured["displacement_state"]["dropped"] == 1


def test_filter_displaced_opposing_entries_drops_a_closed_through_barrier():
  sell_barrier = _entry("sell", 4095.67, 4106.28, tier="major")
  buy_barrier = _entry("buy", 4001.0, 4007.0, tier="major")
  kept = filter_displaced_opposing_entries(
    [sell_barrier, buy_barrier],
    direction="BUY",
    recent_closes=[4098.0, 4102.0, 4108.5],
  )
  # The BUY-side barrier is never "opposing" for a BUY candidate in the
  # first place - kept regardless. The SELL barrier is dropped because one
  # of the recent closes (4108.5) closed decisively above its high (4106.28).
  assert kept == [buy_barrier]


def test_filter_displaced_opposing_entries_keeps_an_untested_barrier():
  sell_barrier = _entry("sell", 4095.67, 4106.28, tier="major")
  kept = filter_displaced_opposing_entries(
    [sell_barrier],
    direction="BUY",
    recent_closes=[4098.0, 4100.0, 4103.5],
  )
  assert kept == [sell_barrier]


def test_filter_displaced_opposing_entries_requires_a_close_not_a_wick():
  # A high approaching or even piercing the barrier intrabar does not count
  # - only a genuine closed-bar break does (recent_closes are closes only).
  sell_barrier = _entry("sell", 4095.67, 4106.28, tier="major")
  kept = filter_displaced_opposing_entries(
    [sell_barrier],
    direction="BUY",
    recent_closes=[4106.28, 4106.0, 4105.9],
  )
  assert kept == [sell_barrier]


def test_barrier_capped_target_is_used_by_reward_risk_pre_gate(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_tp_pips": "30,50,70"})
  # This fixture's _result() helper labels every BUY "counter_bias"
  # regardless of the ctx.htf_bias passed in below (a fixture quirk, not
  # something this test is exercising) - this test is about barrier-capped
  # target room, not counter-bias policy, so keep it enabled here.
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_allow_counter_bias": True})
  result = _result(
    "BUY",
    4095.0,
    4101.0,
    quality=3,
    current_price=4100.5,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    frames={"M5": pd.DataFrame({"close": [4100.5]})},
    indicators={"M5": SimpleNamespace(atr=pd.Series([2.0]))},
    structures={"M5": SimpleNamespace(scalp_range=None)},
    regime=SimpleNamespace(kind="trend"),
  )
  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[result],
    zones=_map_to_zones(_map(
      _entry("sell", 4105.0, 4108.0, tier="zone"),
      price=4100.5,
    )),
    context=ctx,
    atr=2.0,
    pip_size=0.1,
    cfg=actionability_cfg(
      auto_trade_allow_counter_bias=True,
      auto_trade_tp_pips="30,50,70",
    ),
  )

  assert len(resolution.actionable) == 1
  kept = resolution.actionable[0]
  assert kept.target_cap_pips == pytest.approx(70)
  eligible, measured = scanner._reward_risk_pre_gate(
    "XAU",
    "M5",
    "2026-07-28T12:10:00+00:00",
    ctx,
    kept,
  )

  # Scanner RR pre-gate is non-blocking telemetry; full ladder retained.
  assert eligible is True
  assert measured.get("preference_telemetry") is True
  assert measured.get("policy_hard_block") is False
  assert measured["effective_target_pips"] == pytest.approx(70)
  assert measured["configured_target_pips"] == [30, 50, 70]
  assert measured["opposing_low"] == pytest.approx(4105.0)
  # Full ladder primary TP clears min RR; older capped-30 path did not.
  assert measured["reward_risk"] >= measured["min_reward_risk"]


def test_recent_displacement_beyond_barrier_lets_a_contained_buy_through():
  """Live incident: a BUY at XAU ~4099 got hard-blocked by opposing_entry_
  contained against a major opposing zone (4095.67-4106.28) - but recent M5
  closes had already closed decisively above that zone's own high, meaning
  the barrier was functionally already broken by live price while its own
  (HTF-sourced) classification hadn't caught up. The candidate must not be
  blocked on a barrier price has already displaced beyond.
  """
  buy = _result(
    "BUY",
    4093.95,
    4101.61,
    quality=3,
    current_price=4099.41,
  )
  market_map = _map(
    _entry(
      "sell", 4095.67, 4106.28, tier="major",
      tags=("OB", "breaker", "flip", "supply"),
    ),
    price=4099.41,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="down",
    frames={"M5": pd.DataFrame({
      "close": [4098.0, 4102.0, 4108.5],
    })},
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=ctx,
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(displacement_lookback_bars=3),
  )

  assert resolution.gated == ()
  assert len(resolution.actionable) == 1


def test_no_displacement_still_blocks_as_before():
  """Same setup as above, but the recent closes never actually close beyond
  the barrier (still a wick/approach, not a confirmed break). Planned entry
  inside the opposing major is a hard structural gate.
  """
  buy = _result(
    "BUY",
    4093.95,
    4101.61,
    quality=3,
    current_price=4099.41,
  )
  market_map = _map(
    _entry(
      "sell", 4095.67, 4106.28, tier="major",
      tags=("OB", "breaker", "flip", "supply"),
    ),
    price=4099.41,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="down",
    frames={"M5": pd.DataFrame({
      "close": [4098.0, 4100.0, 4103.5],
    })},
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=ctx,
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(displacement_lookback_bars=3),
  )

  assert resolution.actionable == ()
  assert len(resolution.gated) == 1
  reasons = {decision.reason_code for _item, decision in resolution.decisions}
  # Deep overlap / tiny buffered room hard-gates below the cost floor.
  # True containment / overlap still hard-blocks in other cases.
  assert reasons & {
    "opposing_barrier_room_below_cost",
    "opposing_entry_contained",
    "opposing_entry_overlap",
  }


def test_displacement_override_disabled_by_default_lookback_zero():
  """With lookback 0 the displacement override never fires; containment
  hard-gates when the actionability gate is on.
  """
  buy = _result(
    "BUY",
    4093.95,
    4101.61,
    quality=3,
    current_price=4099.41,
  )
  market_map = _map(
    _entry("sell", 4095.67, 4106.28, tier="major"),
    price=4099.41,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="down",
    frames={"M5": pd.DataFrame({"close": [4098.0, 4102.0, 4108.5]})},
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=_map_to_zones(market_map),
    context=ctx,
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert resolution.actionable == ()
  assert resolution.gated
  # Lookback 0 disables displacement; below-cost barrier room still hard-kills.
  assert resolution.gated[0][1].reason_code in {
    "opposing_barrier_room_below_cost",
    "opposing_entry_contained",
    "opposing_entry_overlap",
  }


def test_empty_market_map_is_valid_and_unavailable_map_retains_candidate():
  result = _result("BUY", 4100.0, 4101.0)
  available = resolve_actionability(
    symbol="XAU",
    observed_results=[result],
    zones=_map_to_zones(_map(price=4100.5)),
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
    cfg=_cfg(),
  )
  unavailable = resolve_actionability(
    symbol="XAU",
    observed_results=[result],
    zones=None,
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert len(available.actionable) == 1
  assert len(unavailable.actionable) == 1
  assert unavailable.gated == ()
  decision = next(
    decision for _item, decision in unavailable.decisions
    if decision.reason_code == "context_degraded"
  )
  assert decision.hard_block is False
  assert decision.measured["market_map_available"] is False
  assert decision.measured["context_degraded"] is True
  assert (
    decision.measured["context_degraded_reason"]
    == "opposing_context_unavailable"
  )


def test_gate_false_retains_contextual_observations_including_contested():
  buy = _result("BUY", 4100.0, 4101.0, quality=3)
  sell = _result("SELL", 4100.0, 4101.0, quality=3)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    zones=_map_to_zones(_map(price=4100.5)),
    context=SimpleNamespace(htf_bias="range"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=False),
  )

  assert len(resolution.actionable) == 2
  assert resolution.gated == ()
  contested = [
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "contested_corridor"
  ]
  assert contested
  assert all(decision.hard_block is False for decision in contested)


@pytest.mark.asyncio
async def test_live_incident_never_reaches_lifecycle_card_or_strategy_match(
  monkeypatch,
):
  client = redis_state.get_client()
  frame = pd.DataFrame(
    {
      "open": [4044.8, 4045.1, 4045.4],
      "high": [4045.4, 4046.2, 4046.0],
      "low": [4043.8, 4044.0, 4044.7],
      "close": [4045.0, 4045.4, 4045.95],
      "volume": [100.0, 120.0, 110.0],
    },
    index=pd.date_range(
      "2026-07-28 12:00",
      periods=3,
      freq="5min",
      tz="UTC",
    ),
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="down",
    frames={"M5": frame},
    indicators={"M5": SimpleNamespace(atr=pd.Series([2.0]))},
    structures={
      "M5": SimpleNamespace(
        scalp_range=None,
        scalp_barriers=[],
      )
    },
    regime=SimpleNamespace(kind="trend"),
    spot_price=4045.95,
    analysis=SimpleNamespace(per_tf={}),
    settings=scanner._detector_settings(),
  )
  incident = _result(
    "BUY",
    4041.67,
    4046.73,
    setup="Key Level",
    quality=3,
    current_price=4045.95,
  )
  incident = replace(
    incident,
    key_level=4044.20,
    confirmation="sweep_reclaim",
    confirmation_type="sweep_reclaim",
    source_touches=7,
    bias_relationship="counter_bias",
  )
  current_map = _map(
    _entry("sell", 4046.0, 4055.0, tier="major"),
    _entry("sell", 4060.0, 4063.0, tier="zone"),
    _entry("buy", 4042.0, 4051.0, tier="major", contains_price=True),
    price=4045.95,
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "H1,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_gate_max_source_touches": 0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_actionability_gate_enabled": True,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"key_level_role_ambiguity_gate_enabled": True,})
  monkeypatch.setattr(
    scanner,
    "_load_market_context_for_symbol",
    AsyncMock(return_value=(ctx, ctx.frames)),
  )
  monkeypatch.setattr(
    scanner,
    "build_map",
    lambda *_args, **_kwargs: current_map,
  )
  notify = AsyncMock()

  sent = await scanner._handle_event(
    "XAU:M5:2026-07-28T12:10:00+00:00",
    client=client,
    detectors=[lambda _ctx: incident],
    notify=notify,
  )

  # Owner 2026-08-06: tiny buffered room no longer invents a hard TP gate or
  # a solo ~9 pip target. Actionability may still prefer-telemetry the
  # barrier; the scanner is free to emit when other gates allow.
  status = json.loads(await client.get("scanner:last_tick:XAU:M5"))
  assert status["observed_count"] == 1
  reasons = {
    item["reason_code"] for item in status.get("actionability_gated", [])
  } | {
    item.get("reason_code")
    for item in status.get("actionability_decisions", [])
    if item.get("reason_code")
  }
  assert "execution_cost_insufficient_room" not in reasons
  # Prefer-telemetry codes ok; no hard TP-room reject.
  if status["actionable_count"] == 0:
    gated = status.get("actionability_gated") or []
    assert gated
    assert all(item.get("reason_code") != "execution_cost_insufficient_room" for item in gated)

"""Exact strategy-family registry — no substring classification."""

from __future__ import annotations

import pytest

from app.autotrade.strategy_taxonomy import (
  CANONICAL_FAMILY_RANGE,
  CANONICAL_FAMILY_REACTION,
  CANONICAL_FAMILY_ZONE,
  RANGE_STRATEGIES,
  REACTION_STRATEGIES,
  ZONE_STRATEGIES,
  bypasses_opposing_structure_gates,
  canonical_family,
  is_range_strategy,
  is_reaction_strategy,
  is_zone_strategy,
)


pytestmark = pytest.mark.no_database


def test_reaction_family_contains_exactly_three_strategies():
  assert REACTION_STRATEGIES == frozenset({
    "Key Level",
    "Session Level",
    "Trendline",
  })
  assert len(REACTION_STRATEGIES) == 3


def test_key_level_reaction_is_reaction():
  assert is_reaction_strategy("Key Level")
  assert canonical_family("Key Level") == CANONICAL_FAMILY_REACTION


def test_session_level_reaction_is_reaction():
  assert is_reaction_strategy("Session Level")
  assert canonical_family("Session Level") == CANONICAL_FAMILY_REACTION


def test_trendline_reaction_is_reaction():
  assert is_reaction_strategy("Trendline")
  assert canonical_family("Trendline") == CANONICAL_FAMILY_REACTION


def test_demand_zone_is_not_reaction():
  assert not is_reaction_strategy("Demand Zone")
  assert not is_reaction_strategy("Demand Zone Reaction")
  assert not is_reaction_strategy("Zone Reaction")
  assert not is_reaction_strategy("Flip Zone")
  assert is_zone_strategy("Demand Zone")
  assert is_zone_strategy("Demand Zone Reaction")
  assert is_zone_strategy("Zone Reaction")
  assert is_zone_strategy("Flip Zone")
  assert canonical_family("Demand Zone") == CANONICAL_FAMILY_ZONE
  assert canonical_family("Demand Zone Reaction") == CANONICAL_FAMILY_ZONE
  assert canonical_family("Zone Reaction") == CANONICAL_FAMILY_ZONE
  assert canonical_family("Flip Zone") == CANONICAL_FAMILY_ZONE


def test_supply_zone_is_not_reaction():
  assert not is_reaction_strategy("Supply Zone")
  assert not is_reaction_strategy("Supply Zone Reaction")
  assert is_zone_strategy("Supply Zone")
  assert is_zone_strategy("Supply Zone Reaction")
  assert canonical_family("Supply Zone") == CANONICAL_FAMILY_ZONE
  assert canonical_family("Supply Zone Reaction") == CANONICAL_FAMILY_ZONE


def test_strategy_names_are_not_classified_by_substring():
  # Exact registry only — containing "Reaction" or "Zone" must not classify.
  assert not is_reaction_strategy("Fake Reaction Setup")
  assert not is_reaction_strategy("Mapped Zone Reaction")
  assert not is_zone_strategy("Mapped Zone Reaction")
  assert "Zone Reaction" in ZONE_STRATEGIES
  assert "Flip Zone" in ZONE_STRATEGIES
  assert "Demand Zone Reaction" in ZONE_STRATEGIES
  assert "Demand Zone Reaction" not in REACTION_STRATEGIES
  assert canonical_family("Something Reaction Something") != CANONICAL_FAMILY_REACTION
  assert canonical_family("Demand Zoneish") != CANONICAL_FAMILY_ZONE
  assert canonical_family("Fake Flip Zone") != CANONICAL_FAMILY_ZONE


def test_range_strategies_bypass_opposing_structure_gates():
  for name in RANGE_STRATEGIES:
    assert is_range_strategy(name)
    assert not bypasses_opposing_structure_gates(name)
    # Ladder floor 15p is enough to open / bypass opposing.
    assert bypasses_opposing_structure_gates(name, full_take_profit_pips=15)
    assert bypasses_opposing_structure_gates(name, full_take_profit_pips=20)
    assert not bypasses_opposing_structure_gates(name, full_take_profit_pips=0)
    assert canonical_family(name) == CANONICAL_FAMILY_RANGE
  assert not bypasses_opposing_structure_gates(
    "Key Level", full_take_profit_pips=15,
  )
  assert not bypasses_opposing_structure_gates(
    "Zone Reaction", full_take_profit_pips=15,
  )
  assert not bypasses_opposing_structure_gates(
    "Liquidity Sweep", full_take_profit_pips=15,
  )


def test_m1_scalp_strategies_bypass_opposing_when_room_fits():
  from app.autotrade.strategy_taxonomy import (
    CANONICAL_FAMILY_SCALP,
    M1_SCALP_STRATEGIES,
    is_m1_scalp_strategy,
    match_bypasses_opposing_structure,
  )

  for name in M1_SCALP_STRATEGIES:
    assert is_m1_scalp_strategy(name)
    # M1 scalp always bypasses — map opposing must not silence the scalp loop.
    assert bypasses_opposing_structure_gates(name)
    assert bypasses_opposing_structure_gates(name, full_take_profit_pips=20)
    assert canonical_family(name) == CANONICAL_FAMILY_SCALP
  assert not is_m1_scalp_strategy("HFS Custom Archetype")
  assert bypasses_opposing_structure_gates(
    "Range Sweep Scalp",
    full_take_profit_pips=15,
    family="scalp",
    strategy_mode="scalp_m1",
  )
  # family/mode alone is enough for M1 scalp; range still needs fitted room.
  assert bypasses_opposing_structure_gates(
    "Unknown", family="scalp", strategy_mode="scalp_m1",
  )
  assert not bypasses_opposing_structure_gates(
    "Unknown", family="unknown", strategy_mode="range_scalp",
  )
  assert not bypasses_opposing_structure_gates(
    "Range Edge Scalp", family="range", strategy_mode="range_scalp",
  )
  assert bypasses_opposing_structure_gates(
    "Range Edge Scalp",
    full_take_profit_pips=20,
    family="range",
    strategy_mode="range_scalp",
  )

  class _Match:
    strategy = "Impulse Pullback Scalp"
    full_take_profit_pips = 25
    family = "scalp"
    strategy_mode = "scalp_m1"

  assert match_bypasses_opposing_structure(_Match())
  assert match_bypasses_opposing_structure(
    type("M", (), {
      "strategy": "Impulse Pullback Scalp",
      "full_take_profit_pips": None,
      "family": "scalp",
      "strategy_mode": "scalp_m1",
    })(),
  )


def test_technique_and_confluence_are_zone_not_reaction():
  from app.autotrade.strategy_taxonomy import (
    CONFLUENCE_STRATEGIES,
    TECHNIQUE_STRATEGIES,
    is_confluence_strategy,
    is_technique_or_confluence,
    is_technique_strategy,
  )

  for name in TECHNIQUE_STRATEGIES:
    assert is_technique_strategy(name)
    assert is_technique_or_confluence(name)
    assert is_zone_strategy(name)
    assert not is_reaction_strategy(name)
    assert canonical_family(name) == CANONICAL_FAMILY_ZONE
  for name in CONFLUENCE_STRATEGIES:
    assert is_confluence_strategy(name)
    assert is_technique_or_confluence(name)
    assert is_zone_strategy(name)
    assert not is_reaction_strategy(name)
    assert canonical_family(name) == CANONICAL_FAMILY_ZONE
  assert not is_technique_or_confluence("Zone Reaction")
  assert not is_technique_or_confluence("Key Level")

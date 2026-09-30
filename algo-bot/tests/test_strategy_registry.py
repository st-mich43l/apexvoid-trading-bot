"""Strategy registry parity and enable-path coverage."""

from __future__ import annotations

import pytest

from app.analysis.detectors import LIVE_DETECTOR_REGISTRY
from app.autotrade.execution_policy import strategy_family as legacy_strategy_family
from app.autotrade.strategy_registry import (
  STRATEGY_BY_DETECTOR_KEY,
  STRATEGY_BY_NAME,
  activation_archetype,
  canonical_family,
  location_archetype,
  lookup_row,
  resolve_enable_setting,
  resolve_strategy_enabled,
  strategy_family,
  strategy_mode_enabled,
)
from app.autotrade.strategy_taxonomy import canonical_family as legacy_canonical
from app.analysis.entry_location import location_archetype as legacy_location
from app.core.config import runtime_config


pytestmark = pytest.mark.no_database

_KNOWN_STRATEGIES = tuple(sorted(STRATEGY_BY_NAME))


@pytest.mark.parametrize("registration", LIVE_DETECTOR_REGISTRY)
def test_live_detector_registry_has_strategy_row(registration):
  assert registration.name in STRATEGY_BY_DETECTOR_KEY


@pytest.mark.parametrize("strategy", _KNOWN_STRATEGIES)
def test_registry_strategy_family_matches_legacy(strategy):
  assert strategy_family(strategy) == legacy_strategy_family(strategy)


@pytest.mark.parametrize("strategy", _KNOWN_STRATEGIES)
def test_registry_canonical_family_matches_legacy(strategy):
  assert canonical_family(strategy) == legacy_canonical(strategy)


@pytest.mark.parametrize("strategy", _KNOWN_STRATEGIES)
def test_registry_location_archetype_matches_legacy(strategy):
  assert location_archetype(strategy) == legacy_location(strategy)


@pytest.mark.parametrize("strategy", _KNOWN_STRATEGIES)
def test_enable_setting_resolves(strategy):
  row = lookup_row(strategy)
  assert row is not None
  resolve_enable_setting(row.enable_setting, runtime_config)


@pytest.mark.parametrize(
  ("strategy", "setting"),
  [
    ("Supply Demand", "strategies.technique.sd.enabled"),
    ("Order Block", "strategies.technique.ob.enabled"),
    ("FVG", "strategies.technique.fvg.enabled"),
    ("iFVG", "strategies.technique.ifvg.enabled"),
    ("CRT", "strategies.technique.crt.enabled"),
  ],
)
def test_technique_publishers_use_per_technique_enable(strategy, setting):
  row = lookup_row(strategy)
  assert row is not None
  assert row.enable_setting == setting
  assert resolve_strategy_enabled(row, runtime_config) == strategy_mode_enabled(
    strategy, runtime_config,
  )


def test_supply_demand_is_not_routed_to_demand_reaction_toggle():
  row = lookup_row("Supply Demand")
  assert row is not None
  assert row.enable_setting == "strategies.technique.sd.enabled"
  assert "demand" not in row.enable_setting

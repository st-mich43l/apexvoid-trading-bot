import pytest

from app.core.environment_options import (
  ENVIRONMENT_OPTION_CONTRACTS,
  parse_bool,
  resolve_environment_options,
)

pytestmark = pytest.mark.no_database


def _resolved(environment: dict[str, str], canonical: str):
  return next(
    option
    for option in resolve_environment_options(environment)
    if option.canonical_name == canonical
  )


def test_canonical_false_only_resolves_false():
  option = _resolved(
    {"AUTO_TRADE_MAPPED_ZONE_ENABLED": "false"},
    "AUTO_TRADE_MAPPED_ZONE_ENABLED",
  )
  assert option.resolved_value is False
  assert option.source_name == "AUTO_TRADE_MAPPED_ZONE_ENABLED"
  assert option.warnings == ()


def test_legacy_true_only_warns_and_resolves_true():
  option = _resolved(
    {"AUTO_TRADE_MARKET_MAP_STRATEGY_ENABLED": "true"},
    "AUTO_TRADE_MAPPED_ZONE_ENABLED",
  )
  assert option.resolved_value is True
  assert option.source_name == "AUTO_TRADE_MARKET_MAP_STRATEGY_ENABLED"
  assert option.warnings == (
    "deprecated_variable:AUTO_TRADE_MARKET_MAP_STRATEGY_ENABLED",
  )


def test_equal_canonical_and_legacy_accepts_canonical_with_warning():
  option = _resolved({
    "AUTO_TRADE_MAPPED_ZONE_ENABLED": "false",
    "AUTO_TRADE_MARKET_MAP_STRATEGY_ENABLED": "0",
  }, "AUTO_TRADE_MAPPED_ZONE_ENABLED")
  assert option.resolved_value is False
  assert option.source_name == "AUTO_TRADE_MAPPED_ZONE_ENABLED"
  assert option.aliases_present == (
    "AUTO_TRADE_MARKET_MAP_STRATEGY_ENABLED",
  )


def test_conflicting_canonical_and_legacy_fails():
  with pytest.raises(ValueError, match="conflicting environment aliases"):
    resolve_environment_options({
      "AUTO_TRADE_MAPPED_ZONE_ENABLED": "false",
      "AUTO_TRADE_MARKET_MAP_STRATEGY_ENABLED": "true",
    })


def test_conflicting_strategy_match_legacy_aliases_fail():
  with pytest.raises(ValueError, match="AUTO_TRADE_STRATEGY_MATCH_ENABLED"):
    resolve_environment_options({
      "AUTO_TRADE_STRATEGY_BRIDGE_ENABLED": "true",
      "AUTO_TRADE_FORMING_GATE_ENABLED": "false",
    })


@pytest.mark.parametrize("value", ["maybe", "", "enabled", "2"])
def test_invalid_boolean_fails(value):
  with pytest.raises(ValueError, match="invalid boolean"):
    parse_bool(value)


def test_all_alias_choices_are_declared_once():
  names = [item.canonical_name for item in ENVIRONMENT_OPTION_CONTRACTS]
  assert len(names) == len(set(names))


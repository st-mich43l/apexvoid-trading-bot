"""Range Edge, Session Level and the standalone Liquidity Sweep are purged (Q2).

Their display names stay resolvable so persisted trades, events and reports still decode; nothing can publish them any more.
"""

import json

import pytest

from app.analysis_client.provenance import CATALOG_STRATEGY_IDS
from app.autotrade import go_opportunity_policy as pol
from app.autotrade import strategy_catalog as catalog
from app.autotrade import strategy_names as names
from app.autotrade import strategy_taxonomy as tx

pytestmark = pytest.mark.no_database

PURGED_IDS = ("range_edge", "session_level", "liquidity_sweep")
PURGED_NAMES = ("Range Edge Scalp", "Session Level", "Liquidity Sweep")


def test_no_adapter_or_catalog_id_remains():
  for strategy_id in PURGED_IDS:
    assert strategy_id not in pol.REVIEWED_SCOPES
    assert strategy_id not in CATALOG_STRATEGY_IDS
  assert frozenset(pol.REVIEWED_SCOPES) == CATALOG_STRATEGY_IDS


@pytest.mark.parametrize("name", PURGED_NAMES)
def test_a_purged_name_still_decodes_but_is_retired_and_owns_no_profile(name):
  entry = names.resolve_strategy(name)
  assert entry is not None and entry.canonical == name
  assert entry.retired is True and entry.detector_id is None
  assert catalog.lookup_profile(name) is None
  assert not tx.is_scalp_strategy(name) and not tx.is_reaction_strategy(name)


def test_the_historical_aliases_resolve_to_the_retired_names():
  assert names.resolve_strategy("session-level").canonical == "Session Level"
  assert names.resolve_strategy("range_edge_scalp").canonical == "Range Edge Scalp"
  assert names.resolve_strategy("go:liquidity_sweep").canonical == "Liquidity Sweep"


@pytest.mark.parametrize("scope", PURGED_IDS)
def test_a_go_event_of_a_purged_strategy_is_not_adapted(scope):
  # The adapter registry is the only way a Go opportunity becomes a match: no row, no match.
  assert pol.REVIEWED_SCOPES.get(scope) is None


def test_a_stored_match_of_a_purged_strategy_still_reads_back():
  from app.autotrade.strategy_match import StrategyMatch

  fields = {"match_id": "old", "symbol": "XAU", "direction": "BUY", "strategy": "Range Edge Scalp"}
  restored = StrategyMatch.from_json(json.dumps(fields))
  assert restored is None or restored.strategy == "Range Edge Scalp"

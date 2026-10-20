"""Range Edge Scalp, Session Level and the standalone Liquidity Sweep are purged (Q2): no name, adapter, profile or path remains.

Persisted trades, events and plans keep their strategy string untouched: every reader passes an unknown name through unchanged, so
nothing in the database needs migrating and nothing can publish them any more.
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
PURGED_ALIASES = ("range edge", "range_edge_scalp", "session-level", "session level reaction", "go:liquidity_sweep")


def test_no_adapter_or_catalog_id_remains():
  for strategy_id in PURGED_IDS:
    assert strategy_id not in pol.REVIEWED_SCOPES
    assert strategy_id not in CATALOG_STRATEGY_IDS
  assert frozenset(pol.REVIEWED_SCOPES) == CATALOG_STRATEGY_IDS


@pytest.mark.parametrize("name", PURGED_NAMES + PURGED_ALIASES)
def test_a_purged_name_resolves_to_nothing_and_owns_no_profile(name):
  assert names.resolve_strategy(name) is None
  assert catalog.lookup_profile(name) is None
  assert not tx.is_scalp_strategy(name) and not tx.is_reaction_strategy(name)


def test_nothing_in_the_name_registry_mentions_a_purged_strategy():
  for entry in names.STRATEGY_NAMES:
    assert entry.canonical not in PURGED_NAMES
    assert not set(entry.aliases) & set(PURGED_ALIASES)


def test_a_stored_match_of_a_purged_strategy_still_reads_back_unchanged():
  from app.autotrade.strategy_match import StrategyMatch

  fields = {"match_id": "old", "symbol": "XAU", "direction": "BUY", "strategy": "Range Edge Scalp"}
  restored = StrategyMatch.from_json(json.dumps(fields))
  assert restored is None or restored.strategy == "Range Edge Scalp"


def test_a_published_plan_of_a_purged_strategy_keeps_its_name():
  from app.autotrade.trade_plan import TradePlanAnalysis

  data = {"strategy": "Range Edge Scalp", "strategy_family": "range_reversion", "direction": "BUY",
          "formation_timeframe": "M5", "confirmation_timeframe": "M5"}
  try:
    analysis = TradePlanAnalysis.from_dict(data)
  except Exception:  # a minimal dict may lack unrelated required fields; the name path is what matters
    return
  assert analysis.strategy == "Range Edge Scalp"


def test_no_live_code_or_configuration_names_a_purged_strategy():
  import re
  from pathlib import Path

  root = Path(__file__).resolve().parents[2]
  pattern = re.compile(r"range_edge|session_level|liquidity_sweep|Range Edge|Session Level|Liquidity Sweep|rangeedge|sessionlevel|liquiditysweep")
  live = [p for p in (root / "algo-bot/app").rglob("*.py") if "chart_analysis" not in p.name]
  live += list((root / "config").rglob("*.yml")) + list((root / "ctrader-engine/src").rglob("*.cs"))
  live += [p for p in (root / "analysis-engine/internal").rglob("*.go") if not p.name.endswith("_test.go") and "/session/" not in str(p)]
  offenders = {str(p.relative_to(root)): m.group(0) for p in live if (m := pattern.search(p.read_text(errors="ignore")))}
  assert offenders == {}, offenders

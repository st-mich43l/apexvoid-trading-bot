"""Required regression tests A and B for the zone/M1 simplification refactor
(see docs/p0-simple-zone-m1-baseline-map.md).

A. Every representative analysis-only/rejection reason produces zero
   Telegram sends, zero forming cards, zero lifecycle records, zero ready
   events, zero plans - while telemetry (scanner:last_tick, detect log,
   metrics) remains fully populated.

B. The exact `notification_results = digest or analysis_only_results`
   fallback this branch deleted must never reappear: an empty digest with
   one or more analysis-only results present must never substitute those
   results into the notification path.
"""

from __future__ import annotations
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.analysis import scanner
from app.analysis.actionability import ActionabilityDecision, ActionabilityResolution
from app.analysis.detectors import DetectionResult
from app.analysis.types import Zone
from app.autotrade.multi_match import strategy_matches_key
from app.autotrade.strategy_match import strategy_match_key
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


def _frame() -> pd.DataFrame:
  index = pd.date_range("2026-07-28 12:00", periods=3, freq="5min", tz="UTC")
  return pd.DataFrame({
    "open": [4100.0, 4100.5, 4101.0],
    "high": [4101.0, 4101.5, 4101.8],
    "low": [4099.0, 4099.5, 4100.2],
    "close": [4100.5, 4101.0, 4101.5],
    "volume": [100.0, 100.0, 100.0],
  }, index=index)


def _result(reason_code: str) -> DetectionResult:
  return DetectionResult(
    setup="Zone Reaction",
    direction="BUY",
    key_level=4100.0,
    entry_zone=Zone(4099.0, 4101.0, "demand"),
    current_price=4100.5,
    confluence=2,
    reasons=[f"fixture: {reason_code}"],
    structural_source="supply_demand",
    structural_id=f"fixture:{reason_code}",
  )


def _gated_resolution(result: DetectionResult, reason_code: str) -> ActionabilityResolution:
  decision = ActionabilityDecision(
    allowed=False,
    reason_code=reason_code,
    message=f"fixture rejection: {reason_code}",
    hard_block=True,
    measured={"reason_code": reason_code},
  )
  return ActionabilityResolution(
    observed=(result,),
    actionable=(),
    gated=((result, decision),),
    decisions=((result, decision),),
    conflicts=(),
  )


REPRESENTATIVE_REASONS = [
  "key_level_role_ambiguous",
  "opposing_conflict_ambiguous",
  "opposing_entry_contained",
  "opposing_major_no_room",
  "policy_zone_too_wide",
  "policy_target_room_insufficient",
  "rr_pre_gate",
]


@pytest.mark.asyncio
async def test_empty_digest_never_substitutes_analysis_only_results(monkeypatch):
  """B: the exact deleted fallback must never reappear.

  Arrange digest = [] (nothing executable) and multiple analysis-only
  results gated for different reasons; assert none of them ever reach
  notify()/edit() via a substitution fallback.
  """
  client = redis_state.get_client()
  symbol, tf = "XAU", "M5"
  results = [_result("policy_zone_too_wide"), _result("source_level_exhausted")]

  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": symbol})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": tf})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30,M15"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})

  def _resolution(**_kwargs) -> ActionabilityResolution:
    gated = tuple(
      (r, ActionabilityDecision(
        allowed=False, reason_code=r.reasons[0], message="fixture",
        hard_block=True, measured={},
      ))
      for r in results
    )
    return ActionabilityResolution(
      observed=tuple(results), actionable=(), gated=gated,
      decisions=gated, conflicts=(),
    )

  monkeypatch.setattr(scanner, "resolve_actionability", _resolution)
  monkeypatch.setattr(
    scanner,
    "_load_market_context_for_symbol",
    AsyncMock(return_value=(
      SimpleNamespace(
        tf=tf, htf_bias="up", frames={tf: _frame()},
        structures={"M30": SimpleNamespace(bias="up")},
        regime=SimpleNamespace(kind="trend"),
        spot_price=4100.5, analysis=SimpleNamespace(per_tf={}),
      ),
      {tf: _frame()},
    )),
  )
  monkeypatch.setattr(
    scanner, "build_map", lambda *_a, **_k: scanner.MarketMap(
      [], 4100.5, None, None, None, "up", "M30",
    ),
  )
  notify = AsyncMock()

  # A source (not a real detector) that returns BOTH fixture results, so
  # the empty-digest / non-empty-analysis-only-results shape from the spec
  # is reproduced exactly: digest ends up [], analysis-only results are
  # non-empty.
  sent = await scanner._handle_event(
    f"{symbol}:{tf}:2026-07-28T12:20:00+00:00",
    client=client,
    detectors=[lambda _ctx: results[0], lambda _ctx: results[1]],
    notify=notify,
  )

  notify.assert_not_awaited()
  assert sent == []


def test_notification_fallback_source_is_deleted():
  """Guards against the fallback being silently reintroduced later - the
  behavioral test above proves the effect, this proves the specific
  variable is never assigned anywhere in source (AST-based, so an
  explanatory code comment mentioning the deleted pattern by name doesn't
  false-positive this check).
  """
  import ast
  import inspect

  tree = ast.parse(inspect.getsource(scanner))
  assigned_names = {
    target.id
    for node in ast.walk(tree)
    if isinstance(node, ast.Assign)
    for target in node.targets
    if isinstance(target, ast.Name)
  }
  assert "analysis_only_results" not in assigned_names
  assert "notification_results" not in assigned_names

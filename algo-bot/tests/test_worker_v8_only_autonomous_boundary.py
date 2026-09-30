"""Proves the autonomous cycle never re-acquires a V6 publish call site.

Per docs/adr-trade-plan-v8-cutover.md Section (Legacy autonomous removal),
TradePlan V8 is the sole autonomous order-creation path: worker.py's
autonomous per-bar entry point (_handle_event, which drives the
scanner-routed, private M1 range, and trend intents) must never call
_publish_strategy_match, _publish_candidate, or _publish_trend_candidate -
the three V6 candidate-building functions - regardless of any config value.
Those functions have been deleted; this source-text check stays as a tripwire
so a future edit that reintroduces an autonomous call site fails immediately,
mirroring
ctrader-engine's TradePlanExecutionEngineDependencyTests pattern for the
same boundary on the C# side.
"""

from __future__ import annotations

import inspect
import re

import pytest

from app.autotrade import worker

pytestmark = pytest.mark.no_database

_FORBIDDEN_CALLS = (
  "_publish_strategy_match(",
  "_publish_candidate(",
  "_publish_trend_candidate(",
)


def _strip_comments(source: str) -> str:
  return "\n".join(
    re.sub(r"#.*$", "", line) for line in source.split("\n")
  )


@pytest.mark.parametrize("forbidden_call", _FORBIDDEN_CALLS)
def test_autonomous_cycle_never_calls_v6_publish_functions(forbidden_call):
  source = _strip_comments(inspect.getsource(worker._handle_event))
  assert forbidden_call not in source


def test_publish_trade_plan_v8_no_longer_gates_on_contract_mode():
  source = inspect.getsource(worker._publish_trade_plan_v8)
  assert "contract_mode" not in source
  assert "auto_trade_contract_mode" not in source

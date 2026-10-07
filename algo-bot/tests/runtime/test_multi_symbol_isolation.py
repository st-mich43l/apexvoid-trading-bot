"""Multi-symbol Python registry / rollout isolation tests."""

from __future__ import annotations

import pytest

from app.runtime.instruments import InstrumentRollout
from app.core.config import runtime_config
from app.core.symbols import pip_for
from app.runtime.instrument_registry import (
  InstrumentRuntimeError,
  build_instrument_runtime_registry,
)
from app.runtime.rollout_gates import (
  permits_analysis,
  permits_broker_execution,
  permits_candidate_publication,
  permits_feed,
)


pytestmark = pytest.mark.no_database


def test_registry_production_live_instruments_are_the_declared_ones():
  registry = build_instrument_runtime_registry(runtime_config)
  assert sorted(ctx.instrument_id for ctx in registry.live_instruments()) == [
    "EURUSD", "GBPJPY", "GBPUSD", "USDJPY", "XAU",
  ]
  assert registry.get("XAUUSD").instrument_id == "XAU"


def test_unknown_pip_fails_closed():
  with pytest.raises(KeyError):
    pip_for("NO_SUCH_SYMBOL")


def test_rollout_matrix_isolation():
  # Synthetic contexts via mapping, independent of the production registry.
  assert permits_feed(InstrumentRollout.FEED_ONLY)
  assert not permits_analysis(InstrumentRollout.FEED_ONLY)
  assert not permits_candidate_publication(InstrumentRollout.FEED_ONLY)
  assert permits_analysis(InstrumentRollout.ANALYSIS_ONLY)
  assert not permits_candidate_publication(InstrumentRollout.ANALYSIS_ONLY)
  assert permits_candidate_publication(InstrumentRollout.PAPER)
  assert not permits_broker_execution(InstrumentRollout.PAPER)
  assert permits_broker_execution(InstrumentRollout.LIVE)


def test_registry_rejects_unknown_symbol():
  registry = build_instrument_runtime_registry(runtime_config)
  with pytest.raises(InstrumentRuntimeError, match="unknown"):
    registry.get("NOPE")

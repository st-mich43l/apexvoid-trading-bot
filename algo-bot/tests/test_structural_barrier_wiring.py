"""Key Level structural repair Phase 2: wiring StructuralBarrierBook into
the live opposing-structure gates (scanner actionability).

Phase 1 (PR #523) built StructuralBarrierBook but left it shadow-only. This
file tests the wiring: the opposing_entries bypass param on
resolve_actionability, scanner.py's barrier-book-backed entry builder, and the
opposing_zone_present telemetry three-state fix.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.analysis import actionability, scanner
from app.analysis.actionability import resolve_actionability
from app.analysis.types import Zone
from app.autotrade.structural_target_room import ZoneOpposingEntry

pytestmark = pytest.mark.no_database


def _demand(low: float, high: float, **kwargs) -> Zone:
  return Zone(bottom=low, top=high, side="demand", **kwargs)


def _supply(low: float, high: float, **kwargs) -> Zone:
  return Zone(bottom=low, top=high, side="supply", **kwargs)


def _tf(zones: list[Zone], atr: float = 3.0) -> SimpleNamespace:
  return SimpleNamespace(zones=zones, atr=pd.Series([atr]))


# ---------------------------------------------------------------------------
# scanner._structural_barrier_opposing_entries
# ---------------------------------------------------------------------------

def test_scanner_builds_multi_timeframe_entries_from_per_tf(monkeypatch):
  analysis = SimpleNamespace(per_tf={
    "M5": _tf([_demand(4494.0, 4497.0, score=15.0)]),
    "M15": _tf([_demand(4495.0, 4498.0, score=10.0)]),
    "H1": _tf([_supply(4520.0, 4523.0, score=20.0)]),
  })

  entries = scanner._structural_barrier_opposing_entries(analysis, symbol="XAU")

  assert entries is not None
  sides = sorted(entry.side for entry in entries)
  assert sides == ["buy", "sell"]
  # The M5 and M15 demand zones overlap and must merge into one barrier
  # (same-side merge - the operation the Market Map purge dropped).
  buy = next(entry for entry in entries if entry.side == "buy")
  assert (buy.lo, buy.hi) == (4494.0, 4498.0)


def test_scanner_returns_none_without_per_tf():
  analysis = SimpleNamespace(per_tf={})
  assert scanner._structural_barrier_opposing_entries(analysis, symbol="XAU") is None


# ---------------------------------------------------------------------------
# resolve_actionability(opposing_entries=...)
# ---------------------------------------------------------------------------

def test_resolve_actionability_uses_opposing_entries_when_given(monkeypatch):
  # If opposing_entries is honored, zone_opposing_entries (deriving from
  # raw `zones`) must never be called - a spy proves the bypass actually
  # takes effect rather than silently falling through to the old path.
  spy = Mock()
  monkeypatch.setattr(
    actionability, "zone_opposing_entries",
    lambda *a, **k: spy(*a, **k) or (),
  )
  entries = (
    ZoneOpposingEntry(side="sell", lo=4100.0, hi=4102.0, tier="major"),
  )

  resolve_actionability(
    symbol="XAU",
    observed_results=[],
    zones=[_supply(4100.0, 4102.0)],
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
    opposing_entries=entries,
  )

  spy.assert_not_called()


def test_resolve_actionability_falls_through_without_opposing_entries(monkeypatch):
  spy = Mock(return_value=())
  monkeypatch.setattr(
    actionability, "zone_opposing_entries",
    lambda *a, **k: spy(*a, **k) or (),
  )

  resolve_actionability(
    symbol="XAU",
    observed_results=[],
    zones=[_supply(4100.0, 4102.0)],
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
  )

  spy.assert_called_once()


# ---------------------------------------------------------------------------
# opposing_zone_present telemetry (True/False/None three-state fix)
# ---------------------------------------------------------------------------

def test_opposing_zone_present_false_when_room_check_ran_and_found_nothing():
  from app.analysis.detectors import DetectionResult
  from app.analysis.types import Zone as _Zone

  buy = DetectionResult(
    setup="Key Level",
    direction="BUY",
    key_level=4089.0,
    entry_zone=_Zone(bottom=4088.0, top=4090.0, side="demand"),
    current_price=4089.5,
    confluence=2,
    reasons=["test fixture"],
    provisional_targets_pips=(30, 60),
    mode="reaction",
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    zones=[],
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
  )

  result = resolution.actionable[0]
  assert result.opposing_zone_present is False

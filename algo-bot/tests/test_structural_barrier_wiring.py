"""Key Level structural repair Phase 2: wiring StructuralBarrierBook into
the live opposing-structure gates (scanner actionability + TradePlan room
check) and restoring the fixed-RR ladder's opposing-wall room cap.

Phase 1 (PR #523) built StructuralBarrierBook but left it shadow-only. This
file tests the actual wiring: the new opposing_entries bypass param on
resolve_actionability, scanner.py's and worker.py's barrier-book-backed
entry builders, the htf_zones fallback (frames lacking full M5/M15/H1
coverage must never silently drop opposing structure the caller already
computed), the restored fixed-RR room cap, and the opposing_zone_present
telemetry three-state fix.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from app.analysis import actionability, scanner
from app.analysis.actionability import resolve_actionability
from app.analysis.types import Zone
from app.autotrade import worker
from app.autotrade.structural_target_room import ZoneOpposingEntry
from tests.configuration.canonical_fixtures import install_runtime_overrides
from tests.test_publish_trade_plan_v8 import _confirm_setup, _m1_trigger_bar, _match
from app.persistence import redis_state

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
# worker._structural_barrier_zone_book / _structural_barrier_entries
# ---------------------------------------------------------------------------

def _fake_frame() -> pd.DataFrame:
  index = pd.date_range("2026-01-01", periods=3, freq="5min", tz="UTC")
  return pd.DataFrame(
    {"open": [1.0, 1.0, 1.0], "high": [1.0, 1.0, 1.0],
     "low": [1.0, 1.0, 1.0], "close": [1.0, 1.0, 1.0]},
    index=index,
  )


def test_structural_barrier_zone_book_pools_every_available_timeframe(monkeypatch):
  monkeypatch.setattr(worker, "atr_series", lambda *a, **k: pd.Series([3.0]))
  monkeypatch.setattr(worker, "displacement", lambda *a, **k: [object()])
  monkeypatch.setattr(
    worker, "supply_demand",
    lambda frame, legs: [_demand(4494.0, 4497.0, score=15.0)],
  )
  monkeypatch.setattr(worker, "mark_mitigation", lambda zones, frame: zones)
  frames = {"M5": _fake_frame(), "M15": _fake_frame()}
  # H1 deliberately absent - must be skipped, not raise.

  per_tf = worker._structural_barrier_zone_book(frames, symbol="XAU")

  assert set(per_tf) == {"M5", "M15"}
  assert per_tf["M5"].zones[0].low == 4494.0


def test_structural_barrier_zone_book_skips_timeframes_with_no_legs(monkeypatch):
  monkeypatch.setattr(worker, "displacement", lambda *a, **k: [])
  frames = {"M5": _fake_frame()}

  per_tf = worker._structural_barrier_zone_book(frames, symbol="XAU")

  assert per_tf == {}


def test_structural_barrier_entries_builds_from_zone_book(monkeypatch):
  monkeypatch.setattr(worker, "atr_series", lambda *a, **k: pd.Series([3.0]))
  monkeypatch.setattr(worker, "displacement", lambda *a, **k: [object()])
  monkeypatch.setattr(
    worker, "supply_demand",
    lambda frame, legs: [_supply(4100.0, 4102.0, score=15.0)],
  )
  monkeypatch.setattr(worker, "mark_mitigation", lambda zones, frame: zones)
  frames = {"M5": _fake_frame()}

  entries = worker._structural_barrier_entries(frames, symbol="XAU")

  assert len(entries) == 1
  assert entries[0].side == "sell"
  assert (entries[0].lo, entries[0].hi) == (4100.0, 4102.0)


def test_structural_barrier_entries_empty_without_frames():
  assert worker._structural_barrier_entries({}, symbol="XAU") == ()


# ---------------------------------------------------------------------------
# _publish_trade_plan_v8: htf_zones fallback + fixed-RR room cap wiring
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_news(monkeypatch):
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))


@pytest.mark.asyncio
async def test_htf_zones_still_used_when_frames_lacks_multi_timeframe_coverage(
  monkeypatch,
):
  """A caller that only loaded M1 (like most existing V8 tests) but already
  computed htf_zones from its own M15 read must not have that opposing
  structure silently discarded just because the barrier book itself found
  nothing (frames has no M5/M15/H1 keys for it to pool from).
  """
  install_runtime_overrides(monkeypatch, legacy_overrides={})
  client = redis_state.get_client()
  match = _match(match_id="match-htf-fallback", thesis_id="thesis-htf-fallback")
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4089.0, ts=int(time.time()), fresh=True, bid=4088.9, ask=4089.1,
  )
  htf_zones = [Zone(bottom=4096.0, top=4098.0, side="supply", score=10.0)]
  spy = Mock()
  real = worker.evaluate_structural_target_room

  def _spy_room(**kwargs):
    spy(actionable_entries=tuple(kwargs.get("actionable_entries") or ()))
    return real(**kwargs)

  monkeypatch.setattr(worker, "evaluate_structural_target_room", _spy_room)

  await worker._publish_trade_plan_v8(
    client, "XAU", spot, match,
    frames={"M1": _m1_trigger_bar()},
    htf_zones=htf_zones,
  )

  seen_entries = spy.call_args.kwargs["actionable_entries"]
  assert any(
    entry.lo == 4096.0 and entry.hi == 4098.0 for entry in seen_entries
  ), "htf_zones opposing structure must still reach the room check"


@pytest.mark.asyncio
async def test_fixed_rr_room_cap_threads_a_real_value_when_barrier_book_enabled(
  monkeypatch,
):
  """Before this wiring, available_target_room_pips was always None (PR
  #499 stopped passing it) - the fixed_rr ladder sized with zero regard
  for real opposing structure. With the flag on, a real opposing barrier
  must now produce a real (non-None) available_target_room_pips.
  """
  install_runtime_overrides(monkeypatch, legacy_overrides={})
  client = redis_state.get_client()
  match = _match(
    match_id="match-fixed-rr-cap", thesis_id="thesis-fixed-rr-cap",
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4089.0, ts=int(time.time()), fresh=True, bid=4088.9, ask=4089.1,
  )
  htf_zones = [Zone(bottom=4096.0, top=4098.0, side="supply", score=10.0)]
  spy = Mock()
  real = worker.evaluate_execution_policy

  def _spy_policy(*args, **kwargs):
    spy(available_target_room_pips=kwargs.get("available_target_room_pips"))
    return real(*args, **kwargs)

  monkeypatch.setattr(worker, "evaluate_execution_policy", _spy_policy)

  await worker._publish_trade_plan_v8(
    client, "XAU", spot, match,
    frames={"M1": _m1_trigger_bar()},
    htf_zones=htf_zones,
  )

  calls = [
    call.kwargs["available_target_room_pips"] for call in spy.call_args_list
  ]
  assert any(value is not None for value in calls), (
    "with a real opposing barrier present, at least one execution-policy "
    "evaluation must receive a real room cap, not always None"
  )


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

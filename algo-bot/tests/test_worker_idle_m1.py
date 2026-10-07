"""Idle M1 worker ticks skip leftover analysis when Redis has no match."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.analysis_client.provenance import CATALOG_TAG, GO_ORIGIN_TAG
from app.autotrade import worker
from app.autotrade.go_live_opportunities import go_live_opportunities_key
from app.autotrade.multi_match import serialize_matches, strategy_matches_key
from app.autotrade.route_outcome import route_outcome_key
from app.autotrade.strategy_match import (
  STRATEGY_MATCH_VERSION,
  StrategyMatch,
  strategy_match_id,
)
from app.persistence import redis_state
from tests.support.canonical_fixtures import install_runtime_overrides


pytestmark = pytest.mark.no_database


def _frame() -> pd.DataFrame:
  index = pd.date_range("2026-07-20", periods=20, freq="1min", tz="UTC")
  return pd.DataFrame({
    "open": [4016.8] * 20,
    "high": [4017.4] * 20,
    "low": [4016.2] * 20,
    "close": [4017.0] * 20,
    "volume": [100.0] * 20,
  }, index=index)


@pytest.mark.asyncio
async def test_go_only_idle_m1_skips_python_frames_and_writes_thin_last_gate(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_symbols": "XAU"})
  publish = AsyncMock(return_value=None)
  monkeypatch.setattr(worker, "_publish_trade_plan_v8", publish)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  source = AsyncMock()
  source.window = AsyncMock(return_value=_frame())
  monkeypatch.setattr(
    worker,
    "_load_spot",
    AsyncMock(return_value=worker.AutoTradeSpot(4017.2, now, True)),
  )

  result = await worker._handle_event(
    f"XAU:M1:{now}", source=source, client=client,
  )

  assert result is None
  publish.assert_not_awaited()
  source.window.assert_not_awaited()
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["state"] == "idle_no_match"
  assert status["gate_source"] == "idle_no_match"


# ---- Go is the sole automatic technical-opportunity producer -----------------

GO_TAGS = (GO_ORIGIN_TAG, f"{CATALOG_TAG}supply", "go_opportunity:opp")


def _base_match(now: int) -> StrategyMatch:
  return StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id="",
    symbol="XAU",
    source_tf="M5",
    event_ts=str(now - 60),
    issued_at=now - 60,
    expires_at=now + 600,
    strategy="Supply Demand",
    strategy_mode="with_bias",
    direction="SELL",
    key_level=4017.5,
    entry_low=4017.0,
    entry_high=4018.0,
    current_price=4017.2,
    confluence=3,
    reasons=("supply",),
    atr=4.0,
    structure_swing=4019.0,
    targets_pips=(100,),
  )


def _python_match(now: int) -> StrategyMatch:
  """A legacy scanner match with its real (event-derived) identity and no Go tag."""
  base = _base_match(now)
  return replace(base, match_id=strategy_match_id(
    base.symbol, base.source_tf, base.event_ts, base.strategy,
    base.direction, base.entry_low, base.entry_high,
  ))


def _go_match(now: int) -> StrategyMatch:
  return replace(
    _base_match(now), match_id="go_opp", structural_zone_id="zone-go", tags=GO_TAGS,
  )


def _worker_cycle(monkeypatch, *, now: int):
  install_runtime_overrides(
    monkeypatch,
    {
      "analysis.technical_authority.consumer_enabled": True,
      "strategies.matching.multiple_matches_enabled": True,
      "analysis.scanner.window": 500,
    },
    legacy_overrides={
      "auto_trade_enabled": True,
      "auto_trade_symbols": "XAU",
      "auto_trade_strategy_match_enabled": True,
    },
  )
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  monkeypatch.setattr(
    worker,
    "_load_spot",
    AsyncMock(return_value=worker.AutoTradeSpot(4017.2, now, True)),
  )
  source = AsyncMock()
  source.window = AsyncMock(return_value=_frame())
  return source


class _ReachedFullPass(Exception):
  """Stops _handle_event right where it starts arbitrating its matches."""


def _capture_full_pass(monkeypatch) -> list[list[str]]:
  seen: list[list[str]] = []

  def capture(matches, **_kwargs):
    seen.append(sorted(item.match_id for item in matches))
    raise _ReachedFullPass

  monkeypatch.setattr(worker, "select_primary", capture)
  return seen


@pytest.mark.asyncio
async def test_go_mode_stale_python_match_cannot_produce_a_plan(monkeypatch):
  """A Python scanner match left in Redis at cutover is invisible to the sweep:
  no preflight, no publish, no route outcome - the cycle is the ordinary idle
  one."""
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  source = _worker_cycle(monkeypatch, now=now)
  stale = _python_match(now)
  await client.set(strategy_matches_key("XAU"), serialize_matches([stale]))
  assert [m.match_id for m in await worker._load_strategy_matches(client, "XAU")] == [stale.match_id]
  publish = AsyncMock(return_value="plan-should-never-exist")
  admit = AsyncMock(return_value=None)
  monkeypatch.setattr(worker, "_publish_trade_plan_v8", publish)
  monkeypatch.setattr(worker, "_admit_strategy_intent_for_cycle", admit)

  result = await worker._handle_event(f"XAU:M1:{now}", source=source, client=client)

  assert result is None
  publish.assert_not_awaited()
  admit.assert_not_awaited()
  assert await client.get(route_outcome_key("XAU", stale.match_id)) is None
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["state"] == "idle_no_match"


@pytest.mark.asyncio
async def test_go_mode_sweep_keeps_go_origin_matches(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  source = _worker_cycle(monkeypatch, now=now)
  stale, go = _python_match(now), _go_match(now)
  await client.set(strategy_matches_key("XAU"), serialize_matches([stale, go]))
  seen = _capture_full_pass(monkeypatch)

  with pytest.raises(_ReachedFullPass):
    await worker._handle_event(f"XAU:M1:{now}", source=source, client=client)

  assert seen == [[go.match_id]]


@pytest.mark.asyncio
async def test_go_mode_reconciles_match_that_is_no_longer_in_live_book(monkeypatch):
  """A restart withdrawal is projected before it can look executable again."""
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  source = _worker_cycle(monkeypatch, now=now)
  stale = _go_match(now)
  await client.set(strategy_matches_key("XAU"), serialize_matches([stale]))
  await client.set(
    go_live_opportunities_key("XAU"),
    json.dumps({"symbol": "XAU", "generated_at": now, "ids": []}),
  )

  result = await worker._handle_event(
    f"XAU:M1:{now}", source=source, client=client,
  )

  assert result is None
  retained = await worker._load_strategy_matches(client, "XAU")
  assert len(retained) == 1
  assert retained[0].match_id == stale.match_id
  assert retained[0].arbitration_status == "suppressed"
  assert retained[0].arbitration_reason_code == "go_opportunity_not_live"


@pytest.mark.asyncio
async def test_go_mode_filters_a_retained_zonewatch_match_even_via_ready_match_id(monkeypatch):
  """Go is the sole automatic technical-opportunity producer: the explicit
  ready_match_id path ZoneWatch activation used to publish through
  (try_publish_executable_signal -> _handle_event(ready_match_id=...)) is now
  filtered the same as the closed-bar sweep. Production no longer installs the
  ZoneWatch cutover in go mode at all (app.main / install_zone_execution_cutover),
  so this can only happen from a leftover retained-zone record; it must still
  never reach arbitration. See test_go_mode_sweep_keeps_go_origin_matches for
  the closed-bar-sweep half of the same invariant."""
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  source = _worker_cycle(monkeypatch, now=now)
  retained, go = _python_match(now), _go_match(now)
  await client.set(strategy_matches_key("XAU"), serialize_matches([retained, go]))
  _capture_full_pass(monkeypatch)

  result = await worker._handle_event(
    f"XAU:M1:{now}", source=source, client=client,
    ready_match_id=retained.match_id,
  )

  assert result is None  # idle: no Go-origin match to arbitrate, nothing published

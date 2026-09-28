"""Idle M1 worker ticks skip leftover analysis when Redis has no match."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from app.analysis_client.authority import CATALOG_TAG, EPOCH_TAG, GO_ORIGIN_TAG
from app.autotrade import worker
from app.autotrade.gate import AutoScalpDecision
from app.autotrade.multi_match import serialize_matches, strategy_matches_key
from app.autotrade.route_outcome import route_outcome_key
from app.autotrade.strategy_match import (
  STRATEGY_MATCH_VERSION,
  StrategyMatch,
  strategy_match_id,
)
from app.persistence import redis_state
from tests.configuration.canonical_fixtures import install_runtime_overrides


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
async def test_idle_m1_skips_pandas_gates_and_writes_thin_last_gate(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_symbols": "XAU"})
  publish = AsyncMock(return_value=None)
  gate = Mock()
  trend_fn = Mock()
  regime_fn = Mock()
  monkeypatch.setattr(worker, "_publish_trade_plan_v8", publish)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  source = AsyncMock()
  source.window = AsyncMock(return_value=_frame())
  monkeypatch.setattr(
    worker,
    "_load_spot",
    AsyncMock(return_value=worker.AutoTradeSpot(4017.2, now, True)),
  )
  monkeypatch.setattr(worker, "evaluate_auto_scalp_gate", gate)
  monkeypatch.setattr(worker, "evaluate_trend_gate", trend_fn)
  monkeypatch.setattr(worker, "classify_regime", regime_fn)

  result = await worker._handle_event(
    f"XAU:M1:{now}", source=source, client=client,
  )

  assert result is None
  publish.assert_not_awaited()
  gate.assert_not_called()
  trend_fn.assert_not_called()
  regime_fn.assert_not_called()
  source.window.assert_awaited_once()
  assert source.window.await_args.args[1] == "M1"
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["state"] == "idle_no_match"
  assert status["gate_source"] == "idle_no_match"


@pytest.mark.asyncio
async def test_mapped_thesis_rearm_does_not_bool_coerce_m1_frame(monkeypatch):
  """Prod 2026-08-17: frames.get('M1') or frames.get('M1') crashed every minute."""
  client = redis_state.get_client()
  df = _frame()
  called = {}

  async def fake_advance(client_arg, *, symbol, m1, atr):
    called["symbol"] = symbol
    called["rows"] = len(m1)
    called["atr"] = atr

  monkeypatch.setattr(worker, "_advance_mapped_thesis_rearms", fake_advance)
  await worker._advance_mapped_thesis_rearms_from_frames(
    client, symbol="XAU", frames={"M1": df},
  )
  assert called["symbol"] == "XAU"
  assert called["rows"] == 20
  assert called["atr"] > 0


# ---- Go is the sole automatic technical-opportunity producer (mode=go) -------

GO_TAGS = (GO_ORIGIN_TAG, f"{CATALOG_TAG}supply", f"{EPOCH_TAG}1", "go_opportunity:opp")


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


def _worker_cycle(monkeypatch, *, mode: str, now: int):
  install_runtime_overrides(
    monkeypatch,
    {
      "analysis.technical_authority.mode": mode,
      "analysis.technical_authority.consumer_enabled": mode != "python",
      "strategies.matching.multiple_matches_enabled": True,
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

  monkeypatch.setattr(worker, "dedupe_matches", capture)
  monkeypatch.setattr(
    worker, "evaluate_auto_scalp_gate",
    lambda *a, **k: AutoScalpDecision("waiting_for_box"),
  )
  monkeypatch.setattr(
    worker, "_resolve_worker_range",
    AsyncMock(return_value=(AutoScalpDecision("waiting_for_box"), None, {})),
  )
  return seen


@pytest.mark.asyncio
async def test_go_mode_stale_python_match_cannot_produce_a_plan(monkeypatch):
  """A Python scanner match left in Redis at cutover is invisible to the sweep:
  no preflight, no publish, no route outcome, and none of the private
  Python scalp/regime/trend pass runs - the cycle is the ordinary idle one."""
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  source = _worker_cycle(monkeypatch, mode="go", now=now)
  stale = _python_match(now)
  await client.set(strategy_matches_key("XAU"), serialize_matches([stale]))
  assert [m.match_id for m in await worker._load_strategy_matches(client, "XAU")] == [stale.match_id]
  publish = AsyncMock(return_value="plan-should-never-exist")
  admit = AsyncMock(return_value=None)
  gate, trend_fn, regime_fn = Mock(), Mock(), Mock()
  monkeypatch.setattr(worker, "_publish_trade_plan_v8", publish)
  monkeypatch.setattr(worker, "_admit_strategy_intent_for_cycle", admit)
  monkeypatch.setattr(worker, "evaluate_auto_scalp_gate", gate)
  monkeypatch.setattr(worker, "evaluate_trend_gate", trend_fn)
  monkeypatch.setattr(worker, "classify_regime", regime_fn)

  result = await worker._handle_event(f"XAU:M1:{now}", source=source, client=client)

  assert result is None
  publish.assert_not_awaited()
  admit.assert_not_awaited()
  gate.assert_not_called()
  trend_fn.assert_not_called()
  regime_fn.assert_not_called()
  assert await client.get(route_outcome_key("XAU", stale.match_id)) is None
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["state"] == "idle_no_match"


@pytest.mark.asyncio
async def test_python_mode_still_evaluates_the_same_match(monkeypatch):
  """Control for the test above: the filter is what hides it, not the fixture."""
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  source = _worker_cycle(monkeypatch, mode="python", now=now)
  stale, go = _python_match(now), _go_match(now)
  await client.set(strategy_matches_key("XAU"), serialize_matches([stale, go]))
  seen = _capture_full_pass(monkeypatch)

  with pytest.raises(_ReachedFullPass):
    await worker._handle_event(f"XAU:M1:{now}", source=source, client=client)

  assert seen == [sorted([stale.match_id, go.match_id])]


@pytest.mark.asyncio
async def test_go_mode_sweep_keeps_go_origin_matches(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  source = _worker_cycle(monkeypatch, mode="go", now=now)
  stale, go = _python_match(now), _go_match(now)
  await client.set(strategy_matches_key("XAU"), serialize_matches([stale, go]))
  seen = _capture_full_pass(monkeypatch)

  with pytest.raises(_ReachedFullPass):
    await worker._handle_event(f"XAU:M1:{now}", source=source, client=client)

  assert seen == [[go.match_id]]


@pytest.mark.asyncio
async def test_go_mode_leaves_zone_watch_direct_publish_path_alone(monkeypatch):
  """ZoneWatch activation publishes a retained zone through
  try_publish_executable_signal -> _handle_event(ready_match_id=...). Filtering
  that path would strand every pending ZoneWatch setup, so it is not filtered."""
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  source = _worker_cycle(monkeypatch, mode="go", now=now)
  retained, go = _python_match(now), _go_match(now)
  await client.set(strategy_matches_key("XAU"), serialize_matches([retained, go]))
  seen = _capture_full_pass(monkeypatch)

  with pytest.raises(_ReachedFullPass):
    await worker._handle_event(
      f"XAU:M1:{now}", source=source, client=client,
      ready_match_id=retained.match_id,
    )

  assert seen == [[retained.match_id]]

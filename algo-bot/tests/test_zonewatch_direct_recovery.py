"""ZoneWatch direct-publish recovery — no READY-stream consumer required."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.autotrade import worker
from app.autotrade import zone_execution_cutover as cutover
from app.autotrade.strategy_match import StrategyMatch


pytestmark = pytest.mark.no_database


def _match() -> StrategyMatch:
  return StrategyMatch(
    version=1,
    match_id="setup-1",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-30T06:00:00+00:00",
    issued_at=1_785_390_000,
    expires_at=1_785_390_420,
    strategy="Key Level",
    strategy_mode="with_bias",
    direction="SELL",
    key_level=4114.5,
    entry_low=4113.0,
    entry_high=4116.0,
    current_price=4114.5,
    confluence=3,
    reasons=("supply rejection",),
    atr=4.0,
    structure_swing=4116.0,
    targets_pips=(300,),
    family="key_level",
    structural_source="key_level",
    confluence_zone_id="zone-1",
    structural_zone_id="zone-1",
    structural_zone_low=4113.0,
    structural_zone_high=4116.0,
    touch_bar_ts="1785390000",
    confirmation_bar_ts="1785390300",
    reaction_type="rejection_choch",
  )


@pytest.mark.asyncio
async def test_zonewatch_direct_publish_recovers_after_transient_failure(
  monkeypatch,
):
  """ZoneWatch direct path: first publish throws, fallback watches; retry
  publishes once and reconciles without stranding or duplicating.
  """
  match = _match()
  ensure = AsyncMock()
  monkeypatch.setattr(cutover, "_ensure_published_root_card", ensure)
  attempts = {"n": 0}

  async def flaky_then_ok(*_args, **_kwargs):
    attempts["n"] += 1
    if attempts["n"] == 1:
      raise RuntimeError("transient publish failure")
    return worker.PublishResult(
      status=worker.PUBLISH_STATUS_PUBLISHED,
      plan_id="v8:setup-1",
      reason_code="candidate_published",
      zone_id="zone-1",
      setup_id="setup-1",
    )

  monkeypatch.setattr(cutover, "_ORIGINAL_DIRECT_PUBLISH", flaky_then_ok)
  monkeypatch.setattr(
    worker,
    "resolve_existing_v8_state",
    AsyncMock(side_effect=[
      SimpleNamespace(already_published=False, plan_id=None),
      SimpleNamespace(already_published=True, plan_id="v8:setup-1"),
    ]),
  )

  cutover._PUBLISHED_SETUP_IDS.discard(match.match_id)
  first = await cutover._safe_direct_publish(
    object(), match, symbol="XAU", event_ts=match.event_ts,
  )
  assert first.status == worker.PUBLISH_STATUS_REMAINED_WATCHING
  assert first.reason_code == "direct_publish_failed_durable_fallback"
  assert match.match_id not in cutover._PUBLISHED_SETUP_IDS

  second = await cutover._safe_direct_publish(
    object(), match, symbol="XAU", event_ts=match.event_ts,
  )
  assert second.status in {
    worker.PUBLISH_STATUS_PUBLISHED,
    worker.PUBLISH_STATUS_DUPLICATE_RECONCILED,
  }
  assert match.match_id in cutover._PUBLISHED_SETUP_IDS
  assert attempts["n"] == 2
  ensure.assert_awaited()

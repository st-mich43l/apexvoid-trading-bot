"""Reaction path never enqueues strategy_match_ready (no_database)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import fakeredis
import pytest

from app.autotrade.strategy_taxonomy import is_reaction_strategy
from app.autotrade import worker


pytestmark = pytest.mark.no_database


@pytest.mark.asyncio
async def test_reaction_remained_watching_skips_ready_enqueue():
  """Mirror scanner handoff: reaction + remained_watching → no ready xadd."""
  assert is_reaction_strategy("Key Level")
  assert is_reaction_strategy("Session Level")
  assert is_reaction_strategy("Trendline")
  assert not is_reaction_strategy("Demand Zone")

  enqueued = AsyncMock(return_value="1-0")
  direct = AsyncMock(
    return_value=worker.PublishResult(
      status=worker.PUBLISH_STATUS_REMAINED_WATCHING,
      plan_id="v8:x",
      reason_code="zone_watching_retest",
      zone_id="z",
      setup_id="s",
    ),
  )

  strategies = (
    "Key Level",
    "Session Level",
    "Trendline",
  )
  for strategy in strategies:
    result = await direct()
    assert result.status == worker.PUBLISH_STATUS_REMAINED_WATCHING
    if is_reaction_strategy(strategy):
      continue
    await enqueued()

  enqueued.assert_not_awaited()

"""Which publication outcomes let a lower-ranked intent publish in the same cycle.

Only an intent's own terminal reject or its own retained waiting state may expose the next
ranked intent. A global or ownership condition (stale quote, news guard, reservation
unavailable, cycle owner, duplicate) must keep blocking it, or two workers could publish.
"""

from __future__ import annotations

import pytest

from app.autotrade import worker
from app.autotrade.arbitration import CandidatePublicationResult, ExecutionIntent
from app.autotrade.cycle_publish import publish_ranked_cycle
from app.autotrade.route_outcome import record_route_outcome
from app.persistence import redis_state
from tests.test_publish_trade_plan_v8 import _reaction_match

pytestmark = pytest.mark.no_database


async def _result_for(reason: str, *, status: str, retained: bool):
  client = redis_state.get_client()
  match = _reaction_match(match_id=f"m-{reason}", thesis_id=f"t-{reason}")
  await record_route_outcome(
    client, match, stage="publication", status=status, reason_code=reason,
    message=reason, retained=retained, publish_status=False,
  )
  return await worker._strategy_publication_result(client, match, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["required_limit_side_unavailable", "waiting_retest_entry_zone"])
async def test_an_intents_own_waiting_state_exposes_the_next_ranked_intent(reason):
  result = await _result_for(reason, status="waiting", retained=True)
  assert result.status == "terminal_reject"
  assert not result.blocks_lower_ranked_intents


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [
  "stale_spot", "news_guard_unavailable", "entry_zone_reservation_unavailable", "route_evaluation_in_progress",
])
async def test_a_global_waiting_condition_keeps_blocking_lower_ranked_intents(reason):
  result = await _result_for(reason, status="waiting", retained=True)
  assert result.status == "publication_unavailable"
  assert result.blocks_lower_ranked_intents


@pytest.mark.asyncio
async def test_an_unretained_block_is_terminal_and_falls_back():
  result = await _result_for("opposing_barrier_room_below_cost", status="blocked", retained=False)
  assert result.status == "terminal_reject" and not result.blocks_lower_ranked_intents


def _intent(index: int) -> ExecutionIntent:
  return ExecutionIntent(
    intent_id=f"strategy:m{index}", source="go_analysis_engine", strategy=f"s{index}", direction="BUY",
    confluence=2, freshness=1000.0, distance_pips=0.0, match_id=f"m{index}",
  )


@pytest.mark.asyncio
async def test_ranked_cycle_falls_through_terminal_rejects_only_and_publishes_once():
  client = redis_state.get_client()
  ordered = (_intent(0), _intent(1), _intent(2))
  calls: list[str] = []

  async def publisher(item: ExecutionIntent) -> CandidatePublicationResult:
    calls.append(item.intent_id)
    if item.intent_id == "strategy:m0":
      return CandidatePublicationResult.terminal_reject("policy_reward_risk_insufficient")
    return CandidatePublicationResult.published("v8:m1")

  result = await publish_ranked_cycle(client, symbol="XAU", cycle_id="c1", ordered=ordered, publisher=publisher)
  assert result.status == "published" and calls == ["strategy:m0", "strategy:m1"]
  again = await publish_ranked_cycle(client, symbol="XAU", cycle_id="c1", ordered=ordered, publisher=publisher)
  assert again.status == "cycle_conflict" and calls == ["strategy:m0", "strategy:m1"]
  # Another symbol's cycle is independent.
  other = await publish_ranked_cycle(client, symbol="EURUSD", cycle_id="c1", ordered=ordered, publisher=publisher)
  assert other.status == "published"


@pytest.mark.asyncio
async def test_a_blocking_result_stops_the_fallback_chain():
  client = redis_state.get_client()
  calls: list[str] = []

  async def publisher(item: ExecutionIntent) -> CandidatePublicationResult:
    calls.append(item.intent_id)
    return CandidatePublicationResult.blocked("duplicate_thesis", "thesis_already_owned")

  result = await publish_ranked_cycle(
    client, symbol="XAU", cycle_id="c2", ordered=(_intent(0), _intent(1)), publisher=publisher,
  )
  assert result.status == "duplicate_thesis" and calls == ["strategy:m0"]

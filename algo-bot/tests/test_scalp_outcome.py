"""scalp_outcome: one terminal outcome per scalp-lane opportunity (helpers and the first-writer-wins contract)."""

from __future__ import annotations

import pytest

from app.autotrade import scalp_outcome as so

pytestmark = pytest.mark.no_database


class _Redis:
  """Just the calls scalp_outcome makes: SET [NX], GET, INCR, HINCRBY."""

  def __init__(self):
    self.values: dict[str, str] = {}
    self.counters: dict[str, int] = {}

  async def set(self, key, value, nx=False, ex=None):
    if nx and key in self.values:
      return None
    self.values[key] = value
    return True

  async def get(self, key):
    return self.values.get(key)

  async def incr(self, key):
    self.counters[key] = self.counters.get(key, 0) + 1

  async def hincrby(self, *_args):
    return 1


def test_helpers_name_the_strategy_and_the_outcome():
  assert so.strategy_key("Impulse Pullback Scalp") == "impulse_pullback"
  assert so.strategy_key("Key Level") == "key_level"
  assert so.label(so.ADMISSION_REJECTED, "xau_scalp_opposite_too_close") == "admission_rejected:xau_scalp_opposite_too_close"
  assert so.label(so.PLAN_PUBLISHED, None) == "plan_published"
  assert so.metric_key("Impulse Pullback Scalp", "plan_published") == "scalp:metric:outcome:impulse_pullback:plan_published"
  assert so.is_scalp_lane("Impulse Pullback Scalp") and not so.is_scalp_lane("Key Level")


@pytest.mark.asyncio
async def test_the_first_outcome_wins_and_a_second_is_not_counted():
  client = _Redis()
  kw = {"strategy": "Impulse Pullback Scalp", "opportunity_id": "opp_1"}
  assert await so.record_scalp_outcome(client, terminal=so.INVALIDATED_BEFORE_ENTRY, reason="IMPULSE_REFERENCE_LOST", **kw) is True
  assert await so.record_scalp_outcome(client, terminal=so.WAITING_RETEST_EXPIRED, **kw) is False
  assert await so.record_scalp_outcome(client, terminal=so.ACTIVATION_REJECTED, reason="x", **kw) is False
  assert client.counters == {"scalp:metric:outcome:impulse_pullback:invalidated_before_entry:IMPULSE_REFERENCE_LOST": 1}


@pytest.mark.asyncio
async def test_only_plan_published_is_superseded_and_only_by_an_activation_rejection():
  client = _Redis()
  kw = {"strategy": "Impulse Pullback Scalp", "opportunity_id": "opp_2"}
  assert await so.record_scalp_outcome(client, terminal=so.PLAN_PUBLISHED, **kw) is True
  assert await so.record_scalp_outcome(client, terminal=so.INVALIDATED_BEFORE_ENTRY, reason="SETUP_EXPIRED", **kw) is False
  assert await so.record_scalp_outcome(client, terminal=so.ACTIVATION_REJECTED, reason="spread", **kw) is True
  assert client.values["scalp:outcome:opp_2"] == "activation_rejected:spread"
  assert await so.record_scalp_outcome(client, terminal=so.ACTIVATION_REJECTED, reason="again", **kw) is False


@pytest.mark.asyncio
async def test_other_lanes_unknown_outcomes_and_a_failing_store_are_ignored_without_raising():
  client = _Redis()
  assert await so.record_scalp_outcome(client, strategy="Key Level", opportunity_id="opp_3", terminal=so.PLAN_PUBLISHED) is False
  assert await so.record_scalp_outcome(client, strategy="Impulse Pullback Scalp", opportunity_id="opp_3", terminal="made_up") is False
  assert client.values == {} and client.counters == {}

  class _Broken:
    async def set(self, *_a, **_k):
      raise ConnectionError("redis down")

  assert await so.record_scalp_outcome(_Broken(), strategy="Impulse Pullback Scalp", opportunity_id="opp_4", terminal=so.PLAN_PUBLISHED) is False

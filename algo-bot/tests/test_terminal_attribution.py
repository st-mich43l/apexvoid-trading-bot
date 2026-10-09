"""A terminal outcome keeps what the opportunity was, survives a failed projection and a restart,
and never replaces evidence the executor or broker established.

Production 2026-10-08: ``_record_terminal_outcome`` built a thin object, so ``record_route_outcome``
wrote an empty strategy, direction and structure over whatever the outcome already said; and when
the lifecycle transition landed but the projection failed, redelivery skipped the projection (the
setup was already terminal) and a restart rewrote the reason to ``startup_reconciliation``.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from app.analysis_client.models import InvalidationTopic
from app.autotrade import go_opportunity_policy as pol
from app.autotrade.multi_match import deserialize_matches, strategy_matches_key
from app.autotrade.route_outcome import (
  AUTHORITATIVE_EXECUTION_STATUSES,
  record_route_outcome,
  route_history_key,
  route_outcome_key,
)
from app.autotrade.setup_lifecycle import (
  EXPIRED,
  INVALIDATED,
  SetupRecord,
  create_setup,
  load_setup,
  transition_setup,
)
from app.autotrade.startup_reconciliation import reconcile_startup_state
from app.persistence import redis_state
from tests.test_go_opportunity_policy import (  # noqa: F401 - fixtures + builders
  _terminal_event,
  event,
  h,
)

MATCH_ID = "go_opp_golden_supply_xau"


async def _outcome(match_id: str = MATCH_ID) -> dict:
  raw = await redis_state.get_client().get(route_outcome_key("XAU", match_id))
  return json.loads(raw) if raw else {}


async def _live_match():
  matches = deserialize_matches(await redis_state.get_client().get(strategy_matches_key("XAU")))
  return next(m for m in matches if m.match_id == MATCH_ID)


async def _waiting_outcome(match) -> None:
  await record_route_outcome(
    redis_state.get_client(), match, stage="policy", status="waiting",
    reason_code="waiting_retest_entry_zone", message="waiting for the retest", publish_status=False,
  )


IDENTITY = ("strategy", "strategy_family", "direction", "structural_source", "structural_id")


def _identity(outcome: dict) -> tuple:
  return tuple(outcome[name] for name in IDENTITY)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,status,code", [
  ("ZONE_INVALIDATED", "blocked", "go_zone_invalidated"),
  ("SETUP_EXPIRED", "expired", "go_setup_expired"),
])
async def test_go_terminal_preserves_the_strategy_direction_and_structure(h, reason, status, code):
  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  match = await _live_match()
  await _waiting_outcome(match)
  before = _identity(await _outcome())
  assert before[0] == "Supply Demand" and before[2] == "SELL" and before[4]

  await h.deliver(_terminal_event(h, reason), InvalidationTopic)

  outcome = await _outcome()
  assert (outcome["status"], outcome["reason_code"]) == (status, code)
  assert _identity(outcome) == before            # nothing blanked or defaulted


@pytest.mark.asyncio
async def test_go_terminal_with_no_earlier_outcome_takes_the_identity_from_the_withdrawn_match(h):
  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  match = await _live_match()
  assert await _outcome() == {}

  await h.deliver(_terminal_event(h, "ZONE_INVALIDATED"), InvalidationTopic)

  outcome = await _outcome()
  assert _identity(outcome) == (match.strategy, match.family, match.direction, match.structural_source, match.structural_zone_id)


@pytest.mark.asyncio
async def test_a_projection_that_failed_is_retried_by_the_redelivered_event(h, monkeypatch):
  """The lifecycle transition succeeds, the route-outcome write fails; redelivery must not
  skip the projection just because the setup is already terminal."""
  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  match = await _live_match()
  await _waiting_outcome(match)
  client = redis_state.get_client()

  with monkeypatch.context() as patch:
    patch.setattr(pol, "record_route_outcome", AsyncMock(side_effect=RuntimeError("redis hiccup")))
    await h.deliver(_terminal_event(h, "ZONE_INVALIDATED"), InvalidationTopic)
  assert (await load_setup(client, MATCH_ID)).state == INVALIDATED     # the transition landed
  assert (await _outcome())["status"] == "waiting"                      # the projection did not

  await h.deliver(_terminal_event(h, "ZONE_INVALIDATED"), InvalidationTopic)   # Kafka redelivery

  outcome = await _outcome()
  assert (outcome["status"], outcome["reason_code"]) == ("blocked", "go_zone_invalidated")
  assert outcome["strategy"] == "Supply Demand" and outcome["direction"] == "SELL"


@pytest.mark.asyncio
async def test_the_go_reason_is_stored_on_the_setup_and_a_restart_restores_it(h, monkeypatch):
  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  match = await _live_match()
  await _waiting_outcome(match)
  client = redis_state.get_client()
  with monkeypatch.context() as patch:
    patch.setattr(pol, "record_route_outcome", AsyncMock(side_effect=RuntimeError("down")))
    await h.deliver(_terminal_event(h, "SETUP_EXPIRED"), InvalidationTopic)
  record = await load_setup(client, MATCH_ID)
  assert (record.state, record.terminal_reason) == (EXPIRED, "go_setup_expired")
  assert (record.strategy, record.direction) == ("Supply Demand", "SELL")

  await reconcile_startup_state(client)          # the process restarted before any retry

  outcome = await _outcome()
  assert (outcome["status"], outcome["reason_code"]) == ("expired", "go_setup_expired")
  assert outcome["strategy"] == "Supply Demand" and outcome["direction"] == "SELL"
  metrics = await client.hgetall("auto_trade:metrics:XAU")
  assert int(metrics.get("terminal_reason_unrecoverable", 0)) == 0


@pytest.mark.asyncio
async def test_an_unrecoverable_original_reason_is_reported_not_invented():
  """A setup that became terminal before the reason was stored keeps the generic reason and is
  counted as unrecoverable."""
  client = redis_state.get_client()
  await create_setup(client, setup_id="legacy-terminal", thesis_id="t", symbol="XAU")
  await transition_setup(client, "legacy-terminal", INVALIDATED)

  await reconcile_startup_state(client)

  raw = await client.get(route_outcome_key("XAU", "legacy-terminal"))
  assert json.loads(raw)["reason_code"] == "startup_reconciliation"
  metrics = await client.hgetall("auto_trade:metrics:XAU")
  assert int(metrics.get("terminal_reason_unrecoverable", 0)) >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(AUTHORITATIVE_EXECUTION_STATUSES))
async def test_executor_and_broker_outcomes_are_never_overwritten_by_an_analysis_terminal(status):
  client = redis_state.get_client()
  await create_setup(client, setup_id="exec-1", thesis_id="t", symbol="XAU")
  match = type("M", (), {
    "match_id": "exec-1", "symbol": "XAU", "strategy": "FVG", "family": "supply_demand",
    "direction": "BUY", "structural_source": "go:fvg", "structural_zone_id": "z1",
    "issued_at": 1, "expires_at": int(time.time()) + 600,
  })()
  await record_route_outcome(
    client, match, stage="broker", status=status, reason_code="from_the_executor",
    message="executor evidence", publish_status=False,
  )
  history = await client.xlen(route_history_key("XAU"))

  for stage in ("entry_invalidation", "scanner"):
    await record_route_outcome(
      client, match, stage=stage, status="blocked" if stage == "entry_invalidation" else "expired",
      reason_code="go_zone_invalidated", message="analysis terminal", publish_status=False,
    )

  raw = json.loads(await client.get(route_outcome_key("XAU", "exec-1")))
  assert (raw["status"], raw["reason_code"], raw["strategy"]) == (status, "from_the_executor", "FVG")
  assert await client.xlen(route_history_key("XAU")) == history


@pytest.mark.asyncio
async def test_reconciliation_leaves_a_broker_confirmed_outcome_alone():
  client = redis_state.get_client()
  await create_setup(client, setup_id="exec-2", thesis_id="t", symbol="XAU")
  await transition_setup(client, "exec-2", INVALIDATED, terminal_reason="go_zone_invalidated")
  match = type("M", (), {"match_id": "exec-2", "symbol": "XAU", "strategy": "FVG", "direction": "BUY",
                         "issued_at": 1, "expires_at": int(time.time()) + 600})()
  await record_route_outcome(client, match, stage="broker", status="order_filled", reason_code="filled",
                             message="filled", publish_status=False)

  await reconcile_startup_state(client)

  assert json.loads(await client.get(route_outcome_key("XAU", "exec-2")))["status"] == "order_filled"


@pytest.mark.asyncio
async def test_duplicate_terminal_delivery_is_idempotent(h):
  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  client = redis_state.get_client()
  await h.deliver(_terminal_event(h, "ZONE_INVALIDATED"), InvalidationTopic)
  first = await _outcome()
  history = await client.xlen(route_history_key("XAU"))

  for _ in range(3):
    await h.deliver(_terminal_event(h, "ZONE_INVALIDATED"), InvalidationTopic)

  assert await _outcome() == {**first, "checked_at": (await _outcome())["checked_at"]}
  assert await client.xlen(route_history_key("XAU")) == history


@pytest.mark.asyncio
async def test_a_different_terminal_source_is_not_relabelled_by_a_later_go_event(h):
  """A setup invalidated by another source keeps that source's reason: the retry path only
  fills a projection for a Go-terminal (or reason-less) setup."""
  await h.activate()
  await h.deliver(event(int(h.clock.now)))
  client = redis_state.get_client()
  await transition_setup(client, MATCH_ID, INVALIDATED, terminal_reason="worker_rejected")

  await h.deliver(_terminal_event(h, "ZONE_INVALIDATED"), InvalidationTopic)

  assert await _outcome() == {}                      # no Go projection was forced onto it


def test_a_legacy_setup_record_without_attribution_fields_still_loads():
  record = SetupRecord.from_dict({
    "setup_id": "old", "thesis_id": "t", "symbol": "XAU", "state": "invalidated",
    "created_at": 1, "updated_at": 2,
  })
  assert (record.strategy, record.direction, record.terminal_reason) == (None, None, None)
  assert SetupRecord.from_dict(record.to_dict()) == record

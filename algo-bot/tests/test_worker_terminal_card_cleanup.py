"""Orphan-card regression coverage for worker.py's inline terminal
transitions (see fix/p0-setup-lifecycle-projection-consistency).

worker.py cannot import app.bot.client directly (see
test_worker_source_has_no_direct_scanner_market_map_or_telegram_import in
test_auto_scalp_worker.py), so it can only clear a forming card indirectly:
emit_lifecycle(..., publish_status=True) -> auto_trade:events ->
delivery._deliver_auto_trade_event's _CARD_TERMINAL_TYPES handling ->
kill_setup_card. Several worker.py terminal transitions used to skip that
publish entirely, leaving their forming card orphaned forever. This covers
the PLAN_BUILT -> CANCELLED "incomplete build" path end to end, and the
_CARD_TERMINAL_TYPES fix that made a CANCELLED event eligible for cleanup
at all.
"""

from __future__ import annotations
from app.core.config import runtime_config
from tests.support.canonical_fixtures import leaf

import json

import pytest

from app.autotrade import delivery, worker
from app.autotrade.setup_lifecycle import (
  CANCELLED,
  CONFIRMED,
  PLAN_BUILT,
  WATCHING,
  TOUCHED,
  FORMING,
  create_setup,
  load_setup,
  transition_setup,
)
from app.autotrade.setup_card import forming_message_key
from app.autotrade.strategy_match import StrategyMatch
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


def _match(setup_id: str) -> StrategyMatch:
  return StrategyMatch(
    version=1,
    match_id=setup_id,
    symbol="XAU",
    source_tf="M5",
    event_ts="1722168600",
    issued_at=1722168600,
    expires_at=2_000_000_000,
    strategy="Demand Zone Reaction",
    strategy_mode="with_bias",
    direction="BUY",
    key_level=4100.5,
    entry_low=4100.0,
    entry_high=4101.0,
    current_price=4100.5,
    confluence=3,
    reasons=("demand reaction",),
    atr=2.0,
    structure_swing=4100.0,
    targets_pips=(30,),
  )


@pytest.mark.asyncio
async def test_plan_build_incomplete_cancels_and_clears_orphan_card(
  monkeypatch,
):
  client = redis_state.get_client()
  setup_id = "worker-cancel-1"
  match = _match(setup_id)

  await create_setup(
    client, setup_id=setup_id, thesis_id="thesis-1", symbol="XAU",
  )
  for state in (WATCHING, TOUCHED, FORMING, CONFIRMED, PLAN_BUILT):
    await transition_setup(client, setup_id, state)

  # A forming card was posted earlier in the setup's life and never
  # cleaned up - this is the exact "orphan card" shape the fix closes.
  await client.set(
    forming_message_key(setup_id),
    json.dumps({"chat_id": 4242, "message_id": 9005}),
    ex=60,
  )

  plan_id = worker._v8_plan_id(match)
  assert await client.exists(f"execution:trade_plan:v8:{plan_id}") == 0

  result = await worker._publish_trade_plan_v8(client, "XAU", None, match)

  assert result is None
  record = await load_setup(client, setup_id)
  assert record.state == CANCELLED

  events = await client.xrange(leaf(runtime_config, "auto_trade_event_stream"))
  payloads = [json.loads(fields["payload"]) for _id, fields in events]
  assert payloads, "expected a terminal lifecycle event to be published"
  assert payloads[-1]["type"] == CANCELLED
  assert payloads[-1]["reason_code"] == "v8_plan_build_incomplete"

  edited = []

  async def edit_card(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  async def delete_card(chat_id, message_id):
    raise AssertionError("reject/expire must edit root, not delete")

  monkeypatch.setattr(delivery, "delete_scanner_message", delete_card)
  monkeypatch.setattr(delivery, "edit_scanner_message_text", edit_card)

  delivered = await delivery._deliver_auto_trade_event(
    client, payloads[-1], profile="internal", chat_id=4242,
  )

  assert delivered is False
  # Close leaves the root body intact (no TERMINAL rewrite) when there is
  # nothing to strip; mapping stays for threaded replies.
  assert len(edited) == 0
  assert await client.get(forming_message_key(setup_id)) is not None


@pytest.mark.asyncio
async def test_card_terminal_types_now_covers_cancelled():
  assert "cancelled" in delivery._CARD_TERMINAL_TYPES

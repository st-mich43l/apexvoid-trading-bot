"""Telegram delivery correlation audit (independent-position-tracking quickfix).

Confirms two invariants the incident report asked to be verified rather than
assumed:

1. clear_active_setup_tracking (called on every "opened" event) only ever
   touches scanner watchlist/forming state (scanner:setup:*) - never
   execution tracking or Telegram message anchors for an already-open
   position (auto_trade:position:*, auto_trade:msg:*, auto_trade:tp_msg:*).
2. The reply target for take_profit/stop_moved/position_closed is resolved
   strictly from that event's own position_id - never falls back to a
   group-level key or another active same-direction trade's message.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.analysis.scanner import clear_active_setup_tracking
from app.autotrade import delivery, setup_card
from app.autotrade.delivery import (
  _compact_route_line,
  _group_message_key,
  _mark_forming_card_position_activated,
  _message_key,
  _resolve_reply_message_id,
  tp_message_key,
)
from app.autotrade.setup_lifecycle import CONFIRMED, create_setup, transition_setup
from app.persistence import redis_state


async def _confirmed_setup(client, setup_id: str) -> None:
  await create_setup(
    client, setup_id=setup_id, thesis_id=f"thesis-{setup_id}", symbol="XAU",
  )
  for state in ("watching", "touched", "forming", CONFIRMED):
    await transition_setup(client, setup_id, state)


@pytest.mark.asyncio
async def test_clear_active_setup_tracking_never_touches_execution_state():
  client = redis_state.get_client()
  await client.set("scanner:setup:active:XAU:M5:BUY", "1")
  await client.set("scanner:setup:active_band:XAU:M5:BUY:zone-1", "1")
  await client.set("auto_trade:position:91", "should-survive")
  await client.set(_message_key("internal", 91), "12345")
  await client.set(tp_message_key("internal", 91), "12346")

  await clear_active_setup_tracking(client, "XAU", tf="M5", direction="BUY")

  assert await client.get("scanner:setup:active:XAU:M5:BUY") is None
  assert await client.get("scanner:setup:active_band:XAU:M5:BUY:zone-1") is None
  assert await client.get("auto_trade:position:91") == "should-survive"
  assert await client.get(_message_key("internal", 91)) == "12345"
  assert await client.get(tp_message_key("internal", 91)) == "12346"


@pytest.mark.parametrize("event_type", ["take_profit", "stop_moved", "position_closed"])
@pytest.mark.asyncio
async def test_position_events_resolve_reply_only_from_their_own_position_id(
  event_type,
):
  client = redis_state.get_client()
  # Position A's own message, position B's message, and a group-level
  # message all exist - only A's own key may ever be picked for A's event.
  await client.set(_message_key("internal", 91), "1001")
  await client.set(_message_key("internal", 92), "2002")
  await client.set(_group_message_key("internal", "group-a"), "3003")

  message_id, reason = await _resolve_reply_message_id(
    client,
    {
      "type": event_type,
      "position_id": 91,
      "group_id": "group-a",
    },
    "internal",
  )

  assert message_id == 1001
  assert reason == ""


@pytest.mark.parametrize("event_type", ["take_profit", "stop_moved", "position_closed"])
@pytest.mark.asyncio
async def test_position_events_never_fall_back_to_group_or_other_position(
  event_type,
):
  client = redis_state.get_client()
  # This position has no message of its own yet, but a sibling position in
  # the same group and a group-level message both exist - neither may be
  # used as a substitute reply target.
  await client.set(_message_key("internal", 92), "2002")
  await client.set(_group_message_key("internal", "group-a"), "3003")

  message_id, reason = await _resolve_reply_message_id(
    client,
    {
      "type": event_type,
      "position_id": 91,
      "group_id": "group-a",
    },
    "internal",
  )

  assert message_id is None
  assert reason != ""


@pytest.mark.asyncio
async def test_order_filled_waits_for_a_root_card_still_mid_send(monkeypatch):
  # Live 2026-08-11: order_filled sent standalone (no reply_to) because its
  # own setup's root card was still mid-send (Telegram flood-control
  # stretched a single edit/send to 17s+) - the owner saw a disconnected
  # ORDER FILLED bubble with no thread to its ORDER ACTIVATED card.
  # Reply resolution must poll for the card instead of giving up on the
  # first empty lookup.
  client = redis_state.get_client()
  match_id = "root-card-still-sending"
  await _confirmed_setup(client, match_id)
  monkeypatch.setattr(delivery, "_FORMING_REPLY_WAIT_SECONDS", 1.0)
  monkeypatch.setattr(delivery, "_FORMING_REPLY_POLL_SECONDS", 0.01)

  calls = 0

  async def fake_load_forming_card(_client, _setup_id):
    nonlocal calls
    calls += 1
    if calls < 3:
      return None
    return {"chat_id": 123, "message_id": 9001, "text": "root"}

  monkeypatch.setattr(delivery, "load_forming_card", fake_load_forming_card)

  message_id, reason = await _resolve_reply_message_id(
    client,
    {"type": "order_filled", "candidate_id": f"v8:{match_id}"},
    "internal",
  )

  assert message_id == 9001
  assert reason == ""
  assert calls >= 3


@pytest.mark.asyncio
async def test_order_filled_falls_back_standalone_after_the_wait_expires(
  monkeypatch,
):
  client = redis_state.get_client()
  match_id = "root-card-never-arrives"
  await _confirmed_setup(client, match_id)
  monkeypatch.setattr(delivery, "_FORMING_REPLY_WAIT_SECONDS", 0.05)
  monkeypatch.setattr(delivery, "_FORMING_REPLY_POLL_SECONDS", 0.01)

  async def fake_load_forming_card(_client, _setup_id):
    return None

  monkeypatch.setattr(delivery, "load_forming_card", fake_load_forming_card)

  message_id, reason = await _resolve_reply_message_id(
    client,
    {"type": "order_filled", "candidate_id": f"v8:{match_id}"},
    "internal",
  )

  assert message_id is None
  assert reason != ""


@pytest.mark.asyncio
async def test_ensure_root_card_for_manage_reply_finds_root_via_telegram_root_key_alone(
  monkeypatch,
):
  # 2026-09 (owner-reported duplicate root card): forming_message_key and
  # telegram_root_message_key are written together by save_forming_card and
  # are supposed to always name the same message. If forming_message_key is
  # ever unreadable while telegram_root_message_key still names the real
  # root - this seeds exactly that split - the old code (a direct
  # load_forming_card check with no fallback) wrongly concluded no root
  # existed and created a second one. It must instead find the real root via
  # telegram_root_message_key and never attempt to create anything.
  client = redis_state.get_client()
  match_id = "root-key-split"
  await client.set(
    setup_card.telegram_root_message_key(match_id),
    '{"chat_id":123,"root_message_id":9009,"updated_at":1}',
  )

  async def fail_if_called(*_args, **_kwargs):
    raise AssertionError(
      "ensure_root_card_for_setup_id must not be called when "
      "telegram_root_message_key already names the real root"
    )

  monkeypatch.setattr(setup_card, "ensure_root_card_for_setup_id", fail_if_called)

  message_id = await delivery._ensure_root_card_for_manage_reply(
    client,
    {"type": "take_profit", "symbol": "XAU"},
    match_id=match_id,
    chat_id=123,
  )

  assert message_id == 9009


@pytest.mark.asyncio
async def test_forming_reply_lookup_logs_identity_mismatch(monkeypatch, caplog):
  # Companion coverage: when the two keys actively disagree (both present,
  # different message ids - a re-post raced ahead of a cleanup, or vice
  # versa), forming_message_key still wins for the reply target, but the
  # mismatch must be logged loudly so it's caught the moment it happens
  # instead of requiring after-the-fact archaeology once Telegram history is
  # gone.
  client = redis_state.get_client()
  match_id = "root-key-mismatch"
  await setup_card.save_forming_card(
    client, match_id, chat_id=123, message_id=7001,
  )
  await client.set(
    setup_card.telegram_root_message_key(match_id),
    '{"chat_id":123,"root_message_id":7099,"updated_at":1}',
  )

  with caplog.at_level("ERROR", logger="app.autotrade.delivery"):
    message_id = await delivery._lookup_forming_reply_message_id(client, match_id)

  assert message_id == 7001
  assert any(
    "forming_reply_identity_mismatch" in record.message
    for record in caplog.records
  )


def test_compact_route_line_never_shows_a_preflight_pass_through_code():
  # preflight_reason_code lingers as whatever the last preflight-stage
  # event recorded - once a candidate clears every preflight check that's
  # "preflight_allowed", which then sits there as the displayed "why" on
  # every later status check even though it explains nothing (it means
  # "passed", not "here's what happened"). Confirmed live: a card reading
  # "Key Level Reaction · waiting · preflight allowed" told the owner
  # nothing about why nothing had executed yet.
  line = _compact_route_line({
    "strategy": "Key Level Reaction",
    "status": "waiting",
    "preflight_reason_code": "preflight_allowed",
  })

  assert line == "Key Level Reaction · waiting"


def test_compact_route_line_still_shows_a_genuine_rejection_reason():
  line = _compact_route_line({
    "strategy": "Key Level Reaction",
    "status": "blocked",
    "reason_code": "opposing_entry_contained",
  })

  assert line == "Key Level Reaction · blocked · opposing_entry_contained"


def test_compact_route_line_prefers_reason_code_over_stale_preflight_code():
  line = _compact_route_line({
    "strategy": "Key Level Reaction",
    "status": "blocked",
    "reason_code": "policy_reward_risk_insufficient",
    "preflight_reason_code": "preflight_allowed",
  })

  assert line == "Key Level Reaction · blocked · policy_reward_risk_insufficient"


@pytest.mark.asyncio
async def test_mark_forming_card_position_activated_rewrites_head_and_stop(
  monkeypatch,
):
  """A filled position must show its real stop, not the "SL" placeholder
  left over from before publish - and the head must no longer read SETUP
  FORMING once the order is actually live.
  """
  client = redis_state.get_client()
  match_id = "setup-activated-stop"
  await _confirmed_setup(client, match_id)
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "​",
    "🟢 <b>BUY · Key Level Reaction</b> · ⭐⭐",
    "",
    "📍 <b>Trade area</b>",
    "• <b>Entry zone:</b> <b>3399.00–3401.00</b>",
    "• <b>Key level:</b> <b>3398.00</b>",
    "• <b>Stop:</b> <b>SL</b>",
  ])
  await setup_card.save_forming_card(
    client, match_id, chat_id=123, message_id=5001, text=original,
  )

  async def fake_stop_price(_client, _match_id):
    return 3395.5

  edited = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  monkeypatch.setattr(delivery, "published_plan_stop_price", fake_stop_price)
  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)

  await _mark_forming_card_position_activated(client, match_id)

  card = await setup_card.load_forming_card(client, match_id)
  assert card is not None
  lines = card["text"].splitlines()
  assert lines[0] == "✅ <b>ORDER ACTIVATED · XAU M5</b>"
  # The header alone carries the ORDER ACTIVATED text now - the status
  # slot beneath it collapses to invisible instead of repeating it.
  assert card["text"].count("ORDER ACTIVATED") == 1
  assert "• <b>Stop:</b> <b>3,395.50</b>" in card["text"]
  assert "SL</b>" not in card["text"]


def _waiting_fill_card_text() -> str:
  return "\n".join([
    "🔎 <b>XAU M1 · IN ZONE · WAITING FILL</b>",
    "⏳ <b>IN ZONE</b> · waiting market fill",
    "🔴 <b>SELL · Impulse Pullback Scalp</b> · ⭐⭐",
    "• <b>Price now:</b> <b>4,334.10</b> <i>(live)</i>",
  ])


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_order_filled_rewrites_waiting_fill_root(monkeypatch):
  client = redis_state.get_client()
  match_id = "c22db147waitingfill"
  await _confirmed_setup(client, match_id)
  await setup_card.save_forming_card(
    client, match_id, chat_id=123, message_id=4582, text=_waiting_fill_card_text(),
  )
  await client.sadd(setup_card.FORMING_ACTIVE_INDEX_KEY, match_id)

  async def fake_stop_price(_client, _match_id):
    return 4320.0

  async def fake_edit(chat_id, message_id, text):
    return None

  async def sent(text, **kwargs):
    return SimpleNamespace(message_id=9001)

  monkeypatch.setattr(delivery, "published_plan_stop_price", fake_stop_price)
  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "order_filled",
      "match_id": match_id,
      "candidate_id": f"v8:{match_id}",
      "message": "SELL 0.10 lots filled 4334.47",
      "position_id": 40398863,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  card = await setup_card.load_forming_card(client, match_id)
  assert card is not None
  assert "WAITING FILL" not in card["text"]
  assert "ORDER ACTIVATED" in card["text"]
  assert not await client.sismember(setup_card.FORMING_ACTIVE_INDEX_KEY, match_id)


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_plan_expired_marks_waiting_fill_root(monkeypatch):
  """Unfilled expire must leave PLAN EXPIRED on the root — not WAITING FILL forever."""
  client = redis_state.get_client()
  match_id = "11c1d02cexpired"
  await _confirmed_setup(client, match_id)
  await setup_card.save_forming_card(
    client, match_id, chat_id=123, message_id=4583, text=_waiting_fill_card_text(),
  )
  # Published plans store a durable PLAN PUBLISHED status; expire must outrank it.
  await setup_card.save_forming_card_status(
    client,
    match_id,
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V8 sent to executor",
    state="plan_published",
  )
  await client.sadd(setup_card.FORMING_ACTIVE_INDEX_KEY, match_id)
  sent = []
  edited = []

  async def fake_edit(chat_id, message_id, text):
    edited.append(text)
    return None

  async def fake_delete(chat_id, message_id):
    raise AssertionError("expire must edit root, not delete")

  async def send_fn(text, **kwargs):
    sent.append(text)
    return SimpleNamespace(message_id=9002)

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  monkeypatch.setattr(delivery, "delete_scanner_message", fake_delete)

  delivered = await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "plan_expired",
      "match_id": match_id,
      "candidate_id": f"v8:{match_id}",
      "message": "price never entered the entry zone",
      "reason_code": "outside_zone",
    },
    profile="internal",
    chat_id=123,
    send=send_fn,
  )

  assert delivered is True
  assert sent == []
  card = await setup_card.load_forming_card(client, match_id)
  assert card is not None
  assert "PLAN EXPIRED" in card["text"]
  assert "TERMINAL" not in card["text"]
  assert "(live)" not in card["text"]
  assert any("PLAN EXPIRED" in text for text in edited)
  assert not await client.sismember(setup_card.FORMING_ACTIVE_INDEX_KEY, match_id)


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_position_closed_leaves_waiting_fill_root_intact(monkeypatch):
  client = redis_state.get_client()
  match_id = "03bb3092closed"
  await _confirmed_setup(client, match_id)
  await setup_card.save_forming_card(
    client, match_id, chat_id=123, message_id=4584, text=_waiting_fill_card_text(),
  )
  await client.sadd(setup_card.FORMING_ACTIVE_INDEX_KEY, match_id)

  async def fake_edit(chat_id, message_id, text):
    return None

  async def sent(text, **kwargs):
    return SimpleNamespace(message_id=9003)

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "position_closed",
      "match_id": match_id,
      "candidate_id": f"v8:{match_id}",
      "message": "SELL position is closed · -26.0 pips",
      "position_id": 40398674,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  card = await setup_card.load_forming_card(client, match_id)
  assert card is not None
  assert "TERMINAL" not in card["text"]
  assert "WAITING FILL" in card["text"]
  assert "(live)" not in card["text"]
  assert not await client.sismember(setup_card.FORMING_ACTIVE_INDEX_KEY, match_id)


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_order_filled_creates_root_when_publish_never_posted(monkeypatch):
  """Fast fills can beat the publish card; still thread under a recovered root."""
  client = redis_state.get_client()
  match_id = "norootfill03bb"
  await _confirmed_setup(client, match_id)
  sent_roots: list[str] = []
  replies: list[tuple[str, dict]] = []

  async def fake_send_root(text, **kwargs):
    sent_roots.append(text)
    return SimpleNamespace(message_id=6100)

  async def fake_edit(chat_id, message_id, text):
    return None

  async def sent(text, **kwargs):
    replies.append((text, kwargs))
    return SimpleNamespace(message_id=6101)

  async def no_wait():
    return None

  monkeypatch.setattr(
    "app.bot.client.send_scanner_root_card_with_retry", fake_send_root,
  )
  monkeypatch.setattr("app.bot.client.wait_out_scanner_flood", no_wait)
  monkeypatch.setattr("app.bot.client.note_scanner_flood", lambda *_a, **_k: None)
  async def fake_stop(*_a, **_k):
    return None

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  monkeypatch.setattr(delivery, "published_plan_stop_price", fake_stop)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "order_filled",
      "match_id": match_id,
      "candidate_id": f"v8:{match_id}",
      "symbol": "XAU",
      "message": "BUY 0.10 lots filled 4332.51",
      "position_id": 40398674,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  card = await setup_card.load_forming_card(client, match_id)
  assert card is not None
  assert card["message_id"] == 6100
  assert "ORDER ACTIVATED" in card["text"]
  assert replies
  assert replies[0][1].get("reply_to") == 6100
  assert "ORDER FILLED" in replies[0][0]

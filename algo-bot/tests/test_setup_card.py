"""One forming card per setup: post-or-edit, delete on terminal (Codex
Prompt P4).
"""

from __future__ import annotations
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

import asyncio
import os
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from redis.asyncio import Redis

from app.autotrade import setup_card
from app.autotrade.setup_lifecycle import (
  CONFIRMED,
  INVALIDATED,
  create_setup,
  transition_setup,
)
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


async def _confirmed_setup(client, setup_id: str) -> None:
  await create_setup(
    client, setup_id=setup_id, thesis_id=f"thesis-{setup_id}", symbol="XAU",
  )
  for state in ("watching", "touched", "forming", CONFIRMED):
    await transition_setup(client, setup_id, state)


@pytest.mark.asyncio
async def test_one_card_per_setup_posts_once_then_edits():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-1")
  sent = []
  edited = []

  async def send_fn(text, **kwargs):
    sent.append(text)
    return SimpleNamespace(message_id=9001)

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  first_id = await setup_card.post_or_edit_forming_card(
    client, "setup-1", "forming v1", chat_id=123, send_fn=send_fn, edit_fn=edit_fn,
  )
  second_id = await setup_card.post_or_edit_forming_card(
    client, "setup-1", "forming v2", chat_id=123, send_fn=send_fn, edit_fn=edit_fn,
  )
  third_id = await setup_card.post_or_edit_forming_card(
    client, "setup-1", "forming v3", chat_id=123, send_fn=send_fn, edit_fn=edit_fn,
  )

  assert first_id == second_id == third_id == 9001
  assert sent == ["forming v1"]  # posted exactly once
  assert edited == [
    (123, 9001, "forming v2"),
    (123, 9001, "forming v3"),
  ]
  card = await setup_card.load_forming_card(client, "setup-1")
  assert card == {
    "chat_id": 123,
    "message_id": 9001,
    "text": "forming v3",
  }


@pytest.mark.asyncio
async def test_concurrent_first_create_sends_only_one_telegram_root():
  """Prod 2026-08-10 13:07: same setup_id double mode=send → msg 3946+3947.

  Two concurrent post_or_edit callers both saw no forming card and both
  sent a full root. Create-lock must serialize so only one Telegram send
  happens; the loser attaches to the winner's message_id.
  """
  client = redis_state.get_client()
  setup_id = "f716d5f0c179a9bb3da16e7ddf1b8d8b"
  await _confirmed_setup(client, setup_id)

  send_count = 0
  send_started = asyncio.Event()
  release_send = asyncio.Event()
  message_ids = iter((3946, 3947, 3948))

  async def send_fn(text, **kwargs):
    nonlocal send_count
    send_count += 1
    send_started.set()
    await release_send.wait()
    await asyncio.sleep(0)
    return SimpleNamespace(message_id=next(message_ids))

  async def edit_fn(chat_id, message_id, text):
    return None

  waiting = "\n".join([
    "⚫ <b>XAU M1 · IN ZONE · WAITING FILL</b>",
    "⏳ <b>IN ZONE · waiting market fill</b>",
    "🔴 <b>SELL</b> · Impulse Pullback Scalp",
  ])
  activated = setup_card.apply_forming_card_status(
    waiting,
    "✅ <b>POSITION ACTIVATED</b>",
  )

  async def _caller(body: str):
    return await setup_card.post_or_edit_forming_card(
      client,
      setup_id,
      body,
      chat_id=123,
      send_fn=send_fn,
      edit_fn=edit_fn,
    )

  first = asyncio.create_task(_caller(waiting))
  await send_started.wait()
  second = asyncio.create_task(_caller(activated))
  await asyncio.sleep(0.05)
  release_send.set()
  first_id, second_id = await asyncio.gather(first, second)

  assert send_count == 1
  assert first_id == 3946
  assert second_id == 3946
  card = await setup_card.load_forming_card(client, setup_id)
  assert card is not None
  assert card["message_id"] == 3946
  assert "ORDER ACTIVATED" in card["text"]


@pytest.mark.asyncio
async def test_edit_failure_falls_back_to_a_fresh_post():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-2")
  await setup_card.save_forming_card(client, "setup-2", chat_id=123, message_id=7777)

  async def send_fn(text, **kwargs):
    return SimpleNamespace(message_id=8888)

  async def edit_fn(chat_id, message_id, text):
    raise TelegramBadRequest(method=None, message="Bad Request: message to edit not found")

  new_id = await setup_card.post_or_edit_forming_card(
    client, "setup-2", "forming v2", chat_id=123, send_fn=send_fn, edit_fn=edit_fn,
  )

  assert new_id == 8888
  card = await setup_card.load_forming_card(client, "setup-2")
  assert card == {
    "chat_id": 123,
    "message_id": 8888,
    "text": "forming v2",
  }


@pytest.mark.asyncio
async def test_forming_card_ttl_survives_a_weekend_not_just_a_day():
  """Owner-reported live bug: a 24h TTL floor on the card's own identity
  keys let a Friday fill's mapping expire mid-weekend with no further
  event to refresh it, so Monday's first real event found no card and
  posted a duplicate instead of threading onto the original. The floor
  must comfortably outlive any realistic single silent gap.
  """
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-ttl")
  await setup_card.save_forming_card(
    client, "setup-ttl", chat_id=123, message_id=4242, text="body",
  )
  await setup_card.save_forming_card_status(
    client, "setup-ttl", "✅ <b>ORDER ACTIVATED</b>", state="order_filled",
  )

  message_ttl = await client.ttl(setup_card.forming_message_key("setup-ttl"))
  root_ttl = await client.ttl(setup_card.telegram_root_message_key("setup-ttl"))
  status_ttl = await client.ttl(setup_card.forming_status_key("setup-ttl"))

  a_weekend = 3 * 24 * 3600
  assert message_ttl > a_weekend
  assert root_ttl > a_weekend
  assert status_ttl > a_weekend


def test_apply_forming_card_stop_does_not_duplicate_existing_stop():
  """Card already has Stop after Key level — patch must not insert a second."""
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "• <b>Key level:</b> <b>4,034.85</b>",
    "• <b>Stop:</b> <b>4,039.68</b>",
    "",
    "🧭 <b>Context</b>",
  ])
  text = setup_card.apply_forming_card_stop(original, 4039.68)
  assert text.count("• <b>Stop:</b>") == 1
  assert "• <b>Stop:</b> <b>4,039.68</b>" in text


def test_should_stop_forming_price_track_after_activation():
  waiting = "🔎 <b>XAU M5 · SETUP FORMING</b>\n🟢 <b>PLAN PUBLISHED</b>"
  filled = "✅ <b>POSITION ACTIVATED · XAU M5</b>\n🟢 <b>ORDER FILLED</b>"
  assert setup_card.should_stop_forming_price_track(waiting) is False
  assert setup_card.should_stop_forming_price_track(filled) is True
  assert setup_card.should_stop_forming_price_track(
    waiting, status_state="order_filled",
  ) is True
  expired = "🔎 <b>XAU M1 · IN ZONE · WAITING FILL</b>\n⌛ PLAN EXPIRED"
  assert setup_card.should_stop_forming_price_track(expired) is True


def test_waiting_fill_header_stays_intact_on_terminal_status():
  waiting = "\n".join([
    "🔎 <b>XAU M1 · IN ZONE · WAITING FILL</b>",
    "⏳ <b>IN ZONE</b> · waiting market fill",
    "🔴 <b>SELL · Impulse Pullback Scalp</b>",
  ])
  text = setup_card.apply_forming_card_status(
    waiting, "❌ <b>TERMINAL</b> · outside zone",
  )
  assert text.splitlines()[0] == "🔎 <b>XAU M1 · IN ZONE · WAITING FILL</b>"
  assert "TERMINAL" not in text
  assert "WAITING FILL" in text.splitlines()[0]


def test_waiting_fill_gets_plan_expired_status_line():
  """Unfilled expire paints PLAN EXPIRED under the WAITING FILL header."""
  waiting = "\n".join([
    "🔎 <b>XAU M1 · IN ZONE · WAITING FILL</b>",
    "⏳ <b>IN ZONE</b> · waiting market fill",
    "🔴 <b>SELL · Impulse Pullback Scalp</b>",
    "• <b>Price now:</b> <b>4,334.10</b> <i>(live)</i>",
  ])
  text = setup_card.apply_forming_card_status(
    waiting, "⌛ <b>PLAN EXPIRED</b>",
  )
  lines = text.splitlines()
  assert lines[0] == "🔎 <b>XAU M1 · IN ZONE · WAITING FILL</b>"
  assert lines[1] == "⌛ <b>PLAN EXPIRED</b>"
  assert setup_card.should_stop_forming_price_track(text) is True
  assert setup_card._infer_status_state("⌛ <b>PLAN EXPIRED</b>") == "plan_expired"
  assert setup_card.CARD_STATUS_PRIORITY["plan_expired"] > (
    setup_card.CARD_STATUS_PRIORITY["plan_published"]
  )


def test_activated_header_stays_intact_on_terminal_status():
  """Close must not paint TERMINAL on the autotrade root card."""
  activated = "\n".join([
    "✅ <b>POSITION ACTIVATED · XAU M5</b>",
    "🔴 <b>SELL · Key Level</b> · ⭐⭐",
    "• <b>Price now:</b> <b>4,396.18</b> <i>(live)</i>",
  ])
  text = setup_card.apply_forming_card_status(
    activated, "❌ <b>TERMINAL</b> · stop loss or take profit",
  )
  lines = text.splitlines()
  assert lines[0] == "✅ <b>POSITION ACTIVATED · XAU M5</b>"
  assert "TERMINAL" not in text
  assert "SELL · Key Level" in text
  assert "(live)" not in text


def test_forming_card_matches_strategy_detects_stale_body():
  text = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟢 <b>PLAN PUBLISHED</b>",
    "🔴 <b>SELL · Key Level</b> · ⭐⭐",
  ])
  match = SimpleNamespace(
    direction="BUY",
    strategy="Trend Pullback",
  )
  assert setup_card.forming_card_matches_strategy(text, match) is False
  match_ok = SimpleNamespace(
    direction="SELL",
    strategy="Key Level",
  )
  assert setup_card.forming_card_matches_strategy(text, match_ok) is True


def test_event_recovery_root_card_is_activated_on_fill():
  text = setup_card.format_event_recovery_root_card({
    "type": "order_filled",
    "symbol": "XAU",
    "message": "SELL 0.10 lots filled 4334.47",
    "strategy": "Impulse Pullback Scalp",
  })
  assert text.splitlines()[0] == "✅ <b>ORDER ACTIVATED · XAU M1</b>"
  assert "SELL · Impulse Pullback Scalp" in text


def test_parse_forming_card_symbol_from_position_activated_header():
  text = "\n".join([
    "✅ <b>POSITION ACTIVATED · GBPUSD M5</b>",
    "🟢 <b>BUY · Key Level</b>",
  ])
  assert setup_card.parse_forming_card_symbol(text) == "GBPUSD"


@pytest.mark.asyncio
async def test_edit_forming_card_stop_uses_fx_digits_after_activation(monkeypatch):
  monkeypatch.setattr(setup_card, "digits_for", lambda symbol: {
    "GBPUSD": 5, "EURUSD": 5, "XAU": 2,
  }[str(symbol).upper()])
  client = redis_state.get_client()
  setup_id = "setup-stop-fx-activated"
  await _confirmed_setup(client, setup_id)
  original = "\n".join([
    "✅ <b>POSITION ACTIVATED · GBPUSD M5</b>",
    "🟢 <b>BUY · Key Level</b>",
    "• <b>Stop:</b> <b>SL</b>",
  ])
  await setup_card.save_forming_card(
    client, setup_id, chat_id=123, message_id=556, text=original,
  )
  edited = []

  async def edit_fn(chat_id, message_id, text):
    edited.append(text)

  assert await setup_card.edit_forming_card_stop(
    client, setup_id, 1.35806, edit_fn=edit_fn,
  )
  assert "• <b>Stop:</b> <b>1.35806</b>" in edited[0]
  assert "• <b>Stop:</b> <b>1.36</b>" not in edited[0]


@pytest.mark.asyncio
async def test_apply_forming_card_stop_patches_trade_area_stop_line():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-stop")
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V8 sent to executor",
    "• <b>Entry zone:</b> <b>4,072.99–4,076.89</b>",
    "• <b>Key level:</b> <b>4,074.94</b>",
    "• <b>Stop:</b> <b>SL</b>",
    "",
    "→ Executor owns mechanical entry and risk enforcement.",
  ])
  await setup_card.save_forming_card(
    client,
    "setup-stop",
    chat_id=123,
    message_id=555,
    text=original,
  )
  edited = []

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  assert await setup_card.edit_forming_card_stop(
    client, "setup-stop", 4070.5, edit_fn=edit_fn,
  )
  text = edited[0][2]
  assert "• <b>Stop:</b> <b>4,070.50</b>" in text
  assert "• <b>Stop:</b> <b>SL</b>" not in text
  assert "Copy draft" not in text


@pytest.mark.asyncio
async def test_apply_forming_card_stop_still_patches_legacy_copy_draft_if_present():
  """Older cards may still carry a manual copy draft; keep Stop+draft in sync."""
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-stop-legacy")
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V8 sent to executor",
    "• <b>Entry zone:</b> <b>4,072.99–4,076.89</b>",
    "• <b>Key level:</b> <b>4,074.94</b>",
    "",
    "📋 <b>Copy draft</b>",
    "<code>gold buy entry zone (4072.99-4076.89) / sl SL / tp TP1/TP2/TP3 / setup key-level-reaction **</code>",
  ])
  await setup_card.save_forming_card(
    client,
    "setup-stop-legacy",
    chat_id=123,
    message_id=556,
    text=original,
  )
  edited = []

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  assert await setup_card.edit_forming_card_stop(
    client, "setup-stop-legacy", 4070.5, edit_fn=edit_fn,
  )
  text = edited[0][2]
  assert "• <b>Stop:</b> <b>4,070.50</b>" in text
  assert "/ sl 4070.50 /" in text
  assert "sl SL" not in text

@pytest.mark.asyncio
async def test_lifecycle_status_replaces_only_the_card_status_line():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-status")
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟡 <b>QUEUED</b> · worker acknowledgement pending",
    "🔴 <b>SELL · Supply Zone Reaction</b>",
  ])
  await setup_card.save_forming_card(
    client,
    "setup-status",
    chat_id=123,
    message_id=9876,
    text=original,
  )
  edited = []

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  changed = await setup_card.edit_forming_card_status(
    client,
    "setup-status",
    "🟠 <b>WAITING RETEST</b> · executable quote is outside the zone",
    edit_fn=edit_fn,
  )

  assert changed
  assert edited[0][0:2] == (123, 9876)
  assert "WAITING RETEST" in edited[0][2]
  assert "Supply Zone Reaction" in edited[0][2]
  card = await setup_card.load_forming_card(client, "setup-status")
  assert card is not None
  assert card["text"] == edited[0][2]


@pytest.mark.asyncio
async def test_position_activated_rewrites_the_stale_setup_forming_head():
  """A filled position still showing "SETUP FORMING" in the card headline
  reads as "still waiting" when it's already live - only the body line
  used to update on this transition. order_filled must rewrite the
  headline itself too, not just the status line beneath it.
  """
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-activated")
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "​",
    "🔴 <b>SELL · Key Level</b> · ⭐⭐",
  ])
  await setup_card.save_forming_card(
    client,
    "setup-activated",
    chat_id=123,
    message_id=4242,
    text=original,
  )
  edited = []

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  changed = await setup_card.edit_forming_card_status(
    client,
    "setup-activated",
    "✅ <b>POSITION ACTIVATED</b>",
    state="order_filled",
    edit_fn=edit_fn,
  )

  assert changed
  text = edited[0][2]
  lines = text.splitlines()
  assert lines[0] == "✅ <b>ORDER ACTIVATED · XAU M5</b>"
  # Live incident: line[1] used to repeat the identical "POSITION
  # ACTIVATED" text the header now already says, reading as a duplicated
  # line. The header alone is enough. A blank placeholder line was tried
  # (an invisible-character-only line) but Telegram still renders that
  # at full line-height, showing a stray empty line under the header -
  # so the slot line is removed outright instead of blanked.
  assert lines[1] == "🔴 <b>SELL · Key Level</b> · ⭐⭐"
  assert text.count("ORDER ACTIVATED") == 1
  assert "SETUP FORMING" not in text


@pytest.mark.asyncio
async def test_second_fill_event_does_not_double_the_activated_header():
  """Live incident: a multi-leg entry fires order_filled once per leg
  (L1 filled, then ENTRY GROUP FULLY FILLED). The second call used to
  re-parse the header this same function had already rewritten on the
  first call, reading "POSITION ACTIVATED" itself as the symbol/tf
  tokens and mangling the header into "POSITION ACTIVATED · POSITION
  ACTIVATED" - losing the real symbol/timeframe entirely.
  """
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-activated-twice")
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "​",
    "🔴 <b>SELL · Trendline</b> · ⭐⭐",
  ])
  await setup_card.save_forming_card(
    client,
    "setup-activated-twice",
    chat_id=123,
    message_id=4244,
    text=original,
  )
  edited = []

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  await setup_card.edit_forming_card_status(
    client,
    "setup-activated-twice",
    "✅ <b>POSITION ACTIVATED</b>",
    state="order_filled",
    edit_fn=edit_fn,
  )
  await setup_card.edit_forming_card_status(
    client,
    "setup-activated-twice",
    "✅ <b>POSITION ACTIVATED</b>",
    state="order_filled",
    edit_fn=edit_fn,
  )

  assert len(edited) == 1, "second identical fill event should be a no-op edit"
  text = edited[0][2]
  assert text.splitlines()[0] == "✅ <b>ORDER ACTIVATED · XAU M5</b>"
  assert text.count("ORDER ACTIVATED") == 1


@pytest.mark.asyncio
async def test_second_post_fill_status_replaces_not_stacks():
  """After activation, a real status line is re-inserted (SL move). A
  second real status later (TP hit) must replace that same line, not
  stack a new one above the body - both are non-order_filled updates so
  neither one rewrites the header again to signal "already handled".
  """
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-post-fill-status")
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "​",
    "🔴 <b>SELL · Trendline</b> · ⭐⭐",
  ])
  await setup_card.save_forming_card(
    client,
    "setup-post-fill-status",
    chat_id=123,
    message_id=4245,
    text=original,
  )
  edited = []

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  await setup_card.edit_forming_card_status(
    client,
    "setup-post-fill-status",
    "✅ <b>POSITION ACTIVATED</b>",
    state="order_filled",
    edit_fn=edit_fn,
  )
  await setup_card.edit_forming_card_status(
    client,
    "setup-post-fill-status",
    "🛡 <b>SL MOVED TO BE</b>",
    state="sl_moved",
    edit_fn=edit_fn,
  )
  await setup_card.edit_forming_card_status(
    client,
    "setup-post-fill-status",
    "🎯 <b>TP1 HIT</b>",
    state="tp_booked",
    edit_fn=edit_fn,
  )

  final_lines = edited[-1][2].splitlines()
  assert final_lines[0] == "✅ <b>ORDER ACTIVATED · XAU M5</b>"
  assert final_lines[1] == "🎯 <b>TP1 HIT</b>"
  assert final_lines[2] == "🔴 <b>SELL · Trendline</b> · ⭐⭐"
  assert len(final_lines) == 3, "TP status must replace SL line, not stack"


@pytest.mark.asyncio
async def test_non_order_filled_transitions_leave_the_head_untouched():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-preflight")
  original = "\n".join([
    "🔵 <b>XAU M5 · MARKET OBSERVATION</b>",
    "🔵 <b>ANALYSIS ONLY</b> · no executable StrategyMatch",
    "🟢 <b>BUY · Demand Zone Reaction</b> · ⭐",
  ])
  await setup_card.save_forming_card(
    client,
    "setup-preflight",
    chat_id=123,
    message_id=4243,
    text=original,
  )
  edited = []

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  await setup_card.edit_forming_card_status(
    client,
    "setup-preflight",
    "🟡 <b>QUEUED</b> · worker acknowledgement pending",
    state="queued",
    edit_fn=edit_fn,
  )

  assert edited[0][2].splitlines()[0] == "🔵 <b>XAU M5 · MARKET OBSERVATION</b>"


@pytest.mark.asyncio
async def test_status_snapshot_wins_when_worker_finishes_before_card_post():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-race")
  await setup_card.save_forming_card_status(
    client,
    "setup-race",
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V8 sent to executor",
  )
  sent = []

  async def send_fn(text, **kwargs):
    sent.append(text)
    return SimpleNamespace(message_id=6789)

  async def edit_fn(chat_id, message_id, text):
    raise AssertionError("new card should be posted, not edited")

  await setup_card.post_or_edit_forming_card(
    client,
    "setup-race",
    "\n".join([
      "🔎 <b>XAU M5 · SETUP FORMING</b>",
      "🟡 <b>QUEUED</b> · worker acknowledgement pending",
      "🔴 <b>SELL · Supply Zone Reaction</b>",
    ]),
    chat_id=123,
    send_fn=send_fn,
    edit_fn=edit_fn,
  )

  assert len(sent) == 1
  assert "PLAN PUBLISHED" in sent[0]
  assert "QUEUED" not in sent[0]


@pytest.mark.asyncio
async def test_terminal_setup_is_never_re_carded():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-3")
  await transition_setup(client, "setup-3", INVALIDATED, reason_code="structure_broke")
  calls = []

  async def send_fn(text, **kwargs):
    calls.append(text)
    return SimpleNamespace(message_id=1)

  async def edit_fn(chat_id, message_id, text):
    calls.append(text)

  result = await setup_card.post_or_edit_forming_card(
    client, "setup-3", "forming again?", chat_id=123, send_fn=send_fn, edit_fn=edit_fn,
  )

  assert result is None
  assert calls == []


@pytest.mark.asyncio
async def test_kill_setup_card_leaves_root_body_intact(caplog):
  """Close retains POSITION ACTIVATED body; no TERMINAL rewrite."""
  client = redis_state.get_client()
  original = "\n".join([
    "✅ <b>POSITION ACTIVATED · XAU M5</b>",
    "🔴 <b>SELL · Key Level</b> · ⭐⭐",
    "• <b>Price now:</b> <b>4,396.18</b> <i>(live)</i>",
  ])
  await setup_card.save_forming_card(
    client, "setup-intact-kill", chat_id=123, message_id=7777,
    text=original,
  )
  edited = []

  async def delete_fn(chat_id, message_id):
    raise AssertionError("delete should not run on retain path")

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  with caplog.at_level("INFO"):
    await setup_card.kill_setup_card(
      client,
      "setup-intact-kill",
      reason_code="stop_loss_or_take_profit",
      delete_fn=delete_fn,
      edit_fn=edit_fn,
    )

  assert "forming_card_left_intact" in caplog.text
  card = await setup_card.load_forming_card(client, "setup-intact-kill")
  assert card is not None
  assert "TERMINAL" not in card["text"]
  assert "POSITION ACTIVATED · XAU M5" in card["text"]
  assert "(live)" not in card["text"]
  assert edited and "(live)" not in edited[0][2]


@pytest.mark.asyncio
async def test_kill_setup_card_noop_edit_when_already_intact():
  client = redis_state.get_client()
  original = "\n".join([
    "✅ <b>POSITION ACTIVATED · XAU M5</b>",
    "🔴 <b>SELL · Key Level</b>",
  ])
  await setup_card.save_forming_card(
    client, "setup-not-mod-kill", chat_id=123, message_id=7777,
    text=original,
  )

  async def delete_fn(chat_id, message_id):
    raise AssertionError("delete should not run on retain path")

  async def edit_fn(chat_id, message_id, text):
    raise AssertionError("no Telegram edit when body already intact")

  await setup_card.kill_setup_card(
    client,
    "setup-not-mod-kill",
    reason_code="startup_reconciliation_missing_setup",
    delete_fn=delete_fn,
    edit_fn=edit_fn,
  )

  card = await setup_card.load_forming_card(client, "setup-not-mod-kill")
  assert card is not None
  assert card["text"] == original
  assert "TERMINAL" not in card["text"]


@pytest.mark.asyncio
async def test_kill_setup_card_deletes_when_forced(monkeypatch):
  """Legacy delete path remains reachable only via explicit monkeypatch."""
  client = redis_state.get_client()
  monkeypatch.setattr(setup_card, "should_delete_root_on_terminal", lambda: True)
  await setup_card.save_forming_card(client, "setup-4", chat_id=123, message_id=5555)
  await setup_card.save_forming_card_status(
    client,
    "setup-4",
    "🟠 WAITING RETEST",
  )
  deleted = []

  async def delete_fn(chat_id, message_id):
    deleted.append((chat_id, message_id))

  async def edit_fn(chat_id, message_id, text):
    raise AssertionError("edit_fn should not be called when delete succeeds")

  await setup_card.kill_setup_card(
    client, "setup-4", reason_code="structure_broke",
    delete_fn=delete_fn, edit_fn=edit_fn,
  )

  assert deleted == [(123, 5555)]
  assert await setup_card.load_forming_card(client, "setup-4") is None
  assert await setup_card.load_forming_card_status(client, "setup-4") is None


@pytest.mark.asyncio
async def test_kill_setup_card_falls_back_to_terminal_edit_when_delete_fails(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(setup_card, "should_delete_root_on_terminal", lambda: True)
  await setup_card.save_forming_card(client, "setup-5", chat_id=123, message_id=6666)
  edited = []

  async def delete_fn(chat_id, message_id):
    raise TelegramBadRequest(method=None, message="Bad Request: message can't be deleted")

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  await setup_card.kill_setup_card(
    client, "setup-5", reason_code="structure_broke",
    delete_fn=delete_fn, edit_fn=edit_fn,
  )

  assert len(edited) == 1
  assert edited[0][:2] == (123, 6666)
  assert "structure broke" in edited[0][2].lower()
  assert await setup_card.load_forming_card(client, "setup-5") is None


@pytest.mark.asyncio
async def test_kill_setup_card_is_a_noop_with_no_stored_card():
  client = redis_state.get_client()
  await setup_card.save_forming_card_status(
    client,
    "setup-does-not-exist",
    "🟡 PREFLIGHT",
  )
  calls = []

  async def delete_fn(chat_id, message_id):
    calls.append("delete")

  async def edit_fn(chat_id, message_id, text):
    calls.append("edit")

  await setup_card.kill_setup_card(
    client, "setup-does-not-exist", reason_code="structure_broke",
    delete_fn=delete_fn, edit_fn=edit_fn,
  )

  assert calls == []
  assert (
    await setup_card.load_forming_card_status(
      client,
      "setup-does-not-exist",
    )
    is None
  )


@pytest.mark.asyncio
async def test_delete_on_terminal_disabled_retains_root_intact(monkeypatch):
  client = redis_state.get_client()
  await setup_card.save_forming_card(
    client, "setup-6", chat_id=123, message_id=4444,
    text="🔎 <b>XAU M5 · SETUP FORMING</b>\n🔴 <b>SELL · Key Level</b>",
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_telegram_single_root_card": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_telegram_delete_root_on_terminal": False,})
  calls = []

  async def delete_fn(chat_id, message_id):
    calls.append("delete")

  async def edit_fn(chat_id, message_id, text):
    calls.append("edit")

  await setup_card.kill_setup_card(
    client, "setup-6", reason_code="structure_broke",
    delete_fn=delete_fn, edit_fn=edit_fn,
  )

  # Intact body (no live cue) → no Telegram rewrite; root mapping kept.
  assert calls == []
  card = await setup_card.load_forming_card(client, "setup-6")
  assert card is not None
  assert int(card["message_id"]) == 4444
  assert "TERMINAL" not in card["text"]
  assert await setup_card.load_telegram_root_message_id(client, "setup-6") == 4444


@pytest.mark.asyncio
async def test_delete_root_flag_true_still_retains(monkeypatch):
  """Config delete flags are ignored — reject/expire leave body intact."""
  client = redis_state.get_client()
  await setup_card.save_forming_card(
    client, "setup-del", chat_id=123, message_id=3333,
    text="🔎 <b>XAU M5 · SETUP FORMING</b>\n🔴 <b>SELL · Key Level</b>",
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_telegram_single_root_card": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_telegram_delete_root_on_terminal": True,})
  calls = []

  async def delete_fn(chat_id, message_id):
    calls.append("delete")

  async def edit_fn(chat_id, message_id, text):
    calls.append("edit")

  await setup_card.kill_setup_card(
    client, "setup-del", reason_code="expired",
    delete_fn=delete_fn, edit_fn=edit_fn,
  )
  assert calls == []
  assert await setup_card.load_telegram_root_message_id(client, "setup-del") == 3333
  card = await setup_card.load_forming_card(client, "setup-del")
  assert card is not None
  assert "TERMINAL" not in card["text"]


@pytest.mark.asyncio
async def test_load_forming_card_reads_legacy_scalar_format(monkeypatch):
  client = redis_state.get_client()
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 999})
  await client.set(setup_card.forming_message_key("setup-7"), "12345", ex=60)

  card = await setup_card.load_forming_card(client, "setup-7")

  assert card == {"chat_id": 999, "message_id": 12345}


@pytest.mark.asyncio
async def test_identical_status_edit_is_a_local_successful_noop():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-identical")
  status = "🟢 <b>PLAN PUBLISHED</b> · TradePlan V8 sent to executor"
  text = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    status,
    "🔴 <b>SELL · Trendline</b>",
  ])
  await setup_card.save_forming_card(
    client,
    "setup-identical",
    chat_id=123,
    message_id=7001,
    text=text,
  )
  await setup_card.save_forming_card_status(
    client,
    "setup-identical",
    status,
    state="plan_published",
  )
  edits = []

  async def edit_fn(chat_id, message_id, updated):
    edits.append((chat_id, message_id, updated))

  assert await setup_card.edit_forming_card_status(
    client,
    "setup-identical",
    status,
    state="plan_published",
    edit_fn=edit_fn,
  )
  assert edits == []


@pytest.mark.asyncio
async def test_not_modified_status_edit_is_treated_as_success():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-not-modified")
  await setup_card.save_forming_card(
    client,
    "setup-not-modified",
    chat_id=123,
    message_id=7002,
    text="\n".join([
      "🔎 <b>XAU M5 · SETUP FORMING</b>",
      "🟡 <b>QUEUED</b> · worker acknowledgement pending",
      "🔴 <b>SELL · Trendline</b>",
    ]),
  )

  async def edit_fn(chat_id, message_id, updated):
    raise TelegramBadRequest(
      method=None,
      message="Bad Request: message is not modified",
    )

  assert await setup_card.edit_forming_card_status(
    client,
    "setup-not-modified",
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V8 sent to executor",
    state="plan_published",
    edit_fn=edit_fn,
  )
  card = await setup_card.load_forming_card(client, "setup-not-modified")
  assert card is not None
  assert "PLAN PUBLISHED" in card["text"]


@pytest.mark.asyncio
async def test_card_status_is_monotonic_after_plan_publication():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-monotonic")
  await setup_card.save_forming_card(
    client,
    "setup-monotonic",
    chat_id=123,
    message_id=7003,
    text="\n".join([
      "🔎 <b>XAU M5 · SETUP FORMING</b>",
      "🟡 <b>QUEUED</b> · worker acknowledgement pending",
      "🔴 <b>SELL · Trendline</b>",
    ]),
  )

  async def edit_fn(chat_id, message_id, updated):
    return None

  updates = [
    ("queued", "🟡 <b>QUEUED</b> · worker acknowledgement pending"),
    ("preflight", "🟡 <b>PREFLIGHT</b> · dynamic execution checks in progress"),
    (
      "plan_published",
      "🟢 <b>PLAN PUBLISHED</b> · TradePlan V8 sent to executor",
    ),
    (
      "waiting_retest",
      "🟠 <b>WAITING RETEST</b> · executable quote is outside the zone",
    ),
    ("queued", "🟡 <b>QUEUED</b> · worker acknowledgement pending"),
  ]
  for state, status in updates:
    assert await setup_card.edit_forming_card_status(
      client,
      "setup-monotonic",
      status,
      state=state,
      edit_fn=edit_fn,
    )

  snapshot = await setup_card.load_forming_card_status_snapshot(
    client,
    "setup-monotonic",
  )
  assert snapshot is not None
  assert snapshot.state == "plan_published"
  card = await setup_card.load_forming_card(client, "setup-monotonic")
  assert card is not None
  assert "PLAN PUBLISHED" in card["text"]
  assert "WAITING RETEST" not in card["text"]


@pytest.mark.real_redis
@pytest.mark.asyncio
async def test_real_redis_concurrent_card_status_keeps_highest_priority():
  configured = os.getenv("REAL_REDIS_URL")
  if not configured:
    pytest.skip("REAL_REDIS_URL is required")
  source = configured.rsplit("/", 1)[0]
  client = Redis.from_url(f"{source}/12", decode_responses=True)
  await client.flushdb()
  setup_id = "real-redis-monotonic"
  try:
    await asyncio.gather(
      setup_card.save_forming_card_status(
        client,
        setup_id,
        "🟡 <b>QUEUED</b> · worker acknowledgement pending",
        state="queued",
      ),
      setup_card.save_forming_card_status(
        client,
        setup_id,
        "🟢 <b>PLAN PUBLISHED</b> · TradePlan V8 sent to executor",
        state="plan_published",
      ),
      setup_card.save_forming_card_status(
        client,
        setup_id,
        "🟠 <b>WAITING RETEST</b> · executable quote is outside the zone",
        state="waiting_retest",
      ),
    )

    snapshot = await setup_card.load_forming_card_status_snapshot(
      client,
      setup_id,
    )
    assert snapshot is not None
    assert snapshot.state == "plan_published"
    assert snapshot.priority == 100
  finally:
    await client.flushdb()
    await client.aclose()


def _strategy_match_for_card(setup_id: str = "setup-publish-card") -> object:
  from app.autotrade.strategy_match import StrategyMatch

  return StrategyMatch(
    version=1,
    match_id=setup_id,
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-31T13:03:00+00:00",
    issued_at=1_785_502_980,
    expires_at=1_785_503_400,
    strategy="Key Level",
    strategy_mode="with_bias",
    bias_relationship="with_bias",
    direction="BUY",
    key_level=4050.68,
    entry_low=4048.73,
    entry_high=4052.63,
    current_price=4051.6,
    confluence=2,
    reasons=("key reaction x13", "key reaction 4050.68 x13"),
    atr=4.0,
    structure_swing=10.0,
    targets_pips=(20, 40, 60),
    tags=("key_level",),
    structural_source="key_level",
    structural_kind="key_level",
    structural_timeframe="M5",
    reaction_type="sweep_reclaim",
    htf_bias="up (H1)",
  )


def test_root_card_shows_unified_with_bias_line():
  match = _strategy_match_for_card("setup-with-bias")
  text = setup_card.format_plan_published_root_card(match, stop_price=4045.0)
  assert "🧭 <b>Bias:</b> with bias" in text
  assert "Mode:" not in text


def test_root_card_shows_unified_counter_bias_line():
  from dataclasses import replace

  match = replace(
    _strategy_match_for_card("setup-counter-bias"),
    bias_relationship="counter_bias",
  )
  text = setup_card.format_plan_published_root_card(match, stop_price=4045.0)
  assert "⚠️ <b>Bias:</b> counter bias" in text
  assert "Mode:" not in text
  assert "counter swing" not in text
  assert "Counter-trend" not in text


def test_root_card_omits_bias_line_when_relationship_unknown():
  from dataclasses import replace

  match = replace(
    _strategy_match_for_card("setup-neutral-bias"),
    bias_relationship=None,
  )
  text = setup_card.format_plan_published_root_card(match, stop_price=4045.0)
  assert "Bias:" not in text
  assert "Mode:" not in text


def test_root_card_scalp_match_shows_real_bias_not_hardcoded_mode():
  """Live 2026-08-25: HFS scalp cards hardcoded strategy_mode='scalp_m1',
  which always rendered as 'Mode: Counter-trend - counter swing' regardless
  of true bias. bias_relationship is now a separate, correctly-computed
  field so scalp cards show the same unified Bias: line structural cards do.
  """
  from dataclasses import replace

  match = replace(
    _strategy_match_for_card("setup-scalp-bias"),
    strategy="Breakout Retest Scalp",
    strategy_mode="scalp_m1",
    bias_relationship="with_bias",
    structural_source="scalp",
  )
  text = setup_card.format_plan_published_root_card(match, stop_price=4045.0)
  assert "🧭 <b>Bias:</b> with bias" in text
  assert "Counter-trend" not in text
  assert "Mode:" not in text


def test_fx_root_card_uses_instrument_price_digits(monkeypatch):
  """Live 2026-08-21 GBPUSD cards collapsed 1.36447 → 1.36 via hardcoded .2f."""
  from dataclasses import replace

  monkeypatch.setattr(setup_card, "digits_for", lambda symbol: {
    "GBPUSD": 5, "EURUSD": 5, "XAU": 2, "GBPJPY": 3, "USDJPY": 3,
  }[str(symbol).upper()])
  monkeypatch.setattr(setup_card, "pip_for", lambda symbol: {
    "GBPUSD": 0.0001, "EURUSD": 0.0001, "XAU": 0.1, "GBPJPY": 0.01, "USDJPY": 0.01,
  }[str(symbol).upper()])

  match = replace(
    _strategy_match_for_card("fx-card-digits"),
    symbol="GBPUSD",
    key_level=1.36447,
    entry_low=1.36420,
    entry_high=1.36480,
    current_price=1.36447,
    reasons=("demand ifvg 1.36420-1.36480",),
  )
  text = setup_card.format_plan_published_root_card(
    match, stop_price=1.36380,
  )
  assert "1.36–1.36" not in text
  assert "1.36447" in text
  assert "1.36420–1.36480" in text
  assert "1.36380" in text
  assert setup_card.card_price_digits("GBPUSD") == 5


def test_root_card_shows_target_prices_with_pip_offsets():
  # This file's ambient runtime_config has no registered instruments, so
  # the configured-R-multiple lookup can't resolve and falls back to the
  # plain pip offset -- see test_fx_one_to_two.py for the R-multiple path
  # exercised against a real production-shaped instrument config.
  match = _strategy_match_for_card("setup-tp-levels")
  text = setup_card.format_plan_published_root_card(
    match,
    stop_price=4045.0,
    target_prices=(4050.73, 4052.73, 4054.73),
  )
  assert "• <b>TP1:</b> <b>4,050.73 (+20)</b>" in text
  assert "• <b>TP2:</b> <b>4,052.73 (+40)</b>" in text
  assert "• <b>TP3:</b> <b>4,054.73 (+60)</b>" in text


def test_root_card_target_r_multiple_lookup_never_crashes_on_unknown_symbol():
  # Live 2026-09-07 regression risk: an unregistered/unresolvable symbol
  # used to raise EffectiveInstrumentError straight out of the R-multiple
  # lookup instead of falling back to the pip display.
  from dataclasses import replace

  match = replace(
    _strategy_match_for_card("setup-unknown-symbol"), symbol="NOTASYMBOL",
  )
  text = setup_card.format_plan_published_root_card(
    match,
    stop_price=4045.0,
    target_prices=(4050.73, 4052.73, 4054.73),
  )
  assert "• <b>TP1:</b> <b>4,050.73 (+" in text
  assert "R)</b>" not in text


def test_root_card_falls_back_to_targets_pips_ladder():
  """No absolute TP prices known yet - still one labeled line per level."""
  match = _strategy_match_for_card("setup-tp-pips")
  text = setup_card.format_plan_published_root_card(match, stop_price=4045.0)
  assert "• <b>TP1:</b> <b>+20 pips</b>" in text
  assert "• <b>TP2:</b> <b>+40 pips</b>" in text
  assert "• <b>TP3:</b> <b>+60 pips</b>" in text


def test_fx_root_card_shows_target_levels_with_instrument_digits(monkeypatch):
  from dataclasses import replace

  monkeypatch.setattr(setup_card, "digits_for", lambda symbol: {
    "GBPUSD": 5, "EURUSD": 5, "XAU": 2,
  }[str(symbol).upper()])
  monkeypatch.setattr(setup_card, "pip_for", lambda symbol: {
    "GBPUSD": 0.0001, "EURUSD": 0.0001, "XAU": 0.1,
  }[str(symbol).upper()])

  match = replace(
    _strategy_match_for_card("fx-tp-levels"),
    symbol="GBPUSD",
    key_level=1.36447,
    entry_low=1.36420,
    entry_high=1.36480,
    current_price=1.36447,
    targets_pips=(20, 40, 60),
  )
  text = setup_card.format_plan_published_root_card(
    match,
    stop_price=1.36380,
    target_prices=(1.36620, 1.36820, 1.37020),
  )
  assert "• <b>TP1:</b> <b>1.36620 (+20)</b>" in text
  assert "• <b>TP2:</b> <b>1.36820 (+40)</b>" in text
  assert "• <b>TP3:</b> <b>1.37020 (+60)</b>" in text
  assert "1.36 " not in text  # must not collapse FX to 2dp


def test_detector_number_keeps_fx_precision():
  from app.analysis.detectors import _number

  assert _number(1.36447) == "1.36447"
  assert _number(216.917) == "216.917"
  assert _number(4530.12) == "4530.12"


@pytest.mark.asyncio
async def test_ensure_plan_published_root_card_creates_missing_card():
  """Direct-publish path must create the first PLAN PUBLISHED root card."""
  client = redis_state.get_client()
  setup_id = "setup-publish-card"
  await _confirmed_setup(client, setup_id)
  match = _strategy_match_for_card(setup_id)
  sent = []

  async def send_fn(text, **kwargs):
    sent.append(text)
    return SimpleNamespace(message_id=4242)

  async def edit_fn(chat_id, message_id, text):
    raise AssertionError("edit should not run when no card exists yet")

  message_id = await setup_card.ensure_plan_published_root_card(
    client,
    match,
    chat_id=123,
    send_fn=send_fn,
    edit_fn=edit_fn,
  )

  assert message_id == 4242
  assert len(sent) == 1
  text = sent[0]
  assert "IN ZONE · WAITING FILL" in text
  assert "SETUP FORMING" not in text
  assert "PLAN PUBLISHED" not in text
  assert "waiting market fill" in text
  assert "Trade area" in text
  assert "Entry zone" in text
  assert "Key level" in text
  assert "Stop" in text
  assert "TP1:</b> <b>+20 pips" in text
  assert "TP2:</b> <b>+40 pips" in text
  assert "TP3:</b> <b>+60 pips" in text
  assert "Context" in text
  assert "Identity" not in text
  assert "Kind:" not in text
  assert "Copy draft" not in text
  assert "<code>" not in text
  card = await setup_card.load_forming_card(client, setup_id)
  assert card is not None
  assert card["message_id"] == 4242
  # Reply-thread anchors used by take_profit / stop_moved / position_closed.
  assert await client.get(setup_card.forming_message_key(setup_id))
  assert await client.get(setup_card.telegram_root_message_key(setup_id))
  root = await setup_card.load_telegram_root_message_id(client, setup_id)
  assert root == 4242
  status = await setup_card.load_forming_card_status(client, setup_id)
  assert status is not None
  assert "PLAN PUBLISHED" not in status


@pytest.mark.asyncio
async def test_ensure_plan_published_root_card_threads_tp_sl_close_replies(monkeypatch):
  """TP archive / trailing SL / close must reply_to the ensured root card."""
  from app.autotrade import delivery

  client = redis_state.get_client()
  setup_id = "setup-publish-thread"
  await _confirmed_setup(client, setup_id)
  match = _strategy_match_for_card(setup_id)

  async def send_fn(text, **kwargs):
    return SimpleNamespace(message_id=6060)

  async def edit_fn(chat_id, message_id, text):
    return None

  message_id = await setup_card.ensure_plan_published_root_card(
    client,
    match,
    chat_id=123,
    send_fn=send_fn,
    edit_fn=edit_fn,
  )
  assert message_id == 6060

  calls = []
  edited = []
  deleted = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=7000 + len(calls))

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  async def fake_delete(chat_id, message_id):
    deleted.append((chat_id, message_id))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  monkeypatch.setattr(delivery, "delete_scanner_message", fake_delete)

  for event in (
    {
      "type": "take_profit",
      "match_id": setup_id,
      "message": "TP1 +30 pips closed volume 200",
      "position_id": 1,
      "stop_pips": 65,
    },
    {
      "type": "stop_moved",
      "match_id": setup_id,
      "message": "🛡 ApexVoid Algo stop → 4,044.91 (breakeven)",
      "price": 4044.91,
      "position_id": 1,
    },
    {
      "type": "position_closed",
      "match_id": setup_id,
      "message": "Highest TP archived TP3 · +81.0 pips",
      "target_pips": 81.0,
      "position_id": 1,
    },
  ):
    await delivery._deliver_auto_trade_event(
      client,
      event,
      profile="internal",
      chat_id=123,
      send=sent,
    )

  # TP creates manage reply; BE/trail and close each delete+repost under root.
  assert len(calls) == 3
  assert all(c[1]["reply_to"] == 6060 for c in calls)
  assert "🎯" in calls[0][0] and "TP1" in calls[0][0]
  assert any("BE" in text or "Trail" in text or "Stop" in text for text, _ in calls)
  assert "POSITION CLOSED" in calls[-1][0]
  assert "TP3" in calls[-1][0] or "+81.0" in calls[-1][0]
  assert deleted == [(123, 7001), (123, 7002)]
  # Root forming card itself is never deleted here.
  assert all(d[1] != 6060 for d in deleted)
  # Trailing / BE update the manage reply; Trade-area Stop stays as published.
  card = await setup_card.load_forming_card(client, setup_id)
  assert card is not None
  assert "4,044.91" not in card["text"] and "4044.91" not in card["text"]
  assert "🛰️" not in card["text"] and "🔐" not in card["text"]


def test_apply_forming_card_targets_inserts_after_stop_on_scanner_body():
  """Scanner SETUP FORMING omits Targets — publish must insert after Stop."""
  original = "\n".join([
    "🔎 <b>EURUSD M5 · SETUP FORMING</b>",
    "🔴 <b>SELL · Trend Pullback</b> · ⭐⭐",
    "",
    "📍 <b>Trade area</b>",
    "• <b>Price now:</b> <b>1.15978</b> <i>(live)</i>",
    "• <b>Entry zone:</b> <b>1.15992–1.16001</b>",
    "• <b>Key level:</b> <b>1.15992</b>",
    "• <b>Stop:</b> <b>1.16092</b>",
    "",
    "🧭 <b>Context</b>",
    "• <b>HTF bias:</b> up (M5)",
  ])
  targets = "• <b>Targets:</b> <b>TP1 1.15892 (+10)</b>"
  text = setup_card.apply_forming_card_targets(original, targets)
  lines = text.splitlines()
  stop_i = next(i for i, line in enumerate(lines) if "Stop:" in line)
  assert lines[stop_i + 1] == targets
  assert text.count("Targets:") == 1


def test_apply_forming_card_targets_replaces_existing_line():
  original = "\n".join([
    "📍 <b>Trade area</b>",
    "• <b>Stop:</b> <b>1.16092</b>",
    "• <b>Targets:</b> <b>+20 / +40 pips</b>",
    "",
    "🧭 <b>Context</b>",
  ])
  text = setup_card.apply_forming_card_targets(
    original, "• <b>Targets:</b> <b>TP1 1.15892 (+10)</b>",
  )
  assert "TP1 1.15892 (+10)" in text
  assert "+20 / +40 pips" not in text
  assert text.count("Targets:") == 1


@pytest.mark.asyncio
async def test_ensure_plan_published_patches_targets_onto_scanner_card(monkeypatch):
  """Existing scanner body matching strategy must gain Targets on publish."""
  client = redis_state.get_client()
  setup_id = "setup-fx-missing-targets"
  await _confirmed_setup(client, setup_id)
  from dataclasses import replace

  match = replace(
    _strategy_match_for_card(setup_id),
    symbol="EURUSD",
    strategy="Trend Pullback",
    direction="SELL",
    key_level=1.15992,
    entry_low=1.15992,
    entry_high=1.16001,
    current_price=1.15978,
    structure_swing=1.16092,
    targets_pips=(10,),
    structural_source="supply_demand",
  )
  original = "\n".join([
    "🔎 <b>EURUSD M5 · SETUP FORMING</b>",
    "​",
    "🔴 <b>SELL · Trend Pullback</b> · ⭐⭐",
    "🧱 <b>Structural source:</b> supply_demand",
    "",
    "📍 <b>Trade area</b>",
    "• <b>Price now:</b> <b>1.15978</b> <i>(live)</i>",
    "• <b>Entry zone:</b> <b>1.15992–1.16001</b>",
    "• <b>Key level:</b> <b>1.15992</b>",
    "• <b>Stop:</b> <b>1.16092</b>",
    "",
    "🧭 <b>Context</b>",
  ])
  await setup_card.save_forming_card(
    client, setup_id, chat_id=123, message_id=555, text=original,
  )
  edited = []

  async def send_fn(text, **kwargs):
    raise AssertionError("should edit existing card")

  async def edit_fn(chat_id, message_id, text):
    edited.append(text)
    await setup_card.save_forming_card(
      client, setup_id, chat_id=chat_id, message_id=message_id, text=text,
    )

  async def fake_stop(_client, _match_id):
    return 1.16092

  async def fake_targets(_client, _match_id):
    return (1.15892,)

  monkeypatch.setattr(setup_card, "published_plan_stop_price", fake_stop)
  monkeypatch.setattr(setup_card, "published_plan_target_prices", fake_targets)
  monkeypatch.setattr(setup_card, "digits_for", lambda symbol: 5)
  monkeypatch.setattr(setup_card, "pip_for", lambda symbol: 0.0001)

  message_id = await setup_card.ensure_plan_published_root_card(
    client,
    match,
    chat_id=123,
    send_fn=send_fn,
    edit_fn=edit_fn,
  )
  assert message_id == 555
  card = await setup_card.load_forming_card(client, setup_id)
  assert card is not None
  assert "TP1:" in card["text"]
  assert "1.15892" in card["text"]
  assert any("TP1:" in text for text in edited)


@pytest.mark.asyncio
async def test_ensure_plan_published_root_card_edits_existing_status_only():
  client = redis_state.get_client()
  setup_id = "setup-publish-existing"
  await _confirmed_setup(client, setup_id)
  match = _strategy_match_for_card(setup_id)
  # Same direction/strategy as the match so publish only refreshes status,
  # not the whole body (wrong-direction bodies are rewritten separately).
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟡 <b>QUEUED</b> · worker acknowledgement pending",
    "🟢 <b>BUY · Key Level</b>",
  ])
  await setup_card.save_forming_card(
    client, setup_id, chat_id=123, message_id=777, text=original,
  )
  sent = []
  edited = []

  async def send_fn(text, **kwargs):
    sent.append(text)
    return SimpleNamespace(message_id=999)

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  message_id = await setup_card.ensure_plan_published_root_card(
    client,
    match,
    chat_id=123,
    send_fn=send_fn,
    edit_fn=edit_fn,
  )

  assert message_id == 777
  assert sent == []
  # Existing card keeps its head status — no PLAN PUBLISHED rewrite.
  assert all("PLAN PUBLISHED" not in text for _, _, text in edited)
  card = await setup_card.load_forming_card(client, setup_id)
  assert card is not None
  assert "QUEUED" in card["text"] or any(
    "QUEUED" in text for _, _, text in edited
  )
  assert "PLAN PUBLISHED" not in card["text"]
  # Match has targets_pips — publish must still surface TP lines.
  assert "TP1:" in card["text"] or any(
    "TP1:" in text for _, _, text in edited
  )


@pytest.mark.asyncio
async def test_ensure_plan_published_root_card_rewrites_mismatched_strategy_body():
  client = redis_state.get_client()
  setup_id = "setup-publish-mismatch"
  await _confirmed_setup(client, setup_id)
  match = _strategy_match_for_card(setup_id)
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟡 <b>QUEUED</b> · worker acknowledgement pending",
    "🔴 <b>SELL · Key Level</b>",
  ])
  await setup_card.save_forming_card(
    client, setup_id, chat_id=123, message_id=888, text=original,
  )
  edited = []

  async def send_fn(text, **kwargs):
    raise AssertionError("should edit existing card, not send")

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  message_id = await setup_card.ensure_plan_published_root_card(
    client,
    match,
    chat_id=123,
    send_fn=send_fn,
    edit_fn=edit_fn,
  )
  assert message_id == 888
  assert edited
  card = await setup_card.load_forming_card(client, setup_id)
  assert card is not None
  assert "🟢 <b>BUY · Key Level</b>" in card["text"]
  assert "PLAN PUBLISHED" not in card["text"]
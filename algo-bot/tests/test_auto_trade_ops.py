import json
from pathlib import Path
from unittest.mock import AsyncMock
from app.core.config import runtime_config
from app.configuration.python_loader import load_python_canonical_settings
from app.configuration.python_sources import load_python_runtime_source_bundle
from app.configuration.source_policy import PythonConfigurationSourcePolicy
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.autotrade import delivery
from app.autotrade import setup_card
from app.persistence import redis_state, store


def _opened_event() -> dict:
  return {
    "type": "opened",
    "message": (
      "SELL 0.06 lots filled 4,111.26, SL 4,117.76 · "
      "65p structure · risk-bound"
    ),
    "position_id": 39000344,
    "group_id": "group-39000344",
    "setup": "Box Breakout",
    "regime": "breakout",
    "confluence": 3,
    "stop_pips": 65,
    "targets_pips": [30, 60, 90, 120, 200],
  }


def test_render_auto_trade_event_filters_noise_and_escapes_message():
  rejected = delivery.render_auto_trade_event({
    "type": "rejected",
    "message": "ordinary candidate rejection",
  })
  # One forming card per setup (P4): a reject never becomes a card - it
  # deletes the forming card instead (see _deliver_auto_trade_event).
  assert rejected is None
  text = delivery.render_auto_trade_event({
    "type": "opened",
    "message": "BUY <0.12> lots",
    "position_id": 91,
  })
  assert "ApexVoid Algo" in text
  assert "ORDER FILLED" in text
  assert "Position opened" in text
  assert "BUY &lt;0.12&gt; lots" in text
  assert "91" not in text
  assert "auto trade" not in text.lower()


def test_execution_lifecycle_cards_suppress_noise_keep_essentials():
  for silent in (
    "candidate_published",
    "order_submitted",
    "order_accepted",
    "managing",
    "position_managing",
    "config_fatal",
    "broker_fatal",
    "configuration_health",
    "config_health",
  ):
    assert delivery.render_auto_trade_event({
      "type": silent,
      "strategy": "Range Edge Scalp",
      "direction": "BUY",
      "message": "noise",
    }) is None

  assert delivery.render_auto_trade_event({
    "type": "rejected",
    "reason_code": "duplicate_reaction_active",
    "message": "duplicate",
  }) is None
  assert delivery.render_auto_trade_event({
    "type": "rejected",
    "reason_code": "already_processed",
  }) is None

  waiting = delivery.render_auto_trade_event({
    "type": "zone_planned",
    "message": "BUY limit is armed",
  })
  closed = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "BUY position is closed",
  })
  rejected = delivery.render_auto_trade_event({
    "type": "rejected",
    "message": "stop plan invalid",
  })

  assert "WAITING FOR PRICE" in waiting
  assert "POSITION CLOSED" in closed
  assert rejected is None


def test_position_closed_labels_broker_stop_loss_or_take_profit():
  closed = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "SELL position is closed",
    "reason_code": "stop_loss_or_take_profit",
  })
  assert "Closed by broker SL/TP" in closed
  assert "Closed manually" not in closed


def test_position_closed_labels_manual_or_external_close():
  closed = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "SELL position is closed",
    "reason_code": "manual_or_external_close",
  })
  assert "Closed manually on platform" in closed
  assert "Closed by broker SL/TP" not in closed


def test_position_closed_near_stop_not_labeled_manual():
  """2026-08-28 XAU Flip Zone: SL @ 4604.47, exit 4604.16 mislabeled manual."""
  event = {
    "type": "position_closed",
    "message": (
      "PLAN CLOSED · manual_or_external · losing -34 pips · @ 4604.16"
    ),
    "reason_code": "manual_or_external_close",
    "group_realized_pips": -34.0,
    "price": 4604.16,
    "stop_loss": 4604.47,
    "direction": "SELL",
    "symbol": "XAU",
  }
  compact = delivery._format_position_closed_compact_line(
    event, str(event["message"]),
  )
  assert "Closed manually" not in compact
  assert "🛡 SL" in compact
  assert "Losing: -34.0 pips" in compact


def test_position_closed_break_even_message_uses_be_label():
  event = {
    "type": "position_closed",
    "message": "PLAN CLOSED · stop_loss_or_take_profit · break-even · @ 4600.84",
    "reason_code": "stop_loss_or_take_profit",
    "group_realized_pips": 0.0,
    "price": 4600.84,
    "stop_loss": 4600.84,
    "direction": "SELL",
    "symbol": "XAU",
    "break_even_applied": True,
  }
  compact = delivery._format_position_closed_compact_line(
    event, str(event["message"]),
  )
  assert "Closed manually" not in compact
  assert "0 pips (BE)" in compact


def test_position_closed_manual_close_reports_winning_pips():
  closed = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "PLAN CLOSED · manual_or_external · winning 18 pips · @ 4094.50",
    "reason_code": "manual_or_external_close",
    "group_realized_pips": 18.0,
    "price": 4094.50,
  })
  assert "Closed manually on platform" in closed
  assert "Winning:" in closed
  assert "+18.0 pips" in closed
  assert "@ <b>4094.50</b>" in closed


def test_algo_auto_manual_close_does_not_invent_stop_loss():
  closed = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": (
      "position closed at broker: manual or external order · winning 18.0 pips"
    ),
    "reason_code": "manual_or_external_close",
    "group_realized_pips": 18.0,
    "stop_pips": 25.0,
    "price": 4094.50,
    "stream": "algo_auto",
  })
  assert "Closed manually on platform" in closed
  assert "Winning:" in closed
  assert "+18.0 pips" in closed
  assert "Closed by broker SL/TP" not in closed
  compact = delivery._format_position_closed_compact_line(
    {
      "reason_code": "manual_or_external_close",
      "group_realized_pips": 18.0,
      "stop_pips": 25.0,
    },
    "position closed at broker: manual or external order · winning 18.0 pips",
  )
  assert "Winning:" in compact
  assert "SL" not in compact


def test_algo_auto_unconfirmed_close_does_not_invent_stop_loss():
  closed = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": (
      "position is no longer open at broker (reason unconfirmed) · winning 9.0 pips"
    ),
    "group_realized_pips": 9.0,
    "stop_pips": 41.5,
    "price": 4094.50,
    "stream": "algo_auto",
  })
  assert "Closed by broker SL/TP" not in closed
  assert "Winning:" in closed
  assert "+9.0 pips" in closed


def test_position_closed_omits_label_when_reason_unconfirmed():
  closed = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "SELL position is closed",
  })
  assert "Closed by broker SL/TP" not in closed
  assert "Closed manually" not in closed


def test_position_closed_recaps_total_after_earlier_partial_tps():
  # SL hit after being moved to BE/TP2/TP3 - the final leg's own pips are
  # not the story; the owner needs the blended group result (earlier TP
  # legs already booked their own pips in their own cards).
  closed = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "position is no longer open at broker (reason unconfirmed)",
    "previous_state": "partially_closed",
    "group_realized_pips": 7.975,
  })
  assert "Total: <b>+8.0 pips</b>" in closed


def test_position_closed_omits_total_when_never_partially_closed():
  # A one-shot SL (no prior TP legs) reports Losing, not a Total recap.
  closed = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "SELL position is closed",
    "reason_code": "stop_loss_or_take_profit",
    "group_realized_pips": -12.0,
  })
  assert "Total:" not in closed
  assert "❌ Losing:" in closed
  assert "-12.0 pips" in closed

def test_render_box_open_and_full_tp_as_shareable_cards():
  opened = delivery.render_auto_trade_event({
    "type": "opened",
    "message": (
      "Sell 0.04 lots filled 4,066.78, SL 4,070.63 · 39p structure · "
      "full TP 50p · range 4,062.00-4,069.00 · risk-bound"
    ),
    "position_id": 39025496,
  })
  take_profit = delivery.render_auto_trade_event({
    "type": "take_profit",
    "message": "FULL TP +51.3 pips closed volume 400",
    "position_id": 39025496,
    "price": 4061.78,
    "volume": 400,
    "group_initial_volume": 400,
    "remaining_volume": 0,
    "leg_realized_pips": 51.3,
    "group_realized_pips": 51.3,
    "lot_size": 10_000,
    "group_realized_pnl": 71.82,
  })

  assert "XAU SELL opened" in opened
  assert "Entry: <b>4,066.78</b>" in opened
  assert "SL: <b>4,070.63</b> · 39 pips" in opened
  assert "Full TP: <b>4,061.78</b> · +50 pips" in opened
  assert "Box: <b>4,062.00–4,069.00</b>" in opened
  assert "39025496" not in opened
  assert "✅ FULL TP closed" in take_profit
  assert "Leg: <b>+51.3 pips</b>" in take_profit
  assert "Initial volume" not in take_profit
  assert "lot" not in take_profit.lower()
  assert "$" not in take_profit
  assert "71.82" not in take_profit
  assert "39025496" not in take_profit
  assert "Auto trade" not in (opened + take_profit)


@pytest.mark.no_database
def test_fx_delivery_uses_symbol_pips_and_price_digits(monkeypatch):
  config_file = Path(__file__).resolve().parents[2] / "config" / "trading-bot.yml"
  policy = PythonConfigurationSourcePolicy(config_file=str(config_file))
  production = load_python_canonical_settings(
    load_python_runtime_source_bundle(policy=policy),
  ).config
  install_runtime_overrides(monkeypatch, base=production)

  opened = delivery.render_auto_trade_event({
    "type": "opened",
    "symbol": "EURUSD",
    "message": (
      "BUY 0.02 lots filled 1.08456, SL 1.08156 · 30p structure · "
      "full TP 60p · risk-bound"
    ),
  })
  route = delivery.render_auto_trade_event({
    "type": "strategy_route",
    "symbol": "EURUSD",
    "status": "candidate_published",
    "strategy": "Key Level",
    "direction": "BUY",
    "measured": {
      "planned_execution_route": "market",
      "planned_entry_price": 1.08456,
      "entry_low": 1.08421,
      "entry_high": 1.08467,
    },
  })
  stop = delivery.render_auto_trade_event({
    "type": "stop_moved",
    "symbol": "EURUSD",
    "price": 1.08567,
  })

  assert "EURUSD BUY opened" in opened
  assert "Full TP: <b>1.09056</b> · +60 pips" in opened
  assert "Planned entry: <b>1.08456</b>" in route
  assert "Zone: <b>1.08421–1.08467</b>" in route
  assert "move SL to <b>1.08567</b>" in stop


def test_partial_and_final_tp_use_volume_weighted_pips_not_money():
  partial = delivery.render_auto_trade_event({
    "type": "take_profit",
    "message": "TP1 +48.4 pips closed volume 300",
    "daily_seq": 1,
    "volume": 300,
    "remaining_volume": 600,
    "group_initial_volume": 900,
    "leg_realized_pips": 48.4,
    "group_realized_pips": 16.133333,
    "lot_size": 10_000,
  })
  final = delivery.render_auto_trade_event({
    "type": "take_profit",
    "message": "TP2 +0.9 pips closed volume 600",
    "daily_seq": 1,
    "volume": 600,
    "remaining_volume": 0,
    "group_initial_volume": 900,
    "leg_realized_pips": 0.9,
    "group_realized_pips": 16.7,
    "lot_size": 10_000,
  })

  assert "✅ #1 TP2 closed" in final
  assert "Leg: <b>+0.9 pips</b>" in final
  assert "Net so far" not in final
  assert "Total net" not in final
  assert partial == (
    "🤖 <b>ApexVoid Algo</b>\n"
    "🎯 #1 TP1 booked 33.3%\n"
    "Leg: <b>+48.4 pips</b>"
  )
  assert "Initial volume" not in final
  assert "lot" not in final.lower()
  assert "$" not in partial + final


@pytest.mark.no_database
def test_partial_tp_without_volume_percent_uses_closed_volume_fallback():
  partial = delivery.render_auto_trade_event({
    "type": "take_profit",
    "message": "TP1 +48.4 pips closed volume 300",
    "volume": 300,
    "leg_realized_pips": 48.4,
  })

  assert partial is not None
  assert "closed volume <b>300</b>" in partial
  assert "Leg: <b>+48.4 pips</b>" in partial
  assert "33.3%" not in partial


@pytest.mark.no_database
def test_stop_moved_renders_compact_move_sl_line():
  stop = delivery.render_auto_trade_event({
    "type": "stop_moved",
    "message": "🛡 ApexVoid Algo stop → 4,087.60 (BE+6 ticks) · position 39016393",
    "direction": "BUY",
    "entry_price": 4087.66,
    "previous_stop": 4081.66,
    "price": 4087.60,
    "mode": "BE+6 ticks",
    "buffer_price": 0.06,
    "trigger_tp1_broker_confirmed": True,
  })

  assert stop == (
    "🤖 <b>ApexVoid Algo</b>\n"
    "🛡 move SL to <b>4,087.60</b>"
  )


@pytest.mark.no_database
def test_strategy_route_plan_published_shows_executor_fields():
  text = delivery.render_auto_trade_event({
    "type": "strategy_route",
    "status": "candidate_published",
    "strategy": "Key Level",
    "direction": "BUY",
    "measured": {
      "planned_execution_route": "market",
      "planned_entry_price": 4100.5,
      "entry_low": 4100.0,
      "entry_high": 4100.3,
      "executor_distance_pips": 2.5,
      "executor_limit_pips": 10.0,
    },
  })

  assert "Algo bot PLAN PUBLISHED" in text
  assert "Algo bot READY" not in text
  assert "Route: <b>market</b>" in text
  assert "Planned entry: <b>4,100.50</b>" in text
  assert "Executor distance: <b>2.5p</b>" in text
  assert "Executor limit: <b>10.0p</b>" in text
  assert "Drift" not in text


@pytest.mark.no_database
@pytest.mark.parametrize("status", ["waiting", "blocked", "executor_rejected"])
def test_strategy_route_preflight_outcomes_are_telegram_silent(status):
  text = delivery.render_auto_trade_event({
    "type": "strategy_route",
    "status": status,
    "reason_code": "executor_entry_envelope_exceeded",
    "strategy": "Mapped Zone Reaction",
    "direction": "BUY",
    "measured": {
      "executor_quote": 4053.56,
      "entry_low": 4050.0,
      "entry_high": 4052.0,
      "executor_distance_pips": 15.6,
      "executor_limit_pips": 10.0,
    },
  })

  assert text is None



def test_essential_trade_lifecycle_still_renders():
  filled = delivery.render_auto_trade_event(_opened_event())
  partial = delivery.render_auto_trade_event({
    "type": "take_profit",
    "message": "TP1 +30.4 pips closed volume 300",
    "volume": 300,
    "remaining_volume": 600,
    "group_initial_volume": 900,
    "leg_realized_pips": 30.4,
  })
  protected = delivery.render_auto_trade_event({
    "type": "stop_moved",
    "message": "🛡 ApexVoid Algo stop → 4,100.00 (breakeven)",
  })
  closed = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "position is no longer open at broker (SL or manual close)",
    "group_realized_pips": 7.2,
    "daily_seq": 3,
  })
  closed_without_net = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "BUY position is closed",
  })
  rejected = delivery.render_auto_trade_event({
    "type": "rejected",
    "message": "volume planning failed",
  })

  assert "ORDER FILLED" in filled
  assert "TP1 booked 33.3%" in partial
  assert "Leg: <b>+30.4 pips</b>" in partial
  assert "Remaining" not in partial
  assert "lot" not in partial.lower()
  assert "move SL to" in protected
  assert "POSITION CLOSED" in closed
  assert "Total net" not in closed
  assert "POSITION CLOSED" in closed_without_net
  assert rejected is None


@pytest.mark.asyncio
async def test_silent_lifecycle_events_still_persist_in_redis(monkeypatch):
  client = redis_state.get_client()
  recorded = []

  async def _capture(*args, **kwargs):
    recorded.append((args, kwargs))
    return {"state": args[1] if len(args) > 1 else kwargs.get("state")}

  monkeypatch.setattr(delivery, "emit_lifecycle", _capture)
  await delivery._record_lifecycle_event(client, {
    "type": "opened",
    "lifecycle_id": "life-1",
    "candidate_id": "cand-1",
    "symbol": "XAU",
    "message": "filled",
  })
  # Managing is still emitted internally after fill, even though Telegram is silent.
  states = [call[0][1] for call in recorded]
  assert "order_filled" in states
  assert "managing" in states
  assert delivery.render_auto_trade_event({"type": "managing"}) is None


def test_opened_event_renders_strategy_attribution():
  opened = delivery.render_auto_trade_event({
    "type": "opened",
    "message": (
      "Sell 0.04 lots filled 4,066.78, SL 4,070.63 · 39p structure · "
      "full TP 50p · range 4,062.00-4,069.00 · risk-bound"
    ),
    "position_id": 39025496,
    "candidate_id": "a" * 64,
    "setup": "Range Box Scalp",
    "regime": "chop",
    "confluence": 3,
  })

  assert "Range Box Scalp" in opened
  assert "chop" in opened
  assert "★★★" in opened


def test_opened_event_without_attribution_degrades_gracefully():
  opened = delivery.render_auto_trade_event({
    "type": "opened",
    "message": "Sell 0.04 lots filled 4,066.78, SL 4,070.63 · 39p structure · legacy",
    "position_id": 1,
  })
  assert opened is not None
  assert "🧭" not in opened


def test_render_auto_trade_stop_and_warning_events():
  stop = delivery.render_auto_trade_event({
    "type": "stop_moved",
    "message": "🛡 Auto trade stop → 4,029.49 (BE+3) · position 39016393",
    "position_id": 39016393,
  })
  warning = delivery.render_auto_trade_event({
    "type": "warning",
    "message": "token grants live account 44669326 — re-authorize as demo only",
  })

  assert "ApexVoid Algo" in stop
  assert "move SL to" in stop
  assert "4,029.49" in stop
  assert "39016393" not in stop
  assert "Warning" in warning
  assert "live account 44669326" in warning
  assert "auto trade" not in (stop + warning).lower()


def test_render_scale_in_zone_and_group_events():
  scale_in = delivery.render_auto_trade_event({
    "type": "add",
    "message": "Tranche 2 · 0.08 lots · exposure-bound",
  })
  zone = delivery.render_auto_trade_event({
    "type": "zone_planned",
    "message": "two limits",
  })
  result = delivery.render_auto_trade_event({
    "type": "group_result",
    "message": "realised 42.0 pips · no-add 31.0 pips",
    "group_realized_pips": 42.0,
  })

  assert "Scale-in filled" in scale_in
  assert "WAITING FOR PRICE" in zone
  # group_result no longer renders a card - its only content used to be a
  # net-pip summary, which each TP/close event already reports per leg.
  assert result is None
  assert "ApexVoid Algo" in scale_in + zone


def test_internal_profile_hides_broker_position_id():
  assert delivery.render_auto_trade_event(_opened_event(), profile="internal") == (
    "🤖 <b>ApexVoid Algo</b>\n"
    "✅ <b>ORDER FILLED</b>\n"
    "🔴 <b>XAU SELL opened</b>\n"
    "\n"
    "📍 Entry: <b>4,111.26</b>\n"
    "🛡 SL: <b>4,117.76</b> · 65 pips\n"
    "🧭 Box Breakout · breakout · ★★★"
  )


def test_public_profile_hides_position_and_lot_and_keeps_ladder():
  text = delivery.render_auto_trade_event(
    _opened_event(),
    profile="public",
    footer="Trade responsibly.",
  )

  assert "39000344" not in text
  assert "0.06" not in text
  assert "lot" not in text.lower()
  assert "Position" not in text
  assert "Targets: <b>+30 / +60 / +90 / +120 / +200 pips</b>" in text
  assert text.endswith("Trade responsibly.")


def test_public_take_profit_computes_r_from_event_stop_distance():
  text = delivery.render_auto_trade_event({
    "type": "take_profit",
    "message": "TP1 +30.0 pips closed volume 200",
    "position_id": 39000344,
    "target_pips": 30,
    "stop_pips": 65,
    "volume": 200,
    "remaining_volume": 400,
    "group_initial_volume": 600,
    "leg_realized_pips": 30.0,
    "lot_size": 10_000,
  }, profile="public")

  assert "+0.46R" in text
  assert "39000344" not in text
  assert "$" not in text
  assert "lot" not in text.lower()


def test_group_result_no_longer_renders_a_card():
  text = delivery.render_auto_trade_event({
    "type": "group_result",
    "message": "group abc realised $42.00 · 16.7 pips",
    "group_realized_pips": 16.7,
    "group_realized_pnl": 42.0,
  })
  assert text is None



def test_empty_public_footer_adds_no_trailing_blank_lines():
  text = delivery.render_auto_trade_event(
    _opened_event(),
    profile="public",
    footer="",
  )

  assert text == text.rstrip()
  assert not text.endswith("\n\n")


@pytest.mark.asyncio
async def test_opened_event_stores_message_id_with_ttl():
  client = redis_state.get_client()

  async def sent(*args, **kwargs):
    return SimpleNamespace(message_id=8123)

  await delivery._deliver_auto_trade_event(
    client,
    _opened_event(),
    profile="internal",
    chat_id=123,
    send=sent,
  )

  key = "auto_trade:msg:39000344"
  assert await client.get(key) == "8123"
  assert 0 < await client.ttl(key) <= 7 * 24 * 3600


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_order_filled_replies_using_v8_plan_id_without_head_fill(monkeypatch):
  client = redis_state.get_client()
  setup_id = "ece2d0168a881f82d8a7fa673c36d40e"
  await client.set(
    delivery._forming_message_key(setup_id),
    json.dumps({
      "chat_id": 123,
      "message_id": 7001,
      "text": "\n".join([
        "🔎 <b>XAU M5 · SETUP FORMING</b>",
        "🟢 <b>PLAN PUBLISHED</b> · TradePlan V8 sent to executor",
        "🟢 <b>BUY · Key Level</b>",
      ]),
    }),
    ex=60,
  )
  edited = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=8123)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "order_filled",
      "candidate_id": f"v8:{setup_id}",
      "group_id": f"v8:{setup_id}",
      "message": "ENTRY L1 FILLED volume=800 @ 4074.68; L2 still pending",
      "position_id": 39000344,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert len(calls) == 1
  assert calls[0][1]["reply_to"] == 7001
  body = calls[0][0]
  assert "✅ <b>ORDER FILLED</b>" in body
  assert "• ✅" not in body
  assert "• ENTRY L1 FILLED lot=0.08 @ 4074.68; L2 still pending" in body
  # Reply keeps ORDER FILLED; SETUP FORMING head becomes ORDER ACTIVATED.
  # The header alone carries the text now - the status slot beneath it
  # collapses to invisible instead of repeating it (see setup_card.py's
  # apply_forming_card_status).
  head_edits = [e for e in edited if e[1] == 7001]
  assert len(head_edits) == 1
  assert "✅ <b>ORDER ACTIVATED · XAU M5</b>" in head_edits[0][2].splitlines()[0]
  assert "ORDER FILLED" not in head_edits[0][2]
  assert "PLAN PUBLISHED" not in head_edits[0][2]
  card = json.loads(await client.get(delivery._forming_message_key(setup_id)))
  assert "✅ <b>ORDER ACTIVATED · XAU M5</b>" in card["text"].splitlines()[0]
  assert "ORDER FILLED" not in card["text"]
  assert "PLAN PUBLISHED" not in card["text"]


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_order_filled_prefers_live_forming_card_over_stale_root(monkeypatch):
  client = redis_state.get_client()
  setup_id = "stale-root-setup"
  await client.set(
    delivery._forming_message_key(setup_id),
    json.dumps({
      "chat_id": 123,
      "message_id": 9002,
      "text": "🔎 <b>XAU M5 · SETUP FORMING</b>\n🟢 <b>PLAN PUBLISHED</b>",
    }),
    ex=60,
  )
  await client.set(
    setup_card.telegram_root_message_key(setup_id),
    json.dumps({"chat_id": 123, "root_message_id": 1111, "updated_at": 1}),
    ex=60,
  )
  edited = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=8123)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "order_filled",
      "match_id": setup_id,
      "message": "ENTRY L1 FILLED lot=800 @ 4063.41; L2 still pending",
      "position_id": 39758961,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert len(calls) == 1
  assert calls[0][1]["reply_to"] == 9002
  assert "✅ <b>ORDER FILLED</b>" in calls[0][0]
  assert "• ✅" not in calls[0][0]
  # SETUP FORMING head becomes ORDER ACTIVATED; stale root id 1111 unused.
  head_edits = [e for e in edited if e[1] == 9002]
  assert len(head_edits) == 1
  assert "✅ <b>ORDER ACTIVATED · XAU M5</b>" in head_edits[0][2]
  assert all(e[1] != 1111 for e in edited)


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_tp_booked_does_not_overwrite_forming_card_head(monkeypatch):
  """TP stays on the manage reply; forming head stays free of fill/TP noise."""
  client = redis_state.get_client()
  setup_id = "tp-head-setup"
  head = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V8 sent to executor",
    "🔴 <b>SELL · Key Level</b>",
  ])
  await client.set(
    delivery._forming_message_key(setup_id),
    json.dumps({"chat_id": 123, "message_id": 9003, "text": head}),
    ex=60,
  )
  edited = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=8124)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "tp_booked",
      "match_id": setup_id,
      "message": "TP COMPLETED TP3 closed L1 lot=0.02 L2 lot=0.01 remaining lot=0.03 (2/2)",
      "price": 4030.0,
      "target_pips": 50,
      "position_id": 1,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  # No manage reply yet → one fallback create; never edit the forming head.
  assert len(calls) == 1
  assert calls[0][1]["reply_to"] == 9003
  assert "🎯 TP3 · 💰 Fill: 4030.00 · ✅ Achieved: +50.0 pips" in calls[0][0]
  assert "TP COMPLETED" not in calls[0][0]
  head_edits = [e for e in edited if e[1] == 9003]
  assert head_edits == []
  card = json.loads(await client.get(delivery._forming_message_key(setup_id)))
  assert "PLAN PUBLISHED" in card["text"].splitlines()[1]
  assert "ORDER FILLED" not in card["text"]
  assert "TP COMPLETED" not in card["text"]


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_order_filled_stores_manage_keys_and_second_fill_replaces(monkeypatch):
  client = redis_state.get_client()
  setup_id = "manage-fill-setup"
  await client.set(
    delivery._forming_message_key(setup_id),
    json.dumps({
      "chat_id": 123,
      "message_id": 7001,
      "text": "\n".join([
        "🔎 <b>XAU M5 · SETUP FORMING</b>",
        "🟢 <b>PLAN PUBLISHED</b>",
        "🟢 <b>BUY · Key Level</b>",
      ]),
    }),
    ex=60,
  )
  edited = []
  deleted = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  async def fake_delete(chat_id, message_id):
    deleted.append((chat_id, message_id))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  monkeypatch.setattr(delivery, "delete_scanner_message", fake_delete)
  calls = []
  next_id = {"n": 8123}

  async def sent(text, **kwargs):
    mid = next_id["n"]
    next_id["n"] += 1
    calls.append((text, kwargs, mid))
    return SimpleNamespace(message_id=mid)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "order_filled",
      "match_id": setup_id,
      "message": "ENTRY L1 FILLED lot=0.08 @ 4034.50; L2 still pending",
      "position_id": 1,
      "reason_code": "l1_fill",
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )
  assert len(calls) == 1
  assert await client.get(delivery._manage_msg_key(setup_id)) == "8123"
  assert "ORDER FILLED" in (await client.get(delivery._manage_text_key(setup_id)) or "")

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "order_filled",
      "match_id": setup_id,
      "message": "ENTRY GROUP FULLY FILLED BUY lot=0.11 weighted=4034.20",
      "position_id": 1,
      "reason_code": "l2_fill",
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )
  assert deleted == [(123, 8123)]
  assert len(calls) == 2  # second fill deletes old + posts new
  assert await client.get(delivery._manage_msg_key(setup_id)) == "8124"
  assert "FULLY FILLED" in calls[1][0]
  assert calls[1][1]["reply_to"] == 7001
  # Root card may be edited for ORDER ACTIVATED; manage msg itself is never edited.
  assert all(e[1] != 8123 for e in edited)


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_tp_booked_replaces_manage_reply_accumulates_lines(monkeypatch):
  client = redis_state.get_client()
  setup_id = "manage-tp-setup"
  await client.set(
    delivery._forming_message_key(setup_id),
    json.dumps({
      "chat_id": 123,
      "message_id": 7001,
      "text": "\n".join([
        "🔎 <b>XAU M5 · SETUP FORMING</b>",
        "✅ <b>ORDER FILLED</b> · L1 filled",
        "🟢 <b>BUY · Key Level</b>",
      ]),
    }),
    ex=60,
  )
  fill_body = "\n".join([
    "🤖 <b>ApexVoid Algo</b>",
    "✅ <b>ORDER FILLED</b>",
    "• ENTRY L1 FILLED lot=0.08 @ 4034.50",
  ])
  await delivery._save_manage_message(
    client, setup_id, message_id=8123, text=fill_body,
  )
  edited = []
  deleted = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  async def fake_delete(chat_id, message_id):
    deleted.append((chat_id, message_id))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  monkeypatch.setattr(delivery, "delete_scanner_message", fake_delete)
  calls = []
  next_id = {"n": 9001}

  async def sent(text, **kwargs):
    mid = next_id["n"]
    next_id["n"] += 1
    calls.append((text, kwargs, mid))
    return SimpleNamespace(message_id=mid)

  for target, price, pips in (("TP1", 4029.98, 41.0), ("TP2", 4010.0, 60.0)):
    await delivery._deliver_auto_trade_event(
      client,
      {
        "type": "tp_booked",
        "match_id": setup_id,
        "message": f"TP COMPLETED {target} closed L1 lot=0.02 remaining lot=0.06 (1/2)",
        "price": price,
        "target_pips": pips,
        "position_id": 1,
        "reason_code": f"{target.lower()}_booked",
      },
      profile="internal",
      chat_id=123,
      send=sent,
    )

  assert deleted == [(123, 8123), (123, 9001)]
  assert len(calls) == 2
  final = calls[-1][0]
  assert "✅ <b>ORDER FILLED</b>" in final
  assert "🎯 TP1 · 💰 Fill: 4029.98 · ✅ Achieved: +41.0 pips" in final
  assert "🎯 TP2 · 💰 Fill: 4010.00 · ✅ Achieved: +60.0 pips" in final
  assert calls[-1][1]["reply_to"] == 7001
  assert await client.get(delivery._manage_msg_key(setup_id)) == "9002"
  head_edits = [e for e in edited if e[1] == 7001]
  assert head_edits == []


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_sl_moved_be_updates_manage_reply_not_head(monkeypatch):
  client = redis_state.get_client()
  setup_id = "manage-be-setup"
  head = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V8 sent to executor",
    "🟢 <b>BUY · Key Level</b>",
    "📍 <b>Trade area</b>",
    "• <b>Stop:</b> <b>4,020.00</b>",
  ])
  await client.set(
    delivery._forming_message_key(setup_id),
    json.dumps({"chat_id": 123, "message_id": 7001, "text": head}),
    ex=60,
  )
  fill_body = "\n".join([
    "🤖 <b>ApexVoid Algo</b>",
    "✅ <b>ORDER FILLED</b>",
    "• ENTRY L1 FILLED lot=0.08 @ 4034.50",
  ])
  await delivery._save_manage_message(
    client, setup_id, message_id=8123, text=fill_body,
  )
  edited = []
  deleted = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  async def fake_delete(chat_id, message_id):
    deleted.append((chat_id, message_id))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  monkeypatch.setattr(delivery, "delete_scanner_message", fake_delete)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=9001)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "sl_moved",
      "match_id": setup_id,
      "message": "GROUP SL MOVED TO BE 4034.99 (2/2)",
      "price": 4034.99,
      "position_id": 1,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert deleted == [(123, 8123)]
  assert len(calls) == 1
  assert calls[0][1]["reply_to"] == 7001
  assert "BE" in calls[0][0]
  assert "4034.99" in calls[0][0]
  assert await client.get(delivery._manage_msg_key(setup_id)) == "9001"
  head_edits = [e for e in edited if e[1] == 7001]
  assert head_edits == []


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_sl_moved_trail_updates_manage_reply_not_head(monkeypatch):
  client = redis_state.get_client()
  setup_id = "manage-trail-setup"
  head = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V8 sent to executor",
    "🟢 <b>BUY · Key Level</b>",
    "📍 <b>Trade area</b>",
    "• <b>Stop:</b> <b>4,034.99</b>",
  ])
  await client.set(
    delivery._forming_message_key(setup_id),
    json.dumps({"chat_id": 123, "message_id": 7001, "text": head}),
    ex=60,
  )
  fill_body = "\n".join([
    "🤖 <b>ApexVoid Algo</b>",
    "✅ <b>ORDER FILLED</b>",
    "• ENTRY L1 FILLED lot=0.08 @ 4034.50",
    "🔐 <b>BE</b> · 4034.99",
  ])
  await delivery._save_manage_message(
    client, setup_id, message_id=8124, text=fill_body,
  )
  edited = []
  deleted = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  async def fake_delete(chat_id, message_id):
    deleted.append((chat_id, message_id))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  monkeypatch.setattr(delivery, "delete_scanner_message", fake_delete)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=9002)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "sl_moved",
      "match_id": setup_id,
      "message": "SL MOVED to 4070.31 (trail TP1)",
      "price": 4070.31,
      "position_id": 1,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert deleted == [(123, 8124)]
  assert len(calls) == 1
  assert "Trail" in calls[0][0]
  assert "4070.31" in calls[0][0]
  assert "🔐" not in calls[0][0]
  assert calls[0][1]["reply_to"] == 7001
  head_edits = [e for e in edited if e[1] == 7001]
  assert head_edits == []


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_position_closed_replaces_manage_reply_under_card(monkeypatch):
  client = redis_state.get_client()
  setup_id = "manage-close-setup"
  await client.set(
    delivery._forming_message_key(setup_id),
    json.dumps({
      "chat_id": 123,
      "message_id": 7001,
      "text": "🔎 <b>XAU M5 · SETUP FORMING</b>\n🛰️ <b>Trail</b> · 4070.31",
    }),
    ex=60,
  )
  fill_body = "\n".join([
    "🤖 <b>ApexVoid Algo</b>",
    "✅ <b>ORDER FILLED</b>",
    "• ENTRY L1 FILLED lot=0.08 @ 4034.50",
    "🎯 TP1 · 💰 Fill: 4029.98 · ✅ Achieved: +41.0 pips",
  ])
  await delivery._save_manage_message(
    client, setup_id, message_id=8123, text=fill_body,
  )
  edited = []
  deleted = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  async def fake_delete(chat_id, message_id):
    deleted.append((chat_id, message_id))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  monkeypatch.setattr(delivery, "delete_scanner_message", fake_delete)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=9001)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "position_closed",
      "match_id": setup_id,
      "message": "PLAN CLOSED · highest TP archived TP2 · @ 4106.00",
      "price": 4106.0,
      "target_pips": 90,
      "position_id": 1,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert deleted == [(123, 8123)]
  assert len(calls) == 1
  final = calls[0][0]
  assert calls[0][1]["reply_to"] == 7001
  assert "✅ <b>ORDER FILLED</b>" in final
  assert "🎯 TP1 · 💰 Fill: 4029.98 · ✅ Achieved: +41.0 pips" in final
  assert "🎯 TP2 · 💰 Fill: 4106.00 · ✅ Achieved: +90.0 pips" in final
  assert "🏁 POSITION CLOSED" in final
  assert "@ 4106.00" in final
  assert "• @ 4106.00" not in final
  assert "Highest TP archived" not in final
  assert await client.get(delivery._manage_msg_key(setup_id)) == "9001"


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_position_closed_appends_missing_final_tp_line(monkeypatch):
  """Engine skips tp_booked on final target — close must still show that TP."""
  client = redis_state.get_client()
  setup_id = "manage-close-final-tp"
  await client.set(
    delivery._forming_message_key(setup_id),
    json.dumps({
      "chat_id": 123,
      "message_id": 7001,
      "text": "🔎 <b>XAU M5 · SETUP FORMING</b>\n🛰️ <b>Trail</b>",
    }),
    ex=60,
  )
  fill_body = "\n".join([
    "🤖 <b>ApexVoid Algo</b>",
    "✅ <b>ORDER FILLED</b>",
    "• ENTRY L1 FILLED lot=0.08 @ 4034.50",
    "🎯 TP1 · 💰 Fill: 4029.98 · ✅ Achieved: +41.0 pips",
  ])
  await delivery._save_manage_message(
    client, setup_id, message_id=8123, text=fill_body,
  )
  edited = []
  deleted = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  async def fake_delete(chat_id, message_id):
    deleted.append((chat_id, message_id))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  monkeypatch.setattr(delivery, "delete_scanner_message", fake_delete)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=9001)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "position_closed",
      "match_id": setup_id,
      "message": "PLAN CLOSED · highest TP archived TP3",
      "price": 4010.0,
      "target_pips": 81.0,
      "position_id": 1,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert deleted == [(123, 8123)]
  assert len(calls) == 1
  final = calls[0][0]
  assert "🎯 TP1 · 💰 Fill: 4029.98 · ✅ Achieved: +41.0 pips" in final
  assert "🎯 TP3 · 💰 Fill: 4010.00 · ✅ Achieved: +81.0 pips" in final
  assert "🏁 POSITION CLOSED" in final
  assert "Highest TP archived" not in final
  # Root card stays intact (no TERMINAL rewrite); close lives on the reply.
  assert all(e[1] != 7001 or "TERMINAL" not in e[2] for e in edited)
  assert all(d[1] != 7001 for d in deleted)


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_position_closed_fallback_creates_manage_reply(monkeypatch):
  """No prior fill manage msg → one reply under the card with close line."""
  client = redis_state.get_client()
  setup_id = "manage-close-fallback"
  await client.set(
    delivery._forming_message_key(setup_id),
    json.dumps({
      "chat_id": 123,
      "message_id": 7001,
      "text": "🔎 <b>XAU M5 · SETUP FORMING</b>\n🛰️ <b>Trail</b> · 4070.31",
    }),
    ex=60,
  )
  edited = []
  deleted = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  async def fake_delete(chat_id, message_id):
    deleted.append((chat_id, message_id))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  monkeypatch.setattr(delivery, "delete_scanner_message", fake_delete)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=9001)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "position_closed",
      "match_id": setup_id,
      "message": "PLAN CLOSED · highest TP archived TP2 · @ 4106.00",
      "price": 4106.0,
      "target_pips": 90,
      "position_id": 1,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert len(calls) == 1
  assert calls[0][1]["reply_to"] == 7001
  assert all(e[1] != 7001 or "TERMINAL" not in e[2] for e in edited)
  assert deleted == []
  assert "🏁 POSITION CLOSED" in calls[0][0]
  assert "🎯 TP2 · 💰 Fill: 4106.00 · ✅ Achieved: +90.0 pips" in calls[0][0]
  assert await client.get(delivery._manage_msg_key(setup_id)) == "9001"


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_order_filled_skips_standalone_when_reply_rejected(monkeypatch):
  client = redis_state.get_client()
  setup_id = "reply-reject-setup"
  await client.set(
    delivery._forming_message_key(setup_id),
    json.dumps({
      "chat_id": 123,
      "message_id": 7001,
      "text": "🔎 <b>XAU M5 · SETUP FORMING</b>\n🟢 <b>PLAN PUBLISHED</b>",
    }),
    ex=60,
  )
  edited = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    if kwargs.get("reply_to") is not None:
      raise TelegramBadRequest(
        method=SimpleNamespace(),
        message="Bad Request: message to be replied not found",
      )
    return SimpleNamespace(message_id=9999)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "order_filled",
      "match_id": setup_id,
      "message": "ENTRY L1 FILLED lot=800 @ 4063.41",
      "position_id": 1,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  # Reply target gone → skip standalone spam; still mark root ORDER ACTIVATED.
  assert len(calls) == 1
  assert calls[0][1]["reply_to"] == 7001
  head_edits = [e for e in edited if e[1] == 7001]
  assert len(head_edits) == 1
  assert "✅ <b>ORDER ACTIVATED · XAU M5</b>" in head_edits[0][2]
  card = json.loads(await client.get(delivery._forming_message_key(setup_id)))
  assert "ORDER FILLED" not in card["text"]
  assert "ORDER ACTIVATED" in card["text"]


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_opened_event_replies_to_stored_forming_message():
  client = redis_state.get_client()
  match_id = "supply:M5:4062.49:4066.18:sweep"
  await client.set(
    delivery._forming_message_key(match_id),
    "7001",
    ex=60,
  )
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=8123)

  event = _opened_event()
  event["match_id"] = match_id
  await delivery._deliver_auto_trade_event(
    client,
    event,
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert len(calls) == 1
  assert calls[0][1]["reply_to"] == 7001


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_rejected_event_retains_root_card_and_sends_nothing(monkeypatch):
  # One forming card per setup: reject leaves the root body intact and
  # sends nothing new (single-root retain mode).
  client = redis_state.get_client()
  match_id = "supply:M5:4062.49:4066.18:sweep"
  await client.set(
    delivery._forming_message_key(match_id),
    json.dumps({"chat_id": 123, "message_id": 7001, "text": "forming"}),
    ex=60,
  )
  deleted = []
  edited = []

  async def fake_delete(chat_id, message_id):
    deleted.append((chat_id, message_id))

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  monkeypatch.setattr(delivery, "delete_scanner_message", fake_delete)
  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=8124)

  result = await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "rejected",
      "match_id": match_id,
      "message": "executor veto",
      "reason_code": "entry_drift_exceeded",
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert result is False
  assert calls == []
  assert deleted == []
  assert all("TERMINAL" not in (e[2] or "") for e in edited)
  stored = await client.get(delivery._forming_message_key(match_id))
  assert stored is not None
  assert "TERMINAL" not in stored


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_blocked_strategy_route_does_not_reply_to_forming_message():
  client = redis_state.get_client()
  match_id = "supply:M5:4062.49:4066.18:sweep"
  await client.set(
    delivery._forming_message_key(match_id),
    "7001",
    ex=60,
  )
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=8125)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "strategy_route",
      "match_id": match_id,
      "status": "blocked",
      "strategy": "Supply Zone Reaction",
      "direction": "SELL",
      "message": "opposing barrier veto",
      "reason_code": "opposing_barrier",
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert calls == []
  assert await client.get(delivery._forming_message_key(match_id)) == "7001"


@pytest.mark.asyncio
async def test_take_profit_replies_to_stored_order_message():
  client = redis_state.get_client()
  await client.set("auto_trade:msg:39000344", "8123", ex=60)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=8124)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "take_profit",
      "message": "TP1 +30 pips closed volume 200",
      "position_id": 39000344,
      "stop_pips": 65,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert len(calls) == 1
  assert calls[0][1]["reply_to"] == 8123
  assert await client.get("auto_trade:tp_msg:39000344") == "8124"


@pytest.mark.asyncio
async def test_take_profit_prefers_the_forming_card_over_position_chain():
  # One forming card per setup (P4): when a forming card exists for the
  # event's match_id, take_profit/stop_moved/position_closed reply directly
  # to it - not to the position-chain "opened" message (_reply_message_id).
  client = redis_state.get_client()
  match_id = "supply:M5:4062.49:4066.18:sweep"
  await client.set(
    delivery._forming_message_key(match_id),
    json.dumps({"chat_id": 123, "message_id": 7001}),
    ex=60,
  )
  await client.set("auto_trade:msg:39000344", "8123", ex=60)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=8124)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "take_profit",
      "match_id": match_id,
      "message": "TP1 +30 pips closed volume 200",
      "position_id": 39000344,
      "stop_pips": 65,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert len(calls) == 1
  assert calls[0][1]["reply_to"] == 7001


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_stop_moved_and_position_closed_also_thread_to_forming_card(
  monkeypatch,
):
  client = redis_state.get_client()
  match_id = "supply:M5:4062.49:4066.18:sweep"
  await client.set(
    delivery._forming_message_key(match_id),
    json.dumps({
      "chat_id": 123,
      "message_id": 7001,
      "text": "\n".join([
        "🔎 <b>XAU M5 · SETUP FORMING</b>",
        "✅ <b>ORDER FILLED</b>",
        "Trade area: Entry 4090.00 · Stop 4080.00",
      ]),
    }),
    ex=60,
  )
  edited = []
  deleted = []

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  async def fake_delete(chat_id, message_id):
    deleted.append((chat_id, message_id))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)
  monkeypatch.setattr(delivery, "delete_scanner_message", fake_delete)
  calls = []
  next_id = {"n": 8124}

  async def sent(text, **kwargs):
    mid = next_id["n"]
    next_id["n"] += 1
    calls.append((text, kwargs, mid))
    return SimpleNamespace(message_id=mid)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "stop_moved",
      "match_id": match_id,
      "message": "🛡 ApexVoid Algo stop → 4,100.00 (breakeven)",
      "position_id": 39000344,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )
  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "position_closed",
      "match_id": match_id,
      "message": "BUY position is closed",
      "position_id": 39000344,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  # BE/trail seeds manage reply; close deletes it and posts an updated reply.
  assert len(calls) == 2
  assert calls[0][1]["reply_to"] == 7001
  assert "BE" in calls[0][0] or "Trail" in calls[0][0] or "Stop" in calls[0][0]
  assert deleted == [(123, 8124)]
  assert calls[1][1]["reply_to"] == 7001
  assert "POSITION CLOSED" in calls[1][0]
  # Root forming card itself is never deleted here.
  assert all(d[1] != 7001 for d in deleted)


@pytest.mark.asyncio
async def test_thread_lifecycle_disabled_reverts_to_position_chain(monkeypatch):
  client = redis_state.get_client()
  match_id = "supply:M5:4062.49:4066.18:sweep"
  await client.set(
    delivery._forming_message_key(match_id),
    json.dumps({"chat_id": 123, "message_id": 7001}),
    ex=60,
  )
  await client.set("auto_trade:msg:39000344", "8123", ex=60)
  install_runtime_overrides(monkeypatch, legacy_overrides={"delivery_thread_lifecycle": False})
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=9999)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "take_profit",
      "match_id": match_id,
      "message": "TP1 +30 pips closed volume 200",
      "position_id": 39000344,
      "stop_pips": 65,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert calls[0][1]["reply_to"] == 8123


@pytest.mark.asyncio
async def test_manual_algo_events_never_dm_the_owner():
  """Manual /algo signals get their lifecycle update on the VIP/public
  channel via app.signals.manual_execution's reconcile loop - a separate
  '🤖 ApexVoid Algo' owner DM for the same take_profit/stop_moved/
  position_closed event would be a duplicate, not new information.
  """
  calls = []

  async def sent(text, **kwargs):
    calls.append(text)
    return SimpleNamespace(message_id=1)

  for event_type in ("take_profit", "stop_moved", "position_closed"):
    delivered = await delivery._deliver_auto_trade_event(
      redis_state.get_client(),
      {
        "type": event_type,
        "message": "irrelevant",
        "position_id": 1,
        "setup": "Manual Algo",
      },
      profile="internal",
      chat_id=123,
      send=sent,
    )
    assert delivered is False

  assert calls == []


@pytest.mark.asyncio
async def test_session_bootstrap_batches_startup_into_one_message():
  """Full engine bootstrap (config + ready + capability) → one owner DM."""
  calls = []

  async def sent(text, **kwargs):
    calls.append(text)
    return SimpleNamespace(message_id=1)

  client = redis_state.get_client()
  await client.delete("auto_trade:session_bootstrap_batch")
  await client.delete("auto_trade:session_notify:sent")

  assert await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "warning",
      "message": (
        "token grants live account 44669326 — "
        "re-authorize with the demo account only"
      ),
      "symbol": "XAU",
    },
    profile="internal",
    chat_id=123,
    send=sent,
  ) is False
  assert await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "config_health",
      "message": "configuration health healthy",
      "symbol": "XAU",
    },
    profile="internal",
    chat_id=123,
    send=sent,
  ) is False
  assert await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "ready",
      "message": "demo executor ready: fpmarketssc balance 924.87",
      "symbol": "XAU",
    },
    profile="internal",
    chat_id=123,
    send=sent,
  ) is False
  assert await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "account_capability",
      "message": "demo account supports hedged two-sided XAU execution",
      "symbol": "XAU",
    },
    profile="internal",
    chat_id=123,
    send=sent,
  ) is True

  assert len(calls) == 1
  assert "Engine ready" in calls[0]
  assert "Balance <b>$924.87</b>" in calls[0]
  assert "token grants live account 44669326" in calls[0]
  assert "configuration health healthy" in calls[0]
  assert "hedged two-sided" in calls[0]


@pytest.mark.asyncio
async def test_session_bootstrap_notify_dedupes_ready_spam():
  """cTrader reconnect republishes ready — owner gets one DM per cooldown."""
  calls = []

  async def sent(text, **kwargs):
    calls.append(text)
    return SimpleNamespace(message_id=1)

  client = redis_state.get_client()
  await client.delete("auto_trade:session_bootstrap_batch")
  await client.delete("auto_trade:session_notify:sent")

  event = {
    "type": "ready",
    "message": "demo executor ready: fpmarketssc balance 924.87",
    "symbol": "XAU",
  }
  assert await delivery._deliver_auto_trade_event(
    client, event, profile="internal", chat_id=123, send=sent,
  ) is True
  assert len(calls) == 1
  assert "Engine ready" in calls[0]
  assert "Balance <b>$924.87</b>" in calls[0]
  assert await delivery._deliver_auto_trade_event(
    client, event, profile="internal", chat_id=123, send=sent,
  ) is False
  assert len(calls) == 1


@pytest.mark.asyncio
async def test_missing_message_key_sends_standalone_without_position_id():
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=8124)

  await delivery._deliver_auto_trade_event(
    redis_state.get_client(),
    {
      "type": "take_profit",
      "message": "TP1 +30 pips closed volume 200",
      "position_id": 39000344,
      "stop_pips": 65,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert len(calls) == 1
  assert calls[0][1]["reply_to"] is None
  assert "39000344" not in calls[0][0]


@pytest.mark.asyncio
async def test_full_tp_merges_result_and_suppresses_duplicate_group_reply():
  client = redis_state.get_client()
  await client.set("auto_trade:msg:39000344", "8123", ex=60)
  calls = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=8124)

  full_tp = {
    "type": "take_profit",
    "message": "FULL TP +51.3 pips closed volume 400",
    "position_id": 39000344,
    "group_id": "group-39000344",
    "price": 4061.78,
    "volume": 400,
    "remaining_volume": 0,
    "group_initial_volume": 400,
    "leg_realized_pips": 51.3,
    "group_realized_pips": 51.3,
    "lot_size": 10_000,
    "group_realized_pnl": 71.82,
  }
  group_result = {
    "type": "group_result",
    "message": (
      "group group-39000344 realised 51.3 pips · "
      "no-add counterfactual 51.3 pips · adds degraded"
    ),
    "position_id": 39000344,
    "group_id": "group-39000344",
    "group_realized_pips": 51.3,
    "group_realized_pnl": 71.82,
  }

  delivered_tp = await delivery._deliver_auto_trade_event(
    client,
    full_tp,
    profile="internal",
    chat_id=123,
    send=sent,
  )
  delivered_group = await delivery._deliver_auto_trade_event(
    client,
    group_result,
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert delivered_tp is True
  assert delivered_group is False
  assert len(calls) == 1
  assert calls[0][1]["reply_to"] == 8123
  assert "Leg: <b>+51.3 pips</b>" in calls[0][0]
  assert "$" not in calls[0][0]
  assert "71.82" not in calls[0][0]
  assert "39000344" not in calls[0][0]


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_terminal_dedupe_allows_one_close_card_per_group():
  client = redis_state.get_client()
  calls = []

  async def sent(text, **kwargs):
    calls.append(text)
    return SimpleNamespace(message_id=9001)

  group_id = "group-terminal-dedupe"
  position_closed = {
    "type": "position_closed",
    "message": "BUY position is closed",
    "group_id": group_id,
    "group_realized_pips": 7.2,
  }
  group_result = {
    "type": "group_result",
    "message": f"group {group_id} realised 7.2 pips",
    "group_id": group_id,
    "group_realized_pips": 7.2,
  }

  delivered_closed = await delivery._deliver_auto_trade_event(
    client,
    position_closed,
    profile="internal",
    chat_id=123,
    send=sent,
  )
  delivered_result = await delivery._deliver_auto_trade_event(
    client,
    group_result,
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert delivered_closed is True
  assert delivered_result is False
  assert len(calls) == 1
  assert "POSITION CLOSED" in calls[0]


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_full_tp_suppresses_position_closed_terminal_card():
  client = redis_state.get_client()
  calls = []

  async def sent(text, **kwargs):
    calls.append(text)
    return SimpleNamespace(message_id=9002)

  group_id = "group-full-tp-terminal"
  await client.set(
    delivery._full_tp_result_key("internal", group_id),
    "1",
    ex=60,
  )
  delivered = await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "position_closed",
      "message": "position is closed",
      "group_id": group_id,
      "group_realized_pips": 51.3,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert delivered is False
  assert calls == []


@pytest.mark.asyncio
async def test_bad_reply_target_retries_once_standalone():
  client = redis_state.get_client()
  await client.set("auto_trade:msg:39000344", "8123", ex=60)
  calls = []

  async def sent(text, **kwargs):
    calls.append(kwargs)
    if len(calls) == 1:
      raise TelegramBadRequest(
        method=None,
        message="Bad Request: message to be replied not found",
      )
    return SimpleNamespace(message_id=8124)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "take_profit",
      "message": "TP1 +30 pips closed volume 200",
      "position_id": 39000344,
      "stop_pips": 65,
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert [call["reply_to"] for call in calls] == [8123, None]


@pytest.mark.asyncio
async def test_owner_delivery_failure_keeps_cursor_for_replay():
  client = redis_state.get_client()
  await client.set(delivery._CURSOR_KEY, "100-0")
  entries = [("101-0", {"payload": json.dumps(_opened_event())})]
  calls = []

  async def failed_send(text, **kwargs):
    calls.append("failed")
    raise TelegramBadRequest(
      method=None,
      message="Bad Request: owner temporarily unavailable",
    )

  with pytest.raises(TelegramBadRequest, match="temporarily unavailable"):
    await delivery._process_owner_entries(
      client,
      entries,
      cursor="100-0",
      chat_id=123,
      send=failed_send,
    )

  assert await client.get(delivery._CURSOR_KEY) == "100-0"

  async def replayed_send(text, **kwargs):
    calls.append("replayed")
    return SimpleNamespace(message_id=9123)

  cursor = await delivery._process_owner_entries(
    client,
    entries,
    cursor="100-0",
    chat_id=123,
    send=replayed_send,
  )

  assert cursor == "101-0"
  assert await client.get(delivery._CURSOR_KEY) == "101-0"
  assert calls == ["failed", "replayed"]


@pytest.mark.asyncio
async def test_auto_trade_loop_only_starts_owner_delivery(monkeypatch):
  calls = []

  async def owner_loop(*, chat_id):
    calls.append(chat_id)

  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 123})
  install_runtime_overrides(monkeypatch, legacy_overrides={"signal_public_channel_id": -100456})
  monkeypatch.setattr(delivery, "_auto_trade_owner_events_loop", owner_loop)

  await delivery.auto_trade_events_loop()

  assert calls == [123]


@pytest.mark.asyncio
async def test_scale_in_replies_to_group_root_and_starts_tranche_thread():
  client = redis_state.get_client()
  await client.set(
    "auto_trade:group_msg:group-39000344",
    "8123",
    ex=60,
  )
  calls = []

  async def sent(text, **kwargs):
    calls.append(kwargs)
    return SimpleNamespace(message_id=8125)

  await delivery._deliver_auto_trade_event(
    client,
    {
      "type": "add",
      "message": "Tranche 2 · 0.03 lots · exposure-bound",
      "position_id": 39000345,
      "group_id": "group-39000344",
    },
    profile="internal",
    chat_id=123,
    send=sent,
  )

  assert calls[0]["reply_to"] == 8123
  assert await client.get("auto_trade:msg:39000345") == "8125"


@pytest.mark.asyncio
async def test_group_stats_split_adds_and_deduplicate():
  client = redis_state.get_client()
  event = {
    "type": "group_result",
    "group_id": "group-a",
    "had_adds": True,
    "group_realized_pnl": 42,
    "counterfactual_pnl": 31,
    "group_realized_pips": 84,
    "counterfactual_pips": 73,
  }

  await delivery._record_group_result(client, event)
  await delivery._record_group_result(client, event)
  await delivery._record_group_result(client, {
    "type": "group_result",
    "group_id": "group-b",
    "had_adds": False,
    "group_realized_pnl": 7,
  })

  stats = await client.hgetall("auto_trade:stats")
  assert stats["groups"] == "2"
  assert stats["with_adds"] == "1"
  assert stats["without_adds"] == "1"
  assert float(stats["realized_pnl"]) == 49
  assert float(stats["add_delta_pnl"]) == 11
  assert float(stats["realized_pips"]) == 84
  assert float(stats["counterfactual_pips"]) == 73
  assert stats["adds_improved"] == "1"


@pytest.mark.asyncio
async def test_execution_stream_is_persisted_at_fill_and_queryable_with_manual():
  await store.init_db()
  signal = await store.store_manual_signal(
    1, "BUY", 4000, 4001, 3997, [4006], execution_mode="algo",
  )
  await store.set_execution_intent(
    signal["id"], intent_id=f"manual:{signal['id']}:1",
    status="armed", revision=1,
  )
  await store.record_auto_trade_event({
    "type": "manual_opened",
    "timestamp": 10,
    "candidate_id": f"manual:{signal['id']}:1",
    "position_id": 901,
    "group_id": "manual-group-1",
    "stream": "algo_manual",
    "direction": "BUY",
    "setup": "Manual Algo",
    "price": 4001,
    "stop_pips": 40,
    "volume": 200,
  })
  await store.store_pips("+", 50, signal_id=signal["id"])
  await store.record_auto_trade_event({
    "type": "group_result",
    "timestamp": 20,
    "group_id": "manual-group-1",
    "group_realized_pips": 48,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 21,
    "position_id": 901,
    "group_id": "manual-group-1",
    "price": 3997,
  })

  records = await store.get_pips_records(0, 4_000_000_000)
  by_stream = {row["stream"]: row for row in records}
  persisted = await store.get_manual_signal(signal["id"])

  assert set(by_stream) == {"manual", "algo_manual"}
  assert by_stream["manual"]["trade_key"] == f"manual:{signal['id']}"
  assert by_stream["algo_manual"]["trade_key"] == f"manual:{signal['id']}"
  assert by_stream["algo_manual"]["pips"] == 48
  assert persisted["trade_stream"] == "algo_manual"


@pytest.mark.asyncio
async def test_v8_order_filled_and_position_closed_feed_trade_stats():
  """TradePlan emits order_filled (not opened) and often omits stream=algo_auto."""
  await store.init_db()
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 10,
    "position_id": 39760749,
    "group_id": "v8:17ab03ca932a19b11d374d2ae9de8f30",
    "candidate_id": "v8:17ab03ca932a19b11d374d2ae9de8f30",
    "direction": "BUY",
    "setup": "Key Level",
    "symbol": "XAU",
    "price": 4060.85,
    "stop_loss": 4056.55,
    "volume": 1100,
    "message": "ENTRY GROUP FULLY FILLED BUY lot=1100 weighted=4060.85",
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 20,
    "position_id": 39760749,
    "group_id": "v8:17ab03ca932a19b11d374d2ae9de8f30",
    "candidate_id": "v8:17ab03ca932a19b11d374d2ae9de8f30",
    "direction": "BUY",
    "price": 4064.85,
    "target_pips": 90,
    "remaining_volume": 0,
    "message": "PLAN CLOSED · highest TP archived TP1",
  })

  records = await store.get_pips_records(0, 4_000_000_000)
  assert len(records) == 1
  row = records[0]
  assert row["stream"] == "algo_auto"
  assert row["trade_key"] == "algo:v8:17ab03ca932a19b11d374d2ae9de8f30"
  assert row["sign"] == "+"
  assert row["pips"] == 90  # highest archived TP, not the residual close
  assert row["stop_pips"] == pytest.approx(43.0)  # (4060.85 - 4056.55) / 0.1


@pytest.mark.asyncio
async def test_archived_tp_wins_over_group_realized_pips_when_both_present():
  """Owner directive: /trade_stats records the highest TP archived, not a
  volume-weighted blended net - even when the broker also reports a real
  blended group_realized_pips alongside it (a 4-leg TP1-TP4 close reports
  both: the true blended net and the highest booked target)."""
  await store.init_db()
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 10,
    "position_id": 39760750,
    "group_id": "v8:archived-vs-net",
    "candidate_id": "v8:archived-vs-net",
    "direction": "BUY",
    "setup": "Key Level",
    "symbol": "XAU",
    "price": 4270.0,
    "stop_loss": 4267.0,
    "volume": 1100,
    "message": "ENTRY GROUP FULLY FILLED BUY lot=1100 weighted=4270.0",
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 20,
    "position_id": 39760750,
    "group_id": "v8:archived-vs-net",
    "candidate_id": "v8:archived-vs-net",
    "direction": "BUY",
    "price": 4287.0,
    "target_pips": 170,
    "group_realized_pips": 87.04,
    "remaining_volume": 0,
    "message": "PLAN CLOSED · highest TP archived TP4",
  })

  records = await store.get_pips_records(0, 4_000_000_000)
  assert len(records) == 1
  row = records[0]
  assert row["sign"] == "+"
  assert row["pips"] == 170  # archived TP4, not the 87.04 blended net


@pytest.mark.asyncio
async def test_terminal_manual_close_uses_broker_fill_before_reconcile_fallback():
  await store.init_db()
  await store.record_auto_trade_event({
    "type": "opened",
    "timestamp": 10,
    "position_id": 902,
    "group_id": "auto-group-2",
    "stream": "algo_auto",
    "direction": "BUY",
    "setup": "Range Box Scalp",
    "price": 4000,
    "stop_pips": 30,
    "volume": 200,
  })
  await store.record_auto_trade_event({
    "type": "manual_closed",
    "timestamp": 20,
    "position_id": 902,
    "group_id": "auto-group-2",
    "remaining_volume": 0,
    "price": 4004,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 21,
    "position_id": 902,
    "group_id": "auto-group-2",
    "price": 3997,
  })

  records = await store.get_pips_records(0, 4_000_000_000)

  assert len(records) == 1
  assert records[0]["stream"] == "algo_auto"
  assert records[0]["pips"] == 40
  assert records[0]["sign"] == "+"


@pytest.mark.asyncio
async def test_pause_resume_and_status(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  await delivery.set_auto_trade_paused(True)
  client = redis_state.get_client()
  await client.set(
    "auto_trade:last_gate:XAU",
    '{"state":"waiting_rejection","box_state":"candidate",'
    '"trend_state":"no_setup","selected_strategy":"Range Box Scalp",'
    '"selected_timeframe":"M1","direction":"BUY",'
    '"box":{"low":4016.5,"high":4024.5},"full_tp_pips":70}',
  )
  assert await client.get("auto_trade:paused") == "1"
  text = await delivery.auto_trade_status_text()
  assert "demo trading" in text
  assert "paused" in text
  assert "Algo bot" in text
  assert "Open <b>0</b> · groups <b>0</b> · today <b>0</b>" in text
  assert "Range Box Scalp · BUY · M1 · waiting rejection" in text
  assert len(text) < 900
  assert "auto trader" not in text.lower()
  await delivery.set_auto_trade_paused(False)
  assert await client.get("auto_trade:paused") is None


@pytest.mark.asyncio
async def test_status_includes_compact_profile_regime_groups_and_route(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_profile": "demo_eval"})
  client = redis_state.get_client()
  await client.set(
    "auto_trade:last_gate:XAU",
    json.dumps({
      "state": "candidate",
      "selected_strategy": "Key Level",
      "selected_timeframe": "M5",
      "direction": "SELL",
      "regime": "chop",
      "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }),
  )
  await client.set(
    "auto_trade:executor_snapshot:XAU",
    json.dumps({
      "symbol": "XAU",
      "profile": "demo_eval",
      "group_ids": ["g1", "g2"],
      "position_ids": [11],
      "ready": True,
    }),
  )
  await client.set(
    "auto_trade:last_route_outcome:XAU",
    json.dumps({
      "strategy": "Key Level",
      "status": "blocked",
      "reason_code": "opposing_barrier",
    }),
  )
  await client.set(
    "auto_trade:config_health",
    json.dumps({"state": "healthy", "profile": "demo_eval"}),
  )
  await client.set(
    "auto_trade:executor_readiness",
    json.dumps({"ready": True}),
  )

  text = await delivery.auto_trade_status_text()

  assert "demo trading · <b>running</b> · demo_eval" in text
  assert "groups <b>2</b>" in text
  assert "Regime <b>chop</b>" in text
  assert "Route: Key Level · blocked · opposing_barrier" in text
  assert len(text) < 900
  assert "auto trader" not in text.lower()


@pytest.mark.asyncio
async def test_status_shows_equity_and_balance_when_they_differ(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  client = redis_state.get_client()
  await client.set(
    "auto_trade:executor_snapshot:XAU",
    json.dumps({
      "symbol": "XAU",
      "profile": "demo_eval",
      "group_ids": [],
      "position_ids": [],
      "ready": True,
      "account_balance": 10000.0,
      "account_equity": 10123.45,
    }),
  )

  text = await delivery.auto_trade_status_text()

  assert "💰 Equity <b>$10,123.45</b> · Balance <b>$10,000.00</b>" in text


@pytest.mark.asyncio
async def test_status_omits_balance_when_equal_to_equity(monkeypatch):
  # Flat account, no open exposure -- equity == balance is the common case
  # and a redundant "Balance $X · Balance $X" line would just be noise.
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  client = redis_state.get_client()
  await client.set(
    "auto_trade:executor_snapshot:XAU",
    json.dumps({
      "symbol": "XAU",
      "profile": "demo_eval",
      "group_ids": [],
      "position_ids": [],
      "ready": True,
      "account_balance": 10000.0,
      "account_equity": 10000.0,
    }),
  )

  text = await delivery.auto_trade_status_text()

  assert "💰 Equity <b>$10,000.00</b>" in text
  assert "Balance" not in text


@pytest.mark.asyncio
async def test_status_omits_equity_line_before_first_account_snapshot(monkeypatch):
  # AutoTradeExecutorSnapshot defaults account_balance/account_equity to 0
  # until the engine's first broker account snapshot arrives -- must not
  # render a misleading "$0.00".
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  client = redis_state.get_client()
  await client.set(
    "auto_trade:executor_snapshot:XAU",
    json.dumps({
      "symbol": "XAU",
      "profile": "demo_eval",
      "group_ids": [],
      "position_ids": [],
      "ready": True,
      "account_balance": 0.0,
      "account_equity": 0.0,
    }),
  )

  text = await delivery.auto_trade_status_text()

  assert "Equity" not in text
  assert "Balance" not in text


@pytest.mark.asyncio
async def test_status_shows_manual_algo_pending_count(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  from app.persistence import store

  await store.init_db()
  rec = await store.store_manual_signal(
    1_800_000_000, "SELL", 4100, 4105, 4110, [4095, 4090, 4080],
    execution_mode="algo",
  )
  await store.set_execution_status(rec["id"], "pending")

  text = await delivery.auto_trade_status_text()

  assert "algo <b>1</b>" in text


@pytest.mark.asyncio
async def test_status_shows_live_price_and_spread(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  client = redis_state.get_client()
  await client.set(
    "price:XAU:spot", json.dumps({"bid": 4071.85, "ask": 4072.15, "ts": 1}),
  )

  text = await delivery.auto_trade_status_text()

  assert "XAU <b>4,071.85</b>/<b>4,072.15</b>" in text
  assert "spread 3.0p" in text


@pytest.mark.asyncio
async def test_status_shows_cooldown_for_confirmed_stop_loss(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  client = redis_state.get_client()
  await client.set(
    "auto_trade:zone:cooldown:XAU:SELL",
    json.dumps({
      "reason": "stop_loss",
      "confidence": "confirmed",
      "entry_price": 4070.0,
      "stop_price": 4075.0,
      "closed_at": 1,
    }),
    ex=900,
  )

  text = await delivery.auto_trade_status_text()

  assert "Cooldown <b>SELL</b>" in text
  assert "15m left" in text


@pytest.mark.asyncio
async def test_status_hides_cooldown_for_ambiguous_close(monkeypatch):
  # Matches worker.py's own fail-open rule (_zone_cooldown_reason): a
  # marker the engine could not positively attribute to a stop-loss must
  # never look like an active block on /algo_status either.
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  client = redis_state.get_client()
  await client.set(
    "auto_trade:zone:cooldown:XAU:SELL",
    json.dumps({
      "reason": "unknown",
      "confidence": "ambiguous",
      "entry_price": 4070.0,
      "stop_price": 4075.0,
      "closed_at": 1,
    }),
    ex=900,
  )

  text = await delivery.auto_trade_status_text()

  assert "Cooldown" not in text


@pytest.mark.asyncio
async def test_status_warns_when_engine_snapshot_is_stale(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  client = redis_state.get_client()
  stale_ts = int(datetime.now(timezone.utc).timestamp()) - 500
  await client.set(
    "auto_trade:executor_snapshot:XAU",
    json.dumps({"symbol": "XAU", "group_ids": [], "updated_at": stale_ts}),
  )

  text = await delivery.auto_trade_status_text()

  assert "Engine stale" in text
  assert "8m ago" in text


@pytest.mark.asyncio
async def test_status_silent_when_engine_snapshot_is_fresh(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  client = redis_state.get_client()
  fresh_ts = int(datetime.now(timezone.utc).timestamp()) - 5
  await client.set(
    "auto_trade:executor_snapshot:XAU",
    json.dumps({"symbol": "XAU", "group_ids": [], "updated_at": fresh_ts}),
  )

  text = await delivery.auto_trade_status_text()

  assert "Engine stale" not in text


@pytest.mark.no_database
@pytest.mark.asyncio
async def test_status_warns_when_component_is_fatal(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})

  async def fake_fatals():
    return [{
      "component": "scanner_loop",
      "state": "fatal",
      "error": "KeyError: missing plan field",
      "retry_count": 0,
    }]

  async def fake_scorecard():
    return None

  async def fake_book(_client):
    return []

  monkeypatch.setattr(redis_state, "list_fatal_components", fake_fatals)
  monkeypatch.setattr(delivery, "_today_algo_scorecard_line", fake_scorecard)
  monkeypatch.setattr(delivery, "_open_trade_plan_book_lines", fake_book)

  text = await delivery.auto_trade_status_text()

  assert "scanner_loop" in text
  assert "fatal" in text
  assert "Ready consumer" not in text


@pytest.mark.no_database
@pytest.mark.asyncio
async def test_status_is_silent_when_no_fatal_components(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})

  async def fake_fatals():
    return []

  async def fake_scorecard():
    return None

  async def fake_book(_client):
    return []

  monkeypatch.setattr(redis_state, "list_fatal_components", fake_fatals)
  monkeypatch.setattr(delivery, "_today_algo_scorecard_line", fake_scorecard)
  monkeypatch.setattr(delivery, "_open_trade_plan_book_lines", fake_book)

  text = await delivery.auto_trade_status_text()

  assert "Ready consumer" not in text
  assert "fatal ·" not in text


@pytest.mark.asyncio
async def test_status_attributes_the_exact_published_secondary_strategy(
  monkeypatch,
):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  client = redis_state.get_client()
  await client.set(
    "auto_trade:last_gate:XAU",
    json.dumps({
      "state": "candidate",
      "box_state": "waiting_for_touch",
      "trend_state": "no_setup",
      "selected_strategy": "Liquidity Sweep",
      "selected_timeframe": "M5",
      "direction": "BUY",
      "gate_source": "multi_strategy_match",
      "candidate_id": "secondary-candidate",
      "published_candidate": {
        "winner_intent_id": "strategy:secondary",
        "candidate_id": "secondary-candidate",
        "source_strategy": "Supply Zone Reaction",
        "signal_source": "scanner_strategy_match",
        "family": "supply_demand",
        "direction": "SELL",
        "timeframe": "M5",
        "group_id": "secondary-group",
      },
    }),
  )

  text = await delivery.auto_trade_status_text()

  assert "Supply Zone Reaction · SELL · M5 · candidate" in text
  assert "Liquidity Sweep · BUY" not in text


@pytest.mark.asyncio
async def test_status_surfaces_match_build_rejection_reason(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  client = redis_state.get_client()
  await client.set(
    "auto_trade:last_match_build:XAU",
    json.dumps({
      "stage": "match_build_rejected",
      "reason": "insufficient_target_room",
      "measured": {"room_pips": 45.2},
    }),
  )

  text = await delivery.auto_trade_status_text()

  assert "Why: insufficient_target_room" in text


@pytest.mark.asyncio
async def test_status_surfaces_match_build_ready(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  client = redis_state.get_client()
  await client.set(
    "auto_trade:last_match_build:XAU",
    json.dumps({
      "stage": "match_ready",
      "strategy": "Range Edge Scalp",
      "direction": "BUY",
      "full_take_profit_pips": 40,
    }),
  )

  text = await delivery.auto_trade_status_text()

  assert "Why: ready · Range Edge Scalp BUY" in text


@pytest.mark.asyncio
async def test_status_identifies_scanner_strategy_match(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  client = redis_state.get_client()
  await client.set(
    "auto_trade:last_gate:XAU",
    json.dumps({
      "state": "strategy_match_waiting",
      "gate_source": "scanner_strategy_match",
      "strategy_match": {
        "strategy": "Liquidity Sweep",
        "direction": "SELL",
        "source_tf": "M5",
      },
      "selected_strategy": "Liquidity Sweep",
      "selected_timeframe": "M5",
      "direction": "SELL",
      "box_state": "waiting_for_box",
      "trend_state": "no_setup",
      "box": {"low": 4113.0, "high": 4122.0},
      "reasons": ["sell-side liquidity swept"],
    }),
  )

  text = await delivery.auto_trade_status_text()

  assert "Liquidity Sweep · SELL · M5 · strategy match waiting" in text


@pytest.mark.asyncio
async def test_status_explains_when_no_strategy_matches(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  client = redis_state.get_client()
  await client.set("auto_trade:last_gate:XAU", json.dumps({
    "state": "waiting_for_box",
    "box_state": "waiting_for_box",
    "trend_state": "no_setup",
    "selected_strategy": None,
    "direction": None,
    "regime": "chop",
    "reasons": ["no valid M1 consolidation box in the lookback window"],
  }))

  text = await delivery.auto_trade_status_text()

  assert "none · waiting for box" in text
  assert "Why: no valid M1 consolidation box in the lookback window" in text


@pytest.mark.asyncio
async def test_status_shows_market_map_reason_when_idle(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  client = redis_state.get_client()
  await client.set("auto_trade:last_gate:XAU", json.dumps({
    "state": "waiting_for_touch",
    "box_state": "waiting_for_box",
    "trend_state": "no_setup",
    "market_map_state": "waiting_for_touch",
    "selected_strategy": None,
    "direction": None,
    "reasons": [
      "nearest mapped SELL zone 4087.00-4095.00 "
      "(14.1 away · tracked, execute within 4.5)",
    ],
  }))

  text = await delivery.auto_trade_status_text()

  assert "none · waiting for touch" in text
  assert "Why: nearest mapped SELL zone 4087.00-4095.00" in text
  assert len(text) < 500


@pytest.mark.asyncio
async def test_status_includes_today_algo_scorecard(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  await store.init_db()
  now = int(datetime.now(timezone.utc).timestamp())
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": now - 120,
    "position_id": 88001,
    "group_id": "v8:status-score-win",
    "candidate_id": "v8:status-score-win",
    "stream": "algo_auto",
    "direction": "BUY",
    "setup": "Key Level",
    "symbol": "XAU",
    "price": 4050.0,
    "stop_loss": 4045.0,
    "volume": 100,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": now - 60,
    "position_id": 88001,
    "group_id": "v8:status-score-win",
    "candidate_id": "v8:status-score-win",
    "stream": "algo_auto",
    "direction": "BUY",
    "price": 4054.0,
    "target_pips": 40,
    "message": "PLAN CLOSED · highest TP archived TP1",
  })
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": now - 50,
    "position_id": 88002,
    "group_id": "v8:status-score-loss",
    "candidate_id": "v8:status-score-loss",
    "stream": "algo_auto",
    "direction": "SELL",
    "setup": "Supply Zone Reaction",
    "symbol": "XAU",
    "price": 4060.0,
    "stop_loss": 4065.0,
    "volume": 100,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": now - 10,
    "position_id": 88002,
    "group_id": "v8:status-score-loss",
    "candidate_id": "v8:status-score-loss",
    "stream": "algo_auto",
    "direction": "SELL",
    "price": 4065.0,
    "group_realized_pips": -50,
    "message": "PLAN CLOSED · no TP archived",
  })

  text = await delivery.auto_trade_status_text()

  assert "Today · <b>1W/1L</b> · net <b>−10p</b>" in text
  assert len(text) < 4000


@pytest.mark.asyncio
async def test_status_lists_open_v8_plan_book(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  client = redis_state.get_client()

  async def _fake_read_trade_plan(_client, plan_id: str):
    assert plan_id == "plan-status-open-1"
    return SimpleNamespace(
      analysis=SimpleNamespace(strategy="Supply Zone Reaction"),
    )

  monkeypatch.setattr(
    "app.autotrade.trade_plan_stream.read_trade_plan",
    _fake_read_trade_plan,
  )
  await client.set("execution:trade_plan_runtime_ids", "plan-status-open-1")
  await client.set(
    "execution:plan_runtime:plan-status-open-1",
    json.dumps({
      "PlanId": "plan-status-open-1",
      "SetupId": "setup-status-1",
      "Direction": "SELL",
      "Stage": "FullyOpen",
      "GroupStage": "managing",
      "GroupWeightedFillPrice": 4054.2,
      "TotalFilledVolume": 200,
      "RemainingVolume": 200,
    }),
  )

  text = await delivery.auto_trade_status_text()

  assert "Open: <b>SELL</b> · Supply Zone Reaction · managing" in text
  assert "Open book: none" not in text
  assert len(text) < 4000


@pytest.mark.asyncio
async def test_status_open_book_caps_at_three_plans(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  client = redis_state.get_client()
  plan_ids = [f"plan-status-cap-{i}" for i in range(1, 5)]
  await client.set("execution:trade_plan_runtime_ids", ",".join(plan_ids))
  for plan_id in plan_ids:
    await client.set(
      f"execution:plan_runtime:{plan_id}",
      json.dumps({
        "PlanId": plan_id,
        "Direction": "BUY",
        "Stage": "FullyOpen",
        "GroupStage": "fully_open",
        "GroupWeightedFillPrice": 4000.0 + plan_ids.index(plan_id),
        "TotalFilledVolume": 100,
        "RemainingVolume": 100,
      }),
    )

  text = await delivery.auto_trade_status_text()

  assert text.count("Open: <b>BUY</b>") == 3
  assert "+1 more" in text
  assert len(text) < 4000

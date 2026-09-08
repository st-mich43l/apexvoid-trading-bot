"""TradePlan V8 events must render, not crash Telegram delivery.

Found while wiring TradePlanRuntime.cs (Section M of the TradePlan cutover):
render_auto_trade_event's fallback branch does an unguarded
labels[event_type] dict lookup with no .get() default - any event type
without an entry raises KeyError. The C# runtime emits five new TradePlan-only
types (plan_armed, order_submitted, order_filled, tp_booked, sl_moved)
that did not previously exist in that dict.
"""

from __future__ import annotations

import pytest

from app.autotrade import delivery


pytestmark = pytest.mark.no_database


def test_plan_armed_event_stays_silent_and_does_not_crash():
  # PLAN ARMED as a separate user-visible idle-waiting phase is exactly
  # what the direct-publish model must not show - the plan card already
  # says PLAN PUBLISHED, and the owner reading a lingering ARMED card as
  # "still waiting" is precisely the confusion this must avoid. Moved into
  # TELEGRAM_SILENT_LIFECYCLE_TYPES; still must not crash on the lookup.
  text = delivery.render_auto_trade_event({
    "type": "plan_armed",
    "message": "PLAN ARMED Trend Pullback BUY (market_watch)",
  })

  assert text is None


def test_v8_order_submitted_event_renders_without_crashing():
  # "order_submitted" (no trade-plan-specific prefix) is already claimed by the V6
  # lifecycle as an always-silent type (TELEGRAM_SILENT_LIFECYCLE_TYPES) -
  # confirm it stays silent, and that the TradePlan-distinct name is also silent
  # (owner does not want ORDERS SUBMITTED cards).
  silent = delivery.render_auto_trade_event({
    "type": "order_submitted",
    "message": "should stay silent",
  })
  assert silent is None

  text = delivery.render_auto_trade_event({
    "type": "v8_order_submitted",
    "message": "ORDERS SUBMITTED BUY L1=800 L2=300 (pending=1)",
  })

  assert text is None
  assert "v8_order_submitted" in delivery.TELEGRAM_SILENT_LIFECYCLE_TYPES


def test_event_setup_id_strips_v8_plan_prefix():
  assert delivery._event_match_id({
    "match_id": "abc123",
    "candidate_id": "v8:abc123",
  }) == "abc123"
  assert delivery._event_match_id({
    "candidate_id": "v8:setup-only",
  }) == "setup-only"
  assert delivery._event_match_id({
    "plan_id": "v8:from-plan",
  }) == "from-plan"


def test_order_filled_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "order_filled",
    "message": (
      "ENTRY GROUP FULLY FILLED BUY volume=1100 "
      "weighted=4074.1181818181818181818181818"
    ),
  })

  assert text is not None
  assert "ORDER FILLED" in text
  assert "lot=0.11" in text
  assert "lot=1100" not in text
  assert "volume=" not in text
  assert "weighted=4074.12" in text
  assert "4074.118181818" not in text


def test_clean_message_formats_partial_fill_lot_and_price():
  cleaned = delivery._clean_message(
    "ENTRY L1 FILLED volume=800 @ 4074.6812345; L2 still pending"
  )
  assert "lot=0.08" in cleaned
  assert "lot=800" not in cleaned
  assert "volume=" not in cleaned
  assert "@ 4074.68" in cleaned
  assert "4074.6812345" not in cleaned


def test_tp_booked_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "tp_booked",
    "message": "TP COMPLETED TP1 closed L1=320 L2=120 remaining=660 (2/2)",
    "setup": "Key Level",
    "price": 4054.86,
    "target_pips": 60,
  })

  assert text is not None
  assert "TP COMPLETED" in text
  assert "TP1" in text
  assert "4054.86" in text
  assert "+60.0 pips" in text
  assert "Key Level" not in text
  assert "🧭" not in text
  assert "Closed" not in text
  assert "Remaining" not in text
  assert "Legs open" not in text
  assert "L1" not in text
  assert "📦" not in text
  assert "🎯" in text


def test_clean_message_formats_tp_leg_and_remaining_as_lot():
  cleaned = delivery._clean_message(
    "TP1 COMPLETED TP2 closed L1=100 remaining=700 (1/1)"
  )
  assert "L1 lot=0.01" in cleaned
  assert "remaining lot=0.07" in cleaned
  assert "L1=100" not in cleaned
  assert "remaining=700" not in cleaned
  assert "lot=100" not in cleaned
  assert "lot=700" not in cleaned


def test_clean_message_keeps_already_converted_lots():
  cleaned = delivery._clean_message(
    "ENTRY L1 FILLED lot=0.08 @ 4051.93; L2 still pending"
  )
  assert "lot=0.08" in cleaned
  assert "lot=0.000008" not in cleaned


def test_sl_moved_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "sl_moved",
    "message": "GROUP SL MOVED TO BE 4089.10 (2/2)",
    "setup": "Key Level",
  })

  assert text is not None
  assert "GROUP SL MOVED" in text
  assert "Break-even" in text
  assert "4089.10" in text
  assert "🔐" in text or "🛡" in text
  assert "Key Level" not in text
  assert "🧭" not in text


def test_sl_moved_trail_renders_trail_kind():
  text = delivery.render_auto_trade_event({
    "type": "sl_moved",
    "message": "SL MOVED to 4070.31 (trail TP1)",
    "setup": "Key Level",
  })

  assert text is not None
  assert "Trail" in text
  assert "4070.31" in text
  assert "trail TP1" in text
  assert "Key Level" not in text
  assert "🧭" not in text


def test_tp_compact_line_format():
  line = delivery._format_tp_compact_line(
    {"price": 4029.98, "target_pips": 41.0},
    "TP COMPLETED TP1 closed L1 lot=0.02 remaining lot=0.06 (1/2)",
  )
  assert line == (
    "🎯 TP1 · 💰 Fill: 4029.98 · ✅ Achieved: +41.0 pips"
  )


def test_tp_compact_lines_stack_like_owner_sample():
  tp1 = delivery._format_tp_compact_line(
    {"price": 4029.98, "target_pips": 41.0},
    "TP COMPLETED TP1 closed L1 lot=0.02 remaining lot=0.06 (1/2)",
  )
  tp2 = delivery._format_tp_compact_line(
    {"price": 4033.0, "target_pips": 45.0},
    "TP COMPLETED TP2 closed L1 lot=0.02 remaining lot=0.04 (1/2)",
  )
  body = "\n".join([tp1, tp2])
  assert body == "\n".join([
    "🎯 TP1 · 💰 Fill: 4029.98 · ✅ Achieved: +41.0 pips",
    "🎯 TP2 · 💰 Fill: 4033.00 · ✅ Achieved: +45.0 pips",
  ])


def test_be_trail_head_status_formats():
  be_status, be_state, be_price = delivery._format_be_trail_head_status(
    {"price": 4034.99},
    "GROUP SL MOVED TO BE 4034.99 (2/2)",
  )
  assert "BE" in be_status
  assert "4034.99" in be_status
  assert be_state == "sl_moved"
  assert be_price == 4034.99

  trail_status, _, trail_price = delivery._format_be_trail_head_status(
    {"price": 4070.31},
    "SL MOVED to 4070.31 (trail TP1)",
  )
  assert "Trail" in trail_status
  assert "4070.31" in trail_status
  assert trail_price == 4070.31


def test_position_closed_compact_line_format():
  line = delivery._format_position_closed_compact_line(
    {"target_pips": 90},
    "PLAN CLOSED · highest TP archived TP2 · @ 4106.00",
  )
  assert line == "🏁 POSITION CLOSED · @ 4106.00"

  losing = delivery._format_position_closed_compact_line(
    {"group_realized_pips": -47},
    "PLAN CLOSED · no TP archived · losing -47 pips · @ 4090.50",
  )
  assert losing == (
    "🏁 POSITION CLOSED · 🛡 SL · ❌ Losing: -47.0 pips · @ 4090.50"
  )
  assert "\n" not in losing
  assert "Highest TP archived" not in losing


def test_tp_compact_line_from_final_close_message():
  """Final target hit is position_closed only — still render a 🎯 TP line."""
  line = delivery._format_tp_compact_line(
    {"price": 4010.0, "target_pips": 81.0},
    "PLAN CLOSED · highest TP archived TP3 · @ 4010.00",
  )
  assert line == (
    "🎯 TP3 · 💰 Fill: 4010.00 · ✅ Achieved: +81.0 pips"
  )


def test_deferred_tp_stopout_cannot_render_as_achieved_tp():
  event = {
    "type": "position_closed",
    "message": "PLAN CLOSED · highest TP archived TP1 · @ 4630.96",
    "symbol": "XAU",
    "direction": "BUY",
    "price": 4630.96,
    "stop_loss": 4631.04,
    "target_pips": 31,
    "group_realized_pips": -60,
    "previous_state": "fully_open",
    "reason_code": "manual_or_external_close",
  }

  assert delivery._format_tp_compact_line(
    event, event["message"],
  ) is None
  close = delivery._format_position_closed_compact_line(
    event, event["message"],
  )
  assert close == (
    "🏁 POSITION CLOSED · 🛡 SL · ❌ Losing: -60.0 pips · @ 4630.96"
  )
  rendered = delivery.render_auto_trade_event(event)
  assert "Highest TP archived" not in rendered
  assert "Achieved" not in rendered
  assert "🛡" in rendered and "SL" in rendered
  assert "-60.0 pips" in rendered


def test_plan_closed_event_renders_highest_tp_only():
  text = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "PLAN CLOSED · highest TP archived TP2 · @ 4106.00",
    "target_pips": 90,
  })

  assert text is not None
  assert "POSITION CLOSED" in text
  assert "Highest TP archived" in text
  assert "TP2" in text
  assert "+90.0 pips" in text
  assert "4106.00" in text
  assert "L1" not in text
  assert "lot=" not in text.lower()


def test_plan_closed_no_tp_archived_renders_losing():
  text = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "PLAN CLOSED · no TP archived · losing -47 pips · @ 4090.50",
    "group_realized_pips": -47,
    "reason_code": "stop_loss_or_take_profit",
  })

  assert text is not None
  assert "🛡" in text and "SL" in text
  assert "Closed by broker SL/TP" not in text
  assert "Highest TP archived" not in text
  assert "❌ Losing:" in text
  assert "-47.0 pips" in text
  assert "4090.50" in text
  assert "achieved" not in text.lower()


def test_plan_closed_no_tp_archived_parses_losing_from_message_alone():
  text = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "PLAN CLOSED · no TP archived · losing -36 pips · @ 4101.00",
  })

  assert "🛡" in text and "SL" in text
  assert "❌ Losing:" in text
  assert "-36.0 pips" in text


def test_position_closed_compact_sl_shows_net_loss():
  line = delivery._format_position_closed_compact_line(
    {"group_realized_pips": -39.1, "reason_code": "stop_loss_or_take_profit"},
    "PLAN CLOSED · no TP archived",
  )
  assert "🛡 SL" in line
  assert "❌ Losing: -39.1 pips" in line
  assert "Closed by broker SL/TP" not in line
  assert "Highest TP archived" not in line


def test_position_closed_one_shot_sl_reports_losing():
  closed = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "SELL position is closed",
    "reason_code": "stop_loss_or_take_profit",
    "group_realized_pips": -12.0,
  })
  assert "❌ Losing:" in closed
  assert "🛡" in closed and "SL" in closed
  assert "Closed by broker SL/TP" not in closed
  assert "-12.0 pips" in closed
  assert "Total:" not in closed


def test_plan_rejected_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "plan_rejected",
    "message": "insufficient_target_room",
  })

  assert text is not None
  assert "PLAN REJECTED" in text


def test_plan_expired_event_renders_without_crashing():
  # Live incident: a market_watch entry (eg. Fade Scalp) that never got a
  # live quote back inside its declared zone used to expire in
  # TradePlanRuntime.cs with no PublishEventAsync call at all - the owner's
  # "SETUP FORMING" card just sat there forever with no resolution. Now
  # published like every other terminal transition in that file.
  text = delivery.render_auto_trade_event({
    "type": "plan_expired",
    "message": (
      "TradePlan V8 expired SELL · price left the entry zone without a "
      "fill (outside_zone)"
    ),
  })

  assert text is not None
  assert "PLAN EXPIRED" in text


def test_plan_expired_spread_limit_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "plan_expired",
    "message": (
      "TradePlan V8 expired BUY · spread stayed above the plan limit "
      "while waiting (spread_exceeds_declared_limit)"
    ),
  })

  assert text is not None
  assert "PLAN EXPIRED" in text
  assert "spread" in text.lower()

import json
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.autotrade import delivery
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
  assert "EXECUTOR REJECTED" in rejected
  assert "ordinary candidate rejection" in rejected
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
  assert "EXECUTOR REJECTED" in rejected


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
  assert "✅ closed" in take_profit
  assert "Net: <b>+51.3 pips</b>" in take_profit
  assert "Initial volume" not in take_profit
  assert "lot" not in take_profit.lower()
  assert "$" not in take_profit
  assert "71.82" not in take_profit
  assert "39025496" not in take_profit
  assert "Auto trade" not in (opened + take_profit)


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

  assert partial == (
    "🤖 <b>ApexVoid Algo</b>\n"
    "🎯 #1 TP1 booked 33.3%\n"
    "Realized: <b>+48.4 pips</b>"
  )
  assert "Remaining" not in partial
  assert "lot" not in partial.lower()
  assert final == (
    "🤖 <b>ApexVoid Algo</b>\n"
    "✅ #1 closed\n"
    "Net: <b>+16.7 pips</b>"
  )
  assert "Initial volume" not in final
  assert "lot" not in final.lower()
  assert "$" not in partial + final



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
    "message": "BUY position is closed",
  })
  rejected = delivery.render_auto_trade_event({
    "type": "rejected",
    "message": "volume planning failed",
  })

  assert "ORDER FILLED" in filled
  assert "TP1 booked 33.3%" in partial
  assert "Realized: <b>+30.4 pips</b>" in partial
  assert "Remaining" not in partial
  assert "lot" not in partial.lower()
  assert "Risk protected" in protected or "SL moved" in protected
  assert "POSITION CLOSED" in closed
  assert "EXECUTOR REJECTED" in rejected


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
  assert "Risk protected" in stop
  assert "SL moved to <b>4,029.49</b>" in stop
  assert "BE+3" in stop
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
  assert "Trade result" in result
  assert "Net: <b>+42.0 pips</b>" in result
  assert "$" not in result
  assert "ApexVoid Algo" in scale_in + zone + result


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


def test_group_result_telegram_is_pips_only():
  text = delivery.render_auto_trade_event({
    "type": "group_result",
    "message": "group abc realised $42.00 · 16.7 pips",
    "group_realized_pips": 16.7,
    "group_realized_pnl": 42.0,
  })
  assert "Net: <b>+16.7 pips</b>" in text
  assert "$" not in text
  assert "42" not in text



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
  assert "Net: <b>+51.3 pips</b>" in calls[0][0]
  assert "$" not in calls[0][0]
  assert "71.82" not in calls[0][0]
  assert "39000344" not in calls[0][0]


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

  monkeypatch.setattr(delivery.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(delivery.settings, "telegram_owner_id", 123)
  monkeypatch.setattr(delivery.settings, "signal_public_channel_id", -100456)
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
  monkeypatch.setattr(delivery.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(delivery.settings, "auto_trade_dry_run", False)
  await delivery.set_auto_trade_paused(True)
  client = redis_state.get_client()
  await client.set(
    "auto_trade:last_gate",
    '{"state":"waiting_rejection","box_state":"candidate",'
    '"trend_state":"no_setup","selected_strategy":"Range Box Scalp",'
    '"selected_timeframe":"M1","direction":"BUY",'
    '"box":{"low":4016.5,"high":4024.5},"full_tp_pips":70}',
  )
  await client.set(
    "auto_trade:last_guard:XAU",
    json.dumps({
      "strategy": "Demand Zone Reaction",
      "direction": "BUY",
      "guard": "counter_bias",
      "outcome": "adjust_target",
      "reason": "target_capped_by_structure",
      "hard_block": False,
      "source_structure": "market_map_zone",
      "opposing_structure": {
        "side": "resistance",
        "low": 4058.8,
        "high": 4059.2,
      },
      "measured": {
        "available_room_pips": 23,
        "effective_pips": 10,
        "original_target": 4060,
        "adjusted_target": 4058.5,
        "barrier_price": 4058.8,
      },
      "updated_at": 1784900000,
    }),
  )
  await client.hset(
    "auto_trade:zone_reconcile:XAU",
    mapping={
      "mode": "shadow",
      "zones_input": 6,
      "zones_shadow_output": 4,
      "zones_trimmed": 1,
      "zones_dropped": 1,
      "candidate_difference_count": 2,
    },
  )
  assert await client.get("auto_trade:paused") == "1"
  text = await delivery.auto_trade_status_text()
  assert "demo trading" in text
  assert "paused" in text
  assert "Trades today: <b>0</b> · <b>unlimited</b>" in text
  assert "Measured groups" in text
  assert "ApexVoid Algo" in text
  assert "Selected strategy: <b>Range Box Scalp · BUY · M1</b>" in text
  assert "Source: <b>private OHLC matcher</b>" in text
  assert "Execution: <b>waiting rejection</b>" in text
  assert "box 4,016.50–4,024.50" in text
  assert "full TP 70p" in text
  assert "Demand Zone Reaction · BUY · counter_bias · adjust_target" in text
  assert "target_capped_by_structure" in text
  assert "hard block=False" in text
  assert "source=market_map_zone" in text
  assert "opposing resistance 4058.8-4059.2" in text
  assert "target 4060→4058.5" in text
  assert "mode=shadow" in text
  assert "candidate_difference_count=2" in text
  assert "auto trader" not in text.lower()
  await delivery.set_auto_trade_paused(False)
  assert await client.get("auto_trade:paused") is None


@pytest.mark.asyncio
async def test_status_surfaces_match_build_rejection_reason(monkeypatch):
  # 23 Jul incident: Telegram showed a Range Edge Scalp card with ~40-49
  # pips of room but no autonomous order ever opened, and nothing recorded
  # why - /auto_status must now answer that without reading source code.
  monkeypatch.setattr(delivery.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(delivery.settings, "auto_trade_dry_run", False)
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

  assert "StrategyMatch bridge: <b>blocked</b>" in text
  assert "insufficient_target_room" in text
  assert "room 45.2 pips" in text


@pytest.mark.asyncio
async def test_status_surfaces_match_build_ready(monkeypatch):
  monkeypatch.setattr(delivery.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(delivery.settings, "auto_trade_dry_run", False)
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

  assert "StrategyMatch bridge: <b>ready</b>" in text
  assert "Range Edge Scalp BUY" in text
  assert "TP 40p" in text


@pytest.mark.asyncio
async def test_status_identifies_scanner_strategy_match(monkeypatch):
  monkeypatch.setattr(delivery.settings, "auto_trade_enabled", True)
  client = redis_state.get_client()
  await client.set(
    "auto_trade:last_gate",
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

  assert "Selected strategy: <b>Liquidity Sweep · SELL · M5</b>" in text
  assert "Source: <b>scanner detector</b>" in text
  assert "Execution: <b>strategy match waiting</b>" in text
  assert "Why: sell-side liquidity swept" in text


@pytest.mark.asyncio
async def test_status_explains_when_no_strategy_matches(monkeypatch):
  monkeypatch.setattr(delivery.settings, "auto_trade_enabled", True)
  client = redis_state.get_client()
  await client.set("auto_trade:last_gate", json.dumps({
    "state": "waiting_for_box",
    "box_state": "waiting_for_box",
    "trend_state": "no_setup",
    "selected_strategy": None,
    "direction": None,
    "regime": "chop",
    "reasons": ["no valid M1 consolidation box in the lookback window"],
  }))
  await client.set("scanner:last_tick", json.dumps({
    "detected": [],
    "scalp": {"state": "waiting_edge"},
  }))

  text = await delivery.auto_trade_status_text()

  assert "Selected strategy: <b>none</b>" in text
  assert "Source: <b>none</b>" in text
  assert "Scanner M5: <b>no setup matched · range waiting edge</b>" in text
  assert "Range Box <b>waiting for box</b> · Trend <b>no setup</b>" in text
  assert "Market context: <b>chop</b> <i>(telemetry only)</i>" in text
  assert "Gate:" not in text


@pytest.mark.asyncio
async def test_status_shows_market_map_working_set_and_filter_counts(
  monkeypatch,
):
  monkeypatch.setattr(delivery.settings, "auto_trade_enabled", True)
  client = redis_state.get_client()
  await client.set("auto_trade:last_gate", json.dumps({
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
    "market_map_entries_seen": 7,
    "market_map_entries_actionable": 2,
    "market_map_track_limit": 24.0,
    "market_map_execute_limit": 4.5,
    "market_map_top": [
      {
        "side": "buy",
        "lo": 4066.0,
        "hi": 4073.0,
        "tier": "zone",
        "score": 6.5,
        "contains_price": True,
        "distance": 0.0,
      },
      {
        "side": "sell",
        "lo": 4087.0,
        "hi": 4095.0,
        "tier": "zone",
        "score": 9.0,
        "contains_price": False,
        "distance": 14.12,
      },
    ],
    "market_map_filter_counts": {
      "side": 3,
      "actionable": 1,
      "degenerate_width": 1,
      "distance": 1,
    },
  }))

  text = await delivery.auto_trade_status_text()

  assert "Map entries: <b>7</b> seen · <b>2</b> actionable" in text
  assert "BUY 4,066.00–4,073.00 (inside)" in text
  assert (
    "SELL 4,087.00–4,095.00 "
    "(14.1 away · tracked, execute within 4.5)"
  ) in text
  assert (
    "Map filters: side <b>3</b> · actionable <b>1</b> · "
    "width <b>1</b> · distance <b>1</b>"
  ) in text

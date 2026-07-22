import json
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.autotrade import delivery
from app.persistence import redis_state


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
  assert delivery.render_auto_trade_event({
    "type": "rejected",
    "message": "ordinary candidate rejection",
  }) is None
  text = delivery.render_auto_trade_event({
    "type": "opened",
    "message": "BUY <0.12> lots",
    "position_id": 91,
  })
  assert "ApexVoid Algo" in text
  assert "Position opened" in text
  assert "BUY &lt;0.12&gt; lots" in text
  assert "91" not in text
  assert "auto trade" not in text.lower()


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
    "message": "FULL TP +50 pips closed volume 400",
    "position_id": 39025496,
    "price": 4061.78,
    "group_realized_pips": 51.3,
    "group_realized_pnl": 71.82,
  })

  assert "XAU SELL opened" in opened
  assert "Entry: <b>4,066.78</b>" in opened
  assert "SL: <b>4,070.63</b> · 39 pips" in opened
  assert "Full TP: <b>4,061.78</b> · +50 pips" in opened
  assert "Box: <b>4,062.00–4,069.00</b>" in opened
  assert "39025496" not in opened
  assert "FULL TAKE PROFIT" in take_profit
  assert "Profit: <b>+50 pips</b>" in take_profit
  assert "Position closed in full" in take_profit
  assert "Trade result" in take_profit
  assert "+51.3 pips · +$71.82" in take_profit
  assert "39025496" not in take_profit
  assert "Auto trade" not in (opened + take_profit)


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
    "message": "realised $42 · no-add $31",
  })

  assert "Scale-in filled" in scale_in
  assert "Entry plan ready" in zone
  assert "Trade result" in result
  assert "ApexVoid Algo" in scale_in + zone + result


def test_internal_profile_hides_broker_position_id():
  assert delivery.render_auto_trade_event(_opened_event(), profile="internal") == (
    "🤖 <b>ApexVoid Algo</b>\n"
    "🔴 <b>XAU SELL opened</b>\n"
    "\n"
    "📍 Entry: <b>4,111.26</b>\n"
    "🛡 SL: <b>4,117.76</b> · 65 pips\n"
    "📊 Size: <b>0.06 lot</b>\n"
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
    "message": "TP1 +30 pips closed volume 200",
    "position_id": 39000344,
    "target_pips": 30,
    "stop_pips": 65,
  }, profile="public")

  assert "+0.46R" in text
  assert "39000344" not in text


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
    "message": "FULL TP +50 pips closed volume 400",
    "position_id": 39000344,
    "group_id": "group-39000344",
    "price": 4061.78,
    "group_realized_pips": 51.3,
    "group_realized_pnl": 71.82,
  }
  group_result = {
    "type": "group_result",
    "message": (
      "group group-39000344 realised $71.82 · 51.3 pips · "
      "no-add counterfactual $71.82 / 51.3 pips · adds degraded $0.00"
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
  assert "Trade result" in calls[0][0]
  assert "+51.3 pips · +$71.82" in calls[0][0]
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
  assert "auto trader" not in text.lower()
  await delivery.set_auto_trade_paused(False)
  assert await client.get("auto_trade:paused") is None


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

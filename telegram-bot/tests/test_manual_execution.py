import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.analysis.types import Zone
from app.core.config import settings
from app.persistence import redis_state, store
from app.signals import broadcast, manual_execution
from app.signals.manual_intent import ManualTradeIntent


def _intent(**overrides) -> ManualTradeIntent:
  base = dict(
    intent_id="manual:47:0",
    manual_signal_id=47,
    revision=0,
    direction="SELL",
    entry_low=4100.0,
    entry_high=4105.0,
    sl=4110.0,
    tps=(4095.0, 4090.0, 4080.0),
    created_at=1_800_000_000,
    expires_at=None,
    setup_type="golden-fib",
    confluence=2,
    execution_mode="algo",
  )
  base.update(overrides)
  return ManualTradeIntent(**base)


# ---------------------------------------------------------------------------
# _intent_to_candidate_payload
# ---------------------------------------------------------------------------

def test_intent_to_candidate_payload_sell_uses_entry_low_reference_edge():
  payload = manual_execution._intent_to_candidate_payload(_intent())

  assert payload["version"] == 3
  assert payload["candidate_id"] == "manual:47:0"
  assert payload["symbol"] == "XAU"
  assert payload["timeframe"] == "M1"
  assert payload["setup"] == "Manual Algo"
  assert payload["mode"] == "manual_algo"
  assert payload["direction"] == "SELL"
  assert payload["entry_zone"] == {"low": 4100.0, "high": 4105.0}
  assert payload["manual_stop_loss"] == 4110.0
  assert payload["manual_expires_at"] is None
  assert payload["confluence"] == 2
  # SELL reference edge = entry_low (4100.0, matches pips_format.rr_entry's
  # own SELL -> entry convention): |4100-4095|=5 -> 50p, |4100-4090|=10 ->
  # 100p, |4100-4080|=20 -> 200p.
  assert payload["targets_pips"] == [50, 100, 200]
  assert payload["current_price"] == pytest.approx(4100.0)
  assert payload["key_level"] == pytest.approx(4100.0)


def test_intent_to_candidate_payload_buy_uses_entry_high_reference_edge():
  payload = manual_execution._intent_to_candidate_payload(_intent(
    direction="BUY",
    entry_low=1999.5,
    entry_high=2000.5,
    sl=1994.0,
    tps=(2010.0, 2020.0),
    setup_type=None,
    confluence=None,
  ))

  assert payload["direction"] == "BUY"
  # Untagged manual signals default confluence to 1, exempt from the
  # global MinConfluence gate on the C# side (see AutoTradeEngine.cs).
  assert payload["confluence"] == 1
  # BUY reference edge = entry_high (2000.5): |2000.5-2010|=9.5 -> 95p,
  # |2000.5-2020|=19.5 -> 195p.
  assert payload["targets_pips"] == [95, 195]
  assert payload["current_price"] == pytest.approx(2000.5)
  assert payload["key_level"] == pytest.approx(2000.5)


def test_intent_to_candidate_payload_never_emits_zero_or_negative_pips():
  # A TP exactly at the reference edge would otherwise round to 0, which
  # AutoTradeEngine.cs's manual-algo target-contract validation rejects.
  payload = manual_execution._intent_to_candidate_payload(_intent(
    direction="SELL",
    entry_low=4100.0,
    entry_high=4105.0,
    tps=(4100.02,),
  ))

  assert payload["targets_pips"] == [1]


# ---------------------------------------------------------------------------
# bridge_intents_loop / _process_intent_entries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_intent_entries_publishes_candidate_shaped_payload(monkeypatch):
  monkeypatch.setattr(manual_execution.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(manual_execution.settings, "auto_trade_stream_maxlen", 100)
  client = redis_state.get_client()
  intent_payload = {
    "intent_id": "manual:5:0",
    "manual_signal_id": 5,
    "revision": 0,
    "direction": "SELL",
    "entry_low": 4100.0,
    "entry_high": 4105.0,
    "sl": 4110.0,
    "tps": [4095.0, 4090.0, 4080.0],
    "created_at": 1_800_000_000,
    "expires_at": None,
    "setup_type": "golden-fib",
    "confluence": 2,
    "execution_mode": "algo",
  }
  entries = [("101-0", {"payload": json.dumps(intent_payload)})]

  cursor = await manual_execution._process_intent_entries(
    client, entries, cursor="0-0",
  )

  assert cursor == "101-0"
  candidates = await client.xrange("auto_trade:test")
  assert len(candidates) == 1
  candidate = json.loads(candidates[0][1]["payload"])
  assert candidate["candidate_id"] == "manual:5:0"
  assert candidate["mode"] == "manual_algo"
  assert candidate["manual_stop_loss"] == 4110.0
  assert candidate["targets_pips"] == [50, 100, 200]
  assert await client.get(manual_execution._INTENT_BRIDGE_CURSOR_KEY) == "101-0"


@pytest.mark.asyncio
async def test_process_intent_entries_skips_malformed_payload_but_advances_cursor(
  monkeypatch,
):
  monkeypatch.setattr(manual_execution.settings, "auto_trade_stream", "auto_trade:test2")
  client = redis_state.get_client()
  entries = [("55-0", {"payload": "not json"})]

  cursor = await manual_execution._process_intent_entries(
    client, entries, cursor="0-0",
  )

  assert cursor == "55-0"
  assert await client.xrange("auto_trade:test2") == []


@pytest.mark.asyncio
async def test_bridge_intents_loop_is_a_no_op_when_disabled():
  # manual_algo_enabled defaults False and conftest doesn't override it.
  await asyncio.wait_for(manual_execution.bridge_intents_loop(), timeout=2)


@pytest.mark.asyncio
async def test_reconcile_events_loop_is_a_no_op_when_disabled():
  await asyncio.wait_for(manual_execution.reconcile_events_loop(), timeout=2)


# ---------------------------------------------------------------------------
# _warn_if_would_be_vetoed (Fixes 1/3/4: manual_algo is exempt from every
# worker.py veto by construction - it never touches worker.py at all - but
# should still warn the owner when an entry would have been refused on the
# auto path.)
# ---------------------------------------------------------------------------

def _stub_frames_and_atr(monkeypatch, atr: float) -> None:
  monkeypatch.setattr(
    manual_execution.worker,
    "_load_frames",
    AsyncMock(return_value={"M1": pd.DataFrame({"close": [4116.9]})}),
  )
  monkeypatch.setattr(
    manual_execution, "atr_series", lambda df, length: pd.Series([atr]),
  )


@pytest.mark.asyncio
async def test_warn_if_would_be_vetoed_sends_owner_dm_for_opposing_barrier(
  monkeypatch,
):
  monkeypatch.setattr(manual_execution.settings, "telegram_owner_id", 4242)
  _stub_frames_and_atr(monkeypatch, atr=1.2)
  monkeypatch.setattr(
    manual_execution.worker,
    "_htf_zones",
    lambda frames, cfg: [Zone(4116.0, 4127.0, "supply", touches=8)],
  )
  monkeypatch.setattr(manual_execution.worker, "_htf_levels", lambda frames, cfg: [])
  monkeypatch.setattr(manual_execution.worker, "decode_market_map", lambda raw: None)
  sent = AsyncMock()
  monkeypatch.setattr(manual_execution, "send_scanner_with_retry", sent)
  client = redis_state.get_client()
  intent = _intent(direction="BUY", entry_low=4116.0, entry_high=4116.5)

  # Never a veto: manual_algo never calls worker.py's publish functions at
  # all, so there is nothing here to "pass" or "block" - only to warn about.
  await manual_execution._warn_if_would_be_vetoed(client, intent, 4116.25)

  sent.assert_awaited_once()
  text = sent.await_args.args[0]
  assert "would be refused" in text
  assert "inside opposing" in text


@pytest.mark.asyncio
async def test_warn_if_would_be_vetoed_is_silent_when_nothing_would_fire(
  monkeypatch,
):
  monkeypatch.setattr(manual_execution.settings, "telegram_owner_id", 4242)
  _stub_frames_and_atr(monkeypatch, atr=1.2)
  monkeypatch.setattr(manual_execution.worker, "_htf_zones", lambda frames, cfg: [])
  monkeypatch.setattr(manual_execution.worker, "_htf_levels", lambda frames, cfg: [])
  monkeypatch.setattr(manual_execution.worker, "decode_market_map", lambda raw: None)
  sent = AsyncMock()
  monkeypatch.setattr(manual_execution, "send_scanner_with_retry", sent)
  client = redis_state.get_client()
  intent = _intent(direction="BUY", entry_low=4116.0, entry_high=4116.5)

  await manual_execution._warn_if_would_be_vetoed(client, intent, 4116.25)

  sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_intent_inside_cooldown_is_still_published_but_warns(
  monkeypatch,
):
  """Fix 3's manual-path rule: manual_algo is exempt from the cooldown veto
  (the owner may deliberately re-enter a failed zone) - the intent is still
  bridged onto auto_trade:candidates, just with an owner warning alongside.
  """
  monkeypatch.setattr(manual_execution.settings, "auto_trade_stream", "auto_trade:test3")
  monkeypatch.setattr(manual_execution.settings, "auto_trade_stream_maxlen", 100)
  monkeypatch.setattr(manual_execution.settings, "telegram_owner_id", 4242)
  monkeypatch.setattr(manual_execution.settings, "auto_trade_zone_cooldown_atr", 1.0)
  _stub_frames_and_atr(monkeypatch, atr=2.0)
  monkeypatch.setattr(manual_execution.worker, "_htf_zones", lambda frames, cfg: [])
  monkeypatch.setattr(manual_execution.worker, "_htf_levels", lambda frames, cfg: [])
  monkeypatch.setattr(manual_execution.worker, "decode_market_map", lambda raw: None)
  sent = AsyncMock()
  monkeypatch.setattr(manual_execution, "send_scanner_with_retry", sent)
  client = redis_state.get_client()
  await client.set(
    manual_execution.worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({"entry_price": 4116.25, "stop_price": 4111.54, "closed_at": 1000}),
  )
  intent_payload = {
    "intent_id": "manual:9:0",
    "manual_signal_id": 9,
    "revision": 0,
    "direction": "BUY",
    "entry_low": 4116.5,
    "entry_high": 4117.0,
    "sl": 4111.5,
    "tps": [4130.0],
    "created_at": 1_800_000_000,
    "expires_at": None,
    "setup_type": None,
    "confluence": 1,
    "execution_mode": "algo",
  }
  entries = [("201-0", {"payload": json.dumps(intent_payload)})]

  cursor = await manual_execution._process_intent_entries(
    client, entries, cursor="0-0",
  )

  assert cursor == "201-0"
  candidates = await client.xrange("auto_trade:test3")
  assert len(candidates) == 1
  sent.assert_awaited_once()
  assert "zone cooldown" in sent.await_args.args[0]


# ---------------------------------------------------------------------------
# reconcile_events_loop / _handle_event
# ---------------------------------------------------------------------------

async def _algo_signal(**overrides) -> int:
  """Create a real, algo-armed manual_signals row with a VIP post attached
  so fanout_update actually has somewhere to send its update.
  """
  await store.init_db()
  base = dict(
    ts=1_800_000_000,
    action="SELL",
    entry=4100.0,
    entry_end=4105.0,
    sl=4110.0,
    tps=[4095.0, 4090.0, 4080.0],
    execution_mode="algo",
  )
  base.update(overrides)
  rec = await store.store_manual_signal(**base)
  await store.set_execution_intent(
    rec["id"], intent_id=f"manual:{rec['id']}:0", status="armed", revision=0,
  )
  await store.insert_signal_post(
    rec["id"], settings.signal_vip_channel_id, 9000 + rec["id"], "vip",
  )
  return rec["id"]


def _mock_send(monkeypatch) -> AsyncMock:
  send = AsyncMock(return_value=SimpleNamespace(message_id=99999))
  monkeypatch.setattr(broadcast, "_send_message", send)
  return send


@pytest.mark.asyncio
async def test_handle_event_fill_marks_filled_records_broker_fields_and_activates(
  monkeypatch,
):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "manual_opened",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "setup": "Manual Algo",
    "price": 4100.5,
    "volume": 600,
  }
  await manual_execution._handle_event(client, event, positions)

  assert positions[555] == sid
  row = await store.get_manual_signal(sid)
  assert row["execution_status"] == "filled"
  assert row["broker_position_id"] == "555"
  assert row["broker_fill_price"] == pytest.approx(4100.5)
  assert row["algo_armed"] is True
  assert row["fill_state"] == "filled"
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_event_skips_opened_event_without_manual_algo_setup(monkeypatch):
  send = _mock_send(monkeypatch)
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "manual_opened",
    "position_id": 888,
    "candidate_id": "manual:1:0",
    "setup": "Box Breakout",
  }
  await manual_execution._handle_event(client, event, positions)

  assert positions == {}
  send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_event_skips_events_for_unknown_positions(monkeypatch):
  send = _mock_send(monkeypatch)
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "take_profit", "position_id": 777, "price": 4001.0, "target_pips": 30,
  }
  await manual_execution._handle_event(client, event, positions)

  send.assert_not_awaited()
  assert positions == {}


@pytest.mark.asyncio
async def test_handle_event_take_profit_books_equal_weight_partial_leg(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  # Configured targets [50, 100, 200]p; 50 is not the max -> partial 1/3.
  event = {"type": "take_profit", "position_id": 555, "price": 4095.0, "target_pips": 50}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  # _decode_signal already deserializes legs from its stored JSON text.
  legs = row["legs"]
  assert len(legs) == 1
  assert legs[0]["frac"] == pytest.approx(1 / 3, rel=1e-3)
  assert legs[0]["pips"] == 50
  send.assert_awaited_once()
  assert "TP1" in send.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_event_take_profit_closes_in_full_on_last_configured_target(
  monkeypatch,
):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  # 200 is the max configured target pip distance -> full close (frac=None),
  # even though this is the ladder's FIRST take_profit event for this
  # signal - proving finality is judged against the configured ladder, not
  # an event-count.
  event = {"type": "take_profit", "position_id": 555, "price": 4080.0, "target_pips": 200}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "closed"
  assert row["result_pips"] == 200
  send.assert_awaited_once()
  assert "TP3" in send.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_event_position_closed_uses_signed_sl_result_pips(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()  # SELL entry=4100/4105 sl=4110
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  event = {"type": "position_closed", "position_id": 555, "price": 4110.0}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "closed"
  # sl_result_pips: SELL, fill 4110 outside [4100,4105] -> distance =
  # entry_low(4100) - fill(4110) = -10 -> -100 pips.
  assert row["result_pips"] == -100
  assert 555 not in positions
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_event_position_closed_without_price_marks_error_not_silent(
  monkeypatch,
):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  event = {"type": "position_closed", "position_id": 555, "price": None}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  assert row["execution_status"] == "error"
  send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_event_manual_closed_applies_owner_requested_fraction(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}
  await manual_execution.request_close(sid, 555, frac=0.5)

  event = {"type": "manual_closed", "position_id": 555, "price": 4095.0, "volume": 300}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  legs = row["legs"]
  assert legs[0]["frac"] == pytest.approx(0.5)
  assert row["status"] == "open"
  assert await client.get("manual_trade:pending_close:555") is None
  assert 555 not in positions
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_event_manual_closed_full_close_when_no_frac_was_requested(
  monkeypatch,
):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}
  await manual_execution.request_close(sid, 555, frac=None)

  event = {"type": "manual_closed", "position_id": 555, "price": 4095.0, "volume": 600}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "closed"
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_event_manual_sl_moved_updates_stop(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  event = {"type": "manual_sl_moved", "position_id": 555, "price": 4108.0}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["sl"] == pytest.approx(4108.0)
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_event_manual_cancelled_cancels_armed_signal(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()  # armed, never filled
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {"type": "manual_cancelled", "candidate_id": f"manual:{sid}:0"}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "cancelled"
  assert row["execution_status"] == "cancelled"
  assert row["algo_armed"] is False
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_event_manual_expired_releases_watcher_ownership(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  client = redis_state.get_client()

  await manual_execution._handle_event(
    client,
    {"type": "manual_expired", "candidate_id": f"manual:{sid}:0"},
    {},
  )

  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  assert row["execution_status"] == "expired"
  assert row["algo_armed"] is False
  send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_event_stop_moved_never_mutates_manual_signals(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  event = {"type": "stop_moved", "position_id": 555, "price": 4108.0}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["sl"] == pytest.approx(4110.0)
  send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_event_resolves_signal_via_candidate_id_after_cache_miss(
  monkeypatch,
):
  # Simulates a process restart: the in-memory position_id->signal_id cache
  # is empty, but the event still carries candidate_id, which self-heals
  # the mapping via the persisted broker_position_id on the signal row.
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "take_profit",
    "position_id": 555,
    "price": 4080.0,
    "target_pips": 200,
    "candidate_id": f"manual:{sid}:0",
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "closed"
  assert positions[555] == sid


@pytest.mark.asyncio
async def test_handle_event_command_error_marks_execution_status_error(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "manual_command_error",
    "candidate_id": f"manual:{sid}:0",
    "message": "cancel requested but no pending order found",
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["execution_status"] == "error"
  assert "no pending order" in row["execution_error"]
  send.assert_not_awaited()


# ---------------------------------------------------------------------------
# request_cancel / request_close / request_move_sl
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_cancel_xadds_cancel_pending_command(monkeypatch):
  monkeypatch.setattr(
    manual_execution.settings, "manual_trade_command_stream", "manual_trade:cmd1",
  )
  client = redis_state.get_client()

  await manual_execution.request_cancel("manual:9:0")

  entries = await client.xrange("manual_trade:cmd1")
  assert len(entries) == 1
  payload = json.loads(entries[0][1]["payload"])
  assert payload == {"type": "cancel_pending", "intent_id": "manual:9:0"}


@pytest.mark.asyncio
async def test_request_close_xadds_close_command_and_remembers_frac(monkeypatch):
  monkeypatch.setattr(
    manual_execution.settings, "manual_trade_command_stream", "manual_trade:cmd2",
  )
  client = redis_state.get_client()

  await manual_execution.request_close(9, 555, frac=0.5)

  entries = await client.xrange("manual_trade:cmd2")
  payload = json.loads(entries[0][1]["payload"])
  assert payload == {"type": "close", "position_id": 555, "frac": 0.5}
  pending = json.loads(await client.get("manual_trade:pending_close:555"))
  assert pending == {"signal_id": 9, "frac": 0.5}


@pytest.mark.asyncio
async def test_request_move_sl_xadds_move_sl_command(monkeypatch):
  monkeypatch.setattr(
    manual_execution.settings, "manual_trade_command_stream", "manual_trade:cmd3",
  )
  client = redis_state.get_client()

  await manual_execution.request_move_sl(9, 555, 4108.5)

  entries = await client.xrange("manual_trade:cmd3")
  payload = json.loads(entries[0][1]["payload"])
  assert payload == {"type": "move_sl", "position_id": 555, "price": 4108.5}

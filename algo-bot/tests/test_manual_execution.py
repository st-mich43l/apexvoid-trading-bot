import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf
from app.persistence import redis_state, store
from app.signals import broadcast, manual_execution
from app.signals.manual_intent import ManualTradeIntent


def _intent(**overrides) -> ManualTradeIntent:
  base = dict(
    intent_id="manual:47:0",
    manual_signal_id=47,
    revision=0,
    direction="SELL",
    symbol="XAU",
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

@pytest.mark.no_database
def test_intent_to_candidate_payload_sell_uses_entry_low_reference_edge():
  payload = manual_execution._intent_to_candidate_payload(_intent())

  assert payload["version"] == 3
  assert payload["candidate_id"] == "manual:47:0"
  assert payload["symbol"] == "XAU"
  assert payload["timeframe"] == "M1"
  assert payload["setup"] == "golden-fib"
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
  assert payload["manual_take_profits"] == [4095.0, 4090.0, 4080.0]
  assert payload["manual_target_weights"] == [33, 33, 34]
  assert payload["manual_single_entry"] is False
  assert payload["risk_multiplier"] == 1.0
  assert payload["group_id"] == "manual:47:0"
  assert payload["strategy_family"] == "manual"
  assert payload["parent_group_id"] is None
  assert payload["current_price"] == pytest.approx(4100.0)
  assert payload["key_level"] == pytest.approx(4100.0)


@pytest.mark.no_database
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
  assert payload["setup"] == "key-level"
  # BUY reference edge = entry_high (2000.5): |2000.5-2010|=9.5 -> 95p,
  # |2000.5-2020|=19.5 -> 195p.
  assert payload["targets_pips"] == [95, 195]
  assert payload["manual_target_weights"] == [50, 50]
  assert payload["current_price"] == pytest.approx(2000.5)
  assert payload["key_level"] == pytest.approx(2000.5)


@pytest.mark.no_database
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
  assert payload["manual_target_weights"] == [100]


@pytest.mark.no_database
def test_intent_to_candidate_payload_fx_includes_volume_multiplier(monkeypatch):
  from tests.test_config_effective_instrument_context import _load_production_example

  cfg = _load_production_example().config
  for target in (
    "app.signals.manual_execution.runtime_config",
    "app.signals.fx_manual_algo.runtime_config",
    "app.core.symbols.runtime_config",
    "app.signals.pips_format.runtime_config",
  ):
    monkeypatch.setattr(target, cfg, raising=False)

  payload = manual_execution._intent_to_candidate_payload(_intent(
    symbol="EURUSD",
    entry_low=1.15007,
    entry_high=1.15007,
    sl=1.14867,
    tps=(1.15147, 1.15217, 1.15287),
  ))

  assert payload["manual_single_entry"] is True
  assert payload["manual_target_weights"] == [25, 25, 50]
  assert payload["risk_multiplier"] == pytest.approx(
    float(cfg.manual_algo.sizing.fx_volume_multiplier),
  )


@pytest.mark.no_database
@pytest.mark.parametrize(
  ("targets", "expected"),
  [
    ((4095.0,), [100]),
    ((4095.0, 4090.0), [40, 60]),
    ((4095.0, 4090.0, 4085.0), [40, 30, 30]),
    ((4095.0, 4090.0, 4085.0, 4080.0, 4075.0), [40, 15, 15, 15, 15]),
  ],
)
def test_xau_manual_tp1_fraction_adapts_to_any_tp_count(
  monkeypatch,
  targets,
  expected,
):
  from tests.test_config_effective_instrument_context import _load_production_example

  cfg = _load_production_example().config
  for target in (
    "app.signals.manual_execution.runtime_config",
    "app.core.symbols.runtime_config",
    "app.signals.pips_format.runtime_config",
  ):
    monkeypatch.setattr(target, cfg, raising=False)

  payload = manual_execution._intent_to_candidate_payload(_intent(tps=targets))

  assert payload["manual_target_weights"] == expected
  assert payload["manual_single_entry"] is False
  assert payload["risk_multiplier"] == 1.0


# ---------------------------------------------------------------------------
# bridge_intents_loop / _process_intent_entries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_intent_entries_publishes_candidate_shaped_payload(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream_maxlen": 100})
  client = redis_state.get_client()
  intent_payload = {
    "intent_id": "manual:5:0",
    "manual_signal_id": 5,
    "revision": 0,
    "direction": "SELL",
    "symbol": "XAU",
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test2"})
  client = redis_state.get_client()
  entries = [("55-0", {"payload": "not json"})]

  cursor = await manual_execution._process_intent_entries(
    client, entries, cursor="0-0",
  )

  assert cursor == "55-0"
  assert await client.xrange("auto_trade:test2") == []


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_dispatch_intent_entries_routes_to_symbol_worker(monkeypatch):
  manual_execution._symbol_queues.clear()
  manual_execution._symbol_worker_tasks.clear()
  published: list[ManualTradeIntent] = []

  async def _capture(_client, intent):
    published.append(intent)

  monkeypatch.setattr(manual_execution, "_publish_intent", _capture)
  client = redis_state.get_client()
  intent_payload = {
    "intent_id": "manual:12:0",
    "manual_signal_id": 12,
    "revision": 0,
    "direction": "BUY",
    "symbol": "EURUSD",
    "entry_low": 1.15007,
    "entry_high": 1.15007,
    "sl": 1.14867,
    "tps": [1.15147],
    "created_at": 1_800_000_000,
    "expires_at": None,
    "setup_type": "key-level",
    "confluence": 1,
    "execution_mode": "algo",
  }
  entries = [("301-0", {"payload": json.dumps(intent_payload)})]

  cursor = await manual_execution._dispatch_intent_entries(
    client, entries, cursor="0-0",
  )

  assert cursor == "301-0"
  await asyncio.sleep(0.05)
  assert len(published) == 1
  assert published[0].symbol == "EURUSD"
  for task in manual_execution._symbol_worker_tasks.values():
    task.cancel()
  await asyncio.gather(
    *manual_execution._symbol_worker_tasks.values(),
    return_exceptions=True,
  )


@pytest.mark.no_database
def test_is_manual_algo_event_filters_autonomous_stream():
  assert manual_execution._is_manual_algo_event({
    "type": "opened",
    "stream": "algo_auto",
    "candidate_id": "cand:1",
  }) is False
  assert manual_execution._is_manual_algo_event({
    "type": "manual_limit_placed",
    "stream": "algo_manual",
    "symbol": "EURUSD",
  }) is True
  assert manual_execution._is_manual_algo_event({
    "type": "take_profit",
    "candidate_id": "manual:5:0",
    "symbol": "USDJPY",
  }) is True


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_enqueue_event_prefers_db_symbol_over_bad_xau_stamp(monkeypatch):
  """Scale-up: FX fills stamped XAU must still route to the FX worker."""
  manual_execution._symbol_queues.clear()
  for task in list(manual_execution._symbol_worker_tasks.values()):
    task.cancel()
  manual_execution._symbol_worker_tasks.clear()

  async def fake_symbol(_event):
    return "USDJPY"

  monkeypatch.setattr(
    manual_execution, "_symbol_from_manual_candidate", fake_symbol
  )
  # Don't start a real worker loop — just assert which queue receives the event.
  ensured: list[str] = []

  def capture_ensure(symbol: str) -> None:
    ensured.append(symbol)
    if symbol not in manual_execution._symbol_queues:
      manual_execution._symbol_queues[symbol] = asyncio.Queue()

  monkeypatch.setattr(manual_execution, "_ensure_symbol_worker", capture_ensure)

  await manual_execution._enqueue_event({
    "type": "manual_limit_placed",
    "stream": "algo_manual",
    "candidate_id": "manual:103:0",
    "symbol": "XAU",
  })

  assert ensured == ["USDJPY"]
  assert manual_execution._symbol_queues["USDJPY"].qsize() == 1
  assert "XAU" not in manual_execution._symbol_queues


@pytest.mark.asyncio
async def test_bridge_intents_loop_is_a_no_op_when_disabled():
  # manual_algo_enabled defaults False and conftest doesn't override it.
  await asyncio.wait_for(manual_execution.bridge_intents_loop(), timeout=2)


@pytest.mark.asyncio
async def test_reconcile_events_loop_is_a_no_op_when_disabled():
  await asyncio.wait_for(manual_execution.reconcile_events_loop(), timeout=2)


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_manual_intent_bypasses_worker_strategy_gates(
  monkeypatch,
):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test3"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream_maxlen": 100})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  sent = AsyncMock()
  monkeypatch.setattr(manual_execution, "send_scanner_with_retry", sent)
  client = redis_state.get_client()
  intent_payload = {
    "intent_id": "manual:9:0",
    "manual_signal_id": 9,
    "revision": 0,
    "direction": "BUY",
    "symbol": "XAU",
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
  candidate = json.loads(candidates[0][1]["payload"])
  assert candidate["mode"] == "manual_algo"
  assert candidate["bypass_analysis_gates"] is True
  sent.assert_not_awaited()


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
    rec["id"], runtime_config.delivery.telegram.telegram_channel_id, 9000 + rec["id"], "vip",
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
  truth = AsyncMock()
  monkeypatch.setattr(manual_execution, "_send_executor_truth", truth)
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_algo_owner_execution_dm_enabled": True})
  sid = await _algo_signal()
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "manual_opened",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "setup": "Manual Algo",
    "stream": "algo_manual",
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
  # zone-edge estimate was |4100.0 - 4110.0| = 100 pips; the real fill
  # (4100.5) risks only |4100.5 - 4110.0| = 95 pips, so the entry card gets
  # reposted with the corrected number on top of the usual "active" reply.
  assert send.await_count == 2
  card_texts = [call.args[0] for call in send.await_args_list]
  assert any("95 pips" in text for text in card_texts)
  truth.assert_awaited_once()


@pytest.mark.asyncio
async def test_fill_event_reposts_entry_card_with_real_fill_risk(monkeypatch):
  """Live 2026-09-10 (signal #309): a BUY zone 4333-4336 against sl 4330
  advertised "risk 60 pips" (the conservative zone-edge estimate), but the
  real fill landed at 4335.45 - true risk only 54 pips - and the close line
  correctly reported -0.7R for a -39 pip loss using the real fill, leaving
  the pinned card's "60 pips" impossible to reconcile against it. The card
  must now repost with the real-fill risk once the fill is known.
  """
  send = _mock_send(monkeypatch)
  sid = await _algo_signal(
    action="BUY", entry=4333.0, entry_end=4336.0, sl=4330.0,
    tps=[4339.0, 4342.0, 4348.0, 4354.0],
  )
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "manual_opened",
    "position_id": 309,
    "candidate_id": f"manual:{sid}:0",
    "setup": "Key Level",
    "stream": "algo_manual",
    "price": 4335.45,
    "volume": 600,
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["broker_fill_price"] == pytest.approx(4335.45)
  assert send.await_count == 2
  card_texts = [call.args[0] for call in send.await_args_list]
  assert any("risk <b>54 pips</b>" in text for text in card_texts)
  assert not any("risk <b>60 pips</b>" in text for text in card_texts)


@pytest.mark.asyncio
async def test_limit_placed_event_is_the_first_broker_confirmation(monkeypatch):
  sid = await _algo_signal()
  truth = AsyncMock()
  monkeypatch.setattr(manual_execution, "_send_executor_truth", truth)
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_algo_owner_execution_dm_enabled": True})
  event = {
    "type": "manual_limit_placed",
    "stream": "algo_manual",
    "candidate_id": f"manual:{sid}:0",
    "direction": "SELL",
    "entry_low": 4100.0,
    "entry_high": 4105.0,
    "stop_loss": 4110.0,
    "target_prices": [4095.0, 4090.0, 4080.0],
    "order_id": 777,
  }

  await manual_execution._handle_event(
    redis_state.get_client(), event, {},
  )

  row = await store.get_manual_signal(sid)
  assert row["execution_status"] == "pending"
  truth.assert_awaited_once()
  text = truth.await_args.args[0]
  assert "LIMIT ORDER PLACED" in text
  assert "777" in text
  assert f"manual:{sid}:0" in text


@pytest.mark.asyncio
async def test_limit_placed_never_posts_to_channel(monkeypatch):
  """manual_limit_placed must never reach the VIP/public channel - a manual
  /algo signal's entry can be several independent legs (shallow/mid/deep),
  each publishing its own manual_limit_placed event with no dedup, so a
  channel post here would spam an identical "waiting for fill" card 2-3x
  per signal. The owner-only DM (off by default) is the only surface."""
  sid = await _algo_signal()
  send = _mock_send(monkeypatch)
  truth = AsyncMock()
  monkeypatch.setattr(manual_execution, "_send_executor_truth", truth)
  event = {
    "type": "manual_limit_placed",
    "stream": "algo_manual",
    "candidate_id": f"manual:{sid}:0",
    "direction": "SELL",
    "entry_low": 4100.0,
    "entry_high": 4105.0,
    "stop_loss": 4110.0,
    "target_prices": [4095.0, 4090.0, 4080.0],
    "order_id": 777,
  }

  await manual_execution._handle_event(
    redis_state.get_client(), event, {},
  )

  row = await store.get_manual_signal(sid)
  assert row["execution_status"] == "pending"
  truth.assert_not_awaited()
  send.assert_not_awaited()


@pytest.mark.asyncio
async def test_fill_event_owner_dm_is_off_by_default(monkeypatch):
  """The owner-only "POSITION OPENED" debug DM is noise on top of the real
  "🟢 active" channel update trade_ops.do_active/post_result already sends
  for the same fill - must stay silent unless explicitly re-enabled."""
  send = _mock_send(monkeypatch)
  truth = AsyncMock()
  monkeypatch.setattr(manual_execution, "_send_executor_truth", truth)
  sid = await _algo_signal()
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "manual_opened",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "setup": "Manual Algo",
    "stream": "algo_manual",
    "price": 4100.5,
    "volume": 600,
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["execution_status"] == "filled"
  truth.assert_not_awaited()
  # The real subscriber-facing channel update must still fire unchanged,
  # alongside the entry-card repost with the real-fill-corrected risk.
  assert send.await_count == 2


@pytest.mark.asyncio
async def test_manual_rejection_reports_machine_reason(monkeypatch):
  sid = await _algo_signal()
  truth = AsyncMock()
  monkeypatch.setattr(manual_execution, "_send_executor_truth", truth)
  event = {
    "type": "rejected",
    "stream": "algo_manual",
    "candidate_id": f"manual:{sid}:0",
    "reason_code": "broker_account_not_hedged_for_opposite_manual_order",
  }

  await manual_execution._handle_event(
    redis_state.get_client(), event, {},
  )

  row = await store.get_manual_signal(sid)
  assert row["execution_status"] == "rejected"
  assert row["execution_error"] == (
    "broker_account_not_hedged_for_opposite_manual_order"
  )
  assert "ORDER REJECTED" in truth.await_args.args[0]
  assert "No broker order submitted" in truth.await_args.args[0]


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
async def test_multi_leg_same_tp_only_fans_out_furthest_once(monkeypatch):
  """Two filled ladder legs both hitting TP1 must post once, not twice."""
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid, 556: sid}

  for position_id in (555, 556):
    await manual_execution._handle_event(
      client,
      {
        "type": "take_profit",
        "position_id": position_id,
        "candidate_id": f"manual:{sid}:0",
        "price": 4095.0,
        "target_pips": 50,
      },
      positions,
    )

  row = await store.get_manual_signal(sid)
  assert len(row["legs"]) == 1
  assert row["legs"][0]["frac"] == pytest.approx(1 / 3, rel=1e-3)
  send.assert_awaited_once()
  assert "TP1" in send.await_args.args[0]


@pytest.mark.asyncio
async def test_multi_leg_further_tp_still_fans_out(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid, 556: sid}

  await manual_execution._handle_event(
    client,
    {
      "type": "take_profit",
      "position_id": 555,
      "price": 4095.0,
      "target_pips": 50,
      "candidate_id": f"manual:{sid}:0",
    },
    positions,
  )
  await manual_execution._handle_event(
    client,
    {
      "type": "take_profit",
      "position_id": 556,
      "price": 4090.0,
      "target_pips": 100,
      "candidate_id": f"manual:{sid}:0",
    },
    positions,
  )

  row = await store.get_manual_signal(sid)
  assert len(row["legs"]) == 2
  assert send.await_count == 2
  assert "TP1" in send.await_args_list[0].args[0]
  assert "TP2" in send.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_multi_leg_lower_ordinal_from_a_different_leg_still_books(
  monkeypatch,
):
  """Regression for live signal 102 (XAU SELL, 2026-08-20): shallow-first
  booking means different legs own disjoint ordinal ranges and their
  events can interleave in either order - a "highest ordinal ever seen"
  scalar treated a legitimate, never-before-booked LOWER ordinal from a
  sibling leg as stale once a higher one had already booked. Confirmed
  live: a leg's own real TP1 (then TP2, its full close) arrived after the
  group had already booked TP3 from a different leg, and both got
  silently dropped - not mislabeled, entirely missing, with that leg's
  full close never recorded anywhere. Booking must be keyed by ordinal,
  not gated on a running maximum.
  """
  send = _mock_send(monkeypatch)
  # Five-level ladder (matching the real signal 102's shape) so booking a
  # middle ordinal (TP3) does not read as the final target and close the
  # whole remaining position out from under the second event.
  sid = await _algo_signal(
    tps=[4095.0, 4090.0, 4080.0, 4070.0, 4050.0],
  )
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid, 556: sid}

  # Shallow leg (555) books TP3 (target_pips=200, a middle ordinal) first.
  await manual_execution._handle_event(
    client,
    {
      "type": "take_profit",
      "position_id": 555,
      "price": 4080.0,
      "target_pips": 200,
      "candidate_id": f"manual:{sid}:0",
    },
    positions,
  )
  # A sibling leg (556) then books its own TP1 (target_pips=50) - a lower
  # ordinal than what's already booked, but never booked before now.
  await manual_execution._handle_event(
    client,
    {
      "type": "take_profit",
      "position_id": 556,
      "price": 4095.0,
      "target_pips": 50,
      "candidate_id": f"manual:{sid}:0",
    },
    positions,
  )

  row = await store.get_manual_signal(sid)
  assert len(row["legs"]) == 2
  assert send.await_count == 2
  assert "TP3" in send.await_args_list[0].args[0]
  assert "TP1" in send.await_args_list[1].args[0]

  # A genuine repeat of an already-booked ordinal (from either leg) is
  # still correctly deduped.
  await manual_execution._handle_event(
    client,
    {
      "type": "take_profit",
      "position_id": 555,
      "price": 4080.0,
      "target_pips": 200,
      "candidate_id": f"manual:{sid}:0",
    },
    positions,
  )
  row = await store.get_manual_signal(sid)
  assert len(row["legs"]) == 2
  assert send.await_count == 2


@pytest.mark.asyncio
async def test_take_profit_unresolved_ordinal_is_dropped_not_spammed(monkeypatch):
  """Regression for live signal 99 (XAU SELL, 2026-08-20): before the
  reached==0 guard, a take_profit event whose target_pips did not match any
  configured TP ordinal fell through and booked+posted anyway with no dedup
  and no TP label - production logged TP1 booked twice (+39p, +38p, 2s
  apart) and TP2 booked twice (+68p, +66p, 3s apart) from a code path that
  predated this guard. An unresolved ordinal must now be dropped outright.
  """
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  # Configured targets [50, 100, 200]p (see _algo_signal defaults) - 5 is
  # below every configured ordinal, same shape as a stale/mismatched
  # target_pips that cannot resolve to TP1/TP2/TP3.
  event = {"type": "take_profit", "position_id": 555, "price": 4099.5, "target_pips": 5}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["legs"] == []
  send.assert_not_awaited()


@pytest.mark.asyncio
async def test_multi_leg_same_sl_move_only_fans_out_furthest_once(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid, 556: sid}

  for position_id in (555, 556):
    await manual_execution._handle_event(
      client,
      {
        "type": "stop_moved",
        "position_id": position_id,
        "candidate_id": f"manual:{sid}:0",
        "price": 4103.06,
        "message": "🛡 ApexVoid Algo stop → 4,103.06 (BE+6 ticks)",
      },
      positions,
    )

  row = await store.get_manual_signal(sid)
  assert row["sl"] == pytest.approx(4103.06)
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_multi_leg_further_sl_move_still_fans_out(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()  # SELL: further SL = lower
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid, 556: sid}

  await manual_execution._handle_event(
    client,
    {
      "type": "stop_moved",
      "position_id": 555,
      "candidate_id": f"manual:{sid}:0",
      "price": 4103.06,
      "message": "BE",
    },
    positions,
  )
  await manual_execution._handle_event(
    client,
    {
      "type": "stop_moved",
      "position_id": 556,
      "candidate_id": f"manual:{sid}:0",
      "price": 4095.0,
      "message": "trail after TP2",
    },
    positions,
  )

  row = await store.get_manual_signal(sid)
  assert row["sl"] == pytest.approx(4095.0)
  assert send.await_count == 2


@pytest.mark.no_database
def test_stop_is_further_direction_aware():
  buy = {"action": "BUY", "symbol": "XAU"}
  sell = {"action": "SELL", "symbol": "XAU"}
  assert manual_execution._stop_is_further(buy, 2010.0, 2000.0) is True
  assert manual_execution._stop_is_further(buy, 1990.0, 2000.0) is False
  assert manual_execution._stop_is_further(sell, 4090.0, 4100.0) is True
  assert manual_execution._stop_is_further(sell, 4110.0, 4100.0) is False
  assert manual_execution._stop_is_further(sell, 4100.02, 4100.0) is False


@pytest.mark.no_database
def test_tp_ordinal_reached_maps_target_pips():
  sig = {
    "action": "SELL",
    "symbol": "XAU",
    "entry": 4100.0,
    "entry_end": 4105.0,
    "sl": 4110.0,
    "tps": [4095.0, 4090.0, 4080.0],
  }
  assert manual_execution._tp_ordinal_reached(sig, 50) == 1
  assert manual_execution._tp_ordinal_reached(sig, 100) == 2
  assert manual_execution._tp_ordinal_reached(sig, 200) == 3
  assert manual_execution._tp_ordinal_reached(sig, 10) == 0


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_take_profit_skips_when_tp_already_reached(monkeypatch):
  execute = AsyncMock()
  post = AsyncMock()
  monkeypatch.setattr("app.signals.trade_ops._execute_close", execute)
  monkeypatch.setattr("app.signals.trade_ops.post_result", post)
  monkeypatch.setattr(
    manual_execution,
    "get_manual_signal",
    AsyncMock(return_value={
      "id": 7,
      "action": "SELL",
      "symbol": "XAU",
      "entry": 4100.0,
      "entry_end": 4105.0,
      "sl": 4110.0,
      "tps": [4095.0, 4090.0, 4080.0],
    }),
  )
  await redis_state.mark_tp_ordinal_booked(7, 1)

  await manual_execution._handle_take_profit(
    {"price": 4095.0, "target_pips": 50, "position_id": 556},
    7,
  )

  execute.assert_not_awaited()
  post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_manual_tp_reached_is_notify_only(monkeypatch):
  notify = AsyncMock(return_value={
    "action": "tp_reached",
    "ok": True,
    "sid": 7,
    "seq": 7,
    "tp_number": 4,
    "pips": 130,
  })
  post = AsyncMock()
  monkeypatch.setattr("app.signals.trade_ops.do_tp_reached", notify)
  monkeypatch.setattr("app.signals.trade_ops.post_result", post)
  sig = {
    "id": 7,
    "action": "SELL",
    "symbol": "XAU",
    "entry": 4100.0,
    "entry_end": 4105.0,
    "sl": 4110.0,
    "tps": [4097.0, 4094.0, 4090.0, 4087.0, 4080.0],
  }
  monkeypatch.setattr(manual_execution, "get_manual_signal", AsyncMock(return_value=sig))
  monkeypatch.setattr(
    manual_execution,
    "get_signal_by_execution_intent_id",
    AsyncMock(return_value=sig),
  )

  await manual_execution._handle_event(
    None,
    {
      "type": "manual_tp_reached",
      "stream": "algo_manual",
      "candidate_id": "manual:7:0",
      "position_id": 555,
      "target_pips": 130,
    },
    {555: 7},
  )

  notify.assert_awaited_once_with({
    "sid": 7,
    "symbol": "XAU",
    "tp_number": 4,
    "pips": 130,
  })
  post.assert_awaited_once()


@pytest.mark.no_database
def test_trade_result_renders_unbooked_tp_as_reached():
  from app.signals import trade_ops

  rendered = trade_ops.render_result({
    "action": "tp_reached",
    "ok": True,
    "seq": 7,
    "tp_number": 4,
    "pips": 130,
  }, "XAU")

  assert "TP4 reached" in rendered
  assert "no volume booked" in rendered


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_handle_event_sl_moved_is_treated_as_stop_moved(monkeypatch):
  execute = AsyncMock()
  post = AsyncMock()
  sig = {
    "id": 7,
    "action": "SELL",
    "symbol": "XAU",
    "entry": 4500.0,
    "entry_end": 4503.0,
    "sl": 4506.0,
    "broker_fill_price": 4503.0,
    "execution_mode": "algo",
    "execution_intent_id": "manual:7:0",
    "tps": [4497.0],
  }
  monkeypatch.setattr("app.signals.trade_ops._execute_sl", execute)
  monkeypatch.setattr("app.signals.trade_ops.post_result", post)
  monkeypatch.setattr(manual_execution, "get_manual_signal", AsyncMock(return_value=sig))
  monkeypatch.setattr(
    manual_execution,
    "get_signal_by_execution_intent_id",
    AsyncMock(return_value=sig),
  )

  await manual_execution._handle_event(
    None,
    {
      "type": "sl_moved",
      "candidate_id": "manual:7:0",
      "price": 4502.94,
      "message": "GROUP SL MOVED TO BE 4502.94",
    },
    {},
  )

  execute.assert_awaited()
  post.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_stop_moved_skips_when_not_further(monkeypatch):
  execute = AsyncMock()
  post = AsyncMock()
  monkeypatch.setattr("app.signals.trade_ops._execute_sl", execute)
  monkeypatch.setattr("app.signals.trade_ops.post_result", post)
  monkeypatch.setattr(
    manual_execution,
    "get_manual_signal",
    AsyncMock(return_value={
      "id": 7,
      "action": "SELL",
      "symbol": "XAU",
      "entry": 4100.0,
      "entry_end": 4105.0,
      "sl": 4103.06,
      "tps": [4095.0],
    }),
  )

  await manual_execution._handle_stop_moved(
    {"price": 4103.06, "position_id": 556, "message": "BE"},
    7,
  )

  execute.assert_not_awaited()
  post.assert_not_awaited()


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
async def test_handle_event_position_closed_full_close_defers_to_group_result(
  monkeypatch,
):
  """A leg closing completely (remaining_volume=0) must NOT finalize the
  signal by itself - a manual /algo signal can be several independent
  entry legs sharing one group, and only AutoTradeEngine.cs's own
  group_result event (fired once every leg is done) knows the true,
  correctly volume-weighted final result. See finalize_manual_group.
  """
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()  # SELL entry=4100/4105 sl=4110
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  event = {
    "type": "position_closed",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "price": 4110.0,
    "remaining_volume": 0,
    "leg_realized_pips": -100,
    "volume": 1000,
    "group_initial_volume": 1000,
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  assert 555 not in positions
  send.assert_not_awaited()

  group_event = {
    "type": "group_result",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "group_realized_pips": -100,
  }
  await manual_execution._handle_event(client, group_event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "closed"
  assert row["result_pips"] == -100
  send.assert_awaited_once()


@pytest.mark.no_database
@pytest.mark.asyncio
async def test_resolve_group_close_pips_prefers_booked_peak(monkeypatch):
  monkeypatch.setattr(
    manual_execution,
    "get_manual_signal",
    AsyncMock(return_value={
      "legs": [
        {"frac": 0.2, "pips": 60},
        {"frac": 0.2, "pips": 160},
      ],
    }),
  )
  monkeypatch.setattr(
    redis_state,
    "get_progress",
    AsyncMock(return_value={"tp": 4, "runner_pips": 160, "sl": False}),
  )
  resolved = await manual_execution._resolve_group_close_pips(100, 130.0)
  assert resolved == 160


@pytest.mark.asyncio
async def test_handle_event_group_result_keeps_peak_tp_not_shallow_blend(
  monkeypatch,
  sql,
):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  tp_legs = [
    {"frac": 0.2, "pips": 60, "ts": 1},
    {"frac": 0.2, "pips": 90, "ts": 2},
    {"frac": 0.2, "pips": 130, "ts": 3},
    {"frac": 0.2, "pips": 160, "ts": 4},
  ]
  await sql.exec(
    "UPDATE manual_signals SET legs = $1 WHERE id = $2",
    json.dumps(tp_legs),
    sid,
  )
  client = redis_state.get_client()
  await redis_state.set_runner_pips(sid, 160)
  await manual_execution._handle_event(
    client,
    {
      "type": "group_result",
      "candidate_id": f"manual:{sid}:0",
      "group_realized_pips": 130,
    },
    {},
  )
  row = await store.get_manual_signal(sid)
  assert row["status"] == "closed"
  assert row["result_pips"] == 160
  text = send.call_args.args[0]
  assert "+160" in text
  assert "achieved +160" in text


@pytest.mark.asyncio
async def test_final_close_declutters_interim_replies_and_shows_realized_rr(
  monkeypatch,
):
  """On the terminal close, every interim TP/reached/SL reply this signal
  accumulated during its life gets deleted and replaced by one summary
  reply on the root card carrying the realized R, instead of leaving a
  dozen scattered bubbles behind.
  """
  import re

  from app.signals import trade_ops

  send = _mock_send(monkeypatch)
  deleted = AsyncMock()
  monkeypatch.setattr(trade_ops, "delete_posts", deleted)
  sid = await _algo_signal(
    tps=[4095.0, 4090.0, 4080.0, 4070.0, 4050.0],
  )
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  # TP1 books for real (a genuine partial close).
  await manual_execution._handle_event(
    client,
    {
      "type": "take_profit", "position_id": 555, "price": 4095.0,
      "target_pips": 50, "candidate_id": f"manual:{sid}:0",
    },
    positions,
  )
  # TP3 is reached but never booked (no leg's own ladder owns it).
  await manual_execution._handle_event(
    client,
    {
      "type": "manual_tp_reached", "position_id": 555,
      "target_pips": 200, "candidate_id": f"manual:{sid}:0",
    },
    positions,
  )
  deleted.assert_not_awaited()
  send.reset_mock()

  await manual_execution._handle_event(
    client,
    {
      "type": "group_result",
      "position_id": 555,
      "candidate_id": f"manual:{sid}:0",
      "group_realized_pips": 160,
    },
    positions,
  )

  deleted.assert_awaited_once()
  assert len(deleted.await_args.args[0]) == 2
  send.assert_awaited_once()
  text = send.await_args.args[0]
  assert "closed" in text
  assert re.search(r"[+-]\d+\.\dR", text)


@pytest.mark.asyncio
async def test_realized_rr_uses_the_booking_legs_own_entry_price(monkeypatch):
  """``leg_entry_price`` on a real take_profit event must flow all the way
  through to the terminal close summary's R, not the advertised entry
  zone - a multi-leg group's actual fills routinely differ from it.
  """
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()  # SELL entry=4100/4105, sl=original_sl=4110
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  # TP1 (tps[0]=4095.0 -> 50p) books with a real leg fill of 4098.0 -
  # inside the advertised 4100-4105 zone, but not its 4102.5 midpoint.
  await manual_execution._handle_event(
    client,
    {
      "type": "take_profit", "position_id": 555, "price": 4095.0,
      "target_pips": 50, "candidate_id": f"manual:{sid}:0",
      "leg_realized_pips": 30, "leg_entry_price": 4098.0,
    },
    positions,
  )
  row = await store.get_manual_signal(sid)
  assert row["legs"][0]["entry_price"] == 4098.0
  send.reset_mock()

  await manual_execution._handle_event(
    client,
    {
      "type": "group_result",
      "position_id": 555,
      "candidate_id": f"manual:{sid}:0",
      "group_realized_pips": 30,
      # Same physical leg 555 - AutoTradeEngine.cs always carries its own
      # EntryPrice on this event too (see PublishAsync call sites).
      "leg_entry_price": 4098.0,
    },
    positions,
  )

  # _resolve_group_close_pips takes the runner-pips high-water mark (the
  # TP1 target level, 50) over the raw group_realized_pips (30) here, so
  # the achieved net is 50 - filled at the same leg's 4098.0. Risk against
  # original_sl 4110.0 = |4098-4110| = 12.0 price = 120 pips (XAU pip 0.1)
  # -> 50/120 = +0.4R. The zone-midpoint calc (4102.5) would give a
  # different, wrong number.
  send.assert_awaited_once()
  text = send.await_args.args[0]
  assert "+50 pips" in text
  assert "+0.4R" in text


@pytest.mark.asyncio
async def test_handle_event_position_closed_partial_close_uses_leg_fields_not_broker_fill(
  monkeypatch,
):
  """A genuine partial close (this leg itself still has volume left, e.g.
  a broker-side liquidity partial-fill on an owner flatten) is still
  booked immediately via the existing close_leg ledger - but from the
  event's OWN leg_realized_pips/volume/group_initial_volume, not the
  whole-signal broker_fill_price (which, for a multi-leg group, tracks the
  shallow risk-reference entry and would give the wrong number
  for a per-leg partial).
  """
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()  # SELL entry zone 4100-4105
  # Deliberately a DIFFERENT price than the leg's own fill, to prove the
  # partial-close pips come from the event's leg_realized_pips, not a
  # recompute against this stale, whole-signal column.
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4103.0)
  client = redis_state.get_client()
  positions = {555: sid}

  event = {
    "type": "position_closed",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "price": 4110.0,
    "remaining_volume": 400,
    "leg_realized_pips": -70,
    "volume": 600,
    "group_initial_volume": 1000,
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  legs = row["legs"]
  assert legs[0]["pips"] == -70
  assert legs[0]["frac"] == pytest.approx(0.6)
  assert 555 in positions
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_take_profit_falls_back_to_broker_fill_without_leg_pips(
  monkeypatch,
):
  """Fallback path only: an event predating leg_realized_pips (e.g. mid-
  deploy replay) still books using the stored broker_fill_price."""
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  sig = await store.get_manual_signal(sid)
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4104.0)
  client = redis_state.get_client()
  positions = {555: sid}
  event = {
    "type": "take_profit",
    "position_id": 555,
    "price": 4095.0,
    "target_pips": 50,
  }
  await manual_execution._handle_event(client, event, positions)
  # From fill 4104 → 4095 = +90 pips (zone-edge math would be +50).
  assert send.await_count >= 1
  text = send.call_args.args[0]
  assert "+90 pips" in text
  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  assert row["legs"][0]["pips"] == 90


@pytest.mark.asyncio
async def test_handle_take_profit_uses_the_booking_legs_own_pips(monkeypatch):
  """Owner-reported 2026-08-20: a multi-leg group's TP pips must reflect
  the booking leg's OWN entry-to-exit distance, not a shared "deepest
  filled" reference blended across the whole group. AutoTradeEngine.cs
  already computes and publishes this correctly per leg as
  leg_realized_pips - it must win over broker_fill_price whenever present,
  even when the two would otherwise disagree wildly (deep leg filled far
  better than this shallow leg's own +30p target)."""
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  # Stored shallow fill (4104) is a DIFFERENT leg's price - if the group
  # net calc were still used here it would report +90p, not this leg's
  # real +30p.
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4104.0)
  client = redis_state.get_client()
  positions = {555: sid}
  event = {
    "type": "take_profit",
    "position_id": 555,
    "price": 4095.0,
    "target_pips": 50,
    "leg_realized_pips": 30,
  }
  await manual_execution._handle_event(client, event, positions)
  assert send.await_count >= 1
  text = send.call_args.args[0]
  assert "+30 pips" in text
  row = await store.get_manual_signal(sid)
  assert row["legs"][0]["pips"] == 30



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
  await manual_execution.request_close(sid, f"manual:{sid}:0", frac=0.5)

  # This leg still has volume left (a genuine partial /trade_close 50%) -
  # its own leg_realized_pips/volume/group_initial_volume drive the ledger
  # entry, matching the "actual executed fraction" AutoTradeEngine.cs
  # reports rather than remembering the originally-requested one.
  event = {
    "type": "manual_closed",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "price": 4095.0,
    "remaining_volume": 300,
    "leg_realized_pips": 50,
    "volume": 300,
    "group_initial_volume": 600,
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  legs = row["legs"]
  assert legs[0]["frac"] == pytest.approx(0.5)
  assert legs[0]["pips"] == 50
  assert row["status"] == "open"
  assert 555 in positions
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_event_manual_closed_full_close_defers_to_group_result(
  monkeypatch,
):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}
  await manual_execution.request_close(sid, f"manual:{sid}:0", frac=None)

  event = {
    "type": "manual_closed",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "price": 4095.0,
    "remaining_volume": 0,
    "leg_realized_pips": 50,
    "volume": 600,
    "group_initial_volume": 600,
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  assert 555 not in positions
  send.assert_not_awaited()

  group_event = {
    "type": "group_result",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "group_realized_pips": 50,
  }
  await manual_execution._handle_event(client, group_event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "closed"
  assert row["result_pips"] == 50
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
  await store.insert_signal_post(sid, -100987654321, 9900 + sid, "public")
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {"type": "manual_cancelled", "candidate_id": f"manual:{sid}:0"}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "cancelled"
  assert row["execution_status"] == "cancelled"
  assert row["algo_armed"] is False
  # Broker-confirmed cancel is a real signal lifecycle result and replies to
  # both persisted VIP + public roots.
  assert send.await_count == 2
  texts = [call.args[0] for call in send.await_args_list]
  assert all("cancelled" in text for text in texts)
  assert any(f"#{sid}" in text for text in texts)
  assert any(f"#{sid}" not in text for text in texts)


@pytest.mark.asyncio
async def test_handle_event_manual_cancelled_hard_deletes_when_pending_delete(
  monkeypatch,
):
  # /trade_delete flagged the intent — broker cancel must remove the row
  # and posts (🗑 deleted), not leave ❌ cancelled.
  _mock_send(monkeypatch)
  ack = AsyncMock()
  monkeypatch.setattr(manual_execution, "_send_owner_command_ack", ack)
  monkeypatch.setattr(broadcast, "delete_posts", AsyncMock())
  sid = await _algo_signal()
  await store.insert_signal_post(sid, -100987654321, 9900 + sid, "vip")
  await store.insert_signal_post(sid, -100987654322, 9800 + sid, "public")
  intent = f"manual:{sid}:0"
  client = redis_state.get_client()
  await manual_execution.mark_pending_delete(intent)

  await manual_execution._handle_event(
    client,
    {"type": "manual_cancelled", "candidate_id": intent},
    {},
  )

  assert await store.get_manual_signal(sid) is None
  ack.assert_awaited_once()
  assert "deleted" in ack.await_args.args[0]
  assert "cancelled" not in ack.await_args.args[0]
  assert await client.get(f"manual_trade:pending_delete:{intent}") is None


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
async def test_handle_event_stop_moved_fans_out_manual_algo_be_move(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  event = {
    "type": "stop_moved",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "price": 4103.06,
    "message": "🛡 ApexVoid Algo stop → 4,103.06 (BE+6 ticks)",
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["sl"] == pytest.approx(4103.06)
  send.assert_awaited_once()
  text = send.await_args.args[0]
  assert "move SL to" in text


@pytest.mark.asyncio
async def test_handle_event_stop_moved_ignores_autonomous_positions(monkeypatch):
  send = _mock_send(monkeypatch)
  client = redis_state.get_client()

  event = {"type": "stop_moved", "position_id": 999, "price": 4108.0}
  await manual_execution._handle_event(client, event, {})

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
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_trade_command_stream": "manual_trade:cmd1",})
  client = redis_state.get_client()

  await manual_execution.request_cancel("manual:9:0")

  entries = await client.xrange("manual_trade:cmd1")
  assert len(entries) == 1
  payload = json.loads(entries[0][1]["payload"])
  assert payload == {"type": "cancel_pending", "intent_id": "manual:9:0"}


@pytest.mark.asyncio
async def test_request_close_xadds_close_command_by_intent_id(monkeypatch):
  # Routed by intent_id (the signal's group token), not a single
  # position_id - AutoTradeEngine.cs resolves and closes every open leg in
  # the group, so /trade_close actually flattens a multi-leg manual /algo
  # position instead of leaving two of three legs open on the broker.
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_trade_command_stream": "manual_trade:cmd2",})
  client = redis_state.get_client()

  await manual_execution.request_close(9, "manual:9:0", frac=0.5)

  entries = await client.xrange("manual_trade:cmd2")
  payload = json.loads(entries[0][1]["payload"])
  assert payload == {"type": "close", "intent_id": "manual:9:0", "frac": 0.5}


@pytest.mark.asyncio
async def test_request_move_sl_xadds_move_sl_command(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_trade_command_stream": "manual_trade:cmd3",})
  client = redis_state.get_client()

  await manual_execution.request_move_sl(9, 555, 4108.5)

  entries = await client.xrange("manual_trade:cmd3")
  payload = json.loads(entries[0][1]["payload"])
  assert payload == {"type": "move_sl", "position_id": 555, "price": 4108.5}

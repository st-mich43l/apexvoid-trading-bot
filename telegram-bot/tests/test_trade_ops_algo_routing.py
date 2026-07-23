from unittest.mock import AsyncMock

import pytest

from app.persistence import store
from app.signals import manual_execution, trade_ops


# ---------------------------------------------------------------------------
# Regression: non-algo (execution_mode='notify', the pre-existing default)
# do_close/do_sl/do_cancel must be completely unaffected by this PR.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_do_close_non_algo_calls_close_leg_with_unchanged_arguments(monkeypatch):
  await store.init_db()
  rec = await store.store_manual_signal(
    1, "BUY", 2000.0, 2002.0, 1990.0, [2010.0, 2020.0],
  )
  close_row = {
    "id": rec["id"],
    "channel_message_id": None,
    "daily_seq": 1,
    "symbol": "XAU",
    "visibility": "both",
    "closed": True,
    "net": 50,
    "remaining": 0.0,
    "frac": 1.0,
  }
  close_leg_mock = AsyncMock(return_value=close_row)
  monkeypatch.setattr(trade_ops, "close_leg", close_leg_mock)
  store_pips_mock = AsyncMock()
  monkeypatch.setattr(trade_ops, "store_pips", store_pips_mock)

  result = await trade_ops.do_close({
    "sid": rec["id"], "symbol": "XAU", "pips": 50, "frac": None, "reply_to": 7,
  })

  close_leg_mock.assert_awaited_once_with(rec["id"], 50, None)
  store_pips_mock.assert_awaited_once()
  assert result == {
    "action": "close",
    "ok": True,
    "row": close_row,
    "pips": 50,
    "reply_to": 7,
    "tp_number": None,
  }
  assert "pending" not in result


@pytest.mark.asyncio
async def test_do_sl_non_algo_calls_update_sl_with_unchanged_arguments(monkeypatch):
  await store.init_db()
  rec = await store.store_manual_signal(1, "BUY", 2000.0, 2002.0, 1990.0, [2010.0])
  signals = [{
    "id": rec["id"], "entry": 2000.0, "entry_end": 2002.0,
    "channel_message_id": None,
  }]
  monkeypatch.setattr(trade_ops, "get_open_signals", AsyncMock(return_value=signals))
  update_row = {"id": rec["id"], "channel_message_id": None}
  update_mock = AsyncMock(return_value=update_row)
  monkeypatch.setattr(trade_ops, "update_sl", update_mock)
  clear_mock = AsyncMock()
  monkeypatch.setattr(trade_ops, "clear_sl_alert", clear_mock)

  result = await trade_ops.do_sl({
    "sid": rec["id"], "symbol": "XAU", "sl": "be", "reply_to": 5,
  })

  update_mock.assert_awaited_once_with(rec["id"], 2001.0)
  clear_mock.assert_awaited_once_with(rec["id"])
  assert result == {
    "action": "sl",
    "ok": True,
    "row": update_row,
    "price": 2001.0,
    "is_be": True,
    "reply_to": 5,
  }
  assert "pending" not in result


@pytest.mark.asyncio
async def test_do_cancel_non_algo_calls_cancel_manual_signal_with_unchanged_arguments(
  monkeypatch,
):
  await store.init_db()
  rec = await store.store_manual_signal(1, "BUY", 2000.0, 2002.0, 1990.0, [2010.0])
  cancel_row = {"id": rec["id"], "channel_message_id": None}
  cancel_mock = AsyncMock(return_value=cancel_row)
  monkeypatch.setattr(trade_ops, "cancel_manual_signal", cancel_mock)

  result = await trade_ops.do_cancel({
    "sid": rec["id"], "symbol": "XAU", "reply_to": 3,
  })

  cancel_mock.assert_awaited_once_with(rec["id"])
  assert result == {
    "action": "cancel", "ok": True, "row": cancel_row, "reply_to": 3,
  }
  assert "pending" not in result


@pytest.mark.asyncio
async def test_do_close_not_open_returns_unchanged_error_shape():
  await store.init_db()
  result = await trade_ops.do_close({
    "sid": 999999, "symbol": "XAU", "pips": 10, "frac": None,
  })
  assert result == {"action": "close", "ok": False, "error": "not_open"}


@pytest.mark.asyncio
async def test_do_sl_signal_not_found_returns_unchanged_error_shape():
  await store.init_db()
  result = await trade_ops.do_sl({"sid": 999999, "symbol": "XAU", "sl": "be"})
  assert result == {"action": "sl", "ok": False, "error": "not_open"}


@pytest.mark.asyncio
async def test_do_cancel_not_open_returns_unchanged_error_shape():
  await store.init_db()
  result = await trade_ops.do_cancel({"sid": 999999, "symbol": "XAU"})
  assert result == {"action": "cancel", "ok": False, "error": "not_open"}


# ---------------------------------------------------------------------------
# Algo-armed/filled signals: this is the actual defect this feature fixes -
# owner overrides now route to the real broker instead of only mutating
# Postgres/Telegram.
# ---------------------------------------------------------------------------

async def _algo_signal(**overrides) -> dict:
  await store.init_db()
  base = dict(
    ts=1, action="SELL", entry=4100.0, entry_end=4105.0, sl=4110.0,
    tps=[4095.0, 4090.0, 4080.0], execution_mode="algo",
  )
  base.update(overrides)
  return await store.store_manual_signal(**base)


@pytest.mark.asyncio
async def test_do_close_defers_to_broker_when_algo_and_filled(monkeypatch):
  rec = await _algo_signal()
  await store.set_execution_fill(
    rec["id"], broker_position_id=555, broker_fill_price=4100.0,
  )
  request_close = AsyncMock()
  monkeypatch.setattr(manual_execution, "request_close", request_close)
  close_leg_mock = AsyncMock()
  monkeypatch.setattr(trade_ops, "close_leg", close_leg_mock)

  result = await trade_ops.do_close({
    "sid": rec["id"], "symbol": "XAU", "pips": 30, "frac": 0.5, "reply_to": None,
  })

  request_close.assert_awaited_once_with(rec["id"], 555, frac=0.5)
  close_leg_mock.assert_not_awaited()
  assert result["ok"] is True
  assert result["pending"] is True
  assert trade_ops.render_result(result, "XAU") == (
    f"⏳ #{rec['id']} close requested — awaiting broker confirmation"
  )


@pytest.mark.asyncio
async def test_do_close_falls_through_when_algo_but_not_yet_filled(monkeypatch):
  rec = await _algo_signal()
  await store.set_execution_intent(
    rec["id"], intent_id="manual:x:0", status="armed", revision=0,
  )
  request_close = AsyncMock()
  monkeypatch.setattr(manual_execution, "request_close", request_close)

  result = await trade_ops.do_close({
    "sid": rec["id"], "symbol": "XAU", "pips": 30, "frac": None, "reply_to": None,
  })

  request_close.assert_not_awaited()
  assert result.get("pending") is None


@pytest.mark.asyncio
async def test_do_sl_defers_to_broker_when_algo_and_filled(monkeypatch):
  rec = await _algo_signal()
  await store.set_execution_fill(
    rec["id"], broker_position_id=555, broker_fill_price=4100.0,
  )
  request_move_sl = AsyncMock()
  monkeypatch.setattr(manual_execution, "request_move_sl", request_move_sl)
  update_mock = AsyncMock()
  monkeypatch.setattr(trade_ops, "update_sl", update_mock)

  result = await trade_ops.do_sl({"sid": rec["id"], "symbol": "XAU", "sl": "4108.5"})

  request_move_sl.assert_awaited_once_with(rec["id"], 555, 4108.5)
  update_mock.assert_not_awaited()
  assert result["pending"] is True


@pytest.mark.asyncio
async def test_do_cancel_defers_to_broker_when_algo_and_armed(monkeypatch):
  rec = await _algo_signal()
  await store.set_execution_intent(
    rec["id"], intent_id="manual:x:1", status="armed", revision=0,
  )
  request_cancel = AsyncMock()
  monkeypatch.setattr(manual_execution, "request_cancel", request_cancel)
  cancel_mock = AsyncMock()
  monkeypatch.setattr(trade_ops, "cancel_manual_signal", cancel_mock)

  result = await trade_ops.do_cancel({"sid": rec["id"], "symbol": "XAU"})

  request_cancel.assert_awaited_once_with("manual:x:1")
  cancel_mock.assert_not_awaited()
  assert result["pending"] is True


@pytest.mark.asyncio
async def test_do_cancel_falls_through_when_algo_and_already_filled(monkeypatch):
  # A filled position is not a broker "cancel" verb (there is no resting
  # order left to cancel) - the owner should use /trade_close instead. This
  # intentionally stays a Postgres-only cancel; the real broker position is
  # untouched either way.
  rec = await _algo_signal()
  await store.set_execution_fill(
    rec["id"], broker_position_id=555, broker_fill_price=4100.0,
  )
  request_cancel = AsyncMock()
  monkeypatch.setattr(manual_execution, "request_cancel", request_cancel)

  result = await trade_ops.do_cancel({"sid": rec["id"], "symbol": "XAU"})

  request_cancel.assert_not_awaited()
  assert result.get("pending") is None
  row = await store.get_manual_signal(rec["id"])
  assert row["status"] == "cancelled"


@pytest.mark.asyncio
async def test_do_close_falls_through_for_errored_algo_signal(monkeypatch):
  rec = await _algo_signal()
  await store.set_execution_status(rec["id"], "error", error="boom")
  request_close = AsyncMock()
  monkeypatch.setattr(manual_execution, "request_close", request_close)

  result = await trade_ops.do_close({
    "sid": rec["id"], "symbol": "XAU", "pips": 10, "frac": None,
  })

  request_close.assert_not_awaited()
  assert result.get("pending") is None


# ---------------------------------------------------------------------------
# do_reopen execution_mode bug fix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_do_reopen_inherits_parent_algo_execution_mode():
  rec = await _algo_signal()
  await store.close_manual_signal(rec["id"], -50)

  result = await trade_ops.do_reopen({"sid": rec["id"], "symbol": "XAU"})

  assert result["ok"] is True
  reopened = await store.get_manual_signal(result["record"]["id"])
  assert reopened["execution_mode"] == "algo"


@pytest.mark.asyncio
async def test_do_reopen_defaults_to_notify_for_a_notify_parent():
  await store.init_db()
  rec = await store.store_manual_signal(1, "SELL", 4100.0, 4105.0, 4110.0, [4095.0])
  await store.close_manual_signal(rec["id"], -50)

  result = await trade_ops.do_reopen({"sid": rec["id"], "symbol": "XAU"})

  reopened = await store.get_manual_signal(result["record"]["id"])
  assert reopened["execution_mode"] == "notify"


# ---------------------------------------------------------------------------
# render_result pending state
# ---------------------------------------------------------------------------

def test_render_result_pending_close():
  result = {
    "action": "close", "ok": True, "pending": True,
    "row": {"id": 1, "daily_seq": 3},
  }
  assert trade_ops.render_result(result, "XAU") == (
    "⏳ #3 close requested — awaiting broker confirmation"
  )


def test_render_result_pending_cancel():
  result = {
    "action": "cancel", "ok": True, "pending": True,
    "row": {"id": 1, "daily_seq": 4},
  }
  assert trade_ops.render_result(result, "XAU") == (
    "⏳ #4 cancel requested — awaiting broker confirmation"
  )


def test_render_result_pending_sl():
  result = {
    "action": "sl", "ok": True, "pending": True,
    "row": {"id": 1, "daily_seq": 5},
  }
  assert trade_ops.render_result(result, "XAU") == (
    "⏳ #5 stop-loss move requested — awaiting broker confirmation"
  )


def test_render_result_public_tier_pending_close_has_no_seq():
  result = {
    "action": "close", "ok": True, "pending": True,
    "row": {"id": 1, "daily_seq": 3},
  }
  assert trade_ops.render_result(result, "XAU", "public") == (
    "⏳ close requested — awaiting broker confirmation"
  )

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app import telegram


def _signal(
  row_id: int,
  daily_seq: int,
  trade_date: str,
  message_id: int,
) -> dict:
  return {
    "id": row_id,
    "daily_seq": daily_seq,
    "trade_date": trade_date,
    "channel_message_id": message_id,
  }


def _channel_message(text: str) -> SimpleNamespace:
  return SimpleNamespace(
    text=text,
    message_id=900,
    chat=SimpleNamespace(id=-100123456789),
    reply_to_message=SimpleNamespace(message_id=700),
  )


def test_owner_lock_fails_closed(monkeypatch):
  msg = SimpleNamespace(from_user=SimpleNamespace(id=42))

  monkeypatch.setattr(telegram.settings, "telegram_owner_id", None)
  assert telegram._is_owner(msg) is False

  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  assert telegram._is_owner(msg) is True
  msg.from_user.id = 99
  assert telegram._is_owner(msg) is False


@pytest.mark.asyncio
async def test_calculate_uses_local_log_and_correct_status(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  summary = AsyncMock(return_value={
    "wins": 0,
    "win_pips": 0,
    "losses": 0,
    "loss_pips": 0,
    "net": 0,
    "total": 0,
  })
  monkeypatch.setattr(telegram, "get_pips_summary", summary)
  msg = SimpleNamespace(
    text="/trade_pips today",
    from_user=SimpleNamespace(id=42),
    answer=AsyncMock(),
  )

  await telegram.handle_trade_pips(msg)

  assert msg.answer.await_args_list[0].args[0] == "📊 Calculating pips…"
  summary.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_sid_paths(monkeypatch):
  today = "2026-07-03"
  opens = [
    _signal(1, 2, "2026-07-02", 101),
    _signal(2, 2, today, 102),
  ]
  monkeypatch.setattr(telegram, "_today_str", lambda: today)
  get_open = AsyncMock(return_value=opens)
  by_channel = AsyncMock(return_value={"id": 9})
  monkeypatch.setattr(telegram, "get_open_signals", get_open)
  monkeypatch.setattr(
    telegram,
    "get_signal_by_post",
    by_channel,
  )

  assert await telegram._resolve_sid(2, None) == 2
  assert await telegram._resolve_sid(None, 555) == 9

  get_open.return_value = [_signal(4, 1, today, 104)]
  assert await telegram._resolve_sid(None, None) == 4

  get_open.return_value = [
    _signal(4, 1, today, 104),
    _signal(5, 2, today, 105),
  ]
  assert await telegram._resolve_sid(None, None) is None


@pytest.mark.asyncio
async def test_channel_close_unifies_accounting(monkeypatch):
  msg = _channel_message("close #2 +80")
  resolve = AsyncMock(return_value=22)
  close = AsyncMock(return_value={"action": "close", "ok": True})
  post = AsyncMock(return_value="closed")
  delete = AsyncMock()
  monkeypatch.setattr(telegram, "_resolve_sid", resolve)
  monkeypatch.setattr(telegram, "do_close", close)
  monkeypatch.setattr(telegram, "post_result", post)
  monkeypatch.setattr(telegram, "_delete_command", delete)

  await telegram.handle_channel_close(msg)

  ctx = close.await_args.args[0]
  assert ctx == {
    "sid": 22,
    "symbol": "XAU",
    "chat_id": -100123456789,
    "reply_to": 700,
    "pips": 80,
    "frac": None,
  }
  post.assert_awaited_once_with(
    {"action": "close", "ok": True},
    "XAU",
  )
  delete.assert_awaited_once_with(msg)


@pytest.mark.asyncio
async def test_partial_then_final_close_books_weighted_net_once(monkeypatch):
  partial_msg = _channel_message("close #3 +50 50%")
  final_msg = _channel_message("close #3 +90")
  monkeypatch.setattr(telegram, "_resolve_sid", AsyncMock(return_value=23))
  close = AsyncMock(return_value={"action": "close", "ok": True})
  post = AsyncMock(return_value="ok")
  monkeypatch.setattr(telegram, "do_close", close)
  monkeypatch.setattr(telegram, "post_result", post)
  monkeypatch.setattr(telegram, "_delete_command", AsyncMock())

  await telegram.handle_channel_close(partial_msg)
  await telegram.handle_channel_close(final_msg)

  assert close.await_args_list[0].args[0]["frac"] == 0.5
  assert close.await_args_list[0].args[0]["pips"] == 50
  assert close.await_args_list[1].args[0]["frac"] is None
  assert close.await_args_list[1].args[0]["pips"] == 90
  assert post.await_count == 2


@pytest.mark.asyncio
async def test_overbook_is_rejected_without_accounting(monkeypatch):
  msg = _channel_message("close #3 +40 60%")
  monkeypatch.setattr(telegram, "_resolve_sid", AsyncMock(return_value=23))
  close = AsyncMock(return_value={"action": "close", "ok": True})
  post = AsyncMock(return_value="overbook")
  monkeypatch.setattr(telegram, "do_close", close)
  monkeypatch.setattr(telegram, "post_result", post)
  monkeypatch.setattr(telegram, "_delete_command", AsyncMock())

  await telegram.handle_channel_close(msg)

  assert close.await_args.args[0]["frac"] == 0.6
  post.assert_awaited_once()


def test_close_be_parses_as_full_remaining():
  assert telegram._parse_close("close #3 be") == (3, 0, None)


def test_fallback_router_is_included_last():
  assert telegram.dp.sub_routers[-1].name == "fallback"


@pytest.mark.asyncio
async def test_channel_active_deduplicates(monkeypatch):
  msg = _channel_message("active #1")
  row = _signal(11, 1, "2026-07-03", 701)
  monkeypatch.setattr(telegram, "_resolve_sid", AsyncMock(return_value=11))
  active = AsyncMock(side_effect=[
    {"action": "active", "ok": True, "row": row},
    {"action": "active", "ok": False},
  ])
  post = AsyncMock(return_value="active")
  delete = AsyncMock()
  monkeypatch.setattr(telegram, "do_active", active)
  monkeypatch.setattr(telegram, "post_result", post)
  monkeypatch.setattr(telegram, "_delete_command", delete)

  await telegram.handle_channel_active(msg)
  await telegram.handle_channel_active(msg)

  assert active.await_count == 2
  post.assert_awaited_once()
  delete.assert_awaited_once_with(msg)


@pytest.mark.asyncio
async def test_handler_order_and_bare_pips_default_off(monkeypatch):
  dm_callbacks = [
    handler.callback.__name__
    for handler in telegram.dp.observers["message"].handlers
  ]
  for name in (
    "handle_trade_reopen",
    "handle_trade_uncclose",
    "handle_trade_tag",
    "handle_trade_untagged",
    "handle_trade_note",
    "handle_trade_review",
    "handle_trade_stats",
  ):
    assert dm_callbacks.index(name) < dm_callbacks.index(
      "handle_private_signal"
    )

  callbacks = [
    handler.callback.__name__
    for handler in telegram.dp.observers["channel_post"].handlers
  ]
  ordered = [
    "handle_channel_active",
    "handle_channel_close",
    "handle_channel_cancel",
    "handle_channel_sl",
    "handle_channel_reopen",
    "handle_channel_tag",
    "handle_channel_note",
    "handle_profit_screenshot",
    "handle_profit_text",
  ]
  assert [callbacks.index(name) for name in ordered] == sorted(
    callbacks.index(name) for name in ordered
  )

  handle_pips = AsyncMock()
  monkeypatch.setattr(telegram, "_handle_pips", handle_pips)
  msg = SimpleNamespace(text="still +40 pips to go")

  monkeypatch.setattr(telegram.settings, "auto_book_bare_pips", False)
  await telegram.handle_profit_text(msg)
  handle_pips.assert_not_awaited()

  monkeypatch.setattr(telegram.settings, "auto_book_bare_pips", True)
  await telegram.handle_profit_text(msg)
  handle_pips.assert_awaited_once_with(
    msg,
    "still +40 pips to go",
    has_photo=False,
  )


@pytest.mark.asyncio
async def test_move_sl_to_breakeven_resets_alert(monkeypatch):
  signal = {
    **_signal(31, 4, "2026-07-03", 704),
    "entry": 2000.0,
    "entry_end": 2004.0,
  }
  from app import trade_ops
  monkeypatch.setattr(
    trade_ops,
    "get_open_signals",
    AsyncMock(return_value=[signal]),
  )
  update = AsyncMock(return_value=signal)
  monkeypatch.setattr(trade_ops, "update_sl", update)

  from app import redis_state
  await redis_state.set_sl_flag(31)
  moved = await trade_ops.do_sl({
    "sid": 31,
    "symbol": "XAU",
    "sl": "be",
  })

  update.assert_awaited_once_with(31, 2002.0)
  assert trade_ops.render_result(moved, "XAU") == (
    "🛡 #4 move SL to 2,002 (BE)"
  )
  assert (await redis_state.get_progress(31))["sl"] is False

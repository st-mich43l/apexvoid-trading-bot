import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app import dedup, symbols, telegram
from app.handlers import scanner_dm
from app.reports import format_review
from app.symbols import SYMBOLS, pip_for, symbol_for_channel


def _dm(text: str, user_id: int = 42):
  return SimpleNamespace(
    text=text,
    from_user=SimpleNamespace(id=user_id),
    answer=AsyncMock(),
  )


def _channel(text: str, chat_id: int = -100123456789):
  return SimpleNamespace(
    text=text,
    message_id=900,
    chat=SimpleNamespace(id=chat_id),
    reply_to_message=SimpleNamespace(message_id=700),
  )


@pytest.mark.asyncio
async def test_scoped_command_menu(monkeypatch):
  target = SimpleNamespace(set_my_commands=AsyncMock())
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)

  await telegram.setup_commands(target)

  first, second = target.set_my_commands.await_args_list
  assert first.args[0] == []
  assert first.kwargs["scope"].type == "default"
  assert second.args[0] == telegram.OWNER_COMMANDS
  assert second.kwargs["scope"].chat_id == 42
  assert {command.command for command in telegram.OWNER_COMMANDS} == {
    "trade_active", "trade_close", "trade_uncclose", "trade_tp",
    "trade_sl", "trade_cancel", "trade_delete",
    "trade_reopen", "trade_tag", "trade_untagged", "trade_note", "trade_review",
    "trade_map", "auto_status", "auto_pause", "auto_resume",
    "trade_stats", "trade_pips", "help",
  } | {"trade_open"}


@pytest.mark.asyncio
async def test_signal_bot_exposes_public_start_and_owner_trade_map(monkeypatch):
  target = SimpleNamespace(set_my_commands=AsyncMock())
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)

  await telegram.setup_scanner_commands(target)

  first, second = target.set_my_commands.await_args_list
  assert first.args[0] == telegram.SCANNER_PUBLIC_COMMANDS
  assert second.args[0] == telegram.SCANNER_OWNER_COMMANDS
  assert second.kwargs["scope"].chat_id == 42
  assert [command.command for command in telegram.SCANNER_OWNER_COMMANDS] == [
    "start",
    "trade_map",
    "auto_status",
    "auto_pause",
    "auto_resume",
  ]


@pytest.mark.asyncio
async def test_signal_bot_trade_map_handler_uses_shared_delivery(monkeypatch):
  deliver = AsyncMock()
  monkeypatch.setattr(scanner_dm, "deliver_trade_map", deliver)
  msg = _dm("/trade_map XAU")

  await scanner_dm.handle_trade_map(msg)

  deliver.assert_awaited_once_with(msg)


@pytest.mark.asyncio
async def test_signal_bot_start_handler_uses_shared_welcome(monkeypatch):
  welcome = AsyncMock()
  monkeypatch.setattr(scanner_dm, "deliver_welcome", welcome)
  msg = _dm("/start", user_id=999)

  await scanner_dm.handle_start(msg)

  welcome.assert_awaited_once_with(msg)


@pytest.mark.asyncio
async def test_start_welcomes_public_users(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  msg = _dm("/start", user_id=999)

  await telegram.handle_start(msg)

  out = msg.answer.await_args.args[0]
  assert "👋 <b>Welcome to Apex Void Trading</b>" in out
  assert "📢 <b>Public channel</b>" in out
  assert "📚 <b>Trading Knowledge Base</b>" in out
  assert "✨ Follow the channel" in out
  assert "@apexvoidtrading" in out
  assert "https://t.me/apexvoidtrading" in out
  assert "trading.apexvoid.net" in out


@pytest.mark.asyncio
async def test_trade_map_is_owner_gated_and_returns_current_board(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  send = AsyncMock(return_value=True)
  monkeypatch.setattr(telegram, "send_current_market_map", send)
  owner = _dm("/trade_map XAU")
  stranger = _dm("/trade_map XAU", user_id=999)

  await telegram.handle_trade_map(owner)
  await telegram.handle_trade_map(stranger)

  send.assert_awaited_once_with("XAU")
  owner.answer.assert_awaited_once_with(
    "✅ Market Map sent via the dedicated signal bot."
  )
  stranger.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_trade_open_lists_open_signals(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(
    telegram,
    "get_open_signals",
    AsyncMock(return_value=[{
      "id": 9, "daily_seq": 6, "symbol": "XAU", "action": "BUY",
      "entry": 4100.0, "entry_end": 4105.0, "sl": 4088.0,
      "fill_state": "filled", "legs": [{"frac": 0.5, "pips": 90}],
    }]),
  )
  msg = _dm("/trade_open")

  await telegram.handle_trade_open(msg)

  out = msg.answer.await_args.args[0]
  assert "#6 XAU BUY 4100–4105" in out
  assert "SL 4088" in out and "filled" in out and "50% open" in out


@pytest.mark.asyncio
async def test_trade_open_empty(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(telegram, "get_open_signals", AsyncMock(return_value=[]))
  msg = _dm("/trade_open")

  await telegram.handle_trade_open(msg)

  assert "No open signals" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_channel_and_dm_close_share_executor(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(
    telegram,
    "_resolve_sid",
    AsyncMock(return_value=3),
  )
  execute = AsyncMock(return_value={"action": "close", "ok": True})
  monkeypatch.setattr(telegram, "do_close", execute)
  monkeypatch.setattr(
    telegram,
    "post_result",
    AsyncMock(return_value="closed"),
  )
  monkeypatch.setattr(telegram, "_delete_command", AsyncMock())

  await telegram.handle_channel_close(_channel("close #3 +80"))
  await telegram.handle_trade_close(_dm("/trade_close 3 +80"))

  channel_ctx = execute.await_args_list[0].args[0]
  dm_ctx = execute.await_args_list[1].args[0]
  assert {
    key: channel_ctx[key]
    for key in ("sid", "symbol", "chat_id", "pips", "frac")
  } == {
    key: dm_ctx[key]
    for key in ("sid", "symbol", "chat_id", "pips", "frac")
  }


@pytest.mark.asyncio
async def test_manual_tp_command_is_notify_only(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(
    telegram,
    "_resolve_sid",
    AsyncMock(return_value=3),
  )
  execute = AsyncMock(return_value={
    "action": "tp",
    "ok": True,
    "sid": 3,
    "seq": 1,
    "tp_number": 2,
    "pips": 56,
  })
  post = AsyncMock(return_value="🎯 #1 TP2 +56 pips 💸")
  monkeypatch.setattr(telegram, "do_tp", execute)
  monkeypatch.setattr(telegram, "post_result", post)
  msg = _dm("/trade_tp XAU #1 2 +56")

  await telegram.handle_trade_tp(msg)

  execute.assert_awaited_once_with({
    "sid": 3,
    "symbol": "XAU",
    "tp_number": 2,
    "pips": 56,
  })
  post.assert_awaited_once_with(execute.return_value, "XAU")
  msg.answer.assert_awaited_once_with("🎯 #1 TP2 +56 pips 💸")


@pytest.mark.asyncio
async def test_uncclose_command_resolves_closed_signal(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(
    telegram,
    "_resolve_any_sid",
    AsyncMock(return_value=3),
  )
  execute = AsyncMock(return_value={
    "action": "uncclose",
    "ok": True,
    "sid": 3,
    "row": {"id": 3, "daily_seq": 1},
    "remaining": 1.0,
  })
  post = AsyncMock(return_value="♻️ #1 restored — trade still running")
  monkeypatch.setattr(telegram, "do_uncclose", execute)
  monkeypatch.setattr(telegram, "post_result", post)
  msg = _dm("/trade_uncclose XAU #1")

  await telegram.handle_trade_uncclose(msg)

  execute.assert_awaited_once_with({
    "sid": 3,
    "symbol": "XAU",
    "chat_id": telegram.channel_for_symbol("XAU"),
    "reply_to": None,
  })
  post.assert_awaited_once_with(execute.return_value, "XAU")
  msg.answer.assert_awaited_once_with(
    "♻️ #1 restored — trade still running"
  )


@pytest.mark.asyncio
async def test_delete_command_resolves_any_state(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(
    telegram,
    "_resolve_any_sid",
    AsyncMock(return_value=7),
  )
  execute = AsyncMock(return_value={
    "action": "delete",
    "ok": True,
    "row": {"id": 7, "daily_seq": 2},
    "seq": 2,
  })
  post = AsyncMock(return_value="🗑 #2 deleted")
  monkeypatch.setattr(telegram, "do_delete", execute)
  monkeypatch.setattr(telegram, "post_result", post)
  msg = _dm("/trade_delete XAU #2")

  await telegram.handle_trade_delete(msg)

  execute.assert_awaited_once_with({
    "sid": 7,
    "symbol": "XAU",
    "chat_id": telegram.channel_for_symbol("XAU"),
    "reply_to": None,
  })
  post.assert_awaited_once_with(execute.return_value, "XAU")
  msg.answer.assert_awaited_once_with("🗑 #2 deleted")


@pytest.mark.asyncio
async def test_unknown_channel_is_ignored(monkeypatch):
  execute = AsyncMock()
  monkeypatch.setattr(telegram, "do_close", execute)

  await telegram.handle_channel_close(_channel("close #3 +80", -999))

  execute.assert_not_awaited()


def test_symbol_channel_and_pip_maps(monkeypatch):
  monkeypatch.setitem(
    SYMBOLS,
    "US30",
    {"pip": 1.0, "digits": 1},
  )
  monkeypatch.setattr(symbols, "CHANNELS", [
    {
      "symbol": "XAU", "tier": "vip",
      "channel_id": -100123456789,
    },
    {
      "symbol": "US30", "tier": "vip",
      "channel_id": -100987,
    },
  ])

  assert symbol_for_channel(-100123456789) == "XAU"
  assert symbol_for_channel(-100987) == "US30"
  assert symbol_for_channel(-999) is None
  assert pip_for("XAU") == 0.1
  assert pip_for("US30") == 1.0


@pytest.mark.asyncio
async def test_per_symbol_sequence_and_resolver(tmp_path, monkeypatch):
  await dedup.init_db()
  xau = await dedup.store_manual_signal(
    1, "BUY", 2000, 2001, 1990, [2010], symbol="XAU",
  )
  us30 = await dedup.store_manual_signal(
    2, "BUY", 40000, 40001, 39990, [40010], symbol="US30",
  )

  assert xau["daily_seq"] == 1
  assert us30["daily_seq"] == 1
  assert await telegram._resolve_sid(1, None, "XAU") == xau["id"]
  assert await telegram._resolve_sid(1, None, "US30") == us30["id"]


def test_review_uses_symbol_pip_size(monkeypatch):
  monkeypatch.setitem(
    SYMBOLS,
    "US30",
    {"pip": 1.0, "digits": 1, "channel_id": -100987},
  )
  base = {
    "id": 1,
    "daily_seq": 1,
    "action": "BUY",
    "entry": 100.0,
    "entry_end": 100.0,
    "sl": 90.0,
    "tps": [120.0],
    "status": "closed",
    "result_pips": 100,
    "legs": [],
  }

  assert "realized ~1.0R" in format_review([{**base, "symbol": "XAU"}])
  assert "realized ~10.0R" in format_review([
    {**base, "symbol": "US30"},
  ])


@pytest.mark.asyncio
async def test_trade_stats_symbol_filter_and_all(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  monkeypatch.setitem(
    SYMBOLS,
    "US30",
    {"pip": 1.0, "digits": 1, "channel_id": -100987},
  )
  records = AsyncMock(return_value=[])
  signals = AsyncMock(return_value=[])
  monkeypatch.setattr(telegram, "get_pips_records", records)
  monkeypatch.setattr(telegram, "get_all_signals", signals)

  await telegram.handle_trade_stats(_dm("/trade_stats US30 week"))
  assert records.await_args.args[2] == "US30"
  assert signals.await_args.args == ("US30",)

  await telegram.handle_trade_stats(_dm("/trade_stats week"))
  assert records.await_args.args[2] is None
  assert signals.await_args.args == (None,)


@pytest.mark.asyncio
async def test_aggregate_outputs_stay_in_owner_dm(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  channel_target = AsyncMock()
  monkeypatch.setattr(telegram, "post_result", channel_target)
  monkeypatch.setattr(
    telegram,
    "get_pips_summary",
    AsyncMock(return_value={
      "total": 1,
      "wins": 1,
      "losses": 0,
      "win_pips": 70,
      "loss_pips": 0,
      "net": 70,
    }),
  )
  monkeypatch.setattr(
    telegram,
    "get_pips_records",
    AsyncMock(return_value=[]),
  )
  monkeypatch.setattr(
    telegram,
    "get_all_signals",
    AsyncMock(return_value=[]),
  )
  monkeypatch.setattr(
    telegram,
    "_resolve_any_sid",
    AsyncMock(return_value=1),
  )
  monkeypatch.setattr(
    telegram,
    "get_signal_cluster",
    AsyncMock(return_value=[{
      "id": 1,
      "daily_seq": 1,
      "symbol": "XAU",
      "action": "BUY",
      "entry": 2000.0,
      "entry_end": 2000.0,
      "sl": 1990.0,
      "tps": [2010.0],
      "status": "closed",
      "result_pips": 70,
      "legs": [],
    }]),
  )

  messages = [
    _dm("/trade_pips today"),
    _dm("/trade_stats today"),
    _dm("/trade_review #1"),
  ]
  await telegram.handle_trade_pips(messages[0])
  await telegram.handle_trade_stats(messages[1])
  await telegram.handle_trade_review(messages[2])

  assert all(msg.answer.await_count for msg in messages)
  channel_target.assert_not_awaited()


@pytest.mark.asyncio
async def test_help_is_owner_only_and_documents_both_surfaces(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  owner = _dm("/help")
  stranger = _dm("/help", user_id=99)

  await telegram.handle_help(owner)
  await telegram.handle_help(stranger)

  text = owner.answer.await_args.args[0]
  assert "Channel replies" in text
  assert "close #id ±pips [%] | be" in text
  assert "/trade_close [SYMBOL]" in text
  assert "/trade_uncclose [SYMBOL]" in text
  assert "/trade_tp [SYMBOL]" in text
  stranger.answer.assert_not_awaited()

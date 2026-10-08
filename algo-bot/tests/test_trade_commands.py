import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app.core.config import runtime_config
from tests.support.canonical_fixtures import install_runtime_overrides, leaf
from app.persistence import store
from app.core import symbols
from app.bot import wiring
from app.bot.handlers import scanner_dm
from app.signals.reports import format_review
from app.core.symbols import SYMBOLS, pip_for, symbol_for_channel


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
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})

  await wiring.setup_commands(target)

  first, second = target.set_my_commands.await_args_list
  assert first.args[0] == []
  assert first.kwargs["scope"].type == "default"
  assert second.args[0] == wiring.OWNER_COMMANDS
  assert second.kwargs["scope"].chat_id == 42
  assert {command.command for command in wiring.OWNER_COMMANDS} == {
    "trade",
    "trade_active", "trade_close", "trade_close_auto", "trade_uncclose", "trade_tp",
    "trade_sl", "trade_cancel", "trade_delete",
    "trade_modify",
    "trade_reopen", "trade_tag", "trade_untagged", "trade_note", "trade_review",
    "trade_map", "algo_status", "algo_pause", "algo_resume", "algo_close_all",
    "trade_stats", "trade_pips", "help",
  } | {"trade_open"}


@pytest.mark.asyncio
async def test_signal_bot_exposes_public_start_and_owner_trade_map(monkeypatch):
  target = SimpleNamespace(set_my_commands=AsyncMock())
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})

  await wiring.setup_scanner_commands(target)

  first, second = target.set_my_commands.await_args_list
  assert first.args[0] == wiring.SCANNER_PUBLIC_COMMANDS
  assert second.args[0] == wiring.SCANNER_OWNER_COMMANDS
  assert second.kwargs["scope"].chat_id == 42
  assert [command.command for command in wiring.SCANNER_OWNER_COMMANDS] == [
    "start",
    "trade_map",
    "algo_status",
    "algo_pause",
    "algo_resume",
    "algo_close_all",
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  msg = _dm("/start", user_id=999)

  await wiring.handle_start(msg)

  out = msg.answer.await_args.args[0]
  assert "👋 <b>Welcome to Apex Void Trading</b>" in out
  assert "📢 <b>Public channel</b>" in out
  assert "📚 <b>Trading Knowledge Base</b>" in out
  assert "✨ Follow the channel" in out
  assert "@apexvoidtrading" in out
  assert "https://t.me/apexvoidtrading" in out
  assert "trading.apexvoid.net" in out


@pytest.mark.asyncio
async def test_trade_open_lists_open_signals(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  monkeypatch.setattr(
    wiring,
    "get_open_signals",
    AsyncMock(return_value=[{
      "id": 9, "daily_seq": 6, "symbol": "XAU", "action": "BUY",
      "entry": 4100.0, "entry_end": 4105.0, "sl": 4088.0,
      "fill_state": "filled", "legs": [{"frac": 0.5, "pips": 90}],
    }]),
  )
  msg = _dm("/trade_open")

  await wiring.handle_trade_open(msg)

  out = msg.answer.await_args.args[0]
  assert "#6 XAU BUY 4100–4105" in out
  assert "SL 4088" in out and "filled" in out and "50% open" in out


@pytest.mark.asyncio
async def test_trade_lists_runtime_symbols_and_manual_modes(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  from app.bot.handlers import dm as dm_handlers
  from tests.support.canonical_fixtures import _load_production_example

  monkeypatch.setattr(dm_handlers, "runtime_config", _load_production_example().config)
  msg = _dm("/trade")

  await wiring.handle_trade(msg)

  out = msg.answer.await_args.args[0]
  assert "ApexVoid trade symbols" in out
  assert "<b>XAU</b>" in out
  assert "<b>EURUSD</b>" in out
  assert "/trade XAU buy" in out


@pytest.mark.asyncio
async def test_trade_alias_submits_through_shared_manual_flow(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  from app.bot.handlers import fallback

  submit = AsyncMock(return_value=True)
  monkeypatch.setattr(fallback, "submit_manual_signal", submit)
  msg = _dm("/trade xauusd buy 4473-4470 / sl 4467 / algo")

  await wiring.handle_trade(msg)

  submit.assert_awaited_once_with(
    msg,
    "XAU buy 4473-4470 / sl 4467 / algo",
  )


@pytest.mark.asyncio
async def test_trade_open_empty(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  monkeypatch.setattr(wiring, "get_open_signals", AsyncMock(return_value=[]))
  msg = _dm("/trade_open")

  await wiring.handle_trade_open(msg)

  assert "No open signals" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_unknown_channel_is_ignored(monkeypatch):
  execute = AsyncMock()
  monkeypatch.setattr(wiring, "do_close", execute)

  await wiring.handle_channel_close(_channel("close #3 +80", -999))

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
  await store.init_db()
  xau = await store.store_manual_signal(
    1, "BUY", 2000, 2001, 1990, [2010], symbol="XAU",
  )
  us30 = await store.store_manual_signal(
    2, "BUY", 40000, 40001, 39990, [40010], symbol="US30",
  )

  assert xau["daily_seq"] == 1
  assert us30["daily_seq"] == 1
  assert await wiring._resolve_sid(1, None, "XAU") == xau["id"]
  assert await wiring._resolve_sid(1, None, "US30") == us30["id"]


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
async def test_aggregate_outputs_stay_in_owner_dm(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  channel_target = AsyncMock()
  monkeypatch.setattr(wiring, "post_result", channel_target)
  monkeypatch.setattr(
    wiring,
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
    wiring,
    "get_pips_records",
    AsyncMock(return_value=[]),
  )
  monkeypatch.setattr(
    wiring,
    "get_all_signals",
    AsyncMock(return_value=[]),
  )
  monkeypatch.setattr(
    wiring,
    "_resolve_any_sid",
    AsyncMock(return_value=1),
  )
  monkeypatch.setattr(
    wiring,
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
  await wiring.handle_trade_pips(messages[0])
  await wiring.handle_trade_stats(messages[1])
  await wiring.handle_trade_review(messages[2])

  assert all(msg.answer.await_count for msg in messages)
  channel_target.assert_not_awaited()


@pytest.mark.asyncio
async def test_help_is_owner_only_and_documents_both_surfaces(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  owner = _dm("/help")
  stranger = _dm("/help", user_id=99)

  await wiring.handle_help(owner)
  await wiring.handle_help(stranger)

  text = owner.answer.await_args.args[0]
  assert "Channel replies" in text
  assert "close #id ±pips [%] | be" in text
  assert "/trade_close [SYMBOL]" in text
  assert "/trade_uncclose [SYMBOL]" in text
  assert "/trade_tp [SYMBOL]" in text
  assert "/algo_close_all confirm" in text
  assert "/algo_status" in text
  stranger.answer.assert_not_awaited()

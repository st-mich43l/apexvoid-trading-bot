"""Shared VIP channel must manage FX and XAU from the replied signal."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.handlers import channel as channel_handlers
from app.scalping.context import _scalping_symbols, is_scalping_symbol
from app.signals.parsing import _take_symbol
from tests.test_config_effective_instrument_context import _load_production_example


pytestmark = pytest.mark.no_database


def test_take_symbol_canonicalizes_aliases(monkeypatch):
  cfg = _load_production_example().config
  monkeypatch.setattr("app.core.symbols.runtime_config", cfg)
  monkeypatch.setattr("app.signals.parsing.runtime_config", cfg)
  assert _take_symbol("xauusd") == ("XAU", "")
  assert _take_symbol("EURUSD #2") == ("EURUSD", "#2")
  assert _take_symbol("gbpusd open") == ("GBPUSD", "open")


def test_scalp_excludes_fx_fixed_rr_live_books():
  cfg = _load_production_example().config
  allowed = _scalping_symbols(cfg)
  assert "XAU" in allowed
  for symbol in ("EURUSD", "GBPJPY", "GBPUSD", "USDJPY"):
    if symbol in cfg.live_instruments():
      assert symbol not in allowed
      assert not is_scalping_symbol(symbol, cfg)
  assert is_scalping_symbol("XAU", cfg)


@pytest.mark.asyncio
async def test_channel_symbol_uses_replied_signal_book(monkeypatch):
  monkeypatch.setattr(
    channel_handlers,
    "tier_for_channel",
    lambda _chat_id: "vip",
  )
  monkeypatch.setattr(
    channel_handlers,
    "get_signal_by_post",
    AsyncMock(return_value={
      "id": 103,
      "symbol": "USDJPY",
      "daily_seq": 2,
      "status": "open",
    }),
  )
  msg = SimpleNamespace(
    chat=SimpleNamespace(id=-1001),
    reply_to_message=SimpleNamespace(message_id=55),
  )
  assert await channel_handlers._channel_symbol(msg) == "USDJPY"


@pytest.mark.asyncio
async def test_channel_close_passes_fx_symbol_to_do_close(monkeypatch):
  monkeypatch.setattr(
    channel_handlers,
    "_channel_symbol",
    AsyncMock(return_value="USDJPY"),
  )
  monkeypatch.setattr(
    channel_handlers,
    "_resolve_sid",
    AsyncMock(return_value=103),
  )
  do_close = AsyncMock(return_value={"ok": True, "pending": False})
  monkeypatch.setattr(channel_handlers, "do_close", do_close)
  monkeypatch.setattr(channel_handlers, "post_result", AsyncMock())
  monkeypatch.setattr(channel_handlers, "_delete_command", AsyncMock())
  msg = SimpleNamespace(
    text="close #2 +20",
    chat=SimpleNamespace(id=-1001),
    reply_to_message=SimpleNamespace(message_id=55),
    message_id=99,
  )
  await channel_handlers.handle_channel_close(msg)
  assert do_close.await_args.args[0]["symbol"] == "USDJPY"
  assert do_close.await_args.args[0]["sid"] == 103

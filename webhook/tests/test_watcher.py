import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app import watcher


@pytest.mark.asyncio
async def test_watcher_skips_feed_without_filled_signals(monkeypatch):
  get_open = AsyncMock(return_value=[
    {"id": 1, "fill_state": "pending"},
  ])
  get_price = AsyncMock()
  monkeypatch.setattr(watcher, "get_open_signals", get_open)
  monkeypatch.setattr(watcher, "get_xau_price", get_price)

  await watcher._watcher_tick(object())

  get_price.assert_not_awaited()
  assert not hasattr(watcher, "store_pips")
  assert not hasattr(watcher, "close_manual_signal")


@pytest.mark.asyncio
async def test_watcher_alert_is_notify_only_and_deduplicated(monkeypatch):
  watcher._alerts.clear()
  sig = {
    "id": 3,
    "daily_seq": 2,
    "channel_message_id": 77,
    "fill_state": "filled",
    "action": "BUY",
    "entry": 2000.0,
    "entry_end": 2002.0,
    "sl": 1990.0,
    "tps": [2010.0],
  }
  monkeypatch.setattr(
    watcher,
    "get_open_signals",
    AsyncMock(return_value=[sig]),
  )
  monkeypatch.setattr(watcher, "_market_open", lambda: True)
  monkeypatch.setattr(
    watcher,
    "get_xau_price",
    AsyncMock(return_value=2010.0),
  )
  fanout = AsyncMock()
  monkeypatch.setattr(watcher, "fanout_update", fanout)

  await watcher._watcher_tick(object())
  await watcher._watcher_tick(object())

  fanout.assert_awaited_once()
  _, render = fanout.await_args.args
  assert render("vip") == (
    "🎯 <b>TP HIT</b> | #2\n"
    "💰 Level: <b>TP1</b>\n"
    "📈 Price: <b>2010</b>\n"
    "✅ Profit: <b>+90 pips</b> 💸\n\n"
    "<i>Reply to confirm:</i> <code>close #2 +90</code>"
  )
  assert render("public") == "🎯 TP1 +90 pips 💸"


def test_public_watcher_alert_hides_pips_when_disabled(monkeypatch):
  monkeypatch.setattr(watcher.settings, "public_show_pips", False)

  tp = watcher._render_level_alert(
    "public", "TP", "TP1", 2, "2010", 90
  )
  sl = watcher._render_level_alert(
    "public", "SL", "SL", 2, "1990", 110
  )

  assert tp == "🎯 TP hit"
  assert sl == "🛡 SL hit"
  assert not any(char.isdigit() for char in tp + sl)

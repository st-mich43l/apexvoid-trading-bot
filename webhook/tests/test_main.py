import asyncio
import logging
import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app import main


@pytest.mark.asyncio
async def test_startup_warns_when_owner_id_is_unset(monkeypatch, caplog):
  monkeypatch.setattr(main.settings, "telegram_owner_id", None)
  monkeypatch.setattr(main.settings, "scanner_telegram_bot_token", "scanner-token")
  monkeypatch.setattr(main.settings, "telegram_bot_token", "general-token")
  init_db = AsyncMock()
  watcher = AsyncMock()
  calendar = AsyncMock()
  weekly = AsyncMock()
  scanner = AsyncMock()
  market_map = AsyncMock()
  commands = AsyncMock()
  scanner_commands = AsyncMock()
  polling = AsyncMock()
  scanner_polling = AsyncMock()
  scanner_close = AsyncMock()
  monkeypatch.setattr(main, "init_db", init_db)
  monkeypatch.setattr(main, "watcher_loop", watcher)
  monkeypatch.setattr(main, "calendar_sync_loop", calendar)
  monkeypatch.setattr(main, "weekly_report_loop", weekly)
  monkeypatch.setattr(main, "scanner_loop", scanner)
  monkeypatch.setattr(main, "market_map_scan_loop", market_map)
  monkeypatch.setattr(main, "setup_commands", commands)
  monkeypatch.setattr(main, "setup_scanner_commands", scanner_commands)
  monkeypatch.setattr(main.dp, "start_polling", polling)
  monkeypatch.setattr(main.scanner_dp, "start_polling", scanner_polling)
  monkeypatch.setattr(main.scanner_bot.session, "close", scanner_close)
  caplog.set_level(logging.WARNING)

  await main.main()
  await asyncio.sleep(0)

  assert "owner-only DM commands are DISABLED" in caplog.text
  init_db.assert_awaited_once()
  polling.assert_awaited_once()
  watcher.assert_awaited_once()
  calendar.assert_awaited_once()
  weekly.assert_awaited_once()
  scanner.assert_awaited_once()
  market_map.assert_awaited_once()
  commands.assert_awaited_once_with(main.bot)
  scanner_commands.assert_awaited_once_with(main.scanner_bot)
  scanner_polling.assert_called_once_with(
    main.scanner_bot,
    allowed_updates=["message"],
    handle_signals=False,
    close_bot_session=False,
  )
  scanner_close.assert_awaited_once()

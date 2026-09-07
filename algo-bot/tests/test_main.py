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

from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf
from app import main


pytestmark = pytest.mark.no_database


@pytest.mark.asyncio
async def test_startup_warns_when_owner_id_is_unset(monkeypatch, caplog):
  install_runtime_overrides(monkeypatch, legacy_overrides={
    "telegram_owner_id": None,
    "scanner_telegram_bot_token": "scanner-token",
    "telegram_bot_token": "general-token",
  })
  init_db = AsyncMock()
  watcher = AsyncMock()
  calendar = AsyncMock()
  weekly = AsyncMock()
  scanner = AsyncMock()
  stats_backfill = AsyncMock(return_value="0-0")
  stats_ingestion = AsyncMock()
  commands = AsyncMock()
  scanner_commands = AsyncMock()
  polling = AsyncMock()
  scanner_polling = AsyncMock()
  scanner_close = AsyncMock()
  monkeypatch.setattr(main, "init_db", init_db)
  monkeypatch.setattr(main, "watcher_loop", watcher)
  monkeypatch.setattr(main, "calendar_sync_loop", calendar)
  monkeypatch.setattr(main, "weekly_report_loop", weekly)
  monkeypatch.setattr(main, "bar_event_dispatcher_loop", scanner)
  monkeypatch.setattr(
    main, "backfill_retained_auto_trade_stats", stats_backfill,
  )
  monkeypatch.setattr(
    main, "auto_trade_stats_ingestion_loop", stats_ingestion,
  )
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
  stats_backfill.assert_awaited_once()
  stats_ingestion.assert_awaited_once()
  commands.assert_awaited_once_with(main.bot)
  scanner_commands.assert_awaited_once_with(main.scanner_bot)
  scanner_polling.assert_called_once_with(
    main.scanner_bot,
    allowed_updates=["message"],
    handle_signals=False,
    close_bot_session=False,
  )
  scanner_close.assert_awaited_once()

import asyncio
import logging

from app.config import settings
from app.telegram import (
  bot,
  dp,
  scanner_bot,
  scanner_dp,
  setup_commands,
  setup_scanner_commands,
)
from app.dedup import init_db, close_pool
from app.watcher import watcher_loop
from app.calendar import calendar_sync_loop
from app.weekly_report import weekly_report_loop
from app.scanner import scanner_loop
from app.market_map_delivery import market_map_scan_loop
from app.auto_trade_ops import auto_trade_events_loop
from app import redis_state

logging.basicConfig(
  level=settings.log_level,
  format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")


async def main() -> None:
  await init_db()
  await setup_commands(bot)
  scanner_polling = None
  if (
    settings.scanner_telegram_bot_token
    and settings.scanner_telegram_bot_token != settings.telegram_bot_token
  ):
    await setup_scanner_commands(scanner_bot)
    scanner_polling = asyncio.create_task(scanner_dp.start_polling(
      scanner_bot,
      allowed_updates=["message"],
      handle_signals=False,
      close_bot_session=False,
    ))
  asyncio.create_task(watcher_loop())
  asyncio.create_task(calendar_sync_loop())
  asyncio.create_task(weekly_report_loop())
  asyncio.create_task(scanner_loop())
  asyncio.create_task(market_map_scan_loop())
  asyncio.create_task(auto_trade_events_loop())
  log.info("DB ready (PostgreSQL)")
  if not settings.telegram_owner_id:
    log.warning(
      "TELEGRAM_OWNER_ID not set — owner-only DM commands are DISABLED. "
      "Set it to enable the DM interface."
    )
  log.info("Starting Telegram polling")
  # Long-polling is outbound-only — no inbound webhook server is required.
  # start_polling installs its own SIGINT/SIGTERM handlers and closes the
  # bot session on shutdown.
  try:
    # callback_query is required for the inline Close buttons on TP alerts;
    # without it Telegram never delivers button presses.
    await dp.start_polling(
      bot,
      allowed_updates=["channel_post", "message", "callback_query"],
    )
  finally:
    if scanner_polling is not None:
      scanner_polling.cancel()
      await asyncio.gather(scanner_polling, return_exceptions=True)
    await scanner_bot.session.close()
    await redis_state.close_client()
    await close_pool()


if __name__ == "__main__":
  asyncio.run(main())

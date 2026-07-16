import asyncio
import logging

from app.config import settings
from app.telegram import bot, dp, setup_commands
from app.dedup import init_db, close_pool
from app.watcher import watcher_loop
from app.calendar import calendar_sync_loop
from app.weekly_report import weekly_report_loop
from app.scanner import scanner_loop
from app.market_map_delivery import market_map_session_loop
from app import redis_state

logging.basicConfig(
  level=settings.log_level,
  format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")


async def main() -> None:
  await init_db()
  await setup_commands(bot)
  asyncio.create_task(watcher_loop())
  asyncio.create_task(calendar_sync_loop())
  asyncio.create_task(weekly_report_loop())
  asyncio.create_task(scanner_loop())
  asyncio.create_task(market_map_session_loop())
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
    await redis_state.close_client()
    await close_pool()


if __name__ == "__main__":
  asyncio.run(main())

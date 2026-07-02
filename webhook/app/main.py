import asyncio
import logging

from app.config import settings
from app.telegram import bot, dp
from app.dedup import init_db

logging.basicConfig(
  level=settings.log_level,
  format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")


async def main() -> None:
  await init_db()
  log.info("DB ready at %s", settings.db_path)
  log.info("Starting Telegram polling")
  # Long-polling is outbound-only — no inbound webhook server is required.
  # start_polling installs its own SIGINT/SIGTERM handlers and closes the
  # bot session on shutdown.
  await dp.start_polling(bot, allowed_updates=["channel_post", "message"])


if __name__ == "__main__":
  asyncio.run(main())

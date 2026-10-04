import asyncio
import logging

from app.core.config import runtime_config
from app.core.logging_setup import configure_logging
from app.bot.wiring import (
  bot,
  dp,
  scanner_bot,
  scanner_dp,
  setup_commands,
  setup_scanner_commands,
)
from app.persistence.store import init_db, close_pool
from app.analysis_client.startup_gate import require_live_go_consumer
from app.signals.watcher import watcher_loop
from app.signals.calendar import calendar_sync_loop
from app.signals.weekly_report import weekly_report_loop
from app.bot.owner_dm_journal import owner_dm_daily_wipe_loop
from app.autotrade.bar_event_dispatcher import bar_event_dispatcher_loop
from app.autotrade.delivery import auto_trade_events_loop
from app.autotrade.stats_ingestion import (
  auto_trade_stats_ingestion_loop,
  backfill_retained_auto_trade_stats,
)
from app.autotrade.setup_expiry_sweeper import setup_expiry_sweeper_loop
from app.autotrade.startup_reconciliation import reconcile_startup_state
from app.autotrade.worker import configure_forming_card_edit_fn
from app.bot.client import edit_scanner_message_text
from app.bot.telegram_actor import start_telegram_actor
from app.signals.manual_execution import bridge_intents_loop, reconcile_events_loop
from app.persistence import redis_state

_log_info = configure_logging(
  level=runtime_config.runtime.logging.level,
  log_dir=runtime_config.runtime.logging.directory,
  retention_days=runtime_config.runtime.logging.retention_days,
  enable_file=runtime_config.runtime.logging.file_enabled,
)
log = logging.getLogger("bot")
log.info(
  "configuration_loaded environment=%s source=%s",
  runtime_config.runtime.environment,
  runtime_config.runtime.environment,
)
if _log_info.get("file"):
  log.info(
    "file logging enabled path=%s retention_days=%s",
    _log_info["file"],
    _log_info["retention_days"],
  )


def _spawn_supervised(name: str, factory) -> asyncio.Task:
  """Background Redis consumers must survive Compose recreate DNS blips."""
  return asyncio.create_task(
    redis_state.run_supervised(name, factory),
    name=name,
  )


async def main() -> None:
  # Go is the sole automatic technical-opportunity producer. Refuse to start
  # automatic trading without the live Go/Kafka path,
  # before anything touches Redis, PostgreSQL or Telegram. Manual-only
  # deployments (auto_trade disabled) are not gated.
  require_live_go_consumer(runtime_config)
  # Composition-root Telegram edit callback — worker never imports bot.client.
  configure_forming_card_edit_fn(edit_scanner_message_text)
  await init_db()
  # Compose can report Redis healthy then briefly drop DNS while recreating the
  # container; wait for a real PING before anything else touches the client.
  await redis_state.wait_until_ready()
  log.info(
    "AUTO-TRADE CONFIG service=algo-bot profile=%s enabled=%s dry_run=%s "
    "candidate_stream=%s event_stream=%s",
    runtime_config.runtime.environment,
    runtime_config.auto_algo.enabled,
    runtime_config.auto_algo.dry_run,
    runtime_config.runtime.redis_streams.candidates,
    runtime_config.runtime.redis_streams.events,
  )
  if runtime_config.auto_algo.enabled:
    # Repair pre-existing orphaned/stale state before any background task
    # starts reading it, so nothing races startup reconciliation.
    await reconcile_startup_state(redis_state.get_client())
  # Accounting has its own cursor and must be ready before Telegram accepts
  # /trade_stats. Startup also catches up retained events after the durable
  # cursor, including when autonomous execution is currently switched off.
  await backfill_retained_auto_trade_stats(redis_state.get_client())
  await setup_commands(bot)
  start_telegram_actor()
  scanner_polling = None
  if (
    runtime_config.telegram.scanner_telegram_bot_token
    and runtime_config.telegram.scanner_telegram_bot_token
    != runtime_config.telegram.bot_token
  ):
    await setup_scanner_commands(scanner_bot)
    scanner_polling = asyncio.create_task(scanner_dp.start_polling(
      scanner_bot,
      allowed_updates=["message"],
      handle_signals=False,
      close_bot_session=False,
    ))
  _spawn_supervised("watcher_loop", watcher_loop)
  _spawn_supervised("calendar_sync_loop", calendar_sync_loop)
  _spawn_supervised("weekly_report_loop", weekly_report_loop)
  _spawn_supervised("owner_dm_daily_wipe_loop", owner_dm_daily_wipe_loop)
  _spawn_supervised("bar_event_dispatcher_loop", bar_event_dispatcher_loop)
  # ZoneWatch execution is retired from the automatic path. The durable Go
  # opportunity consumer and the bar worker are the only automatic setup path.
  _spawn_supervised("setup_expiry_sweeper_loop", setup_expiry_sweeper_loop)
  # market_map_scan_loop removed from production startup 2026-09
  # (owner-directed Market Map purge): the periodic owner digest push is
  # retired along with Market Map's role as a trading-decision input.
  _spawn_supervised("auto_trade_events_loop", auto_trade_events_loop)
  _spawn_supervised("auto_trade_stats_ingestion_loop", auto_trade_stats_ingestion_loop)
  _spawn_supervised("bridge_intents_loop", bridge_intents_loop)
  _spawn_supervised("reconcile_events_loop", reconcile_events_loop)
  if runtime_config.auto_algo.enabled:
    from app.analysis_client.consumer import analysis_opportunity_consumer_loop
    _spawn_supervised("analysis_opportunity_consumer_loop", analysis_opportunity_consumer_loop)
  log.info("DB ready (PostgreSQL)")
  if not runtime_config.telegram.telegram_owner_id:
    log.warning(
      "TELEGRAM_OWNER_ID not set — owner-only DM commands are DISABLED. "
      "Set it to enable the DM interface."
    )
  log.info("Starting Telegram polling")
  try:
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

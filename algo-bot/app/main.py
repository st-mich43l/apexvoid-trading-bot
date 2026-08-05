import asyncio
import logging

from app.core.config import (
  active_configuration_startup_message,
  runtime_config,
)
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
from app.signals.watcher import watcher_loop
from app.signals.calendar import calendar_sync_loop
from app.signals.weekly_report import weekly_report_loop
from app.analysis.scanner import scanner_loop
from app.analysis.market_map_delivery import market_map_scan_loop
from app.autotrade.delivery import auto_trade_events_loop
from app.autotrade.stats_ingestion import (
  auto_trade_stats_ingestion_loop,
  backfill_retained_auto_trade_stats,
)
from app.autotrade.setup_expiry_sweeper import setup_expiry_sweeper_loop
from app.autotrade.startup_reconciliation import reconcile_startup_state
from app.autotrade.worker import auto_scalp_loop, configure_forming_card_edit_fn
from app.autotrade.zone_execution_cutover import (
  install_zone_execution_cutover,
  zone_watch_execution_loop,
)
from app.scalping.runtime import scalp_m1_event_loop
from app.bot.client import edit_scanner_message_text
from app.autotrade.zone_execution_runtime import uninstall_zone_execution_cutover
from app.autotrade.direct_publish_same_cycle import (
  install_same_cycle_publish_retry,
  uninstall_same_cycle_publish_retry,
)
from app.autotrade.config_health import (
  python_manifest,
  publish_python_manifest,
)
from app.configuration.runtime_manifest_boot import (
  verify_mounted_runtime_manifest_or_raise,
)
from app.signals.manual_execution import bridge_intents_loop, reconcile_events_loop
from app.persistence import redis_state

_log_info = configure_logging(
  level=runtime_config.bootstrap.logging.level,
  log_dir=runtime_config.bootstrap.logging.directory,
  retention_days=runtime_config.bootstrap.logging.retention_days,
  enable_file=runtime_config.bootstrap.logging.file_enabled,
)
log = logging.getLogger("bot")
log.info(active_configuration_startup_message())
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
  # Scanner and worker are already imported above. Install the retained-zone
  # cutover before any background task starts so every live scanner cycle uses
  # ZoneWatch and direct publication from its first event.
  install_zone_execution_cutover()
  install_same_cycle_publish_retry()
  # Composition-root Telegram edit callback — worker never imports bot.client.
  configure_forming_card_edit_fn(edit_scanner_message_text)
  verify_mounted_runtime_manifest_or_raise()
  await init_db()
  # Compose can report Redis healthy then briefly drop DNS while recreating the
  # container; wait for a real PING before anything else touches the client.
  await redis_state.wait_until_ready()
  config_health = await publish_python_manifest(redis_state.get_client())
  manifest = python_manifest()
  log.info(
    "AUTO-TRADE CONFIG service=algo-bot profile=%s enabled=%s "
    "dry_run=%s candidate_stream=%s event_stream=%s symbols=%s "
    "targets=%s range_targets=%s candidate_max_age=%s "
    "candidate_storage_ttl=%s range_flip=%s two_sided=%s concurrent=%s "
    "counter_bias=%s account_mode=%s contract_version=%s "
    "deprecated=%s sources=%s config_health=%s",
    manifest["profile"],
    manifest["auto_trade_enabled"],
    manifest["dry_run"],
    manifest["candidate_stream"],
    manifest["event_stream"],
    manifest["symbols"],
    manifest["target_plans"],
    manifest["range_target_plans"],
    manifest["candidate_execution_max_age_seconds"],
    manifest["candidate_storage_ttl_seconds"],
    manifest["range_flip"],
    manifest["two_sided_range"],
    manifest["concurrent_strategies"],
    manifest["allow_counter_bias"],
    manifest["account_mode"],
    manifest["candidate_contract_version"],
    manifest["deprecated_variables"],
    manifest["config_sources"],
    config_health["state"],
  )
  if runtime_config.runtime.auto_trade.enabled:
    # Repair pre-existing orphaned/stale state before any background task
    # starts reading it, so nothing races startup reconciliation.
    await reconcile_startup_state(redis_state.get_client())
  # Accounting has its own cursor and must be ready before Telegram accepts
  # /trade_stats. Startup also catches up retained events after the durable
  # cursor, including when autonomous execution is currently switched off.
  await backfill_retained_auto_trade_stats(redis_state.get_client())
  await setup_commands(bot)
  scanner_polling = None
  if (
    runtime_config.delivery.telegram.scanner_telegram_bot_token
    and runtime_config.delivery.telegram.scanner_telegram_bot_token
    != runtime_config.bootstrap.telegram.bot_token
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
  _spawn_supervised("scanner_loop", scanner_loop)
  _spawn_supervised("zone_watch_execution_loop", zone_watch_execution_loop)
  _spawn_supervised("auto_scalp_loop", auto_scalp_loop)
  # HFS M1 scalping is shadow/paper by default and never publishes broker
  # candidates. The loop no-ops immediately when mode=off.
  _spawn_supervised("scalp_m1_event_loop", scalp_m1_event_loop)
  # strategy_match_ready_loop removed from production startup: ZoneWatch →
  # direct TradePlan is authoritative. Legacy parsers remain for one release.
  _spawn_supervised("setup_expiry_sweeper_loop", setup_expiry_sweeper_loop)
  _spawn_supervised("market_map_scan_loop", market_map_scan_loop)
  _spawn_supervised("auto_trade_events_loop", auto_trade_events_loop)
  _spawn_supervised("auto_trade_stats_ingestion_loop", auto_trade_stats_ingestion_loop)
  _spawn_supervised("bridge_intents_loop", bridge_intents_loop)
  _spawn_supervised("reconcile_events_loop", reconcile_events_loop)
  log.info("DB ready (PostgreSQL)")
  if not runtime_config.delivery.telegram.telegram_owner_id:
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
    # Production normally exits the process here. Bounded app-lifecycle tests
    # continue in the same interpreter, so restore patched module globals.
    uninstall_same_cycle_publish_retry()
    uninstall_zone_execution_cutover()


if __name__ == "__main__":
  asyncio.run(main())

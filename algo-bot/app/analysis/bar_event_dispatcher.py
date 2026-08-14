"""One Redis ``bars:new`` subscriber for ZoneWatch M1, HFS, scanner, and worker.

Publish/activation handlers run first. Scanner detectors and the legacy worker
gate run after so a heavy analysis tick cannot delay an already-watched zone.
ZoneWatch still owns ``spots:new`` separately.
"""

from __future__ import annotations

import logging
from typing import Any

from app.analysis.ohlc_source import (
  RedisOHLCSource,
  prefetch_closed_bar_windows,
)
from app.core.config import runtime_config
from app.persistence import redis_state

log = logging.getLogger(__name__)


def parse_closed_bar(data: object) -> tuple[str, str, str] | None:
  text = data.decode() if isinstance(data, bytes) else str(data or "")
  parts = text.strip().split(":", 2)
  if len(parts) != 3:
    return None
  symbol, tf, event_ts = parts[0].upper(), parts[1].upper(), parts[2]
  if not symbol or not tf or not event_ts:
    return None
  return symbol, tf, event_ts


async def dispatch_closed_bar(
  data: object,
  *,
  client: Any,
  source: RedisOHLCSource,
) -> list[str]:
  """Run isolated handlers. Publish/activation first, analysis last.

  Existing ZoneWatches and HFS must not wait on scanner detectors. Scanner
  still runs before the worker because the worker reads this bar's matches.
  ZoneWatch, HFS, scanner, and worker share one OHLC window cache for this
  bar. ZoneWatch still runs first. M1 does not prefetch H1/M15; M5 warms
  the HTF windows scanner needs.
  """
  parsed = parse_closed_bar(data)
  if parsed is None:
    return []
  symbol, tf, event_ts = parsed
  ran: list[str] = []
  caching = hasattr(source, "begin_closed_bar_cache")
  if caching:
    source.begin_closed_bar_cache()

  async def _run(name: str, coro) -> None:
    try:
      await coro
      ran.append(name)
    except Exception:
      log.exception(
        "dispatcher %s tick failed symbol=%s tf=%s", name, symbol, tf,
      )

  try:
    if runtime_config.runtime.auto_trade.enabled and tf == "M1":
      from app.autotrade.zone_execution_cutover import evaluate_active_zone_watches

      await _run(
        "zone_watch",
        evaluate_active_zone_watches(
          client, symbol=symbol, event_ts=event_ts, source=source,
        ),
      )

    if tf != "M1":
      try:
        await prefetch_closed_bar_windows(
          source, symbol, closed_tf=tf,
        )
      except Exception:
        log.exception(
          "dispatcher OHLC prefetch failed symbol=%s tf=%s", symbol, tf,
        )

    from app.scalping.runtime import handle_closed_bar as hfs_handle

    await _run("hfs", hfs_handle(data, client=client, source=source))

    if runtime_config.runtime.scanner.enabled:
      from app.analysis.scanner import _handle_event as scanner_handle

      await _run("scanner", scanner_handle(data, source=source, client=client))

    if runtime_config.runtime.auto_trade.enabled:
      from app.autotrade.worker import _handle_event as worker_handle

      await _run("worker", worker_handle(data, source=source, client=client))
  finally:
    if caching:
      source.end_closed_bar_cache()

  return ran


async def bar_event_dispatcher_loop() -> None:
  client = redis_state.get_client()
  source = RedisOHLCSource(client)
  if runtime_config.runtime.auto_trade.enabled:
    try:
      from app.autotrade.worker import _reconcile_legacy_mapped_thesis_claims

      await _reconcile_legacy_mapped_thesis_claims(client)
    except Exception:
      log.exception("legacy mapped thesis claim reconcile failed")

  channel = str(
    getattr(runtime_config.market_data.ctrader_feed, "bars_channel", None)
    or "bars:new"
  )
  pubsub = client.pubsub()
  await pubsub.subscribe(channel)
  log.info("bar event dispatcher started channel=%s", channel)
  try:
    async for message in pubsub.listen():
      if message.get("type") != "message":
        continue
      try:
        await dispatch_closed_bar(
          message.get("data"), client=client, source=source,
        )
      except Exception:
        log.exception("bar event dispatch failed")
        try:
          from app.autotrade.lifecycle import increment_metric

          await increment_metric(client, "lifecycle_error")
        except Exception:
          log.exception("dispatcher lifecycle_error metric failed")
  finally:
    await pubsub.unsubscribe(channel)
    await pubsub.close()

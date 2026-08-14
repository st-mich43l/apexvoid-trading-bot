"""One Redis ``bars:new`` subscriber for scanner, worker, ZoneWatch M1, and HFS.

Each of those used to subscribe separately, so one closed bar was deserialized
and handled three or four times on the same event loop. ZoneWatch still owns
``spots:new`` (sub-second activation); this module owns closed bars only.
"""

from __future__ import annotations

import logging
from typing import Any

from app.analysis.ohlc_source import RedisOHLCSource
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
  """Run isolated handlers. Returns names that ran without raising."""
  parsed = parse_closed_bar(data)
  if parsed is None:
    return []
  symbol, tf, event_ts = parsed
  ran: list[str] = []

  if runtime_config.runtime.scanner.enabled:
    try:
      from app.analysis.scanner import _handle_event as scanner_handle

      await scanner_handle(data, source=source, client=client)
      ran.append("scanner")
    except Exception:
      log.exception("dispatcher scanner tick failed symbol=%s tf=%s", symbol, tf)

  if runtime_config.runtime.auto_trade.enabled:
    try:
      from app.autotrade.worker import _handle_event as worker_handle

      await worker_handle(data, source=source, client=client)
      ran.append("worker")
    except Exception:
      log.exception("dispatcher worker tick failed symbol=%s tf=%s", symbol, tf)
    if tf == "M1":
      try:
        from app.autotrade.zone_execution_cutover import evaluate_active_zone_watches

        await evaluate_active_zone_watches(
          client, symbol=symbol, event_ts=event_ts,
        )
        ran.append("zone_watch")
      except Exception:
        log.exception(
          "dispatcher zone-watch M1 failed symbol=%s event_ts=%s",
          symbol,
          event_ts,
        )

  try:
    from app.scalping.runtime import handle_closed_bar as hfs_handle

    await hfs_handle(data, client=client, source=source)
    ran.append("hfs")
  except Exception:
    log.exception("dispatcher hfs tick failed symbol=%s tf=%s", symbol, tf)

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

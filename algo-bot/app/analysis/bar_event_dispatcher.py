"""One Redis ``bars:new`` subscriber for ZoneWatch M1, scalping, scanner, and worker.

Publish/activation handlers run first. Scanner detectors and the legacy worker
gate run after so a heavy analysis tick cannot delay an already-watched zone.
ZoneWatch still owns ``spots:new`` separately.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.analysis.ohlc_source import (
  RedisOHLCSource,
  prefetch_closed_bar_windows,
)
from app.core.config import runtime_config
from app.persistence import redis_state

log = logging.getLogger(__name__)

_SYMBOL_QUEUE_MAXSIZE = 64
_SHUTDOWN_DRAIN_TIMEOUT_S = 2.0


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

  Existing ZoneWatches and M1 scalping must not wait on scanner detectors. Scanner
  still runs before the worker because the worker reads this bar's matches.
  ZoneWatch, scalping, scanner, and worker share one OHLC window cache for this
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

    from app.scalping.runtime import handle_closed_bar as scalp_handle

    await _run("scalp", scalp_handle(data, client=client, source=source))

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


class _PerSymbolBarDispatcher:
  """Keep per-symbol FIFO while allowing different symbols to make progress.

  A single subscriber previously awaited the complete ZoneWatch/scalping/scanner/
  worker chain before reading the next Pub/Sub message.  Five bars closing at
  the same instant therefore multiplied queue age by five.  Each worker owns
  its OHLC source/cache, so one symbol cannot clear another symbol's cache.
  """

  def __init__(self, client: Any):
    self._client = client
    self._queues: dict[str, asyncio.Queue[object]] = {}
    self._tasks: dict[str, asyncio.Task[None]] = {}
    self._closed = False

  async def submit(self, data: object) -> bool:
    parsed = parse_closed_bar(data)
    if parsed is None or self._closed:
      return False
    symbol = parsed[0]
    queue = self._queues.get(symbol)
    if queue is None:
      queue = asyncio.Queue(maxsize=_SYMBOL_QUEUE_MAXSIZE)
      self._queues[symbol] = queue
      self._tasks[symbol] = asyncio.create_task(
        self._run_symbol(symbol, queue),
        name=f"bar-dispatch:{symbol}",
      )
    await queue.put(data)
    return True

  async def _run_symbol(
    self,
    symbol: str,
    queue: asyncio.Queue[object],
  ) -> None:
    source = RedisOHLCSource(self._client)
    while True:
      data = await queue.get()
      try:
        await dispatch_closed_bar(
          data,
          client=self._client,
          source=source,
        )
      except asyncio.CancelledError:
        raise
      except Exception:
        log.exception("bar event dispatch failed symbol=%s", symbol)
        try:
          from app.autotrade.lifecycle import increment_metric

          await increment_metric(self._client, "lifecycle_error")
        except Exception:
          log.exception("dispatcher lifecycle_error metric failed")
      finally:
        queue.task_done()

  async def wait_idle(self) -> None:
    await asyncio.gather(*(queue.join() for queue in self._queues.values()))

  async def close(
    self,
    *,
    drain_timeout: float = _SHUTDOWN_DRAIN_TIMEOUT_S,
  ) -> None:
    self._closed = True
    tasks = list(self._tasks.values())
    if tasks and drain_timeout > 0:
      try:
        await asyncio.wait_for(
          self.wait_idle(),
          timeout=float(drain_timeout),
        )
      except TimeoutError:
        # Pub/Sub is non-durable and the old single dispatcher also lost its
        # in-flight event on process cancellation. Give normal fast handlers a
        # bounded grace period, then stop promptly instead of hanging deploys.
        pass
    for task in tasks:
      task.cancel()
    if tasks:
      await asyncio.gather(*tasks, return_exceptions=True)
    # Balance unfinished-task counters for items intentionally abandoned
    # after the bounded shutdown grace period. This keeps wait_idle/test and
    # embedding callers from hanging forever after close().
    for queue in self._queues.values():
      while True:
        try:
          queue.get_nowait()
        except asyncio.QueueEmpty:
          break
        else:
          queue.task_done()
    self._tasks.clear()
    self._queues.clear()


async def bar_event_dispatcher_loop() -> None:
  client = redis_state.get_client()
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
  dispatcher = _PerSymbolBarDispatcher(client)
  await pubsub.subscribe(channel)
  log.info("bar event dispatcher started channel=%s", channel)
  if runtime_config.runtime.scanner.enabled:
    log.info(
      "scanner structure mode causal=False (live confirmed-swing lookahead)",
    )
  try:
    async for message in pubsub.listen():
      if message.get("type") != "message":
        continue
      try:
        await dispatcher.submit(message.get("data"))
      except Exception:
        log.exception("bar event enqueue failed")
        try:
          from app.autotrade.lifecycle import increment_metric

          await increment_metric(client, "lifecycle_error")
        except Exception:
          log.exception("dispatcher lifecycle_error metric failed")
  finally:
    await dispatcher.close()
    await pubsub.unsubscribe(channel)
    await pubsub.close()

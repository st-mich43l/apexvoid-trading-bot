"""Closed-bar execution clock for Go-derived StrategyMatches.

The Go Analysis Engine owns all automatic technical opportunities and publishes
them through Kafka. This subscriber only wakes the existing Auto Algo worker
to evaluate already-consumed matches against current quotes and account policy.
It must never import, schedule or call Python scanner/scalping detectors.
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
  """Wake the execution worker for a closed bar; never produce technical setups."""
  parsed = parse_closed_bar(data)
  if parsed is None:
    return []
  symbol, tf, event_ts = parsed
  ran: list[str] = []
  caching = hasattr(source, "begin_closed_bar_cache")
  if caching:
    source.begin_closed_bar_cache()

  try:
    if not runtime_config.runtime.auto_trade.enabled:
      return ran
    if tf != "M1":
      try:
        await prefetch_closed_bar_windows(source, symbol, closed_tf=tf)
      except Exception:
        log.exception("execution-bar OHLC prefetch failed symbol=%s tf=%s", symbol, tf)
    # The worker consumes StrategyMatches written by the Go Kafka policy. It
    # retains execution-time price/risk gates but does not run an independent
    # technical scanner here. Technical computations inside the worker are
    # tracked for removal in the subsequent Go-facts extraction PR.
    from app.autotrade.worker import _handle_event as worker_handle
    try:
      await worker_handle(data, source=source, client=client)
      ran.append("worker")
    except Exception:
      log.exception("execution-bar worker failed symbol=%s tf=%s ts=%s", symbol, tf, event_ts)
  finally:
    if caching:
      source.end_closed_bar_cache()
  return ran


class _PerSymbolBarDispatcher:
  """Keep execution wake-ups FIFO per symbol and concurrent across symbols."""

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
  channel = str(
    getattr(runtime_config.market_data.ctrader_feed, "bars_channel", None)
    or "bars:new"
  )
  pubsub = client.pubsub()
  dispatcher = _PerSymbolBarDispatcher(client)
  await pubsub.subscribe(channel)
  log.info("bar event dispatcher started channel=%s", channel)
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

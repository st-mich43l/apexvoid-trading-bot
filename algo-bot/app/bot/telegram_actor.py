"""Serialize owner-chat Telegram I/O so fill edits beat price-track and scanner.

Several loops (delivery, forming price track, scanner posts) share one chat
and one flood budget. Without a single actor they stampede, hit RetryAfter,
and stall the event cursor. Jobs are a priority queue; droppable jobs
(Price-now) are discarded while flood-paused instead of competing with fills.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from aiogram.exceptions import TelegramRetryAfter

log = logging.getLogger(__name__)

PRIORITY_LIFECYCLE = 0
PRIORITY_MANAGE = 1
PRIORITY_CARD = 2
PRIORITY_PRICE = 3

T = TypeVar("T")

_queue: asyncio.PriorityQueue[tuple[int, int, int]] | None = None
_pending: dict[int, tuple[Callable[[], Awaitable[Any]], asyncio.Future[Any], bool]] = {}
_seq = 0
_worker: asyncio.Task[None] | None = None
_paused_until = 0.0
_started = False


def flood_paused() -> bool:
  return time.monotonic() < _paused_until


def note_flood(retry_after: float | int) -> None:
  global _paused_until
  wait = max(0.0, float(retry_after))
  _paused_until = time.monotonic() + min(wait, 30.0)


def start_telegram_actor() -> None:
  """Idempotent; no-op in tests that never call this (jobs run inline)."""
  global _started, _queue, _worker, _seq, _paused_until
  if _started:
    return
  _queue = asyncio.PriorityQueue()
  _pending.clear()
  _seq = 0
  _paused_until = 0.0
  _started = True
  _worker = asyncio.create_task(_run_worker(), name="telegram-actor")
  log.info("telegram actor started")


async def stop_telegram_actor() -> None:
  global _started, _queue, _worker
  task = _worker
  _worker = None
  _started = False
  _queue = None
  if task is not None:
    task.cancel()
    try:
      await task
    except asyncio.CancelledError:
      pass
  _pending.clear()


async def submit(
  fn: Callable[[], Awaitable[T]],
  *,
  priority: int = PRIORITY_MANAGE,
  droppable: bool = False,
) -> T | None:
  """Run ``fn`` on the actor, or inline when the actor is not started."""
  if not _started or _queue is None:
    return await fn()
  loop = asyncio.get_running_loop()
  fut: asyncio.Future[T | None] = loop.create_future()
  global _seq
  _seq += 1
  job_id = _seq
  _pending[job_id] = (fn, fut, droppable)
  await _queue.put((int(priority), job_id, job_id))
  return await fut


async def _run_worker() -> None:
  assert _queue is not None
  while True:
    _priority, _tie, job_id = await _queue.get()
    item = _pending.pop(job_id, None)
    if item is None:
      continue
    fn, fut, droppable = item
    if fut.cancelled():
      continue
    pause = _paused_until - time.monotonic()
    if pause > 0 and droppable:
      if not fut.done():
        fut.set_result(None)
      continue
    if pause > 0:
      await asyncio.sleep(pause)
    try:
      result = await fn()
    except TelegramRetryAfter as exc:
      note_flood(exc.retry_after)
      log.warning(
        "telegram actor flood-paused retry_after=%s droppable=%s",
        exc.retry_after,
        droppable,
      )
      if droppable:
        if not fut.done():
          fut.set_result(None)
        continue
      if not fut.done():
        fut.set_exception(exc)
      continue
    except Exception as exc:
      if not fut.done():
        fut.set_exception(exc)
      continue
    if not fut.done():
      fut.set_result(result)

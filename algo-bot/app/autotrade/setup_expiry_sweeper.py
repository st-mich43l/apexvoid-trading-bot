"""P0-2: durable, restart-safe expiry sweep.

Reads analysis:setup_expiry (a ZSET kept in sync by setup_lifecycle._save
on every write - see setup_lifecycle.py's _sync_expiry_index) and drives
any setup whose score (expires_at) has passed into EXPIRED, using
expire_setup for the state transition and finalize_terminal_setup for
cleanup. This never relies on Redis key TTL as the expiry signal, and it
never depends on a future bar, Telegram delivery, or a ready-stream event
arriving to notice the expiry - the whole point of P0-1/P0-2 is that a
setup nobody ever touches again still gets closed out.

Multi-worker-safe: every step here is either idempotent (expire_setup,
finalize_terminal_setup) or a plain ZREM, so two sweepers racing on the
same batch just do harmless duplicate work - there is no in-memory state
that would diverge between workers, and a restart loses nothing (the
index plus the canonical records are the only source of truth for what's
due).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.autotrade.lifecycle import increment_metric
from app.autotrade.setup_lifecycle import expire_setup, setup_expiry_index_key
from app.autotrade.terminal_cleanup import finalize_terminal_setup
from app.core.config import settings
from app.persistence import redis_state

log = logging.getLogger("bot")

_BATCH_SIZE = 200
_POLL_INTERVAL_SECONDS = 3


async def sweep_expired_setups_once(client: Any, *, now: int | None = None) -> int:
  """Process one bounded batch of due setups. Returns the count processed."""
  key = setup_expiry_index_key()
  now = now if now is not None else int(time.time())
  due = await client.zrangebyscore(key, "-inf", now, start=0, num=_BATCH_SIZE)
  if not due:
    return 0
  for raw in due:
    setup_id = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
      _record, outcome = await expire_setup(client, setup_id)
    except Exception:
      log.exception(
        "setup_expiry_sweeper: expire_setup failed setup_id=%s", setup_id,
      )
      continue

    if outcome == "missing":
      # A stale/orphan index member with no canonical record behind it
      # (legacy data, or a record that already fell out of retention
      # before this index existed). Nothing left to clean up but itself.
      await client.zrem(key, setup_id)
      await increment_metric(client, "setup_expiry_orphan_index_member")
      continue
    if outcome == "already_terminal":
      # Raced by another worker, or an inline caller (eg. worker.py
      # noticing match.expires_at during live evaluation) already closed
      # this out. The index member is stale either way.
      await client.zrem(key, setup_id)
      await increment_metric(client, "setup_expiry_already_terminal")
      continue
    if outcome == "publication_won":
      # P1-2: PLAN_BUILT/PLAN_PUBLISHED/ARMED must never be retroactively
      # expired here as "unexecuted" - stop tracking it and leave the
      # executor's own lifecycle to own what happens next.
      await client.zrem(key, setup_id)
      continue

    # outcome == "transitioned": expire_setup already persisted EXPIRED,
    # which re-synced the index (setup_lifecycle._save -> _sync_expiry_index
    # removes terminal records), so no explicit zrem is needed here.
    await increment_metric(client, "setup_expiry_transitioned")
    try:
      await finalize_terminal_setup(
        client, setup_id,
        terminal_state="expired",
        reason_code="setup_expired_before_execution",
        source="expiry_sweeper",
      )
    except Exception:
      log.exception(
        "setup_expiry_sweeper: finalize_terminal_setup failed setup_id=%s",
        setup_id,
      )
  return len(due)


async def setup_expiry_sweeper_loop() -> None:
  if not settings.auto_trade_enabled:
    return
  client = redis_state.get_client()
  log.info("ApexVoid Algo setup-expiry sweeper started")
  while True:
    try:
      processed = await sweep_expired_setups_once(client)
      if processed >= _BATCH_SIZE:
        # More may already be due - keep draining instead of waiting out
        # a full poll interval while a backlog exists.
        continue
    except Exception:
      log.exception("setup_expiry_sweeper: sweep iteration failed")
    await asyncio.sleep(_POLL_INTERVAL_SECONDS)

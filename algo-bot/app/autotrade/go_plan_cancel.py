"""Withdraw unexecuted Go-derived work when the reason to trade it is gone.

Kafka invalidation/expiry or an operator cancellation must not leave a Go-derived
plan alive somewhere between "match in Redis" and "order at the broker". Three
stages, each with a defined owner and outcome:

* match / setup (Python, Redis)      removed / cancelled here.
* queued plan (``published`` on the  a durable *cancel intent* is written; the
  ``execution:trade_plans`` stream,   executor honours it before it submits
  not yet submitted)                  anything, so no broker exposure is created.
* submitted-unfilled / partial       the intent makes the executor cancel every
                                     still-pending entry leg. Legs that already
                                     filled are *positions*: they keep their
                                     protective stop and normal TP/BE management.
* open position                      never closed by a cancellation or an
                                     invalidation; ownership governs plan
                                     *creation*, not management.

The intent is a tombstone, written even when no plan exists yet, so a plan that
is being published concurrently (or redelivered) is cancelled on arrival. Python
never edits executor state (``execution:plan_state:*``); the executor stays the
single writer of it and reports what it did in ``execution:plan_cancel_ack:*``.
"""

from __future__ import annotations

import json
from typing import Any

PLAN_CANCEL_TTL_SECONDS = 7 * 86400
# Shared with the executor: contracts/autotrade/plan-cancel-intent.json.
SOURCE_INVALIDATED = "opportunity_invalidated"
SOURCE_EXPIRED = "opportunity_expired"
SOURCE_OPERATOR_CANCEL = "operator_cancel"


def plan_cancel_key(plan_id: str) -> str:
  return f"execution:plan_cancel:{plan_id}"


def plan_id_for_match(match_id: str) -> str:
  """Same derivation as ``worker._v8_plan_id`` (pinned by a test)."""
  return f"v8:{match_id}"


def _text(raw: Any) -> str:
  return raw.decode() if isinstance(raw, bytes) else str(raw)


async def request_plan_cancel(
  client: Any, plan_id: str, *, reason: str, source: str, requested_at: int,
  opportunity_id: str | None = None,
) -> bool:
  """Write the cancel intent once. True if newly written, False if one already
  existed (first reason wins: the audit trail keeps the original cause)."""
  payload = json.dumps({
    "plan_id": plan_id, "reason": reason, "source": source, "requested_at": int(requested_at),
    "opportunity_id": opportunity_id,
  }, separators=(",", ":"), sort_keys=True)
  return bool(await client.set(plan_cancel_key(plan_id), payload, ex=PLAN_CANCEL_TTL_SECONDS, nx=True))


async def read_plan_cancel(client: Any, plan_id: str) -> dict[str, Any] | None:
  raw = await client.get(plan_cancel_key(plan_id))
  return None if raw is None else json.loads(_text(raw))

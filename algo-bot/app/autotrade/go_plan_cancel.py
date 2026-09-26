"""S14B: withdraw unexecuted Go-derived work when the reason to trade it is gone.

Kafka invalidation/expiry or an authority rollback must not leave a Go-derived
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
* open position                      never closed by an authority change or an
                                     invalidation; ownership governs plan
                                     *creation*, not management (S13B).

The intent is a tombstone, written even when no plan exists yet, so a plan that
is being published concurrently (or redelivered) is cancelled on arrival. Python
never edits executor state (``execution:plan_state:*``); the executor stays the
single writer of it and reports what it did in ``execution:plan_cancel_ack:*``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.analysis_client.authority import CATALOG_TAG, EPOCH_TAG, GO_ORIGIN_TAG
from app.autotrade.multi_match import deserialize_matches, serialize_matches, strategy_matches_key
from app.autotrade.setup_lifecycle import (
  CANCELLED,
  CONFIRMED,
  DISCOVERED,
  FORMING,
  INVALIDATED,
  PLAN_BUILT,
  TOUCHED,
  WATCHING,
  SetupLifecycleError,
  load_setup,
  transition_setup,
)

log = logging.getLogger(__name__)

PLAN_CANCEL_TTL_SECONDS = 7 * 86400
# Shared with the executor: contracts/autotrade/plan-cancel-intent.json.
SOURCE_INVALIDATED = "opportunity_invalidated"
SOURCE_EXPIRED = "opportunity_expired"
SOURCE_ROLLBACK = "authority_rollback"
GO_PLAN_REGISTRY_KEY = "analysis:go_plans"
# Setup states in which withdrawal is still Python's to do (nothing published
# yet). The lifecycle only allows pre-plan -> INVALIDATED and PLAN_BUILT ->
# CANCELLED; a PLAN_PUBLISHED/ARMED setup belongs to the executor's plan now.
_PRE_PLAN_STATES = frozenset({DISCOVERED, WATCHING, TOUCHED, FORMING, CONFIRMED})


def plan_cancel_key(plan_id: str) -> str:
  return f"execution:plan_cancel:{plan_id}"


def plan_cancel_ack_key(plan_id: str) -> str:
  return f"execution:plan_cancel_ack:{plan_id}"


def plan_id_for_match(match_id: str) -> str:
  """Same derivation as ``worker._v8_plan_id`` (pinned by a test)."""
  return f"v8:{match_id}"


def _text(raw: Any) -> str:
  return raw.decode() if isinstance(raw, bytes) else str(raw)


async def request_plan_cancel(
  client: Any, plan_id: str, *, reason: str, source: str, requested_at: int,
  opportunity_id: str | None = None, epoch: int | None = None,
) -> bool:
  """Write the cancel intent once. True if newly written, False if one already
  existed (first reason wins: the audit trail keeps the original cause)."""
  payload = json.dumps({
    "plan_id": plan_id, "reason": reason, "source": source, "requested_at": int(requested_at),
    "opportunity_id": opportunity_id, "epoch": epoch,
  }, separators=(",", ":"), sort_keys=True)
  return bool(await client.set(plan_cancel_key(plan_id), payload, ex=PLAN_CANCEL_TTL_SECONDS, nx=True))


async def read_plan_cancel(client: Any, plan_id: str) -> dict[str, Any] | None:
  raw = await client.get(plan_cancel_key(plan_id))
  return None if raw is None else json.loads(_text(raw))


async def read_plan_cancel_ack(client: Any, plan_id: str) -> dict[str, Any] | None:
  raw = await client.get(plan_cancel_ack_key(plan_id))
  return None if raw is None else json.loads(_text(raw))


def _go_scope(tags: Any) -> tuple[str | None, int | None]:
  scope = next((t[len(CATALOG_TAG):] for t in tags if t.startswith(CATALOG_TAG)), None)
  epoch = next((t[len(EPOCH_TAG):] for t in tags if t.startswith(EPOCH_TAG)), None)
  return scope, int(epoch) if epoch is not None and epoch.isdigit() else None


async def register_go_plan(client: Any, *, plan_id: str, match: Any, expires_at: int) -> None:
  """Index a Go-derived plan *before* it is published, so a rollback can always
  find it. Registration failure aborts the publish (fail closed)."""
  scope, epoch = _go_scope(match.tags)
  await client.hset(GO_PLAN_REGISTRY_KEY, plan_id, json.dumps({
    "plan_id": plan_id, "match_id": match.match_id, "symbol": match.symbol, "scope": scope,
    "epoch": epoch, "expires_at": int(expires_at),
  }, separators=(",", ":"), sort_keys=True))


async def registered_go_plans(client: Any, *, symbol: str | None = None, scope: str | None = None) -> list[dict[str, Any]]:
  rows = await client.hgetall(GO_PLAN_REGISTRY_KEY)
  plans = [json.loads(_text(v)) for v in rows.values()]
  return sorted(
    (p for p in plans if (symbol is None or p["symbol"] == symbol.upper()) and (scope is None or p["scope"] == scope)),
    key=lambda p: p["plan_id"],
  )


@dataclass
class WithdrawalReport:
  symbol: str
  scope: str | None
  matches_removed: list[str] = field(default_factory=list)
  setups_withdrawn: list[str] = field(default_factory=list)
  plans_cancel_requested: list[str] = field(default_factory=list)
  plans_already_requested: list[str] = field(default_factory=list)

  def as_dict(self) -> dict[str, Any]:
    return {
      "symbol": self.symbol, "scope": self.scope, "matches_removed": self.matches_removed,
      "setups_withdrawn": self.setups_withdrawn, "plans_cancel_requested": self.plans_cancel_requested,
      "plans_already_requested": self.plans_already_requested,
    }


async def withdraw_go_scope(
  client: Any, *, symbol: str, scope: str | None, reason: str, source: str, now: int, actor: str = "analysis_authority",
) -> WithdrawalReport:
  """Withdraw every Go-derived match and unexecuted plan of a scope (or of every
  Go scope of the symbol when ``scope`` is None). Idempotent and re-runnable: the
  operator's recovery from a crash between the fence flip and this call."""
  symbol = symbol.upper()
  report = WithdrawalReport(symbol=symbol, scope=scope)
  key = strategy_matches_key(symbol)
  matches = deserialize_matches(await client.get(key))

  def _selected(match: Any) -> bool:
    if GO_ORIGIN_TAG not in match.tags:
      return False
    return scope is None or f"{CATALOG_TAG}{scope}" in match.tags

  doomed = [m for m in matches if _selected(m)]
  if doomed:
    kept = [m for m in matches if not _selected(m)]
    if kept:
      await client.set(key, serialize_matches(kept), ex=max(60, max(m.expires_at for m in kept) - now))
    else:
      await client.delete(key)
    report.matches_removed = [m.match_id for m in doomed]
  for match in doomed:
    record = await load_setup(client, match.match_id)
    target = None
    if record is not None:
      target = INVALIDATED if record.state in _PRE_PLAN_STATES else CANCELLED if record.state == PLAN_BUILT else None
    if target is not None:
      try:
        await transition_setup(client, match.match_id, target, reason_code=f"go_{source}")
        report.setups_withdrawn.append(match.match_id)
      except SetupLifecycleError:
        log.exception("could not withdraw setup %s during Go withdrawal", match.match_id)
    _, epoch = _go_scope(match.tags)
    await _cancel(client, report, plan_id_for_match(match.match_id), reason, source, now, epoch=epoch)
  for plan in await registered_go_plans(client, symbol=symbol, scope=scope):
    await _cancel(client, report, plan["plan_id"], reason, source, now, epoch=plan.get("epoch"))
  return report


async def _cancel(client: Any, report: WithdrawalReport, plan_id: str, reason: str, source: str, now: int, *, epoch: int | None) -> None:
  if plan_id in report.plans_cancel_requested or plan_id in report.plans_already_requested:
    return
  fresh = await request_plan_cancel(client, plan_id, reason=reason, source=source, requested_at=now, epoch=epoch)
  (report.plans_cancel_requested if fresh else report.plans_already_requested).append(plan_id)

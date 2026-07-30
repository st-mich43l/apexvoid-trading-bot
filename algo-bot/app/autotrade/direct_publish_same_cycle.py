"""Complete the first-call arm handoff for fresh B-grade M1 signals.

Legacy non-reaction execution intentionally returned after moving a setup from
WORKER_ACKNOWLEDGED to ARMED_WAITING_TRIGGER. ZoneWatch has already supplied a
fresh, episode-scoped M1 trigger before setup creation, so that forced future
cycle is no longer valid. This adapter performs one bounded second evaluation
in the same await chain and never loops.
"""

from __future__ import annotations

from typing import Any


_INSTALLED = False
_ORIGINAL_SAFE_DIRECT: Any = None
_ORIGINAL_WORKER_DIRECT: Any = None


def install_same_cycle_publish_retry() -> None:
  global _INSTALLED, _ORIGINAL_SAFE_DIRECT, _ORIGINAL_WORKER_DIRECT
  if _INSTALLED:
    return

  from app.autotrade import worker
  from app.autotrade import zone_execution_cutover as cutover

  _ORIGINAL_SAFE_DIRECT = cutover._safe_direct_publish
  _ORIGINAL_WORKER_DIRECT = worker.try_publish_executable_signal
  original = _ORIGINAL_SAFE_DIRECT

  async def same_cycle_publish(
    client: Any,
    match: Any,
    *,
    symbol: str,
    event_ts: str | None = None,
    source: Any | None = None,
  ):
    result = await original(
      client,
      match,
      symbol=symbol,
      event_ts=event_ts,
      source=source,
    )
    fresh_b_trigger = bool(
      match.strategy == "Range Edge Scalp"
      and match.reaction_type
      and match.confirmation_bar_ts
    )
    if (
      not fresh_b_trigger
      or result.status != worker.PUBLISH_STATUS_REMAINED_WATCHING
      or result.reason_code == "direct_publish_failed_durable_fallback"
    ):
      return result

    existing = await worker.resolve_existing_v7_state(client, match)
    if existing.already_published or existing.already_terminal:
      return result

    # First pass performed the legacy ACK -> ARMED_WAITING_TRIGGER transition.
    # The second pass consumes the already-supplied current-episode M1 trigger
    # and publishes immediately. Exactly one bounded retry is permitted.
    return await original(
      client,
      match,
      symbol=symbol,
      event_ts=event_ts,
      source=source,
    )

  cutover._safe_direct_publish = same_cycle_publish
  worker.try_publish_executable_signal = same_cycle_publish
  _INSTALLED = True


def uninstall_same_cycle_publish_retry() -> None:
  """Restore module globals after a bounded app lifecycle/test run."""
  global _INSTALLED, _ORIGINAL_SAFE_DIRECT, _ORIGINAL_WORKER_DIRECT
  if not _INSTALLED:
    return

  from app.autotrade import worker
  from app.autotrade import zone_execution_cutover as cutover

  if _ORIGINAL_SAFE_DIRECT is not None:
    cutover._safe_direct_publish = _ORIGINAL_SAFE_DIRECT
  if _ORIGINAL_WORKER_DIRECT is not None:
    worker.try_publish_executable_signal = _ORIGINAL_WORKER_DIRECT
  _ORIGINAL_SAFE_DIRECT = None
  _ORIGINAL_WORKER_DIRECT = None
  _INSTALLED = False

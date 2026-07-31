"""Same-cycle direct publish helper.

WORKER_ACKNOWLEDGED / ARMED_WAITING_TRIGGER handoffs are removed. Direct
publish evaluates once per call; this adapter remains as a no-op install
point for ZoneWatch cutover wiring.
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
  # Identity wrappers keep the cutover install/uninstall contract intact
  # without reintroducing ACK -> ARMED same-cycle retries.
  cutover._safe_direct_publish = _ORIGINAL_SAFE_DIRECT
  worker.try_publish_executable_signal = _ORIGINAL_WORKER_DIRECT
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
  _INSTALLED = False
  _ORIGINAL_SAFE_DIRECT = None
  _ORIGINAL_WORKER_DIRECT = None

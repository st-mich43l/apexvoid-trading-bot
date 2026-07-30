"""Install/uninstall helpers for the live ZoneWatch cutover.

The production process installs once and normally runs forever. Tests exercise
``app.main.main`` in a bounded lifecycle, so shutdown must restore the scanner
and worker module globals instead of leaking the cutover into unrelated tests.
"""

from __future__ import annotations


def uninstall_zone_execution_cutover() -> None:
  from app.analysis import scanner
  from app.autotrade import worker
  from app.autotrade import zone_execution_cutover as cutover

  if not cutover._INSTALLED:
    return
  if cutover._ORIGINAL_SYNC is not None:
    scanner._sync_strategy_match = cutover._ORIGINAL_SYNC
  if cutover._ORIGINAL_FORMAT is not None:
    scanner._format_detection = cutover._ORIGINAL_FORMAT
  if cutover._ORIGINAL_DIRECT_PUBLISH is not None:
    worker.try_publish_executable_signal = cutover._ORIGINAL_DIRECT_PUBLISH

  cutover._PUBLISHED_SETUP_IDS.clear()
  cutover._ORIGINAL_SYNC = None
  cutover._ORIGINAL_FORMAT = None
  cutover._ORIGINAL_DIRECT_PUBLISH = None
  cutover._INSTALLED = False

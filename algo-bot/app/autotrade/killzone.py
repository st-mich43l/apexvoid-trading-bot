"""Trading-window label persisted with each closed autonomous result.

Analytics only: session is a context and quality feature, never a clock gate.
The windows are the XAU London/NY playbook ones (owner 2026-08-10 prod dig):
London 07-10, NY 13-16, late NY 22-23 UTC, with the 20-22 UTC rollover hours
outside any window.
"""

from __future__ import annotations

from datetime import datetime, timezone

KILLZONE_LONDON = "london"
KILLZONE_NY = "london_ny"
KILLZONE_LATE_NY = "late_ny"
KILLZONE_NONE = "outside"

_LONDON_HOURS = range(7, 10)
_NY_HOURS = range(13, 16)
_LATE_NY_HOURS = (22, 23)
_ROLLOVER_HOURS = (20, 21, 22)


def classify_killzone(ts: int | float | None = None) -> str:
  """Window name for a UTC timestamp (now when omitted)."""
  hour = (
    datetime.now(timezone.utc).hour
    if ts is None
    else datetime.fromtimestamp(int(ts), tz=timezone.utc).hour
  )
  if hour in _LATE_NY_HOURS:
    return KILLZONE_LATE_NY
  if hour in _ROLLOVER_HOURS:
    return KILLZONE_NONE
  if hour in _LONDON_HOURS:
    return KILLZONE_LONDON
  if hour in _NY_HOURS:
    return KILLZONE_NY
  return KILLZONE_NONE

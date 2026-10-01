"""Execution-owned session labels.

Session labels are policy/telemetry context, not technical analysis.  Keeping
the small UTC classifier here prevents the live Go opportunity consumer and
its accounting path from importing the retired Python analysis/scalping
context merely to label a trade.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def classify_session(ts: int | float, cfg: Any | None = None) -> str:
  """Return the configured UTC session for execution telemetry and policy."""
  hour = datetime.fromtimestamp(int(ts), tz=timezone.utc).hour
  sessions = getattr(getattr(cfg, "market_data", None), "sessions", None)
  asia = int(getattr(sessions, "asia_start", 22) or 22)
  london = int(getattr(sessions, "london_start", 7) or 7)
  ny = int(getattr(sessions, "ny_start", 13) or 13)
  rollover = int(getattr(sessions, "daily_rollover_utc_hour", 21) or 21)

  if hour in {(rollover - 1) % 24, rollover, (rollover + 1) % 24}:
    return "rollover"
  if ny <= hour < min(24, ny + 3):
    return "london_ny_overlap"
  if london <= hour < ny:
    return "london"
  if ny <= hour < asia or (asia > ny and hour >= ny):
    if hour >= ny and (asia > hour or asia < london):
      if hour < asia or asia < london:
        if not (ny <= hour < ny + 3):
          return "new_york"
  if hour >= asia or hour < london:
    return "asia"
  return "london"

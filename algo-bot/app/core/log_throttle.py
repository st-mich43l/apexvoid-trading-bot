"""Process-local log throttling for high-frequency expected conditions.

First emission for a key uses the requested level; repeats within the interval
are demoted to DEBUG so default INFO stays readable. ``LOG_LEVEL=DEBUG`` still
surfaces every repeat.
"""

from __future__ import annotations

import logging
import time
from typing import Any

DEFAULT_INTERVAL_S = 300.0

_last_emitted_at: dict[str, float] = {}


def reset_log_throttle() -> None:
  """Clear throttle state (tests)."""
  _last_emitted_at.clear()


def log_at_most(
  logger: logging.Logger,
  key: str,
  msg: str,
  *args: Any,
  interval_s: float = DEFAULT_INTERVAL_S,
  level: int = logging.INFO,
  **kwargs: Any,
) -> bool:
  """Log at ``level`` at most once per ``key`` within ``interval_s``.

  Returns True when the message was emitted at the requested level.
  """
  now = time.monotonic()
  last = _last_emitted_at.get(key)
  if last is not None and (now - last) < float(interval_s):
    logger.debug(msg, *args, **kwargs)
    return False
  _last_emitted_at[key] = now
  logger.log(level, msg, *args, **kwargs)
  return True

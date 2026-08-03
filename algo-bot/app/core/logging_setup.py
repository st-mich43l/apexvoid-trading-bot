"""Host-mounted daily rotating file logs for the algo-bot service.

Stdout/stderr remain active for ``docker compose logs``. File logs land under
``LOG_DIR`` (default ``/var/log/apexvoid``) and rotate at midnight UTC-local
via ``TimedRotatingFileHandler`` — the service rotates itself; no host
logrotate is required.
"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any


DEFAULT_LOG_DIR = "/var/log/apexvoid"
DEFAULT_LOG_FILE_NAME = "algo-bot.log"
DEFAULT_RETENTION_DAYS = 14


def configure_logging(
  *,
  level: str = "INFO",
  log_dir: str | None = None,
  log_file_name: str = DEFAULT_LOG_FILE_NAME,
  retention_days: int = DEFAULT_RETENTION_DAYS,
  enable_file: bool = True,
) -> dict[str, Any]:
  """Configure root logging: console always, rotating file when enabled."""
  resolved_level = getattr(logging, str(level).upper(), logging.INFO)
  fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
  root = logging.getLogger()
  root.handlers.clear()
  root.setLevel(resolved_level)

  console = logging.StreamHandler()
  console.setFormatter(fmt)
  console.setLevel(resolved_level)
  root.addHandler(console)

  info: dict[str, Any] = {
    "level": logging.getLevelName(resolved_level),
    "console": True,
    "file": None,
    "retention_days": int(retention_days),
  }
  if not enable_file:
    return info

  # ``log_dir`` is supplied by the composition root from ``runtime_config``
  # (``bootstrap.logging.directory``, itself LOG_DIR/APEXVOID_LOG_DIR aware), so
  # this early-boot helper no longer performs an ambient environment read.
  directory = Path(log_dir or DEFAULT_LOG_DIR)
  try:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / log_file_name
    handler = TimedRotatingFileHandler(
      filename=str(path),
      when="midnight",
      interval=1,
      backupCount=max(1, int(retention_days)),
      encoding="utf-8",
      utc=False,
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(fmt)
    handler.setLevel(resolved_level)
    root.addHandler(handler)
    info["file"] = str(path)
  except OSError as exc:
    # Keep running on console-only if the mount is missing/unwritable.
    logging.getLogger("bot").warning(
      "file logging disabled path=%s error=%s",
      directory / log_file_name,
      exc,
    )
  return info

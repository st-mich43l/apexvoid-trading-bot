"""One-time destructive migration: drop the obsolete manual_algo_charts table.

The manual_algo_charts feature (Redis OHLC snapshots around owner /algo
issue/fill/close events, for later formula fitting) is removed — see
docs/analysis-engine-v2-migration.md and
apexvoid-bot-prompts/rebuild-strategies.md §58-§68. Its module
(app/signals/manual_algo_chart.py), persistence helpers (store.py's
upsert_manual_algo_chart/load_manual_algo_charts/_safe_snapshot_manual_chart
and every call site), and dependent scripts
(manual_formula_replay.py, backfill_manual_algo_charts.py) are already
deleted. This script is the last step: the table itself.

This repo has no migration-file/version-tracking tool — store.py's
init_db() is one big idempotent (CREATE TABLE IF NOT EXISTS / ADD COLUMN
IF NOT EXISTS) schema-bootstrap function, safe to call on every startup.
A DROP does not belong in that function (it would run on every startup
too); it belongs here, as a standalone, explicitly-invoked, auditable
step — run once, by a human, not part of any automatic deploy path.

Usage (inside the bot container or with DATABASE_URL pointed at the
target Postgres):

  python -m app.scripts.drop_manual_algo_charts --dry-run
  python -m app.scripts.drop_manual_algo_charts \
    --apply --archive-file /secure-backup/manual_algo_charts.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

log = logging.getLogger("drop_manual_algo_charts")


async def _row_count(db) -> int | None:
  """Row count before the drop, or None if the table is already gone."""
  exists = await db.fetchval("SELECT to_regclass('public.manual_algo_charts')")
  if exists is None:
    return None
  return await db.fetchval("SELECT COUNT(*) FROM manual_algo_charts")


def _json_value(value):
  if isinstance(value, (date, datetime)):
    return value.isoformat()
  if isinstance(value, Decimal):
    return str(value)
  if isinstance(value, bytes):
    return {"encoding": "hex", "value": value.hex()}
  raise TypeError(f"cannot archive database value of type {type(value).__name__}")


async def _archive(db, path: Path, expected_count: int) -> str:
  columns = await db.fetch(
    """
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'manual_algo_charts'
    ORDER BY ordinal_position
    """
  )
  rows = await db.fetch("SELECT * FROM manual_algo_charts")
  if len(rows) != expected_count:
    raise RuntimeError(
      f"archive count changed during read: expected {expected_count}, got {len(rows)}"
    )
  document = {
    "format": "apexvoid.manual_algo_charts.archive.v1",
    "archived_at": datetime.now(timezone.utc).isoformat(),
    "table": "public.manual_algo_charts",
    "row_count": expected_count,
    "columns": [dict(column) for column in columns],
    "rows": [dict(row) for row in rows],
  }
  encoded = json.dumps(
    document, ensure_ascii=True, separators=(",", ":"), default=_json_value,
  ).encode("utf-8") + b"\n"
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary = path.with_name(f".{path.name}.tmp")
  with temporary.open("xb") as handle:
    os.chmod(temporary, 0o600)
    handle.write(encoded)
    handle.flush()
    os.fsync(handle.fileno())
  os.replace(temporary, path)
  digest = hashlib.sha256(encoded).hexdigest()
  log.info(
    "archived %d rows to %s (sha256=%s)", expected_count, path, digest,
  )
  return digest


async def run(*, apply: bool, archive_file: Path | None = None) -> int:
  """Returns the pre-drop row count (0 if the table didn't exist)."""
  from app.persistence import store

  async with store._connect() as db:  # noqa: SLF001 - same-package internal reuse
    before = await _row_count(db)
    if before is None:
      log.info("manual_algo_charts does not exist — nothing to drop")
      return 0
    log.info("manual_algo_charts row count before drop: %d", before)
    if not apply:
      log.info("dry-run: not dropping (pass --apply to actually drop)")
      return before
    if archive_file is None:
      raise ValueError("--apply requires --archive-file; refusing irreversible drop")
    if archive_file.exists():
      raise FileExistsError(
        f"archive already exists: {archive_file}; choose a new path"
      )
    async with db.transaction():
      await db.execute("LOCK TABLE manual_algo_charts IN ACCESS EXCLUSIVE MODE")
      locked_count = await db.fetchval("SELECT COUNT(*) FROM manual_algo_charts")
      await _archive(db, archive_file, locked_count)
      await db.execute("DROP TABLE IF EXISTS manual_algo_charts")
    after = await db.fetchval("SELECT to_regclass('public.manual_algo_charts')")
    if after is not None:
      raise RuntimeError(
        "manual_algo_charts still exists after DROP TABLE — migration did not "
        "take effect, refusing to report success"
      )
    log.info("manual_algo_charts dropped; to_regclass confirms it no longer exists")
    return before


def main(argv: list[str] | None = None) -> int:
  logging.basicConfig(level=logging.INFO, format="%(message)s")
  parser = argparse.ArgumentParser(description=__doc__)
  mode = parser.add_mutually_exclusive_group(required=True)
  mode.add_argument(
    "--dry-run", action="store_true", help="report the row count only",
  )
  mode.add_argument(
    "--apply", action="store_true", help="actually drop the table",
  )
  parser.add_argument(
    "--archive-file", type=Path,
    help="new JSON archive path; required with --apply",
  )
  args = parser.parse_args(argv)
  if args.apply and args.archive_file is None:
    parser.error("--apply requires --archive-file")
  asyncio.run(run(apply=args.apply, archive_file=args.archive_file))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())

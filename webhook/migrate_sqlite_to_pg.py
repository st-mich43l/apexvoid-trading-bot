#!/usr/bin/env python3
"""
migrate_sqlite_to_pg.py — one-shot data migration from the legacy SQLite
``signals.db`` into the new PostgreSQL database.

It creates the schema (via ``app.dedup.init_db``) and copies every row of the
five live tables, preserving primary keys, then advances the identity
sequences so future inserts do not collide.

Usage (run inside the bot container so the app settings/env are available):

    DATABASE_URL=postgresql://user:pass@host:5432/signals \\
      python migrate_sqlite_to_pg.py --sqlite /data/signals.db [--reset]

``--reset`` TRUNCATEs the target tables first, making the migration
re-runnable. Without it, the target tables must be empty.

Legacy columns present in SQLite but absent from the new schema (e.g.
``tps_hit``) are silently dropped — only columns that exist in both are copied.
"""

import argparse
import asyncio
import json
import logging
import sqlite3
import sys

import asyncpg

from app import dedup
from app.config import settings

logging.basicConfig(level="INFO", format="%(levelname)s: %(message)s")
log = logging.getLogger("migrate")

# Live tables, in insertion order. Tables with an identity primary key list it
# so we can reset the sequence afterwards.
TABLES = [
  ("meta", None),
  ("events", None),
  ("manual_signals", "id"),
  ("pips_log", "id"),
  ("signal_posts", None),
]


def read_sqlite_rows(path: str, table: str) -> list[dict]:
  conn = sqlite3.connect(path)
  conn.row_factory = sqlite3.Row
  try:
    cur = conn.execute(f"SELECT * FROM {table}")
    return [dict(r) for r in cur.fetchall()]
  finally:
    conn.close()


async def pg_columns(pg: asyncpg.Connection, table: str) -> list[str]:
  rows = await pg.fetch(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name = $1 ORDER BY ordinal_position",
    table,
  )
  return [r["column_name"] for r in rows]


async def copy_table(
  pg: asyncpg.Connection,
  sqlite_path: str,
  table: str,
  identity_col: str | None,
  reset: bool,
) -> int:
  src_rows = read_sqlite_rows(sqlite_path, table)
  dest_cols = await pg_columns(pg, table)
  if reset:
    await pg.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")
  existing = await pg.fetchval(f"SELECT COUNT(*) FROM {table}")
  if existing and not reset:
    raise SystemExit(
      f"Refusing to migrate: {table} already has {existing} rows "
      f"(pass --reset to truncate first)."
    )
  if not src_rows:
    log.info("%-16s 0 rows (nothing to copy)", table)
    return 0

  # Only copy columns present in both schemas, in the destination's order.
  cols = [c for c in dest_cols if c in src_rows[0]]
  placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
  collist = ", ".join(cols)
  sql = f"INSERT INTO {table} ({collist}) VALUES ({placeholders})"
  values = [tuple(row[c] for c in cols) for row in src_rows]
  await pg.executemany(sql, values)

  if identity_col:
    # Advance the IDENTITY sequence past the largest migrated id.
    await pg.execute(
      f"SELECT setval("
      f"  pg_get_serial_sequence('{table}', '{identity_col}'), "
      f"  (SELECT MAX({identity_col}) FROM {table})"
      f")"
    )
  dropped = [c for c in src_rows[0] if c not in cols]
  extra = f" (dropped legacy cols: {dropped})" if dropped else ""
  log.info("%-16s %d rows copied%s", table, len(src_rows), extra)
  return len(src_rows)


async def verify(pg: asyncpg.Connection, sqlite_path: str) -> None:
  log.info("--- verification (sqlite -> postgres row counts) ---")
  ok = True
  for table, _ in TABLES:
    src = len(read_sqlite_rows(sqlite_path, table))
    dst = await pg.fetchval(f"SELECT COUNT(*) FROM {table}")
    flag = "OK " if src == dst else "MISMATCH"
    if src != dst:
      ok = False
    log.info("  %-16s sqlite=%-4d postgres=%-4d  %s", table, src, dst, flag)
  if not ok:
    raise SystemExit("Row-count verification FAILED.")
  log.info("All row counts match.")


async def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument("--sqlite", default="/data/signals.db",
                  help="Path to the source SQLite database.")
  ap.add_argument("--reset", action="store_true",
                  help="TRUNCATE target tables before copying (re-runnable).")
  args = ap.parse_args()

  log.info("Source SQLite : %s", args.sqlite)
  log.info("Target Postgres: %s", settings.database_url.split("@")[-1])

  # Create the schema in Postgres (idempotent).
  await dedup.init_db()

  pg = await asyncpg.connect(settings.database_url)
  try:
    async with pg.transaction():
      total = 0
      for table, identity_col in TABLES:
        total += await copy_table(
          pg, args.sqlite, table, identity_col, args.reset,
        )
    log.info("Copied %d rows total.", total)
    await verify(pg, args.sqlite)
  finally:
    await pg.close()
    await dedup.close_pool()

  log.info("Migration complete.")


if __name__ == "__main__":
  asyncio.run(main())

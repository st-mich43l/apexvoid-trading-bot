# PostgreSQL migration & cutover runbook

The bot persisted to SQLite (`/data/signals.db`) via `aiosqlite`. It now uses
PostgreSQL via `asyncpg`, with Postgres running as a sidecar container in the
same compose stack. This document covers the schema, the data migration, and
the production cutover.

## What changed

- `app/dedup.py` — rewritten from `aiosqlite` to `asyncpg` (shared pool,
  `$1` placeholders, `RETURNING`, `ON CONFLICT`, `FOR UPDATE` locking). Public
  function signatures and return shapes are unchanged, so callers are untouched.
- `app/config.py` — `db_path` replaced by `database_url` (env `DATABASE_URL`).
- `requirements.txt` — `aiosqlite` → `asyncpg`.
- `docker-compose.yml` / `deployment-template/docker-compose.yml.j2` — added a
  `postgres:17-alpine` service on a named `pgdata` volume; the bot `depends_on`
  it (healthcheck-gated).
- `migrate_sqlite_to_pg.py` — one-shot SQLite → Postgres data copier (baked
  into the image).
- `docs/schema.sql` — reference DDL (mirrors `dedup.init_db()`).

The `signals`, `votes` and `cooldowns` tables from the old SQLite file were
dead (empty, not created by `init_db`, not queried) and are **not** migrated.
Migrated tables: `manual_signals`, `signal_posts`, `pips_log`, `events`, `meta`.

## Required deploy config (vault)

`vault_apexvoid_trading_bot_env` (in ansible-library) must define these keys,
and drop the now-unused `DB_PATH`:

```
DATABASE_URL:      postgresql://apexvoid:<PASSWORD>@postgres:5432/signals
POSTGRES_USER:     apexvoid
POSTGRES_PASSWORD: <PASSWORD>
POSTGRES_DB:       signals
```

`ansible-library` `inventory/group_vars/all/vars.yml` already bumps
`expected_services` for this project from 1 to 2 (bot + postgres).

> The `DATABASE_URL` host is the compose service name `postgres`. The password
> in `DATABASE_URL` and `POSTGRES_PASSWORD` must match.

## Cutover procedure (VPS)

Data volume `pgdata` persists across deploys, so this is done once.

1. **Back up** the live SQLite DB (consistent snapshot):
   ```
   docker exec apexvoid-trading-bot python3 -c \
     "import sqlite3;s=sqlite3.connect('/data/signals.db');d=sqlite3.connect('/data/signals.backup.db');s.backup(d)"
   ```
2. Render/write the new `docker-compose.yml` (postgres + bot) with the env above.
3. Start Postgres and wait until healthy:
   ```
   docker compose up -d postgres
   ```
4. Migrate the data (schema is created by the script via `init_db`):
   ```
   docker compose run --rm --no-deps bot \
     python migrate_sqlite_to_pg.py --sqlite /data/signals.db
   ```
   The script prints a per-table row-count verification and aborts on mismatch.
5. Bring up the new bot:
   ```
   docker compose up -d
   ```
6. **Verify**: `docker logs apexvoid-trading-bot` shows `DB ready (PostgreSQL)`
   and `Starting Telegram polling` with no tracebacks; spot-check row counts:
   ```
   docker exec apexvoid-trading-postgres \
     psql -U apexvoid -d signals -c "SELECT count(*) FROM manual_signals;"
   ```

### Rollback

The old image + `/data/signals.db` are untouched. To roll back, redeploy the
previous image tag with the SQLite-based `docker-compose.yml` (no postgres
service). No data written after cutover would be in SQLite, so roll back
promptly if needed.

## Re-running the migration

`migrate_sqlite_to_pg.py --reset` TRUNCATEs the target tables first, making it
safe to re-run against a non-empty database.

## Local development / tests

```
docker run -d --name pg -e POSTGRES_USER=apexvoid -e POSTGRES_PASSWORD=apexvoid \
  -e POSTGRES_DB=signals -p 55432:5432 postgres:17-alpine
export DATABASE_URL=postgresql://apexvoid:apexvoid@localhost:55432/signals
pytest            # 76 tests, each runs against a freshly-wiped schema
```

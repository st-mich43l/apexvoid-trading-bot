"""
Shared test fixtures.

The bot now persists to PostgreSQL, so the suite needs a live Postgres. Point
``DATABASE_URL`` at a throwaway database (default: the local dev container on
``localhost:55432``); every test runs against a freshly-wiped ``public`` schema.
"""

import asyncio
import os

import asyncpg
import fakeredis
import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")
os.environ.setdefault(
  "DATABASE_URL",
  "postgresql://apexvoid:apexvoid@localhost:55432/signals",
)

from app import dedup  # noqa: E402  (import after env is seeded)
from app import redis_state  # noqa: E402
from app import market_map_delivery  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
  """One event loop for the whole session so the shared pool stays valid."""
  loop = asyncio.new_event_loop()
  yield loop
  loop.close()


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
  """Back the watcher's Redis state with an isolated in-memory fake per test."""
  monkeypatch.setattr(
    redis_state,
    "_client",
    fakeredis.FakeAsyncRedis(decode_responses=True),
  )
  market_map_delivery.clear_market_map_cache()
  yield
  redis_state._client = None
  market_map_delivery.clear_market_map_cache()


@pytest.fixture(autouse=True)
def _reset_db(event_loop):
  """Drop and recreate the schema before each test; drop the pool after."""
  async def _wipe():
    await dedup.close_pool()
    conn = await asyncpg.connect(dedup.settings.database_url)
    try:
      await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
      await conn.close()

  event_loop.run_until_complete(_wipe())
  yield
  event_loop.run_until_complete(dedup.close_pool())


class _Sql:
  """Minimal async helper for tests that need to poke the DB directly."""

  async def exec(self, query, *args):
    async with dedup._connect() as db:
      return await db.execute(query, *args)

  async def val(self, query, *args):
    async with dedup._connect() as db:
      return await db.fetchval(query, *args)

  async def row(self, query, *args):
    async with dedup._connect() as db:
      return await db.fetchrow(query, *args)

  async def fetch(self, query, *args):
    async with dedup._connect() as db:
      return await db.fetch(query, *args)


@pytest.fixture
def sql():
  return _Sql()

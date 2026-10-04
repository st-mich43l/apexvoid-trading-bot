"""Shared test fixtures for the native YAML runtime."""

import asyncio
import os

import asyncpg
import fakeredis
import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")
os.environ.setdefault("DATABASE_URL", "postgresql://apexvoid:apexvoid@localhost:55432/signals")
os.environ.setdefault("POSTGRES_PASSWORD", "apexvoid")

from app.core.config import runtime_config  # noqa: E402
from app.persistence import redis_state, store  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
  loop = asyncio.new_event_loop()
  yield loop
  loop.close()


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
  client = fakeredis.FakeAsyncRedis(decode_responses=True)
  client._apexvoid_allow_non_atomic_test_fallback = True
  monkeypatch.setattr(redis_state, "_client", client)
  yield
  redis_state._client = None


@pytest.fixture(autouse=True)
def _reset_db(event_loop, request):
  if request.node.get_closest_marker("no_database"):
    yield
    return

  async def _wipe():
    await store.close_pool()
    conn = await asyncpg.connect(runtime_config.runtime.postgres.url)
    try:
      await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
      await conn.close()

  event_loop.run_until_complete(_wipe())
  yield
  event_loop.run_until_complete(store.close_pool())


class _Sql:
  async def exec(self, query, *args):
    async with store._connect() as db:
      return await db.execute(query, *args)

  async def val(self, query, *args):
    async with store._connect() as db:
      return await db.fetchval(query, *args)

  async def row(self, query, *args):
    async with store._connect() as db:
      return await db.fetchrow(query, *args)

  async def fetch(self, query, *args):
    async with store._connect() as db:
      return await db.fetch(query, *args)


@pytest.fixture
def sql():
  return _Sql()

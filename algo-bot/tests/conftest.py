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
os.environ.setdefault("POSTGRES_PASSWORD", "apexvoid")

from app.core.config import runtime_config  # noqa: E402  (import after env is seeded)
from app.persistence import store  # noqa: E402  (import after env is seeded)
from app.persistence import redis_state  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
  """One event loop for the whole session so the shared pool stays valid."""
  loop = asyncio.new_event_loop()
  yield loop
  loop.close()


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
  """Back the watcher's Redis state with an isolated in-memory fake per test."""
  client = fakeredis.FakeAsyncRedis(decode_responses=True)
  # Production publication fails closed if Redis EVAL is unavailable. Tests
  # opt into the rollback-safe compatibility path explicitly.
  client._apexvoid_allow_non_atomic_test_fallback = True
  monkeypatch.setattr(
    redis_state,
    "_client",
    client,
  )
  yield
  redis_state._client = None


@pytest.fixture(autouse=True)
def _bridge_legacy_symbol_map_tests(monkeypatch, request):
  """Route two legacy SYMBOLS-mutation tests through typed instrument context.

  Production helpers intentionally reject unknown symbols. These tests predate
  the effective-instrument registry and add US30 to the compatibility map during
  the test body. The proxy observes that mutation and builds a temporary typed
  feed-only instrument rather than restoring a production fallback.
  """
  legacy_tests = {
    "test_symbol_channel_and_pip_maps",
    "test_review_uses_symbol_pip_size",
  }
  if request.node.name not in legacy_tests:
    yield
    return

  from app.configuration.effective_instrument import EffectiveInstrumentError
  from app.configuration.models.instruments import (
    InstrumentAnalysisConfig,
    InstrumentConfig,
    InstrumentContractConfig,
    InstrumentLookbacksConfig,
    InstrumentMarketDataConfig,
    InstrumentRollout,
    InstrumentZoneWidthConfig,
    InstrumentsConfig,
  )
  from app.core import symbols as symbol_module

  base = symbol_module.runtime_config

  class _TypedLegacySymbolProxy:
    def __getattr__(self, name):
      return getattr(base, name)

    def for_instrument(self, symbol):
      try:
        return base.for_instrument(symbol)
      except EffectiveInstrumentError:
        canonical = str(symbol).strip().upper()
        metadata = symbol_module.SYMBOLS.get(canonical)
        if metadata is None:
          raise
        instruments = dict(base.instruments.root)
        instruments[canonical] = InstrumentConfig(
          enabled=True,
          rollout=InstrumentRollout.FEED_ONLY,
          canonical_symbol=canonical,
          broker_symbol=canonical,
          timeframes=["M1"],
          contract=InstrumentContractConfig(
            pip_size=float(metadata["pip"]),
            contract_units_per_lot=1.0,
            price_digits=int(metadata["digits"]),
          ),
          market_data=InstrumentMarketDataConfig(
            lookbacks=InstrumentLookbacksConfig(
              h1_bars=50,
              m15_bars=50,
              m5_bars=50,
              m1_bars=50,
            ),
          ),
          analysis=InstrumentAnalysisConfig(
            zones=InstrumentZoneWidthConfig(
              minimum_width_price=1.0,
              preferred_minimum_width_price=1.0,
              preferred_maximum_width_price=2.0,
              major_maximum_width_price=3.0,
            ),
          ),
        )
        augmented = base.model_copy(
          update={"instruments": InstrumentsConfig(root=instruments)},
        )
        return augmented.for_instrument(canonical)

  monkeypatch.setattr(symbol_module, "runtime_config", _TypedLegacySymbolProxy())
  yield


@pytest.fixture(autouse=True)
def _reset_db(event_loop, request):
  """Drop and recreate the schema before each test; drop the pool after."""
  if request.node.get_closest_marker("no_database"):
    yield
    return

  async def _wipe():
    await store.close_pool()
    conn = await asyncpg.connect(runtime_config.bootstrap.postgres.url)
    try:
      await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
      await conn.close()

  event_loop.run_until_complete(_wipe())
  yield
  event_loop.run_until_complete(store.close_pool())


class _Sql:
  """Minimal async helper for tests that need to poke the DB directly."""

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

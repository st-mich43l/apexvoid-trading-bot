from contextlib import asynccontextmanager

import pytest

pytestmark = pytest.mark.no_database

from app.autotrade import lifecycle, reaction_funnel
from app.persistence import redis_state, store


class _Connection:
  def __init__(self):
    self.calls = []

  async def execute(self, query, *args):
    self.calls.append((query, args))


async def _persist_fill(monkeypatch, event):
  connection = _Connection()

  @asynccontextmanager
  async def connect():
    yield connection

  unresolved = []

  async def record_unresolved(*args, **kwargs):
    unresolved.append((args, kwargs))

  monkeypatch.setattr(store, "_connect", connect)
  monkeypatch.setattr(store, "_record_unresolved_fill_setup", record_unresolved)
  monkeypatch.setattr(reaction_funnel, "_record_unresolved_setup_name", lambda raw: None)
  await store._record_auto_trade_fill({
    "type": "order_filled",
    "position_id": 42,
    "group_id": "group-42",
    "stream": "algo_auto",
    "symbol": "XAU",
    "direction": "BUY",
    "price": 4000.0,
    "volume": 1000,
    "timestamp": 1,
    **event,
  })
  return connection.calls[-1], unresolved


@pytest.mark.asyncio
async def test_fill_setup_canonicalizes_primary_setup_key(monkeypatch):
  (_query, args), unresolved = await _persist_fill(
    monkeypatch, {"setup": "key-level"},
  )
  assert args[5:7] == ("Key Level", None)
  assert unresolved == []


@pytest.mark.asyncio
async def test_fill_setup_uses_strategy_fallback(monkeypatch):
  (_query, args), unresolved = await _persist_fill(
    monkeypatch, {"strategy": "key-level"},
  )
  assert args[5:7] == ("Key Level", None)
  assert unresolved == []


@pytest.mark.asyncio
async def test_missing_fill_setup_persists_null_and_is_observed(monkeypatch):
  (_query, args), unresolved = await _persist_fill(monkeypatch, {})
  assert args[5:7] == (None, None)
  assert unresolved[0][1] == {
    "symbol": "XAU", "stream": "algo_auto", "raw_setup": None,
  }


@pytest.mark.asyncio
async def test_unmapped_fill_setup_preserves_raw_and_is_observed(monkeypatch):
  (_query, args), unresolved = await _persist_fill(
    monkeypatch, {"setup": "Future Setup"},
  )
  assert args[5:7] == ("Future Setup", "Future Setup")
  assert unresolved[0][1]["raw_setup"] == "Future Setup"


@pytest.mark.asyncio
async def test_scale_in_fill_keeps_parent_setup_suffix(monkeypatch):
  (_query, args), unresolved = await _persist_fill(
    monkeypatch, {"setup": "Key Level · add_momentum"},
  )
  assert args[5:7] == ("Key Level · add_momentum", None)
  assert unresolved == []


@pytest.mark.asyncio
async def test_unresolved_fill_setup_metric_and_log_include_stream_and_keys(
  monkeypatch, caplog,
):
  seen = []

  async def increment(client, name, *, symbol, dimensions):
    seen.append((client, name, symbol, dimensions))

  client = object()
  monkeypatch.setattr(lifecycle, "increment_metric", increment)
  monkeypatch.setattr(redis_state, "get_client", lambda: client)

  with caplog.at_level("WARNING"):
    await store._record_unresolved_fill_setup(
      {"type": "order_filled", "position_id": 42},
      symbol="EURUSD",
      stream="algo_auto",
      raw_setup=None,
    )

  assert seen == [
    (client, "fill_setup_unresolved", "EURUSD", {"stream": "algo_auto"}),
  ]
  assert "event_keys=position_id,type" in caplog.text

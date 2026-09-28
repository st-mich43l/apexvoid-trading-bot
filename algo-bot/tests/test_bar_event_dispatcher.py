"""Execution-clock tests: Python never regenerates technical opportunities."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.analysis import bar_event_dispatcher as dispatcher


pytestmark = pytest.mark.no_database


def _enable_execution(monkeypatch, *, enabled=True):
  monkeypatch.setattr(
    dispatcher, "runtime_config",
    SimpleNamespace(runtime=SimpleNamespace(auto_trade=SimpleNamespace(enabled=enabled))),
  )


def test_parse_closed_bar():
  assert dispatcher.parse_closed_bar(b"XAU:M1:1700000000") == ("XAU", "M1", "1700000000")
  assert dispatcher.parse_closed_bar("bad") is None


def test_execution_dispatcher_does_not_import_legacy_python_detectors():
  source = inspect.getsource(dispatcher)
  assert "app.analysis.scanner" not in source
  assert "app.scalping." not in source
  assert "app.autotrade.zone_execution_cutover" not in source


@pytest.mark.asyncio
async def test_per_symbol_dispatch_is_concurrent_and_same_symbol_fifo(monkeypatch):
  xau_started = asyncio.Event()
  eur_started = asyncio.Event()
  xau_second_started = asyncio.Event()
  release_xau = asyncio.Event()
  release_eur = asyncio.Event()

  async def fake_dispatch(data, **kwargs):
    raw = data.decode() if isinstance(data, bytes) else str(data)
    if raw == "XAU:M1:1":
      xau_started.set()
      await release_xau.wait()
    elif raw == "EURUSD:M1:1":
      eur_started.set()
      await release_eur.wait()
    elif raw == "XAU:M5:2":
      xau_second_started.set()
    return []

  monkeypatch.setattr(dispatcher, "dispatch_closed_bar", fake_dispatch)
  per_symbol = dispatcher._PerSymbolBarDispatcher(SimpleNamespace())
  try:
    assert await per_symbol.submit("XAU:M1:1")
    assert await per_symbol.submit("XAU:M5:2")
    assert await per_symbol.submit("EURUSD:M1:1")
    await asyncio.wait_for(xau_started.wait(), timeout=1)
    await asyncio.wait_for(eur_started.wait(), timeout=1)
    assert not xau_second_started.is_set()
    release_xau.set()
    await asyncio.wait_for(xau_second_started.wait(), timeout=1)
    release_eur.set()
    await asyncio.wait_for(per_symbol.wait_idle(), timeout=1)
  finally:
    release_xau.set()
    release_eur.set()
    await per_symbol.close()


@pytest.mark.asyncio
async def test_per_symbol_dispatch_uses_independent_ohlc_sources(monkeypatch):
  sources = {}

  async def fake_dispatch(data, *, source, **kwargs):
    sources[dispatcher.parse_closed_bar(data)[0]] = source
    return []

  monkeypatch.setattr(dispatcher, "dispatch_closed_bar", fake_dispatch)
  per_symbol = dispatcher._PerSymbolBarDispatcher(SimpleNamespace())
  try:
    await per_symbol.submit("XAU:M1:1")
    await per_symbol.submit("EURUSD:M1:1")
    await per_symbol.wait_idle()
  finally:
    await per_symbol.close()
  assert set(sources) == {"XAU", "EURUSD"}
  assert sources["XAU"] is not sources["EURUSD"]


@pytest.mark.asyncio
async def test_forced_dispatcher_close_balances_abandoned_queue(monkeypatch):
  started = asyncio.Event()
  never_release = asyncio.Event()

  async def blocked_dispatch(*args, **kwargs):
    started.set()
    await never_release.wait()
    return []

  monkeypatch.setattr(dispatcher, "dispatch_closed_bar", blocked_dispatch)
  per_symbol = dispatcher._PerSymbolBarDispatcher(SimpleNamespace())
  await per_symbol.submit("XAU:M1:1")
  await per_symbol.submit("XAU:M5:2")
  await asyncio.wait_for(started.wait(), timeout=1)
  queue = per_symbol._queues["XAU"]
  await per_symbol.close(drain_timeout=0.01)
  await asyncio.wait_for(queue.join(), timeout=1)


@pytest.mark.asyncio
async def test_execution_bar_invokes_only_worker_and_preserves_cache(monkeypatch):
  _enable_execution(monkeypatch)
  worker = AsyncMock()
  prefetch = AsyncMock()
  monkeypatch.setattr("app.autotrade.worker._handle_event", worker)
  monkeypatch.setattr(dispatcher, "prefetch_closed_bar_windows", prefetch)
  events = []
  source = SimpleNamespace(
    begin_closed_bar_cache=lambda: events.append("begin"),
    end_closed_bar_cache=lambda: events.append("end"),
  )
  client = SimpleNamespace()
  assert await dispatcher.dispatch_closed_bar("XAU:M1:1700000000", client=client, source=source) == ["worker"]
  prefetch.assert_not_awaited()
  worker.assert_awaited_once()
  assert events == ["begin", "end"]

  worker.reset_mock()
  assert await dispatcher.dispatch_closed_bar("XAU:M5:1700000300", client=client, source=source) == ["worker"]
  prefetch.assert_awaited_once_with(source, "XAU", closed_tf="M5")
  worker.assert_awaited_once()
  assert events == ["begin", "end", "begin", "end"]


@pytest.mark.asyncio
async def test_disabled_auto_execution_does_not_wake_worker_or_prefetch(monkeypatch):
  _enable_execution(monkeypatch, enabled=False)
  worker = AsyncMock()
  prefetch = AsyncMock()
  monkeypatch.setattr("app.autotrade.worker._handle_event", worker)
  monkeypatch.setattr(dispatcher, "prefetch_closed_bar_windows", prefetch)
  assert await dispatcher.dispatch_closed_bar(
    "XAU:M5:1700000300", client=SimpleNamespace(), source=SimpleNamespace(),
  ) == []
  worker.assert_not_awaited()
  prefetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_exception_is_logged_without_starting_legacy_scanner(monkeypatch, caplog):
  _enable_execution(monkeypatch)
  async def broken(*args, **kwargs):
    raise RuntimeError("worker unavailable")
  monkeypatch.setattr("app.autotrade.worker._handle_event", broken)
  assert await dispatcher.dispatch_closed_bar(
    "XAU:M1:1700000000", client=SimpleNamespace(), source=SimpleNamespace(),
  ) == []
  assert "execution-bar worker failed" in caplog.text

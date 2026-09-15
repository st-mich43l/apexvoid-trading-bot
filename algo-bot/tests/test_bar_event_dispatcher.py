from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.analysis import bar_event_dispatcher as dispatcher


def _enable_handlers(monkeypatch) -> None:
  monkeypatch.setattr(
    dispatcher,
    "runtime_config",
    SimpleNamespace(
      runtime=SimpleNamespace(
        scanner=SimpleNamespace(enabled=True),
        auto_trade=SimpleNamespace(enabled=True),
      )
    ),
  )


pytestmark = pytest.mark.no_database


def test_parse_closed_bar():
  assert dispatcher.parse_closed_bar(b"XAU:M1:1700000000") == (
    "XAU", "M1", "1700000000",
  )
  assert dispatcher.parse_closed_bar("bad") is None


@pytest.mark.asyncio
async def test_per_symbol_dispatch_is_concurrent_and_same_symbol_fifo(monkeypatch):
  xau_started = asyncio.Event()
  eur_started = asyncio.Event()
  xau_second_started = asyncio.Event()
  release_xau = asyncio.Event()
  release_eur = asyncio.Event()

  async def fake_dispatch(data, **kwargs):
    text = data.decode() if isinstance(data, bytes) else str(data)
    if text == "XAU:M1:1":
      xau_started.set()
      await release_xau.wait()
    elif text == "EURUSD:M1:1":
      eur_started.set()
      await release_eur.wait()
    elif text == "XAU:M5:2":
      xau_second_started.set()
    return []

  monkeypatch.setattr(dispatcher, "dispatch_closed_bar", fake_dispatch)
  per_symbol = dispatcher._PerSymbolBarDispatcher(SimpleNamespace())
  try:
    assert await per_symbol.submit("XAU:M1:1") is True
    assert await per_symbol.submit("XAU:M5:2") is True
    assert await per_symbol.submit("EURUSD:M1:1") is True

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
  sources: dict[str, object] = {}

  async def fake_dispatch(data, *, source, **kwargs):
    symbol = dispatcher.parse_closed_bar(data)[0]
    sources[symbol] = source
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

  # close() cancels the in-flight handler and calls task_done() for the queued
  # bar it intentionally abandons, so an embedding caller can never hang.
  await asyncio.wait_for(queue.join(), timeout=1)


@pytest.mark.asyncio
async def test_dispatch_runs_isolated_handlers(monkeypatch):
  _enable_handlers(monkeypatch)
  scanner = AsyncMock()
  worker = AsyncMock()
  zone = AsyncMock()
  scalp_handler = AsyncMock()
  monkeypatch.setattr(
    "app.analysis.scanner._handle_event", scanner, raising=False,
  )
  monkeypatch.setattr(
    "app.autotrade.worker._handle_event", worker, raising=False,
  )
  monkeypatch.setattr(
    "app.autotrade.zone_execution_cutover.evaluate_active_zone_watches",
    zone,
    raising=False,
  )
  monkeypatch.setattr(
    "app.scalping.runtime.handle_closed_bar", scalp_handler, raising=False,
  )

  client = SimpleNamespace()
  source = SimpleNamespace()
  ran = await dispatcher.dispatch_closed_bar(
    "XAU:M1:1700000000",
    client=client,
    source=source,
  )

  assert ran == ["zone_watch", "scalp", "scanner", "worker"]
  scanner.assert_awaited_once()
  worker.assert_awaited_once()
  zone.assert_awaited_once()
  scalp_handler.assert_awaited_once()
  assert zone.await_args.args[0] is client
  assert zone.await_args.kwargs["source"] is source


@pytest.mark.asyncio
async def test_dispatch_keeps_later_handlers_if_scanner_raises(monkeypatch):
  _enable_handlers(monkeypatch)
  async def boom(*args, **kwargs):
    raise RuntimeError("scanner down")

  worker = AsyncMock()
  zone = AsyncMock()
  scalp_handler = AsyncMock()
  monkeypatch.setattr("app.analysis.scanner._handle_event", boom, raising=False)
  monkeypatch.setattr(
    "app.autotrade.worker._handle_event", worker, raising=False,
  )
  monkeypatch.setattr(
    "app.autotrade.zone_execution_cutover.evaluate_active_zone_watches",
    zone,
    raising=False,
  )
  monkeypatch.setattr(
    "app.scalping.runtime.handle_closed_bar", scalp_handler, raising=False,
  )

  ran = await dispatcher.dispatch_closed_bar(
    "XAU:M1:1700000000",
    client=SimpleNamespace(),
    source=SimpleNamespace(),
  )

  assert ran == ["zone_watch", "scalp", "worker"]
  worker.assert_awaited_once()
  scalp_handler.assert_awaited_once()
  zone.assert_awaited_once()


@pytest.mark.asyncio
async def test_m5_bar_skips_zone_watch(monkeypatch):
  _enable_handlers(monkeypatch)
  scanner = AsyncMock()
  worker = AsyncMock()
  zone = AsyncMock()
  scalp_handler = AsyncMock()
  monkeypatch.setattr("app.analysis.scanner._handle_event", scanner, raising=False)
  monkeypatch.setattr("app.autotrade.worker._handle_event", worker, raising=False)
  monkeypatch.setattr(
    "app.autotrade.zone_execution_cutover.evaluate_active_zone_watches",
    zone,
    raising=False,
  )
  monkeypatch.setattr("app.scalping.runtime.handle_closed_bar", scalp_handler, raising=False)

  ran = await dispatcher.dispatch_closed_bar(
    "XAU:M5:1700000000",
    client=SimpleNamespace(),
    source=SimpleNamespace(),
  )

  assert ran == ["scalp", "scanner", "worker"]
  zone.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_m1_skips_htf_prefetch_and_clears_cache(monkeypatch):
  _enable_handlers(monkeypatch)
  order: list[str] = []
  prefetch = AsyncMock()

  async def zone(*args, **kwargs):
    order.append("zone_watch")

  async def scalp_handler(*args, **kwargs):
    order.append("scalp")

  source = SimpleNamespace(
    begin_closed_bar_cache=lambda: order.append("begin"),
    end_closed_bar_cache=lambda: order.append("end"),
  )
  monkeypatch.setattr(
    "app.autotrade.zone_execution_cutover.evaluate_active_zone_watches",
    zone,
    raising=False,
  )
  monkeypatch.setattr("app.scalping.runtime.handle_closed_bar", scalp_handler, raising=False)
  monkeypatch.setattr("app.analysis.scanner._handle_event", AsyncMock(), raising=False)
  monkeypatch.setattr("app.autotrade.worker._handle_event", AsyncMock(), raising=False)
  monkeypatch.setattr(dispatcher, "prefetch_closed_bar_windows", prefetch)

  await dispatcher.dispatch_closed_bar(
    "XAU:M1:1700000000",
    client=SimpleNamespace(),
    source=source,
  )

  prefetch.assert_not_awaited()
  assert order[:3] == ["begin", "zone_watch", "scalp"]
  assert order[-1] == "end"


@pytest.mark.asyncio
async def test_dispatch_m5_prefetches_before_scalp(monkeypatch):
  _enable_handlers(monkeypatch)
  order: list[str] = []

  async def prefetch(*args, **kwargs):
    order.append("prefetch")
    assert kwargs.get("closed_tf") == "M5"

  async def scalp_handler(*args, **kwargs):
    order.append("scalp")

  source = SimpleNamespace(
    begin_closed_bar_cache=lambda: order.append("begin"),
    end_closed_bar_cache=lambda: order.append("end"),
  )
  monkeypatch.setattr(
    "app.autotrade.zone_execution_cutover.evaluate_active_zone_watches",
    AsyncMock(),
    raising=False,
  )
  monkeypatch.setattr("app.scalping.runtime.handle_closed_bar", scalp_handler, raising=False)
  monkeypatch.setattr("app.analysis.scanner._handle_event", AsyncMock(), raising=False)
  monkeypatch.setattr("app.autotrade.worker._handle_event", AsyncMock(), raising=False)
  monkeypatch.setattr(dispatcher, "prefetch_closed_bar_windows", prefetch)

  await dispatcher.dispatch_closed_bar(
    "XAU:M5:1700000000",
    client=SimpleNamespace(),
    source=source,
  )

  assert order[:3] == ["begin", "prefetch", "scalp"]
  assert order[-1] == "end"

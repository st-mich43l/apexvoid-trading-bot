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


def _patch_handlers(monkeypatch, *, zone, worker):
  """Install ZoneWatch/worker doubles plus scanner/scalping sentinels.

  The scanner and M1 scalping discovery are no longer dispatched (Go is the
  sole automatic technical-opportunity producer); the sentinels prove it.
  """
  scanner = AsyncMock()
  scalp_handler = AsyncMock()
  monkeypatch.setattr("app.analysis.scanner._handle_event", scanner, raising=False)
  monkeypatch.setattr("app.scalping.runtime.handle_closed_bar", scalp_handler, raising=False)
  monkeypatch.setattr("app.autotrade.worker._handle_event", worker, raising=False)
  monkeypatch.setattr(
    "app.autotrade.zone_execution_cutover.evaluate_active_zone_watches",
    zone,
    raising=False,
  )
  return scanner, scalp_handler


@pytest.mark.asyncio
async def test_dispatch_runs_zone_watch_then_worker_and_never_python_detection(monkeypatch):
  # scanner.enabled=True on purpose: the flag no longer brings the scanner back.
  _enable_handlers(monkeypatch)
  worker = AsyncMock()
  zone = AsyncMock()
  scanner, scalp_handler = _patch_handlers(monkeypatch, zone=zone, worker=worker)

  client = SimpleNamespace()
  source = SimpleNamespace()
  ran = await dispatcher.dispatch_closed_bar(
    "XAU:M1:1700000000",
    client=client,
    source=source,
  )

  assert ran == ["zone_watch", "worker"]
  worker.assert_awaited_once()
  zone.assert_awaited_once()
  scanner.assert_not_awaited()
  scalp_handler.assert_not_awaited()
  assert zone.await_args.args[0] is client
  assert zone.await_args.kwargs["source"] is source
  assert zone.await_args.kwargs["symbol"] == "XAU"
  assert zone.await_args.kwargs["event_ts"] == "1700000000"


@pytest.mark.asyncio
async def test_dispatch_keeps_worker_if_zone_watch_raises(monkeypatch):
  _enable_handlers(monkeypatch)
  async def boom(*args, **kwargs):
    raise RuntimeError("zone watch down")

  worker = AsyncMock()
  _patch_handlers(monkeypatch, zone=boom, worker=worker)

  ran = await dispatcher.dispatch_closed_bar(
    "XAU:M1:1700000000",
    client=SimpleNamespace(),
    source=SimpleNamespace(),
  )

  assert ran == ["worker"]
  worker.assert_awaited_once()


@pytest.mark.asyncio
async def test_m5_bar_skips_zone_watch(monkeypatch):
  _enable_handlers(monkeypatch)
  worker = AsyncMock()
  zone = AsyncMock()
  scanner, scalp_handler = _patch_handlers(monkeypatch, zone=zone, worker=worker)

  ran = await dispatcher.dispatch_closed_bar(
    "XAU:M5:1700000000",
    client=SimpleNamespace(),
    source=SimpleNamespace(),
  )

  assert ran == ["worker"]
  zone.assert_not_awaited()
  scanner.assert_not_awaited()
  scalp_handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_m1_shares_one_bar_cache_and_clears_it(monkeypatch):
  _enable_handlers(monkeypatch)
  order: list[str] = []

  async def zone(*args, **kwargs):
    order.append("zone_watch")

  async def worker(*args, **kwargs):
    order.append("worker")

  source = SimpleNamespace(
    begin_closed_bar_cache=lambda: order.append("begin"),
    end_closed_bar_cache=lambda: order.append("end"),
  )
  _patch_handlers(monkeypatch, zone=zone, worker=worker)

  await dispatcher.dispatch_closed_bar(
    "XAU:M1:1700000000",
    client=SimpleNamespace(),
    source=source,
  )

  assert order == ["begin", "zone_watch", "worker", "end"]


@pytest.mark.asyncio
async def test_dispatch_m5_no_longer_prefetches_scanner_windows(monkeypatch):
  """The HTF prefetch only warmed windows the scanner read; nothing reads them now."""
  _enable_handlers(monkeypatch)
  order: list[str] = []
  window = AsyncMock()

  async def worker(*args, **kwargs):
    order.append("worker")

  source = SimpleNamespace(
    begin_closed_bar_cache=lambda: order.append("begin"),
    end_closed_bar_cache=lambda: order.append("end"),
    window=window,
  )
  _patch_handlers(monkeypatch, zone=AsyncMock(), worker=worker)

  await dispatcher.dispatch_closed_bar(
    "XAU:M5:1700000000",
    client=SimpleNamespace(),
    source=source,
  )

  window.assert_not_awaited()
  assert order == ["begin", "worker", "end"]


@pytest.mark.asyncio
async def test_dispatch_runs_nothing_with_auto_trade_disabled(monkeypatch):
  monkeypatch.setattr(
    dispatcher,
    "runtime_config",
    SimpleNamespace(
      runtime=SimpleNamespace(
        scanner=SimpleNamespace(enabled=True),
        auto_trade=SimpleNamespace(enabled=False),
      )
    ),
  )
  worker = AsyncMock()
  zone = AsyncMock()
  scanner, scalp_handler = _patch_handlers(monkeypatch, zone=zone, worker=worker)

  ran = await dispatcher.dispatch_closed_bar(
    "XAU:M1:1700000000",
    client=SimpleNamespace(),
    source=SimpleNamespace(),
  )

  assert ran == []
  for handler in (worker, zone, scanner, scalp_handler):
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatcher_loop_still_reconciles_legacy_thesis_claims(monkeypatch):
  """The startup thesis-claim reconcile is execution housekeeping, not scanner work."""
  _enable_handlers(monkeypatch)
  monkeypatch.setattr(
    dispatcher.runtime_config,
    "market_data",
    SimpleNamespace(ctrader_feed=SimpleNamespace(bars_channel="bars:new")),
    raising=False,
  )
  reconcile = AsyncMock()
  monkeypatch.setattr(
    "app.autotrade.worker._reconcile_legacy_mapped_thesis_claims", reconcile,
  )

  class _PubSub:
    async def subscribe(self, *_a):
      return None

    async def unsubscribe(self, *_a):
      return None

    async def close(self):
      return None

    async def listen(self):
      if False:  # pragma: no cover - an empty async generator
        yield None

  client = SimpleNamespace(pubsub=lambda: _PubSub())
  monkeypatch.setattr(dispatcher.redis_state, "get_client", lambda: client)

  await dispatcher.bar_event_dispatcher_loop()

  reconcile.assert_awaited_once_with(client)

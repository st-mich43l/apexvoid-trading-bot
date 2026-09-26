"""S14B: durable go-effective boundary, consumer-off guard, runtime audit."""

from __future__ import annotations

import pytest

from app.analysis_client import authority as auth
from app.analysis_client.authority import (
  AuthoritySnapshot,
  audit_authority_runtime,
  authorize_legacy_match,
)
from tests.test_analysis_authority_fence import Clock, MemoryStore, grant_go, make

GO_TAGS = (auth.GO_ORIGIN_TAG, "catalog:supply", "authority_epoch:1")


@pytest.fixture(autouse=True)
def _fresh_snapshot():
  auth.reset_snapshot_for_tests()
  yield
  auth.reset_snapshot_for_tests()


# ---- activation boundary ----------------------------------------------------

@pytest.mark.asyncio
async def test_boundary_is_the_end_of_the_drain_and_moves_forward_on_re_grant():
  fence, _, clock = make()
  granted = await grant_go(fence, clock, drain=10)
  boundary = int(clock.now) + 10
  assert granted.go_effective_at == boundary
  # Before the drain ends nobody may publish, so no decision carries a boundary.
  assert not (await fence.authorize_go_publication("XAU", "supply")).allowed
  clock.advance(10)
  decision = await fence.authorize_go_publication("XAU", "supply")
  assert decision.allowed and decision.boundary == boundary

  # Rollback then a fresh grant: the boundary is the *new* activation, so events
  # observed while Python owned the scope are history, not new opportunities.
  clock.advance(500)
  await fence.rollback("XAU", "supply", expected_epoch=granted.epoch, actor="owner", reason="test", drain_seconds=10)
  clock.advance(10)
  rolled = await fence._store.get("XAU", "supply")
  await fence.record_acceptance("XAU", "supply", "ev-2", approved_by="owner", ttl_seconds=3600)
  again = await fence.begin_transfer("XAU", "supply", "go", expected_epoch=rolled.epoch, actor="owner", reason="re-grant", evidence_ref="ev-2", drain_seconds=10)
  clock.advance(10)
  later = await fence.authorize_go_publication("XAU", "supply")
  assert later.allowed and later.boundary == again.drain_until > boundary


def test_only_go_records_have_a_boundary():
  rec = auth.AuthorityRecord(symbol="XAU", strategy_id="supply", owner="python", target_owner=None, epoch=0)
  assert rec.go_effective_at == 0
  going_python = auth.AuthorityRecord(symbol="XAU", strategy_id="supply", owner="draining", target_owner="python", epoch=2, drain_until=50)
  assert going_python.go_effective_at == 0


# ---- consumer disabled must not re-open a Go-owned scope --------------------

async def _snapshot_with_go_scope(*, max_age=90.0):
  fence, store, clock = make()
  await grant_go(fence, clock, drain=5)
  snapshot = AuthoritySnapshot(clock, max_age=max_age)
  await snapshot.refresh(store)
  return fence, store, clock, snapshot


@pytest.mark.asyncio
async def test_consumer_off_still_blocks_legacy_python_into_a_go_owned_scope():
  _, _, clock, snapshot = await _snapshot_with_go_scope()
  clock.advance(5)      # past the drain: Go is the effective owner
  denied = await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", consumer_enabled=False, snapshot=snapshot)
  assert not denied.allowed and denied.reason == "scope_owned_by_go_consumer_disabled"


@pytest.mark.asyncio
async def test_consumer_off_blocks_python_during_the_drain_too():
  _, _, _, snapshot = await _snapshot_with_go_scope()
  denied = await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", consumer_enabled=False, snapshot=snapshot)
  assert not denied.allowed and denied.reason == "scope_owned_by_none_consumer_disabled"


@pytest.mark.asyncio
async def test_consumer_off_leaves_other_scopes_and_symbols_python_owned():
  _, _, clock, snapshot = await _snapshot_with_go_scope()
  clock.advance(5)
  for symbol, name, direction in (("XAU", "Supply Demand", "BUY"), ("EURUSD", "Supply Demand", "SELL"), ("XAU", "Retired Name", "SELL")):
    decision = await authorize_legacy_match(symbol=symbol, strategy_name=name, direction=direction, consumer_enabled=False, snapshot=snapshot)
    assert decision.allowed, (symbol, name, direction, decision.reason)


@pytest.mark.asyncio
async def test_a_completed_rollback_reopens_python_even_with_the_consumer_off():
  fence, store, clock, snapshot = await _snapshot_with_go_scope()
  clock.advance(5)
  rec = await store.get("XAU", "supply")
  await fence.rollback("XAU", "supply", expected_epoch=rec.epoch, actor="owner", reason="back", drain_seconds=10)
  await snapshot.refresh(store)
  assert not (await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", consumer_enabled=False, snapshot=snapshot)).allowed
  clock.advance(10)
  assert (await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", consumer_enabled=False, snapshot=snapshot)).allowed


@pytest.mark.asyncio
async def test_stale_snapshot_fails_closed_and_a_refresh_recovers():
  _, store, clock, snapshot = await _snapshot_with_go_scope(max_age=30)
  clock.advance(31)
  stale = await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", consumer_enabled=False, snapshot=snapshot)
  assert not stale.allowed and stale.reason == "authority_snapshot_stale"
  await snapshot.refresh(store)
  assert snapshot.authorize_python_publication("EURUSD", ["supply"]).allowed


@pytest.mark.asyncio
async def test_required_snapshot_that_never_loaded_fails_closed():
  snapshot = AuthoritySnapshot(Clock())
  assert (await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", consumer_enabled=False, snapshot=snapshot)).allowed
  snapshot.require()
  denied = await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", consumer_enabled=False, snapshot=snapshot)
  assert not denied.allowed and denied.reason == "authority_snapshot_unavailable"


@pytest.mark.asyncio
async def test_watch_loop_requires_and_refreshes_the_process_snapshot(monkeypatch):
  import asyncio
  fence, store, clock = make()
  await grant_go(fence, clock, drain=0)
  monkeypatch.setattr(auth, "PostgresAuthorityStore", lambda: store)
  task = asyncio.create_task(auth.authority_watch_loop(interval=0.01))
  for _ in range(100):
    await asyncio.sleep(0.01)
    if auth.get_snapshot().initialized:
      break
  task.cancel()
  assert auth.get_snapshot().initialized and len(auth.get_snapshot().go_bound()) == 1
  denied = await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", consumer_enabled=False)
  assert not denied.allowed and denied.reason.startswith("scope_owned_by_")


@pytest.mark.asyncio
async def test_go_origin_publication_is_unchanged_by_the_snapshot():
  fence, _, clock = make()
  await grant_go(fence, clock, drain=5)
  clock.advance(5)
  assert (await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", tags=GO_TAGS, consumer_enabled=True, fence=fence)).allowed


# ---- runtime audit ----------------------------------------------------------

@pytest.mark.asyncio
async def test_boot_audit_records_every_boot_and_flags_a_consumer_flip():
  store = MemoryStore()
  clock = Clock()
  first = await audit_authority_runtime(store, consumer_enabled=False, mode="go_shadow", clock=clock)
  assert first["event"] == "runtime_boot" and first["previous_consumer_enabled"] is None
  again = await audit_authority_runtime(store, consumer_enabled=False, mode="go_shadow", clock=clock)
  assert again["event"] == "runtime_boot"
  flipped = await audit_authority_runtime(store, consumer_enabled=True, mode="go", clock=clock)
  assert flipped["event"] == "consumer_enabled_changed" and flipped["previous_consumer_enabled"] is False
  assert len(store.audit) == 3


@pytest.mark.asyncio
async def test_boot_audit_flags_consumer_off_while_a_scope_is_go_bound():
  fence, store, clock = make()
  await grant_go(fence, clock, drain=5)
  row = await audit_authority_runtime(store, consumer_enabled=False, mode="go", clock=clock)
  assert row["event"] == "consumer_disabled_go_scopes_fail_closed"
  assert row["go_bound_scopes"] == ["XAU:supply"]
  # Consumer on with a Go scope is the normal live state, not a warning.
  assert (await audit_authority_runtime(store, consumer_enabled=True, mode="go", clock=clock))["event"] == "consumer_enabled_changed"
  assert (await audit_authority_runtime(store, consumer_enabled=True, mode="go", clock=clock))["event"] == "runtime_boot"

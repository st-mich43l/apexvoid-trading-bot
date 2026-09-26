"""S13B authority fence: state machine, CAS, rollback, fail-closed predicate."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from app.analysis_client import authority as auth
from app.analysis_client.authority import (
  AuthorityFence,
  AuthorityRecord,
  PostgresAuthorityStore,
  TransferRefused,
  authorize_legacy_match,
  catalog_ids_for_legacy,
)


class Clock:
  def __init__(self, now: float = 1_000_000.0):
    self.now = now

  def __call__(self) -> float:
    return self.now

  def advance(self, seconds: float) -> None:
    self.now += seconds


class MemoryStore:
  """Same contract as PostgresAuthorityStore, including compare-and-set."""

  def __init__(self):
    self.rows: dict[tuple[str, str], AuthorityRecord] = {}
    self.log: list[tuple[str, str, int, int, str, str]] = []
    self.accept: dict[tuple[str, str, str], int] = {}
    self.audit: list[dict] = []

  async def get(self, symbol, strategy_id):
    return self.rows.get((symbol.upper(), strategy_id))

  async def swap(self, expected_epoch, new, *, from_owner):
    current = self.rows.get((new.symbol, new.strategy_id))
    if (current.epoch if current else 0) != expected_epoch:
      return False
    self.rows[(new.symbol, new.strategy_id)] = new
    self.log.append((new.symbol, new.strategy_id, expected_epoch, new.epoch, from_owner, new.target_owner))
    return True

  async def has_acceptance(self, symbol, strategy_id, evidence_ref, now):
    return self.accept.get((symbol.upper(), strategy_id, evidence_ref), 0) > now

  async def record_acceptance(self, symbol, strategy_id, evidence_ref, approved_by, approved_at, expires_at):
    self.accept[(symbol, strategy_id, evidence_ref)] = expires_at

  async def owned_by(self, owner):
    return [rec for rec in self.rows.values() if rec.owner == owner]

  async def active_handovers(self):
    return [rec for rec in self.rows.values() if rec.owner != "python"]

  async def record_runtime_audit(self, **fields):
    self.audit.append(fields)

  async def last_runtime_audit(self):
    return self.audit[-1] if self.audit else None


def make(clock=None, store=None, ttl=0.0):
  clock = clock or Clock()
  store = store or MemoryStore()
  return AuthorityFence(store, cache_ttl=ttl, clock=clock), store, clock


async def grant_go(fence, clock, symbol="XAU", scope="supply", expected=0, evidence="ev-1", drain=10):
  await fence.record_acceptance(symbol, scope, evidence, approved_by="owner", ttl_seconds=3600)
  return await fence.begin_transfer(symbol, scope, "go", expected_epoch=expected, actor="owner", reason="approved cutover", evidence_ref=evidence, drain_seconds=drain)


# ---- defaults ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_every_scope_defaults_to_python_and_go_is_denied():
  fence, _, _ = make()
  assert (await fence.authorize_python_publication("XAU", ["supply"])).allowed
  denied = await fence.authorize_go_publication("XAU", "supply")
  assert not denied.allowed and denied.reason == "scope_owned_by_python"


@pytest.mark.asyncio
async def test_unknown_catalog_scope_is_denied_for_go():
  fence, _, _ = make()
  assert (await fence.authorize_go_publication("XAU", "not_a_strategy")).reason == "unknown_catalog_scope"


# ---- acceptance gate ---------------------------------------------------------

@pytest.mark.asyncio
async def test_go_requires_evidence_and_an_operator_recorded_unexpired_acceptance():
  fence, store, clock = make()
  with pytest.raises(TransferRefused) as missing_evidence:
    await fence.begin_transfer("XAU", "supply", "go", expected_epoch=0, actor="a", reason="r", drain_seconds=1)
  assert missing_evidence.value.code == "acceptance_required"
  with pytest.raises(TransferRefused) as no_acceptance:
    await fence.begin_transfer("XAU", "supply", "go", expected_epoch=0, actor="a", reason="r", evidence_ref="ev", drain_seconds=1)
  assert no_acceptance.value.code == "acceptance_missing"
  assert store.rows == {}

  await fence.record_acceptance("XAU", "supply", "ev", approved_by="owner", ttl_seconds=60)
  clock.advance(61)  # acceptance expired
  with pytest.raises(TransferRefused) as expired:
    await fence.begin_transfer("XAU", "supply", "go", expected_epoch=0, actor="a", reason="r", evidence_ref="ev", drain_seconds=1)
  assert expired.value.code == "acceptance_missing"


@pytest.mark.asyncio
async def test_acceptance_is_scope_specific():
  fence, _, _ = make()
  await fence.record_acceptance("XAU", "supply", "ev", approved_by="owner", ttl_seconds=60)
  with pytest.raises(TransferRefused) as wrong_symbol:
    await fence.begin_transfer("EURUSD", "supply", "go", expected_epoch=0, actor="a", reason="r", evidence_ref="ev", drain_seconds=1)
  with pytest.raises(TransferRefused) as wrong_scope:
    await fence.begin_transfer("XAU", "demand", "go", expected_epoch=0, actor="a", reason="r", evidence_ref="ev", drain_seconds=1)
  assert wrong_symbol.value.code == wrong_scope.value.code == "acceptance_missing"


# ---- handover and exclusivity ------------------------------------------------

@pytest.mark.asyncio
async def test_handover_drains_with_no_publisher_then_go_exclusively_owns():
  fence, _, clock = make()
  rec = await grant_go(fence, clock, drain=10)
  assert rec.owner == "draining" and rec.target_owner == "go" and rec.epoch == 1

  # While draining NEITHER side may publish.
  assert not (await fence.authorize_python_publication("XAU", ["supply"])).allowed
  assert (await fence.authorize_go_publication("XAU", "supply")).reason == "scope_owned_by_none"

  clock.advance(10)
  go = await fence.authorize_go_publication("XAU", "supply", epoch=1)
  assert go.allowed and go.epoch == 1
  py = await fence.authorize_python_publication("XAU", ["supply"])
  assert not py.allowed and py.reason == "scope_owned_by_go"
  # Other scopes and symbols are untouched.
  assert (await fence.authorize_python_publication("XAU", ["demand"])).allowed
  assert (await fence.authorize_python_publication("EURUSD", ["supply"])).allowed


@pytest.mark.asyncio
async def test_stale_go_epoch_is_denied_after_rollback_and_regrant():
  fence, _, clock = make()
  await grant_go(fence, clock, drain=5)
  clock.advance(5)
  await fence.rollback("XAU", "supply", expected_epoch=1, actor="o", reason="bad shadow", drain_seconds=5)
  clock.advance(5)
  await fence.record_acceptance("XAU", "supply", "ev-2", approved_by="owner", ttl_seconds=3600)
  await fence.begin_transfer("XAU", "supply", "go", expected_epoch=2, actor="o", reason="regrant", evidence_ref="ev-2", drain_seconds=5)
  clock.advance(5)
  stale = await fence.authorize_go_publication("XAU", "supply", epoch=1)
  assert not stale.allowed and stale.reason == "stale_authority_epoch"
  assert (await fence.authorize_go_publication("XAU", "supply", epoch=3)).allowed


# ---- rollback ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_rollback_stops_go_immediately_and_python_resumes_after_drain():
  fence, _, clock = make()
  await grant_go(fence, clock, drain=5)
  clock.advance(5)
  assert (await fence.authorize_go_publication("XAU", "supply", epoch=1)).allowed

  await fence.rollback("XAU", "supply", expected_epoch=1, actor="o", reason="rollback", drain_seconds=20)
  assert not (await fence.authorize_go_publication("XAU", "supply", epoch=1)).allowed   # Go stops now
  assert not (await fence.authorize_python_publication("XAU", ["supply"])).allowed      # Python not yet
  clock.advance(20)
  assert (await fence.authorize_python_publication("XAU", ["supply"])).allowed
  assert not (await fence.authorize_go_publication("XAU", "supply")).allowed


@pytest.mark.asyncio
async def test_rollback_needs_no_acceptance_and_works_mid_drain():
  fence, _, clock = make()
  await grant_go(fence, clock, drain=100)              # still draining toward go
  rec = await fence.rollback("XAU", "supply", expected_epoch=1, actor="o", reason="abort", drain_seconds=1)
  assert rec.target_owner == "python" and rec.epoch == 2
  clock.advance(1)
  assert (await fence.authorize_python_publication("XAU", ["supply"])).allowed


@pytest.mark.asyncio
async def test_rollback_all_returns_every_go_scope_and_is_idempotent():
  fence, _, clock = make()
  await grant_go(fence, clock, scope="supply", evidence="e1", drain=5)
  await grant_go(fence, clock, scope="demand", evidence="e2", drain=5)
  await grant_go(fence, clock, symbol="EURUSD", scope="fvg", evidence="e3", drain=5)
  clock.advance(5)
  moved = await fence.rollback_all(actor="oncall", reason="incident", drain_seconds=5)
  assert {(r.symbol, r.strategy_id) for r in moved} == {("XAU", "supply"), ("XAU", "demand"), ("EURUSD", "fvg")}
  clock.advance(5)
  for symbol, scope in [("XAU", "supply"), ("XAU", "demand"), ("EURUSD", "fvg")]:
    assert (await fence.authorize_python_publication(symbol, [scope])).allowed
  assert await fence.rollback_all(actor="oncall", reason="again", drain_seconds=5) == []


# ---- guards -----------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,code", [
  ({"to_owner": "draining"}, "bad_target"),
  ({"scope": "no_such"}, "unknown_scope"),
  ({"actor": " "}, "missing_audit"),
  ({"reason": ""}, "missing_audit"),
  ({"expected": 7}, "stale_epoch"),
])
async def test_transfer_input_guards(kwargs, code):
  fence, _, _ = make()
  await fence.record_acceptance("XAU", "supply", "ev", approved_by="o", ttl_seconds=60)
  args = {"to_owner": "go", "scope": "supply", "actor": "a", "reason": "r", "expected": 0}
  args.update(kwargs)
  with pytest.raises(TransferRefused) as exc:
    await fence.begin_transfer("XAU", args["scope"], args["to_owner"], expected_epoch=args["expected"], actor=args["actor"], reason=args["reason"], evidence_ref="ev", drain_seconds=1)
  assert exc.value.code == code


@pytest.mark.asyncio
async def test_drain_must_exceed_cache_ttl_and_redundant_handovers_are_refused():
  fence, _, clock = make(ttl=2.0)
  await fence.record_acceptance("XAU", "supply", "ev", approved_by="o", ttl_seconds=3600)
  with pytest.raises(TransferRefused) as short:
    await fence.begin_transfer("XAU", "supply", "go", expected_epoch=0, actor="a", reason="r", evidence_ref="ev", drain_seconds=5.9)
  assert short.value.code == "drain_too_short"
  await fence.begin_transfer("XAU", "supply", "go", expected_epoch=0, actor="a", reason="r", evidence_ref="ev", drain_seconds=6)
  with pytest.raises(TransferRefused) as again:
    await fence.begin_transfer("XAU", "supply", "go", expected_epoch=1, actor="a", reason="r", evidence_ref="ev", drain_seconds=6)
  assert again.value.code == "handover_in_progress"
  clock.advance(6)
  with pytest.raises(TransferRefused) as owner:
    await fence.begin_transfer("XAU", "supply", "go", expected_epoch=1, actor="a", reason="r", evidence_ref="ev", drain_seconds=6)
  assert owner.value.code == "already_owner"


@pytest.mark.asyncio
async def test_read_cache_bounds_staleness_and_local_transfer_invalidates_it():
  clock = Clock()
  mono = {"t": 0.0}
  store = MemoryStore()
  fence = AuthorityFence(store, cache_ttl=2.0, clock=clock, monotonic=lambda: mono["t"])
  assert (await fence.record("XAU", "supply")).owner == "python"
  # A different process moves the scope; this one still serves the cached row...
  store.rows[("XAU", "supply")] = AuthorityRecord("XAU", "supply", "go", None, 5)
  assert (await fence.record("XAU", "supply")).owner == "python"
  mono["t"] = 2.5  # ...but never for longer than the TTL.
  assert (await fence.record("XAU", "supply")).owner == "go"


# ---- catalog identity ---------------------------------------------------------

def test_catalog_ids_match_analysis_config_exactly():
  config = yaml.safe_load((Path(__file__).resolve().parents[2] / "config" / "analysis.yml").read_text())
  assert set(config["analysis"]["strategies"]) == set(auth.CATALOG_STRATEGY_IDS)


def test_every_mapped_legacy_target_is_a_catalog_id_and_retired_names_are_unfenced():
  from app.autotrade.strategy_names import STRATEGY_NAMES
  for entry in STRATEGY_NAMES:
    ids = catalog_ids_for_legacy(entry.canonical, "BUY") + catalog_ids_for_legacy(entry.canonical, "SELL")
    assert set(ids) <= auth.CATALOG_STRATEGY_IDS, entry.canonical
    if entry.retired:
      assert ids == (), f"retired {entry.canonical} must not be fenced"


def test_supply_demand_split_follows_direction_and_unknown_side_fences_both():
  assert catalog_ids_for_legacy("Supply Demand", "SELL") == ("supply",)
  assert catalog_ids_for_legacy("Supply Demand", "BUY") == ("demand",)
  assert set(catalog_ids_for_legacy("Supply Demand", None)) == {"supply", "demand"}
  assert catalog_ids_for_legacy("Break & Retest", "BUY") == ("trendline",)
  assert catalog_ids_for_legacy("Range Box Scalp", "BUY") == ()


# ---- the single publication predicate ---------------------------------------

class ExplodingFence:
  def __getattr__(self, name):
    raise AssertionError(f"fence must not be consulted ({name})")


class BrokenFence:
  async def authorize_python_publication(self, *a, **k):
    raise ConnectionError("db down")

  async def authorize_go_publication(self, *a, **k):
    raise ConnectionError("db down")


@pytest.mark.asyncio
async def test_predicate_makes_no_fence_read_while_consumer_is_disabled():
  auth.reset_snapshot_for_tests()
  decision = await authorize_legacy_match(
    symbol="XAU", strategy_name="Supply Demand", direction="SELL", consumer_enabled=False, fence=ExplodingFence(),
  )
  # No per-plan fence/DB read, and no blind allow either: with no Go-owned scope
  # recorded in the (un-required) snapshot Python keeps its default authority.
  assert decision.allowed and decision.reason == "authority_snapshot_uninitialized_python_default"


@pytest.mark.asyncio
async def test_predicate_fails_closed_when_the_fence_is_unreadable():
  for tags in ((), (auth.GO_ORIGIN_TAG, "catalog:supply", "authority_epoch:1")):
    decision = await authorize_legacy_match(
      symbol="XAU", strategy_name="Supply Demand", direction="SELL", tags=tags, consumer_enabled=True, fence=BrokenFence(),
    )
    assert not decision.allowed and decision.reason.startswith("authority_unavailable")


@pytest.mark.asyncio
async def test_predicate_routes_by_origin_and_denies_the_wrong_publisher():
  fence, _, clock = make()
  await grant_go(fence, clock, drain=5)
  clock.advance(5)
  legacy = await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", consumer_enabled=True, fence=fence)
  assert not legacy.allowed and legacy.reason == "scope_owned_by_go"
  go_tags = (auth.GO_ORIGIN_TAG, "catalog:supply", "authority_epoch:1")
  assert (await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", tags=go_tags, consumer_enabled=True, fence=fence)).allowed
  # Unfenced legacy scope, retired name and BUY-side demand all keep publishing.
  assert (await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="BUY", consumer_enabled=True, fence=fence)).allowed
  assert (await authorize_legacy_match(symbol="XAU", strategy_name="Range Box Scalp", direction="BUY", consumer_enabled=True, fence=fence)).reason == "no_catalog_scope"


@pytest.mark.asyncio
@pytest.mark.parametrize("tags,reason", [
  ((auth.GO_ORIGIN_TAG,), "go_origin_missing_scope"),
  ((auth.GO_ORIGIN_TAG, "catalog:supply"), "go_origin_missing_epoch"),
  ((auth.GO_ORIGIN_TAG, "catalog:supply", "catalog:demand", "authority_epoch:1"), "go_origin_missing_scope"),
])
async def test_go_origin_must_name_exactly_one_scope_and_epoch(tags, reason):
  fence, _, _ = make()
  decision = await authorize_legacy_match(symbol="XAU", strategy_name=None, direction=None, tags=tags, consumer_enabled=True, fence=fence)
  assert not decision.allowed and decision.reason == reason


@pytest.mark.asyncio
async def test_go_origin_match_is_denied_even_with_consumer_flag_off():
  """A stray Go-tagged match must never publish unfenced."""
  fence, _, _ = make()
  decision = await authorize_legacy_match(
    symbol="XAU", strategy_name=None, direction=None, tags=(auth.GO_ORIGIN_TAG, "catalog:supply", "authority_epoch:1"),
    consumer_enabled=False, fence=fence,
  )
  assert not decision.allowed


# ---- Postgres store (real CAS, audit log, concurrency) ------------------------

@pytest.mark.asyncio
async def test_postgres_store_round_trip_audit_log_and_acceptance(sql):
  from app.persistence import store as db_store
  await db_store.init_db()
  clock = Clock()
  fence = AuthorityFence(PostgresAuthorityStore(), cache_ttl=0.0, clock=clock)
  rec = await grant_go(fence, clock, drain=5)
  assert rec.epoch == 1
  clock.advance(5)
  assert (await fence.authorize_go_publication("XAU", "supply", epoch=1)).allowed
  await fence.rollback("XAU", "supply", expected_epoch=1, actor="oncall", reason="rollback", drain_seconds=5)
  rows = await sql.fetch("SELECT from_epoch, to_epoch, from_owner, to_owner, actor FROM analysis_authority_transitions ORDER BY transition_id")
  assert [dict(r) for r in rows] == [
    {"from_epoch": 0, "to_epoch": 1, "from_owner": "python", "to_owner": "go", "actor": "owner"},
    {"from_epoch": 1, "to_epoch": 2, "from_owner": "go", "to_owner": "python", "actor": "oncall"},
  ]
  assert (await PostgresAuthorityStore().get("xau", "supply")).epoch == 2


@pytest.mark.asyncio
async def test_postgres_compare_and_set_lets_exactly_one_concurrent_handover_win(sql):
  from app.persistence import store as db_store
  await db_store.init_db()
  clock = Clock()
  fence = AuthorityFence(PostgresAuthorityStore(), cache_ttl=0.0, clock=clock)
  await fence.record_acceptance("XAU", "supply", "ev", approved_by="o", ttl_seconds=3600)

  async def attempt(actor):
    try:
      await fence.begin_transfer("XAU", "supply", "go", expected_epoch=0, actor=actor, reason="r", evidence_ref="ev", drain_seconds=1)
      return "won"
    except TransferRefused as exc:
      return exc.code

  results = await asyncio.gather(*(attempt(f"actor-{i}") for i in range(8)))
  assert results.count("won") == 1
  assert set(results) - {"won"} <= {"stale_epoch", "handover_in_progress"}
  count = await sql.row("SELECT count(*) AS n FROM analysis_authority_transitions")
  assert count["n"] == 1


@pytest.mark.asyncio
async def test_postgres_rejects_inconsistent_owner_target_rows(sql):
  from app.persistence import store as db_store
  await db_store.init_db()
  with pytest.raises(Exception):
    async with db_store._connect() as db:
      await db.execute(
        "INSERT INTO analysis_authority_scopes (symbol, strategy_id, owner, target_owner, epoch, updated_at, updated_by, reason) "
        "VALUES ('XAU','supply','draining',NULL,1,0,'a','r')"
      )

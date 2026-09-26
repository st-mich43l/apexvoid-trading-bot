"""S13B fenced technical-authority switch, per (symbol, catalog strategy).

Kafka publication from the Go Analysis Engine is *not* a cutover. Who may
create an executable TradePlan for a scope is a separate, durable, fenced
decision recorded here:

* absent row  -> Python owns the scope (epoch 0). This is the default for
  every scope, so deploying this module changes nothing.
* handover    -> ``python -> draining -> go`` (and back). While ``draining``
  *neither* side may publish; the target owner takes effect only after
  ``drain_until``. The drain window is longer than the in-process cache TTL and
  a publish critical section, so a process that read the old owner cannot
  publish after the new owner has started. Every handover advances a monotonic
  ``epoch`` and is compare-and-set on the caller's ``expected_epoch``.
* Go ownership needs an operator-recorded, unexpired acceptance for the exact
  scope and evidence. Nothing in this code base records one.
* rollback    -> always allowed, needs no acceptance, is a normal handover to
  ``python`` (Go stops immediately, Python resumes after the drain).

Only the *publication of an executable plan* is fenced. Analysis-only
observations, manual trading and management of already-open positions are
unaffected: ownership is a property of who may create a plan, not of who
manages one.

The live fence is consulted only when the Go consumer is enabled
(``analysis.technical_authority.consumer_enabled``). Turning the consumer off
is *not* a rollback: a scope still recorded as Go-owned (or mid-handover) stays
closed to legacy Python plans, enforced without a per-plan DB read by a
periodically refreshed ``AuthoritySnapshot`` that fails closed when stale (S14B).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

OWNER_PYTHON = "python"
OWNER_GO = "go"
OWNER_DRAINING = "draining"
OWNER_NONE = "none"  # effective owner while draining: nobody may publish

# Marks a StrategyMatch that the Go adapter produced. Legacy Python matches
# never carry it, so origin is decided from data, not from the caller.
GO_ORIGIN_TAG = "authority:go"
CATALOG_TAG = "catalog:"       # tag value: catalog strategy id of a Go match
EPOCH_TAG = "authority_epoch:"  # tag value: fence epoch the Go match was accepted under

CACHE_TTL_SECONDS = 2.0
DEFAULT_DRAIN_SECONDS = 30

# Catalog identity is owned by config/analysis.yml `analysis.strategies`; a
# test keeps this set equal to it.
CATALOG_STRATEGY_IDS = frozenset({
  "key_level", "confluence_zone", "supply", "demand", "order_block", "fvg",
  "ifvg", "crt", "flip_zone", "session_level", "trendline", "range_edge",
  "box_breakout", "momentum_ride", "snap_back", "liquidity_sweep",
  "range_sweep", "impulse_pullback", "scalp_breakout_retest",
})

# Legacy canonical name (casefolded) -> catalog id, or a (buy_id, sell_id)
# pair for the one legacy detector the catalog split by direction.
_LEGACY_TO_CATALOG: dict[str, str | tuple[str, str]] = {
  "key level": "key_level",
  "confluence zone": "confluence_zone",
  "supply demand": ("demand", "supply"),
  "order block": "order_block",
  "fvg": "fvg",
  "ifvg": "ifvg",
  "crt": "crt",
  "flip zone": "flip_zone",
  "session level": "session_level",
  "trendline": "trendline",
  "break & retest": "trendline",  # folded into Trendline by the S2 catalog
  "range edge scalp": "range_edge",
  "box breakout": "box_breakout",
  "momentum ride": "momentum_ride",
  "snap-back": "snap_back",
  "fade scalp": "liquidity_sweep",
  "range sweep scalp": "range_sweep",
  "impulse pullback scalp": "impulse_pullback",
  "breakout retest scalp": "scalp_breakout_retest",
}


def catalog_ids_for_legacy(strategy_name: str | None, direction: str | None) -> tuple[str, ...]:
  """Catalog scopes a legacy strategy name publishes into; () if none.

  Retired legacy names (Range Box Scalp, Trend Pullback, ...) have no Go
  equivalent and are therefore never fenced.
  """
  mapped = _LEGACY_TO_CATALOG.get(str(strategy_name or "").strip().casefold())
  if mapped is None:
    return ()
  if isinstance(mapped, tuple):
    side = str(direction or "").strip().upper()
    if side == "BUY":
      return (mapped[0],)
    if side == "SELL":
      return (mapped[1],)
    return mapped  # unknown side: fence both, fail closed
  return (mapped,)


class AuthorityError(Exception):
  """Base class for fence failures."""


class TransferRefused(AuthorityError):
  def __init__(self, code: str, message: str):
    super().__init__(f"{code}: {message}")
    self.code = code


@dataclass(frozen=True, slots=True)
class AuthorityRecord:
  symbol: str
  strategy_id: str
  owner: str
  target_owner: str | None
  epoch: int
  drain_until: int = 0
  updated_at: int = 0
  updated_by: str = ""
  reason: str = ""
  evidence_ref: str | None = None

  def effective_owner(self, now: float) -> str:
    """Who may publish *right now*; ``none`` while a handover drains."""
    if self.owner != OWNER_DRAINING:
      return self.owner
    return self.target_owner if now >= self.drain_until else OWNER_NONE  # type: ignore[return-value]

  @property
  def go_effective_at(self) -> int:
    """The durable live-activation boundary for Go: the instant this record
    made Go the effective publisher. A creation whose technical time is before
    it is history, not a new opportunity (S14B). For a direct ``go`` row (only
    ever written by hand, never by begin_transfer) the row's own update time."""
    if self.owner == OWNER_DRAINING and self.target_owner == OWNER_GO:
      return self.drain_until
    if self.owner == OWNER_GO:
      return self.updated_at
    return 0


def default_record(symbol: str, strategy_id: str) -> AuthorityRecord:
  return AuthorityRecord(symbol.upper(), strategy_id, OWNER_PYTHON, None, 0)


@dataclass(frozen=True, slots=True)
class AuthorityDecision:
  allowed: bool
  reason: str
  owner: str = OWNER_PYTHON
  epoch: int = 0
  scopes: tuple[str, ...] = ()
  # Go decisions only: the activation boundary of the record that allowed it.
  boundary: int = 0


class AuthorityStore(Protocol):
  async def get(self, symbol: str, strategy_id: str) -> AuthorityRecord | None: ...
  async def swap(self, expected_epoch: int, new: AuthorityRecord, *, from_owner: str) -> bool: ...
  async def has_acceptance(self, symbol: str, strategy_id: str, evidence_ref: str, now: int) -> bool: ...
  async def record_acceptance(self, symbol: str, strategy_id: str, evidence_ref: str, approved_by: str, approved_at: int, expires_at: int) -> None: ...
  async def owned_by(self, owner: str) -> list[AuthorityRecord]: ...
  async def active_handovers(self) -> list[AuthorityRecord]: ...
  async def record_runtime_audit(self, **fields: Any) -> None: ...
  async def last_runtime_audit(self) -> dict[str, Any] | None: ...


class AuthorityFence:
  """State machine + read cache over an :class:`AuthorityStore`."""

  def __init__(
    self,
    store: AuthorityStore,
    *,
    cache_ttl: float = CACHE_TTL_SECONDS,
    clock: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
  ):
    self._store = store
    self._ttl = cache_ttl
    self._clock = clock
    self._monotonic = monotonic
    self._cache: dict[tuple[str, str], tuple[float, AuthorityRecord]] = {}

  @property
  def min_drain_seconds(self) -> float:
    # A reader may hold a stale record for up to one TTL; require several so
    # the old owner is provably quiet before the new owner starts.
    return 3 * self._ttl

  async def record(self, symbol: str, strategy_id: str) -> AuthorityRecord:
    key = (symbol.upper(), strategy_id)
    hit = self._cache.get(key)
    if hit is not None and self._monotonic() - hit[0] < self._ttl:
      return hit[1]
    fresh = await self._store.get(*key) or default_record(*key)
    self._cache[key] = (self._monotonic(), fresh)
    return fresh

  def invalidate(self, symbol: str | None = None, strategy_id: str | None = None) -> None:
    if symbol is None:
      self._cache.clear()
    else:
      self._cache.pop((symbol.upper(), strategy_id or ""), None)

  async def effective_owner(self, symbol: str, strategy_id: str) -> str:
    return (await self.record(symbol, strategy_id)).effective_owner(self._clock())

  async def authorize_python_publication(
    self, symbol: str, scopes: Iterable[str],
  ) -> AuthorityDecision:
    """A legacy (Python-detected) plan may publish only into Python scopes."""
    scopes = tuple(scopes)
    for scope in scopes:
      rec = await self.record(symbol, scope)
      owner = rec.effective_owner(self._clock())
      if owner != OWNER_PYTHON:
        return AuthorityDecision(False, f"scope_owned_by_{owner}", owner, rec.epoch, scopes)
    return AuthorityDecision(True, "python_owns_scope", OWNER_PYTHON, 0, scopes)

  async def authorize_go_publication(
    self, symbol: str, strategy_id: str, *, epoch: int | None = None,
  ) -> AuthorityDecision:
    """A Go-derived plan may publish only while Go owns the scope.

    ``epoch``: the epoch under which the opportunity was accepted; a handover
    since then (including rollback and re-grant) invalidates it.
    """
    if strategy_id not in CATALOG_STRATEGY_IDS:
      return AuthorityDecision(False, "unknown_catalog_scope", OWNER_NONE, 0, (strategy_id,))
    rec = await self.record(symbol, strategy_id)
    owner = rec.effective_owner(self._clock())
    if owner != OWNER_GO:
      return AuthorityDecision(False, f"scope_owned_by_{owner}", owner, rec.epoch, (strategy_id,))
    if epoch is not None and epoch != rec.epoch:
      return AuthorityDecision(False, "stale_authority_epoch", owner, rec.epoch, (strategy_id,))
    return AuthorityDecision(True, "go_owns_scope", OWNER_GO, rec.epoch, (strategy_id,), boundary=rec.go_effective_at)

  async def begin_transfer(
    self,
    symbol: str,
    strategy_id: str,
    to_owner: str,
    *,
    expected_epoch: int,
    actor: str,
    reason: str,
    evidence_ref: str | None = None,
    drain_seconds: float = DEFAULT_DRAIN_SECONDS,
  ) -> AuthorityRecord:
    symbol = symbol.upper()
    if to_owner not in {OWNER_PYTHON, OWNER_GO}:
      raise TransferRefused("bad_target", f"target owner must be python or go, got {to_owner!r}")
    if strategy_id not in CATALOG_STRATEGY_IDS:
      raise TransferRefused("unknown_scope", f"{strategy_id!r} is not a catalog strategy")
    if not actor.strip() or not reason.strip():
      raise TransferRefused("missing_audit", "actor and reason are required")
    if drain_seconds < self.min_drain_seconds:
      raise TransferRefused("drain_too_short", f"drain must be >= {self.min_drain_seconds}s (3x cache TTL)")
    # Always read the store, never the cache, for a decision that mutates.
    current = await self._store.get(symbol, strategy_id) or default_record(symbol, strategy_id)
    if current.epoch != expected_epoch:
      raise TransferRefused("stale_epoch", f"expected epoch {expected_epoch}, current {current.epoch}")
    now = int(self._clock())
    effective = current.effective_owner(now)
    if effective == to_owner:
      raise TransferRefused("already_owner", f"{to_owner} already owns {symbol}/{strategy_id}")
    if current.owner == OWNER_DRAINING and current.target_owner == to_owner:
      raise TransferRefused("handover_in_progress", f"{symbol}/{strategy_id} is already draining toward {to_owner}")
    if to_owner == OWNER_GO:
      if not evidence_ref or not evidence_ref.strip():
        raise TransferRefused("acceptance_required", "moving a scope to Go requires an evidence reference")
      if not await self._store.has_acceptance(symbol, strategy_id, evidence_ref, now):
        raise TransferRefused("acceptance_missing", f"no unexpired operator acceptance for {symbol}/{strategy_id} evidence {evidence_ref!r}")
    new = AuthorityRecord(
      symbol=symbol, strategy_id=strategy_id, owner=OWNER_DRAINING, target_owner=to_owner,
      epoch=current.epoch + 1, drain_until=now + int(drain_seconds), updated_at=now,
      updated_by=actor, reason=reason, evidence_ref=evidence_ref,
    )
    if not await self._store.swap(expected_epoch, new, from_owner=effective):
      raise TransferRefused("stale_epoch", "concurrent handover won the compare-and-set")
    self.invalidate(symbol, strategy_id)
    return new

  async def rollback(
    self, symbol: str, strategy_id: str, *, expected_epoch: int, actor: str, reason: str,
    drain_seconds: float = DEFAULT_DRAIN_SECONDS,
  ) -> AuthorityRecord:
    return await self.begin_transfer(
      symbol, strategy_id, OWNER_PYTHON, expected_epoch=expected_epoch, actor=actor,
      reason=reason, drain_seconds=drain_seconds,
    )

  async def rollback_all(
    self, *, actor: str, reason: str, drain_seconds: float = DEFAULT_DRAIN_SECONDS,
  ) -> list[AuthorityRecord]:
    """Emergency: return every Go-owned (or Go-bound) scope to Python."""
    moved: list[AuthorityRecord] = []
    candidates = [*await self._store.owned_by(OWNER_GO), *await self._store.owned_by(OWNER_DRAINING)]
    for rec in candidates:
      if rec.effective_owner(self._clock()) == OWNER_PYTHON or rec.target_owner == OWNER_PYTHON:
        continue
      moved.append(await self.rollback(
        rec.symbol, rec.strategy_id, expected_epoch=rec.epoch, actor=actor,
        reason=reason, drain_seconds=drain_seconds,
      ))
    return moved

  async def record_acceptance(
    self, symbol: str, strategy_id: str, evidence_ref: str, *, approved_by: str, ttl_seconds: int,
  ) -> None:
    """Operator-only. No automated path in this code base calls this."""
    if not approved_by.strip() or not evidence_ref.strip() or ttl_seconds <= 0:
      raise TransferRefused("bad_acceptance", "approver, evidence and a positive ttl are required")
    now = int(self._clock())
    await self._store.record_acceptance(symbol.upper(), strategy_id, evidence_ref, approved_by, now, now + ttl_seconds)


class PostgresAuthorityStore:
  """Postgres implementation; ``swap`` is the compare-and-set."""

  @staticmethod
  def _row(row) -> AuthorityRecord:
    return AuthorityRecord(
      symbol=row["symbol"], strategy_id=row["strategy_id"], owner=row["owner"],
      target_owner=row["target_owner"], epoch=int(row["epoch"]), drain_until=int(row["drain_until"]),
      updated_at=int(row["updated_at"]), updated_by=row["updated_by"], reason=row["reason"],
      evidence_ref=row["evidence_ref"],
    )

  async def get(self, symbol: str, strategy_id: str) -> AuthorityRecord | None:
    from app.persistence import store
    async with store._connect() as db:
      row = await db.fetchrow(
        "SELECT * FROM analysis_authority_scopes WHERE symbol = $1 AND strategy_id = $2",
        symbol.upper(), strategy_id,
      )
    return self._row(row) if row else None

  async def swap(self, expected_epoch: int, new: AuthorityRecord, *, from_owner: str) -> bool:
    from app.persistence import store
    async with store._connect() as db:
      async with db.transaction():
        if expected_epoch == 0:
          won = await db.fetchval(
            """
            INSERT INTO analysis_authority_scopes (
              symbol, strategy_id, owner, target_owner, epoch, drain_until,
              updated_at, updated_by, reason, evidence_ref
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (symbol, strategy_id) DO NOTHING RETURNING epoch
            """,
            new.symbol, new.strategy_id, new.owner, new.target_owner, new.epoch,
            new.drain_until, new.updated_at, new.updated_by, new.reason, new.evidence_ref,
          )
        else:
          won = await db.fetchval(
            """
            UPDATE analysis_authority_scopes
            SET owner=$3, target_owner=$4, epoch=$5, drain_until=$6, updated_at=$7,
                updated_by=$8, reason=$9, evidence_ref=$10
            WHERE symbol=$1 AND strategy_id=$2 AND epoch=$11
            RETURNING epoch
            """,
            new.symbol, new.strategy_id, new.owner, new.target_owner, new.epoch,
            new.drain_until, new.updated_at, new.updated_by, new.reason, new.evidence_ref,
            expected_epoch,
          )
        if won is None:
          return False
        await db.execute(
          """
          INSERT INTO analysis_authority_transitions (
            symbol, strategy_id, from_epoch, to_epoch, from_owner, to_owner,
            actor, reason, evidence_ref, at
          ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
          """,
          new.symbol, new.strategy_id, expected_epoch, new.epoch, from_owner,
          new.target_owner, new.updated_by, new.reason, new.evidence_ref, new.updated_at,
        )
    return True

  async def has_acceptance(self, symbol: str, strategy_id: str, evidence_ref: str, now: int) -> bool:
    from app.persistence import store
    async with store._connect() as db:
      return bool(await db.fetchval(
        """
        SELECT 1 FROM analysis_authority_acceptance
        WHERE symbol=$1 AND strategy_id=$2 AND evidence_ref=$3 AND expires_at > $4
        """,
        symbol.upper(), strategy_id, evidence_ref, now,
      ))

  async def record_acceptance(self, symbol, strategy_id, evidence_ref, approved_by, approved_at, expires_at) -> None:
    from app.persistence import store
    async with store._connect() as db:
      await db.execute(
        """
        INSERT INTO analysis_authority_acceptance
          (symbol, strategy_id, evidence_ref, approved_by, approved_at, expires_at)
        VALUES ($1,$2,$3,$4,$5,$6)
        ON CONFLICT (symbol, strategy_id, evidence_ref)
        DO UPDATE SET approved_by=EXCLUDED.approved_by, approved_at=EXCLUDED.approved_at,
                      expires_at=EXCLUDED.expires_at
        """,
        symbol, strategy_id, evidence_ref, approved_by, approved_at, expires_at,
      )

  async def owned_by(self, owner: str) -> list[AuthorityRecord]:
    from app.persistence import store
    async with store._connect() as db:
      rows = await db.fetch(
        "SELECT * FROM analysis_authority_scopes WHERE owner = $1 ORDER BY symbol, strategy_id", owner,
      )
    return [self._row(row) for row in rows]

  async def active_handovers(self) -> list[AuthorityRecord]:
    """Every row that is not plainly Python-owned (go, or draining either way)."""
    from app.persistence import store
    async with store._connect() as db:
      rows = await db.fetch(
        "SELECT * FROM analysis_authority_scopes WHERE owner <> 'python' ORDER BY symbol, strategy_id",
      )
    return [self._row(row) for row in rows]

  async def record_runtime_audit(
    self, *, at: int, event: str, consumer_enabled: bool, mode: str,
    previous_consumer_enabled: bool | None, go_bound_scopes: list[str], detail: str,
  ) -> None:
    import json
    from app.persistence import store
    async with store._connect() as db:
      await db.execute(
        """
        INSERT INTO analysis_authority_runtime_audit
          (at, event, consumer_enabled, mode, previous_consumer_enabled, go_bound_scopes, detail)
        VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7)
        """,
        at, event, consumer_enabled, mode, previous_consumer_enabled, json.dumps(go_bound_scopes), detail,
      )

  async def last_runtime_audit(self) -> dict[str, Any] | None:
    from app.persistence import store
    async with store._connect() as db:
      row = await db.fetchrow("SELECT * FROM analysis_authority_runtime_audit ORDER BY audit_id DESC LIMIT 1")
    return dict(row) if row else None


async def audit_authority_runtime(
  store: AuthorityStore, *, consumer_enabled: bool, mode: str, clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
  """Append the boot-time authority audit row and return what was recorded.

  ``consumer_enabled`` is a config flag, not fence state, so flipping it is
  invisible to the per-scope transition log. This makes it visible: every boot
  writes a row, a change from the previous boot is a distinct
  ``consumer_enabled_changed`` event, and a consumer that is off while a scope is
  still Go-owned/mid-handover is flagged ``consumer_disabled_go_scopes_fail_closed``
  (those scopes then publish from *nobody*: the snapshot guard denies legacy
  Python plans and no Go events are consumed). The correct way to give a scope
  back to Python is the fenced rollback, never the flag.
  """
  now = int(clock())
  previous = await store.last_runtime_audit()
  before = None if previous is None else bool(previous["consumer_enabled"])
  bound = sorted(f"{r.symbol}:{r.strategy_id}" for r in await store.active_handovers())
  if not consumer_enabled and bound:
    event = "consumer_disabled_go_scopes_fail_closed"
    detail = "consumer is off but scopes are not Python-owned; legacy Python plans stay blocked until a fenced rollback completes"
  elif before is not None and before != consumer_enabled:
    event = "consumer_enabled_changed"
    detail = f"consumer_enabled {before} -> {consumer_enabled}"
  else:
    event = "runtime_boot"
    detail = ""
  row = {
    "at": now, "event": event, "consumer_enabled": consumer_enabled, "mode": mode,
    "previous_consumer_enabled": before, "go_bound_scopes": bound, "detail": detail,
  }
  await store.record_runtime_audit(**row)
  return row


SNAPSHOT_MAX_AGE_SECONDS = 90.0
SNAPSHOT_REFRESH_SECONDS = 15.0


class AuthoritySnapshot:
  """Process-local view of every non-Python scope, for the consumer-off case.

  With the Go consumer disabled there is no live fence read on the publish hot
  path, so a scope still recorded as Go-owned (or mid-handover) would let a
  legacy Python plan through: turning the consumer off is *not* a rollback. A
  periodically refreshed snapshot closes that hole without a DB read per plan.
  It is stale-safe: past ``max_age`` without a successful refresh it denies
  Python publication into every catalog-mapped scope (fail closed).
  """

  def __init__(self, clock: Callable[[], float] = time.time, *, max_age: float = SNAPSHOT_MAX_AGE_SECONDS):
    self._clock = clock
    self._max_age = max_age
    self._records: dict[tuple[str, str], AuthorityRecord] = {}
    self._loaded_at: float | None = None
    self._required = False

  @property
  def initialized(self) -> bool:
    return self._loaded_at is not None

  def require(self) -> None:
    """Called once the watch loop is running in production: from then on a
    snapshot that never loaded is as unsafe as a stale one (fail closed).
    Un-required snapshots (unit tests, one-shot CLIs) default to Python."""
    self._required = True

  async def refresh(self, store: AuthorityStore) -> int:
    rows = await store.active_handovers()
    self._records = {(r.symbol.upper(), r.strategy_id): r for r in rows}
    self._loaded_at = self._clock()
    return len(rows)

  def go_bound(self) -> list[AuthorityRecord]:
    now = self._clock()
    return [r for r in self._records.values() if r.effective_owner(now) != OWNER_PYTHON]

  def authorize_python_publication(self, symbol: str, scopes: Iterable[str]) -> AuthorityDecision:
    scopes = tuple(scopes)
    if self._loaded_at is None:
      if self._required:
        return AuthorityDecision(False, "authority_snapshot_unavailable", OWNER_NONE, 0, scopes)
      return AuthorityDecision(True, "authority_snapshot_uninitialized_python_default", scopes=scopes)
    if self._clock() - self._loaded_at > self._max_age:
      return AuthorityDecision(False, "authority_snapshot_stale", OWNER_NONE, 0, scopes)
    now = self._clock()
    for scope in scopes:
      rec = self._records.get((symbol.upper(), scope))
      if rec is not None and rec.effective_owner(now) != OWNER_PYTHON:
        return AuthorityDecision(False, f"scope_owned_by_{rec.effective_owner(now)}_consumer_disabled", rec.effective_owner(now), rec.epoch, scopes)
    return AuthorityDecision(True, "python_owns_scope_snapshot", OWNER_PYTHON, 0, scopes)


_default_snapshot = AuthoritySnapshot()


def get_snapshot() -> AuthoritySnapshot:
  return _default_snapshot


def reset_snapshot_for_tests() -> None:
  global _default_snapshot
  _default_snapshot = AuthoritySnapshot()


async def refresh_authority_snapshot(store: AuthorityStore | None = None) -> int:
  """Boot-time and loop refresh; raises on DB failure so boot cannot proceed
  blind (a Go-owned scope must never be discovered *after* Python publishes)."""
  return await get_snapshot().refresh(store or PostgresAuthorityStore())


async def authority_watch_loop(interval: float = SNAPSHOT_REFRESH_SECONDS) -> None:
  """Supervised background task: keep the snapshot fresh while running."""
  import asyncio
  import logging
  log = logging.getLogger(__name__)
  get_snapshot().require()
  announced: tuple[str, ...] | None = None
  while True:
    try:
      await refresh_authority_snapshot()
      bound = tuple(sorted(f"{r.symbol}:{r.strategy_id}:{r.effective_owner(time.time())}" for r in get_snapshot().go_bound()))
      if bound != announced:            # log transitions, not every tick
        announced = bound
        log.warning("technical-authority: non-Python scopes now %s", list(bound) or "none")
    except Exception:  # noqa: BLE001 - stale snapshot fails closed by itself
      log.exception("technical-authority snapshot refresh failed")
    await asyncio.sleep(interval)


_default_fence: AuthorityFence | None = None


def get_fence() -> AuthorityFence:
  global _default_fence
  if _default_fence is None:
    _default_fence = AuthorityFence(PostgresAuthorityStore())
  return _default_fence


def reset_fence_for_tests() -> None:
  global _default_fence
  _default_fence = None


async def authorize_legacy_match(
  *,
  symbol: str,
  strategy_name: str | None,
  direction: str | None,
  tags: Iterable[str] = (),
  consumer_enabled: bool,
  fence: AuthorityFence | None = None,
  snapshot: AuthoritySnapshot | None = None,
) -> AuthorityDecision:
  """Single publication predicate for an executable TradePlan.

  * consumer disabled           -> Python owns every scope the fence snapshot
                                   does not show as Go-owned/mid-handover.
  * match carries GO_ORIGIN_TAG -> must be Go-owned at publish time.
  * otherwise (legacy detector) -> every mapped scope must be Python-owned.
  Any failure to read the fence denies publication: two publishers is worse
  than a skipped plan.
  """
  tag_set = tuple(tags)
  is_go = GO_ORIGIN_TAG in tag_set
  if not consumer_enabled and not is_go:
    # No per-plan DB read here, but never a blind allow: a scope still
    # recorded as Go-owned/mid-handover stays closed to legacy Python plans
    # even though the consumer is off (see AuthoritySnapshot).
    scopes = catalog_ids_for_legacy(strategy_name, direction)
    if not scopes:
      return AuthorityDecision(True, "no_catalog_scope")
    return (snapshot or get_snapshot()).authorize_python_publication(symbol, scopes)
  fence = fence or get_fence()
  try:
    if is_go:
      # The Go adapter names its own scope and accepted epoch in tags, so
      # origin and scope come from data, never from the legacy name mapping.
      ids = tuple(t[len(CATALOG_TAG):] for t in tag_set if t.startswith(CATALOG_TAG))
      if len(ids) != 1:
        return AuthorityDecision(False, "go_origin_missing_scope")
      epochs = [t[len(EPOCH_TAG):] for t in tag_set if t.startswith(EPOCH_TAG)]
      epoch = int(epochs[0]) if len(epochs) == 1 and epochs[0].isdigit() else None
      if epoch is None:
        return AuthorityDecision(False, "go_origin_missing_epoch", scopes=ids)
      return await fence.authorize_go_publication(symbol, ids[0], epoch=epoch)
    scopes = catalog_ids_for_legacy(strategy_name, direction)
    if not scopes:
      return AuthorityDecision(True, "no_catalog_scope")
    return await fence.authorize_python_publication(symbol, scopes)
  except Exception as exc:  # noqa: BLE001 - fail closed on any fence fault
    return AuthorityDecision(False, f"authority_unavailable:{type(exc).__name__}")

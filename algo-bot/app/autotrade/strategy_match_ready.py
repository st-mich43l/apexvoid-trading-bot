"""Durable scanner-to-worker wake-up references for confirmed matches."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
import socket
import time
from typing import Any, Mapping

from app.autotrade.lifecycle import increment_metric
from app.autotrade.multi_match import (
  deserialize_matches,
  strategy_matches_key,
)
from app.autotrade.strategy_match import (
  StrategyMatch,
  strategy_match_key,
)
from app.core.config import runtime_config


READY_EVENT_VERSION = 1
READY_STREAM = "auto_trade:strategy_match_ready"
READY_GROUP = "algo-worker"
READY_DEDUP_PREFIX = "auto_trade:strategy_match_ready:dedup"
READY_SNAPSHOT_PREFIX = "auto_trade:strategy_match_ready:last"


@dataclass(frozen=True)
class StrategyMatchReadyEvent:
  version: int
  event_id: str
  symbol: str
  match_id: str
  setup_id: str
  thesis_id: str
  source_tf: str
  scanner_event_ts: str
  issued_at: int
  expires_at: int
  direction: str
  entry_low: float
  entry_high: float
  structural_zone_id: str
  confluence_zone_id: str
  market_map_id: str
  ready_at: int
  recovery: bool = False

  def fields(self) -> dict[str, str]:
    return {
      key: (
        "1" if value is True else "0" if value is False else str(value)
      )
      for key, value in asdict(self).items()
    }

  @classmethod
  def from_fields(
    cls,
    fields: Mapping[object, object],
  ) -> StrategyMatchReadyEvent | None:
    payload = {
      _text(key): _text(value) for key, value in fields.items()
    }
    try:
      result = cls(
        version=int(payload["version"]),
        event_id=payload["event_id"],
        symbol=payload["symbol"].upper(),
        match_id=payload["match_id"],
        setup_id=payload["setup_id"],
        thesis_id=payload["thesis_id"],
        source_tf=payload["source_tf"].upper(),
        scanner_event_ts=payload["scanner_event_ts"],
        issued_at=int(payload["issued_at"]),
        expires_at=int(payload["expires_at"]),
        direction=payload["direction"].upper(),
        entry_low=float(payload["entry_low"]),
        entry_high=float(payload["entry_high"]),
        structural_zone_id=payload["structural_zone_id"],
        confluence_zone_id=payload.get("confluence_zone_id", ""),
        market_map_id=payload.get("market_map_id", ""),
        ready_at=int(payload["ready_at"]),
        recovery=payload.get("recovery", "0") in {"1", "true", "True"},
      )
    except (KeyError, TypeError, ValueError):
      return None
    if (
      result.version != READY_EVENT_VERSION
      or result.direction not in {"BUY", "SELL"}
      or not result.match_id
      or result.setup_id != result.match_id
      or not all(
        math.isfinite(value) and value > 0
        for value in (result.entry_low, result.entry_high)
      )
      or result.entry_high <= result.entry_low
      or result.expires_at <= result.issued_at
    ):
      return None
    return result


def _text(value: object) -> str:
  return value.decode() if isinstance(value, bytes) else str(value)


def ready_consumer_name() -> str:
  # ``socket.gethostname()`` returns the container hostname without an ambient
  # ENV read; fall back to a stable name when it is empty/unavailable.
  host = (socket.gethostname() or "algo-worker").strip() or "algo-worker"
  return f"{host}:{os.getpid()}"


def _ready_event_id(match: StrategyMatch, *, recovery: bool) -> str:
  raw = (
    f"v{READY_EVENT_VERSION}|{match.symbol}|{match.match_id}|"
    f"{match.event_ts}|{int(recovery)}"
  )
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def event_for_match(
  match: StrategyMatch,
  *,
  market_map_id: str = "",
  recovery: bool = False,
  now: int | None = None,
) -> StrategyMatchReadyEvent:
  ready_at = int(time.time()) if now is None else int(now)
  eligibility = match.execution_eligibility
  return StrategyMatchReadyEvent(
    version=READY_EVENT_VERSION,
    event_id=_ready_event_id(match, recovery=recovery),
    symbol=match.symbol,
    match_id=match.match_id,
    setup_id=match.match_id,
    thesis_id=str(match.thesis_id or ""),
    source_tf=match.source_tf,
    scanner_event_ts=match.event_ts,
    issued_at=match.issued_at,
    expires_at=match.expires_at,
    direction=match.direction,
    entry_low=match.entry_low,
    entry_high=match.entry_high,
    structural_zone_id=str(match.structural_zone_id or ""),
    confluence_zone_id=str(match.confluence_zone_id or ""),
    market_map_id=(
      market_map_id
      or (
        "" if eligibility is None else eligibility.market_map_id
      )
    ),
    ready_at=ready_at,
    recovery=recovery,
  )


def ready_dedup_key(setup_id: str, *, recovery: bool = False) -> str:
  suffix = ":recovery" if recovery else ""
  return f"{READY_DEDUP_PREFIX}:{setup_id}{suffix}"


def ready_snapshot_key(setup_id: str) -> str:
  return f"{READY_SNAPSHOT_PREFIX}:{setup_id}"


async def save_ready_snapshot(
  client: Any,
  event: StrategyMatchReadyEvent,
  *,
  worker_received_at: int,
) -> None:
  ttl = max(60, event.expires_at - worker_received_at)
  await client.set(
    ready_snapshot_key(event.setup_id),
    json.dumps(
      {
        "event_id": event.event_id,
        "setup_id": event.setup_id,
        "match_id": event.match_id,
        "scanner_event_ts": event.scanner_event_ts,
        "ready_event_ts": event.ready_at,
        "worker_received_ts": worker_received_at,
        "market_map_id": event.market_map_id,
        "recovery": event.recovery,
      },
      separators=(",", ":"),
      sort_keys=True,
    ),
    ex=ttl,
  )


async def enqueue_strategy_match_ready(
  client: Any,
  match: StrategyMatch,
  *,
  market_map_id: str = "",
  recovery: bool = False,
) -> str | None:
  """Enqueue one durable wake-up per setup; duplicates are harmless."""
  event = event_for_match(
    match,
    market_map_id=market_map_id,
    recovery=recovery,
  )
  ttl = max(60, int(match.expires_at) - int(time.time()))
  dedup_key = ready_dedup_key(match.match_id, recovery=recovery)
  reserved = await client.set(
    dedup_key,
    "reserved",
    nx=True,
    ex=min(ttl, 30),
  )
  if not reserved:
    await increment_metric(
      client,
      "strategy_match_ready_duplicate_suppressed",
      symbol=match.symbol,
    )
    return None
  try:
    stream_id = await client.xadd(
      READY_STREAM,
      event.fields(),
      maxlen=max(100, int(runtime_config.contract.streams.candidate_maximum_length)),
      approximate=True,
    )
    await client.set(dedup_key, event.event_id, ex=ttl)
  except Exception:
    await client.delete(dedup_key)
    raise
  await increment_metric(
    client,
    "strategy_match_ready_enqueued",
    symbol=match.symbol,
    dimensions={"recovery": str(recovery).lower()},
  )
  return _text(stream_id)


async def ensure_ready_group(client: Any) -> None:
  try:
    await client.xgroup_create(
      READY_STREAM,
      READY_GROUP,
      id="0",
      mkstream=True,
    )
  except Exception as exc:
    if "BUSYGROUP" not in str(exc):
      raise


# P0-11: distinct from any per-event metric - a single durable snapshot of
# "is the consumer that turns ready events into worker action even
# running," so /auto_status can tell "genuinely nothing to do right now"
# apart from "this consumer is down and nothing will ever advance."
READY_CONSUMER_HEALTH_KEY = "auto_trade:strategy_match_ready_consumer_health"
READY_CONSUMER_HEALTH_TTL_SECONDS = 300


async def save_ready_consumer_health(
  client: Any,
  *,
  state: str,
  consumer: str,
  last_success_at: int | None = None,
  last_event_id: str | None = None,
  pending_recovered: int | None = None,
  retry_count: int = 0,
  last_error: str | None = None,
) -> None:
  existing = await load_ready_consumer_health(client) or {}
  payload = {
    "state": state,
    "consumer": consumer,
    "group": READY_GROUP,
    "stream": READY_STREAM,
    "last_success_at": (
      last_success_at
      if last_success_at is not None
      else existing.get("last_success_at")
    ),
    "last_event_id": (
      last_event_id
      if last_event_id is not None
      else existing.get("last_event_id")
    ),
    "pending_recovered": (
      pending_recovered
      if pending_recovered is not None
      else existing.get("pending_recovered")
    ),
    "retry_count": retry_count,
    "last_error": last_error,
    "updated_at": int(time.time()),
  }
  await client.set(
    READY_CONSUMER_HEALTH_KEY,
    json.dumps(payload, separators=(",", ":"), sort_keys=True),
    ex=READY_CONSUMER_HEALTH_TTL_SECONDS,
  )


async def load_ready_consumer_health(client: Any) -> dict[str, Any] | None:
  raw = await client.get(READY_CONSUMER_HEALTH_KEY)
  if not raw:
    return None
  try:
    return json.loads(_text(raw))
  except (TypeError, ValueError, json.JSONDecodeError):
    return None


async def load_canonical_match(
  client: Any,
  event: StrategyMatchReadyEvent,
) -> StrategyMatch | None:
  matches = deserialize_matches(
    await client.get(strategy_matches_key(event.symbol)),
  )
  for match in matches:
    if match.match_id == event.match_id:
      return match
  primary = StrategyMatch.from_json(
    await client.get(strategy_match_key(event.symbol)),
  )
  if primary is not None and primary.match_id == event.match_id:
    return primary
  return None

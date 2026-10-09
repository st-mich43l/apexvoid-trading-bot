"""Per-StrategyMatch execution-route state persisted in Redis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import types
from typing import Any, Literal


RouteStatus = Literal[
  "detected",
  "checking",
  "waiting",
  "blocked",
  "candidate_published",
  "executor_received",
  "executor_rejected",
  "order_submitted",
  "order_filled",
  "expired",
  "duplicate_suppressed",
  "arbitration_suppressed",
]

RouteStage = Literal[
  "scanner",
  "mode_check",
  "policy",
  "spot_check",
  "counter_bias",
  "opposing_barrier",
  "overlap",
  "cooldown",
  "entry_invalidation",
  "entry_drift",
  "news",
  "range_context",
  "candidate_claim",
  "stream_publish",
  "preflight",
  "technique",
  "arbitration",
  "executor",
  "broker",
]


@dataclass(frozen=True)
class StrategyRouteOutcome:
  version: int
  symbol: str
  match_id: str
  strategy: str
  strategy_family: str
  direction: str
  structural_source: str
  structural_id: str
  stage: RouteStage
  status: RouteStatus
  reason_code: str
  message: str
  measured: dict[str, Any] = field(default_factory=dict)
  detected_at: int = 0
  checked_at: int = 0
  expires_at: int = 0
  candidate_id: str | None = None
  group_id: str | None = None
  executor_event_id: str | None = None
  preflight_reason_code: str | None = None
  arbitration_reason_code: str | None = None
  publication_reason_code: str | None = None
  terminal_reason_code: str | None = None
  current_stage: str | None = None
  retained: bool | None = None
  winner_intent_id: str | None = None
  signal_source: str | None = None

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


_OUTCOME_DEFAULTS: dict[str, Any] = {
  "version": 2, "symbol": "", "match_id": "", "strategy": "", "strategy_family": "scanner",
  "direction": "", "structural_source": "", "structural_id": "", "stage": "scanner",
  "status": "detected", "reason_code": "", "message": "", "measured": {}, "detected_at": 0,
  "checked_at": 0, "expires_at": 0, "candidate_id": None, "group_id": None,
  "executor_event_id": None, "preflight_reason_code": None, "arbitration_reason_code": None,
  "publication_reason_code": None, "terminal_reason_code": None, "current_stage": None,
  "retained": None, "winner_intent_id": None, "signal_source": None,
}


def attribution_shim(
  *,
  match_id: str,
  symbol: str,
  issued_at: int,
  expires_at: int,
  sources: tuple[Any, ...] = (),
) -> Any:
  """A match-shaped object carrying every attribution field a route outcome reads.

  ``sources`` are consulted in order - the live StrategyMatch, the durable SetupRecord, the
  event that triggered the projection - and the first non-empty value of each field wins, so a
  projection built from a thin source still names its strategy, direction and structure when
  any richer source knows them. A field no source knows stays empty rather than invented."""
  def pick(*names: str) -> str:
    for source in sources:
      for name in names:
        value = getattr(source, name, None)
        if value:
          return str(value)
    return ""

  return types.SimpleNamespace(
    match_id=match_id, symbol=symbol, issued_at=issued_at, expires_at=expires_at,
    strategy=pick("strategy"), family=pick("family", "strategy_family"),
    direction=pick("direction"), structural_source=pick("structural_source"),
    structural_zone_id=pick("structural_zone_id", "source_structure_id", "zone_id"),
  )


# Statuses that already describe a finished setup: a later terminal event (a Go
# invalidation, the expiry sweeper, startup reconciliation) must not overwrite them,
# so the first terminal evidence is the one an operator reads.
TERMINAL_ROUTE_STATUSES = frozenset({
  "expired", "blocked", "executor_rejected",
  "duplicate_suppressed", "arbitration_suppressed",
  "order_filled",
})


# Broker/executor evidence. An analysis-side terminal projection (a Go invalidation or expiry,
# the expiry sweeper, startup reconciliation) must never overwrite it: the order, the fill or the
# executor's rejection is what actually happened.
AUTHORITATIVE_EXECUTION_STATUSES = frozenset({
  "executor_rejected", "executor_received", "order_submitted", "order_filled",
})
# The stages analysis-side terminal projections write with.
ANALYSIS_TERMINAL_STAGES = frozenset({"scanner", "entry_invalidation"})


def route_outcome_key(symbol: str, match_id: str) -> str:
  return f"auto_trade:route_outcome:{symbol.upper()}:{match_id}"


def last_route_outcome_key(symbol: str) -> str:
  return f"auto_trade:last_route_outcome:{symbol.upper()}"


def route_history_key(symbol: str) -> str:
  return f"auto_trade:route_history:{symbol.upper()}"


def _material_measurement_changed(
  previous: dict[str, Any],
  current: dict[str, Any],
) -> bool:
  keys = set(previous) | set(current)
  for key in keys:
    left = previous.get(key)
    right = current.get(key)
    if left == right:
      continue
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
      threshold = 1.0 if (
        "pips" in key or "room" in key or "distance" in key
      ) else 0.1 if "price" in key else 0.01
      if abs(float(left) - float(right)) < threshold:
        continue
    return True
  return False


async def record_route_outcome(
  client: Any,
  match: Any,
  *,
  stage: RouteStage,
  status: RouteStatus,
  reason_code: str,
  message: str,
  measured: dict[str, Any] | None = None,
  candidate_id: str | None = None,
  group_id: str | None = None,
  executor_event_id: str | None = None,
  retained: bool | None = None,
  preflight_reason_code: str | None = None,
  arbitration_reason_code: str | None = None,
  publication_reason_code: str | None = None,
  terminal_reason_code: str | None = None,
  winner_intent_id: str | None = None,
  signal_source: str | None = None,
  publish_status: bool = True,
) -> StrategyRouteOutcome:
  now = int(datetime.now(timezone.utc).timestamp())
  details = dict(measured or {})
  setup_id = str(getattr(match, "match_id", ""))
  ready_raw = await client.get(
    f"auto_trade:strategy_match_ready:last:{setup_id}",
  )
  if ready_raw:
    try:
      ready = json.loads(
        ready_raw.decode() if isinstance(ready_raw, bytes) else str(ready_raw),
      )
      for name in (
        "event_id",
        "setup_id",
        "match_id",
        "scanner_event_ts",
        "ready_event_ts",
        "worker_received_ts",
        "market_map_id",
        "recovery",
      ):
        if name in ready:
          details.setdefault(name, ready[name])
    except (TypeError, ValueError, json.JSONDecodeError):
      pass
  if retained is not None:
    details["match_retained"] = retained
  details.setdefault("spot_price", getattr(match, "current_price", None))
  details.setdefault("entry_low", getattr(match, "entry_low", None))
  details.setdefault("entry_high", getattr(match, "entry_high", None))
  key = route_outcome_key(
    str(getattr(match, "symbol", "")).upper(),
    str(getattr(match, "match_id", "")),
  )
  previous_raw = await client.get(key)
  previous: dict[str, Any] = {}
  if previous_raw:
    try:
      previous = json.loads(
        previous_raw.decode()
        if isinstance(previous_raw, bytes) else str(previous_raw)
      )
    except (TypeError, ValueError, json.JSONDecodeError):
      previous = {}
  if (
    stage in ANALYSIS_TERMINAL_STAGES
    and previous.get("status") in AUTHORITATIVE_EXECUTION_STATUSES
  ):
    # Keep what the executor/broker established; an analysis-side invalidation arriving
    # later (a redelivery, a restart, the sweeper) does not rewrite it.
    return StrategyRouteOutcome(**{
      name: previous.get(name, default)
      for name, default in _OUTCOME_DEFAULTS.items()
    })
  preflight_stages = {
    "mode_check", "policy", "spot_check", "counter_bias",
    "opposing_barrier", "overlap", "cooldown", "entry_invalidation",
    "entry_drift", "news", "range_context", "preflight",
  }
  if preflight_reason_code is None:
    preflight_reason_code = previous.get("preflight_reason_code")
    if stage in preflight_stages:
      preflight_reason_code = reason_code
  if arbitration_reason_code is None:
    arbitration_reason_code = previous.get("arbitration_reason_code")
    if stage == "arbitration":
      arbitration_reason_code = reason_code
  if publication_reason_code is None:
    publication_reason_code = previous.get("publication_reason_code")
    if stage in {"candidate_claim", "stream_publish"}:
      publication_reason_code = reason_code
  if terminal_reason_code is None:
    terminal_reason_code = previous.get("terminal_reason_code")
    if status in {"blocked", "expired", "executor_rejected"}:
      terminal_reason_code = reason_code
    elif status in {
      "checking",
      "waiting",
      "candidate_published",
      "executor_received",
      "order_submitted",
      "order_filled",
    }:
      # Current snapshots describe current truth. The append-only route
      # history still retains the earlier terminal transition.
      terminal_reason_code = None
  outcome = StrategyRouteOutcome(
    version=2,
    symbol=str(getattr(match, "symbol", "")).upper(),
    match_id=str(getattr(match, "match_id", "")),
    # A caller that only knows the match id (a terminal projection holding a thin object)
    # must not blank what an earlier outcome already recorded.
    strategy=str(getattr(match, "strategy", "") or previous.get("strategy") or ""),
    strategy_family=str(
      getattr(match, "family", "") or previous.get("strategy_family") or "scanner"
    ),
    direction=str(getattr(match, "direction", "") or previous.get("direction") or "").upper(),
    structural_source=str(
      getattr(match, "structural_source", "")
      or getattr(match, "strategy", "")
      or previous.get("structural_source")
      or ""
    ),
    structural_id=str(
      getattr(match, "structural_zone_id", "")
      or getattr(match, "zone_id", "")
      or getattr(match, "level_id", "")
      or previous.get("structural_id")
      or ""
    ),
    stage=stage,
    status=status,
    reason_code=reason_code,
    message=message,
    measured=details,
    detected_at=int(getattr(match, "issued_at", 0) or now),
    checked_at=now,
    expires_at=int(getattr(match, "expires_at", 0) or now),
    candidate_id=candidate_id or previous.get("candidate_id"),
    group_id=group_id or previous.get("group_id"),
    executor_event_id=(
      executor_event_id or previous.get("executor_event_id")
    ),
    preflight_reason_code=preflight_reason_code,
    arbitration_reason_code=arbitration_reason_code,
    publication_reason_code=publication_reason_code,
    terminal_reason_code=terminal_reason_code,
    current_stage=stage,
    retained=retained,
    winner_intent_id=winner_intent_id or previous.get("winner_intent_id"),
    signal_source=signal_source or previous.get("signal_source"),
  )
  encoded = outcome.to_json()
  ttl = max(300, outcome.expires_at - now, 86400)
  transition_changed = previous_raw is None
  material_changed = False
  if previous_raw:
    try:
      transition_changed = not (
        previous.get("status") == outcome.status
        and previous.get("reason_code") == outcome.reason_code
        and previous.get("current_stage") == outcome.current_stage
      )
      if not transition_changed:
        material_changed = _material_measurement_changed(
          dict(previous.get("measured") or {}),
          outcome.measured,
        )
      if not transition_changed and not material_changed:
        publish_status = False
    except (TypeError, ValueError, json.JSONDecodeError):
      pass
  await client.set(
    key, encoded, ex=ttl,
  )
  await client.set(last_route_outcome_key(outcome.symbol), encoded, ex=ttl)
  if transition_changed or material_changed:
    await client.xadd(
      route_history_key(outcome.symbol),
      {"payload": encoded},
      maxlen=1000,
      approximate=True,
    )
  metric_claimed = await client.set(
    (
      f"auto_trade:route_metric:{outcome.symbol}:"
      f"{outcome.match_id}:{status}"
    ),
    "1",
    nx=True,
    ex=ttl,
  )
  if metric_claimed:
    await client.hincrby(
      f"auto_trade:metrics:{outcome.symbol}",
      f"strategy_match_{status}",
      1,
    )
  if publish_status and status in {
    "waiting", "blocked", "candidate_published", "executor_rejected",
  }:
    await client.xadd(
      "auto_trade:events",
      {"payload": json.dumps({
        "type": "strategy_route",
        **asdict(outcome),
      }, separators=(",", ":"), sort_keys=True)},
      maxlen=5000,
      approximate=True,
    )
  return outcome

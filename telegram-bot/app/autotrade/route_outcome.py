"""Per-StrategyMatch execution-route state persisted in Redis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
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
]

RouteStage = Literal[
  "scanner",
  "mode_check",
  "spot_check",
  "counter_bias",
  "opposing_barrier",
  "overlap",
  "cooldown",
  "entry_invalidation",
  "entry_drift",
  "news",
  "candidate_claim",
  "stream_publish",
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

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


def route_outcome_key(symbol: str, match_id: str) -> str:
  return f"auto_trade:route_outcome:{symbol.upper()}:{match_id}"


def last_route_outcome_key(symbol: str) -> str:
  return f"auto_trade:last_route_outcome:{symbol.upper()}"


def route_history_key(symbol: str) -> str:
  return f"auto_trade:route_history:{symbol.upper()}"


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
  publish_status: bool = True,
) -> StrategyRouteOutcome:
  now = int(datetime.now(timezone.utc).timestamp())
  details = dict(measured or {})
  if retained is not None:
    details["match_retained"] = retained
  details.setdefault("spot_price", getattr(match, "current_price", None))
  details.setdefault("entry_low", getattr(match, "entry_low", None))
  details.setdefault("entry_high", getattr(match, "entry_high", None))
  outcome = StrategyRouteOutcome(
    version=1,
    symbol=str(getattr(match, "symbol", "")).upper(),
    match_id=str(getattr(match, "match_id", "")),
    strategy=str(getattr(match, "strategy", "")),
    strategy_family=str(getattr(match, "family", "") or "scanner"),
    direction=str(getattr(match, "direction", "")).upper(),
    structural_source=str(
      getattr(match, "structural_source", "") or getattr(match, "strategy", "")
    ),
    structural_id=str(
      getattr(match, "structural_zone_id", "")
      or getattr(match, "zone_id", "")
      or getattr(match, "level_id", "")
    ),
    stage=stage,
    status=status,
    reason_code=reason_code,
    message=message,
    measured=details,
    detected_at=int(getattr(match, "issued_at", 0) or now),
    checked_at=now,
    expires_at=int(getattr(match, "expires_at", 0) or now),
    candidate_id=candidate_id,
    group_id=group_id,
    executor_event_id=executor_event_id,
  )
  encoded = outcome.to_json()
  ttl = max(300, outcome.expires_at - now, 86400)
  previous_raw = await client.get(
    route_outcome_key(outcome.symbol, outcome.match_id)
  )
  if previous_raw:
    try:
      previous = json.loads(
        previous_raw.decode()
        if isinstance(previous_raw, bytes) else str(previous_raw)
      )
      if (
        previous.get("status") == outcome.status
        and previous.get("reason_code") == outcome.reason_code
      ):
        publish_status = False
    except (TypeError, ValueError, json.JSONDecodeError):
      pass
  await client.set(
    route_outcome_key(outcome.symbol, outcome.match_id), encoded, ex=ttl,
  )
  await client.set(last_route_outcome_key(outcome.symbol), encoded, ex=ttl)
  await client.xadd(
    route_history_key(outcome.symbol),
    {"payload": encoded},
    maxlen=1000,
    approximate=True,
  )
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


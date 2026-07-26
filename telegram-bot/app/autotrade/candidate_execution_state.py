"""Structured Redis candidate execution records with legacy string support."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any

STATE_PUBLISHED = "published"
STATE_PROCESSING = "processing"
STATE_BROKER_SUBMITTING = "broker_submitting"
STATE_ORDERED = "ordered"
STATE_COMPLETED = "completed"
STATE_REJECTED = "rejected"
STATE_RETRYABLE_ERROR = "retryable_error"
STATE_BROKER_OUTCOME_UNKNOWN = "broker_outcome_unknown"
# Recovery-active ownership: structurally separate from ``processing`` so a
# crashed recovery worker can never decay into a normal retry.
STATE_BROKER_RECONCILING = "broker_reconciling"
STATE_DRY_RUN = "dry_run"
STATE_FLIP_PENDING = "flip_pending"
STATE_INTEGRITY_ERROR = "integrity_error"

EXECUTOR_COMPATIBLE_STATES = frozenset({
  STATE_PUBLISHED,
  STATE_PROCESSING,
  STATE_BROKER_SUBMITTING,
  STATE_ORDERED,
  STATE_COMPLETED,
  STATE_REJECTED,
  STATE_RETRYABLE_ERROR,
  STATE_BROKER_OUTCOME_UNKNOWN,
  STATE_BROKER_RECONCILING,
  STATE_DRY_RUN,
  STATE_FLIP_PENDING,
  STATE_INTEGRITY_ERROR,
})

# The executor may hand a candidate back for a later attempt from these states.
RETRYABLE_STATES = frozenset({STATE_PUBLISHED, STATE_RETRYABLE_ERROR})

# A broker request may have been accepted, so only deterministic broker
# reconciliation may move the record on - never a plain retry.
RECOVERY_REQUIRED_STATES = frozenset({
  STATE_BROKER_OUTCOME_UNKNOWN,
  STATE_BROKER_RECONCILING,
})

TERMINAL_STATES = frozenset({
  STATE_ORDERED,
  STATE_COMPLETED,
  STATE_REJECTED,
  STATE_DRY_RUN,
  STATE_FLIP_PENDING,
  STATE_INTEGRITY_ERROR,
})

LEGACY_PLAIN_STATES = frozenset({
  STATE_PUBLISHED,
  STATE_PROCESSING,
  "dry_run",
})

RECORD_VERSION = 1


@dataclass(frozen=True)
class CandidateExecutionRecord:
  candidate_id: str
  stream_event_id: str | None
  state: str
  lease_token: str | None = None
  lease_expires_at: int | None = None
  outcome: str | None = None
  updated_at: int = 0
  version: int = RECORD_VERSION
  # Written by the executor when it hands a candidate back for another attempt.
  attempt: int = 0
  last_error: str | None = None
  # True only for plain-string markers. Never inferred from empty identity on
  # a structured JSON record.
  is_legacy: bool = False

  @property
  def legacy_status(self) -> str:
    if self.outcome:
      return self.outcome
    return self.state

  @property
  def is_terminal(self) -> bool:
    return self.state in TERMINAL_STATES

  @property
  def is_retryable(self) -> bool:
    return self.state in RETRYABLE_STATES

  @property
  def requires_recovery(self) -> bool:
    return self.state in RECOVERY_REQUIRED_STATES

  @property
  def has_exact_identity(self) -> bool:
    return bool(self.candidate_id) and bool(self.stream_event_id)


def _coerce_optional_str(value: Any) -> str | None:
  if value is None:
    return None
  text = str(value).strip()
  return text or None


def _coerce_optional_int(value: Any) -> int | None:
  if value is None or value == "":
    return None
  return int(value)


def parse_candidate_execution_record(raw: str | bytes | None) -> CandidateExecutionRecord:
  """Parse structured JSON or legacy plain candidate markers."""
  if raw is None:
    raise ValueError("candidate execution record is missing")
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  if not text:
    raise ValueError("candidate execution record is empty")

  if text in LEGACY_PLAIN_STATES:
    return CandidateExecutionRecord(
      candidate_id="",
      stream_event_id=None,
      state=text,
      updated_at=0,
      is_legacy=True,
    )

  if ":" in text:
    prefix, _, remainder = text.partition(":")
    if prefix in {STATE_ORDERED, STATE_REJECTED} or prefix == "flip_pending":
      state = STATE_ORDERED if prefix == STATE_ORDERED else STATE_REJECTED
      if prefix == "flip_pending":
        state = STATE_COMPLETED
      return CandidateExecutionRecord(
        candidate_id="",
        stream_event_id=None,
        state=state,
        outcome=text,
        updated_at=0,
        is_legacy=True,
      )
    if prefix in EXECUTOR_COMPATIBLE_STATES:
      return CandidateExecutionRecord(
        candidate_id="",
        stream_event_id=None,
        state=prefix,
        outcome=text if remainder else None,
        updated_at=0,
        is_legacy=True,
      )

  try:
    parsed = json.loads(text)
  except (TypeError, ValueError, json.JSONDecodeError) as exc:
    raise ValueError(f"unsupported candidate execution record: {text!r}") from exc
  if not isinstance(parsed, dict):
    raise ValueError("candidate execution record must be a JSON object")

  # A structured record never becomes legacy or defaults missing fields:
  # version, identity and state must be explicitly present and valid. Legacy
  # status is determined only by the original raw value being one of the
  # supported plain-string markers handled above.
  required = {"version", "candidate_id", "stream_event_id", "state"}
  missing = required - parsed.keys()
  if missing:
    raise ValueError(
      "structured candidate execution record is missing required fields: "
      + ", ".join(sorted(missing))
    )

  version = parsed["version"]
  if isinstance(version, bool) or not isinstance(version, int):
    raise ValueError(
      f"structured candidate execution record version must be an integer, "
      f"got {version!r}"
    )
  if version != RECORD_VERSION:
    raise ValueError(f"unsupported candidate execution record version: {version}")

  raw_candidate_id = parsed["candidate_id"]
  if raw_candidate_id is not None and not isinstance(raw_candidate_id, str):
    raise ValueError("structured candidate_id must be a string")
  raw_stream_event_id = parsed["stream_event_id"]
  if raw_stream_event_id is not None and not isinstance(raw_stream_event_id, str):
    raise ValueError("structured stream_event_id must be a string")

  state = parsed["state"]
  if not isinstance(state, str) or not state.strip():
    raise ValueError("structured candidate execution record state is missing")
  if state not in EXECUTOR_COMPATIBLE_STATES:
    raise ValueError(f"unknown candidate execution record state: {state!r}")

  # Empty identity parses (so callers can classify it) but is a structured
  # conflict at compatibility time, never an implicit pass.
  return CandidateExecutionRecord(
    candidate_id=raw_candidate_id or "",
    stream_event_id=_coerce_optional_str(raw_stream_event_id),
    state=state,
    lease_token=_coerce_optional_str(parsed.get("lease_token")),
    lease_expires_at=_coerce_optional_int(parsed.get("lease_expires_at")),
    outcome=_coerce_optional_str(parsed.get("outcome")),
    updated_at=int(parsed.get("updated_at") or 0),
    version=version,
    attempt=int(parsed.get("attempt") or 0),
    last_error=_coerce_optional_str(parsed.get("last_error")),
    is_legacy=False,
  )


def serialize_candidate_execution_record(
  *,
  candidate_id: str,
  stream_event_id: str | None,
  state: str,
  lease_token: str | None = None,
  lease_expires_at: int | None = None,
  outcome: str | None = None,
  updated_at: int | None = None,
  version: int = RECORD_VERSION,
  attempt: int = 0,
  last_error: str | None = None,
) -> str:
  payload = {
    "candidate_id": candidate_id,
    "stream_event_id": stream_event_id,
    "state": state,
    "lease_token": lease_token,
    "lease_expires_at": lease_expires_at,
    "outcome": outcome,
    "updated_at": updated_at if updated_at is not None else int(time.time()),
    "version": version,
    "attempt": attempt,
    "last_error": last_error,
  }
  return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def published_candidate_record(
  *,
  candidate_id: str,
  stream_event_id: str,
  updated_at: int | None = None,
) -> str:
  return serialize_candidate_execution_record(
    candidate_id=candidate_id,
    stream_event_id=stream_event_id,
    state=STATE_PUBLISHED,
    updated_at=updated_at,
  )


def candidate_record_compatible(
  record: CandidateExecutionRecord,
  *,
  candidate_id: str,
  stream_event_id: str,
) -> bool:
  if record.is_legacy:
    # Legacy plain markers carry no identity; keep the restricted rolling-
    # deploy surface.
    if record.state in LEGACY_PLAIN_STATES:
      return True
    if record.outcome and (
      record.outcome.startswith("ordered:")
      or record.outcome.startswith("rejected:")
      or record.outcome.startswith("flip_pending:")
    ):
      return True
    return record.state in EXECUTOR_COMPATIBLE_STATES

  # Structured versioned records require exact identity. Missing fields are a
  # conflict, never an implicit pass.
  if record.version < 1 or record.version > RECORD_VERSION:
    return False
  if not record.candidate_id or not record.stream_event_id:
    return False
  if record.candidate_id != candidate_id:
    return False
  # `pending` is the mid-publish placeholder before XADD assigns the durable
  # stream event id. It is non-empty identity, not a missing field.
  if (
    record.stream_event_id != "pending"
    and record.stream_event_id != stream_event_id
  ):
    return False
  if record.outcome and (
    record.outcome.startswith("ordered:")
    or record.outcome.startswith("rejected:")
    or record.outcome.startswith("flip_pending:")
  ):
    return True
  return record.state in EXECUTOR_COMPATIBLE_STATES


def candidate_record_is_progressed(record: CandidateExecutionRecord) -> bool:
  if record.state not in {STATE_PUBLISHED} and record.state in EXECUTOR_COMPATIBLE_STATES:
    return record.state != STATE_PUBLISHED
  if record.outcome:
    return True
  return record.state in {STATE_PROCESSING, "dry_run"}

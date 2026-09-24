"""Durable idempotent lifecycle storage for Go analysis opportunities."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from app.analysis_client.models import (
  AnalysisEvent,
  InvalidationEnvelope,
  OpportunityEnvelope,
)
from app.persistence import store


@dataclass(frozen=True, slots=True)
class LifecycleResult:
  disposition: str
  opportunity_id: str


class PostgresAnalysisOpportunityRepository:
  """Postgres is the commit fence: callers commit Kafka only after return."""

  @staticmethod
  def _event_json(event: AnalysisEvent) -> str:
    return json.dumps(event.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)

  @staticmethod
  def _terminal_state(reason_code: str) -> str:
    """Map only the Go lifecycle's technical setup-expiry reason to expiry."""
    return "expired" if reason_code.upper() == "SETUP_EXPIRED" else "invalidated"

  async def apply(
    self,
    event: AnalysisEvent,
    *,
    topic: str,
    partition: int | None,
    offset: int | None,
  ) -> LifecycleResult:
    now = int(time.time())
    opportunity_id = event.payload.id if isinstance(event, OpportunityEnvelope) else event.payload.opportunity_id
    event_kind = "creation" if isinstance(event, OpportunityEnvelope) else "terminal"
    envelope = self._event_json(event)
    async with store._connect() as db:
      async with db.transaction():
        inserted = await db.fetchval(
          """
          INSERT INTO analysis_opportunity_events (
            event_id, opportunity_id, topic, partition_id, kafka_offset,
            event_kind, disposition, envelope, processed_at
          ) VALUES ($1, $2, $3, $4, $5, $6, 'received', $7::jsonb, $8)
          ON CONFLICT DO NOTHING RETURNING event_id
          """,
          event.event_id, opportunity_id, topic, partition, offset, event_kind, envelope, now,
        )
        if inserted is None:
          return LifecycleResult("duplicate_delivery", opportunity_id)
        if isinstance(event, OpportunityEnvelope):
          row = await db.fetchrow(
            "SELECT state FROM analysis_opportunities WHERE opportunity_id = $1 FOR UPDATE",
            opportunity_id,
          )
          if row is None:
            disposition = "created"
            await db.execute(
              """
              INSERT INTO analysis_opportunities (
                opportunity_id, symbol, strategy, state, creation_event_id,
                created_at, expires_at, creation_envelope, updated_at
              ) VALUES ($1, $2, $3, 'active', $4, $5, $6, $7::jsonb, $8)
              """,
              opportunity_id, event.payload.symbol, event.payload.strategy,
              event.event_id, event.payload.created_at, event.payload.expires_at,
              envelope, now,
            )
          elif row["state"] != "active":
            disposition = "late_creation_rejected"
          else:
            disposition = "duplicate_opportunity"
        else:
          row = await db.fetchrow(
            "SELECT state FROM analysis_opportunities WHERE opportunity_id = $1 FOR UPDATE",
            opportunity_id,
          )
          terminal_state = self._terminal_state(event.payload.reason_code)
          if row is None:
            disposition = "unknown_terminal_tombstoned"
            await db.execute(
              """
              INSERT INTO analysis_opportunities (
                opportunity_id, symbol, strategy, state, terminal_event_id,
                terminal_at, terminal_envelope, updated_at
              ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
              """,
              opportunity_id, event.payload.symbol, event.payload.strategy,
              terminal_state, event.event_id, event.payload.invalidated_at,
              envelope, now,
            )
          elif row["state"] == "active":
            disposition = "terminated"
            await db.execute(
              """
              UPDATE analysis_opportunities
              SET state = $2, terminal_event_id = $3, terminal_at = $4,
                  terminal_envelope = $5::jsonb, updated_at = $6
              WHERE opportunity_id = $1
              """,
              opportunity_id, terminal_state, event.event_id,
              event.payload.invalidated_at, envelope, now,
            )
          else:
            disposition = "duplicate_terminal"
        await db.execute(
          "UPDATE analysis_opportunity_events SET disposition = $2 WHERE event_id = $1",
          event.event_id, disposition,
        )
    return LifecycleResult(disposition, opportunity_id)

  async def record_rejection(
    self, *, topic: str, partition: int, offset: int, reason: str, raw_payload: bytes | str,
  ) -> None:
    raw = raw_payload.decode("utf-8", errors="replace") if isinstance(raw_payload, bytes) else raw_payload
    async with store._connect() as db:
      await db.execute(
        """
        INSERT INTO analysis_opportunity_rejections (
          topic, partition_id, kafka_offset, reason, raw_payload, rejected_at
        ) VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (topic, partition_id, kafka_offset) DO NOTHING
        """,
        topic, partition, offset, reason[:1000], raw[:100000], int(time.time()),
      )

  async def active_for_symbol(self, symbol: str, *, now: int | None = None) -> list[dict]:
    now = int(time.time()) if now is None else now
    async with store._connect() as db:
      rows = await db.fetch(
        """
        SELECT * FROM analysis_opportunities
        WHERE symbol = $1 AND state = 'active' AND (expires_at IS NULL OR expires_at >= $2)
        ORDER BY created_at, opportunity_id
        """,
        symbol, now,
      )
    return [dict(row) for row in rows]

  async def record_shadow_decision(
    self, *, opportunity_id: str, event_id: str, outcome: str, reason: str,
    missing_fields: tuple[str, ...] = (), details: dict | None = None,
  ) -> None:
    async with store._connect() as db:
      await db.execute(
        """
        INSERT INTO analysis_shadow_decisions (
          opportunity_id, event_id, mode, outcome, reason, missing_fields, details, created_at
        ) VALUES ($1, $2, 'go_shadow', $3, $4, $5::jsonb, $6::jsonb, $7)
        ON CONFLICT (opportunity_id, event_id, mode, outcome) DO NOTHING
        """,
        opportunity_id, event_id, outcome, reason,
        json.dumps(list(missing_fields)), json.dumps(details or {}), int(time.time()),
      )

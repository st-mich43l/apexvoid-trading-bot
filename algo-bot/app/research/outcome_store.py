"""Postgres side of the forward outcome labeller: the label table, the pending-opportunity query and the batch runner.

``opportunity_outcome_labels`` holds REPLAY-DERIVED outcomes only. It is keyed by (opportunity_id, label_set) so more than one set of
assumptions can coexist, and it never references or overwrites ``auto_trade_results`` (what actually happened to executed trades).
The labeller reads ``analysis_opportunities`` and ``market_bars`` / ``market_quote_minutes`` and writes only this table.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass

from app.research import bar_store, outcome_model
from app.research.outcome_model import Assumptions, Bar, Label, OpportunitySpec

log = logging.getLogger(__name__)

SCHEMA_STATEMENTS = (
  """
  CREATE TABLE IF NOT EXISTS opportunity_outcome_labels (
    opportunity_id        TEXT NOT NULL,
    label_set             TEXT NOT NULL,
    strategy              TEXT NOT NULL,
    symbol                TEXT NOT NULL,
    direction             TEXT NOT NULL,
    timeframe             TEXT NOT NULL,
    created_at            BIGINT NOT NULL,
    thesis_key            TEXT NOT NULL,
    quality_overall       DOUBLE PRECISION,
    quality_model_version INTEGER,
    quality_components    JSONB,
    stars                 INTEGER,
    config_fingerprint    TEXT,
    config_version        INTEGER,
    entry_low             DOUBLE PRECISION NOT NULL,
    entry_high            DOUBLE PRECISION NOT NULL,
    stop_price            DOUBLE PRECISION NOT NULL,
    target_price          DOUBLE PRECISION NOT NULL,
    eligible_from         BIGINT NOT NULL,
    window_end            BIGINT NOT NULL,
    end_reason            TEXT NOT NULL,
    fill_state            TEXT NOT NULL,
    outcome               TEXT NOT NULL,
    fill_ts               BIGINT,
    fill_price            DOUBLE PRECISION,
    exit_ts               BIGINT,
    target_r              DOUBLE PRECISION,
    r_low                 DOUBLE PRECISION,
    r_high                DOUBLE PRECISION,
    r_mark                DOUBLE PRECISION,
    mfe_r                 DOUBLE PRECISION,
    mae_r                 DOUBLE PRECISION,
    r1_ts                 BIGINT,
    r2_ts                 BIGINT,
    spread_source         TEXT,
    spread_used           DOUBLE PRECISION,
    data_quality          TEXT NOT NULL,
    bars_used             INTEGER NOT NULL,
    coverage_end          BIGINT,
    touches               JSONB,
    ladder_outcome        TEXT,
    ladder_r_low          DOUBLE PRECISION,
    ladder_r_high         DOUBLE PRECISION,
    complete              BOOLEAN NOT NULL,
    labeller_version      TEXT NOT NULL,
    assumptions           JSONB NOT NULL,
    labelled_at           BIGINT NOT NULL,
    PRIMARY KEY (opportunity_id, label_set)
  )
  """,
  "CREATE INDEX IF NOT EXISTS idx_outcome_labels_strategy_ts ON opportunity_outcome_labels(strategy, created_at)",
  "CREATE INDEX IF NOT EXISTS idx_outcome_labels_thesis ON opportunity_outcome_labels(thesis_key, created_at)",
)


async def ensure_schema(db) -> None:
  for statement in SCHEMA_STATEMENTS:
    await db.execute(statement)


async def upsert_label(db, spec: OpportunitySpec, label: Label, a: Assumptions, quality_version: int | None, *, now: int) -> None:
  await db.execute(
    """
    INSERT INTO opportunity_outcome_labels (
      opportunity_id, label_set, strategy, symbol, direction, timeframe, created_at, thesis_key, quality_overall, quality_model_version,
      quality_components, stars, config_fingerprint, config_version, entry_low, entry_high, stop_price, target_price, eligible_from, window_end,
      end_reason, fill_state, outcome, fill_ts, fill_price, exit_ts, target_r, r_low, r_high, r_mark, mfe_r, mae_r, r1_ts, r2_ts, spread_source,
      spread_used, data_quality, bars_used, coverage_end, touches, ladder_outcome, ladder_r_low, ladder_r_high, complete, labeller_version, assumptions, labelled_at
    ) VALUES (
      $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,$35,
      $36,$37,$38,$39,$40::jsonb,$41,$42,$43,$44,$45,$46::jsonb,$47
    )
    ON CONFLICT (opportunity_id, label_set) DO UPDATE SET
      end_reason=EXCLUDED.end_reason, window_end=EXCLUDED.window_end, fill_state=EXCLUDED.fill_state, outcome=EXCLUDED.outcome,
      fill_ts=EXCLUDED.fill_ts, fill_price=EXCLUDED.fill_price, exit_ts=EXCLUDED.exit_ts, target_r=EXCLUDED.target_r, r_low=EXCLUDED.r_low,
      r_high=EXCLUDED.r_high, r_mark=EXCLUDED.r_mark, mfe_r=EXCLUDED.mfe_r, mae_r=EXCLUDED.mae_r, r1_ts=EXCLUDED.r1_ts, r2_ts=EXCLUDED.r2_ts,
      spread_source=EXCLUDED.spread_source, spread_used=EXCLUDED.spread_used, data_quality=EXCLUDED.data_quality, bars_used=EXCLUDED.bars_used,
      coverage_end=EXCLUDED.coverage_end, touches=EXCLUDED.touches, ladder_outcome=EXCLUDED.ladder_outcome, ladder_r_low=EXCLUDED.ladder_r_low,
      ladder_r_high=EXCLUDED.ladder_r_high, complete=EXCLUDED.complete, labeller_version=EXCLUDED.labeller_version,
      assumptions=EXCLUDED.assumptions, labelled_at=EXCLUDED.labelled_at
    """,
    spec.opportunity_id, label.label_set, spec.strategy, spec.symbol, spec.direction, spec.timeframe, spec.created_at, spec.thesis_key,
    spec.quality_overall, quality_version, json.dumps(spec.quality_components, sort_keys=True), spec.stars, spec.config_fingerprint,
    spec.config_version, spec.entry_low, spec.entry_high, spec.stop, spec.target, label.eligible_from, label.window_end, label.end_reason,
    label.fill_state, label.outcome, label.fill_ts, label.fill_price, label.exit_ts, label.target_r, label.r_low, label.r_high, label.r_mark,
    label.mfe_r, label.mae_r, label.r1_ts, label.r2_ts, label.spread_source, label.spread_used, label.data_quality, label.bars_used,
    label.coverage_end, None if label.touches is None else json.dumps(label.touches, sort_keys=True), label.ladder_outcome, label.ladder_r_low,
    label.ladder_r_high, label.complete, outcome_model.LABELLER_VERSION, a.as_json(), now,
  )


def spread_function(quotes: dict[int, tuple[float, float, float, int]], basis: str):
  """SpreadFn over the archived per-minute quote statistics (None for a minute with no quotes: the label then uses its fallback)."""
  index = {"min": 0, "max": 1, "mean": 2}[basis]

  def lookup(minute_ts: int):
    row = quotes.get(minute_ts)
    return None if row is None else (row[index], "quotes")

  return lookup


@dataclass
class RunSummary:
  examined: int = 0
  written: int = 0
  skipped_unusable: int = 0
  outcomes: Counter = None

  def __post_init__(self):
    if self.outcomes is None:
      self.outcomes = Counter()


async def label_batch(db, a: Assumptions, *, symbols: list[str], since_ts: int, limit: int, now: int, dry_run: bool = False) -> RunSummary:
  """Label up to ``limit`` opportunities (oldest first) that have no complete label under these assumptions. Idempotent."""
  from app.core.symbols import pip_for

  summary = RunSummary()
  # A dry run may precede the first schema creation: without the table nothing is labelled yet.
  has_labels = await db.fetchval("SELECT to_regclass('opportunity_outcome_labels')") is not None
  unlabelled = (
    "AND NOT EXISTS (SELECT 1 FROM opportunity_outcome_labels l WHERE l.opportunity_id = o.opportunity_id AND l.label_set = $3 AND l.complete)"
    if has_labels else "AND $3::text IS NOT NULL"
  )
  rows = await db.fetch(
    f"""
    SELECT o.opportunity_id, o.creation_envelope::text AS creation, o.terminal_envelope::text AS terminal
    FROM analysis_opportunities o
    WHERE o.creation_envelope IS NOT NULL AND o.symbol = ANY($1::text[]) AND o.created_at >= $2 {unlabelled}
    ORDER BY o.created_at LIMIT $4
    """,
    symbols, since_ts, a.label_set, limit,
  )
  coverage: dict[str, int | None] = {}
  for row in rows:
    summary.examined += 1
    creation = json.loads(row["creation"])
    terminal = json.loads(row["terminal"]) if row["terminal"] else None
    symbol = str((creation.get("payload") or {}).get("symbol", "")).upper()
    try:
      pip = pip_for(symbol)
    except KeyError:
      summary.skipped_unusable += 1
      continue
    spec, quality_version = outcome_model.spec_from_rows(creation, terminal, pip=pip)
    if spec is None:
      summary.skipped_unusable += 1
      continue
    start = outcome_model.eligible_from(spec, a)
    end, _ = outcome_model.window_end(spec)
    horizon = end + a.max_hold_seconds
    if symbol not in coverage:
      coverage[symbol] = await bar_store.latest_open_ts(db, symbol, "M1")
    bars = [Bar(*b[:5]) for b in await bar_store.read_bars(db, symbol, "M1", start - start % 60, horizon + 120)]
    quotes = await bar_store.read_spreads(db, symbol, start - start % 60, horizon + 120)
    label = outcome_model.label_opportunity(spec, bars, spread_function(quotes, a.spread_basis), a, coverage_end=coverage[symbol])
    summary.outcomes[label.outcome + ("" if label.complete else " (provisional)")] += 1
    if not dry_run:
      await upsert_label(db, spec, label, a, quality_version, now=now)
      summary.written += 1
  return summary


def label_to_row(spec: OpportunitySpec, label: Label, a: Assumptions, quality_version: int | None) -> dict:
  """The label as a dict with the table's column names (offline runs and the research report read this shape)."""
  return {
    "opportunity_id": spec.opportunity_id, "label_set": label.label_set, "strategy": spec.strategy, "symbol": spec.symbol,
    "direction": spec.direction, "timeframe": spec.timeframe, "created_at": spec.created_at, "thesis_key": spec.thesis_key,
    "quality_overall": spec.quality_overall, "quality_model_version": quality_version, "stars": spec.stars,
    "config_fingerprint": spec.config_fingerprint, "config_version": spec.config_version, "entry_low": spec.entry_low,
    "entry_high": spec.entry_high, "stop_price": spec.stop, "target_price": spec.target, "eligible_from": label.eligible_from,
    "window_end": label.window_end, "end_reason": label.end_reason, "fill_state": label.fill_state, "outcome": label.outcome,
    "fill_ts": label.fill_ts, "fill_price": label.fill_price, "exit_ts": label.exit_ts, "target_r": label.target_r, "r_low": label.r_low,
    "r_high": label.r_high, "r_mark": label.r_mark, "mfe_r": label.mfe_r, "mae_r": label.mae_r, "r1_ts": label.r1_ts, "r2_ts": label.r2_ts,
    "spread_source": label.spread_source, "spread_used": label.spread_used, "data_quality": label.data_quality,
    "bars_used": label.bars_used, "coverage_end": label.coverage_end, "touches": label.touches, "ladder_outcome": label.ladder_outcome,
    "ladder_r_low": label.ladder_r_low, "ladder_r_high": label.ladder_r_high, "complete": label.complete,
    "labeller_version": outcome_model.LABELLER_VERSION, "assumptions": json.loads(a.as_json()),
  }

"""Repair the two verified 2026-08-20 XAU manual-ladder result anomalies.

The command is deliberately incident-scoped and dry-run by default. It will
only consider an explicitly repeated ``--signal-id`` from the verified
allow-list, re-derive the group result from retained broker lifecycle events,
and refuse to write unless every Redis and PostgreSQL fingerprint still
matches the audited incident.

Examples (inside the bot container):

  python -m app.scripts.repair_manual_algo_results \
    --signal-id 101 --signal-id 104
  python -m app.scripts.repair_manual_algo_results \
    --signal-id 101 --signal-id 104 --apply
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import json
import time
from typing import Any, Iterable

from app.core.symbols import pip_for
from app.persistence import redis_state, store


CORRECTION_SOURCE = "manual_ladder_terminal_v1"
_TP_EVENT_TYPES = frozenset({"take_profit", "tp_booked"})
_EXPECTED_EVENT_TYPES = (
  "executor_received",
  "routing_selected",
  "manual_limit_placed",
  "manual_limit_placed",
  "manual_limit_placed",
  "manual_opened",
  "manual_opened",
  "manual_opened",
  "position_closed",
  "position_closed",
  "position_closed",
  "group_result",
)


class RepairValidationError(RuntimeError):
  """Raised when retained evidence no longer matches the audited incident."""


@dataclass(frozen=True)
class IncidentLeg:
  order_id: int
  position_id: int
  planned_entry: Decimal
  fill_price: Decimal
  volume: int
  exit_price: Decimal
  leg_result_pips: Decimal
  old_fill_stop_pips: Decimal


@dataclass(frozen=True)
class Incident:
  signal_id: int
  candidate_id: str
  group_id: str
  entry: Decimal
  entry_end: Decimal
  stop_loss: Decimal
  old_manual_result: int
  old_raw_result: Decimal
  old_result_stop_pips: Decimal
  pips_log_id: int
  manual_closed_at: int
  result_closed_at: int
  legs: tuple[IncidentLeg, ...]

  @property
  def shallow_planned_entry(self) -> Decimal:
    return max(self.entry, self.entry_end)

  @property
  def shallow_fill(self) -> Decimal:
    return max(leg.fill_price for leg in self.legs)

  @property
  def deep_fill(self) -> Decimal:
    return min(leg.fill_price for leg in self.legs)

  @property
  def position_ids(self) -> set[int]:
    return {leg.position_id for leg in self.legs}


INCIDENTS: dict[int, Incident] = {
  101: Incident(
    signal_id=101,
    candidate_id="manual:101:0",
    group_id="manual:101",
    entry=Decimal("4470"),
    entry_end=Decimal("4473"),
    stop_loss=Decimal("4467"),
    old_manual_result=-5,
    old_raw_result=Decimal("-5.216666666666667"),
    old_result_stop_pips=Decimal("50.6"),
    pips_log_id=67,
    manual_closed_at=1787226528,
    result_closed_at=1787226527,
    legs=(
      IncidentLeg(
        49183012, 40662911, Decimal("4473"), Decimal("4472.06"),
        600, Decimal("4466.86"), Decimal("-52"), Decimal("50.6"),
      ),
      IncidentLeg(
        49183013, 40662912, Decimal("4471.5"), Decimal("4471.45"),
        400, Decimal("4466.86"), Decimal("-45.9"), Decimal("44.5"),
      ),
      IncidentLeg(
        49183014, 40662913, Decimal("4470"), Decimal("4469.99"),
        200, Decimal("4466.86"), Decimal("-31.3"), Decimal("29.9"),
      ),
    ),
  ),
  104: Incident(
    signal_id=104,
    candidate_id="manual:104:0",
    group_id="manual:104",
    entry=Decimal("4460"),
    entry_end=Decimal("4463"),
    stop_loss=Decimal("4457"),
    old_manual_result=-8,
    old_raw_result=Decimal("-8.016666666666667"),
    old_result_stop_pips=Decimal("58.9"),
    pips_log_id=68,
    manual_closed_at=1787227569,
    result_closed_at=1787227559,
    legs=(
      IncidentLeg(
        49193939, 40667286, Decimal("4463"), Decimal("4462.89"),
        600, Decimal("4455.14"), Decimal("-77.5"), Decimal("58.9"),
      ),
      IncidentLeg(
        49193940, 40667287, Decimal("4461.5"), Decimal("4461.39"),
        400, Decimal("4455.14"), Decimal("-62.5"), Decimal("43.9"),
      ),
      IncidentLeg(
        49193941, 40667288, Decimal("4460"), Decimal("4459.95"),
        200, Decimal("4455.14"), Decimal("-48.1"), Decimal("29.5"),
      ),
    ),
  ),
}


def _same_number(actual: Any, expected: Decimal) -> bool:
  if actual is None:
    return False
  try:
    return abs(Decimal(str(actual)) - expected) <= Decimal("0.000001")
  except Exception:
    return False


def _require(condition: bool, message: str) -> None:
  if not condition:
    raise RepairValidationError(message)


def _json_events(raw_items: Iterable[str | bytes], incident: Incident) -> list[dict]:
  events: list[dict] = []
  for index, raw in enumerate(raw_items):
    if isinstance(raw, bytes):
      raw = raw.decode()
    try:
      event = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
      raise RepairValidationError(
        f"signal {incident.signal_id}: lifecycle item {index} is invalid JSON"
      ) from exc
    _require(
      isinstance(event, dict),
      f"signal {incident.signal_id}: lifecycle item {index} is not an object",
    )
    events.append(event)
  _require(events, f"signal {incident.signal_id}: retained lifecycle is missing")
  return events


def _event_type(event: dict) -> str:
  return str(event.get("type") or "").strip()


def _validate_lifecycle(events: list[dict], incident: Incident) -> None:
  prefix = f"signal {incident.signal_id}"
  relevant = [
    event for event in events
    if _event_type(event) in {
      "manual_limit_placed", "manual_opened", "position_closed", "group_result",
    }
  ]
  for event in relevant:
    _require(event.get("candidate_id") == incident.candidate_id,
             f"{prefix}: candidate id diverged")
    _require(event.get("group_id") == incident.group_id,
             f"{prefix}: group id diverged")
    _require(str(event.get("symbol") or "").upper() == "XAU",
             f"{prefix}: lifecycle symbol is not XAU")
    _require(str(event.get("direction") or "").upper() == "BUY",
             f"{prefix}: lifecycle direction is not BUY")
    _require(str(event.get("stream") or "algo_manual") == "algo_manual",
             f"{prefix}: lifecycle stream is not algo_manual")

  tp_events = [event for event in events if _event_type(event) in _TP_EVENT_TYPES]
  _require(not tp_events, f"{prefix}: TP events exist; refusing loss-only repair")

  placed = [event for event in events if _event_type(event) == "manual_limit_placed"]
  opened = [event for event in events if _event_type(event) == "manual_opened"]
  closed = [event for event in events if _event_type(event) == "position_closed"]
  results = [event for event in events if _event_type(event) == "group_result"]
  _require(len(placed) == 3, f"{prefix}: expected 3 placed legs, got {len(placed)}")
  _require(len(opened) == 3, f"{prefix}: expected 3 filled legs, got {len(opened)}")
  _require(len(closed) == 3, f"{prefix}: expected 3 closed legs, got {len(closed)}")
  # Every count-based check above passed - a leftover divergence at this
  # point (extra/missing bookkeeping events, or the right events in the
  # wrong order) is subtler than any single count check catches, so fail
  # closed on the full exact sequence as a final safety net.
  event_types = tuple(_event_type(event) for event in events)
  _require(
    event_types == _EXPECTED_EVENT_TYPES,
    f"{prefix}: retained lifecycle sequence diverged",
  )
  _require(len(results) == 1, f"{prefix}: expected one terminal group result")
  terminal = results[0]
  _require(
    int(terminal.get("timestamp") or 0) == incident.result_closed_at,
    f"{prefix}: terminal group timestamp diverged",
  )
  _require(
    int(terminal.get("position_id") or 0) == incident.legs[-1].position_id,
    f"{prefix}: terminal group position diverged",
  )
  _require(
    _same_number(terminal.get("group_realized_pips"), incident.old_raw_result),
    f"{prefix}: malformed terminal result fingerprint diverged",
  )

  placed_by_order = {int(event.get("order_id") or 0): event for event in placed}
  opened_by_position = {
    int(event.get("position_id") or 0): event for event in opened
  }
  closed_by_position = {
    int(event.get("position_id") or 0): event for event in closed
  }
  _require(len(placed_by_order) == 3, f"{prefix}: placed order ids are incomplete")
  _require(
    set(opened_by_position) == incident.position_ids,
    f"{prefix}: filled position ids diverged",
  )
  _require(
    set(closed_by_position) == incident.position_ids,
    f"{prefix}: closed position ids diverged",
  )

  total_volume = sum(leg.volume for leg in incident.legs)
  for index, leg in enumerate(incident.legs, start=1):
    plan = placed_by_order.get(leg.order_id)
    _require(plan is not None, f"{prefix}: placed order {leg.order_id} is missing")
    _require(int(plan.get("tranche_index") or 0) == index,
             f"{prefix}: planned tranche {index} diverged")
    _require(int(plan.get("volume") or 0) == leg.volume,
             f"{prefix}: planned volume for leg {index} diverged")
    _require(_same_number(plan.get("price"), leg.planned_entry),
             f"{prefix}: planned entry for leg {index} diverged")
    _require(_same_number(plan.get("stop_loss"), incident.stop_loss),
             f"{prefix}: planned stop for leg {index} diverged")

    fill = opened_by_position[leg.position_id]
    _require(int(fill.get("volume") or 0) == leg.volume,
             f"{prefix}: fill volume for position {leg.position_id} diverged")
    _require(_same_number(fill.get("price"), leg.fill_price),
             f"{prefix}: fill price for position {leg.position_id} diverged")
    _require(_same_number(fill.get("stop_loss"), incident.stop_loss),
             f"{prefix}: fill stop for position {leg.position_id} diverged")

    close = closed_by_position[leg.position_id]
    _require(int(close.get("volume") or 0) == leg.volume,
             f"{prefix}: close volume for position {leg.position_id} diverged")
    _require(
      close.get("remaining_volume") is not None
      and int(close["remaining_volume"]) == 0,
      f"{prefix}: position {leg.position_id} did not close completely",
    )
    _require(_same_number(close.get("price"), leg.exit_price),
             f"{prefix}: exit price for position {leg.position_id} diverged")
    _require(_same_number(close.get("leg_realized_pips"), leg.leg_result_pips),
             f"{prefix}: leg result for position {leg.position_id} diverged")
    _require(int(close.get("group_initial_volume") or 0) == total_volume,
             f"{prefix}: group initial volume diverged")


def _correct_values(incident: Incident) -> tuple[Decimal, int, Decimal]:
  total_volume = sum(leg.volume for leg in incident.legs)
  _require(total_volume > 0, f"signal {incident.signal_id}: invalid total volume")
  raw = sum(
    leg.leg_result_pips * leg.volume for leg in incident.legs
  ) / Decimal(total_volume)
  journal = int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
  pip = Decimal(str(pip_for("XAU")))
  _require(pip > 0, "XAU pip size is invalid")
  shallow_stop_pips = abs(
    incident.shallow_planned_entry - incident.stop_loss
  ) / pip
  return raw, journal, shallow_stop_pips


def _parse_loss_leg(row: dict, incident: Incident) -> tuple[str, list[dict]]:
  raw = row.get("legs")
  try:
    parsed = json.loads(raw)
  except (TypeError, json.JSONDecodeError) as exc:
    raise RepairValidationError(
      f"signal {incident.signal_id}: manual loss leg is invalid JSON"
    ) from exc
  _require(
    isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict),
    f"signal {incident.signal_id}: expected exactly one terminal loss leg",
  )
  _require(_same_number(parsed[0].get("frac"), Decimal("1")),
           f"signal {incident.signal_id}: terminal loss leg fraction diverged")
  _require(int(parsed[0].get("ts") or 0) == incident.manual_closed_at,
           f"signal {incident.signal_id}: terminal loss leg timestamp diverged")
  return raw, parsed


def _validate_static_db_rows(
  incident: Incident,
  signal: dict,
  pips_rows: list[dict],
  result: dict | None,
  fills: list[dict],
) -> tuple[str, list[dict]]:
  prefix = f"signal {incident.signal_id}"
  _require(signal, f"{prefix}: manual_signals row is missing")
  _require(str(signal.get("action") or "").upper() == "BUY",
           f"{prefix}: manual direction is not BUY")
  _require(str(signal.get("symbol") or "").upper() == "XAU",
           f"{prefix}: manual symbol is not XAU")
  _require(signal.get("status") == "closed", f"{prefix}: signal is not closed")
  _require(signal.get("execution_mode") == "algo",
           f"{prefix}: signal is not manual algo")
  _require(signal.get("trade_stream") == "algo_manual",
           f"{prefix}: manual trade stream diverged")
  _require(signal.get("execution_intent_id") == incident.candidate_id,
           f"{prefix}: execution intent diverged")
  _require(_same_number(signal.get("entry"), incident.entry),
           f"{prefix}: entry diverged")
  _require(_same_number(signal.get("entry_end"), incident.entry_end),
           f"{prefix}: entry end diverged")
  _require(_same_number(signal.get("original_sl"), incident.stop_loss),
           f"{prefix}: original stop diverged")
  _require(int(signal.get("closed_at") or 0) == incident.manual_closed_at,
           f"{prefix}: manual close timestamp diverged")
  _require(str(signal.get("broker_position_id")) == str(incident.legs[0].position_id),
           f"{prefix}: broker position anchor diverged")
  old_legs, parsed_legs = _parse_loss_leg(signal, incident)

  _require(len(pips_rows) == 1, f"{prefix}: expected exactly one pips_log row")
  pips_row = pips_rows[0]
  _require(int(pips_row.get("id") or 0) == incident.pips_log_id,
           f"{prefix}: pips_log id diverged")
  _require(pips_row.get("sign") == "-", f"{prefix}: pips_log sign diverged")

  _require(result is not None, f"{prefix}: auto_trade_results row is missing")
  _require(result.get("group_id") == incident.group_id,
           f"{prefix}: result group diverged")
  _require(result.get("trade_key") == incident.group_id,
           f"{prefix}: result trade key diverged")
  _require(result.get("trade_stream") == "algo_manual",
           f"{prefix}: result stream diverged")
  _require(str(result.get("symbol") or "").upper() == "XAU",
           f"{prefix}: result symbol diverged")
  _require(int(result.get("closed_at") or 0) == incident.result_closed_at,
           f"{prefix}: result close timestamp diverged")
  _require(result.get("booked_tp_count") is None,
           f"{prefix}: TP was booked; refusing loss-only repair")

  _require(len(fills) == 3, f"{prefix}: expected exactly 3 persisted fills")
  fills_by_position = {int(row["position_id"]): row for row in fills}
  _require(set(fills_by_position) == incident.position_ids,
           f"{prefix}: persisted fill ids diverged")
  for leg in incident.legs:
    fill = fills_by_position[leg.position_id]
    _require(fill.get("group_id") == incident.group_id,
             f"{prefix}: fill group diverged")
    _require(fill.get("trade_key") == incident.group_id,
             f"{prefix}: fill trade key diverged")
    _require(fill.get("trade_stream") == "algo_manual",
             f"{prefix}: fill stream diverged")
    _require(str(fill.get("symbol") or "").upper() == "XAU",
             f"{prefix}: fill symbol diverged")
    _require(str(fill.get("direction") or "").upper() == "BUY",
             f"{prefix}: fill direction diverged")
    _require(int(fill.get("volume") or 0) == leg.volume,
             f"{prefix}: persisted fill volume diverged")
    _require(_same_number(fill.get("entry_price"), leg.fill_price),
             f"{prefix}: persisted fill price diverged")
  return old_legs, parsed_legs


def _is_old_state(
  incident: Incident,
  signal: dict,
  pips_row: dict,
  result: dict,
  fills: list[dict],
  parsed_legs: list[dict],
) -> bool:
  return all((
    signal.get("result_pips") == incident.old_manual_result,
    parsed_legs[0].get("pips") == incident.old_manual_result,
    _same_number(signal.get("broker_fill_price"), incident.deep_fill),
    pips_row.get("pips") == abs(incident.old_manual_result),
    _same_number(result.get("result_pips"), incident.old_raw_result),
    _same_number(result.get("stop_pips"), incident.old_result_stop_pips),
    result.get("correction_source") is None,
    result.get("corrected_at") is None,
    all(
      _same_number(row.get("stop_pips"), leg.old_fill_stop_pips)
      and row.get("correction_source") is None
      and row.get("corrected_at") is None
      for leg in incident.legs
      for row in fills
      if int(row["position_id"]) == leg.position_id
    ),
  ))


def _is_corrected_state(
  incident: Incident,
  signal: dict,
  pips_row: dict,
  result: dict,
  fills: list[dict],
  parsed_legs: list[dict],
  raw_result: Decimal,
  journal_result: int,
  shallow_stop_pips: Decimal,
) -> bool:
  return all((
    signal.get("result_pips") == journal_result,
    parsed_legs[0].get("pips") == journal_result,
    _same_number(signal.get("broker_fill_price"), incident.shallow_fill),
    pips_row.get("pips") == abs(journal_result),
    _same_number(result.get("result_pips"), raw_result),
    _same_number(result.get("stop_pips"), shallow_stop_pips),
    result.get("booked_tp_count") is None,
    result.get("correction_source") == CORRECTION_SOURCE,
    result.get("corrected_at") is not None,
    all(
      _same_number(row.get("stop_pips"), shallow_stop_pips)
      and row.get("correction_source") == CORRECTION_SOURCE
      and row.get("corrected_at") is not None
      for row in fills
    ),
  ))


async def _load_locked_rows(db: Any, incident: Incident) -> tuple:
  signal_row = await db.fetchrow(
    "SELECT * FROM manual_signals WHERE id = $1 FOR UPDATE",
    incident.signal_id,
  )
  pips_rows = await db.fetch(
    "SELECT * FROM pips_log WHERE signal_id = $1 ORDER BY id FOR UPDATE",
    incident.signal_id,
  )
  result_row = await db.fetchrow(
    "SELECT * FROM auto_trade_results WHERE group_id = $1 FOR UPDATE",
    incident.group_id,
  )
  fill_rows = await db.fetch(
    "SELECT * FROM auto_trade_fills WHERE group_id = $1 "
    "ORDER BY position_id FOR UPDATE",
    incident.group_id,
  )
  return (
    {} if signal_row is None else dict(signal_row),
    [dict(row) for row in pips_rows],
    None if result_row is None else dict(result_row),
    [dict(row) for row in fill_rows],
  )


def _changed(status: str, expected: int, label: str) -> None:
  actual = store._rowcount(status)
  if actual != expected:
    raise RepairValidationError(
      f"CAS failed for {label}: expected {expected} row(s), changed {actual}"
    )


async def _apply_one(
  db: Any,
  incident: Incident,
  *,
  signal: dict,
  pips_row: dict,
  result: dict,
  fills: list[dict],
  old_legs: str,
  parsed_legs: list[dict],
  raw_result: Decimal,
  journal_result: int,
  shallow_stop_pips: Decimal,
  corrected_at: int,
) -> None:
  repaired_legs = [dict(parsed_legs[0])]
  repaired_legs[0]["pips"] = journal_result
  repaired_legs_json = json.dumps(repaired_legs, separators=(",", ":"))

  status = await db.execute(
    """
    UPDATE manual_signals
    SET result_pips = $1, legs = $2, broker_fill_price = $3
    WHERE id = $4
      AND result_pips = $5
      AND legs = $6
      AND broker_fill_price = $7
      AND status = 'closed'
      AND execution_mode = 'algo'
      AND trade_stream = 'algo_manual'
    """,
    journal_result, repaired_legs_json, float(incident.shallow_fill),
    incident.signal_id, signal["result_pips"], old_legs,
    signal["broker_fill_price"],
  )
  _changed(status, 1, f"manual_signals/{incident.signal_id}")

  status = await db.execute(
    """
    UPDATE pips_log
    SET pips = $1
    WHERE id = $2 AND signal_id = $3 AND sign = '-'
      AND pips = $4
    """,
    abs(journal_result), incident.pips_log_id, incident.signal_id,
    pips_row["pips"],
  )
  _changed(status, 1, f"pips_log/{incident.pips_log_id}")

  status = await db.execute(
    """
    UPDATE auto_trade_results
    SET result_pips = $1,
        stop_pips = $2,
        correction_source = $3,
        corrected_at = $4
    WHERE group_id = $5
      AND result_pips = $6
      AND stop_pips = $7
      AND booked_tp_count IS NULL
      AND correction_source IS NULL
      AND corrected_at IS NULL
    """,
    float(raw_result), float(shallow_stop_pips), CORRECTION_SOURCE,
    corrected_at, incident.group_id, result["result_pips"],
    result["stop_pips"],
  )
  _changed(status, 1, f"auto_trade_results/{incident.group_id}")

  for leg in incident.legs:
    fill = next(
      row for row in fills if int(row["position_id"]) == leg.position_id
    )
    status = await db.execute(
      """
      UPDATE auto_trade_fills
      SET stop_pips = $1,
          correction_source = $2,
          corrected_at = $3
      WHERE position_id = $4
        AND group_id = $5
        AND entry_price = $6
        AND volume = $7
        AND stop_pips = $8
        AND correction_source IS NULL
        AND corrected_at IS NULL
      """,
      float(shallow_stop_pips), CORRECTION_SOURCE, corrected_at,
      leg.position_id, incident.group_id, fill["entry_price"], leg.volume,
      fill["stop_pips"],
    )
    _changed(status, 1, f"auto_trade_fills/{leg.position_id}")


async def repair(
  signal_ids: Iterable[int],
  *,
  apply: bool = False,
  client: Any | None = None,
) -> list[dict[str, Any]]:
  """Validate and optionally repair explicitly requested incident ids.

  The entire requested batch commits atomically. A second ``--apply`` is a
  no-op reported as ``already_repaired``; mixed or unfamiliar states fail.
  """
  requested = list(signal_ids)
  _require(requested, "at least one --signal-id is required")
  _require(len(requested) == len(set(requested)), "duplicate --signal-id")
  unknown = sorted(set(requested) - set(INCIDENTS))
  _require(
    not unknown,
    "unsupported signal id(s): " + ", ".join(str(value) for value in unknown),
  )
  incidents = [INCIDENTS[signal_id] for signal_id in sorted(requested)]
  redis = client or redis_state.get_client()
  lifecycle_by_id: dict[int, list[dict]] = {}
  for incident in incidents:
    key = f"auto_trade:lifecycle:{incident.candidate_id}"
    lifecycle = _json_events(await redis.lrange(key, 0, -1), incident)
    _validate_lifecycle(lifecycle, incident)
    lifecycle_by_id[incident.signal_id] = lifecycle

  reports: list[dict[str, Any]] = []
  corrected_at = int(time.time())
  async with store._connect() as db:
    async with db.transaction():
      prepared: list[dict[str, Any]] = []
      for incident in incidents:
        signal, pips_rows, result, fills = await _load_locked_rows(db, incident)
        old_legs, parsed_legs = _validate_static_db_rows(
          incident, signal, pips_rows, result, fills,
        )
        raw_result, journal_result, shallow_stop_pips = _correct_values(incident)
        pips_row = pips_rows[0]
        assert result is not None
        old_state = _is_old_state(
          incident, signal, pips_row, result, fills, parsed_legs,
        )
        corrected_state = _is_corrected_state(
          incident, signal, pips_row, result, fills, parsed_legs,
          raw_result, journal_result, shallow_stop_pips,
        )
        _require(
          old_state or corrected_state,
          f"signal {incident.signal_id}: database fingerprint is neither "
          "the audited anomaly nor a completed repair",
        )
        prepared.append({
          "incident": incident,
          "signal": signal,
          "pips_row": pips_row,
          "result": result,
          "fills": fills,
          "old_legs": old_legs,
          "parsed_legs": parsed_legs,
          "raw_result": raw_result,
          "journal_result": journal_result,
          "shallow_stop_pips": shallow_stop_pips,
          "already_repaired": corrected_state,
        })

      if apply:
        for item in prepared:
          if item["already_repaired"]:
            continue
          await _apply_one(
            db,
            item["incident"],
            signal=item["signal"],
            pips_row=item["pips_row"],
            result=item["result"],
            fills=item["fills"],
            old_legs=item["old_legs"],
            parsed_legs=item["parsed_legs"],
            raw_result=item["raw_result"],
            journal_result=item["journal_result"],
            shallow_stop_pips=item["shallow_stop_pips"],
            corrected_at=corrected_at,
          )

      for item in prepared:
        incident = item["incident"]
        status = (
          "already_repaired" if item["already_repaired"]
          else "repaired" if apply
          else "would_repair"
        )
        reports.append({
          "signal_id": incident.signal_id,
          "status": status,
          "group_id": incident.group_id,
          "position_ids": sorted(incident.position_ids),
          "result_pips_before": incident.old_manual_result,
          "result_pips_after": item["journal_result"],
          "raw_result_pips_after": float(item["raw_result"]),
          "stop_pips_after": float(item["shallow_stop_pips"]),
          "shallow_fill_after": float(incident.shallow_fill),
          "booked_tp_count": None,
          "lifecycle_event_count": len(lifecycle_by_id[incident.signal_id]),
        })
  return reports


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--signal-id",
    action="append",
    type=int,
    required=True,
    help="Verified manual signal id; repeat for each signal",
  )
  parser.add_argument(
    "--apply",
    action="store_true",
    help="Commit the repair; without this flag the command is read-only",
  )
  return parser


async def _run(args: argparse.Namespace) -> list[dict[str, Any]]:
  return await repair(args.signal_id, apply=args.apply)


def main() -> None:
  args = build_parser().parse_args()
  reports = asyncio.run(_run(args))
  print(json.dumps({
    "mode": "apply" if args.apply else "dry_run",
    "repairs": reports,
  }, indent=2, sort_keys=True))


if __name__ == "__main__":
  main()

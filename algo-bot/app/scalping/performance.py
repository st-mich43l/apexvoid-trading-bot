"""Performance join for own scalp mechanism research (observe-only).

Aggregates stamped rows into archetype × session × math_agree tables.
Outcomes are optional — when present, expectancy placeholders are filled.
Never tunes thresholds; never flips live allow/block.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from typing import Any, Iterable


def _agree_key(value: Any) -> str:
  if value is True:
    return "agree"
  if value is False:
    return "disagree"
  return "unknown"


def _cell_key(archetype: str, session: str, agree: str) -> tuple[str, str, str]:
  return (str(archetype or "unknown"), str(session or "unknown"), agree)


def normalize_row(raw: dict[str, Any]) -> dict[str, Any]:
  """Normalize a research / ledger / fixture row."""
  measured = raw.get("measured") if isinstance(raw.get("measured"), dict) else {}
  cf = measured.get("math_counterfactual") if isinstance(measured, dict) else None
  if not isinstance(cf, dict):
    cf = raw.get("math_counterfactual") if isinstance(raw.get("math_counterfactual"), dict) else {}
  math_agree = raw.get("math_agree")
  if math_agree is None and isinstance(measured, dict):
    math_agree = measured.get("math_agree")
  if math_agree is None and "math_would_allow" in raw:
    would = raw.get("math_would_allow")
    math_agree = None if would is None else bool(would)
  outcome = raw.get("outcome")
  if outcome is None:
    outcome = raw.get("result")
  pips = raw.get("realized_pips")
  if pips is None:
    pips = raw.get("group_realized_pips")
  try:
    pips_f = float(pips) if pips is not None else None
  except (TypeError, ValueError):
    pips_f = None
  return {
    "archetype": str(raw.get("archetype") or measured.get("archetype") or "unknown"),
    "session": str(raw.get("session") or "unknown"),
    "math_agree": math_agree,
    "math_model": raw.get("math_model") or cf.get("math_model"),
    "math_reason": raw.get("math_reason") or cf.get("reason_code"),
    "opportunity_id": raw.get("opportunity_id"),
    "outcome": outcome,
    "realized_pips": pips_f,
  }


def rows_from_math_shadow(payload: dict[str, Any]) -> list[dict[str, Any]]:
  """Extract agree_rows from a scalp:last_math_shadow payload."""
  research = payload.get("research") if isinstance(payload, dict) else None
  if not isinstance(research, dict):
    return []
  rows = research.get("agree_rows") or []
  session = str(payload.get("session") or "")
  out: list[dict[str, Any]] = []
  for row in rows:
    if not isinstance(row, dict):
      continue
    merged = dict(row)
    if not merged.get("session"):
      merged["session"] = session
    out.append(normalize_row(merged))
  return out


def aggregate_performance_rows(
  rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
  """Build archetype × session × math_agree performance table."""
  cells: dict[tuple[str, str, str], dict[str, Any]] = {}
  total = 0
  with_outcome = 0

  for raw in rows:
    row = normalize_row(raw)
    agree = _agree_key(row.get("math_agree"))
    key = _cell_key(row["archetype"], row["session"], agree)
    cell = cells.get(key)
    if cell is None:
      cell = {
        "archetype": key[0],
        "session": key[1],
        "math_agree": key[2],
        "count": 0,
        "outcomes": 0,
        "wins": 0,
        "losses": 0,
        "sum_realized_pips": 0.0,
        "math_reasons": defaultdict(int),
      }
      cells[key] = cell
    cell["count"] += 1
    total += 1
    reason = row.get("math_reason")
    if reason:
      cell["math_reasons"][str(reason)] += 1
    pips = row.get("realized_pips")
    outcome = row.get("outcome")
    if pips is not None or outcome is not None:
      with_outcome += 1
      cell["outcomes"] += 1
      if pips is not None:
        cell["sum_realized_pips"] += float(pips)
        if pips > 0:
          cell["wins"] += 1
        elif pips < 0:
          cell["losses"] += 1
      elif str(outcome).lower() in {"tp", "win", "take_profit"}:
        cell["wins"] += 1
      elif str(outcome).lower() in {"sl", "loss", "stop_loss", "group_stop_loss"}:
        cell["losses"] += 1

  table: list[dict[str, Any]] = []
  for cell in sorted(cells.values(), key=lambda c: (c["archetype"], c["session"], c["math_agree"])):
    outcomes = int(cell["outcomes"])
    wins = int(cell["wins"])
    losses = int(cell["losses"])
    sum_pips = float(cell["sum_realized_pips"])
    expectancy = None
    if outcomes > 0 and (wins + losses) > 0:
      expectancy = round(sum_pips / outcomes, 4)
    table.append({
      "archetype": cell["archetype"],
      "session": cell["session"],
      "math_agree": cell["math_agree"],
      "count": cell["count"],
      "outcomes": outcomes,
      "wins": wins,
      "losses": losses,
      "sum_realized_pips": round(sum_pips, 4),
      "expectancy_pips": expectancy,
      "math_reasons": dict(cell["math_reasons"]),
    })

  return {
    "version": 1,
    "total_rows": total,
    "rows_with_outcome": with_outcome,
    "table": table,
    "notes": (
      "Observe-only research join. expectancy_pips is null until outcomes "
      "are attached. Do not tune gates on holdout."
    ),
  }


def load_jsonl(path: str) -> list[dict[str, Any]]:
  rows: list[dict[str, Any]] = []
  with open(path, encoding="utf-8") as handle:
    for line in handle:
      text = line.strip()
      if not text:
        continue
      rows.append(json.loads(text))
  return rows


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description="Aggregate scalp research performance rows (observe-only).",
  )
  parser.add_argument(
    "--rows",
    help="JSONL of research/ledger rows (archetype, session, math_agree, optional outcome)",
  )
  parser.add_argument(
    "--math-shadow",
    help="JSON file with a scalp:last_math_shadow payload",
  )
  parser.add_argument(
    "--out",
    help="Write report JSON to this path (default stdout)",
  )
  args = parser.parse_args(argv)
  rows: list[dict[str, Any]] = []
  if args.rows:
    rows.extend(load_jsonl(args.rows))
  if args.math_shadow:
    with open(args.math_shadow, encoding="utf-8") as handle:
      payload = json.load(handle)
    rows.extend(rows_from_math_shadow(payload))
  if not rows:
    print("no rows loaded; pass --rows and/or --math-shadow", file=sys.stderr)
    return 2
  report = aggregate_performance_rows(rows)
  text = json.dumps(report, indent=2, sort_keys=True)
  if args.out:
    with open(args.out, "w", encoding="utf-8") as handle:
      handle.write(text)
      handle.write("\n")
  else:
    print(text)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())

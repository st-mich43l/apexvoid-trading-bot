"""Build the production group dataset the exposure-policy replay runs on.

Inputs are read-only exports of production: the auto_trade_results/fills join, the
analysis_opportunities structure ids and the algo-bot admission log lines (see
docs/risk/same-direction-exposure-incident.md for the exact queries). Output is
one JSON record per group with its admission, fill and close times, planned
money risk and derived money result.

  python -m tools.exposure_dataset groups.psv opps.psv admission-lines.log out.json

Money is derived, never broker-confirmed: lots x pips x the instrument pip value
from config/instruments.yml. Nothing here writes to production.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# pip_value_per_lot and broker volume units per lot (config/instruments.yml).
CONTRACTS = {
  "XAU": (10.0, 10_000),
  "EURUSD": (10.0, 10_000_000),
  "GBPUSD": (10.0, 10_000_000),
  "GBPJPY": (7.0, 10_000_000),
  "USDJPY": (6.27, 10_000_000),
}

_PUBLISHED = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ .*v8 plan published id=(\S+) symbol=(\S+) strategy=(.+?) direction=(\S+) entry_type=(\S+)")
_STACK = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ .*v8 same-direction stack symbol=(\S+) match_id=(\S+) ")


def _epoch(text: str) -> int:
  return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def build(groups_psv: Path, opps_psv: Path, log: Path) -> list[dict]:
  opps: dict[str, dict] = {}
  for line in opps_psv.read_text().splitlines():
    parts = line.split("|")
    if len(parts) >= 8:
      opps[parts[0]] = {"opportunity_created_at": int(parts[1]), "structure_id": parts[2], "formed_at": parts[3] or None}
  published: dict[str, dict] = {}
  stacks: list[tuple[int, str, str]] = []
  for line in log.read_text().splitlines():
    match = _PUBLISHED.match(line)
    if match:
      published[match.group(2)] = {"admitted_at": _epoch(match.group(1)), "entry_type": match.group(6)}
      continue
    match = _STACK.match(line)
    if match:
      stacks.append((_epoch(match.group(1)), match.group(2), match.group(3)))
  out: list[dict] = []
  for line in groups_psv.read_text().splitlines():
    p = line.split("|")
    if len(p) < 17:
      continue
    group_id, stream, symbol, strategy, direction, session, exit_path = p[0], p[1], p[2], p[3], p[4], p[5], p[6]
    result_pips, stop_pips = float(p[7]), float(p[8] or 0)
    closed_at, first_fill, volume = int(p[11]), int(p[12]), int(p[14])
    pip_value, units = CONTRACTS[symbol]
    lots = volume / units
    admitted = published.get(group_id)
    short = group_id[len("v8:"):][:12]
    stacked = bool(admitted) and any(
      s_symbol == symbol and match_id.startswith(short[:9]) and 0 <= admitted["admitted_at"] - at <= 30
      for at, s_symbol, match_id in stacks
    )
    out.append({
      "group_id": group_id,
      "stream": stream,
      "symbol": symbol,
      "strategy": strategy,
      "direction": direction,
      "session": session,
      "exit_path": exit_path,
      "admitted_at": None if admitted is None else admitted["admitted_at"],
      "entry_type": None if admitted is None else admitted["entry_type"],
      "stacked_on_admission": stacked,
      "first_fill_at": first_fill,
      "closed_at": closed_at,
      "lots": round(lots, 4),
      "stop_pips": round(stop_pips, 2),
      "avg_entry": float(p[16]),
      "result_pips": result_pips,
      "planned_risk": round(lots * stop_pips * pip_value, 2),
      "derived_pnl": round(lots * result_pips * pip_value, 2),
      "structure_id": (opps.get(group_id) or {}).get("structure_id") or None,
    })
  out.sort(key=lambda row: (row["admitted_at"] or row["first_fill_at"], row["group_id"]))
  return out


if __name__ == "__main__":
  rows = build(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
  Path(sys.argv[4]).write_text(json.dumps(rows, indent=1) + "\n")
  missing = sum(1 for row in rows if row["admitted_at"] is None)
  print(f"{len(rows)} groups, {missing} without an admission log line")

"""Chronological replay of same-direction exposure policies on production groups.

Runs the real admission decision (``group_exposure.evaluate_group_exposure``) over
the groups production actually admitted, in admission order, with each blocked
group removed from the book so every later decision sees the exposure that policy
would really have produced (a naive "delete the loser" counterfactual would keep
exposure that never existed).

  python -m tools.exposure_policy_replay dataset.json out.json

What this is not: it cannot create trades. A candidate production refused for
another reason (corridor overlap, thesis claim) and that a freed slot would have
let through is not in the dataset; the count of corridor refusals that follow a
removed group is reported as an upper bound. Money is derived from lots x pips x
the instrument pip value, never broker-confirmed. A group's risk is its planned
risk for as long as it is open (no credit for a TP1 or a moved stop), which is the
conservative reading. Groups without an admission log line enter at their first fill.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.autotrade.group_exposure import GroupExposure, IncomingGroup, evaluate_group_exposure
from app.runtime.instruments import SameDirectionLimits, SameDirectionPolicy

CLUSTER_SECONDS = 1200


def policies(cumulative_budget: float = 55.0, symbol_budget: float = 80.0) -> dict[str, SameDirectionPolicy | None]:
  """The four alternatives; ``None`` is the current behaviour (no bound)."""
  return {
    "A_unlimited": None,
    "B_one_group": SameDirectionPolicy(symbol="*", limits=SameDirectionLimits(max_groups=1), duplicate_thesis_block=False),
    "C_two_groups_budget": SameDirectionPolicy(
      symbol="*", limits=SameDirectionLimits(max_groups=2, max_cumulative_risk=cumulative_budget), duplicate_thesis_block=False,
    ),
    "D_one_per_thesis_symbol_ceiling": SameDirectionPolicy(
      symbol="*", limits=SameDirectionLimits(max_symbol_risk=symbol_budget), duplicate_thesis_block=True,
    ),
  }


def _entry(row: dict) -> int:
  return row["admitted_at"] if row["admitted_at"] is not None else row["first_fill_at"]


def _group(row: dict) -> GroupExposure:
  return GroupExposure(
    plan_id=row["group_id"], setup_id=row["group_id"], symbol=row["symbol"], direction=row["direction"],
    source="runtime", structural_id=row["structure_id"], thesis_id=None,
    risk_amount=row["planned_risk"], risk_basis="runtime",
  )


def run(rows: list[dict], policy: SameDirectionPolicy | None) -> dict[str, Any]:
  allowed: list[dict] = []
  blocked: list[dict] = []
  active: list[dict] = []
  reasons: dict[str, int] = defaultdict(int)
  for row in sorted(rows, key=lambda r: (_entry(r), r["group_id"])):
    now = _entry(row)
    active = [g for g in active if g["closed_at"] > now]
    book = [g for g in active if g["symbol"] == row["symbol"]]
    # The autonomous policy never refuses a manual group, but it occupies the book.
    if policy is not None and row["stream"] == "algo_auto":
      decision = evaluate_group_exposure(
        policy,
        IncomingGroup(
          setup_id=row["group_id"], plan_id=row["group_id"], symbol=row["symbol"], direction=row["direction"],
          strategy=row["strategy"], thesis_id=None, structural_id=row["structure_id"], risk_amount=row["planned_risk"],
        ),
        [_group(g) for g in book],
      )
      if not decision.allowed:
        blocked.append(row)
        reasons[str(decision.reason_code)] += 1
        continue
    allowed.append(row)
    active.append(row)
  return {"allowed": allowed, "blocked": blocked, "reasons": dict(reasons)}


def _peak(rows: list[dict], key=lambda g: (g["symbol"], g["direction"])) -> tuple[int, float]:
  events: list[tuple[int, int, dict]] = []
  for row in rows:
    events.append((_entry(row), 1, row))
    events.append((row["closed_at"], 0, row))
  events.sort(key=lambda e: (e[0], e[1]))
  live: dict[Any, dict[str, dict]] = defaultdict(dict)
  peak_groups, peak_risk = 0, 0.0
  for _, is_open, row in events:
    book = live[key(row)]
    if is_open:
      book[row["group_id"]] = row
    else:
      book.pop(row["group_id"], None)
    peak_groups = max(peak_groups, len(book))
    peak_risk = max(peak_risk, sum(g["planned_risk"] for g in book.values()))
  return peak_groups, peak_risk


def _clusters(rows: list[dict]) -> list[dict]:
  stops = sorted((r for r in rows if r["exit_path"] == "full_stop"), key=lambda r: r["closed_at"])
  out: list[dict] = []
  used: set[str] = set()
  for index, row in enumerate(stops):
    if row["group_id"] in used:
      continue
    members = [row]
    for other in stops[index + 1:]:
      if other["symbol"] == row["symbol"] and other["direction"] == row["direction"] and other["closed_at"] - row["closed_at"] <= CLUSTER_SECONDS and other["group_id"] not in used:
        members.append(other)
    if len(members) >= 2:
      used.update(m["group_id"] for m in members)
      out.append({
        "symbol": row["symbol"], "direction": row["direction"], "closed_at": row["closed_at"],
        "groups": len(members), "loss": round(sum(m["derived_pnl"] for m in members), 2),
        "strategies": sorted({m["strategy"] for m in members}),
      })
  return out


def summarise(rows: list[dict], result: dict[str, Any]) -> dict[str, Any]:
  allowed, blocked = result["allowed"], result["blocked"]
  groups, risk = _peak(allowed)
  clusters = _clusters(allowed)
  pnl = [g["derived_pnl"] for g in allowed]
  r_values = [g["result_pips"] / g["stop_pips"] for g in allowed if g["stop_pips"] > 0]
  return {
    "groups_allowed": len(allowed),
    "groups_blocked": len(blocked),
    "blocked_by_reason": result["reasons"],
    "peak_concurrent_groups_per_symbol_direction": groups,
    "peak_cumulative_planned_risk": round(risk, 2),
    "correlated_stop_clusters": len(clusters),
    "correlated_cluster_loss": round(sum(c["loss"] for c in clusters), 2),
    "derived_pnl_allowed": round(sum(pnl), 2),
    "derived_pnl_blocked": round(sum(g["derived_pnl"] for g in blocked), 2),
    "blocked_winners": sum(1 for g in blocked if g["derived_pnl"] > 0),
    "blocked_losers": sum(1 for g in blocked if g["derived_pnl"] < 0),
    "mean_r_allowed": round(statistics.fmean(r_values), 3) if r_values else None,
    "worst_group_loss": round(min(pnl), 2) if pnl else 0.0,
    "worst_cluster": min(clusters, key=lambda c: c["loss"]) if clusters else None,
  }


def evaluate(rows: list[dict], *, cumulative_budget: float = 55.0, symbol_budget: float = 80.0) -> dict[str, Any]:
  autonomous = [r for r in rows if r["stream"] == "algo_auto"]
  manual = [r for r in rows if r["stream"] != "algo_auto"]
  out: dict[str, Any] = {"groups": len(autonomous), "manual_groups_counted_as_exposure_only": len(manual), "by_policy": {}}
  scopes = {"all_symbols": autonomous}
  for symbol in sorted({r["symbol"] for r in autonomous}):
    scopes[symbol] = [r for r in autonomous if r["symbol"] == symbol]
  scopes["USDJPY_2026-10-12_to_14"] = [
    r for r in autonomous if r["symbol"] == "USDJPY" and r["closed_at"] >= 1_791_763_200 and _entry(r) < 1_791_936_000
  ]
  split = 1_791_590_400  # 2026-10-10 00:00 UTC: earlier half is development, later half held out
  usdjpy = [r for r in autonomous if r["symbol"] == "USDJPY"]
  scopes["USDJPY_before_2026-10-10"] = [r for r in usdjpy if _entry(r) < split]
  scopes["USDJPY_from_2026-10-10"] = [r for r in usdjpy if _entry(r) >= split]
  for name, policy in policies(cumulative_budget, symbol_budget).items():
    out["by_policy"][name] = {}
    for scope, rows_in_scope in scopes.items():
      book = rows_in_scope + [m for m in manual if scope in ("all_symbols", "XAU")]
      result = run(book, policy)
      result["allowed"] = [g for g in result["allowed"] if g["stream"] == "algo_auto"]
      result["blocked"] = [g for g in result["blocked"] if g["stream"] == "algo_auto"]
      out["by_policy"][name][scope] = summarise(rows_in_scope, result)
  out["usdjpy_group_limit_sweep"] = {}
  for limit in (1, 2, 3, 4):
    swept = SameDirectionPolicy(symbol="USDJPY", limits=SameDirectionLimits(max_groups=limit), duplicate_thesis_block=False)
    out["usdjpy_group_limit_sweep"][str(limit)] = {
      scope: summarise(rows_in, _scoped(rows_in, swept))
      for scope, rows_in in (("all", usdjpy), ("before_2026-10-10", scopes["USDJPY_before_2026-10-10"]), ("from_2026-10-10", scopes["USDJPY_from_2026-10-10"]))
    }
  return out


def _scoped(rows: list[dict], policy: SameDirectionPolicy | None) -> dict[str, Any]:
  result = run(rows, policy)
  return result


def corridor_refusals_after_removed_groups(log_lines: list[str]) -> int:
  """Upper bound on replacement trades: corridor refusals a freed slot could let through."""
  return sum(1 for line in log_lines if "reason=entry_zone_overlap_same_direction" in line and "plan build rejected" not in line)


if __name__ == "__main__":
  dataset = json.loads(Path(sys.argv[1]).read_text())
  result = evaluate(dataset)
  Path(sys.argv[2]).write_text(json.dumps(result, indent=1) + "\n")
  for name, scopes in result["by_policy"].items():
    row = scopes["all_symbols"]
    print(f"{name:34s} allowed {row['groups_allowed']:3d} blocked {row['groups_blocked']:3d} peak {row['peak_concurrent_groups_per_symbol_direction']} "
          f"clusters {row['correlated_stop_clusters']} cluster_loss {row['correlated_cluster_loss']:8.2f} pnl {row['derived_pnl_allowed']:9.2f} blocked_pnl {row['derived_pnl_blocked']:9.2f}")

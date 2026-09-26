"""S14G: the pre-grant operator packet. Read-only. It presents facts; it never decides.

  python -m app.scripts.cutover_packet --symbol XAU --scope supply --equity 2000 \\
      [--acceptance-report /reports/acceptance.json] [--images-json images.json] \\
      [--drain-seconds 30] --json packet.json --md packet.md

The packet shows the current production status (authority rows, configuration), the evidence report and
its blockers, deployed SHAs, the proposed scope, the maximum exposure the existing controls permit, open
positions and tracked/pending plans, the exact rollback commands and the projected activation boundary.
Anything it cannot read is printed as ``unavailable``; it never estimates a missing fact.

**It stops.** It never calls ``record_acceptance`` or a transfer, and it prints the accept/grant
command templates only with placeholders for the operator's own name and evidence reference. A grant
needs an explicit operator decision after reading this packet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

STOP_NOTICE = (
  "STOP. This packet grants nothing and records nothing. A live grant requires the operator's explicit "
  "approval, in their own name, citing the evidence they reviewed. Do not run the templates below with a "
  "generic evidence reference or on anyone's behalf."
)


def _load(path: str | None) -> dict[str, Any] | None:
  return json.loads(Path(path).read_text()) if path else None


def maximum_exposure(equity: float | None, *, stop_max_pips: int, risk_leg_gate: bool) -> dict[str, Any]:
  """What the existing Auto Algo controls permit for ONE group at this equity (worst case, every leg fills)."""
  if equity is None:
    return {"status": "unavailable", "reason": "no --equity supplied"}
  from app.autotrade import xau_ladder
  lots = xau_ladder.equity_table_lots(equity)
  pip_value = 10.0
  ladder_loss = lots * stop_max_pips * pip_value                     # every ladder lot at the widest permitted stop
  risk_leg_lots = xau_ladder.risk_leg_volume(equity) if risk_leg_gate else 0.0
  risk_leg_loss = risk_leg_lots * xau_ladder.RISK_LEG_PIPS_FROM_STOP * pip_value
  total = ladder_loss + risk_leg_loss
  return {
    "status": "computed", "equity": equity, "equity_table_lots": lots, "stop_max_pips": stop_max_pips,
    "worst_case_ladder_loss": round(ladder_loss, 2), "risk_leg_enabled_for_go": risk_leg_gate,
    "worst_case_risk_leg_loss": round(risk_leg_loss, 2), "worst_case_group_loss": round(total, 2),
    "worst_case_group_loss_percent_of_equity": round(total / equity * 100, 2) if equity > 0 else None,
    "one_plan_per_structural_thesis": True,
    "notes": [
      "Concurrency across theses stays under the existing Auto Algo open-exposure and account-risk controls; Go does not change them.",
      "risk.max_group_risk_percent is declarative today (the executor never reads it); see docs/analysis/s14e-ladder-precision-risk.md.",
    ],
  }


async def build_packet(args: argparse.Namespace, *, db: Any, redis: Any | None) -> dict[str, Any]:
  from app.analysis_client.authority import DEFAULT_DRAIN_SECONDS
  from app.autotrade.go_opportunity_policy import REVIEWED_SCOPES
  from app.core.config import runtime_config

  symbol, scope = args.symbol.upper(), args.scope
  authority = runtime_config.analysis.technical_authority
  rows = [dict(r) for r in await db.fetch(
    "SELECT symbol, strategy_id, owner, target_owner, epoch, drain_until, updated_by, reason, evidence_ref FROM analysis_authority_scopes ORDER BY symbol, strategy_id")]
  current = next((r for r in rows if r["symbol"] == symbol and r["strategy_id"] == scope), None)
  epoch = current["epoch"] if current else 0
  drain = int(args.drain_seconds if args.drain_seconds is not None else DEFAULT_DRAIN_SECONDS)
  now = int(time.time())
  acceptance = _load(args.acceptance_report)
  blockers = [g for g in (acceptance or {}).get("gates", []) if g["status"] != "pass"]
  exposures: dict[str, Any] = {"status": "unavailable", "reason": "Redis not reachable"}
  if redis is not None:
    try:
      from app.autotrade.active_exposure import load_active_exposures
      live = await load_active_exposures(redis, symbol=symbol)
      exposures = {"status": "read", "count": len(live), "items": [
        {"source": e.source, "direction": e.direction, "entry_price": e.entry_price, "group_id": e.group_id, "plan_id": e.plan_id, "position_id": e.position_id,
         "remaining_volume": e.remaining_volume, "go_origin": bool(e.plan_id and str(e.plan_id).startswith("v8:go_"))} for e in live]}
      go_plans = int(await redis.hlen("analysis:go_plans"))
      exposures["registered_go_plans"] = go_plans
    except Exception as exc:  # noqa: BLE001
      exposures = {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}
  eligible = scope in REVIEWED_SCOPES and symbol == "XAU"
  cmd = "python -m app.scripts.analysis_authority"
  return {
    "kind": "s14g_cutover_packet", "version": 1, "generated_at": now, "STOP": STOP_NOTICE,
    "production_status": {
      "configuration": {"mode": authority.mode, "consumer_enabled": authority.consumer_enabled, "consumer_group": authority.consumer_group,
                        "go_origin_risk_leg_enabled": authority.go_origin_risk_leg_enabled},
      "authority_scopes": rows or "none recorded: every scope is Python-owned at epoch 0",
      "current_epoch_for_scope": epoch, "epoch_source": "analysis_authority_scopes row" if current else "absent row == Python at epoch 0 (read from status, not assumed)",
    },
    "evidence": {"acceptance_report": acceptance and {"overall": acceptance.get("overall"), "window": acceptance.get("window")} or "unavailable: no acceptance report supplied",
                 "blocking_gates": [{"name": g["name"], "status": g["status"]} for g in blockers]},
    "deployed_shas": _load(args.images_json) or "unavailable: no --images-json supplied",
    "proposed_scope": {"symbol": symbol, "scope": scope, "eligible": eligible,
                       "why": "reviewed XAU supply/demand scope" if eligible else "not a reviewed scope: leave it Python-owned"},
    "maximum_permitted_exposure": maximum_exposure(args.equity, stop_max_pips=int(runtime_config.execution.reaction.stop_max_pips),
                                                   risk_leg_gate=bool(authority.go_origin_risk_leg_enabled)),
    "open_exposure_and_pending_plans": exposures,
    "activation_boundary": {
      "rule": "Go becomes the effective publisher when the drain window ends; only opportunities observed at or after that instant may become new plans. Nothing publishes during the drain.",
      "drain_seconds": drain, "projected_if_granted_now": now + drain,
      "note": "the real boundary is drain_until of the row the grant writes (printed by `status`)",
    },
    "rollback_commands": {
      "status": f"{cmd} status --symbol {symbol}",
      "rollback_scope": f"{cmd} rollback --symbol {symbol} --scope {scope} --expected-epoch <epoch printed by status> --actor <your name> --reason <why>",
      "rollback_everything": f"{cmd} rollback-all --actor <your name> --reason <why>",
      "withdraw_only_redis_needed": f"{cmd} withdraw --symbol {symbol} --scope {scope} --actor <your name> --reason <why>",
      "rollback_step_order": "1 fence flips (Go stops now, Python after the drain), 2 withdrawal (matches, queued and unfilled plans; open positions kept)",
    },
    "operator_action_required": {
      "explicit_approval_needed": True,
      "templates_not_executed": [
        f"{cmd} accept --symbol {symbol} --scope {scope} --evidence <evidence reference you reviewed> --approved-by <your name> --ttl-hours <n>",
        f"{cmd} status --symbol {symbol}   # read the current epoch; never assume it is zero",
        f"{cmd} grant --symbol {symbol} --scope {scope} --expected-epoch <epoch from status> --evidence <same reference> --actor <your name> --reason <why> --drain-seconds {drain}",
      ],
    },
  }


def render_markdown(packet: dict[str, Any]) -> str:
  ps, ex, bd = packet["production_status"], packet["maximum_permitted_exposure"], packet["activation_boundary"]
  lines = [
    f"# S14G cutover packet: {packet['proposed_scope']['symbol']} {packet['proposed_scope']['scope']}", "", f"> **{packet['STOP']}**", "",
    "## Production status", f"- Configuration: {ps['configuration']}", f"- Authority scopes: {ps['authority_scopes']}",
    f"- Current epoch for this scope: **{ps['current_epoch_for_scope']}** ({ps['epoch_source']}).", "",
    "## Evidence", f"- Acceptance report: {packet['evidence']['acceptance_report']}",
    f"- Blocking gates: {packet['evidence']['blocking_gates'] or 'none reported'}", f"- Deployed SHAs: {packet['deployed_shas']}", "",
    "## Proposed scope", f"- {packet['proposed_scope']}", "", "## Maximum permitted exposure (one group, existing controls)", f"- {ex}", "",
    "## Open exposure and pending plans", f"- {packet['open_exposure_and_pending_plans']}", "",
    "## Activation boundary", f"- {bd['rule']}", f"- Drain {bd['drain_seconds']} s; projected boundary if granted now: {bd['projected_if_granted_now']} ({bd['note']}).", "",
    "## Rollback (works without Kafka)", *[f"- `{v}`" if k != "rollback_step_order" else f"- Order: {v}" for k, v in packet["rollback_commands"].items()], "",
    "## Operator action (NOT executed)", *[f"- `{c}`" for c in packet["operator_action_required"]["templates_not_executed"]],
  ]
  return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> dict[str, Any]:
  from app.persistence import redis_state, store
  await store.init_db()
  try:
    redis = redis_state.get_client()
    await redis.ping()
  except Exception:  # noqa: BLE001
    redis = None
  async with store._connect() as db:
    return await build_packet(args, db=db, redis=redis)


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--symbol", default="XAU")
  parser.add_argument("--scope", required=True, help="catalog scope, e.g. supply")
  parser.add_argument("--equity", type=float, default=None, help="current account equity, to compute the exposure ceiling")
  parser.add_argument("--acceptance-report", default=None)
  parser.add_argument("--images-json", default=None)
  parser.add_argument("--drain-seconds", type=int, default=None)
  parser.add_argument("--json", required=True)
  parser.add_argument("--md", required=True)
  return parser


def main(argv: list[str] | None = None) -> int:
  args = build_parser().parse_args(argv)
  packet = asyncio.run(run(args))
  Path(args.json).write_text(json.dumps(packet, indent=2, sort_keys=True, default=str) + "\n")
  Path(args.md).write_text(render_markdown(packet))
  print(packet["STOP"])
  return 0


if __name__ == "__main__":
  sys.exit(main())

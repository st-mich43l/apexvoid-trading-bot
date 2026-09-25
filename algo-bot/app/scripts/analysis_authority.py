"""Operator CLI for the S13B technical-authority fence.

Nothing here runs automatically. A scope moves to Go only when a human records
an acceptance naming the evidence and then requests the handover; rollback
needs neither. Every command prints one JSON document.

  python -m app.scripts.analysis_authority status [--symbol XAU]
  python -m app.scripts.analysis_authority accept   --symbol XAU --scope supply \\
      --evidence <ref> --approved-by <name> --ttl-hours 24
  python -m app.scripts.analysis_authority grant    --symbol XAU --scope supply \\
      --expected-epoch 0 --evidence <ref> --actor <name> --reason <text>
  python -m app.scripts.analysis_authority rollback --symbol XAU --scope supply \\
      --expected-epoch 3 --actor <name> --reason <text>
  python -m app.scripts.analysis_authority rollback-all --actor <name> --reason <text>

`grant` fails unless an unexpired `accept` for the same symbol/scope/evidence
exists; `--expected-epoch` (from `status`) makes a stale operator lose the
compare-and-set instead of overwriting a newer decision.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict

from app.analysis_client.authority import (
  DEFAULT_DRAIN_SECONDS,
  AuthorityFence,
  PostgresAuthorityStore,
  TransferRefused,
)


async def _status(store: PostgresAuthorityStore, symbol: str | None) -> dict:
  from app.persistence import store as db_store
  now = time.time()
  async with db_store._connect() as db:
    if symbol:
      rows = await db.fetch("SELECT * FROM analysis_authority_scopes WHERE symbol=$1 ORDER BY strategy_id", symbol.upper())
    else:
      rows = await db.fetch("SELECT * FROM analysis_authority_scopes ORDER BY symbol, strategy_id")
  scopes = []
  for row in rows:
    rec = store._row(row)
    scopes.append({**asdict(rec), "effective_owner": rec.effective_owner(now)})
  return {"scopes": scopes, "note": "absent scope == python owns it at epoch 0"}


async def run(args: argparse.Namespace) -> dict:
  from app.persistence import store as db_store
  await db_store.init_db()
  store = PostgresAuthorityStore()
  fence = AuthorityFence(store)
  if args.command == "status":
    return await _status(store, args.symbol)
  if args.command == "accept":
    await fence.record_acceptance(args.symbol, args.scope, args.evidence, approved_by=args.approved_by, ttl_seconds=int(args.ttl_hours * 3600))
    return {"accepted": {"symbol": args.symbol.upper(), "scope": args.scope, "evidence": args.evidence, "approved_by": args.approved_by}}
  if args.command == "grant":
    rec = await fence.begin_transfer(
      args.symbol, args.scope, "go", expected_epoch=args.expected_epoch, actor=args.actor,
      reason=args.reason, evidence_ref=args.evidence, drain_seconds=args.drain_seconds,
    )
    return {"handover": asdict(rec), "note": "Go owns the scope only after drain_until; nobody publishes until then"}
  if args.command == "rollback":
    rec = await fence.rollback(
      args.symbol, args.scope, expected_epoch=args.expected_epoch, actor=args.actor,
      reason=args.reason, drain_seconds=args.drain_seconds,
    )
    return {"handover": asdict(rec), "note": "Go stops now; Python resumes after drain_until"}
  if args.command == "rollback-all":
    moved = await fence.rollback_all(actor=args.actor, reason=args.reason, drain_seconds=args.drain_seconds)
    return {"rolled_back": [asdict(rec) for rec in moved]}
  raise AssertionError(args.command)


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  sub = parser.add_subparsers(dest="command", required=True)

  status = sub.add_parser("status")
  status.add_argument("--symbol")

  def scope(p, *, epoch: bool):
    p.add_argument("--symbol", required=True)
    p.add_argument("--scope", required=True, help="catalog strategy id, e.g. supply")
    if epoch:
      p.add_argument("--expected-epoch", type=int, required=True)

  def audit(p):
    p.add_argument("--actor", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--drain-seconds", type=int, default=DEFAULT_DRAIN_SECONDS)

  accept = sub.add_parser("accept")
  scope(accept, epoch=False)
  accept.add_argument("--evidence", required=True)
  accept.add_argument("--approved-by", required=True)
  accept.add_argument("--ttl-hours", type=float, default=24.0)

  grant = sub.add_parser("grant")
  scope(grant, epoch=True)
  grant.add_argument("--evidence", required=True)
  audit(grant)

  rollback = sub.add_parser("rollback")
  scope(rollback, epoch=True)
  audit(rollback)

  rollback_all = sub.add_parser("rollback-all")
  audit(rollback_all)
  return parser


def main(argv: list[str] | None = None) -> int:
  args = build_parser().parse_args(argv)
  try:
    result = asyncio.run(run(args))
  except TransferRefused as exc:
    print(json.dumps({"refused": exc.code, "message": str(exc)}, indent=2))
    return 2
  print(json.dumps(result, indent=2, sort_keys=True, default=str))
  return 0


if __name__ == "__main__":
  sys.exit(main())

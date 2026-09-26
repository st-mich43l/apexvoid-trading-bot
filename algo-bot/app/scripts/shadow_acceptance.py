"""S14F: build the production shadow acceptance report from recorded evidence.

Read-only. Run it against production after the deployed ``go_shadow`` window:

  python -m app.scripts.shadow_acceptance --symbol XAU --since 2026-09-27T00:00:00Z --until 2026-09-30T00:00:00Z \\
      [--kafka-json kafka.json] [--replay-report docs/analysis/reports/s14c-policy-replay-xau-20260921.json] \\
      [--dispositions approvals.json] [--images-json images.json --expected-shas-json reviewed.json] \\
      [--risk-review-json risk.json] --json /reports/acceptance.json --md /reports/acceptance.md

Counts come from PostgreSQL and Redis; delivery timing from the ledger's own ``processed_at`` and each
envelope's ``produced_at``. Kafka group state, restart/outage drills, deployed SHAs, owner approvals and
any live risk review come only from files the operator supplies. A gate whose evidence is absent is
``not_measured``, never ``pass``. The overall status is ``ready_for_operator_review`` at best; this tool
never approves, accepts, or grants anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.analysis_client import acceptance as acc


def _ts(value: str) -> int:
  if value.isdigit():
    return int(value)
  return int(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


def _load(path: str | None) -> dict[str, Any] | None:
  return json.loads(Path(path).read_text()) if path else None


async def build_report(args: argparse.Namespace, *, db: Any, redis: Any | None) -> dict[str, Any]:
  from app.core.config import runtime_config

  authority = runtime_config.analysis.technical_authority
  since, until = _ts(args.since), _ts(args.until)
  ledger = await acc.gather_ledger(db, since=since, until=until, symbol=args.symbol)
  side = None
  side_error = None
  if redis is not None:
    try:
      side = await acc.gather_side_effects(redis, db)
    except Exception as exc:  # noqa: BLE001 - reported, and the gates depending on it become not_measured
      side_error = f"{type(exc).__name__}: {exc}"
  scopes = [dict(r) for r in await db.fetch("SELECT symbol, strategy_id, owner, target_owner, epoch, drain_until, updated_by FROM analysis_authority_scopes ORDER BY symbol, strategy_id")]
  runtime_audit = [dict(r) for r in await db.fetch("SELECT at, event, consumer_enabled, mode, go_bound_scopes FROM analysis_authority_runtime_audit WHERE at BETWEEN $1 AND $2 ORDER BY audit_id", since, until)]
  consumer_health, health_readable = None, False
  if redis is not None:
    try:
      raw = await redis.get(acc.CONSUMER_HEALTH_KEY)
      consumer_health = json.loads(raw) if raw else None
      health_readable = True
    except Exception:  # noqa: BLE001 - the gate reports not_measured
      health_readable = False
  gates = [
    acc.gate_consumer_running(authority.mode, authority.consumer_enabled, consumer_health, health_readable=health_readable),
    acc.gate_lifecycle(ledger),
    acc.gate_duplicates(side, ledger),
    acc.gate_freshness(ledger, max_age=authority.max_event_age_seconds, max_lag=authority.max_delivery_lag_seconds),
    acc.gate_policy_inputs(ledger),
    acc.gate_shadow_side_effects(side, ledger),
    acc.gate_risk(_load(args.risk_review_json)),
    acc.gate_kafka(ledger, _load(args.kafka_json), max_lag=authority.max_delivery_lag_seconds),
    acc.gate_differences(_load(args.replay_report), _load(args.dispositions)),
    acc.gate_deployment(_load(args.images_json), _load(args.expected_shas_json)),
  ]
  lag = ledger["delivery_lag_seconds"]
  return {
    "kind": "s14f_shadow_acceptance", "version": 1, "symbol": args.symbol.upper(),
    "window": {"since": since, "until": until},
    "configuration": {"mode": authority.mode, "consumer_enabled": authority.consumer_enabled, "consumer_group": authority.consumer_group,
                      "max_event_age_seconds": authority.max_event_age_seconds, "max_delivery_lag_seconds": authority.max_delivery_lag_seconds},
    "connectivity": {
      "ledger_events": ledger["events"], "dispositions": ledger["dispositions"], "rejections": ledger["rejections"],
      "decisions_by_outcome": ledger["decisions_by_outcome"],
      "delivery_lag_seconds": {"n": len(lag), "p50": acc._pct(lag, 0.5), "p95": acc._pct(lag, 0.95), "max": max(lag) if lag else None},
      "authority_scopes": scopes, "runtime_audit": runtime_audit, "side_effect_scan_error": side_error,
    },
    "gates": [g.as_dict() for g in gates],
    "overall": acc.overall(gates),
    "note": "not_measured and open are blockers, not passes; this report approves nothing and never calls accept or grant",
  }


def render_markdown(report: dict[str, Any]) -> str:
  lines = [
    f"# S14F shadow acceptance: {report['symbol']}", "",
    f"**Overall: `{report['overall']}`** (never an approval). Window {report['window']['since']} → {report['window']['until']}; "
    f"mode `{report['configuration']['mode']}`, consumer_enabled `{report['configuration']['consumer_enabled']}`.", "",
    "| gate | status | summary |", "| --- | --- | --- |",
  ]
  for g in report["gates"]:
    lines.append(f"| `{g['name']}` | **{g['status']}** | {g['summary']} |")
  c = report["connectivity"]
  lines += ["", "## Recorded connectivity", f"- Ledger events {c['ledger_events']}, dispositions {c['dispositions']}, rejections {c['rejections']}.",
            f"- Dry-run / policy decisions: {c['decisions_by_outcome'] or 'none'}.", f"- Kafka→ledger delivery lag (s): {c['delivery_lag_seconds']}.",
            f"- Authority scopes: {c['authority_scopes'] or 'none recorded (every scope Python-owned)'}."]
  blockers = [g for g in report["gates"] if g["status"] != acc.PASS]
  if blockers:
    lines += ["", "## Blockers"] + [f"- `{g['name']}`: {g['status']}: {g['summary']}" for g in blockers]
  return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> dict[str, Any]:
  from app.persistence import redis_state, store
  await store.init_db()
  try:
    redis = redis_state.get_client()
    await redis.ping()
  except Exception:  # noqa: BLE001 - the gates that need Redis report not_measured
    redis = None
  async with store._connect() as db:
    return await build_report(args, db=db, redis=redis)


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--symbol", default="XAU")
  parser.add_argument("--since", required=True, help="epoch seconds or ISO-8601")
  parser.add_argument("--until", required=True)
  for name in ("kafka-json", "replay-report", "dispositions", "images-json", "expected-shas-json", "risk-review-json"):
    parser.add_argument(f"--{name}")
  parser.add_argument("--json", required=True)
  parser.add_argument("--md", required=True)
  return parser


def main(argv: list[str] | None = None) -> int:
  args = build_parser().parse_args(argv)
  report = asyncio.run(run(args))
  Path(args.json).write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
  Path(args.md).write_text(render_markdown(report))
  print(json.dumps({"overall": report["overall"], "gates": {g["name"]: g["status"] for g in report["gates"]}}, indent=2))
  return 0


if __name__ == "__main__":
  sys.exit(main())

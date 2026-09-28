"""S14F acceptance evidence: gates computed from what was actually recorded, never asserted.

The operator runs ``python -m app.scripts.shadow_acceptance`` against production after the
deployed ``go_shadow`` window. Every gate returns one of

* ``pass``          evidence exists and is clean;
* ``fail``          evidence exists and shows a violation;
* ``not_measured``  the evidence needed is absent (no window data, no operator-supplied
                    measurement, no access). Absence is never a pass;
* ``open``          measured, but requires an owner disposition (Go/Python differences).

Nothing here is invented: counts come from PostgreSQL and Redis, timing from the ledger's own
``processed_at`` and each envelope's ``produced_at``, Kafka/lag/restart facts only from files the
operator supplies. The overall status is ``ready_for_operator_review`` only when every gate is
``pass``; it is never an approval. This module never calls ``record_acceptance`` or a transfer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

PASS, FAIL, NOT_MEASURED, OPEN = "pass", "fail", "not_measured", "open"
GO_KEY_PATTERNS = (
  "execution:plan:v8:go_*", "execution:plan_state:v8:go_*", "execution:plan_cancel:v8:go_*",
  "auto_trade:route_outcome:*:go_*", "analysis:setup:go_*", "auto_trade:lifecycle_state:go_*",
)


@dataclass
class Gate:
  name: str
  status: str
  summary: str
  evidence: dict[str, Any] = field(default_factory=dict)

  def as_dict(self) -> dict[str, Any]:
    return {"name": self.name, "status": self.status, "summary": self.summary, "evidence": self.evidence}


def _pct(values: list[float], q: float) -> float | None:
  if not values:
    return None
  ordered = sorted(values)
  return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]


async def gather_ledger(db: Any, *, since: int, until: int, symbol: str) -> dict[str, Any]:
  """Everything the ledger can say about a window, in one pass."""
  sym = symbol.upper()
  events = await db.fetch(
    "SELECT event_kind, disposition, processed_at, envelope FROM analysis_opportunity_events e "
    "JOIN analysis_opportunities o USING (opportunity_id) WHERE o.symbol = $1 AND e.processed_at BETWEEN $2 AND $3",
    sym, since, until,
  )
  dispositions: dict[str, int] = {}
  lags: list[float] = []
  for row in events:
    dispositions[row["disposition"]] = dispositions.get(row["disposition"], 0) + 1
    env = json.loads(row["envelope"]) if isinstance(row["envelope"], str) else row["envelope"]
    produced = env.get("produced_at")
    if produced is not None:
      lags.append(float(row["processed_at"]) - float(produced))
  resurrected = await db.fetchval(
    "SELECT count(*) FROM analysis_opportunities WHERE symbol = $1 AND state = 'active' AND terminal_event_id IS NOT NULL", sym)
  orphans = await db.fetchval(
    "SELECT count(*) FROM analysis_opportunities WHERE symbol = $1 AND creation_event_id IS NULL AND terminal_event_id IS NOT NULL "
    "AND updated_at BETWEEN $2 AND $3", sym, since, until)
  rejections = await db.fetchval("SELECT count(*) FROM analysis_opportunity_rejections WHERE rejected_at BETWEEN $1 AND $2", since, until)
  decisions = await db.fetch(
    "SELECT d.mode, d.outcome, d.reason, d.details, o.creation_envelope FROM analysis_shadow_decisions d "
    "LEFT JOIN analysis_opportunities o USING (opportunity_id) WHERE d.created_at BETWEEN $1 AND $2 AND (o.symbol = $3 OR o.symbol IS NULL)",
    since, until, sym,
  )
  by_outcome: dict[str, int] = {}
  for d in decisions:
    key = f"{d['mode']}:{d['outcome']}"
    by_outcome[key] = by_outcome.get(key, 0) + 1
  return {
    "events": len(events), "dispositions": dispositions, "delivery_lag_seconds": lags,
    "resurrected_active_with_terminal": int(resurrected or 0), "orphan_terminals": int(orphans or 0),
    "rejections": int(rejections or 0), "decisions": [dict(d) for d in decisions], "decisions_by_outcome": by_outcome,
  }


async def gather_side_effects(redis: Any, db: Any) -> dict[str, Any]:
  """Go-origin executable state that must not exist while the scope is Python-owned."""
  counts: dict[str, int] = {}
  for pattern in GO_KEY_PATTERNS:
    counts[pattern] = sum([1 async for _ in redis.scan_iter(match=pattern)])
  registry = int(await redis.hlen("analysis:go_plans"))
  stream = 0
  go_stream_plans: list[dict[str, Any]] = []
  for _id, fields in await redis.xrange("execution:trade_plans"):
    payload = json.loads(fields.get("payload") or "{}")
    if "authority:go" in (payload.get("analysis", {}).get("tags") or []):
      stream += 1
      go_stream_plans.append({"plan_id": payload.get("plan_id"), "thesis_id": payload.get("thesis_id")})
  journal = int(await db.fetchval("SELECT count(*) FROM auto_trade_results WHERE group_id LIKE 'v8:go\\_%'") or 0)
  return {"redis_key_counts": counts, "go_plan_registry": registry, "go_plans_on_stream": stream,
          "go_stream_plans": go_stream_plans, "journal_rows": journal}


# ---- gates (pure) ---------------------------------------------------------------------------------------

def gate_lifecycle(ledger: dict[str, Any] | None) -> Gate:
  name = "no_orphan_or_resurrected_lifecycle"
  if not ledger or ledger["events"] == 0:
    return Gate(name, NOT_MEASURED, "no ledger events in the window: nothing was observed")
  bad = ledger["resurrected_active_with_terminal"] + ledger["orphan_terminals"]
  ev = {"events": ledger["events"], "orphan_terminals": ledger["orphan_terminals"],
        "resurrected": ledger["resurrected_active_with_terminal"],
        "late_creations_refused": ledger["dispositions"].get("late_creation_rejected", 0)}
  return Gate(name, FAIL if bad else PASS, "a terminal opportunity was resurrected or a terminal had no creation" if bad else "every terminal follows its creation; refused late creations stayed terminal", ev)


def gate_duplicates(side: dict[str, Any] | None, ledger: dict[str, Any] | None = None) -> Gate:
  name = "no_duplicate_plans_or_orders"
  if side is None:
    return Gate(name, NOT_MEASURED, "Redis/PostgreSQL side-effect scan was not run")
  if not ledger or ledger["events"] == 0:
    return Gate(name, NOT_MEASURED, "no opportunities flowed in the window: an empty scan proves nothing")
  thesis: dict[str, int] = {}
  for plan in side["go_stream_plans"]:
    thesis[plan["thesis_id"]] = thesis.get(plan["thesis_id"], 0) + 1
  dupes = {k: v for k, v in thesis.items() if v > 1}
  return Gate(name, FAIL if dupes else PASS, "more than one Go plan for a thesis" if dupes else "no thesis has more than one Go-origin plan on the stream",
              {"go_plans_on_stream": side["go_plans_on_stream"], "duplicate_theses": dupes, "journal_rows": side["journal_rows"]})


def gate_freshness(ledger: dict[str, Any] | None, *, max_age: int, max_lag: int) -> Gate:
  name = "no_stale_bootstrap_replay_or_pre_activation_plan"
  if not ledger:
    return Gate(name, NOT_MEASURED, "no ledger window")
  live = [d for d in ledger["decisions"] if d["mode"] == "go" and d["outcome"] == "match_written"]
  if not live:
    return Gate(name, NOT_MEASURED, "no Go-owned (mode go) plan-creating decision exists yet: only the code-level gate is proven, not production behaviour",
                {"go_mode_match_written": 0})
  bad = []
  for d in live:
    det = d["details"] if isinstance(d["details"], dict) else json.loads(d["details"])
    if det.get("event_age_seconds", 10**9) > max_age or det.get("delivery_lag_seconds", 10**9) > max_lag or det.get("observed_at", 0) < det.get("activation_boundary", 10**12):
      bad.append(det.get("match_id"))
  return Gate(name, FAIL if bad else PASS, "a live plan violated the freshness/boundary limits" if bad else "every live plan is inside the age, lag and activation-boundary limits",
              {"go_mode_match_written": len(live), "violations": bad})


def gate_policy_inputs(ledger: dict[str, Any] | None) -> Gate:
  name = "no_missing_confirmation_or_htf_bypass"
  if not ledger:
    return Gate(name, NOT_MEASURED, "no ledger window")
  checked = bypass = 0
  for d in ledger["decisions"]:
    if d["outcome"] not in {"would_publish", "would_wait", "would_reject", "match_written"}:
      continue
    env = d["creation_envelope"]
    env = json.loads(env) if isinstance(env, str) else env
    tech = ((env or {}).get("payload") or {}).get("technical_context") or {}
    checked += 1
    if not tech.get("confirmation") or not tech.get("higher_timeframes"):
      bypass += 1
  if checked == 0:
    return Gate(name, NOT_MEASURED, "no policy-evaluated decision in the window")
  return Gate(name, FAIL if bypass else PASS, "a decision proceeded without confirmation or HTF facts" if bypass else "every policy-evaluated event carried confirmation and HTF facts",
              {"policy_evaluated": checked, "bypasses": bypass})


def gate_shadow_side_effects(side: dict[str, Any] | None, ledger: dict[str, Any] | None) -> Gate:
  name = "no_shadow_plan_reservation_card_or_broker_side_effect"
  if side is None:
    return Gate(name, NOT_MEASURED, "Redis/PostgreSQL side-effect scan was not run")
  if not ledger or not any(d["mode"] == "go_shadow" for d in ledger["decisions"]):
    return Gate(name, NOT_MEASURED, "no go_shadow dry-run decision in the window: nothing exercised the shadow path, so an empty scan proves nothing")
  in_go_mode = bool(ledger and any(d["mode"] == "go" for d in ledger["decisions"]))
  if in_go_mode:
    return Gate(name, NOT_MEASURED, "the window contains mode=go decisions: this gate only applies to a pure go_shadow window", {"note": "re-run over a go_shadow-only window"})
  found = {k: v for k, v in side["redis_key_counts"].items() if v} | ({"analysis:go_plans": side["go_plan_registry"]} if side["go_plan_registry"] else {}) \
    | ({"go_plans_on_stream": side["go_plans_on_stream"]} if side["go_plans_on_stream"] else {}) | ({"journal_rows": side["journal_rows"]} if side["journal_rows"] else {})
  return Gate(name, FAIL if found else PASS, "Go-origin executable state exists during shadow" if found else "no Go-origin plan, setup, route, claim, card or journal state exists",
              {"found": found, "patterns_scanned": list(GO_KEY_PATTERNS)})


def gate_risk(operator_supplied: dict[str, Any] | None) -> Gate:
  name = "no_risk_limit_or_group_exposure_violation"
  if not operator_supplied or "risk_review" not in operator_supplied:
    return Gate(name, NOT_MEASURED, "no Go-owned exposure exists in shadow and no operator risk review was supplied")
  review = operator_supplied["risk_review"]
  return Gate(name, PASS if review.get("violations") == 0 and review.get("group_exposures_reviewed", 0) > 0 else FAIL if review.get("violations") else NOT_MEASURED,
              "operator-supplied live risk review", review)


CONSUMER_HEALTH_KEY = "auto_trade:component_health:analysis_opportunity_consumer"


def gate_consumer_running(mode: str, consumer_enabled: bool, health: dict[str, Any] | None, *, health_readable: bool) -> Gate:
  """The window can only be evidence if the process under test actually runs the Go consumer.

  Production finding (2026-09-26): config/analysis.yml said go / true, but the bot reads the
  ansible-rendered trading-bot.yml, so it ran mode=python with the consumer never started and no
  Kafka group existed. Every other gate was then vacuously `not_measured`; this one names the cause.
  """
  name = "go_consumer_is_running_in_the_process_under_test"
  ev: dict[str, Any] = {"mode": mode, "consumer_enabled": consumer_enabled, "consumer_health": health}
  if mode == "python" or not consumer_enabled:
    return Gate(name, FAIL, f"this process runs mode={mode} consumer_enabled={consumer_enabled}: it observes no Go events. The bot reads the "
                "ansible-rendered trading-bot.yml (analysis.technical_authority), not config/analysis.yml", ev)
  if not health_readable:
    return Gate(name, NOT_MEASURED, "Redis was unreadable, so the consumer's health key could not be checked", ev)
  if not health or health.get("state") != "ready":
    return Gate(name, FAIL, "the consumer is enabled but never reported ready (no health key): it did not start or cannot reach Kafka", ev)
  return Gate(name, PASS, "the consumer reported ready", ev)


def gate_kafka(ledger: dict[str, Any] | None, kafka: dict[str, Any] | None, *, max_lag: int) -> Gate:
  name = "kafka_delivery_lag_restart_outage_within_limits"
  lags = ledger["delivery_lag_seconds"] if ledger else []
  ev: dict[str, Any] = {"events": len(lags), "delivery_lag_p50": _pct(lags, 0.5), "delivery_lag_p95": _pct(lags, 0.95), "delivery_lag_max": max(lags) if lags else None,
                        "limit_seconds": max_lag}
  if not lags:
    return Gate(name, NOT_MEASURED, "no delivery timings recorded", ev)
  missing = [k for k in ("consumer_group", "committed_offsets", "lag_messages", "restart_recovery", "outage_recovery") if not kafka or k not in kafka]
  if missing:
    return Gate(name, NOT_MEASURED, "ledger timings exist but the operator has not supplied: " + ", ".join(missing), ev | {"missing": missing})
  ok = max(lags) <= max_lag and kafka["restart_recovery"].get("ok") is True and kafka["outage_recovery"].get("ok") is True
  return Gate(name, PASS if ok else FAIL, "within the documented limits" if ok else "a limit or recovery drill was not met", ev | {"kafka": kafka})


def gate_differences(report: dict[str, Any] | None, dispositions: dict[str, Any] | None) -> Gate:
  name = "go_python_differences_reconciled_or_explicitly_approved"
  if not report:
    return Gate(name, NOT_MEASURED, "no replay comparison report supplied")
  open_gates = [k for k, ok in report.get("gates", {}).items() if not ok]
  ev = {"replay_verdict": report.get("verdict"), "open_replay_gates": open_gates, "matching": report.get("matching")}
  if report.get("verdict") == "pass":
    return Gate(name, PASS, "replay verdict is pass", ev)
  approved = (dispositions or {}).get("approved") or {}
  missing = [g for g in open_gates if g not in approved or not approved[g].get("approved_by") or not approved[g].get("evidence")]
  if dispositions and not missing:
    return Gate(name, PASS, "every open difference has an owner approval with evidence", ev | {"approvals": approved})
  return Gate(name, OPEN, "differences remain without an owner approval naming the approver and evidence", ev | {"unapproved": missing})


def gate_deployment(images: dict[str, Any] | None, expected: dict[str, Any] | None) -> Gate:
  name = "deployed_shas_match_reviewed_commits"
  if not images or not expected:
    return Gate(name, NOT_MEASURED, "deployed image SHAs and reviewed commits were not both supplied")
  mismatched = {k: {"deployed": images.get(k), "reviewed": v} for k, v in expected.items() if images.get(k) != v}
  return Gate(name, FAIL if mismatched else PASS, "a deployed SHA differs from the reviewed commit" if mismatched else "producer and consumer images match the reviewed commits", {"deployed": images, "reviewed": expected, "mismatched": mismatched})


def overall(gates: list[Gate]) -> str:
  return "ready_for_operator_review" if all(g.status == PASS for g in gates) else "blocked"

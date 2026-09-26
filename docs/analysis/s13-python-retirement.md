# Phase S13 — Retire Python technical analysis, retain trading orchestration

Baseline inspected: master `0c49c66` (PR #630, September 25, 2026).
This document records an **initial, non-executing cleanup boundary**. It
does not certify that S12 cutover or production acceptance has occurred.

## Gate: technical authority must be real, not merely publishing Kafka

At this baseline, `config/analysis.yml` selects `mode: python` with
`consumer_enabled: false`; `AnalysisTechnicalAuthorityConfig` rejects
`mode: go`. The Python Analysis Client's `go_shadow` evaluator (S14A) records
the outcome of a real dry run of the trading policy (`would_publish` /
`would_wait` / `would_reject` ...), replacing the earlier static `contract_gap`.
Go strategy events are published, but this does **not** mean Go owns live TradePlans.

The S13 deletion gate requires an approved, observed S12D/E cutover for each
instrument/strategy scope, including durable consumption, full policy inputs,
authority fencing, live execution ownership, manual-trade isolation, replay
reconciliation, rollback and production evidence. S13 may prepare boundaries
now; it must not delete live Python detection before that gate passes.

## What stays in Python

- `app/analysis_client/*`: Kafka event decoding, lifecycle ledger, validated
  technical context and opportunity access. Never recalculate Go structure.
- `app/autotrade/*`: eligibility, current quote and spread, kill zones,
  account risk, exposure, sizing, duplicate protection, execution routing,
  TradePlan V8, event handling and reconciliation.
- `app/signals/*` and `app/bot/*`: manual owner instructions, Telegram,
  pinned entry cards, per-leg TP/SL reporting, journaling and commands.
- `app/persistence/*` and runtime configuration; cTrader stays responsible
  for broker orders and position management.
- Research/replay utilities that are explicitly offline, when they do not
  start a live scanner or publish broker candidates.

Do not route auto opportunities through Manual Algo's
`bypass_analysis_gates`. The shared `app/autotrade/xau_ladder.py` calculator
is currently **not** wired to order execution and must not be silently
activated or deleted in S13. Preserve executable decimal geometry; whole-point
XAU rounding is a Telegram presentation preference only.

## Inventory before deletion

Run from `algo-bot/`:

```bash
python s13_legacy_dependency_audit.py > /tmp/s13-legacy-imports.json
```

The inventory reports static imports from `app.analysis` and
`app.scalping` among production Python modules. It does not prove which
imports execute, and cannot detect dynamic imports, subprocesses,
configuration-driven registration or retained database/Redis contracts.
Supplement it with runtime traces, startup/worker call graphs and tests.

Confirmed dependencies at this baseline include:

| Live Python reference | Why it cannot be deleted yet |
|---|---|
| `app/analysis/scanner.py` | Imports legacy detector context and drives executable scanner setup cards. |
| `app/scalping/runtime.py` | Imports `AnalysisSettings`, `ScalpStructure` and `scalp_structure` from `app.analysis.engine`; rebuilds technical context and runs `discover_all`. |
| `app/autotrade/worker.py` | Existing Python opportunity-to-TradePlan runtime; analysis/policy imports must be classified function-by-function before removal. |
| `app/analysis/engine.py::scalp_structure` | Still called by the live-capable scalping runtime; the older migration map's “no live callers” note is stale. |

Do not bulk-delete `app/analysis` or `app/scalping`.

## Classified inventory (S13B-1)

`algo-bot/s13_legacy_classification.py` extends the S13A import list with a
reviewed role for every legacy module, per-edge classification, static
reachability from the real process entrypoints, and separate scans for
dynamic imports, lazy function-scope imports, startup tasks and Redis/Kafka
contract names. The full generated table is
[`s13-legacy-inventory.md`](s13-legacy-inventory.md); CI uploads the JSON.

Findings at this baseline (63 modules, 32,958 lines):

- **Not all of `app.scalping` is technical detection.** `outcomes`, `risk`,
  `lifecycle`, `models` and `telemetry` are outcome accounting used by
  `autotrade/stats_ingestion.py` and the expiry sweeper; `context`,
  `activation` and `rollout` are execution-policy inputs. They are classified
  *retain* and must not be swept up by a directory delete.
- **Five modules are unreachable from every entrypoint** (`lab_event_builder`,
  `mad_replay`, `replay_lab`, `performance`, and the `mad_phase` re-export).
  They are offline research/compat, have their own tests and docs, and are
  retained. A guard test fails if a *new* module becomes unreachable without
  review.
- **One legacy background loop starts at boot**: `bar_event_dispatcher_loop`
  (which drives the scanner and the M1 scalp runtime). The Go consumer loop is
  conditional and opt-in.
- **No dynamic import can reach a legacy module** (the only `__import__` call
  is `typing`); there are 29 function-scope (lazy) imports, all listed.
- **19 production modules import a `replace_with_go` module.** These are the
  actual work for S13C, not the two directories as a whole: the detector-facing
  ones are `autotrade/worker.py`, `zone_execution_cutover.py`, `trend.py`,
  `scale_context.py`, `structural_barriers.py`, `structural_target_room.py`,
  `strategy_registry.py`, `strategy_match.py`, `entry_activation.py`,
  `multi_match.py`, `map_strategy.py`; the presentation-side ones
  (`setup_card.py`, `delivery.py`, `setups_report.py`, `bot/handlers/dm.py`,
  `analysis/market_map_delivery.py`) need Go facts or an "unavailable" state.

Regenerate with `python s13_legacy_classification.py --markdown` from `algo-bot/`.

## Sequenced retirement

1. **S13A — boundary and dependency inventory (this PR).** Protect
   `analysis_client` against direct legacy detector imports; generate a
   read-only dependency report. No live behavior changes.
2. **S13B — complete S12 prerequisite.** Deliver and verify the missing
   technical contract, policy evaluation, consumer deployment, exclusive Go
   authority, and scoped rollback. If acceptance is blocked, stop deletion.
3. **S13C — replace live scanner/scalp entrypoints by approved scope.** Route
   Go opportunities through one Analysis Client → Auto Algo policy →
   TradePlan pipeline. Retain analysis-only Telegram observations through
   Go facts or mark them unavailable rather than recomputing Python OHLC.
4. **S13D — dependency-proven deletion.** Remove the retired scanner,
   detector, duplicate OHLC/ATR/zone/swing calculations and live scalp
   technical strategies only after no production import, callback, dynamic
   import, Redis stream, CLI or test fixture needs them. Keep Python policy,
   risk, manual trading, execution reconciliation and offline research.
5. **S13E — production verification.** Confirm Python no longer publishes
   technical automatic candidates in Go scopes, one authority controls
   TradePlan creation, broker positions remain managed, manual trades work,
   and CPU/memory and trading-decision provenance are measured. Rehearse
   rollback before removing its dependencies.

Do not declare S13 complete from a green CI run alone; provide deployed
build/config fingerprints, accepted S12 evidence, deletion diffs and a
production smoke report. The first removal PR should name its exact obsolete
entrypoints and prove the absence of callers.


## S13C: Go opportunity → existing policy (implemented) and the blockers it exposed

`algo-bot/app/autotrade/go_opportunity_policy.py` translates a durable, decoded
Go opportunity into an ordinary `StrategyMatch` in the **same** Redis store the
scanner writes, so arbitration, guards, execution policy (min R:R, stop, routing,
sizing), duplicate protection, the TradePlan V8 build and the publish-time
authority fence all run unchanged. It decides nothing about risk and does not
route through Manual Algo's `bypass_analysis_gates`.

* **Reviewed scopes only:** `supply`, `demand` (zone-anchored; the entry band *is*
  the zone). Every other catalog scope is recorded `scope_not_reviewed` and
  dropped.
* **Fence first** (`go` owner at the accepted epoch), then translate; a rollback
  between match-write and publish is refused by the publish guard.
* **Idempotent + fail-closed:** a policy exception is not swallowed — the offset
  is not committed, the event is redelivered, the policy retries; a late
  redelivery of a creation never resurrects a terminated opportunity.
* `mode: go` is now a legal config value (requires `consumer_enabled`); it moves
  **no** scope — every scope stays Python-owned until an operator records an
  acceptance and performs a fenced handover.

### Blockers found by running the *real* V8 pipeline (not assumed)

The adapter is pipeline-compatible: with the two facts below supplied, the real
`_publish_trade_plan_v8` builds and publishes a TradePlan V8 from a Go-derived
match (`test_go_owned_zone_becomes_a_real_v8_plan_once_confirmation_facts_exist`).
Without them the **existing, unmodified** policy refuses, and the tests pin both:

1. **No reaction confirmation** — `confirmation_metadata_missing`. Legacy zone
   (`supply_demand`) and key-level/session/trendline policy trade *confirmed
   reactions* (touch bar, confirmation bar, reaction type). The Go S7 strategies
   emit a *resting* thesis and carry none of these; the adapter refuses to
   fabricate them. This is a **product/risk decision**, not plumbing: either Go
   must publish reaction-confirmation facts (new Go detection semantics) or the
   owner must approve a resting-thesis confirmation policy. Enabling Go for a
   scope today would silently change *what is traded* (resting zones vs.
   confirmed reactions).
2. **No H1/H4 bias** — `v8_missing_htf_bias`. Go's bias is primary-timeframe
   structural bias; the legacy field means higher-timeframe bias and the builder
   (per its ADR) refuses to derive it from direction.

`regime_kind` is *not* required (verified). Retained, documented approximations
that replay/shadow must reconcile **before** any acceptance: `confluence` =
count of Go evidence codes; `strategy_mode`/`bias_relationship` derive from Go
primary-timeframe bias; the worker's opposing-barrier and target-room checks
still recompute Python zones from Redis OHLC (a retained duplicate technical
computation that blocks deleting those modules).

Consequently **no live Python detector is deleted in this series**: every
scope's live entrypoint still has a production importer *and* no approved,
evidence-backed Go replacement. See the final report for the itemised list.

## Follow-up after S13C: Go confirmation and higher-timeframe inputs

The separate S12 confirmation-contract PR implements two inputs that the
original S13C adapter identified as missing. The initial Go Supply/Demand
opportunity is still a resting observation, while a separately identified
M5 rejection opportunity has real touch/confirmation timestamps. Closed,
fresh H1/H4 canonical structure is carried independently from M5 bias.

The adapter rejects unconfirmed resting opportunities and missing HTF
context rather than writing a match with missing policy evidence. Existing
V8 execution/risk gates remain in force. These changes are not an approval
of the new reaction semantics or a substitute for S12C/S12D production
comparison and scoped authority handover. The worker's remaining Python
opposing-barrier and target-room recomputations still block whole-directory
technical-detector deletion.

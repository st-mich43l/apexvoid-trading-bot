# Phase S13 — Retire Python technical analysis, retain trading orchestration

Baseline inspected: master `0c49c66` (PR #630, September 25, 2026).
This document records an **initial, non-executing cleanup boundary**. It
does not certify that S12 cutover or production acceptance has occurred.

## Gate: technical authority must be real, not merely publishing Kafka

At this baseline, `config/analysis.yml` selects `mode: python` with
`consumer_enabled: false`; `AnalysisTechnicalAuthorityConfig` rejects
`mode: go`. The Python Analysis Client's `go_shadow` evaluator records
`contract_gap` instead of invoking the complete trading policy. Go strategy
events are published, but this does **not** mean Go owns live TradePlans.

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

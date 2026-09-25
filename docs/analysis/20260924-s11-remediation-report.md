# Phase S11 Remediation Report

## Baseline and root causes

Baseline was master `2eebc13` (merged PR #615). The retained production sample
had 351 creations, 448 apparent terminal events, unstable key-level identity,
zero usable FVG/Flip output, historical creation timestamps, only 7/19
strategy factories, and 3,256 obsolete `manual_algo_charts` rows. Those Kafka
counts are historical evidence only and are not reused for post-fix acceptance.

Root causes were transport-unaware in-memory lifecycle publication, zeroed
zone strength for derived zones, a self-reconstructing moving key-level bucket,
formation time being used as first observation, and missing concrete strategy
packages.

## Implementation and defect closure

| Area | Before | Remediation | Status |
|---|---|---|---|
| Lifecycle | bootstrap creation could later emit orphan terminal | durable publication ledger, stable event IDs, FIFO ack gate, restart/outage/replay tests | PASS in deterministic tests; production epoch pending |
| Zone strength | FVG/iFVG/breaker/flip often zero | causal displacement/inversion/acceptance strength plus lifecycle decay | PASS in fixtures |
| Key-level identity | ATR/price-derived bucket drifted | canonical swing/round-level anchored ID preserved through clustering | PASS in variation tests |
| Timestamps | historical primitive time used as creation | separate `formed_at`, actionable `created_at`, observed timeframe | PASS in tests; additive V1 compatibility retained |
| Audit | one-off retained-topic inspection | bounded no-group `shadow-audit`, JSON and human reports | PASS in fixture tests |

Post-fix replay of `testdata/raw_xau_m5_snapshot.jsonl` (300 real XAU M5
bars) produced 129 semantic opportunities: demand 26, flip_zone 9, fvg 48,
key_level 17, order_block 5 and supply 24. The prior same-fixture key-level
result was 147. FVG and Flip Zone are therefore demonstrably eligible on real
input after strength repair; these counts are technical replay evidence, not a
performance or live-equivalence claim.

The outbox guarantee is at-least-once. If Kafka acknowledges and the local ack
fsync then fails, restart may resend the same stable event ID. It cannot publish
a terminal before an acknowledged creation. Outbox persistence failure is
visible in telemetry and does not block candle ingestion.

## Strategy coverage

| Strategy | Factory | Qualifying/rejecting fixture | Default state |
|---|---:|---:|---|
| key_level | yes | yes | enabled |
| supply | yes | yes | enabled |
| demand | yes | yes | enabled |
| order_block | yes | yes | enabled |
| fvg | yes | yes | enabled |
| flip_zone | yes | yes | enabled |
| session_level | yes | yes | enabled |
| ifvg | yes | yes | disabled |
| trendline | yes | yes | disabled |
| crt | yes | yes | disabled |
| confluence_zone | yes | yes | disabled |
| range_edge | yes | yes | disabled |
| box_breakout | yes | yes | disabled |
| momentum_ride | yes | yes | disabled |
| snap_back | yes | yes | disabled |
| liquidity_sweep | yes | yes | disabled |
| range_sweep | yes | yes | disabled |
| impulse_pullback | yes | yes | disabled |
| scalp_breakout_retest | yes | yes | disabled |

The 12 additions have strategy-owned config validation, timeframe declarations,
independent setup identity/geometry/evidence/quality, deterministic qualifying
replay, missing-context rejection and Book lifecycle checks. Disabled rollout
status was preserved; implementation is not authorization to trade.

## Production cleanup

Application references to `manual_algo_charts` were removed earlier. The
one-time migration now requires an exclusive-lock JSON archive containing
schema metadata and all rows, written atomically with SHA-256 logged before
drop. Production execution remains **PENDING AUTHORIZED DEPLOYMENT**; this PR
does not mutate production data.

## Acceptance state

| Gate | State | Evidence / remaining requirement |
|---|---|---|
| S11A stabilization | PASS (code/test), production verification pending | full checks plus a fresh clean audit epoch required |
| S11B 19-strategy coverage | PASS (implementation/test and production shadow rollout) | all 19 enabled in Analysis Engine; still technical-only |
| S11C comparison and shadow acceptance | BLOCKED | deploy this build; capture exact offsets across multiple sessions for XAU + non-JPY FX + JPY FX; export same-input Python/Go observations; reconcile audit/comparison; prove no V2 TradePlan/order; obtain explicit owner approval |

At this remediation checkpoint no Go opportunity consumer, TradePlan producer,
broker order path, or Python live policy was enabled. S12A/B subsequently adds
an initially disabled, manual-commit lifecycle consumer and shadow-only
contract-gap recording; it does not alter this execution-safety conclusion.

## Validation evidence

- `go vet ./...`, `go build ./...`, `go test ./...`, and
  `go test -race ./...`: PASS.
- Production and `demo_eval` Configuration V3 resolution plus JSON Schema
  validation: PASS.
- Real XAU M5 replay (300 bars): PASS with the strategy distribution above.
- `manual_algo_charts` archive regression: 2 tests PASS in the disposable
  production-image test environment.
- Deployable Analysis Engine image build: PASS; `shadow-audit` and
  `strategy-compare` command smoke tests PASS; non-root outbox write access
  PASS.

Sequential implementation commits are S11A `8fd2dad`, S11B `75d659c`, and
S11C tooling/wiring `f2d6669`. Delivery is tracked in
[PR #616](https://github.com/st-mich43l/apexvoid-trading-bot/pull/616).
No clean production Kafka epoch or Python-Go multi-session dataset existed
during implementation, so their required quantitative reports remain
explicitly blocked rather than inferred.

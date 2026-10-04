# Auto-trade configuration contract

The Python publisher and C# executor share config manifest version 2 and
candidate contract version 6. Cross-service values use these canonical
environment variables:

## TradePlan contract mode

`AUTO_TRADE_CONTRACT_MODE` selects which planning/execution path is active.
Both services must resolve the same value; config-health treats
`contract_mode`, `trade_plan_version`, and `trade_plan_stream` as fatal
fields (see `compare_manifests` in `app/autotrade/config_health.py` and
`AutoTradeConfigHealth.Compare` in `ctrader-engine/src/AutoTradeConfigHealth.cs`),
so a mismatch fails closed rather than silently running two different paths.
See `docs/autotrade-execution-integrity.md`.

| Mode | Behavior |
|---|---|
| `v8_only` (live) | TradePlan V8 is the sole autonomous order path; new V6 autonomous candidates are rejected. |
| `legacy_v6` | V6 `TradeCandidate` path only (mechanical tests / legacy manage). TradePlan autonomous publish disabled. |

Do not flip this value without a corresponding, deliberate deployment step —
it is not a per-request toggle.

`CTRADER_EXPECTED_BROKER` is the market-data account guard. The feed resolves
the authorized account immediately after OAuth account authorization and must
match this broker before symbol lookup, historical backfill, spot streaming or
trendbar subscription. Production sets both `CTRADER_EXPECTED_BROKER` and
`AUTO_TRADE_EXPECTED_BROKER` to `fpmarkets`, so scanner data and order
execution cannot silently use different brokers when one access token grants
multiple cTrader accounts.

```text
AUTO_TRADE_PROFILE
AUTO_TRADE_ENABLED
AUTO_TRADE_DRY_RUN
AUTO_TRADE_CANDIDATE_STREAM
AUTO_TRADE_EVENT_STREAM
AUTO_TRADE_CANDIDATE_CONTRACT_VERSION
AUTO_TRADE_CONTRACT_MODE
AUTO_TRADE_TRADE_PLAN_STREAM
AUTO_TRADE_SYMBOLS
AUTO_TRADE_CANONICAL_SYMBOL
AUTO_TRADE_XAU_PIP_SIZE
AUTO_TRADE_XAU_PRICE_DIGITS
AUTO_TRADE_XAU_CONTRACT_SIZE
AUTO_TRADE_ADD_STOP_BUFFER_ATR
AUTO_TRADE_ADD_MIN_STOP_PIPS
AUTO_TRADE_SL_DISTANCE
AUTO_TRADE_WICK_STOP_BUFFER_ATR
AUTO_TRADE_TREND_STOP_MIN_PIPS
AUTO_TRADE_TREND_STOP_MAX_PIPS
AUTO_TRADE_TARGET_PLANS_PIPS
AUTO_TRADE_RANGE_TARGETS_PIPS
AUTO_TRADE_RANGE_TP_BUFFER_PIPS
AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS
AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS
AUTO_TRADE_SPOT_MAX_AGE_SECONDS
AUTO_TRADE_RANGE_FLIP_ENABLED
AUTO_TRADE_RANGE_TWO_SIDED_ENABLED
AUTO_TRADE_ALLOW_CONCURRENT_STRATEGIES
AUTO_TRADE_ALLOW_COUNTER_BIAS
AUTO_TRADE_ZONE_FILL_ENABLED
AUTO_TRADE_MIN_CONFLUENCE
AUTO_TRADE_REQUIRE_DEMO_ACCOUNT
AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY
AUTO_TRADE_STRUCTURAL_GUARD_MODE
AUTO_TRADE_ZONE_COOLDOWN_ENABLED
AUTO_TRADE_ZONE_RECONCILE_MODE
AUTO_TRADE_RANGE_BOX_SCALE_OUT_ENABLED
AUTO_TRADE_RANGE_BOX_SCALE_OUT_THRESHOLD_PIPS
AUTO_TRADE_RANGE_BOX_SCALE_OUT_TRIGGER_PIPS
AUTO_TRADE_RANGE_BOX_SCALE_OUT_FRACTION
AUTO_TRADE_RANGE_BOX_MOVE_SL_TO_BE_AFTER_SCALE_OUT
AUTO_TRADE_ENTRY_CONTRACT_TOLERANCE_PIPS
AUTO_TRADE_STOP_PUSH_BEYOND_ZONE
AUTO_TRADE_BROKER_ABSENCE_CONFIRMATIONS
AUTO_TRADE_BROKER_ABSENCE_RECHECK_SECONDS
AUTO_TRADE_BROKER_RECOVERY_TIMEOUT_SECONDS
```

Canonical manifest representation:

- Symbols are uppercase, unique, and ascending.
- Target plans are integer pips, unique, and ascending.
- Brokers `fpmarkets`, `fpmarkets-sc`, and `fpmarketssc` normalize to
  `fpmarkets`.
- Account aliases normalize to `demo` or `live`; the demo requirement is a
  separate boolean.
- Numeric JSON values compare by value, so `3` and `3.0` are equivalent.

Runtime target selection is intentionally separate. Range targets are sorted
descending before selection so the largest target that fits is selected.

`AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS` controls order eligibility.
`AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS` controls Redis audit retention.
The former is fatal when services disagree; the latter is warning-only.

The six stop-planning values are fatal manifest fields. Python uses
`AUTO_TRADE_XAU_PRICE_DIGITS` when creating the Decimal stop plan; C# uses the
broker symbol digits and rejects candidate payload v5 when the recomputed stop
differs by more than one symbol tick. Candidate contract v6 is therefore
required before a publisher may emit stop-plan-bearing payload v5 events.

The broker-absence quorum settings are validated fatally at startup
(`AutoTradeConfigurationException` disables auto trading):

- `AUTO_TRADE_BROKER_ABSENCE_CONFIRMATIONS` must be at least 2 — a single
  broker snapshot never confirms absence;
- `AUTO_TRADE_BROKER_ABSENCE_RECHECK_SECONDS` must be positive — a
  zero-second interval provides no visibility window and would let an
  immediate restart accelerate the quorum;
- `AUTO_TRADE_BROKER_RECOVERY_TIMEOUT_SECONDS` must be positive and at least
  `recheck × (confirmations − 1)` so the configured quorum is achievable
  within one recovery attempt. The recovery lease heartbeat renews across the
  whole window, so the timeout is not limited by a single lease duration.

For non-hedged accounts,
`AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY` must be one of:

- `broker_netting`
- `close_then_reverse`
- `reject`

Non-hedged capability is visible as a warning and does not itself disable a
demo executor.

## Structural execution policy

Python resolves the structural policy once from `AUTO_TRADE_PROFILE`; the C#
manifest publishes the same resolved values:

| Profile | Structural guard | Zone cooldown | Zone reconciliation |
|---|---|---|---|
| `demo_eval` | `observe` | disabled | `shadow` |
| `conservative` | `balanced` | enabled | `enforce` |
| non-demo/live-like | `strict` unless explicit | enabled | `enforce` unless explicit |

Allowed values are:

- `AUTO_TRADE_STRUCTURAL_GUARD_MODE=observe|balanced|strict`
- `AUTO_TRADE_ZONE_RECONCILE_MODE=off|shadow|enforce`

Structural guard and reconciliation-mode disagreement is warning-only in
config health so an evaluation executor is not disabled by a presentation or
shadow-policy mismatch. Existing fatal fields remain fatal. In particular,
candidate contract, execution mode, Redis streams, symbol/pip contract and
demo-account requirements still fail closed.

`AUTO_TRADE_ZONE_COOLDOWN_ENABLED` controls Python enforcement. Even when
enabled, only a Redis marker with `reason=stop_loss` and
`confidence=confirmed` may block. `manual_close`, `external_close`,
`reconciliation_unknown` and `take_profit` do not enforce a cooldown.

## Final stop and fenced leases

Shared stop planning uses `stop_plan_version=2` (base + final fields, including
opposing-zone push). `AUTO_TRADE_STOP_PUSH_BEYOND_ZONE` and execution-zone width
limits remain part of the shared contract.

`AUTO_TRADE_ENTRY_CONTRACT_TOLERANCE_PIPS` (default `3`) is the maximum
allowed gap between Python's planned entry and the executor's resolved
planned entry, and — for market routes only — between that planned entry and
the executable quote. It is never smaller than one symbol tick. Drift beyond
tolerance rejects the candidate before any broker call
(`final_stop_entry_drift_rejected`); it never silently widens the stop.

Candidate Redis markers are structured JSON with token-owned renewable
leases, reclaimable `retryable_error`, and recovery-required
`broker_outcome_unknown`. Readers must accept legacy plain strings during
rolling deploy. See `docs/autotrade-execution-integrity.md`.

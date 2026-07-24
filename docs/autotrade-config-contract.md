# Auto-trade configuration contract

The Python publisher and C# executor share config manifest version 2 and
candidate contract version 5. Cross-service values use these canonical
environment variables:

```text
AUTO_TRADE_PROFILE
AUTO_TRADE_ENABLED
AUTO_TRADE_DRY_RUN
AUTO_TRADE_CANDIDATE_STREAM
AUTO_TRADE_EVENT_STREAM
AUTO_TRADE_CANDIDATE_CONTRACT_VERSION
AUTO_TRADE_SYMBOLS
AUTO_TRADE_CANONICAL_SYMBOL
AUTO_TRADE_XAU_PIP_SIZE
AUTO_TRADE_XAU_CONTRACT_SIZE
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

# Trendline V2 Causal Redesign

## Scope

This redesign changes only the Trendline strategy. M5 owns structural
geometry and the current interaction; a fresh M1 trigger owns execution.
Other reaction families retain their existing entry contract.

## Root Cause And Previous Flow

V1 selected arbitrary historical pivot pairs, including A/C, then counted all
pivots close to that fitted line as touches. Its average fit error included the
two defining points, whose residual is necessarily zero. It treated a nearby
pivot as a touch without requiring a reclaim or favorable reaction. Wick and
close violations were collapsed to one count, and the wide ATR band could
become an M5-authoritative entry area.

The old flow was `swings -> any two-pivot fit -> proximity touches -> ATR
band -> possible M5 entry`. M1 confirmation was optional for Trendline.

## Causal V2 Flow

`confirmed swings -> consecutive A/B anchors -> immutable projection ->
forward validation -> M5 interaction -> fresh M1 confirmation -> TradePlan`.

1. A fractal pivot now carries `confirmed_index` and `confirmed_ts`; V2 does
   not use it before its confirming right-side bars have closed.
2. Consecutive same-side pivots meeting minimum spacing form immutable A/B
   anchors. A later C is only projected against that A/B line. It cannot
   redraw an A/C line around B.
3. C/D validations must be independently spaced and must pass geometry,
   correct approach, limited penetration, close reclaim, and favorable
   excursion. Their errors are stored separately from anchor error.
4. Near-identical projections retain the earliest anchor pair and its state.
   A later B/C pair cannot erase the original line's tentative, degraded,
   broken, or exhausted lifecycle.
5. A line is `tentative` with only anchors, `confirmed` after the configured
   forward validations and span, `degraded` after recovered close damage or
   excess wick damage, `broken` after unresolved close-through, and
   `exhausted` after repeated independent validations.

## Health And Interaction

V2 records independent validation precision, `slope_atr_per_bar`, validation
spacing, wick and close violations, maximum/latest penetration, recovery,
and violation age. A wick beyond the penetration threshold which closes back
on the correct side is recorded as reclaimed; an unresolved M5 close beyond
the close threshold is broken.

The live interaction state is descriptive and non-executable:
`ABOVE/BELOW`, `APPROACHING`, `TESTING`, `RECLAIMED`, or `FAILED`. BUY support
must approach from above and SELL resistance from below. `TESTING` emits
rejection telemetry and cannot arm a plan. The interaction band is 0.20 ATR
by default and is not an entry permission.

## Execution, Regime, And HTF

Only a causally confirmed, healthy M5 reclaim can create a Trendline V2
candidate. `confirmation_policy_for` then disables M5-authoritative fallback,
requires an in-zone fresh M1 trigger after the M5 interaction, and rejects a
missing or stale M1 trigger. Its type and timestamp are written into the
Trendline evidence before plan publication.

Trendline remains available in chop. In chop, the configuration requires two
independent validations and aligned HTF context; outside chop the existing
optional Trendline HTF rule remains respected. This distinguishes quality
context from a global regime veto.

## Configuration

`analysis.trendlines.version` defaults to `v1` for compatibility. Production
configuration selects `v2`, with metrics-only `shadow_v1` enabled. New
environment-backed parameters are:

- `TL_VALIDATION_TOUCH_TOL_ATR=0.30`
- `TL_INTERACTION_BAND_ATR=0.20`
- `TL_INVALIDATION_PENETRATION_ATR=0.50`
- `TL_CLOSE_VIOLATION_ATR=0.15`
- `TL_APPROACH_MIN_DISTANCE_ATR=0.10`
- `TL_MIN_VALIDATION_TOUCHES=1`
- `TL_MIN_VALIDATION_TOUCH_SPACING=5`
- `TL_VALIDATION_REACTION_BARS=2`
- `TL_MIN_VALIDATION_FAVORABLE_EXCURSION_ATR=0.10`
- `TL_MAX_WICK_VIOLATIONS=2`
- `TL_EXHAUSTION_VALIDATION_TOUCHES=4`
- `TL_CHOP_MIN_VALIDATION_TOUCHES=2`
- `TL_CHOP_REQUIRE_HTF_ALIGNED=true`

The Ansible mirror carries the same production settings. V1 remains available
by setting `version: v1`; V1 shadow metrics never create a V1 order.

## Telemetry And Compatibility

`DetectionResult`, `StrategyMatch`, and `TradePlanAnalysis` now transport a
JSON-safe `trendline_v2` payload. It contains anchors and their availability,
validations and reaction telemetry, counts, raw and ATR-normalized slope,
violation and penetration health, live interaction/band/approach, M5/M1 time
ordering, regime/HTF context, and explicit entry or rejection reasons. C#
retains this as an opaque `JsonElement`; it never recalculates scanner
geometry. Existing V1 serialized lines and plans remain readable because all
new fields are additive.

## Reference Replay

The repository contains no exact production OHLC fixture for plan
`v8:b3feb46882a971c1376d7c00fdf892c9`, so performance statistics must not be
inferred. `tests/test_trendline_v2.py` instead has a deterministic fixture
using the supplied critical geometry:

- A: index 106, 4258.84; B: index 117, 4266.43; C: index 143, 4281.12.
- V1's A/C fit made B eligible by proximity and reported deceptively low
  aggregate fit error because A and C defined the line.
- V2's immutable A/B projection at C is about 4284.37. C differs by about
  3.25 price, or 0.526 ATR, exceeding the 0.30 ATR validation tolerance.
- Result: the A/B line remains `tentative`, with zero forward validations;
  the rejection stage is `insufficient_forward_validation`. No A/C bypass is
  surfaced as a replacement line.

The same suite contains clean rising-support BUY and falling-resistance SELL
fixtures. Both reach `confirmed` with one independent forward validation.
They are structurally eligible only after a new M5 reclaim and fresh M1
trigger; the test intentionally does not fabricate a broker entry from line
contact alone.

## Tests And Remaining Work

Focused coverage includes the supplied incident geometry, BUY/SELL positive
confirmation, two-anchor tentative state, complete reaction-window timing,
shadow-only V1 evaluation, wick recovery/degradation, close break, exhaustion,
wrong-side approach, testing-without-reaction, fresh-M1 policy, and
StrategyMatch/TradePlan evidence round trips.

Historical OHLC plus fill/outcome data should be added to the replay harness
before declaring V2 better. The next review should compare opportunities,
armed trades, rejection reasons, MAE/MFE, expectancy, and performance split by
trend/chop and HTF alignment. Default tolerances are initial conservative
operational values, not performance-optimized claims.

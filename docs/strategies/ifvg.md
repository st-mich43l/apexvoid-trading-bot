# iFVG (v3)

> **Quality review (2026-10-14): RETAIN** (proposal, not applied). Technical PASS, execution PARTIAL, performance UNPROVEN, evidence WEAK. Scope: all instruments. See [ae-quality-certification.md](ae-quality-certification.md) and [ae-strategy-retirement-review.md](ae-strategy-retirement-review.md). Independent review: pending.

`ifvg` trades the retest of a fair-value gap that has just lost its role. v3
replaces the frozen-Python-parity v2 with a Go contract that owns its gap, accepts
an inversion only on a measured closing-price displacement, requires a fresh
retest strictly after the inversion, and sets the stop and target from structure.
This page is the contract: the sequence, the mathematics, the exact code behaviour
and every intentional difference from the Python detector.

- **Owner**: Go Analysis Engine, package `internal/strategy/ifvg`. No Python scanner
  or second decision exists; the Algo Bot only applies execution policy.
- **Independence**: no strategy-family base, no import of another strategy package
  (`internal/strategy/independence_test.go`), and nothing from Confluence Zone, FVG,
  Order Block or the canonical zone chain. It reads only closed M5 candles plus the
  engine's HTF alignment, M5 structure bias and confluence rubric as *measured
  evidence*.
- **Contract version**: `v3`. `Quality.Overall` keeps the scale live arbitration has
  always seen (confluence stars out of three); the measured quality is in
  `Quality.Components` ([Quality](#quality-and-arbitration)).

## Q2: measured session context and the causal inversion proof

* **The inversion is never a formation candle.** The gap is the three completed candles A, B, C (`low(C) > high(A)` bullish, `high(C) < low(A)` bearish, interval `[high(A), low(C)]` / `[high(C), low(A)]`);
  candidate inversions start at the candle after C (`for j > c`), and the first close beyond the far bound by the ATR-normalised buffer (known from the candle BEFORE the inverting one) is the inversion.
  `TestTheFormationCandlesNeverInvertTheirOwnGap` pins it, `TestPrefixInvarianceAndNoFutureLeak` proves no later candle changes a decision.
* **`session_context` is evidence, not a constant.** It was set on every iFVG decision (+2 raw points). It is now true only when a finalized session, previous-day or previous-week level that supports the
  trade was swept and reclaimed between the gap's first candle and the rejection (`LegacyDetector.SessionSweepBetween`; see [shared-structure-evidence.md](shared-structure-evidence.md)). The level must have been
  final before the sweep, its first breach must have been reclaimed, and one event counts once. The candidate then carries the evidence code `session_liquidity_swept`.
* Effect on the 17-capture replay: 127 -> 104 opportunities (stars 2/3: 68/59 -> 74/30). The 23 removed had only their own candle evidence (touches, wick, inversion force) and no higher-timeframe agreement.

## Status and execution

- **Certification**: `GO_NATIVE_VALIDATED` ([matrix](README.md#certification)): the
  technical contract below is certified by tests and replays and is deliberately
  **not** parity with the Python detector. It makes no profitability claim.
- **Symbols**: all instruments. Pip size, the proximal entry slice
  (`fvg_entry_max_width_price`) and the execution stop cap
  (`execution_stop_max_pips`) come from the instrument configuration.
- **Execution**: unchanged by this document. The Algo Bot's `ifvg` scope needs a
  confirmed reaction and an `m5_ifvg_` evidence code; both are carried. XAU execution was
  enabled on 13 Oct 2026 by the owner's decision (`config/instruments.yml`,
  [xau-enablement-2026-10](xau-enablement-2026-10.md)); on FX it trades, so **this contract replaces the FX
  iFVG decision too** (see [FX behaviour change](#fx-behaviour-change)).
- **Not deployed**: nothing here enables or disables a strategy.

## Time and price conventions

- Every timestamp is a candle **open** time (Unix seconds); a candle closes 300 s
  later. Ages and windows are counted in M5 periods from open times
  (`(t_b - t_a) / 300`), never from slice positions, so a feed gap ages a setup
  instead of hiding it.
- **ATR** is Wilder ATR(14) over a *fixed trailing window* of 140 candles ending at
  the candle the value is read for, so it is a pure function of those candles and
  does not depend on how much older history is loaded. The inversion buffer uses the
  ATR **before** the inverting candle; every ratio of a candle uses the ATR at it.
- A **distance** is `max(pips x pip_size, ATR multiple)`; prices are never compared in
  another instrument's pips.
- The detector is written once, for a BUY (a bearish gap inverted upward), and the
  SELL is its exact mirror: the series is reflected about zero, decided, and mapped
  back (`TestMirrorSymmetry`, `TestBearishTwinIsTheExactMirror`).
- Everything is evaluated **as of the last closed candle** and reads only candles
  up to it, and at most the trailing `requiredHistory` of them
  (`TestPrefixInvarianceAndNoFutureLeak`, `TestRequiredHistoryIsSufficient`).

## Causal formation sequence

```
FVG_CREATED -> INVERSION_CONFIRMED -> RETEST_WAITING -> RETEST_TOUCHED
            -> REJECTION_CONFIRMED -> CANDIDATE
terminal: GAP_INVALIDATED | INVERSION_FAILED | RETEST_FAILED | EXPIRED | STRUCTURE_INVALIDATED
```

An episode is one gap and the candle that inverted it; it moves forward only. A gap
is tracked implicitly until a close goes through it (a gap that never inverts is not
an episode). The states reached and the exact refusal reason are published by
`Analyze` for audit.

### 1. The original gap

For three consecutive closed candles A, B, C (C is the candle being formed):

- **bearish gap** (inverted upward, a BUY): `low[A] > high[C]`;
  `G_low = high[C]`, `G_high = low[A]`
- **bullish gap** (inverted downward, a SELL): `high[A] < low[C]`;
  `G_low = high[A]`, `G_high = low[C]`

`gap_width = G_high - G_low`, `gap_atr = gap_width / ATR_C`. The gap is valid only if:

1. A, B and C are consecutive (`t_C - t_B = t_B - t_A = 300 s`): a missing candle
   inside the three is not a gap.
2. `gap_width >= max(minimum_gap_pips x pip, minimum_gap_atr x ATR_C)` (0.5 ATR, the
   v2 baseline, and 2 pips).
3. B is a real displacement in the gap's direction: its body
   `|close-open| / ATR_C >= minimum_origin_body_atr` (0.5) and
   `|close-open| / (high-low) >= minimum_origin_body_ratio` (0.5).

The gap's own window: it must invert within `maximum_inversion_bars` (72, six hours)
of forming; an older gap is stale structure.

### 2. The inversion

Let `j` be the **first** candle after C whose **close** is beyond the gap's far bound
(for a BUY: `close_j > G_high`), by any amount. A wick through the gap is not a close
through it. That first close is judged, once:

```
inversion_buffer = max(minimum_break_pips x pip, minimum_break_atr x ATR_(j-1))
close_j > G_high + inversion_buffer                  else INVERSION_FAILED (inversion_close_inside_buffer)
body_ratio_j      = (close - open) / (high - low)    >= minimum_inversion_body_ratio     (0.5)
close_location_j  = (close - low)  / (high - low)    >= minimum_inversion_close_strength (0.6)
body_atr_j        = (close - open) / ATR_j           >= minimum_inversion_displacement_atr (0.5)
                                                     else INVERSION_FAILED (inversion_quality_insufficient)
```

The candle must be bullish for a BUY (bearish for a SELL). If the first close beyond
the gap fails either test the gap is finished: a later strong candle is no longer its
first close through the gap, so a weak close can never be "promoted" by a later one
(`TestAnEarlierWeakCloseConsumesTheGapsInversion`). Measured and published:
`inversion_close_beyond_atr`, `inversion_body_ratio`, `inversion_close_strength`,
`inversion_displacement_atr`, `bars_gap_to_inversion`.

### 3. The fresh retest

The retest is searched from candle `j + 1`: **strictly after** the inversion, so an
inversion and its retest are never one event.

```
retest_tolerance = max(retest_touch_pips x pip, retest_touch_atr x ATR_j)
flip_fail_buffer = max(role_flip_fail_pips x pip, role_flip_fail_atr x ATR_j)
touch:         low_k <= G_high + retest_tolerance
role flip failed:   close_k < G_low - flip_fail_buffer      (any time before the entry is taken)
retest window: touch must come within retest_max_bars (24) of the inversion, else EXPIRED
```

The role flip failing before a touch is `INVERSION_FAILED (role_flip_failed)`; after a
touch it is `RETEST_FAILED (retest_closed_through)`. A rejection candle `r` (the touch
candle itself or one of the next `confirmation_window_bars - 1`, 3 in all) confirms:

```
close_r > open_r  and  close_r > G_high                      (back on the bullish side)
close_strength_r >= rejection_close_strength (0.6)
and ( lower_wick_ratio_r >= rejection_wick_ratio (0.25)  or  body_ratio_r >= rejection_body_ratio (0.6) )
```

A touch with no rejection inside its window expires (`confirmation_window_expired`),
and the gap may be touched again. The confirmation is **fresh** for
`confirmation_max_age_bars` (2) candles after it; later it is `confirmation_stale`.
A second retest of the same inversion is a separate episode (a distinct
`Key`, the same thesis) only after price has left the gap again by
`retest_departure_atr` (1.0 ATR), and at most `max_retests_per_inversion` (2) are
evaluated. A touch hugging the gap's top is never a second episode
(`TestATouchWithoutDepartureIsNotASecondEpisode`).

Rejected: a reaction before the inversion (the retest search starts after it), the
inversion and the retest read as one event, a stale gap interaction, the same touch
confirmed twice (one thesis, one candidate per confirmation), a failed role flip, a
close through the whole gap, a retest after the window.

### 4. Entry, stop, target

```
retest_extreme = lowest low from the touch to the rejection (highest high for a SELL)
adverse        = min(G_low, retest_extreme)
stop           = adverse - max(pip, invalidation_buffer_atr x ATR_c)
entry          = [ G_high - slice , G_high + entry_tolerance ]
                 slice = min(gap_width, fvg_entry_max_width_price)    (the instrument's proximal cap)
risk           = entry_high - stop
```

- The stop protects the actual invalidation geometry: beyond the gap's far bound and
  beyond whatever the retest itself traded to. It is **never tightened** and a stop
  beyond the instrument's execution cap is refused (`risk_exceeds_execution_envelope`),
  not clipped.
- The target is the nearest credible opposing swing: a pivot high (SELL: low) that was
  already confirmed when the rejection was (a later pivot cannot move the target of a
  setup that was published), above everything price traded since the inversion and
  above the entry, heading a swing of at least `target_swing_atr`. The nearest decides; no target is
  manufactured. `no_opposing_structure` / `insufficient_target_room` are refusals.
- Published only when `reward / risk >= minimum_reward_risk` (1.15), the room is at
  least `minimum_target_room_atr` (0.55), the target has not been reached since the
  confirmation, no later candle took out the stop or closed through the gap, and the
  last close is not through the entry band nor more than `maximum_entry_distance_atr`
  (1.5 ATR) above it.

## Identity and expiry

- **Thesis** (`StructuralID`): `technique:ifvg:<side>:fvg:<role>:<gapFormedAt>:<invertedAt>`.
  Stable across every re-confirmation of the same inversion.
- **Candidate id**: the thesis plus the retest touch time and the confirmation time:
  stable while the confirmation is fresh, distinct for a separate retest.
- `FormedAt` is the inversion candle, `CreatedAt` the rejection candle. Expiry is
  `expiry_hours` (4) after the confirmation (v2 used 24).
- The confirmed reaction (`Reaction`) carries the thesis as `ZoneID`, the touch and
  confirmation candles, and a pattern the Algo Bot already knows
  (`wick_rejection`, or `strong_reclaim` when the rejection is body-led).

## Quality and arbitration

`Quality.Overall = stars / 3`, exactly the scale v2 published; only the *inputs* of the
stars are now measured:

| Factor | Measured as |
|---|---|
| HTF aligned | the engine's HTF read agrees with the direction |
| Touches | distinct retest touches of the inverted gap, this one included |
| Wick rejection | the rejection candle's wick ratio meets `rejection_wick_ratio` |
| Displacement grade | inversion `body / ATR >= displacement_grade_atr` (1.0) |
| Structural agreement | the engine's M5 structure is up (BUY) / down (SELL) |
| Session context | always true: Asia, London and NY partition the UTC day |

The floor is the shared `min_confluence` the Algo Bot also applies. Published
`Quality.Components`: `gap_width_atr`, `origin_displacement_atr`,
`inversion_body_ratio`, `inversion_close_strength`, `inversion_displacement_atr`,
`inversion_close_beyond_atr`, `bars_gap_to_inversion`, `retest_age_bars`,
`retest_penetration_atr`, `rejection_strength`, `rejection_wick_ratio`,
`retest_touches`, `target_room_r`, `target_room_atr`, `zone_integrity`,
`technical_risk_pips`, `confluence_stars`, and `structure_alignment` **only when the
engine has a structural read** (an unavailable fact is left out, never replaced by a
neutral value). The v2 `inversion_strength` / `freshness` components are gone: they
were not measurements of the thing they named.

Arbitration is unchanged and was compared (replay, below): v2 confirmed setups were 0 %
one-star, 68 % two-star and 32 % three-star; v3 is on the same two-value scale
(0.67 / 1.0) with a mix of 53 % / 47 % (37 and 33 setups), so selection among strategies is not shifted by a new
scale.

## Valid and invalid, at a glance

| Fact | Result |
|---|---|
| Close 0.05 beyond the gap (inside the buffer) | `INVERSION_FAILED` |
| Wick far through the gap, close inside it | no inversion, no episode |
| Weak inversion candle (small body, long wick) | `INVERSION_FAILED` |
| Gap under 0.5 ATR or 2 pips | no gap |
| Middle candle not a displacement | no gap |
| Missing candle inside A-B-C | no gap |
| Inverted more than 72 periods after forming | not evaluated |
| Retest on the inverting candle | impossible (search starts at `j + 1`) |
| Rejection closes back inside the gap | no confirmation |
| Close through the whole gap after the inversion | `role_flip_failed` / `retest_closed_through` |
| Touch, no rejection inside 3 candles | `confirmation_window_expired` |
| Confirmation older than 2 candles | `confirmation_stale` |
| Stop beyond the instrument cap | `risk_exceeds_execution_envelope`, never tightened |
| No opposing swing / too close / RR under 1.15 | refused |

## What v2 got wrong (reproduced by `test/ifvgreplay/defects_test.go` on real engine contexts)

The frozen v2 (kept in `test/ifvgreplay/baseline_test.go`, never imported by
production) published setups on the committed XAU capture (Sep 14-21, 110 confirmed
setups, 97 rebuilt offline) and GBPJPY (6 Oct, 163 setups, 148 rebuilt). Measured
against the v3 contract from the capture's own candles:

| Weakness named in the rebuild brief | XAU 14-21 Sep | GBPJPY 6 Oct |
|---|---|---|
| Gap narrower than `minimum_gap_atr` (the confirmed path never applied the filter its resting path did) | 59 | 103 |
| Middle candle not a displacement | 18 | 50 |
| Inversion by a close inside the buffer (a close-through by any amount) | 26 | 72 |
| Inverting candle below the body / close-location / displacement bars | 38 | 57 |
| Reaction touch not strictly after the inversion | 54 | 71 |
| Gap inverted more than 72 periods after it formed | 10 | 6 |
| Any of the above | 82 of 97 | 132 of 148 |

`TestFrozenV2DefectsReproduceOnRealData` asserts every row is non-zero, that the same
independent offline measurement finds **no** defect in what v3 published, and
`TestEngineAndOfflineDetectorAgreeOnTheCaptures` that the engine's context and the
detector read from the capture's candles agree on every evaluated bar (0 mismatches).
The deterministic synthetic XAU paths (`synthetic_test.go`) run through the real
engine: the clean path is published exactly once; a 0.05 point inversion and a 0.5
point gap publish nothing. (The v2 zone chain does not form an iFVG on a path that
small, so the v2 weaknesses are pinned on the immutable real captures instead.)

Other v2 behaviour that is gone: the resting observation (a technical observation the
Algo Bot could not execute) is no longer emitted; the quality components that were
constants or copies are replaced by measurements; the confirmed reaction is bound to
the inversion; the canonical zone strength is not used.

## Replay evidence

Hypothetical outcomes under causal fill rules (`test/ifvgreplay/outcome_test.go`):
the order is placed when the observing candle closes, fills only on a **later** candle
at the proximal edge, the fill candle can only stop the position out, the stop wins a
same-candle tie, 8 hours maximum. Never realised performance; broker outcomes that do
not exist are not counted as wins. Reproduce with
`IFVG_REPORT_DIR=/tmp/ifvg go test ./test/ifvgreplay -run Certification`.

| Capture [role] | v2 confirmed setups (mean R, hypothetical) | v3 setups (mean R) | v2 setups v3 also publishes |
|---|---|---|---|
| XAU 14-21 Sep [development] | 110 (-0.15) | 7 (-0.52, n=6 filled) | 4 |
| XAU 28 Sep-6 Oct [incident] | 120 (-0.28) | 14 (+0.33, n=13) | 9 |
| EURUSD 6 Oct [held-out] | 212 (+0.10) | 7 (-0.30, n=6) | 9 |
| GBPUSD 5 Oct [held-out] | 197 (-0.12) | 9 (-0.28, n=8) | 8 |
| GBPJPY 6 Oct [held-out] | 163 (+0.11) | 15 (-0.07, n=14) | 7 |
| USDJPY 5 Oct [held-out] | 192 (+0.02) | 18 (-0.19, n=17) | 14 |

Pooled with 95 % seeded bootstrap intervals (`summary.md` of the report): XAU
development v2 -0.15 [-0.48, 0.26] vs v3 -0.52 [-1.00, 0.45]; XAU incident v2 -0.28
[-0.54, 0.00] vs v3 +0.33 [-0.55, 1.29]; the four FX captures v2 +0.03 [-0.11, 0.17]
(n=750) vs v3 -0.18 [-0.56, 0.23] (n=45). The v2 setups that already satisfied the v3
contract ("clean") averaged +0.11 / 0.00 / 0.00 R on development / incident / held-out
(n = 15, 18, 83); the defective ones -0.11 / -0.36 / +0.02 (n = 82, 82, 587).

**Reading.** v3 is far more selective (about 94 % fewer setups on the FX captures, 91 %
on XAU) and every published setup meets its technical contract, but **no interval
excludes zero and the v3 samples are 6 to 17 filled setups per capture.** The honest
conclusion is that the evidence neither proves nor disproves an expectancy
improvement. The only association the data supports is directional on XAU, where the
v2 setups that violated the contract lost more than those that did not (-0.36 vs 0.00
on the incident window); that window also motivated the rebuild, so it is not
independent evidence. The incident-window capture therefore proves nothing about
future profitability. Stated differently: what is **proven** is technical correctness
and causality; what is **unproven** is any profitability claim, and an unproven
expectancy is exactly why XAU execution needs the owner's explicit approval.

How the v3 funnel behaves (distinct inversion episodes, all six captures): of 779
inversions started, 378 reach a retest touch, 196 a rejection, 70 are published; the
terminal reasons are dominated by `inversion_close_inside_buffer`, `confirmation_window_expired`,
`retest_closed_through` and `reward_risk_below_minimum`. The
v2 setups v3 does not publish are mostly `gap_below_minimum` (the confirmed path never
applied the width filter).

## FX behaviour change

iFVG trades on FX today. Replacing it changes live FX behaviour, not only XAU: on the
four FX captures v2 published 764 confirmed setups and v3 publishes 49. Both are
small-sample, hypothetical results; the FX held-out check does not show v3 earning
more (mean R -0.18 [-0.56, 0.23], n=45) nor v2 earning more (+0.03 [-0.11, 0.17],
n=750). Treat merging this contract as a deliberate decision about FX iFVG
frequency, not as a no-op.

## Intentional differences from Python parity

| v2 / Python | v3 |
|---|---|
| First close through the gap by any amount | A close beyond a buffer, with measured body, close location and displacement |
| Minimum gap width only on the resting path | Applied to the gap itself, on every path |
| No test of the middle candle | Middle candle must be a displacement |
| Reaction may touch on or before the inversion candle | Touch strictly after the inversion |
| Resting observation emitted | Only confirmed rejections are published |
| Strength from the canonical zone chain | Not used; every component measured from candles |
| Stop: structure far bound - 0.5 ATR | Beyond min(gap far bound, retest extreme) - 0.25 ATR; refused over the cap |
| Target: nearest liquidity pool or a fixed 2R | Nearest credible swing with room; no manufactured target |
| Expiry 24 h | 4 h |
| No retest limit | At most 2 retests per inversion, the second only after a departure |

The frozen Python oracle goldens (`test/techniqueparity`, `technique-parity-oracle.json`)
are untouched: the canonical zone chain, which Confluence Zone and the other
techniques still use, is unchanged, and iFVG is the only consumer that moved.

## Tests executed

- `internal/strategy/ifvg`: 45 tests (unit, 27 negative scenarios each changing one fact,
  randomized prefix invariance over 8,661 evaluations, history sufficiency, BUY/SELL
  mirror symmetry, a 104-setup invariant property test, determinism), all mutation
  checked: removing any of 24 rules makes a test fail (the one surviving mutant is
  equivalent: a gap-direction guard already enforced by the width test).
- `test/ifvgreplay`: defect reproduction, engine-vs-offline agreement, no future
  dependence bar by bar on all six captures, technical invariants of every setup, a
  committed golden (`testdata/ifvg-v3-detect-golden.json`) and the synthetic paths
  through the real engine.
- `test/strategy`, `test/engine`: the v2 qualifying-fixture and two v2-only tests were
  replaced by v3 equivalents (known qualifying fixture, one candidate per retest, the
  production sliver gap of 2026-09-30); the strategy-config test now expects `ifvg` v3.
- The XAU replay meta (`replay-go-xau-20260921.meta.json`) was regenerated: iFVG
  opportunities changed from the v2 count to the v3 count.

## Unresolved risks

- **No profitability claim.** Replay samples are small and every interval includes
  zero; XAU v2 was negative in both windows, v3 has 6 and 13 filled setups there.
- **FX is live** and changes with this contract (above).
- Thresholds are hypotheses chosen on structure, not tuned on outcomes; the
  development capture was only used to check their magnitude.
- The target is a swing high/low of the same M5 candles; no higher-timeframe or
  session liquidity is read (that belongs to a later iteration, not a hidden filter).
- The resting-zone observation is no longer published; anything that consumed it as an
  informational card would no longer see it (nothing in the Algo Bot executes it).

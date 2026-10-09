# Break & Retest (v3)

`break_retest` trades the retest of a structure price has just broken. v3 replaces
the frozen-Python-parity v2 with a Go contract that owns its structures, accepts a
break only on measured evidence, requires a fresh retest strictly after the
acceptance, and sets the stop and target from structure. This page is the
contract: the sequence, the mathematics, the exact code behaviour, and every
intentional difference from the Python detector.

- **Owner**: Go Analysis Engine, package `internal/strategy/breakretest`. No Python
  scanner or second decision exists; the Algo Bot only applies execution policy.
- **Independence**: no strategy-family base, no import of another strategy
  package (`internal/strategy/independence_test.go`), and nothing from Confluence
  Zone. It reads only closed M5 candles plus the engine's HTF alignment, M5
  structure bias and confluence rubric as *measured evidence*.
- **Contract version**: `v3`. `Quality.Overall` is **unchanged** (0.75, see
  [Quality and arbitration](#quality-and-arbitration)).

## Status and execution

- **Certification**: `GO_NATIVE_VALIDATED` ([matrix](README.md#certification)): the
  technical contract below is certified by tests and replays, and is deliberately
  **not** parity with the Python detector. It makes no profitability claim.
- **Symbols**: all instruments. Pip size and the execution stop cap come from the
  instrument configuration (`execution_stop_max_pips`).
- **Execution**: unchanged. The Algo Bot's break-retest scope needs one of
  `m5_trendline_break`, `m5_key_level_break`, `m5_trendline_retest`,
  `m5_key_level_retest`, `m5_retest_holds`, `m5_retest_rejection` and requires no
  separate reaction; XAU ladder/FX single-entry/stop envelope/arbitration are the
  Algo Bot's and untouched.
- **Not deployed**: this change ships behind review; nothing here enables or
  disables a strategy.

## Time and price conventions

- Every timestamp is a candle **open** time (Unix seconds, integers); a candle closes
  300 s later. Ages and windows are counted in M5 periods **from open times**
  (`(t_b - t_a) / 300`), never from slice positions, so a feed gap ages a
  confirmation instead of hiding it.
- **ATR** is Wilder ATR(14) over a *fixed trailing window* of 140 candles ending at
  the candle in question (`TR = max(H-L, |H-C_prev|, |L-C_prev|)`,
  `ATR_t = (13 ATR_{t-1} + TR_t) / 14`, seeded with the mean of the first 14 TRs). The
  fixed window removes the dependence of Wilder's recursion on how much history is
  loaded: the decision at a candle is a pure function of the candles in
  `requiredHistory`.
- Every distance is `max(pips x pip size, ATR multiple)` in **price units**
  (`pip_size` is 0.1 on XAU, 0.0001 on EURUSD, 0.01 on JPY pairs), so XAU, FX and
  JPY thresholds are each meaningful at their own scale.
- A **SELL is the exact price mirror** of a BUY: the candles are reflected
  (`O' = -O, H' = -L, L' = -H, C' = -C`), the one bullish detector runs, and the
  result is reflected back, so the two directions cannot drift apart
  (`TestMirrorSymmetry`).

## Causal formation sequence

Evaluated once per closed M5 candle `e`, using candles `<= e` only. State machine of
one *episode* (one break attempt of one reference), bullish orientation:

```
REFERENCE_VALID --close beyond by buffer--> BREAK_PENDING --k accepted closes--> BREAK_ACCEPTED
        |                                        |                                    |
        |                                        +-- falls back / weak break          v
        |                                            -> FALSE_BREAKOUT          RETEST_WAITING
        |                                                                             |
        |      closes back through the level before any touch: FALSE_BREAKOUT <-------+
        |      protected structure traded: STRUCTURE_INVALIDATED <-------------------+
        |      no touch within the window: EXPIRED <---------------------------------+
        |                                                                             | low reaches the level
        |                                                                             v
        |      closes through after the touch: RETEST_FAILED <---------------- RETEST_TOUCHED
        |      no rejection candle within the window: EXPIRED <--------------------+
        |                                                                           | directional rejection candle
        |                                                                           v
        |      geometry / confluence / freshness refuses it                 RETEST_CONFIRMED --> CANDIDATE
        +---   (the exact reason is recorded)  <-------------------------------+
```

A valid reference that has not been broken yet (`REFERENCE_VALID`) is not an episode
and is not reported; episodes start at the first beyond-candle.

`RETEST_CONFIRMED` that is refused keeps its reason (`risk_exceeds_execution_envelope`,
`confirmation_stale`, `confluence_below_floor`, ...), so a rejected setup is never
silent. Every episode is reported (`Analysis.Episodes`) with its state, reason and the
open time of the candle that moved it there.

### 1. Reference structures

Pivots are fractals over `pivot_bars` = 2 candles: a **pivot high** has a high
strictly above the 2 candles before it and at least the 2 after (so one touch of an
equal-high plateau is one pivot, while two equal highs more than 2 candles apart are
two touches: a double top is a level). A pivot at `p` is knowable once candle `p+2`
closed, so **a reference only uses pivots confirmed strictly before the break
candle `b`** (`conf < b`), within `reference_lookback_bars` = 120.

- **Key level** (`level:<first pivot open time>`): at least `level_min_touches` = 2
  pivot highs clustered by price (each within `max(2 pips, 0.4 ATR)` of every other,
  total span at most twice that). The level is the mean of the members. `Touches`
  is the member count; `FormedAt` is the open time of the candle whose close made the
  second touch knowable; `AnchorTime` is the first touch.
- **Trendline** (`line:<anchor A open>:<anchor B open>`): two chronologically adjacent
  pivot highs at least `line_min_span_bars` = 6 candles apart whose slope is between
  0.01 and 0.10 ATR per candle (a flatter line is a level), with no market-closure gap
  inside the span or between the second anchor and the break. The line is evaluated **at each candle's own index**,
  `v(i) = P_B + slope * (i - i_B)`, never at the last candle. Between anchor B and the
  break no close may be above `v + buffer` and no wick above `v + 0.25 ATR`. `Touches`
  is 2 plus the later pivot highs within the cluster tolerance of the line.

Side before the break: for `pre_break_bars` = 3 candles before `b` every close is at or
below `v + buffer` (price was really below a resistance).

### 2. Accepted break

`b` is the *first* beyond-candle: `C_b > v(b) + buffer`, where
`buffer = max(1 pip, 0.05 ATR_{b-1})` (the ATR as of the candle BEFORE the break, so the
break candle's own range never decides whether a reference exists). The break is **accepted** after
`breakout_accept_bars` = 2 consecutive closes beyond `v + buffer`; a candle of the run
that closes back, or that does not follow the previous candle by exactly one period (a
feed gap), is a `FALSE_BREAKOUT` (`break_not_accepted`). The first beyond-candle
must carry real force, all measured:

- `body_ratio = |C-O| / (H-L) >= 0.5`
- `close_strength = (C-L) / (H-L) >= 0.6`
- `displacement = (C_accept - O_b) / ATR_b >= 0.5`

otherwise `FALSE_BREAKOUT` with reason `break_quality_insufficient`. All four numbers
(and the acceptance close's distance beyond the structure in ATR) are published.

### 3. Retest

Strictly after the acceptance candle `a`, within `retest_max_bars` = 24 periods:

- **Touch**: a candle with `L <= v(i) + max(1 pip, 0.15 ATR_b)`. The acceptance run's
  own candles never count.
- **Failure on or before the first touch**: a close below `v(i) - max(2 pips, 0.3 ATR_b)`
  on the candle that first touches the level, or earlier, is a `FALSE_BREAKOUT`
  (`break_reclaimed_before_retest`: the break never held); once a touch has been made, a
  later close through is `RETEST_FAILED` (`retest_closed_through`).
- **Protected structure**: any trade below it after its pivot and before the
  confirmation (the base, the break run and the retest alike) is `STRUCTURE_INVALIDATED`
  (`protected_structure_lost`). The protected structure is the
  most recent confirmed pivot **low** before `b` (`protected_structure: last_pivot`) or
  the lowest low from `pre_break_bars` before `b` to the acceptance
  (`break_origin`); it must be below the reference.
- **Rejection** within `confirmation_window_bars` = 3 candles of the touch (the touch
  candle included): a bullish candle closing above the level, `close_strength >= 0.6`,
  with a lower wick `>= 0.25` of its range **or** a body `>= 0.6`. Without it the
  episode `EXPIRED` (`confirmation_window_expired`); no touch inside the window
  `EXPIRED` (`retest_window_expired`).
- A confirmation is **fresh** for `confirmation_max_age_bars` = 2 periods; older is
  `RETEST_CONFIRMED` with `confirmation_stale` (never published). Only the first touch
  of an episode is judged.

### 4. Entry, stop, target

With `ATR_c` at the confirmation candle `c`:

- **Entry band**: `v(c) +/- max(1 pip, 0.15 ATR_c)`. Price must not have closed below
  the band (`price_through_entry`) nor run more than 1.5 ATR above it
  (`entry_too_far`). A line's band is centred on the line at the **confirmation** candle.
- **Stop**: `min(retest_low, protected_structure) - max(1 pip, 0.25 ATR_c)`. It is never
  tightened or clamped: if the honest risk `(entry_high - stop)` exceeds
  `execution_stop_max_pips` the setup is refused (`risk_exceeds_execution_envelope`).
  A later trade through the stop, or a close back through the level, is
  `invalidated_after_confirmation`.
- **Target**: the nearest *credible opposing structure*: a confirmed pivot high that
  already existed **before the break began**, above everything price traded since the
  break and outside the broken zone, heading a swing of at least 1 ATR (the
  breakout's own highs are the move, not a barrier). It must leave
  `minimum_target_room_atr` = 0.55 ATR of room (`insufficient_target_room`), none at
  all is `no_opposing_structure`, and reward/risk must be at least 1.15
  (`reward_risk_below_minimum`). Reaching it first is `target_already_reached`. This is
  the strategy's own technical objective, separate from the Algo Bot's R ladder.

## Identity and expiry

- `StructuralID` / thesis: `technique:break_retest:<side>:<reference id>:<break start>`.
  The same accepted break is one thesis for as long as it is fresh.
- Setup key (hashed into the candidate ID):
  `break_retest:<side>:<reference id>:<break start>:<accepted at>:<confirmed at>`:
  stable for one event, distinct for a new break or a new confirmation.
- `FormedAt` = the acceptance candle, `CreatedAt` = the confirming candle,
  `ExpiresAt = CreatedAt + 4 h`.
- At most one candidate per direction per evaluation: two references broken in the
  same move are one trade (freshest confirmation, then stronger break displacement,
  then more touches, then reference id). The confluence floor is applied while choosing,
  so a best-ranked reference that fails it never hides a weaker sibling that passes; the
  others stay on record as `superseded_by_stronger_reference` or `confluence_below_floor`.

## Quality and arbitration

`Quality.Overall` is `published_overall_quality` = **0.75, exactly the value live
arbitration has always seen** for this strategy. The old value was a constant that
carried no information; v3 keeps it so that adopting v3 does not silently move any
arbitration ranking, and publishes the **measured** quality in `Quality.Components`:
`break_distance_atr`, `break_body_ratio`, `break_close_strength`,
`break_displacement_atr`, `break_accept_closes`, `retest_depth_atr`,
`retest_bars_after_accept`, `rejection_wick_ratio`, `rejection_close_strength`,
`reference_touches`, `target_room_atr`, `technical_rr`, `technical_risk_pips`,
`confluence_stars`. Whether `Overall` should become a function of them is an
arbitration decision this change does not take.

Evidence codes: `m5_key_level_break|m5_trendline_break`, `m5_key_level_retest|m5_trendline_retest`,
`m5_break_accepted`, `m5_retest_holds`, `m5_retest_rejection`, plus
`displacement_grade`, `htf_aligned`, `structural_agreement` **only when measured
true** (v2 attached the last two unconditionally).

The shared confluence rubric is scored from measured facts: HTF alignment from the
engine, `Touches` = the reference's real touch count, `WickRejection` from the
rejection candle, `DisplacementGrade` = break displacement `>= 1 ATR`,
`StructuralAgreement` = the engine's M5 structure bias agrees with the trade. The
Algo Bot's floor (`min_confluence = 2`) is applied here too; a refused retest is
recorded as `confluence_below_floor`.

## Valid and invalid, at a glance

| Case | Outcome |
|---|---|
| Level with 2+ clustered pivot-high touches, 2 strong closes beyond, pullback touches, bullish rejection within 3 candles | candidate |
| A single touch | no reference, no episode |
| One close beyond, then back | `FALSE_BREAKOUT / break_not_accepted` |
| Wick-heavy or small-body break | `FALSE_BREAKOUT / break_quality_insufficient` |
| Closes back below the level on or before the first touch | `FALSE_BREAKOUT / break_reclaimed_before_retest` |
| Touch candle is part of the acceptance run | not a retest (waiting) |
| Retest more than 24 periods after the acceptance | `EXPIRED / retest_window_expired` |
| Touch but no rejection within 3 candles | `EXPIRED / confirmation_window_expired` |
| Retest closes through the level | `RETEST_FAILED / retest_closed_through` |
| Protected structure traded | `STRUCTURE_INVALIDATED / protected_structure_lost` |
| Honest stop beyond the instrument cap | `RETEST_CONFIRMED / risk_exceeds_execution_envelope` |
| No / too-close opposing structure | `no_opposing_structure` / `insufficient_target_room` |
| Confirmation older than 2 periods (including after a feed gap) | `confirmation_stale` |
| A 70-candle-old break followed by an unrelated rejection | no episode (the old strategy published this) |

## Worked examples

### Fixture: BUY on a broken key level (XAU, pip 0.1, ATR about 2.8)

OHLC from `bullishLevel` in `internal/strategy/breakretest/fixtures_test.go`; every
number below is asserted by `TestPositiveBuyKeyLevel`.

| # | O | H | L | C | Role |
|---|---|---|---|---|---|
| 76 | 4116.00 | 4119.90 | 4115.50 | 4119.00 | pivot high 1: first resistance touch (4119.90) |
| 83 | 4118.50 | 4120.00 | 4118.00 | 4119.50 | pivot high 2: second touch (4120.00); confirmed by candle 85 (`FormedAt`) |
| 85 | 4117.00 | 4117.50 | 4115.00 | 4115.50 | pivot low 4115.00: the protected structure |
| 89 | 4118.50 | 4122.70 | 4118.40 | 4122.40 | first beyond-candle: body ratio 0.91, close strength 0.93 |
| 90 | 4122.40 | 4123.70 | 4122.20 | 4123.40 | second accepted close (`AcceptedAt`) |
| 91 | 4123.40 | 4123.60 | 4120.10 | 4121.00 | **touch** of the level (low 4120.10 <= 4119.95 + tol) |
| 92 | 4121.00 | 4122.90 | 4120.20 | 4122.60 | **rejection**: bullish, close strength 0.89, lower wick 0.30 of range (`ConfirmedAt`) |

Math (`ATR_c = 2.84`): level = (4119.90 + 4120.00) / 2 = **4119.95**; buffer =
max(1 pip, 0.05 x ATR) = 0.14, so closes 4122.40 and 4123.40 are beyond 4120.09;
displacement = (4123.40 - 4118.50) / ATR_b = 1.69; entry band = 4119.95 +/- max(0.1,
0.15 x 2.84) = **4119.52 - 4120.38**; stop = min(retest low 4120.10, protected 4115.00)
- max(0.1, 0.25 x 2.84) = **4114.29**; the nearest opposing swing high before the break,
above everything price traded since, is the early 4132.00 spike: target **4132.00**;
risk = 4120.38 - 4114.29 = 60.9 pips, reward/risk = 1.91.

### Real capture: BUY on XAU, 17-18 Sep (development capture, replayed bar by bar)

| UTC | O | H | L | C | Role |
|---|---|---|---|---|---|
| 17 Sep 23:20 | | 4347.56 | | | first resistance touch (anchor) |
| 17 Sep 23:55 | 4347.56 | 4348.16 | 4345.54 | 4346.35 | second touch; level = 4347.86; formed 00:05 |
| 18 Sep 00:10 | 4346.99 | 4354.91 | 4346.68 | 4354.34 | first beyond-candle (body 7.35 on a 8.23 range) |
| 18 Sep 00:15 | 4354.17 | 4355.24 | 4352.69 | 4352.97 | second accepted close |
| 18 Sep 01:40 | 4350.58 | 4350.72 | 4347.22 | 4347.26 | touch |
| 18 Sep 01:50 | 4348.24 | 4350.31 | 4344.00 | 4349.18 | rejection: close above the level, lower wick 0.67 of range |

Entry 4347.16 - 4348.56, protected structure 4343.27, stop 4342.10 (= 4343.27 - 1.17),
target 4365.64, risk 64.6 pips, reward/risk 2.64, ATR 4.68. Two touches, a break
worth 1.4 ATR beyond the level, a retest 17 candles after the acceptance (inside the
24 candle window).

## What v2 got wrong (reproduced by `test/brreplay/defects_test.go` on real engine contexts)

1. **Stale retest.** `techniquezone.FindRetest` returned the *first* retest after the
   latest break, however old, and the current candle only had to show *a* rejection.
   The setup carried the retest candle as its formation time: **42 of 59** replayed v2
   setups were published more than 2 candles after their retest candle (median 17
   candles, up to 712).
2. **Fixed quality.** `Quality.Overall = 0.75` with every component 1.0 on all 59
   candidates, whatever the break, retest or rejection looked like.
3. **Unconditional evidence.** `htf_aligned` and `structural_agreement` were attached
   to every candidate and the confluence factors `WickRejection`, `StructuralAgreement`
   (and `DisplacementGrade` on the trendline path) were hard-coded true; in the
   defect run 5 of 12 candidates carried `htf_aligned` while the engine read no
   alignment.
4. **Trendline-first.** The trendline path returned before the key-level path was
   ever evaluated, whichever setup was fresher or stronger (a code-order defect:
   in the two defect-run captures it never actually hid a key-level setup, 0 of 12).
5. **Weak breaks.** A break needed `breakout_accept_bars` closes beyond the level and
   nothing else: no buffer, no body, no displacement, no check that price had ever been on
   the other side.
6. **Trendline evaluated at the last candle.** The retest zone used the line's value
   at the *last* candle for a break found at another candle.
7. **No invalidation.** A break that failed and re-broke, a protected swing that was
   taken out, a retest window that had expired: none ended the thesis.
8. **Stop and target from a multiple.** The stop was `zone low - 0.25 ATR` (inside the
   retest zone) and the target a fixed 2R, whatever structure lay beyond.

## Replay evidence

Six committed real captures (`test/brreplay`, `BR_REPORT_DIR=/tmp/br go test
./test/brreplay -run Certification -v`), every M5 bar replayed through the production
engine (9,000 evaluations; the engine's decision equals the offline decision read
from the capture's candles on all 9,000: 0 mismatches). Roles were fixed before results
were read: the 14-21 Sep XAU capture was the only one used to sanity-check threshold
magnitudes ("development"); the other five are held out and chronologically separate.
No default was chosen on outcomes: the two geometry choices that matter (the
protected structure and the target lookback) were fixed from their structural meaning
and the alternatives are reported below.

Outcomes are HYPOTHETICAL under causal fill rules: the order is placed when the
observing candle closes and fills only on a later candle at the proximal edge, the fill
candle can only stop it out, the stop is checked before the target (a candle touching
both is flagged ambiguous and resolved as a stop; none occurred), and a filled position
is marked to the close after 8 hours. They are never realised performance.

| Capture | v2 setups (retest >2 candles old) | v3 breaks started | accepted | retest touched | retest confirmed | v3 published |
|---|---|---|---|---|---|---|
| XAU 14-21 Sep (development) | 8 (6) | 217 | 111 | 69 | 41 | 1 |
| XAU 28 Sep-6 Oct (held out) | 12 (5) | 239 | 103 | 72 | 43 | 2 |
| EURUSD 6 Oct | 12 (10) | 187 | 98 | 63 | 36 | 2 |
| GBPUSD 5 Oct | 12 (10) | 218 | 88 | 55 | 31 | 4 |
| GBPJPY 6 Oct | 4 (4) | 221 | 110 | 66 | 33 | 6 |
| USDJPY 5 Oct | 11 (7) | 238 | 119 | 80 | 37 | 7 |
| **Total** | **59 (42)** | **1,320** | **629** | **405** | **221** | **22** |

Where the 1,320 v3 episodes ended: break not accepted 383; weak break 198; retest
window expired 130; protected structure lost 126; retest closed through 102; no opposing
structure 88; closed back before the first retest 69; no rejection in the window 60;
reward/risk below the minimum 53; honest stop beyond the execution cap 45; no protected
structure 12; too little room 5; invalidated after confirmation 4; target already reached
4; published 22; still in progress when the capture ended 19.

Hypothetical outcomes (filled orders only):

| | n (filled) | target / stop | mean R | mean MFE / MAE | median planned R:R | median zone | median risk |
|---|---|---|---|---|---|---|---|
| v2, all | 59 (55) | 13 / 42 | -0.29 | 2.20R / 1.64R | 2.00 | 0.28 ATR | 3.8 pips (XAU 15.5) |
| v3, all | 22 (18) | 3 / 15 | -0.57 | 0.99R / 1.11R | 1.73 | 0.48 ATR | 8.1 pips |
| v2, held out | 51 (47) | 12 / 35 | -0.23 | 1.74R / 1.55R | 2.00 | 0.33 ATR | 3.5 pips |
| v3, held out | 21 (17) | 2 / 15 | -0.73 | 0.86R / 1.17R | 1.59 | 0.48 ATR | 7.6 pips |
| v3, XAU | 3 (3) | 1 / 2 | +0.09 | 1.30R / 0.89R | 2.26 | 0.30 ATR | 52-63 pips |
| v3, FX | 19 (15) | 2 / 13 | -0.70 | 0.92R / 1.16R | 1.59 | 0.49 ATR | 7.2 pips |

**What this does and does not show.** v3 publishes well under half as many setups as v2
and each is a structurally real, fresh, accepted-break retest with a stop beyond real
structure; none has a stale retest, a hard-coded factor or a stop inside the retest.
It does **not** show that v3 earns more: on these captures its hypothetical outcome is
*worse* than v2's (held-out mean R -0.73 over 17 fills against -0.23 over 47), both are
negative, the samples are tiny (1 to 7 v3 setups per capture), the captures are one or two
weeks of one market regime, and the proxy ignores the Algo Bot's execution rules. **No
profitability or improvement claim is made, and the held-out proxy is a reason to review
the strategy's live enablement before this ships**; enabling or disabling a strategy is
the owner's decision and this change does not take it.

Alternatives measured (`BR_FUNNEL=1 go test ./test/brreplay -run Funnel -v`; offline,
distinct episodes reaching a publishable state, **before** the confluence floor, in the
capture order above): the production configuration 1+2+2+4+7+8 = 24 (22 after the
floor); `protected_structure: break_origin` 49 (its stop is the launch candle's low, a
weaker structure; 60 with the cap removed); no execution cap 29; one accepted close
instead of two 27, three closes 21; three level touches 13; no break-force gates 39; a
288 candle target lookback 24; a 0.5 ATR target swing 24; a five candle confirmation
window 29. The defaults are the structurally conservative choice in each case, not the
one with the best outcome. With the production protected structure the honest XAU risk is
52-63 pips (all three published XAU setups fit the 70 pip cap) and 45 episodes over the
six captures were refused for exceeding the cap.

## Intentional differences from Python parity

The frozen oracle golden (`testdata/detector-parity-oracle.json`) is **not edited**;
`break_retest` leaves the covered list of `test/detectorparity` because it no longer
reproduces the Python detector, and the v2 decisions stay reproducible as the frozen
baseline in `test/brreplay`. Differences, all deliberate:

| Python / v2 | v3 |
|---|---|
| Direction from the engine's structure bias, premium/discount and chop gates | Direction is the side of the break; HTF alignment and structure bias are *measured evidence* and confluence factors, not gates. Chop and premium/discount are not consulted: a breakout out of a range is the setup |
| Canonical engine trendlines/levels built from the whole window | The strategy's own references from pivots confirmed before the break, with stable ids, formation and confirmation times and a pre-break touch count |
| A close beyond = broken; `breakout_accept_bars` consecutive closes | k closes beyond by a buffer, first candle with measured body, close strength and displacement, price proven to have been on the other side |
| First retest after the latest break, at any age | Only the first touch strictly after the acceptance, inside 24 periods, rejected within 3 candles, fresh for 2 |
| Trendline at the last candle | Line value at each candle's own index |
| Stop `zone low - 0.25 ATR`; target 2R | Stop beyond the retest and protected structure; target the nearest credible opposing structure |
| Constant quality; hard-coded confluence factors | Quality.Overall unchanged; measured components; measured confluence factors |
| `trendline_tolerance_atr`, `momentum_body_fraction`, `strict_premium_discount`, `target_r` | Removed from the configuration (replaced by the parameters above) |

## Tests executed

Commands and outcomes are recorded in the pull request. The Break & Retest tests are:

- `internal/strategy/breakretest`: fixtures that self-check their pivots, positive
  BUY/SELL/trendline/FX-scale setups with every number asserted from OHLC, 17 negative
  fixtures with the exact state and reason of each, a single touch, the old-break
  (stale retest) case, the acceptance-candle-is-not-the-retest case, a feed gap,
  malformed candles, too little history, the lifecycle prefix by prefix, identity,
  configuration fail-closed, the adapter contract; and four property tests: prefix
  invariance with the future replaced by garbage (4,293 evaluations, 79 setups),
  sufficiency of the history window, BUY/SELL mirror symmetry, and the technical
  invariants of every published setup (102 setups).
- `test/brreplay`: prefix invariance and no future leak on every bar of the six real
  captures, the technical invariants on every real setup, the v3 golden
  (`testdata/break-retest-v3-detect-golden.json`), the reproduction of the v2 defects
  on real engine contexts, and the replay certification above.
- Existing suites that cover the wiring: `test/engine`, `test/strategy`,
  `test/architecture` (rank table, no cycles), `internal/strategy` (independence).

## Independent certification

A reviewer who did not write the detector audited the committed code in a separate
worktree against this page, wrote adversarial and fuzz tests (about 44,000 evaluations
and 1,100 setups over mutated and reflected fixtures) and ran them. Their findings, and
what was done (each has a regression test in `negative_test.go`):

| # | Finding | Severity | Resolution |
|---|---|---|---|
| 1 | The k accepted closes were counted by slice index, so a feed gap inside the run still counted as "consecutive" (13 hits in the fuzz) | Medium-high | Fixed: the run must follow one period per candle, otherwise `break_not_accepted` |
| 2 | Trading below the protected structure between its pivot and the retest loop (the base, the break run) was never checked, so a setup could publish with a stop above an already-traded low (12 hits) | Medium | Fixed: any lower low after the pivot, through the acceptance, is `protected_structure_lost` |
| 3 | The cluster tolerance and break buffer used the ATR *including the break candle*, so the break candle's own wick decided whether a level existed | Low-medium | Fixed: both use the ATR as of the candle before the break |
| 4 | A candle that first touches the level and closes through it ended as `FALSE_BREAKOUT`, while this page said `RETEST_FAILED` | Low | Page corrected: on the first touch the break never held; `RETEST_FAILED` follows an earlier touch |
| 5 | A line was evaluated by slice index across a gap between the second anchor and the break | Low | Fixed: such a line is not a reference |
| 6 | `REFERENCE_VALID` was declared but never emitted | Low | Documented: a valid unbroken reference is not an episode |
| 7 | The one-per-direction choice happened before the confluence floor, so a failing best reference hid a weaker sibling that passes | Low | Fixed: the floor is applied while choosing |

What the audit tried and could not break: Wilder ATR against an independent
implementation, the buffer and pivot-confirmation boundaries (a pivot confirmed on the
break candle is excluded), 311 setups exactly mirrored over 400 reflected series with
identical states and reasons, no duplicate episode keys, no vanishing episodes and no
regressing or changing terminal states as evaluation advanced, no published setup that
was stale, over the cap, mis-ordered, retested at or before the acceptance, outside its
windows or hit through its stop, `risk - 1e-6` refused and `risk == cap` accepted with an
unchanged stop, retests 30 periods late expired, no dependence on loaded history, and no
import of another strategy package. The auditor also noted that the in-package prefix
tests are weaker than they look (`DetectAsOf` slices before the garbage is applied); the
real-capture test and the cross-evaluation stability fuzz are the stronger checks.

Verdict after the fixes (the auditor's blocking findings 1 to 3 are resolved):
**certifiable as a technical contract**, with the unresolved risks below. This is not a
statement about profitability.

## Unresolved risks

- **Outcomes.** The hypothetical outcome of v3 on the captures is not better than v2's
  (see the table above) and the samples are tiny. Reviewing the live enablement of
  `break_retest` before this ships is the owner's decision; nothing here changes it.
- **No target, no setup.** 88 of 221 confirmed retests ended in `no_opposing_structure`:
  a breakout to fresh highs has no swing above it to target. That is the contract's
  honesty, but it removes the cleanest continuation breakouts. A measured-move
  objective would be a separate, evidence-backed decision.
- **XAU envelope.** With the structural protected stop the honest XAU risk is 52-63
  pips on the three published setups and 45 episodes over the captures were refused for
  exceeding the cap.
- **Trendline entry drift.** A line's entry band is centred on the line at the
  confirmation candle and does not follow the line afterwards; `line_max_slope_atr`
  bounds the drift.
- **Defaults are uncalibrated judgements** (every threshold is a parameter); the
  protected-structure model and the target lookback were chosen for their structural
  meaning, not their outcomes.
- **`published_overall_quality`** is a configurable constant (default 0.75); changing it
  is an arbitration change.
- **A missing `execution_stop_max_pips` disables the cap pre-check** (the engine
  injects the resolved instrument cap; the Algo Bot still rejects an over-envelope stop).
- **A duplicate timestamp anywhere in the loaded window fails the evaluation closed.**

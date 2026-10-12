# Session Level (v4)

`session_level` trades a reaction off a **completed** reference level: the Asia, London
or New York high or low, the previous trading day's high or low, or the previous
trading week's. v4 replaces the frozen-Python-parity v3 with a Go contract that builds
its own levels from the candles of a period that has really finished, judges two
reaction models inside the one strategy (a qualified first-touch rejection and a
liquidity sweep with a reclaim), and sets the stop and target from the reaction's own
extreme and from real opposing liquidity. This page is the contract: the definitions,
the mathematics, the exact code behaviour and every intentional difference from the
Python detector.

- **Owner**: Go Analysis Engine, package `internal/strategy/sessionlevel`. No Python
  scanner or second decision exists; the Algo Bot only applies execution policy.
- **Independence**: no strategy-family base, no import of another strategy package
  (`internal/strategy/independence_test.go`). It reads closed M5 candles (and closed H1
  candles for the previous day and week) plus the engine's HTF alignment, M5 structure
  bias and confluence rubric as *measured evidence*. It does not read the engine
  frame's session references (`LegacyFrame.Sessions`), which carry no period.
- **Contract version**: `v4`. `Quality.Overall` keeps the scale live arbitration has
  always seen (confluence stars out of three); the measured quality is in
  `Quality.Components` ([Quality](#quality-and-arbitration)).

## Status and execution

- **Certification**: `GO_NATIVE_VALIDATED` ([matrix](README.md#certification)): the
  technical contract below is certified by tests and replays and is deliberately **not**
  parity with the Python detector. It makes no profitability claim.
- **Symbols**: all instruments. The session hours and the daily rollover come from
  `analysis.sessions`, the pip size and the execution stop cap
  (`execution_stop_max_pips`) from the instrument configuration
  (`applySessionLevelGeometry`).
- **Execution**: unchanged by this document. The Algo Bot's `session_level` scope needs a
  confirmed reaction and a `session_level_` evidence code; both are carried. On XAU the
  strategy stays observe-only until the separate enablement change
  (`config/instruments.yml`); on FX it trades, so **this contract replaces the FX
  Session Level decision too** (see [FX behaviour change](#fx-behaviour-change)).
- **Not deployed**: nothing here enables or disables a strategy.

## Time and price conventions

- Every timestamp is a candle **open** time (Unix seconds); an M5 candle closes 300 s
  later, an H1 candle 3,600 s later. Ages and windows are counted in M5 periods from open
  times (`(t_b - t_a) / 300`), never from slice positions, so a feed gap ages a setup
  instead of hiding it.
- All times are **UTC**. Nothing depends on a local clock or a daylight-saving rule.
- **ATR** is Wilder ATR(14) over a *fixed trailing window* of 140 M5 candles ending at the
  candle the value is read for. Every threshold a candle is judged against uses the ATR as
  of the candle **before** it, so its own range can never decide whether it was far
  enough; geometry uses the ATR at the confirming candle.
- A **distance** is `max(pips x pip_size, ATR multiple)`; prices are never compared in
  another instrument's pips.
- The detector is written once, for a BUY off a support, and the SELL is its exact mirror
  (prices reflected about zero, decided, mapped back): `TestMirrorSymmetry`,
  `TestBearishTwinsAreSellsOffTheHigh`.
- Everything is evaluated **as of the last closed M5 candle** and reads only candles up to
  it, the H1 candles that had already closed at that moment, and at most the trailing
  `requiredHistory` / `requiredHigherHistory` of them
  (`TestPrefixInvarianceAndNoFutureLeak`, `TestPrefixInvarianceWithHourlyCandles`,
  `TestRequiredHistoryIsSufficientAndOlderCandlesDoNotMatter`).

## Levels

A level exists only when the period that defines it has **completed** and its candles
really cover it. A forming session never yields a level, not even a provisional one.

| Name | Defining period (UTC) | Source | Identity | Superseded by |
|---|---|---|---|---|
| `ASIA_H` / `ASIA_L` | `asia_start` (22:00) to `london_start` (07:00), crossing midnight | M5 | `level:<NAME>:<periodStartUnix>` | the next completed Asia |
| `LONDON_H` / `LONDON_L` | `london_start` (07:00) to `ny_start` (13:00) | M5 | same | the next completed London |
| `NY_H` / `NY_L` | `ny_start` (13:00) to `asia_start` (22:00) | M5 | same | the next completed New York |
| `PDH` / `PDL` | the previous **trading day**: rollover hour (21:00) of the day before to the rollover hour | H1 | same | the next trading day |
| `PWH` / `PWL` | the previous **trading week**: the rollover hour before Monday's label to the rollover hour on Friday's label | H1 | same | the next trading week |

- **Trading day.** A candle opening at or after `daily_rollover_utc_hour` belongs to the
  *following* calendar date's trading day, so Sunday evening's first candles are
  Monday's and the day turns over at exactly 21:00 (`TestTheTradingDayTurnsOverAtTheRolloverHour`,
  `TestOnAMondayThePreviousTradingDayIsFridayAndSundayEveningIsMondays`). The week is the
  ISO week of that trading-day label.
- **Completeness** (`periodExtremes`). The first candle must open within the start
  tolerance of the period start, the last candle must close within the end tolerance of
  the period end, no hole inside may exceed the maximum gap, and the candles must fill at
  least the minimum coverage. The tolerances absorb the daily 21:00-22:00 UTC break and the
  weekend open of the real feed (sessions: 90 / 90 / 60 min and 0.80; day and week:
  120 / 120 / 150 min and 0.85); anything shorter is refused with its reason
  (`period_start_not_covered`, `period_end_not_covered`, `feed_gap_inside_period`,
  `period_coverage_too_low`, `no_candles_in_period`) and recorded in `Analysis.Rejected`.
- **Latest occurrence only.** Of each name only the most recent completed occurrence is a
  level. An incomplete newer occurrence is **never** replaced by an older one
  (`TestOnlyTheLatestCompletedOccurrenceIsALevelAndNeverAFallback`); a level older than
  `session_max_age_hours` (30), `day_max_age_hours` (72, which spans a weekend) or
  `week_max_age_hours` (168) is refused as `level_too_old`.
- **Provenance** published on every level: price, kind, side (a high is resistance, a low
  support), the exact period start and end, the source timeframe, the open time of the
  candle that made the extreme (`FormedAt`), the moment it became actionable
  (`AvailableAt` = period end), the candle count and the coverage, and `ReachedAt` (the first
  candle after availability that traded to the price, used to decide whether an *opposing*
  level is still liquidity to aim at).
- **History.** A level whose availability precedes the first M5 candle the detector reads is
  `known = false`: the candles cannot show what touched it since, so **no touch count is
  claimed** and no first touch is judged on it (`level_history_incomplete`); a sweep and
  reclaim, which is an event, is still judged.

## The two reaction models

Both models start at a **touch**: a candle with `low <= level + touch_tolerance`, where
`touch_tolerance = max(2 pips, 0.10 ATR)`, at or after the level's availability. Touches
are counted per level: a new touch counts only after price has closed at least
`approach_min_distance_atr` (0.5 ATR) above the level again and `touch_separation_bars`
(3) have passed, so one dwell at the level is one touch. A touch is judged only if price
**approached** it: some close in the last `approach_lookback_bars` (24) was at least
0.5 ATR above the level (else `no_fresh_approach`: a dwell is not a return).

```
LEVEL_AVAILABLE -> TOUCHED -> REACTION_CONFIRMED -> CANDIDATE            (model A)
                -> SWEPT -> RECLAIM_WAITING -> REACTION_CONFIRMED -> CANDIDATE   (model B)
terminal: BREAKOUT_ACCEPTED | REACTION_FAILED | EXPIRED | ESCALATED_TO_SWEEP
```

### Model A: qualified first-touch rejection (`first_touch_rejection`)

For a BUY at support `L` (all prices below are in this orientation):

- **Fresh**: the level has not been swept since it became available
  (`level_already_swept`), had at most `maximum_prior_touches` (1) earlier touches
  (`level_touched_before`), and its touch history is known.
- **Touch** within tolerance and not through the level by more than the sweep threshold
  (that is a sweep: the attempt `ESCALATED_TO_SWEEP` and model B takes the same touch).
- **Confirmation** within `confirmation_window_bars` (3: the touch candle and the two after
  it) by a bullish candle that **closes at or above** `L + hold_buffer`
  (`max(1 pip, 0.05 ATR)`), in the top 60 % of its range, with a lower wick of at least
  25 % of the range **or** a body of at least 60 % of it. Failing that the attempt is
  `confirmation_window_expired`; a close below `L - fail_buffer` (`max(3 pips, 0.25 ATR)`)
  is `REACTION_FAILED / closed_through_the_level`.

### Model B: liquidity sweep and reclaim (`sweep_reclaim`)

- **Sweep**: a candle with `low < L - sweep_threshold`, `sweep_threshold = max(3 pips,
  0.10 ATR)`, after the level became available, on a touch that was approached
  from above. The sweep is an *event with a time and an extreme*, not a flag on the level.
- **Reclaim**: within `reclaim_max_bars` (6) of the sweep candle (the sweep candle itself
  counts, which is the single-candle stop-run), a candle that is itself the rejection
  above, closing at or above `L + reclaim_buffer` (`max(1 pip, 0.05 ATR)`). A reclaim after
  the window is `reclaim_window_expired`; a rejection candle that is not after a sweep is
  not a reclaim of anything.
- **A sustained breakout is not a reversal**: `breakout_accept_closes` (2) consecutive
  closes below `L - breakout_buffer` (`max(2 pips, 0.15 ATR)`) is `BREAKOUT_ACCEPTED`, and a
  strong candle that later closes back above the level is not a reclaim
  (`TestASustainedBreakoutIsNotAReversal`).
- At most `max_episodes_per_level` (3) touches of one level are judged
  (`episode_limit_per_level`).

### After the confirmation

The reaction is published only while it is fresh and unbroken: no later candle took out
the stop or closed below `L - fail_buffer` (`invalidated_after_confirmation`), the target
has not been reached (`target_already_reached`), the confirmation is at most
`confirmation_max_age_bars` (2) old (`confirmation_stale`), and the last close is neither
through the entry (`price_through_entry`) nor more than `maximum_entry_distance_atr`
(1.5 ATR) above it (`entry_too_far`).

## Entry, stop, target

```
extreme = lowest low from the touch to the confirmation          (the sweep wick included)
stop    = min(L, extreme) - max(pip, invalidation_buffer_atr x ATR_c)        (0.25 ATR)
entry   = [ L , L + max(entry_band_atr x ATR_c, entry_tolerance) ]
risk    = entry_high - stop
```

- The stop is beyond the level **and** beyond everything price traded to in the reaction. It
  is **never tightened** inside a sweep wick; a stop beyond the instrument's execution cap
  is refused (`risk_exceeds_execution_envelope`), not clipped, and the risk is never
  expanded (`TestGeometryLimitsAreHonestAndNeverTightened`).
- The target is the **nearest credible opposing liquidity**: a confirmed M5 pivot high
  (confirmed by the time the reaction was, so a later pivot cannot move a published
  target) above everything price traded since the touch and above the entry, heading a swing
  of at least `target_swing_atr` (1.0 ATR); or an opposing reference level that is
  available and that price has not reached. The nearest decides and no target is
  manufactured: `no_opposing_structure` / `insufficient_target_room` (room under
  `minimum_target_room_atr` 0.55) are refusals, as is `reward_risk_below_minimum` (1.15).

## Identity and expiry

- **Level id**: `level:<NAME>:<periodStartUnix>`, stable while the level is current and
  different for every occurrence.
- **Thesis** (`StructuralID`): `technique:session_level:<side>:<levelId>:<model>:<episodeAt>`,
  where `episodeAt` is the touch (model A) or the sweep (model B). A later confirmation of
  the same reaction is the same thesis; a later touch of the same level is a **different**
  one.
- `FormedAt` is the level's availability, `CreatedAt` the confirming candle. Expiry is
  `expiry_hours` (4) after the confirmation (v3 used 24). The confirmed reaction carries
  the thesis as `ZoneID`, the touch (or sweep) and confirmation candles, and a pattern the
  Algo Bot already knows (`wick_rejection` / `strong_reclaim` for model A, `sweep_reclaim`
  for model B).

## Quality and arbitration

`Quality.Overall = stars / 3`, exactly the scale v3 published; only the *inputs* are now
measured:

| Factor | Measured as |
|---|---|
| HTF aligned | the engine's HTF read agrees with the direction |
| Touches | the level's **actual** touches (prior + this one) when the history shows them; **0** when it cannot (the v3 hard-coded 2 is gone) |
| Wick rejection | the confirming candle's lower wick meets `rejection_wick_ratio` |
| Displacement grade | confirming body `/ ATR >= displacement_grade_atr` (1.0) |
| Structural agreement | the engine's M5 structure is up (BUY) / down (SELL) |
| Session context | always true: Asia, London and NY partition the UTC day |

Published `Quality.Components`: `level_type` (1 session, 2 previous day, 3 previous week),
`level_age_bars`, `period_completeness`, `rejection_strength`, `rejection_wick_ratio`,
`reclaim_depth_atr`, `reaction_freshness` (periods since the confirmation),
`bars_to_confirm`, `target_room_r`, `target_room_atr`, `level_integrity` (the share of
closes since availability that held the right side of the level), `technical_risk_pips`,
`confluence_stars`; model B adds `sweep_depth_atr`, model A `touch_penetration_atr`;
`actual_level_touches` only when the history shows it; `structure_alignment` only when the
engine has a structural read. A fact that does not apply or cannot be measured is **left
out, never zeroed or replaced by a neutral value**. When two levels react together in one
direction the strategy keeps one, ranked by the freshest confirmation, then `target_room_r`,
then the rejection strength, then the significance of the level (week > day > session), then
the level id; the engine's arbitration is untouched.

## Valid and invalid, at a glance

| Fact | Result |
|---|---|
| London still forming (even one hour from its end) | no London level |
| A session whose candles leave a hole over 60 min / miss the start or end / cover under 80 % | no level, with the reason |
| Yesterday's London standing in for an incomplete today's | never |
| Price dwelling at the level, no close 0.5 ATR above it in 24 candles | `no_fresh_approach` |
| Level swept since it became available, then a plain touch | `level_already_swept` (a sweep and reclaim is model B) |
| Second or later touch beyond `maximum_prior_touches` | `level_touched_before` |
| Touch history not reaching the level's availability | `level_history_incomplete`, no count claimed |
| Touch, no rejection inside 3 candles | `confirmation_window_expired` |
| Weak close, or a wick/body-less candle, or a close below the level | no confirmation |
| Sweep, two closes beyond the breakout buffer | `breakout_accepted` |
| Sweep, reclaim after 6 candles | `reclaim_window_expired` |
| Reclaim closing inside the reclaim buffer | not a reclaim |
| Confirmation older than 2 candles | `confirmation_stale` |
| Stop beyond the instrument cap | `risk_exceeds_execution_envelope`, never tightened |
| No opposing liquidity / too close / RR under 1.15 | refused |
| Malformed series (NaN, `high < low`, non-increasing time) | nothing |

## What v3 got wrong (reproduced by `test/slreplay/defects_test.go` on real engine contexts)

The frozen v3 (kept in `test/slreplay/baseline_test.go`, never imported by production)
published 139 setups on three immutable captures (XAU 14-21 Sep, XAU 1-12 Oct, GBPJPY
6 Oct). Each was measured against the v4 contract by an **independent audit**
(`test/slreplay/audit_test.go`, written from this page and sharing no code with the
detector):

| Weakness named in the brief | Measured on the 139 | Notes |
|---|---|---|
| The touch count was hard-coded to 2 (`ReactionFactors(..., 2)`) | the level really had 0 touches in 21, 1 in 18, **2 in only 26**, 3 or more in 64, and could not be counted in 10 | 113 of 139 differ from the claimed 2 |
| The level was chosen by proximity / stars from the engine frame and never checked for being the current occurrence | 28 setups traded a level a newer occurrence of the same name had superseded; 40 a level whose defining period v4 refuses as incomplete (no candles or a start not covered) | 68 of 139 not current |
| `LegacyFrame.Sessions` has no timestamps | `techniquezone.SessionRef` is `{Name, Price, Swept}` (`TestV3SessionReferenceCarriesNoTimestamps`) | nothing downstream could tell a complete level from a partial one, or when it formed |
| Levels came from a short window and could be days old | 31 setups traded a level older than 24 h | a 3.4-day-old New York low and a 4-day-old Asia low among the XAU losers |
| A swept level qualified on a generic reclaim not tied to the sweep | **0 of 139 on these captures**: every swept-level setup had a sweep within the reclaim window before it | **not reproduced as a cost.** The link was never verified (a structural gap v4 closes by construction); the data does not show it costing a trade, and this page does not claim it did |

Other v3 behaviour that is gone: the reaction band of `+-0.5 ATR` around the level (a
"reaction" 2 dollars from the level on XAU qualified; v4 requires a touch within
`max(2 pips, 0.10 ATR)`), the `session_level_unswept` evidence code, a 24 h expiry, and
the best-by-stars choice that let the list order decide.

### The five losing XAU trades (2-6 Oct 2026)

Replayed through `DetectAsOf` with exactly the data that existed at each plan's candle
(`test/slreplay/trades_test.go`, `TestTheFiveLosingXAUSessionTradesAreAccountedFor`; plans and
fills read read-only from production on 12 Oct). v4 publishes **none** of them:

| Trade (UTC) | Level and its age | What the candles show | v4 |
|---|---|---|---|
| 2 Oct 05:20 SELL NY_H (-37 pips) | `NY_H` 4186.18, 11.8 h, complete and current | price had traded 1.6 ATR **through** it; v4 recorded a sweep at 04:10 that was `BREAKOUT_ACCEPTED`, then a plain touch of a swept level | refused: sustained breakout, swept level |
| 5 Oct 00:15 BUY NY_L (-36) | `NY_L` 4150.07, **81 h** old | the weekend had no New York session, so there is no current `NY_L`; v3 reached back to Thursday's | refused: level not current (`no_candles_in_period`) |
| 6 Oct 00:55 BUY LONDON_L (-42) | `LONDON_L` 4149.44, 17.9 h | price had broken it hours earlier and been **4.8 ATR below**; this was a rally into the broken level from beneath, no approach from above and no live episode | refused: broken level, no episode |
| 6 Oct 00:20 BUY ASIA_L (-43) | `ASIA_L` 4133.79, **95 h** old (Thursday's) | price swept 0.27 ATR below it at 00:05 and was back above it by 00:10: a sweep and reclaim of a four-day-old low, superseded by the completed Asia session of 4-5 Oct | refused: level not current (`superseded`) |
| 6 Oct 05:00 BUY PDL (-46) | `PDL` 4123.15, 13.7 h, complete and current | price had swept it at 02:30 (`BREAKOUT_ACCEPTED`) and was at the level again after it was swept | refused: sustained breakout, swept level |

Other facts the replay pins for all five: each position filled **after** its confirmation
(none entered early) and none reached **+1R** before its stop (best excursion 0.27R to
0.88R). Two of the stopped trades (ASIA_L and PDL) were followed by a large move in their
direction (5.5R and 12.8R within eight hours of the fill, after the stop had been hit);
refusing them is correct under this contract and says nothing about whether a wider stop
would have paid.
The ASIA_L case is a genuine sweep and reclaim of a stale level, refused by the
recency rule: whether older levels deserve a place is a policy question this contract
answers "no" to and does not claim to have settled.

## Replay evidence

Hypothetical outcomes under causal fill rules (`test/slreplay/outcome_test.go`): the order
is placed when the observing candle closes, fills only on a **later** candle at the
proximal edge, the fill candle can only stop the position out, the stop wins a same-candle
tie, 8 hours maximum, a single target. Never realised performance; broker outcomes that
do not exist are not counted as wins. Reproduce with
`SL_REPORT_DIR=/tmp/sl go test ./test/slreplay -run Certification`. The new
`replay-xau-production-capture-20261012.json` (M5 1 Oct 01:55 to 12 Oct 07:30, M15, H1; read
from production Redis read-only on 12 Oct) was taken **after** the design was fixed: its
candles from 6 Oct 06:00 are the out-of-sample XAU window.

| Capture [role] | v3 setups (mean R, filled) | v4 setups (mean R, filled) | v3 setups v4 also publishes |
|---|---|---|---|
| XAU 14-21 Sep [development] | 48 (+0.14, 48) | 6 (-0.53, 5) | 2 |
| XAU 28 Sep-6 Oct [incident] | 38 (+0.21, 38) | 3 (-1.00, 1) | 0 |
| XAU 1-12 Oct [overlaps the incident; out-of-sample from 6 Oct 06:00] | 47 (-0.10, 46) | 11 (-0.70, 9) | 3 |
| EURUSD 6 Oct [held-out] | 38 (-0.20, 37) | 7 (-0.20, 7) | 1 |
| GBPUSD 5 Oct [held-out] | 44 (-0.38, 42) | 6 (-0.04, 6) | 2 |
| GBPJPY 6 Oct [held-out] | 44 (-0.50, 44) | 8 (-0.06, 7) | 1 |
| USDJPY 5 Oct [held-out] | 38 (+0.97, 33) | 6 (+1.63, 5) | 6 |

Pooled with 95 % seeded bootstrap intervals: development v3 +0.14 [-0.30, 0.59] (48) vs v4
-0.53 [-1.00, 0.42] (5); incident v3 +0.21 [-0.21, 0.65] (38) vs v4 -1.00 (1); the four FX
captures v3 -0.09 [-0.34, 0.21] (156) vs v4 +0.25 [-0.32, 0.87] (25); **post-incident XAU
(out of sample) v3 -0.18 [-0.62, 0.30] (26) vs v4 -0.61 [-1.00, 0.17] (7).**

**Reading.** v4 is far more selective (about 84 % fewer setups on the FX captures, 77 to
92 % on the XAU ones) and every published setup meets its technical contract (on the 25
setups of the three audited captures the independent audit finds the level current and
complete, the reclaim tied to a sweep and the touch count equal to the one counted from
the candles, in all of them). But **no interval excludes
zero, the v4 samples are 1 to 9 filled setups per XAU capture, and on XAU every window
leans negative** (development -0.53, incident -1.00, post-incident -0.61; 14 distinct
XAU fills in all). The simulator is not the executor (the live XAU session-level trades
lost 0 of 5 while the simulation of v3 on the same window averages +0.21 R, so simulated R
must not be read as live expectancy), and the defective-versus-clean split does not
support "the v3 defects caused the losses" either: on the incident window the v3 setups
that failed the v4 contract averaged +0.92 R (n=14) and the clean ones -0.20 (n=24). The
honest conclusion is that technical correctness and causality are **proven**, and that any
profitability claim, in either direction, is **unproven**.

How the v4 funnel behaves (distinct episodes, all seven captures; the XAU incident window
is contained in the extended capture): of 1,355 episodes only 189 reach a confirmed
reaction and 47 are published. The terminal reasons are
dominated by `level_already_swept` (the level had been traded through and was then merely
touched), `episode_limit_per_level`, `breakout_accepted`, `risk_exceeds_execution_envelope`
(an honest stop beyond a sweep wick over the cap: 10 to 12 on each XAU capture) and
`reward_risk_below_minimum`.

## FX behaviour change

Session Level trades on FX today (live: EURUSD +48, GBPJPY +67, USDJPY -17 pips). Replacing
it changes live FX behaviour, not only XAU: on the four FX captures v3 published 164 setups
and v4 publishes 27. Both are small-sample, hypothetical results; the held-out check does
not show v4 earning more (mean R +0.25 [-0.32, 0.87], n=25 filled) nor v3 earning more
(-0.09 [-0.34, 0.21], n=156). Treat merging this contract as a deliberate decision about FX
Session Level frequency, not as a no-op.

## Intentional differences from Python parity

| v3 / Python | v4 |
|---|---|
| Levels from the engine frame (`session.Update` over the last ~150 candles), no period, no timestamps | Levels built from the candles of a completed, covered period with exact start, end, availability and identity |
| Any occurrence the frame happened to hold, nearest to price first | Latest completed occurrence only; an incomplete newer one is never replaced by an older one |
| Reaction anywhere in `+-0.5 ATR` of the level | A touch within `max(2 pips, 0.10 ATR)` after a fresh approach |
| Touch count hard-coded to 2 | Counted from the candles; absent when the history cannot show it |
| Swept level valid on any reclaim-type confirmation | A sweep is an event; the reclaim is the rejection candle within 6 candles after it; two closes beyond the buffer end it |
| Confirmation from the shared reaction detector, any age | A bounded window (3) and a bounded age (2) bound to the touch or sweep |
| Entry `level +- 0.05 ATR`... | Entry `[L, L + max(0.5 ATR, tolerance)]` |
| Stop: level + 0.5 ATR | Beyond `min(L, extreme)` (the sweep wick included) by 0.25 ATR; refused over the cap, never tightened |
| Target: nearest liquidity pool at 1 ATR | Nearest confirmed swing or unreached opposing level with room; no manufactured target |
| Best-scored level (list order on a tie) | One reaction per direction, ranked by evidence |
| Expiry 24 h | 4 h |

The frozen Python oracle goldens (`test/legacyparity`) are untouched. The `session_level_reaction`
detector there is now **measured only** (its decisions are still compared and logged,
no longer asserted): v4 deliberately no longer reproduces it.

## Tests executed

- `internal/strategy/sessionlevel`: 62 tests (78 with subtests; 94.8 % statement
  coverage): fixtures for a completed Asia/London/NY level and for the previous day and week
  (UTC day and week rollover, Sunday open, the 21:00 break, the rollover candle), period
  completeness and feed holes, first-touch rejection and sweep and reclaim for BUY and (by
  reflection) SELL, negative scenarios, each changing one fact of the positive one (no
  approach, window, weak close, threshold, sustained breakout, reclaim window, shallow reclaim,
  swept level, prior touches, touches before availability, stale, invalidated, target reached,
  price left the entry, envelope never tightened, malformed candles), exact boundary tests,
  randomized prefix invariance over 6,000+ evaluations (price and hourly candles), history
  sufficiency, BUY/SELL mirror symmetry, a published-setup invariant property test and
  determinism. Mutation checked: 44 deliberate rule changes, 40 make a test fail; the four
  survivors are equivalent (three redundant `end > now` / `>= current` guards in the
  previous-day and previous-week search that the later `end > now` check already enforces,
  and an unreachable degenerate-stop guard: the stop is always below the level by
  construction).
- `test/slreplay`: defect reproduction with the independent audit, the five losing trades,
  engine-vs-offline agreement (0 mismatches over 11,000 evaluations), no future dependence
  bar by bar on all seven captures (price and hourly), technical invariants of every
  setup, a committed golden (`testdata/session-level-v4-detect-golden.json`), and synthetic
  paths through the real engine (published once at the confirmation, the SELL twin, nine
  refusals).
- `test/strategy`, `test/engine`, `test/legacyparity`: the v3 session fixtures were replaced
  by v4 contract edges; the strategy-config test expects `session_level` v4; the frozen
  detector is measured only.
- The XAU replay meta (`replay-go-xau-20260921.meta.json`) was regenerated: Session Level
  opportunities changed from the v3 count to the v4 count.

## Unresolved risks

- **No profitability claim, and on XAU the hypothetical evidence leans negative** (above).
  This contract does not make XAU Session Level worth enabling; it makes it correct.
- **FX is live** and changes with this contract.
- Thresholds are hypotheses chosen on structure (the XAU tick scale and the daily break),
  not tuned on outcomes; the development capture was only used to check their magnitude.
- A level is current only until the next occurrence completes. Older levels are still
  liquidity (the ASIA_L trade above was a clean sweep of a four-day-old low); excluding
  them is a policy decision, not a measured improvement.
- The M5 history the engine loads is 1,000 candles: a previous-**week** level older than
  about three and a half days has `known = false`, so model A is never judged on it (model B still is).
- The target reads M5 pivots and reference levels only; no H4/D1 structure is read.
- With XAU's daily 21:00-22:00 break the session and day tolerances are wide; a feed
  that drops a whole hour in the middle of a session would be accepted only inside the
  60-minute gap rule.

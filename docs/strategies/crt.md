# CRT Strategy V3

`crt` is Candle Range Theory as an independent Go strategy. A **fully closed H1
candle** defines a range. A later **M5 candle sweeps** one edge of it, an **M5
close reclaims** the edge, and a subsequent **M5 market-structure shift**
confirms the reversal. The stop sits beyond the actual manipulation extreme and
the technical objective is the opposite H1 edge. It is enabled in the live Go
opportunity stream; Algo Bot applies execution policy before any TradePlan is
published.

CRT v3 replaces the frozen-Python-parity v2. This page is the contract: the
causal formation sequence, the mathematics, what is valid and invalid, what
changed and why, and the evidence. The numeric thresholds are **tunable
parameters with explicit defaults in `config/analysis.yml`**, checked against the
committed captures; they are not claimed to be universal constants or to be
profitable.

## Conventions and what is, and is not, established

The *sequence* (range candle, then sweep of one edge, reclaim, lower-timeframe
structure shift, entry, stop beyond the manipulation, objective at the other
edge) is the published CRT convention
([CRT overview](https://www.crttrading.com/),
[confirmation and invalidation](https://pearloftrades.com/learn/trading-strategies/candle-range-theory/)).
That a sweep that fails to hold is a false breakout is common price-action
practice ([IG](https://www.ig.com/en/trading-strategies/what-is-a-false-breakout-and-how-can-you-avoid-it--230130)),
and that support and resistance levels carry information at all has empirical
support ([NY Fed](https://www.newyorkfed.org/research/epr/00v06n2/0007osle.html)).
None of these sources establishes any numerical threshold below, and none of
them shows that trading it earns. The thresholds are parameters that must be
calibrated and re-validated on data; the replay evidence below is a
*hypothetical, causal* account of what the rule publishes, not a performance
claim.

## Timestamps and units

Every timestamp is a candle **open** time in Unix seconds (the engine's
convention); a candle's close is its open plus its timeframe length (M5 300 s,
H1 3600 s). **H1 and M5 are related only by time.** A position in one slice is
never used to index the other (see "What v2 got wrong"). Prices are compared in
price units; thresholds are `max(pips x instrument pip size, multiple x ATR)`, so
an FX pip is never an XAU pip.

**ATR** is Wilder ATR(14): `TR[t] = max(H-L, |H-C[t-1]|, |L-C[t-1]|)` and
`ATR[t] = (13 * ATR[t-1] + TR[t]) / 14`, seeded with the mean of the first 14 true
ranges (the repository's `indicator.WilderATR`). It is computed over a **fixed
trailing window** of `atr_window_bars` (140) candles ending at the candle in
question, because Wilder smoothing is recursive and carries a decaying residual
of its seed: over the whole loaded history, a CRT's geometry would depend
slightly on how many candles happened to be loaded. With a fixed window the value
is a pure function of the candles in it. This is the one deliberate departure
from the engine's canonical (simple) ATR and from a whole-history Wilder; it is
tested (`TestInterpretationIsIndependentOfTheH1WarmupCount`). With fewer than 15
candles there is no ATR and no setup.

## Causal formation sequence

Evaluated once per closed M5 candle, using only candles closed by then. State
machine for one direction in its bullish orientation (a SELL is the exact
price-mirror, implemented by reflecting the candles, so the two cannot drift):

```
ANCHORED --sweep--> SWEPT --reclaim--> RECLAIMED --structure shift--> CONFIRMED --> candidate
    |                  |                    |                              |
    |                  |                    |                              +-- stale (age > confirmation_max_age_bars): silent
    |                  |                    +-- reclaim lost / target reached / window expired: rejected
    |                  +-- window expired / overshoot: rejected
    +-- range too small, sweep before anchor close, stale anchor, no sweep: no episode
```

### 1. Anchor

For a fully closed H1 candle `A` (its close time `T_A = open + 3600 <= the
evaluated M5 close`):

- `H_A`, `L_A`, `W_A = H_A - L_A > 0`, `M_A = (H_A + L_A) / 2`.
- `ATR_H1(A)` = Wilder ATR(14) of the H1 series as of `A`'s close (never a later
  candle's ATR).
- Valid iff `W_A / ATR_H1(A) >= minimum_h1_range_atr` (baseline 1.5, kept from v2).
- A candle that has not closed, or whose high/low were not known before the
  sweep, is never an anchor.

### 2. Sweep

A sweep can only begin on an M5 candle that **opens at or after `T_A`** and
**before `T_A + sweep_window_h1_periods x 3600`** (default 1: the next H1
candle, the CRT manipulation candle). A sweep before the anchor closed never
qualifies; an anchor older than the window is stale and produces nothing. A
wider window is an explicit, bounded configuration (1 to 3).

Bullish CRT: `low[s] < L_A - thr(s)`, with
`thr(s) = max(minimum_sweep_pips x pip, minimum_sweep_atr x ATR_M5(s))`.
Bearish CRT: `high[s] > H_A + thr(s)`. The **sweep candle** is the first such
candle. Recorded: the anchor, the sweep time, the threshold, the actual extreme
and its time, and the depth (price and ATR multiple).

### 3. Reclaim

A completed M5 close back inside the original range, on or after the sweep
candle, within `reclaim_max_bars` (6) candles of it:

- Bullish: `L_A + buf <= close[r] <= H_A`, `buf = max(minimum_reclaim_pips x pip,
  minimum_reclaim_atr x ATR_M5(r))`. Bearish mirrors.
- A close **above** `H_A` (bullish) is not a reclaim: price went through the
  whole range (`reclaim_overshoot`).
- No reclaim in the window: `reclaim_window_expired`. A new excursion may only
  start after a close back at the edge.
- The reclaim depth (price and ATR multiple) is measured and reported.

The old whole-range reaction call (`reaction.Evaluate` against the entire H1
candle) is **not** used: it could be satisfied by any rejection touching the
range, including one that predates the sweep.

### 4. Structure shift (the confirmation)

`confirmation_mode: mss`. The shift must be a candle **strictly after** the reclaim
candle (so sweep/reclaim and structure shift are never one candle), within
`mss_max_bars` = 12 candle periods of it (measured in time, so a gap in the feed
expires the window instead of stretching it):

- **Reference swing**: the most recent M5 swing high (strict fractal over
  `structure_pivot_bars` = 2 candles each side) that lies *before the
  manipulation extreme*, within `structure_lookback_bars` (24), inside the
  anchor range (`L_A < swing < H_A`; a more recent swing outside the range is skipped,
  it does not hide an older in-range one), and **confirmable before the deciding candle** (its right-hand
  candles all closed before it). A swing is never chosen with future candles.
- The **shift candle** `j` closes above it: `close[j] > swing + confirmation_buffer`,
  is bullish, and shows real displacement:
  `body_ratio = |C-O| / max(H-L, eps) >= 0.5`,
  `displacement = (C-O) / ATR_M5(j) >= 0.5`,
  `close_strength = (C-L) / (H-L) >= 0.6`.
- A reclaim alone does **not** confirm. `sweep_reclaim` (reclaim confirms) is the
  historical baseline, selectable only so offline replays can compare; production
  selects `mss` explicitly and there is no silent substitution.
- If a close below `L_A` happens between reclaim and shift, the reclaim was lost
  (`reclaim_lost`): this is also what an opposite-direction structure shift looks
  like.

### 5. Double raid

If both H1 extremes were raided, the direction is **ambiguous until a coherent,
time-ordered confirmation exists**, never chosen arbitrarily:

- the opposite extreme raided *between the sweep and the confirmation*:
  `both_extremes_raided_ambiguous`;
- raided *earlier* and **closed back inside before this sweep began**: this later
  raid is the coherent one and the setup carries `m5_crt_double_raid_resolved`;
- raided earlier and never closed back inside: ambiguous.

If both directions confirm on the same anchor, the later confirmation wins; equal
confirmation times keep neither.

## Entry, stop and target

`entry_model: reclaim_retest` (the default; `mss_retest` is selectable and tested):

- **reclaim_retest**: a band centred on the reclaimed H1 edge,
  `[L_A - tol, L_A + tol]`, `tol = max(pip, entry_tolerance_atr x ATR_M5)`: price
  retesting the edge it just reclaimed (the swept edge was also v2's entry
  reference). The honest risk is then bounded by the sweep depth plus the stop
  buffer plus the band.
- **mss_retest**: `[max(S - depth, L_A), S + tol]` around the broken swing `S`,
  `depth = max(pip, entry_depth_atr x ATR)`: price retesting the broken swing from
  above. Width is capped at the instrument's `entry_max_width_price`.
- The structure shift is the *confirmation*, not the entry location. The default is
  `reclaim_retest` because of the committed captures: the broken swing sits 2+ M5
  ATR above the edge, so `mss_retest` risk was 134-210 pips on every confirmed XAU
  episode against the 70 pip execution cap, while `reclaim_retest` is the only model
  that leaves any XAU episode inside it (see "Replay evidence").
- A post-shift displacement zone (FVG / order block) is deliberately not used: it
  would make CRT depend on another strategy's structure.

**Stop** (bullish): `E - max(pip, invalidation_buffer_atr x ATR_M5)` where `E` is
the lowest low since the anchor closed through the confirmation, the *actual*
manipulation extreme. The stop is **never** placed inside the sweep wick and is
**never tightened** to fit a limit: if the honest structural risk exceeds the
instrument's execution stop cap, the setup is rejected
(`risk_exceeds_execution_envelope`). The Algo Bot independently treats Go's
invalidation as a floor (it widens to the envelope minimum, and rejects above the
cap), so this is the same rule applied earlier, with an explicit reason.

**Target**: `H_A` (BUY) / `L_A` (SELL), the opposite anchor edge. It is not
extended to a nominal 2R beyond the range the thesis does not support.

Measured on the proximal edge (the same basis Algo Bot uses): `risk = proximal -
stop`, `reward = target - proximal`, `technical_RR = reward / risk`. Gates, in
order: target beyond entry and `reward >= minimum_target_room_atr x ATR`
(`insufficient_target_room`); `risk <= execution_stop_max_pips` when the instrument
declares one; `technical_RR >= minimum_reward_risk` (`reward_risk_below_minimum`).
`0.55` and `1.15` mirror the CRT execution profile in
`algo-bot/app/autotrade/strategy_catalog.py`, so the detector does not publish what
policy would refuse.

Also rejected: the objective already touched on the way (`target_already_reached`),
price already through the entry (`price_through_entry`), the thesis invalidated
after the confirmation (`invalidated_after_confirmation`: a close back below the swept
edge, **or any trade, a wick included, through the manipulation extreme**, where the stop
sits), and a degenerate band.

## Freshness, identity and expiry

A confirmation older than `confirmation_max_age_bars` (2 M5 periods, **measured from
candle open times, not slice positions**, so a feed gap ages it) is stale and silent: the Algo Bot refuses a reaction older than
`structural_reaction_lookback_bars - 1`, so publishing it would only be rejected
downstream. A fresh episode re-evaluated on each of its fresh bars is the *same*
candidate (same ID), so it never repeats a trade.

- `StructuralID` / `Reaction.ZoneID`: `technique:crt:<buy|sell>:<anchor open>`,
  unchanged from v2 so the thesis identity and the Algo Bot's one-plan-per-thesis
  rule continue to apply to the same range.
- Setup key (hashed into the candidate ID):
  `crt:<side>:<anchor>:<sweep time>:<confirmation time>`: stable for one technical
  event, distinct for a genuinely new sweep or confirmation.
- `FormedAt` = the sweep candle; `CreatedAt` = the confirming candle.
- `expiry_hours` is 4 (v2: 8): a resting order for a one-hour-range thesis
  outliving four hours is no longer that thesis. Uncalibrated; see risks.

## Downstream contract (Algo Bot)

Preserved, so corrected signals still pass normal policy: scope `go_h1_m5_crt`
needs a confirmed `Reaction` (pattern `sweep_reclaim`, touch = the sweep candle,
confirmation = the shift candle) and evidence starting `h1_impulse_range` or
`m5_range_sweep_reclaim` (both are emitted, plus `m5_crt_structure_shift`).
Confluence uses the shared rubric (floor 2) with **measured** factors: HTF
alignment from the engine, `WickRejection` only if a rejection wick was observed
on the sweep/reclaim candles, `StructuralAgreement` because the shift happened,
`DisplacementGrade` only if the shift displaced at least `displacement_grade_atr`
ATR. `Quality.Overall` stays `stars / 3`, as in v2, so arbitration is not
changed; the measured facts (range, sweep depth, reclaim depth, shift
displacement/body/close strength, technical RR and risk pips) are published as
`Quality.Components`, which no arbitration reads.

## Valid and invalid setups

| Case | Result |
|---|---|
| H1 range >= 1.5 ATR, sweep inside the window, close back inside, M5 shift above a prior swing with displacement, honest stop in the envelope, room and R:R satisfied | published |
| Range below 1.5 ATR but swept | `h1_range_below_minimum` |
| Sweep before the anchor closed; anchor older than the sweep window | no episode |
| Penetration below the sweep threshold | no episode |
| No close back inside within 6 candles | `reclaim_window_expired` |
| Close through the whole range | `reclaim_overshoot` |
| Reclaim, then a close back beyond the edge (opposite shift) | `reclaim_lost` |
| Reclaim, no shift within 12 candles | `mss_window_expired` |
| Close above the swing without displacement | `mss_quality_insufficient` |
| No prior confirmable swing in the lookback | `no_structure_reference` |
| Both extremes raided with no coherent confirmation | `both_extremes_raided_ambiguous` |
| Honest risk above the execution stop cap | `risk_exceeds_execution_envelope` |
| No room / below the minimum R:R | `insufficient_target_room` / `reward_risk_below_minimum` |
| Confluence below the floor | `confluence_below_floor` |
| Confirmation older than 2 candles | silent (stale) |
| Malformed or unordered candles; fewer than 15 H1/M5 candles | `invalid_candles` / `insufficient_*_history` |

## Worked examples (actual candles)

### XAU BUY, fixture scale (`internal/strategy/crt/fixtures_test.go`)

Anchor H1 `[4100, 4118]` (range 18, Wilder ATR(14) 10.571, 1.70 ATR). M5 candles
after the anchor closes (k0 opens at the close; ATR_M5 about 2.3):

| k | O | H | L | C | role |
|---|---|---|---|---|---|
| 0-4 | 4109.70 -> 4104.20 | | | | lower-high decline |
| 5 | 4104.20 | 4105.60 | 4103.90 | 4105.20 | swing high 4105.60 (pivot, confirmed after k7) |
| 8 | 4101.20 | 4101.50 | 4100.00 | 4100.50 | tests 4100, below the threshold (0.216): not a sweep |
| 9 | 4100.50 | 4101.30 | **4098.60** | 4101.00 | sweep (depth 1.40, 0.65 ATR) and reclaim on the same candle |
| 11 | 4103.00 | 4106.70 | 4102.80 | **4106.40** | closes above 4105.60: structure shift (body 0.87, displacement 1.47 ATR, close strength 0.92) |

`mss_retest`: entry `[4104.44, 4105.83]`, stop `4098.02` (extreme 4098.60 - 0.25 ATR),
target `4118`: risk 78.1 pips, reward 121.7 pips, technical RR 1.56. With the
production XAU cap of 70 pips this same setup is **refused**
(`risk_exceeds_execution_envelope`); the stop is not moved to fit. The SELL twin
(every price reflected about 4110) produces the mirrored setup exactly
(`TestBearishCRTIsTheExactMirrorOfTheBullishOne`).

### XAU SELL, real capture (`replay-xau-production-capture-20260921.json`)

H1 anchor 21 Sep 11:00 UTC: O 4342.67, H 4371.08, L 4341.96, C 4369.18 (range 29.12,
ATR_H1 15.59: 1.87 ATR). It closes at 12:00.

| M5 (UTC) | O | H | L | C | role |
|---|---|---|---|---|---|
| 12:00 | 4368.99 | 4370.46 | **4363.13** | 4370.44 | swing low 4363.13 (pivot, confirmed after 12:10) |
| 12:05 | 4370.44 | **4374.82** | 4367.27 | 4367.35 | sweep: high above 4371.08 by 3.74 (0.81 ATR, threshold 0.46); closes inside: reclaim on the same candle |
| 12:20 | 4367.18 | 4367.55 | 4355.08 | **4355.08** | closes below 4363.13: structure shift (body 0.97, displacement 2.40 ATR, close at the low) |

`reclaim_retest` (the default): entry `[4370.58, 4371.58]` (the reclaimed edge 4371.08
plus or minus 0.1 ATR_M5 of 5.04), stop `4376.08` (4374.82 + 0.25 ATR), target `4341.96`
(the anchor low): risk 55.0 pips, reward 286.2 pips, technical RR 5.20, inside the 70 pip
cap, HTF aligned (3 stars). Under the causal fill rules of the replay harness the resting
order is never filled (price kept falling to the objective without retesting 4371): a
hypothetical *unfilled* order, reported as such.

## What v2 got wrong

All of this is in code that shipped, found by reading it and reproduced:

1. **H1 position used as an M5 index.** `techniquezone.DiscoverCRT` stores the H1
   candle's slice position as the instance `OriginIndex`; `CollectCRT` hands it to
   `validateInstance`, which scans the *M5* bars from `OriginIndex+1`
   (`notInvalidated`). With 400 H1 candles and 150 M5 candles the origin lies past
   the end and the invalidation scan is skipped; with 20 H1 candles the same M5
   episode is scanned from M5 position 20. Reproduced:
   `test/techniquezone/crt_index_defect_test.go` (identical M5 episode: 0 instances
   with 20 H1 candles, 1 with 400).
2. **A sweep was any tick.** `low < range low`, no threshold, over the last
   `reclaim+5` M5 candles with the *last* piercing candle winning; no check that the
   sweep happened after the anchor closed, and anchors up to three H1 candles old were
   eligible.
3. **The reclaim was any close** back at the edge within 6 candles of that last
   piercing candle, with no measured depth, and the "reaction" was a whole-range
   `reaction.Evaluate` that any touch of the H1 range could satisfy, including one that
   predates the sweep.
4. **No lower-timeframe confirmation** at all.
5. **The stop was inside the sweep wick.** Invalidation was `range edge - 0.25 ATR`; a
   sweep deeper than 0.25 ATR (the normal case) leaves the stop between the edge and
   the manipulation extreme. The old page claimed "invalidation is beyond the sweep plus
   ATR buffer"; the code did not do that. Replay: **105 of 127** setups.
6. **Unconditional evidence.** Wick rejection and structural agreement were scored true
   for every CRT regardless of what happened.
7. **A double raid was never considered**: both sides could publish for one anchor.

## Intentional differences from the frozen Python behavior

The oracle golden is untouched and still proves the frozen port bar for bar
(`test/techniqueparity`, `TechniqueExcludingConfluenceCoverage`). v3 departs from it,
deliberately, in: time-based (never positional) H1/M5 relation; sweep and reclaim
thresholds and a bounded sweep window anchored on the anchor's close; a required M5
structure shift; the stop beyond the manipulation extreme; double-raid handling;
measured confluence factors (fib touch is not scored); `expiry_hours` 8 -> 4; a
pre-check of the instrument's execution stop cap; stale confirmations suppressed;
candidate identity includes the sweep and confirmation times (the thesis identity,
`technique:crt:<side>:<anchor>`, is unchanged).

## Replay evidence

Method (`test/crtreplay`): every committed capture is replayed bar by bar through the
production engine; at each M5 close the frozen v2 logic (kept only in the test) and v3 are
run on the *same* context. Outcomes are **hypothetical** under causal fill rules: the
order is placed when the observing candle closes and fills only on a later candle at the
proximal edge (the fill candle can only stop it out); the stop is checked before the
target, and a candle touching both is flagged ambiguous and resolved as a stop; an
unfilled order is cancelled at expiry; a filled position is marked to the close after 8
hours. They are never realised performance. Roles were fixed before results were read:
the 14-21 Sep XAU capture was the only capture used to sanity-check threshold
magnitudes ("development"); every other capture is held out. No default was tuned on a
held-out capture, and the one default chosen from evidence (`reclaim_retest`) was chosen
on geometry (risk against the instrument cap), not on outcomes.

| Capture | v2 setups | v2 stop inside sweep wick | v3 technical setups (before the confluence floor) | v3 published |
|---|---|---|---|---|
| XAU 14-21 Sep (profit week) | 15 | 13 | 1 | 1 |
| XAU 28 Sep-6 Oct (incident window) | 15 | 15 | 0 | 0 |
| EURUSD 6 Oct | 19 | 19 | 2 | 0 |
| GBPUSD 5 Oct | 2 | 1 | 5 | 0 |
| GBPJPY 6 Oct | 30 | 25 | 1 | 0 |
| USDJPY 5 Oct | 46 | 32 | 0 | 0 |
| **Total** | **127** | **105 (83%)** | **9** | **1** |

Why the 126 other v2 setups were not published by v3 (measured, per v2 setup): 50 relied
on a sweep v3 does not recognise (34 happened more than one H1 candle after the anchor
closed, 16 pierced the edge by less than the sweep threshold); 33 swept a range under 1.5
ATR (measured at the anchor, not at evaluation time); 28 lost the reclaim (a close back
beyond the edge before any structure shift); 12 never reclaimed within 6 candles; 2
reclaimed but never shifted structure; 1 was confirmed but its honest stop exceeded the
XAU cap. Of the 9 v3 technical setups, 8 were refused by the confluence floor: the shared rubric
(version v1) needs two of {HTF alignment, a rejection wick, a 1 ATR displacement} on top of the
structure shift to reach 2 stars, and these had fewer. That is the same floor the Algo Bot
applies (`min_confluence=2`).

Funnel over the six captures (distinct episodes, from the first bar;
`CRT_FUNNEL=1 go test ./test/crtreplay -run Funnel -v`). Sweep and reclaim of a 1.5 ATR
range inside the window, with no structure shift (the historical baseline mode): 59 fresh
episodes. Requiring the structure shift keeps 11 (XAU 3, EURUSD 2, GBPUSD 5, GBPJPY 1). The
production configuration's refusals over the six captures, in distinct episodes, are: reclaim
lost before any shift 34, shift window expired 6, no usable in-range swing 2, swing broken
without displacement 1 (the reclaim-lost count also includes episodes the baseline mode
does not count, so these do not partition the 59). Against the execution stop cap the three confirmed
XAU episodes carry an honest risk of 130, 81 and 55 pips with `reclaim_retest` (190, 210 and
135 pips with `mss_retest`), so only the 21 Sep SELL fits the 70 pip cap; with the default
one-hour window no FX episode exceeds its cap. With a three-hour sweep window the published-technical count rises to 18
(XAU 3, EURUSD 5, GBPUSD 5, GBPJPY 3, USDJPY 2), and refusals for risk to 10.

**What this does and does not show.** v3 produces far fewer setups than v2 by
construction. Simulated outcomes cannot be compared: v3 published a single order and it was
never filled. For v2 the simulation is positive on both XAU captures (mean R 1.59 over 15
and 2.23 over 15, two strongly trending windows; across the 30, 7 reached the target, 3
were marked to the close and 20 were stopped) and negative on EURUSD, GBPUSD and GBPJPY (mean
R -0.61, -0.87, -0.31), and positive on USDJPY (1.06 over 46). These are proxies on 2 to 46 setups per capture, with stops inside
the wick; they neither support nor refute any claim about profitability, and **no
profitability or performance claim is made for v3**. The claims that are made are
technical: the stop is beyond the extreme, the decision uses no future candle, the H1
warm-up depth cannot change an interpretation, and the events are real sweep, reclaim and
structure shifts.

## Tests executed

See the pull request for the exact commands and outcomes. The CRT-specific tests are
`internal/strategy/crt` (positive BUY and SELL, entry models, baseline mode, every
negative case listed above, the H1 warm-up regression, prefix invariance with adversarial
future candles, malformed candles, configuration, candidate contract), `test/crtreplay`
(`TestCRTHasNoFutureDependenceOnRealCaptures`,
`TestCRTSetupsOnRealCapturesSatisfyEveryTechnicalInvariant`,
`TestCRTV3DetectGoldenOnRealCaptures`, and the replay certification
`CRT_REPORT_DIR=/tmp/crt go test ./test/crtreplay -run Certification -v`, which also
requires the engine's decision to equal the offline decision on every evaluation: 0
mismatches over 9,000 evaluations), and the reproduction of the v2 defect
(`test/techniquezone/crt_index_defect_test.go`).

## Independent certification

A reviewer who did not write the detector audited the committed code against this page
in a separate worktree, wrote adversarial tests, and ran them. Their findings and what was
done (every fix has a regression test in `internal/strategy/crt/audit_test.go`):

| # | Finding | Severity | Resolution |
|---|---|---|---|
| 1 | A wick through the stop on a candle **after** the confirmation was never seen, so a setup whose stop had already traded could publish (4 of 1,199 fuzzed setups) | High | Fixed: any later trade through the manipulation extreme, or a close below the swept edge, is `invalidated_after_confirmation` |
| 2 | Freshness, the reclaim window and the shift window counted slice positions, so a 55 minute feed gap left a confirmation "fresh" | Medium | Fixed: every age and window is measured from candle open times |
| 3 | One candle could be the sweep, the reclaim and the structure shift | Medium | Fixed: the shift is strictly after the reclaim candle (the page now says so) |
| 4 | A more recent swing high above the anchor range hid an older valid in-range swing | Low | Fixed: out-of-range swings are skipped |
| 5 | A duplicate timestamp anywhere in the loaded window fails the whole evaluation closed; comparisons carry no epsilon; `execution_stop_max_pips: 0` silently disabled the envelope; the stop uses the deepest low since the anchor closed, including voided earlier excursions | Low | Documented or fixed: the config now rejects a zero cap (the engine only injects a resolved cap); the fail-closed and conservative-stop behaviours are intended and stated here |

What the audit tried and could not break, with 400 randomised scenarios (200 reflected),
8,237 evaluations and 331 setups: prefix invariance and no future leak (replacing every
candle after the evaluated one, and every not-yet-closed H1 candle, with garbage changes
nothing), BUY/SELL mirror symmetry within 1e-9, anchor closure, the envelope rejecting
instead of clamping, double-raid handling, and stable-versus-distinct identities.

Verdict after the fixes: **certifiable as a technical contract**, with the unresolved
risks below. It is not a statement about profitability.

## Unresolved risks

- **Selectivity.** v3 published one setup over six captures where v2 published 127. If
  CRT activity is wanted, relaxing a rule (structure shift, sweep window, the envelope
  for CRT) is a separate, evidence-backed decision; none was tuned here.
- **XAU envelope.** XAU M5 ATR in the captures is about $5, sweeps are $4-10 deep, and an
  honest stop beyond them is mostly past the 70 pip cap. v2 appeared tradable on XAU
  because its stop sat inside the wick. Whether CRT should have its own cap on XAU is an
  owner decision this change does not make.
- **Confluence Zone still reads the frozen CRT instance.** `techniqueInstanceSource`
  still builds CRT instances with the frozen `CollectCRT` (including defect 1) and
  Confluence Zone aggregates them. Replacing that is Confluence Zone's own change and was
  out of scope here.
- **Defaults are uncalibrated.** Every threshold is a parameter; the sample (six
  captures, a handful of setups) cannot calibrate them. `expiry_hours` 4 and the
  `reclaim_retest` band are judgements, documented as such.
- **Wilder ATR over a fixed window** differs slightly from the engine's canonical simple
  ATR; it is used only inside CRT, and documented and tested.
- The simulator's fill rules are conservative proxies, not the executor's behavior.

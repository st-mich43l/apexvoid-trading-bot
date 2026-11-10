# Trendline Strategy V8


**Contract (v7).** Two confirmed anchors establish a line; nothing after them has to validate it. The strategy is the frozen V2 `trendline_reaction` less its two extra-touch requirements: immutable causal anchors, health (not tentative, broken, degraded, exhausted or stale), the chop-regime higher-timeframe requirement, a line touched from the intended side and a confirmed reaction inside the interaction band.

`trendline` consumes immutable causal anchors of one timeframe (M5 or M15, each its own setup, see [Timeframes](#timeframes-v8)) and the canonical interaction classifier. Entry is the interaction band, invalidation is a close-violation buffer, and the target is the nearest opposing liquidity (see below).

## Two-touch contract (v7)

Three different decisions, kept apart:

| | What | Causal rule |
|---|---|---|
| A. Construction | two adjacent confirmed same-kind pivots (support: swing lows, resistance: swing highs), time-ordered, slope in `[minimum_slope_atr, maximum_slope_atr]` ATR per bar, at least `minimum_touch_spacing_bars` apart, spanning at least `minimum_span_bars`; not broken (an unresolved close through the line) and not degraded | the two pivots exist as candles |
| B. Knowable | the line may be used from the close that confirmed its second anchor (`Trendline.AnchorBConfirmedIndex`); a swing is only a swing `pivot_bars` candles later | the line is not published before that close; since v8 the reaction may be the one at B itself (see below) |
| C. Executable | a touch of the projected line, from the intended side, then a confirming candle (`ReactionFrom`: wick rejection, strong reclaim, engulfing, sweep reclaim), both inside the last `reaction_lookback_bars` (3) closed bars | the trade is this reaction, never the second anchor itself |

So the line is never published before the close that confirms B; v7 then required a further visit to the line (the reaction had to start after that close, which is what the older rule called "the third touch"). v8 trades the reaction at B itself, below. A *validation touch* is a later pivot that already completed its own reaction window; it only makes the line stronger. `minimum_validation_touches` and `chop_minimum_validation_touches` are now 0 (they were 1 and 2: with the builder's `max(1, ...)` they made a line with exactly two anchors `tentative` and the strategy rejected tentative lines, so a line had to be touched a third time, and in chop a fourth, before it could trade). A line with `exhaustion_validation_touches` (4) validations is still worn through and rejected; the stale rule (the last validation more than `maximum_bars_since_last_touch` ago) only exists for a line that has a validation; chop still needs the higher-timeframe alignment (`chop_require_htf_aligned`).

**Interaction window (v7).** The reaction no longer has to be on the latest bar. v6 required the latest closed bar itself to touch the band and close on the valid side, so a reaction whose confirming candle did not touch the line (a wick that touched on one candle, a candle that closed back off it on the next) was never published. Now the latest bar must leave the line standing (reclaimed it or not back at it; a close through it or a touch from the wrong side rejects), `ReactionFrom` finds the touch and the confirmation inside `reaction_lookback_bars` (the Algo Bot adapter accepts a confirmation up to `structural_reaction_lookback_bars - 1` bars old), and the **touch bar** is judged where it happened: price reached the projected line at that bar, from the intended side (prior close at least `approach_min_distance_atr` on the valid side), without closing through it. The entry still rests at the line's price at the latest bar and `EntryValid` still refuses price that ran from it. The quality `approach` dimension reads the touch bar.

**Identity (v7).** The thesis id is `trendline:<kind>:<anchor A open time>:<anchor B open time>`. Until v6 it was built from the builder's swing ids (`legacy:<window index>:<n>`), which are positions in the analysis window and change by one on every closed bar, so the same line had a new id on each bar and one reaction was published as a new opportunity up to three times (15 of 41 setups in the replay below were repeats once a reaction could be seen on more than one bar). The Go opportunity id is derived from it, so v7 publishes one opportunity per reaction.

**Not changed (measured, and left alone).** The minimum span between the anchors (`minimum_span_bars`, 20, i.e. 100 minutes): it is the spacing rule of a two-anchor line, and with it the largest remaining gate (most lines have their anchors closer than that, or are broken before price returns). Lowering it to the spacing minimum (3) would add 116 reactions to the 26, from lines whose anchors are 3 to 18 bars apart: two nearby pivots, not a second touch, so it is not done. The structural-target rule (`minimum_target_distance_atr`, no fixed reward:risk): in 11 days of replay it refused one clear-path line (no opposing pool at all, GBPJPY) and, in v6, walls 0.03 to 0.36 ATR in front of the entry, which is no room, not a rule that is too strict; whether a clear path should publish with a labelled fixed target is an owner decision. The stale rule: lifting it would add 3 reactions (validated lines hidden for being untouched for more than 30 bars). Dedupe (oldest anchor pair first): a healthy confirmed line was hidden by an older degraded near-duplicate for 74 line-bars on GBPUSD (1 on XAU); the primitive is shared with the zone scorers, so it is not changed here. Quality (`trendline.quality.v1`): a line with no validation scores 0 for `line_validation` and 0.5 with one, so a two-anchor line is already worth less than a respected one; the dimension is unchanged.

Replay (7 days of production M5 for XAU, EURUSD, GBPUSD, USDJPY, GBPJPY, closed bars only, `cmd/replay`; historical, not live performance):

| | v6 | v7 |
|---|---|---|
| adjacent anchor pairs / spacing-refused / slope-refused | 4,956 / 0 / 3,737 | same |
| two-anchor lines built | 1,219 | 1,219 |
| of which with at least one validation | 403 | 403 |
| lines that ever passed the structural gate | 29 | 61 |
| lines with a confirmed reaction | 7 | 22 |
| published (one per reaction) | 7 | 26 (17 validated, 9 two-anchor only) |
| accepted by the Algo Bot adapter and static admission | 7 / 7 | 26 / 26 |
| all other strategies' envelopes (2,722) | | identical |

Published per symbol, v6 to v7: XAU 1 to 6, EURUSD 1 to 4, GBPUSD 3 to 6, USDJPY 0 to 5, GBPJPY 2 to 5. A truncated-history replay gives identical entries, stops, targets and scores (7 of 7). Of the 26, 25 meet the catalog's 0.55 ATR room and 1.15 reward:risk on the structural stop, and 16 are below 1R once execution widens the stop to the instrument floor (v6: 4 of 7): that is the Algo Bot's decision, not a Go gate.

## Timeframes (v8)

**One strategy, two independent setups.** `setup_timeframes: [M5, M15]`. A line is found, reacted to and published on one timeframe's own closed candles, swings, structure, ATR, projected lines, regime and liquidity pools; an M5 setup never reads M15 and never waits for it, an M15 setup never reads M5, and a frame that is not warm yet never holds the other back. M15 can be switched off by removing it from the list (its parameter block can stay). This is still `strategies.trendline`, not a second strategy or a shared family: the registry, the catalog, the Algo Bot scope and every risk rule are the same.

**Evaluation mechanism.** The registry's `Evaluate(ctx)` did not say which bar had closed. `strategy.ClosedFrameEvaluator` is an optional interface: a strategy that implements `EvaluateClosed(ctx, closed)` is called with the timeframe that just closed, and only needs that frame in the context. Trendline implements it, so an M5 close evaluates M5 and an M15 close evaluates M15, and nothing is evaluated "just in case" on both (every other strategy is unchanged). Anything that has no closing event can still call `Evaluate`, which is every setup frame.

**When Anchor B becomes tradeable (researched, two options).**
- *A. Two confirmed pivot anchors (chosen).* Anchor A and anchor B are swing pivots, each confirmed `pivot_bars` (2) candles after it formed. The line exists from the close that confirms B (`AnchorBConfirmedIndex`). The reaction at B (a wick, a reclaim or an engulfing candle among the bars from B to that close; these are the candles that made B a pivot) is read with the same reaction rules as any other visit, inside the last `reaction_lookback_bars` (3) closed bars, which is exactly B and the two bars that confirm it. B is therefore tradeable at the close of its own confirmation, when the reaction is still fresh (the Algo Bot adapter accepts a confirmation up to two bars old) and the entry has not run (`EntryValid`: price must not be through the entry band and must not be more than the entry distance beyond it). The candidate is created on that bar: nothing is backdated, the touch and the confirmation are the bars that really formed the reaction, and with a B older than the window there is no fresh reaction at all (a two-bar window already refuses it: `TestStaleSecondTouchEvidenceDoesNotExecute`). v7 refused this reaction by starting the window after the confirming close.
- *B. A confirmed first pivot plus an emerging second pivot, qualified by a closed-bar reversal (rejected).* It would draw the line through a provisional second point, the lowest low (highest high) of the last few bars after a reversal candle. That point moves with every lower low (higher high), so the anchor is not immutable, a line drawn through it is redrawn, and the stop, the interaction band and the identity would all move with it. It needs a different construction (a different slope rule, a re-anchoring rule and a way to retire the provisional line), and it buys at most two bars on option A. Not built.

Measured on the production captures, 7 of the 35 M5 opportunities are such a fresh second-touch reaction (median price 1.3 ATR beyond the band by the time B is confirmed, the largest 2.2; later visits: median 0.5 ATR): a pivot is only confirmed after the bounce has started, so these are resting entries that wait for the return to the line, never chases, and the Algo Bot's own entry rules decide whether and when they arm.

**Identity and lifecycle.** The thesis is `trendline:<timeframe>:<kind>:<anchor A open time>:<anchor B open time>` (direction follows the kind): the same two instants read on M5 and on M15 are two theses, restarts and replays give the same ids (they depend on candle times only), and one unchanged reaction (same touch and confirming candles) is one opportunity however many bars it stays fresh. `ObservedTimeframe` is the frame that closed, `StructureTimeframe` the frame the line lives on (both M5 or both M15; M5 opportunities, which left it empty, now carry it too), evidence codes are `m5_trendline_*` or `m15_trendline_*`. An M15 opportunity is withdrawn by an M15 close beyond its invalidation, an M5 one by an M5 close (`InvalidateByClose` matches the observing timeframe); the line itself is refused the moment its own frame breaks it or degrades it. Several opportunities may exist on one line, each its own reaction (a second visit hours later, or one bar later); the existing same-thesis, same-direction and entry-overlap rules of the Algo Bot hold them to one position.

**Parameters per timeframe (reviewed, `strategies.trendline.timeframes.<TF>` and `analysis.trendlines`).** Bar-count geometry was measured, not copied or converted. On 7 days of M5, M15 and the five symbols: median bars between the two anchors 8 (M5) and 9 (M15); share of lines with anchors 20 or more bars apart 5% and 6%; median |slope| 0.129 and 0.120 ATR per bar (the slope gates opened to 0 to 5 to measure it). Structure is self-similar in bars, so `minimum_slope_atr`, `maximum_slope_atr`, `minimum_touch_spacing_bars`, `minimum_span_bars` (20) select the same share of lines on each frame; converting them to minutes would have made M15 three times less selective (7 bars of M15 would pass 71% of lines, 20 bars of M5 pass 5%). Everything in ATR (interaction band 0.20, close violation 0.15, approach 0.10, stop buffer 0.25, target distance 1.0) is already the frame's own volatility. The reaction window is 3 bars on both: it has to contain B and the bars that confirm it, and that is a property of the pivot rule, not of the clock. Expiry is in hours (12 on both, elapsed time already). `maximum_bars_since_last_touch` (30) applies only to a line that has a validation touch, and is a bar count of the frame for the same reason. `timeframes.M5` and `timeframes.M15` hold these values explicitly (`maximum_bars_since_last_touch`, `expiry_hours`, `reaction_lookback_bars`) so a frame can be tuned on its own evidence without code.

**Stops and targets.** Unchanged and per frame: the stop is beyond the line's band and the reaction extreme plus 0.25 ATR of that frame, refused when it exceeds the instrument cap; the target is the nearest unswept opposing liquidity of that frame at least 1 ATR (of that frame) away, with no fixed fallback. An M15 structure is larger, so more M15 setups are refused by the instrument stop cap (2 of 6 M15 candidates in the replay) and by room.

**Quality.** `trendline.quality.v1` is unchanged: a two-anchor line scores 0 for `line_validation` and a respected line more, and the score gates nothing (`quality_ranking: false`). `line_significance` reads span and slope in bars of the frame (same selectivity, above). New measurements for later outcome studies: `timeframe_minutes`, `confirmation_age_bars` and `reaction_at_second_anchor`. HTF context for an M15 setup is the H1 bias, never M15's own; M5 reads the engine's HTF read as before.

Replay (7 days of production M5, M15 and H1, five symbols, closed bars only, `cmd/replay`; historical, not live performance), refusal funnel in lines (a line counted once at the furthest stage it reached on any bar):

| stage | v7 M5 | v8 M5 | v8 M15 |
|---|---|---|---|
| lines in the frame's window | 748 | 748 | 252 |
| pass the structural gate (not broken, degraded, exhausted, stale or short) | 61 | 61 | 24 |
| a reaction inside the window | 24 | 33 | 6 |
| the touch is a real touch of the projected line | 22 | 31 | 5 |
| entry still valid | 22 | 31 | 5 |
| qualified (confluence floor) | 20 | 25 | 5 |
| published (one per reaction) | 26 | 35 | 4 |

The two extra M5 reactions per symbol are the second-touch ones (9 new, none lost); M15 adds 4 over the same days (EURUSD 2, USDJPY 1, GBPJPY 1; 2 more M15 candidates were refused by the instrument stop cap). Every one of the 39 is accepted by the Algo Bot adapter and static admission and builds a TradePlan; the 2,686 envelopes of every other strategy are identical to master; a truncated-history replay gives identical opportunities (22 of 22, none present only in the shorter replay, none missing from it). Examples: EURUSD M15 SELL, resistance through the swing highs of 2026-11-02 19:00 and 2026-11-03 00:15 UTC, wick rejection at the second anchor, published 00:45 (two M15 bars later) with entry 1.11961-1.11982, stop 1.11994 and target 1.11797 (nearest opposing liquidity), no validation touch; XAU M5 SELL, anchors 2026-11-06 02:10 and 04:35, strong reclaim, published 04:45, entry 4044.95-4047.11, stop 4049.12, target 4037.51; GBPJPY M5 SELL, anchors 11-05 20:20 and 22:00, wick rejection, published 22:10.

**Not changed.** The two-touch minimum-validation settings (0 since v7), the span rule, the stale rule, the target rule, dedupe, the instrument stop budget, `quality_ranking`, the Algo Bot's risk, exposure, overlap and entry rules, and every other strategy. The scalp 40-pip stop does not apply (Trendline is not a scalp). No shadow, observe-only or paper mode: both frames publish into the live stream.

**Reaction-anchored invalidation (v5).** Entry is the interaction band and invalidation is the close-violation buffer beyond the band's far edge, or, if the bars from the touch to the confirmation traded beyond it, beyond that extreme (Buy: `min(structure low, lowest low touch..confirm) - invalidation_buffer_atr*ATR`; Sell mirrored). The buffer is unchanged and has no pip floor; when the reaction's extreme set the price the candidate carries `invalidation_beyond_reaction_extreme`, and the target, quality geometry and published invalidation follow the new price. Decisions and entry are unchanged; the invalidation differs from the frozen publisher by design.

**Instrument stop budget (v6).** A candidate whose structural stop (distance from the proximal entry edge to the invalidation above) exceeds the instrument stop cap (XAU 70, EURUSD 20, GBPUSD 25, GBPJPY 35, USDJPY 28 pips) is **not published**: refused, never tightened, and neither the entry nor the invalidation is moved to fit. One inside [floor, cap] carries `stop_budget_structural`; one below the floor carries `stop_budget_floor` and execution widens it.

Identity is the anchor pair's candle times, never evaluation time (v7). Broken/exhausted lines and
testing without reclaim reject. It is enabled in the live Go opportunity
stream; Algo Bot applies execution policy before any TradePlan is published.

## Target (v4)

The target is the nearest unswept opposing liquidity pool at least `minimum_target_distance_atr` (1.0) ATR from
the entry's proximal edge. With none there is no technical objective, so nothing is published; there is no fixed
reward:risk fallback and no projection. v3 reported twice the distance from the entry to the interaction band edge, which
was unrelated to the published invalidation. Measured on the replay captures (16 setups): median technical RR 3.4 (v3: below
1 in every setup), median room cover against the executable stop 0.84 (v3: 0.05). `Quality.Overall` is the mean of eight
`score_*` dimensions (`trendline.quality.v1`, [quality](../analysis-engine.md#acceptance-and-quality)), informational only.

Parity: the frozen oracle took 8 decisions; v4 matches 7 and stays silent on the one with no opposing liquidity, so
`trendline_reaction` is measured only in `test/legacyparity` (as `session_level_reaction` is).

## Q2: the line must be knowable before it can be reacted to

A line is built from the two latest adjacent confirmed pivots of the same kind; its second anchor is knowable only from the candle whose close confirmed it (`Trendline.AnchorBConfirmedIndex`). The reaction's touch and confirmation must come
AFTER that candle (`LegacyDetector.ReactionFrom`): the bars that formed and confirmed an anchor are the line's formation. The line is `price(t) = p1 + slope * (t - t1)` with `slope = (p2 - p1) / (t2 - t1)` over candle positions (the series is a
continuous M5 feed), the slope bounded in ATR per bar, the touch band `interaction_band_atr` x ATR, a support line only ever BUY and a resistance line only ever SELL, so one line cannot produce both directions. The session context is the measured sweep, not a constant: the 16 replayed setups go from 3 stars to 9 x 3 and 7 x 2. A trendline break-and-retest is `break_retest`'s thesis; this strategy publishes rejections only.

## Status and execution

- **Certification**: `GO_NATIVE_VALIDATED` ([catalog](README.md#catalog)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument. Pinned by build, causality and interaction tests. Against the Python `trendline_reaction` it matched 6 of 8 oracle decisions, too few to prove parity.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by execution eligibility, then freshness, then id trades
  (no quality score is part of the order) and the rest are suppressed ([execution](../execution.md#same-thesis-arbitration)).

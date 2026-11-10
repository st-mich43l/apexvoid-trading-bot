# Trendline Strategy V7


**Contract (v7).** Two confirmed anchors establish a line; nothing after them has to validate it. The strategy is the frozen V2 `trendline_reaction` less its two extra-touch requirements: immutable causal anchors, health (not tentative, broken, degraded, exhausted or stale), the chop-regime higher-timeframe requirement, a line touched from the intended side and a confirmed reaction inside the interaction band.

`trendline` consumes immutable causal M5 anchors and the canonical interaction classifier. Entry is the interaction band, invalidation is a close-violation buffer, and the target is the nearest opposing liquidity (see below).

## Two-touch contract (v7)

Three different decisions, kept apart:

| | What | Causal rule |
|---|---|---|
| A. Construction | two adjacent confirmed same-kind pivots (support: swing lows, resistance: swing highs), time-ordered, slope in `[minimum_slope_atr, maximum_slope_atr]` ATR per bar, at least `minimum_touch_spacing_bars` apart, spanning at least `minimum_span_bars`; not broken (an unresolved close through the line) and not degraded | the two pivots exist as candles |
| B. Knowable | the line may be used from the close that confirmed its second anchor (`Trendline.AnchorBConfirmedIndex`); a swing is only a swing `pivot_bars` candles later | nothing at or before that close is a touch of, or a reaction to, the line |
| C. Executable | a touch of the projected line, from the intended side, then a confirming candle (`ReactionFrom`: wick rejection, strong reclaim, engulfing, sweep reclaim), both inside the last `reaction_lookback_bars` (3) closed bars | the trade is this reaction, never the second anchor itself |

So the second anchor can never be traded on its own touch (B comes later than the pivot); the first trade is the first return to the line after B, which is what the older rule called "the third touch". A *validation touch* is a later pivot that already completed its own reaction window; it only makes the line stronger. `minimum_validation_touches` and `chop_minimum_validation_touches` are now 0 (they were 1 and 2: with the builder's `max(1, ...)` they made a line with exactly two anchors `tentative` and the strategy rejected tentative lines, so a line had to be touched a third time, and in chop a fourth, before it could trade). A line with `exhaustion_validation_touches` (4) validations is still worn through and rejected; the stale rule (the last validation more than `maximum_bars_since_last_touch` ago) only exists for a line that has a validation; chop still needs the higher-timeframe alignment (`chop_require_htf_aligned`).

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

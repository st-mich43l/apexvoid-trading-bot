# Reaction-anchored invalidation (key_level v4, supply v4, demand v4, order_block v3, fvg v3, flip_zone v4, trendline v5)

**Defect.** The seven legacy-family strategies published invalidation = far edge of the structure minus `invalidation_buffer_atr*ATR` without looking at the bars the reaction itself traded. A
sweep or strong reclaim is confirmed by a wick that pierces the band, so that price often sits *inside* the confirming wick: a stop that the market has already traded.

**Change (Go only).** For a candidate with a confirmed reaction the invalidation is `min(structure low, lowest low of the M5 bars touch..confirmation) - buffer` (Buy) or
`max(structure high, highest high touch..confirmation) + buffer` (Sell). The buffer is unchanged and has no pip floor. One helper, `strategyutil.ReactionAnchoredInvalidation`, serves
`TechniqueCandidate` (supply, demand, order_block, fvg, flip_zone, trendline) and `keylevel.(*Strategy).candidate`. When the reaction's extreme (not the structure) sets the price the candidate carries
the evidence code `invalidation_beyond_reaction_extreme`. A candidate without a confirmation (resting) is unchanged. The helper reads only bars up to the confirmation (no future bar). The
fixed-reward:risk fallback target, the quality geometry (`room_reward_risk`) and the published invalidation follow the new price automatically; the quality formula and `quality_model_version` are untouched.
`TechniqueSpec.AnchorToReaction` is set by the six live strategies that use `TechniqueCandidate`; the replays that reproduce a retired publisher (`test/czreplay`, `test/ifvgreplay`, `test/crtreplay`) keep the structural price.

## Measurement

`analysis-engine/test/reactionstop/measure_test.go` replays all 21 committed M5 captures (the `-m1-` captures excluded) and compares, for every confirmed candidate of the seven strategies, the
structural-only price with the anchored one. Output: `reaction-anchored-stop-results.json` (written by the replay, not committed). Nothing here is an outcome and nothing was tuned on it.

- **Before** figures come from the merge-base tree (`797266e4`): `git worktree add <dir> 797266e4`, copy `measure_test.go` into it, `REACTION_STOP_OUT=<out> go test ./test/reactionstop -run TestMeasureReactionAnchoredStop`.
  They reproduce the baseline exactly: 12,080 confirmed candidates, 1,481 (12.3%) with the stop inside the reaction; key_level FX 577/4,289, XAU 244/1,697; demand FX 239/2,113, XAU 33/425; supply FX 177/1,937, XAU 40/400;
  order_block FX 139/694, XAU 25/146; flip_zone FX 1/28; trendline FX 4/18, XAU 2/3; fvg 0/267 and 0/52.
- **After** figures come from the same command on this tree, which also asserts that every published invalidation lies beyond the reaction extreme by the buffer (within one tick) and that the evidence code is present exactly when the extreme set the price.
  Result: 12,080 candidates (the same set, the same entry edges), 0 with the stop inside the reaction, 4,970 carry the evidence code. The two outputs are merged into the results file.
- "Anchored" in the table is a candidate whose price moved by at least one instrument tick (4,700); 4,970 carry the code (for the other 270 the extreme is beyond the structure by less than a tick).
- Risk is measured from the proximal entry edge. Envelope maxima: EURUSD 20, GBPUSD 25, GBPJPY 35, USDJPY 28, XAU 70 pips; minima 12 / 15 / 22 / 18 / 50.

| strategy class | n | stop inside reaction, before | after | anchored | added risk pips, median / p90 | added ATR, median / p90 | anchored over envelope max | newly over max | FX anchored below min | XAU executed stop widened | XAU median added $ (0.12 lots) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| demand FX | 2,113 | 239 (11.3%) | 0 | 658 (31.1%) | 1.4 / 4.1 | 0.35 / 0.91 | 9 | 5 | 538 | 0 | 0.00 |
| demand XAU | 425 | 33 (7.8%) | 0 | 106 (24.9%) | 10.8 / 33.4 | 0.31 / 0.82 | 82 | 20 | 0 | 14 | 7.70 |
| flip_zone FX | 28 | 1 (3.6%) | 0 | 3 (10.7%) | 0.7 / 1.4 | 0.29 / 0.64 | 0 | 0 | 3 | 0 | 0.00 |
| flip_zone XAU | 11 | 0 (0.0%) | 0 | 1 (9.1%) | 4.8 / 4.8 | 0.24 / 0.24 | 0 | 0 | 0 | 0 | 0.00 |
| fvg FX | 267 | 0 (0.0%) | 0 | 0 (0.0%) | 0.0 / 0.0 | 0.00 / 0.00 | 0 | 0 | 0 | 0 | 0.00 |
| fvg XAU | 52 | 0 (0.0%) | 0 | 0 (0.0%) | 0.0 / 0.0 | 0.00 / 0.00 | 0 | 0 | 0 | 0 | 0.00 |
| key_level FX | 4,289 | 577 (13.5%) | 0 | 1,831 (42.7%) | 1.2 / 3.8 | 0.32 / 0.91 | 11 | 8 | 1729 | 0 | 0.00 |
| key_level XAU | 1,697 | 244 (14.4%) | 0 | 897 (52.9%) | 10.7 / 36.2 | 0.28 / 0.81 | 482 | 245 | 0 | 255 | 5.78 |
| order_block FX | 694 | 139 (20.0%) | 0 | 394 (56.8%) | 1.4 / 4.8 | 0.35 / 0.95 | 0 | 0 | 382 | 0 | 0.00 |
| order_block XAU | 146 | 25 (17.1%) | 0 | 85 (58.2%) | 12.0 / 33.0 | 0.33 / 0.76 | 38 | 27 | 0 | 12 | 8.33 |
| supply FX | 1,937 | 177 (9.1%) | 0 | 595 (30.7%) | 1.4 / 4.5 | 0.33 / 0.88 | 6 | 5 | 463 | 0 | 0.00 |
| supply XAU | 400 | 40 (10.0%) | 0 | 120 (30.0%) | 13.1 / 35.5 | 0.36 / 0.80 | 89 | 23 | 0 | 13 | 8.90 |
| trendline FX | 18 | 4 (22.2%) | 0 | 8 (44.4%) | 2.0 / 2.3 | 0.28 / 0.41 | 0 | 0 | 8 | 0 | 0.00 |
| trendline XAU | 3 | 2 (66.7%) | 0 | 2 (66.7%) | 15.9 / 15.9 | 0.42 / 0.42 | 0 | 0 | 0 | 0 | 0.00 |
| **total** | 12,080 | 1,481 (12.3%) | 0 | 4,700 (38.9%) | 1.8 / 14.8 | 0.32 / 0.88 | 717 | 333 | 3123 | 294 | 5.85 |

| confirmation pattern | n | stop inside before | share | anchored | anchored over max | newly over max |
|---|---|---|---|---|---|---|
| engulfing | 1,882 | 0 | 0.0% | 0 | 0 | 0 |
| rejection_choch | 288 | 24 | 8.3% | 72 | 15 | 9 |
| strong_reclaim | 3,367 | 930 | 27.6% | 3,139 | 488 | 223 |
| sweep_reclaim | 4,010 | 524 | 13.1% | 1,483 | 214 | 101 |
| wick_rejection | 2,533 | 3 | 0.1% | 6 | 0 | 0 |

### What it costs

- **Python will now reject some setups it used to admit.** 333 candidates (2.8% of the 12,080; XAU key_level 245, supply 23, demand 20, order_block 27; FX 18) were within the envelope maximum and the anchored stop is above it. The existing
  `plan_go_invalidation_stop` rejects them (`stop_exceeds_go_invalidation_envelope`); the counter `go_invalidation_reaction_anchored_too_wide{strategy}` counts them. A further 384 anchored candidates were already over the maximum.
- **XAU risk per trade grows where the executed stop widens.** On XAU the executed stop is Go's stop. In 294 anchored XAU candidates the old stop was inside the 50-70 pip envelope and the new one is larger; the median added risk is
  about **$5.85 per trade at 0.12 lots** (pip value $10 per lot-pip: demand $7.70, key_level $5.78, order_block $8.33, supply $8.90). The median added distance over all anchored XAU candidates is 10.7-13.1 pips.
- **FX executed stops barely change.** 3,123 anchored FX candidates are still below the instrument minimum, where the 12-22 pip floor sets the executed stop (unchanged). The added technical risk is 1.2-2.0 pips at the median.
- **Descriptive forward path (not an outcome).** The old structural price was traded by one of the next 24 M5 bars in 70.5% of the candidates whose stop sat inside the reaction and 57.7% of the others; with the anchor, the published price is traded in
  55.7% of all candidates (61.0% of the anchored, 52.0% of the rest).

## Observability

`go_invalidation_reaction_anchored{strategy}` (every Go-origin plan whose envelope carries the evidence code and reaches stop planning) and `go_invalidation_reaction_anchored_too_wide{strategy}`
(the same, when the stop is rejected for exceeding the envelope maximum) in `auto_trade:metrics:{SYMBOL}`; see `docs/redis-contract.md`. A counter failure is logged and never blocks a plan.

## Goldens regenerated (each a consequence of the new invalidation only)

| File | Reason | Diff |
|---|---|---|
| `analysis-engine/testdata/replay-go-supply-demand-confirmed-envelopes-xau-20260921.jsonl` and `replay-go-xau-20260921.meta.json` | the confirmed supply/demand envelopes carry the anchored invalidation, the evidence code and the geometry-derived `room_reward_risk`; opportunity ids change with the strategy version | 173 of 173 rows change id/event id (version bump); matched by strategy, direction, time and zone, 44 rows change invalidation (all further out), evidence and `room_reward_risk`; entry, targets, direction, timeframe and expiry are identical in all 173 |
| `analysis-engine/testdata/confluence-zone-other-strategies-baseline-master-f18179f.json` and `analysis-engine/testdata/confluence-zone-v3-replay-results.json` | the hash of the other strategies' candidates includes the seven strategies' invalidation (and their ids) | 6 of 6 captures: the hash changes, the candidate counts are identical |

No parity golden changed (`test/keylevelparity`, `test/techniqueparity`, `test/legacyparity`, `test/detectorparity` pass untouched), and no causality test changed.

## Deferred (not in this change)

- **Post-confirmation failure check** (a setup whose price has already traded through its invalidation after the confirmation bar): a new rejection, needs owner approval. Only its frequency is reported above (forward path).
- **Target selection**: nearest pool at least 1 ATR away, including already-swept pools; median technical reward:risk of supply/demand is 1.0.
- **Zone definitions**: order-block width is body-only; the supply/demand base is 3 bars including wicks; `minimum_zone_atr`, `minimum_gap_atr` and `minimum_strength` apply only to resting candidates.
- **Key Level level universe**: M5-only micro swings, role always `ambiguous`.

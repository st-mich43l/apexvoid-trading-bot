# order_block v6: a block is a block until a close breaks it

> Since order_block v7 the definition (rules A and B, their wiring and the `ob_definition_refused` counter) lives in the strategy package, see [order_block](order_block.md); rule C (the engine withdrawing a published block) is unchanged and still reads `strategies.order_block.confirmed_break_buffer_atr`. The text below is the v6 record.

Branch `agent/order-block-definition`, base `master` at `f02d62f9`. Go engine only, plus one new lifecycle reason code. No stop budget, ladder, sizing, enablement, arbitration, quality model, `min_confluence`, `maximum_entry_atr` or `expiry_hours` changed, and `analysis.techniques.*`, `ProductionTechniqueSettings` and `internal/zone` are untouched. It mirrors the fvg v6 pattern ([fvg-definition.md](fvg-definition.md)).

## 1. Evidence (production, 28-29 Oct 2026, XAU M5)

| trade | block | what the candles show | result |
|---|---|---|---|
| BUY filled 28 Oct 23:51 | demand 4052.81-4057.62, origin 19:35 | eight M5 closes below the low between 22:45 and 23:40 (lowest 4052.09), each inside the old tolerance `max(eps, 0.5 x median ATR)`, so no break episode ever opened; entry 1.18 ATR from the zone | -30 pips |
| SELL filled 29 Oct 01:47 | supply 4053.48-4056.43, origin 23:50 | closes above the high at 01:15 (4056.83), 01:20 (4065.80), 01:25; back inside within 6 bars, so `notInvalidated` forgave it as a sweep; the 01:40 rejection traded | -51 pips |
| SELL filled 28 Oct 19:49 | supply 4061.65-4064.67, origin 27 Oct 17:30 | 6 touches, closes above the high at 00:00 (4065.36), 00:05 and 00:10; published 01:20, `waiting_retest` for 17 h; Go retires an opportunity only on a close beyond the STOP or after 24 h | -48 pips |
| BUY filled 28 Oct 18:31 (EURUSD) | demand 1.11324-1.11353, origin 15:45 | **one** close below the low, 18:20 (1.11288, 0.95 ATR), back inside at 18:25 | +24 pips |

## 2. The definition (order blocks only, every timeframe, judged on the block's own frame bars)

- **A. Close-break retirement.** `TechniqueSettings.OBBreakBufferATR` (0 = off). An order block instance is refused (`ob_closed_through`) if ANY close from `OriginIndex+1` to the frame's last closed bar is beyond its far edge (demand: below the low, supply: above the high; the structural edge when the entry band was clipped) by more than `OBBreakBufferATR` x the frame's ATR AT THAT BAR (`atrSeries[i]`, the scalar only where the series is missing). No reclaim forgiveness and no episode budget; a wick that closes back inside is the sweep and retires nothing. Judged last in `validateInstanceWithATR`, next to the fvg definition. `notInvalidated` is untouched, so `supply_demand`, `fvg` and `ifvg` are byte-identical (pinned by `test/techniquezone` and by `OthersSHA` in every measured variant).
- **B. Touches.** `maximum_zone_touches: 2` on `strategies.order_block` (the existing key, `TechniqueSource.publish`).
- **C. Published-opportunity retirement.** `Book.InvalidateByStructureClose`, called from the worker next to `InvalidateByClose`: a live `order_block` opportunity whose candidate carries `Structure {timeframe, low, high}` is invalidated with the new reason code **`STRUCTURE_CLOSED_THROUGH`** by the first close OF THAT STRUCTURE'S TIMEFRAME (an H1 block by an H1 close, never an M5 one) beyond the structural far edge by `confirmed_break_buffer_atr` x that frame's ATR at the bar. An opportunity published before the structure block existed has none and is not touched.
- **D. Wiring.** `strategies.order_block.confirmed_break_buffer_atr`, injected by `zoneDefinitionTechnique` (the former `fvgDefinitionTechnique`, now applying both the FVG and the OB keys) into `techniqueInstanceSource` and `higherTimeframeInstanceSource` only. `structuralInstanceSource` (Confluence Zone facts) and the zone book are unchanged. `engine.WithoutOBDefinition` removes the buffer and the touch limit; `replaycapture.Options.WithoutStopBudget` implies it (and `WithoutOBDefinition` applies it alone), so the frozen-Python parity replays are unchanged.
- **E.** `order_block` `v5` -> `v6`. Opportunity ids change once at deploy (the version is part of the id; the thesis id does not).

Shipped: `confirmed_break_buffer_atr: 0.1`, `maximum_zone_touches: 2`.

## 3. Measurement (required before choosing the buffer)

`test/obdefinition/measure_test.go` (`OB_DEFINITION_OUT=<dir>`) replays the 21 committed M5 captures fvg v6 used plus the two production windows (`testdata/obwindow-xau-20261029.json`, `obwindow-eurusd-20261029.json`: Redis `bars:{XAU,EURUSD}:{M5,M15,H1}`, closed bars only, captured 29 Oct 06:36 UTC) once per variant, with the engine as configured except for the order block definition. Unit: one confirmed `order_block` opportunity per id, first sight across overlapping captures (1,315 in the frozen behaviour). The break is re-derived from the capture's own bars, independently of the engine, and every refusal is explained by it or by the touch limit (no `other_selection`). Result: [order-block-definition-results.json](order-block-definition-results.json).

| buffer (ATR) | touches | published | refused: closed through | refused: touches | published, then withdrawn by rule C | newly reachable | 3 losers refused or withdrawn before their fill | EURUSD winner still publishes |
|---:|---|---:|---:|---:|---:|---:|---|---|
| 0.00 | off | 1,315 | 0 | 0 | 0 | 0 | **no** | yes |
| 0.00 | 2 | 817 | 0 | 498 | 0 | 6 | **no** | yes |
| 0.10 | off | 609 | 706 | 0 | 265 | 9 | yes | **no** |
| 0.10 | 2 | 473 | 706 | 136 | 200 | 8 | yes | **no** |
| 0.25 | off | 756 | 559 | 0 | 305 | 6 | yes | **no** |
| 0.25 | 2 | 560 | 559 | 196 | 217 | 7 | yes | **no** |
| 1.00 | off | 1,089 | 226 | 0 | 91 | 2 | **no** | yes |
| 1.00 | 2 | 726 | 226 | 363 | 62 | 6 | **no** | yes |

Per symbol and structure timeframe at the chosen setting (0.10 / 2):

| | frozen | published | refused: closed through | refused: touches | withdrawn by rule C |
|---|---:|---:|---:|---:|---:|
| EURUSD M5 | 227 | 52 | 149 | 26 | 21 |
| EURUSD M15 | 113 | 66 | 29 | 18 | 26 |
| GBPJPY M5 | 186 | 49 | 124 | 13 | 35 |
| GBPJPY M15 | 63 | 25 | 38 | 0 | 5 |
| GBPJPY H1 | 14 | 14 | 0 | 0 | 6 |
| GBPUSD M5 | 166 | 39 | 116 | 11 | 24 |
| GBPUSD M15 | 133 | 84 | 28 | 21 | 26 |
| GBPUSD H1 | 1 | 1 | 0 | 0 | 0 |
| USDJPY M5 | 136 | 45 | 69 | 22 | 22 |
| USDJPY M15 | 108 | 46 | 55 | 7 | 14 |
| USDJPY H1 | 23 | 10 | 13 | 0 | 2 |
| XAU M5 | 109 | 26 | 68 | 15 | 10 |
| XAU M15 | 32 | 14 | 15 | 3 | 9 |
| XAU H1 | 4 | 2 | 2 | 0 | 0 |

Fate of the four reference trades at the chosen setting (0.10 / 2):

| trade | fate |
|---|---|
| XAU SELL 28 Oct 19:49 (published 01:20, waited 17 h) -48 pips | published 27 19:30, withdrawn 28 00:00 before the fill 28 19:49 |
| XAU BUY 28 Oct 23:51 -30 pips | never published before the fill |
| XAU SELL 29 Oct 01:47 -51 pips | never published before the fill |
| EURUSD BUY 28 Oct 18:31 +24 pips | never published before the fill |

### The pass criteria conflict, and what was shipped

The brief's criteria are (1) the three losers refused or retired before their fill and (2) the EURUSD 18:31 BUY still publishes, and it says to ship the smallest buffer that passes. **No buffer in the grid satisfies both.**

- 0.10 and 0.25 fix all three losers and retire the winner: its own 18:20 M5 close is 3.6 pips = **0.95 ATR below its low** (it was reclaimed one bar later, which is what "no reclaim forgiveness" removes).
- Only a buffer above 0.95 ATR keeps the winner, and at 1.0 two losers come back (the BUY, whose deepest close is 0.37 ATR below its low, and the 28 Oct SELL):

| trade | fate at 1.0 / 2 |
|---|---|
| XAU SELL 28 Oct 19:49 (published 01:20, waited 17 h) -48 pips | published 27 19:30 and still live at the fill 28 19:49 |
| XAU BUY 28 Oct 23:51 -30 pips | published 28 23:45 and still live at the fill 28 23:51 |
| XAU SELL 29 Oct 01:47 -51 pips | never published before the fill |
| EURUSD BUY 28 Oct 18:31 +24 pips | published 28 18:25 and still live at the fill 28 18:31 |

So the shipped setting is the smallest buffer that satisfies the safety criterion (the losers): **0.10**. It does not satisfy criterion 2, and that needs an owner decision, not a quiet choice. The data point to a different rule, not to a different buffer: the winner had exactly one close beyond its edge (18:20), the losers had 6 (BUY, in 4 runs), 4 (28 Oct SELL, 2 runs) and 3 (29 Oct SELL, consecutive). A rule that retires a block after a SECOND close beyond the edge would keep the winner and retire all three losers on these four trades. That is the episode budget the brief rules out, so it is NOT implemented here, and it has not been measured on the full sample.

Rule A also retires the SELL of 29 Oct at 01:20, not at 01:15: the 01:15 close is 0.40 above the high, below 0.1 ATR (ATR 5.17 = 0.52). The brief's "retired at 01:15" holds for a buffer under 0.077 ATR; `test/obdefinition` pins both numbers.

### Reported, not gated

- Confirmation type of the frozen candidates: FX wick_rejection 223, sweep_reclaim 407, strong_reclaim 378, engulfing 149, rejection_choch 13; XAU wick_rejection 23, sweep_reclaim 66, strong_reclaim 44, engulfing 11, rejection_choch 1.
- Entry distance from the entry band, in ATR, of the frozen candidates: median 0, p90 0.45, maximum 1.73. `maximum_entry_atr` stays at the shared 2.0. **`strategies.order_block` cannot override it**: `applyLegacyDetector` copies the shared `LegacyDetector` values over every detector-contract strategy's parameters after reading them, so a per-strategy `maximum_entry_atr` would be overwritten.
- The sample cannot validate expectancy. This is a definition fix: a block that a close has broken is not a block.

## 4. Observability

`ob_definition_refused` (symbol, structure timeframe, reason `ob_closed_through`), once per instance per evaluation, through the engine's refusal ledger (fvg v7 reports its own refusals as `strategy_refused`). `go_opportunity_structure_closed_through` (symbol, structure timeframe, strategy id in the reason label) on rule C. Neither blocks an evaluation. `engine.Settings.OnWithdrawn` / `replaycapture.Options.OnWithdrawn` expose rule C to replays.

## 5. Contract and Python

The new terminal reason code is `STRUCTURE_CLOSED_THROUGH` on `analysis.opportunity.invalidated.v1` (`reason_code` is a free machine-readable string in `contracts/analysis/opportunity-invalidated-v1.schema.json`). **Verified in Algo Bot: no Python change and no Python-first deploy is needed.** The envelope accepts any non-empty `reason_code`; `go_opportunity_policy` treats every code other than `SETUP_EXPIRED` as `INVALIDATED`, withdraws the match, advances the setup to the terminal state and writes the plan-cancel tombstone, so a withdrawn id is never filled. Pinned by `tests/test_analysis_lifecycle.py` and `tests/test_terminal_attribution.py`.

## 6. Parity and goldens

The frozen-Python parity replays must not see the definition: `engine.WithoutOBDefinition` removes `confirmed_break_buffer_atr` and `maximum_zone_touches`, and `replaycapture.Options.WithoutStopBudget` (set by every parity replay) applies it. `test/techniqueparity`, `legacyparity`, `detectorparity` and `keylevelparity` pass **without regenerating any parity golden**, with `-timeout 60m`.

Tests that now replay without the definition because they certify something else (they read the engine's technique instances or isolate stop geometry): `test/czreplay` (frozen v2 reproduction), `test/htfzones`, `test/reactionstop` (regression and measurement) and `test/stopbudget`.

Non-parity goldens that moved (regenerated, reviewed, nothing else changed):

| golden | before | after | cause |
|---|---|---|---|
| `testdata/replay-go-xau-20260921.meta.json` (`TestRealCaptureReplayIsDeterministicAndMatchesTheCommittedGolden`) | 382 discovered opportunities, envelope hash `290d7a2c...` | 367 (-15, -3.9%), hash `eaa886cb...` | the order block definition (the 12 golden supply/demand confirmed envelopes are unchanged) |
| `testdata/confluence-zone-other-strategies-baseline-master-f18179f.json` and `docs/strategies/confluence-zone-v3-replay-results.json` (six captures) | the counts 1,518 / 3,110 / 4,405 / 6,003 / 4,436 / 5,954 | **the same counts**, six new hashes | the opportunity id of `order_block` carries the strategy version (v5 to v6); `test/czreplay` replays without the definition, so no candidate is added or removed |

New fixtures: `testdata/obwindow-xau-20261029.json` and `obwindow-eurusd-20261029.json` (the production Redis windows above; named so that the `replay-*-production-capture-*` globs of the other measurement tests do not pick them up).

## 7. Deploy and rollback

`strategies.order_block.*` is not rendered from `st-mich43l/ansible-library`: its `analysis.yml` group_vars carry no `strategies` block, so the new keys ride with the image's `config/analysis.yml` (checked against `origin/master` of ansible-library; the `config-compiler` itself was not run). Opportunity ids change once at deploy. Rollback: set `confirmed_break_buffer_atr` to 0 and drop `maximum_zone_touches` (v6 then publishes what v5 did, with ids still changed), or revert.

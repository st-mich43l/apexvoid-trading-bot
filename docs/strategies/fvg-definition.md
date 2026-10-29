# A real fair value gap on every timeframe, and a root card that shows which gap (fvg v6)

## 1. The owner report (2026-10-28) and what the replay shows

Trades were labelled FVG where the chart shows no fair value gap. Since #812 (`docs/strategies/htf-zone-structure.md`) `fvg` reads M5, M15 and H1 gaps (XAU, 21 captures: 79 distinct M15 gaps, 6 H1, 29 M5), and the card never said which. The confirmed path (`strategyutil.ConfirmedTechnique` -> `techniquezone.validateInstance`) only required a gap of at least 1 pip, a width of at most `FVGMaxATR`, and not fully filled. Measured against the ATR of the gap's own timeframe (XAU, median):

| timeframe | gap | share below 0.5 ATR |
|---|---:|---:|
| M5 | 34 pips | 10% |
| M15 | 21 pips | 68% |
| H1 | 24 pips | 100% (83% had a middle body below 0.5 ATR too) |

The card showed the execution zone (the proximal slice, widened or clipped), not the gap, so a 4 pip M15 gap printed as a 40 pip "FVG".

This is an approved definition fix, not an outcome-tuned filter: the thresholds are the iFVG v3 gap contract (0.5 / 0.5 / 0.5), nothing was fitted to results.

## 2. The definition

A fair value gap is judged on **the bars and ATR of the frame it comes from**, as of the candle that completed it (`frame.ATR[origin]`, the third candle; the scalar ATR when the series is not aligned; causal), and on its **structural** bounds when the entry was clipped. It is a gap only when all of these hold:

| key (`strategies.fvg`) | value | refusal reason |
|---|---:|---|
| `confirmed_minimum_gap_atr` | 0.5 | `gap_small`: width below 0.5 of the frame's ATR |
| `confirmed_minimum_displacement_body_atr` | 0.5 | `displacement_weak`: middle-candle body below 0.5 ATR |
| `confirmed_minimum_displacement_body_ratio` | 0.5 | `displacement_weak`: body below half of the middle candle's range |
| (direction, judged when either body key is set) | | `displacement_direction`: the middle candle did not close in the gap's direction |

Order of the checks: `gap_small`, `displacement_weak`, `displacement_direction`. A value of 0 switches a condition off, which is what the frozen-Python parity replays and every setting that does not name the keys get. Same values for every instrument.

Where it applies: the `fvg` strategy's two instance sources only (`techniqueInstanceSource` for the execution frame, `higherTimeframeInstanceSource` for M15 and H1), through the engine injecting the three values into the technique settings those sources use (`engine.fvgDefinitionTechnique`). The shared technique settings, the zone book, `structuralInstanceSource`, iFVG and Confluence Zone facts do not see it. Every other check (width cap, fully filled, proximity) runs first and a gap it refuses is never counted as a definition refusal.

## 3. Counter

`telemetry.CounterFVGDefinitionRefused` (`fvg_definition_refused`), labels `symbol`, structure timeframe and `reason` (`gap_small`, `displacement_weak`, `displacement_direction`). Once per instance per evaluation, recorded in `engine/worker.go` after the strategies ran (a per-evaluation ledger drained by the worker); a counter never blocks an evaluation. It is a Go counter, so it is not listed in `docs/redis-contract.md`.

## 4. What it does to the published set

`analysis-engine/test/fvgdefinition/measure_test.go` (`FVG_DEFINITION_OUT=<dir>`) replays all 21 committed M5 captures twice with the engine as configured, with the definition removed (what v5 published) and with it. Unit: one confirmed `fvg` candidate per opportunity id, first sight across overlapping captures. The gap, its middle candle and its ATR are re-derived from the capture's own bars of the gap's timeframe, independently of the engine, and the reason is judged in the engine's order. Result: `fvg-definition-results.json` (written by the replay, not committed).

| class, timeframe | v5 candidates | distinct gaps | refused | gap_small | displacement_weak | displacement_direction | still published | newly reachable |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| XAU M5 | 37 | 29 | 4 (11%) | 3 | 1 | 0 | 33 | 0 |
| XAU M15 | 120 | 79 | 75 (63%) | 73 | 2 | 0 | 45 | 2 |
| XAU H1 | 11 | 6 | 11 (100%) | 11 | 0 | 0 | 0 | 0 |
| FX M5 | 266 | 153 | 36 (14%) | 29 | 7 | 0 | 230 | 0 |
| FX M15 | 680 | 290 | 251 (37%) | 224 | 24 | 3 | 429 | 7 |
| FX H1 | 152 | 56 | 76 (50%) | 74 | 2 | 0 | 76 | 2 |
| **all** | **1,266** | **613** | **453 (36%)** | **414** | **36** | **3** | **813** | **11** |

Own-timeframe medians of the v5 population (p10 / median / p90, in ATR): gap XAU M15 0.08 / 0.43 / 0.82, XAU H1 0.04 / 0.22 / 0.24, XAU M5 0.57 / 1.02 / 1.62; middle body XAU M15 0.44 / 0.80 / 1.50. 271 of the 613 distinct gaps never trade again.

- On XAU, **75 of 120 M15 candidates (63%)** and **all 11 H1 candidates** are not gaps by the definition; M5 is hardly touched (4 of 37).
- Invariants asserted by the test, per evaluated bar: every candidate the definition removes is a gap the (independent) arithmetic refuses; every candidate it publishes passes it; a candidate published both ways has the identical entry band, invalidation and target (the definition never moves a geometry); **no strategy other than `fvg` publishes a different candidate** (82,355 candidates checked); every confirmed candidate of the zone family carries a structure block and no other strategy does.
- **Newly reachable: 11 opportunities (57 sightings)** are published only with the definition: `fvg` picks one best instance per evaluation, and a refused gap that used to win no longer does, so the next real gap is reached. They are real gaps by construction and are not part of the 1,266.

Real-case regression (`test/fvgdefinition/regression_test.go`, `replay-xau-production-capture-20261022.json`; the bar is the first M5 bar the candidate is published on, UTC; found by replaying with and without the definition and listing every confirmed `fvg` candidate with its gap in own-timeframe ATR):

| setup | v5 | v6 |
|---|---|---|
| SELL M15 4,162.73-4,164.80, bar 2026-10-14T06:10 (gap 0.37 M15 ATR) | published | **refused** (`gap_small`) |
| BUY M15 4,157.26-4,157.99, bar 2026-10-14T00:30 (0.22 M15 ATR) | published | **refused** (`gap_small`) |
| BUY H1 4,154.82-4,157.82, bar 2026-10-15T23:55 (middle body 0.31 H1 ATR, gap 0.22) | published | **refused** |
| BUY M15 4,170.78-4,175.77, bar 2026-10-16T04:55 (0.60 ATR) | published | published, same entry, invalidation and target |
| BUY M5 4,157.52-4,160.65, bar 2026-10-14T00:25 (1.46 ATR) | published | published, same entry, invalidation and target |

A sixth case shows the card change: the M5 gap of 14 Oct 00:00 is 4,151.62-4,157.55 but is ordered on the clipped 4,152.55-4,157.55.

## 5. The contract and the card (display only)

Go publishes an optional `structure: {timeframe, low, high, formed_at}` on the candidate and on `analysis.opportunity.v1` (`contracts/analysis/opportunity-v1.schema.json`, `kafka.StructurePayload`) for the zone family (`supply`, `demand`, `order_block`, `fvg`), on every timeframe including M5: the whole structure on its own timeframe (the whole gap, not the clipped entry band) and `formed_at`, the open time of the bar that completed it (the third candle of a gap; for a higher-timeframe structure that frame's bar time, carried by `techniquezone.Instance.OriginTime`). `opportunity.Candidate.Validate` rejects an invalid timeframe or an inverted range. Documented in `docs/redis-contract.md`.

Python (`app/analysis_client/models.py` `StructureZone`, optional so old events still parse) copies it to `StrategyMatch` (`structure_timeframe/low/high/formed_at`, optional) and the root card (`app/autotrade/setup_card.structure_lines`) prints one line under the setup name:

```
📈 BUY · FVG · ⭐⭐
Structure: FVG M15 · 4,152.30–4,154.40 · formed 14:15 UTC
Order zone: 4,153.60–4,154.40
```

The name is the card's own setup label (`FVG`, `Supply` for a SELL, `Demand` for a BUY, `Order Block`), prices use the instrument's card digits, and `Order zone` appears only when the zone the plan trades differs from the structure. An event, cached match or card without the block renders exactly as before. Display only: strategy identity, stats keys, policy and results do not read it, and an already-published card still matches its setup (`forming_card_matches_strategy`, #814).

## 6. Parity and goldens

The frozen-Python parity replays must not see the definition: `engine.WithoutFVGDefinition` removes the three keys, `replaycapture.Options.WithoutStopBudget` (set by every parity replay) applies it, and `Options.WithoutFVGDefinition` applies it alone. `test/techniqueparity`, `legacyparity`, `detectorparity` and `keylevelparity` pass **without regenerating any parity golden**, with `-timeout 60m`.

Tests that now replay without the definition, because they certify something else and read the engine's technique instances: `test/czreplay` (the frozen v2 re-implementation of Confluence Zone reproduces the three production trades only from the unfiltered instances, so the Confluence Zone baseline and `confluence-zone-v3-replay-results.json` are unchanged) and `test/htfzones` (isolates the stop budget).

Non-parity goldens that moved (regenerated, reviewed, nothing else changed):

| golden | before | after | cause |
|---|---|---|---|
| `testdata/replay-go-xau-20260921.meta.json` (`TestRealCaptureReplayIsDeterministicAndMatchesTheCommittedGolden`) | 399 discovered opportunities, envelope hash `152a9496...` | 382 (-17, -4.3%), hash `290d7a2c...` | the definition refuses thin `fvg` gaps (a different hash because of it and of the structure block) |
| `testdata/replay-go-supply-demand-confirmed-envelopes-xau-20260921.jsonl` | 12 envelopes | the same 12 envelopes, each with one added field, `payload.structure` (verified: no other field of any envelope differs) | the structure block of the contract |
| `testdata/confluence-zone-other-strategies-baseline-master-f18179f.json`, XAU 21 Sep / XAU 13 Oct / EURUSD / GBPUSD / GBPJPY / USDJPY 13 Oct | 1,518 / 3,121 / 4,406 / 6,007 / 4,439 / 5,957 candidates | **the same counts**, six new hashes | the opportunity id of `fvg` carries the strategy version (v5 to v6); `test/czreplay` replays without the definition, so no candidate is added or removed |
| `analysis-engine/testdata/confluence-zone-v3-replay-results.json` | the six hashes above | the six new ones | derived from the same replays; its Confluence Zone figures are unchanged (only the `other_strategies_candidates_sha256` fields differ) |

That baseline file certifies that the Confluence Zone v3 change did not touch other strategies. Like the earlier Key Level changes, a deliberate change to a strategy's identity moves its hashes, and the new values are the baseline from here on.

## 7. Deferred (owner decisions)

- **iFVG on M15 and H1.** iFVG keeps its own gap contract on the execution frame; whether it should read higher-timeframe gaps is not decided here.
- **Python's zone widening and its deeper risk-targeted XAU entry.** Not touched; the card now shows both the structure and the order zone, which makes the difference visible.
- **The proximal clip.** The execution zone is the proximal slice of a wide gap (`entry_max_width_price`, 5.0 for XAU); this change shows it on the card and does not move it.
- **FX thresholds.** The gap contract is the same for every instrument; the FX shares above (M15 37%, H1 50%) are the measurement for deciding otherwise.

## 8. Release

**Deploy the Python bot first (or together with Go).** The Python envelope models forbid unknown fields (`FrozenConfigModel`, `extra="forbid"`), so a bot without this change would reject every zone-family event that carries the new `structure` block; the new bot parses events with and without it, so Python-first is safe in both orders of rollout. Then the production Go engine. Opportunity ids change once at deploy (the strategy version is part of the id; the thesis id does not). If `config/analysis.yml` is rendered from `st-mich43l/ansible-library`, the three leaves are `strategies.fvg.confirmed_minimum_gap_atr`, `confirmed_minimum_displacement_body_atr` and `confirmed_minimum_displacement_body_ratio` (that repository was not edited). Rollback: set the three keys to `0` (the definition is off, v6 then publishes what v5 did, with the structure block still attached) and restart the analysis engine.

# Legacy strategies respect the instrument stop budget

Owner request, 2026-10-24: "my style is using stop from 50 to 70 pips only, make the AE work on this range" (XAU `stop_envelope` 50-70 pips, `config/instruments.yml`).

**Change.** `key_level`, `supply`, `demand`, `order_block`, `fvg`, `flip_zone` and `trendline` now apply the instrument stop envelope inside the detector, the way CRT, Break & Retest, iFVG and Confluence Zone already do.
The engine injects the resolved envelope per instrument (`applyLegacyStopBudget`: `execution_stop_max_pips`, `execution_stop_min_pips`, `execution_pip_size`); one helper, `strategyutil.StopBudget.Apply`, is called from
`TechniqueCandidate` and `keylevel.(*Strategy).candidate` with the proximal entry edge and the (reaction-anchored) invalidation they already computed:

| structural risk = distance from the proximal entry edge to the invalidation | result | evidence code |
|---|---|---|
| greater than the cap (equal publishes) | **no candidate** ("refused, never tightened") | - |
| below the floor | published as it is; execution widens it to the floor | `stop_budget_floor` |
| inside [floor, cap] | published as it is | `stop_budget_structural` |
| no budget configured | as before | none |

Neither the entry nor the invalidation is ever moved to make a setup fit. A resting candidate has no confirmation and is not budgeted. The pip size travels under its own key (`execution_pip_size`) because `pip_size` feeds the technique geometry:
the budget must not change which zones a strategy decides on. Versions: key_level v5, supply v5, demand v5, order_block v4, fvg v4, flip_zone v5, trendline v6.

Generic mechanism: it applies on every instrument with that instrument's own envelope (FX caps 20 / 25 / 28 / 35), the same one the native strategies use on FX. There is no FX-specific switch. Out of scope and untouched: Python stop logic,
`risk_targeted_entry_price`, `widen_tight_xau_zone`, the XAU risk leg, scale-in legs, ladder, floors, caps, sizing, enablement, arbitration, quality, `min_confluence`, native strategies.

## Measurement

`analysis-engine/test/stopbudget/measure_test.go` (`STOP_BUDGET_OUT=<dir> go test ./test/stopbudget -run TestMeasureStopBudget`) replays the 21 committed M5 captures twice, without the budget (what is published today, reaction-anchored stop included) and with it.
It classifies every confirmed candidate (12,080 distinct opportunities, the same set as the reaction-anchored-stop study) and asserts that the budgeted engine publishes exactly the candidates that are not refused, with the same entry, the same invalidation and the matching
evidence code. Output: [legacy-stop-budget-results.json](legacy-stop-budget-results.json). Risk is the published (tick-rounded) distance; the detector decides on the unrounded one, so a candidate within one tick of a bound can read either way.
Nothing here is an outcome and nothing is tuned.

### XAU (cap 70, floor 50)

| | n | refused (> cap) | structural (in budget) | floor (< min) | median pips refused | median pips structural | median pips floor |
|---|---:|---:|---:|---:|---:|---:|---:|
| key_level | 1,697 | 660 (39%) | 776 (46%) | 261 (15%) | 87 | 59.5 | 44.9 |
| supply | 400 | 343 (86%) | 42 (11%) | 15 (4%) | 115.7 | 62.5 | 44.8 |
| demand | 425 | 374 (88%) | 39 (9%) | 12 (3%) | 123.6 | 62.3 | 46.5 |
| order_block | 146 | 63 (43%) | 47 (32%) | 36 (25%) | 85.7 | 61 | 42.2 |
| fvg | 52 | 15 (29%) | 23 (44%) | 14 (27%) | 80.3 | 58.4 | 43.3 |
| flip_zone | 11 | 3 (27%) | 4 (36%) | 4 (36%) | 72 | 68.5 | 32 |
| trendline | 3 | 0 (0%) | 0 (0%) | 3 (100%) | - | - | 36 |
| **XAU total** | 2,734 | 1,458 (53%) | 931 (34%) | 345 (13%) | 98.3 | 59.8 | 44.7 |

### FX (EURUSD 20, GBPUSD 25, GBPJPY 35, USDJPY 28)

| | n | refused (> cap) | structural (in budget) | floor (< min) | median pips refused | median pips structural | median pips floor |
|---|---:|---:|---:|---:|---:|---:|---:|
| key_level | 4,289 | 15 (0%) | 110 (3%) | 4,164 (97%) | 33.1 | 17.7 | 6.5 |
| supply | 1,937 | 105 (5%) | 458 (24%) | 1,374 (71%) | 35.9 | 22.1 | 10.2 |
| demand | 2,113 | 260 (12%) | 384 (18%) | 1,469 (70%) | 36.1 | 20.3 | 9.2 |
| order_block | 694 | 0 (0%) | 15 (2%) | 679 (98%) | - | 21.9 | 6.7 |
| fvg | 267 | 1 (0%) | 0 (0%) | 266 (100%) | 30.9 | - | 5.6 |
| flip_zone | 28 | 0 (0%) | 0 (0%) | 28 (100%) | - | - | 5.1 |
| trendline | 18 | 0 (0%) | 0 (0%) | 18 (100%) | - | - | 2.7 |
| **FX total** | 9,346 | 381 (4%) | 967 (10%) | 7,998 (86%) | 35.8 | 20.4 | 7.3 |

By instrument:

| | n | refused (> cap) | structural (in budget) | floor (< min) | median pips refused | median pips structural | median pips floor |
|---|---:|---:|---:|---:|---:|---:|---:|
| EURUSD (12-20) | 2,519 | 65 (3%) | 225 (9%) | 2,229 (88%) | 31.7 | 13.5 | 5.7 |
| GBPUSD (15-25) | 2,839 | 25 (1%) | 174 (6%) | 2,640 (93%) | 38.1 | 16.9 | 6.8 |
| GBPJPY (22-35) | 1,144 | 119 (10%) | 300 (26%) | 725 (63%) | 44.9 | 26.4 | 12.7 |
| USDJPY (18-28) | 2,844 | 172 (6%) | 268 (9%) | 2,404 (85%) | 31.4 | 20.8 | 9 |

13 candidates were over the cap at their first evaluation and inside the budget at a later bar (the ATR, hence the buffer, moved): they are published then and counted as structural / floor above.

## The behaviour change the owner is approving

- **XAU: 1,458 of 2,734 published candidates (53%) are refused**; 931 (34%) are structural and 345 (13%) are below the floor. Supply and demand are almost all refused
  (86% / 88%; median honest stop 116 / 124 pips: M5 zones are wide); Key Level 39%.
- **Of those 1,458 refused XAU candidates, Python already rejects 1,282 today** (`stop_exceeds_go_invalidation_envelope`, evaluated with the real `evaluate_execution_policy` and the production configuration, at the price of the evaluated bar).
  **176 (all Key Level) are admitted today** because the price is already inside the zone and the entry is placed deeper than the proximal edge: median 16.7 pips deeper, median planned stop 62.1 pips.
  Those 176 are the size of the behaviour change on XAU: they will no longer be traded at all. That is consistent with Q5.5 (production entering about 16 pips deeper than the replay).
- **FX: 381 of 9,346 (4%) refused**, mostly demand (260) and supply (105). Python rejects 166 of them today and **admits 215**
  (median 11.4 pips deeper than the proximal edge, median stop 22.0 pips): those 215 are the FX behaviour change. 7,998 FX candidates (86%) are below the floor and are published as before.
- The refused candidates are the ones the replay study found negative (-0.11 R above random, 419 setups); the within-budget group was +0.19 to +0.22 R, an in-sample, one-regime, small-cell result, **a reason to build the budget, not a measured edge**.

## Observability

Go: `telemetry.CounterStrategyStopBudgetRefused` (`strategy_stop_budget_refused`, labels symbol and timeframe) is recorded after every strategy evaluation. The native strategies expose their `ReasonRiskEnvelope` refusals only through their
replay analysis, so there was no existing per-strategy counter to follow; `strategy.Evaluation.StopBudgetRefused` additionally carries the per-strategy split (drained from each strategy through `strategy.StopBudgetReporter`) for replay tools.
Python: `go_stop_budget_structural` and `go_stop_budget_floor` (dimension `strategy`) in `auto_trade:metrics:{SYMBOL}`, see `docs/redis-contract.md`.

## Parity

The parity replays prove detection against a frozen Python publisher that had no stop budget. They run with `replaycapture.Options.WithoutStopBudget` (`engine.WithoutLegacyStopBudget`), exactly as an engine with no resolved instrument envelope runs, so a refused decision is
never mistaken for a detection difference. `test/keylevelparity` failed without it (XAU decisions refused above 70 pips); with it, `keylevelparity`, `techniqueparity`, `legacyparity` and `detectorparity` pass and **no parity golden was regenerated**.

## Goldens regenerated (each a consequence of the budget only)

| File | Reason | Diff |
|---|---|---|
| `analysis-engine/testdata/replay-go-supply-demand-confirmed-envelopes-xau-20260921.jsonl` + `replay-go-xau-20260921.meta.json` | the confirmed XAU supply/demand envelopes of the September capture are budgeted: those over 70 pips are no longer published, the others carry a budget code; ids change with the version | 173 rows before, 12 after: 161 rows removed (all with a structural stop of 71.6-644.6 pips from the proximal edge); the 12 kept rows differ only by the `stop_budget_structural` (9) / `stop_budget_floor` (3) evidence code and the id; entry, invalidation, targets, quality, direction and expiry are identical |
| `analysis-engine/testdata/confluence-zone-other-strategies-baseline-master-f18179f.json` + `docs/strategies/confluence-zone-v3-replay-results.json` | the other strategies' candidate set now excludes the refused legacy candidates and carries the budget codes | 6 of 6 captures: candidate count and hash change (4050 to 3288, 2185 to 1612, 4217 to 4189, 5591 to 5552, 4193 to 4102, 5801 to 5613); no confluence_zone field changes |

No parity golden changed. `test/keylevelparity`, `test/techniqueparity`, `test/legacyparity` and `test/detectorparity` pass untouched with `WithoutStopBudget`; the causality and prefix-invariance suites pass unchanged.

## Deferred (not in this change)

- **Python's deeper risk-targeted entry and the zone widening for XAU** (`execution.yml` / `instruments.yml`): entries placed deeper than the proximal edge earn less (Q5.5: production entered 16 pips deeper, +0.36 R against the replay's +0.56 R). Owner decision.
- **Entry at the M5 confirmation** (market at the confirming close) versus the resting zone ladder: the study's best cell used the confirmation close. An execution-route change for a later PR.
- **M15/H1 export of order block, FVG and iFVG** (timeframe research, step 3): with the budget in place the right structure scale will be selected once those exist.
- **Stop-distance sizing**: with `equity_table` sizing a 70-pip stop risks 40% more dollars than a 50-pip one at the same lots.

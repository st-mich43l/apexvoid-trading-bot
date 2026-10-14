# Strategy retirement and restriction review (2026-10-14)

Companion to [ae-quality-certification.md](ae-quality-certification.md). It turns that
audit's verdicts into proposals an owner can approve one at a time. **Nothing here is
applied.** No enablement, containment, stop, target, size or deletion has changed; the patches
below are prepared and only checked with `git apply --check`.

## Rules used to decide

A strategy is judged on four separate things: technical correctness, execution
compatibility, observed performance and the strength of that evidence.

| Outcome | When |
|---|---|
| RETAIN | Detector is sound and nothing shows harm. Not a claim of profit. |
| RETAIN_RESTRICTED | Sound detector, but on named instruments the hypothetical interval lies wholly below zero (or the geometry cannot pay for the executable stop). Observe-only there, live elsewhere. |
| RECONSTRUCT | The defect is geometry or a published number (target, quality) that can be repaired without changing the thesis. Enablement unchanged meanwhile. |
| OBSERVE_ONLY | Already contained; keep observing, review again. |
| RETIRE | A technical failure that is the thesis itself (not a parameter), together with realised evidence against it. Never a handful of losses alone. |

Not used: any universal minimum `quality.overall` (none is proposed); any change to the P1
global arbitration; any stop widening, target shortening or change to the FX single-entry
contract.

## The retirement procedure

Owner rule (2026-10-14): **retired means removed, not disabled.** A RETIRE outcome deletes, in
one PR the owner can review and revert:

1. the Go package, its registry/factory entry, stop-envelope, instrument-policy and MAD entries;
2. its `config/analysis.yml` entry and every config leaf only it read;
3. the Python catalog id, scope, display name, aliases, execution profile and trigger labels;
4. the executor's name lists;
5. its specification, tests, fixtures, parity oracles and generators;
6. every count that said how many strategies there are.

Not removed: rows already in the ledger and fixtures that record production history, and dated
audit documents. Nothing in the code reads the retired name afterwards; an unknown historical
name resolves to nothing and is treated as unknown. There is no observe-only outcome: the owner
ruled it out (2026-10-14), so a strategy is either live or removed.

Before deploying a removal, close any open plan of that strategy: the executor manages a plan by
its strategy name, so a plan open at the moment of the deploy would lose its scalp handling.

## Status

- 2026-10-14: the owner approved retiring Range Sweep (item 1) and set the rule that **a retired
  strategy is removed, not disabled**: code, config leaves, tests, fixtures, oracle data and its
  specification all go. Range Sweep was removed in one PR (#779; replaces the earlier "contain, soak,
  disable" plan). Production-data fixtures
  (`tests/fixtures/exposure_groups_*.json`, the dated audit documents and result files) still
  name it because they record what happened; nothing in the code reads the name.
- A RETIRE outcome always means removal under the same rule.

## Proposals and owner decisions

| # | Strategy | Audit proposal | Owner decision (2026-10-14) |
|---|---|---|---|
| 1 | range_sweep | RETIRE | Approved; removed in #779/#780. |
| 2 | range_edge | observe-only on the four FX pairs | **Rejected: nothing is observe-only.** Stays live everywhere. |
| 3 | confluence_zone | observe-only on XAU, GBPJPY | Rejected, stays live. |
| 4 | order_block | observe-only on EURUSD, GBPJPY | Rejected, stays live. |
| 5 | fvg | observe-only on XAU | Rejected, stays live. |
| 6 | flip_zone | observe-only on XAU | Rejected, stays live. |
| 7 | trendline | RECONSTRUCT | Approved; reworked in v4 (target is the nearest opposing liquidity). |

The owner also set every remaining observe-only entry to live: `impulse_pullback` (all instruments), `liquidity_sweep`
(XAU) and the M15 supply/demand structure timeframe. `config/instruments.yml` now contains no observe-only strategy or
timeframe, and a test pins that. The audit evidence behind rows 2 to 6 is unchanged and stays in
[ae-quality-replay-results.json](ae-quality-replay-results.json); with no observe-only mode the only ways to act on it are
to remove a strategy or leave it live. For the two that were observe-only on negative evidence: `impulse_pullback`'s closed trades
(11 over its history) bound the mean at -0.68R to -0.59R, 95% interval [-1.0, -0.11], and `liquidity_sweep` has 21 trades with no
recorded R. Both now trade live by the owner's decision.

## Reconstruction backlog (no enablement change, separate PRs, each needs approval)

| Item | Defect | Note |
|---|---|---|
| Trendline target and stop | Target is `entry +/- 2 x distance to the band edge`, unrelated to the stop; room cover 0.05 | Rebuild from the line; propose observe-only until then (no production trades exist, so containing it costs nothing). |
| Supply and Demand geometry | Technical RR < 1 in 57% / 62% of setups | Detector is parity-proven; the target must stop being a fixed 1R beyond the zone's own objective. Changes a published number: needs the owner's approval of the rule, not the audit's. |
| Scalp Breakout Retest target | Fixed 1R / 2R labels, not a technical objective | Production is positive (+0.54R on 7 R-known trades), so keep trading it while repairing. |
| Quality formulas | Nine strategies saturate at two values, two are constant | Arbitration-facing: an offline impact report against the P1 evaluation is required before any code change. |
| Executor `realized_pips` | Stop-out loss missing from `volume_weighted_pips` (full stops read 0.0) | A C# fix with tests in `ctrader-engine`; until then every R in this audit counts a full stop as -1R. |
| Flip Zone | 28% fixed-R first targets | Same class as above. |

## Not proposed, deliberately

- No universal minimum quality threshold.
- No change to the P1 global arbitration algorithm or its score inputs.
- No stop widening, target shortening, or change to the FX single-entry contract.
- No deletion of any strategy code, catalog entry, ledger row or journal decoder.
- No change to Key Level, iFVG, CRT, Session Level, Box Breakout, Break & Retest, Momentum Ride,
  Snap Back, Fade Scalp or Liquidity Sweep: sound detectors with no evidence of harm. Fade Scalp
  (replay -0.22R, production +0.99R on 8 trades) and Scalp Breakout Retest are the two where
  replay and production disagree, so the right action is more data, not a decision.

## Catalog now

20 strategies, all live on every instrument they are configured for. Three still await geometry work (Supply, Demand, Scalp
Breakout Retest). Strategies that fire only a few times a month (CRT, Momentum Ride, Snap Back, Liquidity Sweep, Trendline)
are kept: rarity is not a defect. The next audit should be triggered by sample size (30 R-known production trades per
strategy), not by the calendar.

## Open items for the owner

- [ ] Supply and Demand geometry (technical RR below 1 in 57% / 62% of setups).
- [ ] Scalp Breakout Retest and Flip Zone fixed-R first targets.
- [ ] Quality formulas (nine strategies saturate at two values, two are constant): needs an offline arbitration impact report first.
- [ ] The executor `realized_pips` fix is merged (#778); an independent reviewer for this audit is not yet assigned.

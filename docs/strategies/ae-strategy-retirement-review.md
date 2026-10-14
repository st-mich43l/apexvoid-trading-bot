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

Each step is reversible by the one before it, and each needs its own owner approval.

1. **Contain (observe-only).** Add the strategy to the instrument's
   `execution.go_opportunity.observe_only_strategies` in `config/instruments.yml`. The
   strategy keeps publishing, journalling and being measured; no new TradePlan is built.
   Containment is read at match creation and again at plan admission, so it stops *new* plans
   only and does not cancel, modify or close anything already published or open
   (stated in the comment on that list in `config/instruments.yml`; this audit did not
   re-test it). Precedent: `impulse_pullback`, `liquidity_sweep`, and the earlier `ifvg` and
   `session_level` containment. Rollback: remove the name.
2. **Soak.** At least two weeks of observation with the same replay and production export,
   comparing the contained strategy's would-be outcomes. If the evidence reverses, stop here.
3. **Disable publication.** `enabled: false` in `config/analysis.yml`. The registry builds only
   enabled strategies, so it stops publishing. The catalog entry
   (`algo-bot/app/autotrade/strategy_catalog.py`), the provenance decoder
   (`algo-bot/app/analysis_client/provenance.py`) and every ledger row stay, so old trades
   and journals still decode and attribute to the strategy. Mark the strategy's spec
   `RETIRED` and keep it listed.
4. **Delete code (separate PR, later).** Only after the above has soaked and the owner asks.
   Trade data is never deleted.

The requirement that an old event cannot create a new plan after step 1 is **not verified by
this audit**: the config comment says containment is read at match creation and again at plan
admission, which should cover it, but the first retirement PR must add a test that an
opportunity published before containment is refused at admission, and that a journal row of
the retired strategy still decodes.

## Proposals

Every row needs explicit owner approval. Patch A applies step 1 to all restrictions; patch B
is step 3 for Range Sweep only.

| # | Strategy | Proposal | Scope | Evidence (hypothetical R unless stated; 95% interval) | Patch | Reversible |
|---|---|---|---|---|---|---|
| 1 | range_sweep | RETIRE | XAU (its only instrument) | Technical FAIL (see certification section 5). Production exit paths: 26 of 38 closed trades full stops, mean -0.41R to -0.37R, interval [-0.62, -0.11] | A (step 1), then B (step 3) | yes, until step 4 |
| 2 | range_edge | observe-only | EURUSD, GBPUSD, USDJPY, GBPJPY | Technical reward is 0.27-0.30x the 12-22 pip FX scalp stop in the median setup and shorter than it in 99-100% of setups. All-instrument replay -0.09R [-0.21, +0.02], OOS -0.25R [-0.40, -0.10]; production -0.19R [-0.50, +0.17] on 62 trades | A | yes |
| 3 | confluence_zone | observe-only | XAU, GBPJPY | XAU -0.51R [-0.69, -0.29] n=52; GBPJPY -0.47R [-0.73, -0.08] n=55; all-instrument OOS -0.51R; the v3 certification already recommended XAU containment | A | yes |
| 4 | order_block | observe-only | EURUSD, GBPJPY | EURUSD -0.48R [-0.83, -0.03] n=45; GBPJPY -0.38R [-0.74, -0.05] n=56 | A | yes |
| 5 | fvg | observe-only | XAU | -0.23R [-0.45, -0.01] n=102; FX is flat | A | yes |
| 6 | flip_zone | observe-only | XAU | -0.37R [-0.61, -0.08] n=40; FX flat to positive | A | yes |

Patch A: [ae-containment-proposals.patch](ae-containment-proposals.patch) (`config/instruments.yml`
only; each added name is commented as proposed). Patch B:
[ae-range-sweep-retirement-step2.patch](ae-range-sweep-retirement-step2.patch) (one line in
`config/analysis.yml`). Both apply cleanly (`git apply --check`); neither has been run
against the Go, Python or .NET test suites, because applying an enablement change to the
working tree was out of bounds for this audit. Run them before merging.

Each restriction is a named instrument, never the whole strategy, because the replay
intervals differ by instrument (for example Flip Zone is +0.32R on EURUSD, Order Block +0.07R
on XAU and USDJPY). Rows 3 to 6 have intervals just below zero on one month of data: they are
the weakest proposals here, and the owner may reasonably prefer to keep observing first.

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

## Recommended future catalog

After rows 1 to 6: 20 strategies published, 4 of them under reconstruction (Trendline,
Supply, Demand, Scalp Breakout Retest). Strategies that fire only a few times a month (CRT,
Momentum Ride, Snap Back, Liquidity Sweep, Trendline) are kept: rarity is not a defect, and
their evidence is insufficient either way. The next audit should be triggered by sample size
(30 R-known production trades per strategy), not by the calendar.

## Owner approval checklist

- [ ] 1. Retire Range Sweep: patch A rows for XAU, soak, then patch B.
- [ ] 2. Range Edge observe-only on the four FX pairs.
- [ ] 3. Confluence Zone observe-only on XAU and GBPJPY.
- [ ] 4. Order Block observe-only on EURUSD and GBPJPY.
- [ ] 5. FVG observe-only on XAU.
- [ ] 6. Flip Zone observe-only on XAU.
- [ ] 7. Authorise the reconstruction PRs above one by one (Trendline first).
- [ ] 8. Authorise the executor `realized_pips` fix.
- [ ] 9. Authorise an independent reviewer for this audit's verdicts.

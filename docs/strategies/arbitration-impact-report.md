# Arbitration without quality: offline impact report

Date 2026-10-15. Tool: `algo-bot/tools/arbitration_impact.py` (wraps `tools/arbitration_eval.py`). Result:
[arbitration-impact-results.json](arbitration-impact-results.json). Required by
[ae-quality-certification.md](ae-quality-certification.md) section 4 before any change to an arbitration-facing number.

**Verdict.** Remove `quality_overall`, confluence stars and `v2_raw` from the arbitration order, and remove the quality margin
from direction conflicts, in both Algo Bot (the decision) and the Go arbitration (telemetry). The replay finds **no measurable
difference in hypothetical outcome** between the quality order and an order that uses no score, and finds that the score has
no relation to outcome. The cost is not an edge cost, it is **more holds**: direction conflicts that a quality gap used to settle
are now held unless the higher-timeframe bias settles them. The decision rests on "nothing measured supports using it", not on
"the neutral rule is better": the difference between the two orders is within noise (section 3).

## 1. What was compared

Every closed M5 bar of every committed production capture (5 symbols; XAU from 14 Sep, FX from late September, all to 14 Oct), each Go candidate admitted through
Algo Bot's own admission code, arbitrated by **Algo Bot's own `arbitrate_execution_intents`** (0 mismatches against the
tool's reimplementation over 10,551 cycles), played forward under the production 45-minute corridor reservation, valued by
causal closed-candle fills (fixed rule, assumed constant spreads, single leg). Hypothetical outcomes, not broker results, no
expectancy claim. This run adds the 14 October captures and one merged closed-bar M5 series per symbol, so it is not comparable
with `arbitration-eval-results.json` (six captures, M1 series for XAU). 47,733 candidate rows after deduplicating overlapping
captures; 15,064 cycles, 18,003 admitted intents; chronological split per symbol, first 60% development, rest out of sample.

Models: **A** incumbent; **N** no quality anywhere (order: executable, freshest confirmation, id; any executable opposite
pair is a conflict that only the bias settles); **NB** as N with the bias-aligned intent first; B and C are the two earlier
alternatives (per-strategy percentile, confluence first).

## 2. The quality score does not predict the outcome

Hypothetical R of every executable candidate (10,434 unique), by the score the ranking reads:

| Score | Pooled Spearman with R | dev / OOS |
|---|---|---|
| `quality.overall` | -0.028 | -0.035 / -0.018 |
| confluence stars | -0.036 | -0.040 / -0.029 |
| `v2_raw` | -0.050 | -0.045 / -0.059 |

| Quality bin | n | mean R |
|---|---|---|
| 0.5 - 0.67 | 7,109 | +0.010 |
| 1.0 (3 stars) | 2,862 | **-0.094** |
| stars 2 / stars 3 | 7,537 / 2,897 | +0.009 / **-0.095** |

The top bin is not better; if anything it is worse (neighbouring candidates repeat the same thesis, so the interval is wider than
n suggests; the point is that there is no gradient in the helpful direction). Within strategy the correlations scatter around
zero (-0.17 to +0.12). The quality of the trades the incumbent actually publishes shows the same: mid-rank percentile
tertiles +0.006 (low), +0.021 (middle), -0.124 (high).

## 3. What changes when quality leaves the decision

| | A incumbent | N no quality | NB bias first |
|---|---|---|---|
| Published (all) | 2,473 | 2,463 | 2,464 |
| Filled | 2,117 | 2,127 | 2,128 |
| Mean hypothetical R | -0.0374 | -0.0308 | -0.0310 |
| Mean hypothetical R, OOS | -0.1140 | -0.1122 | -0.1132 |
| Mean hypothetical R, double spread | -0.0980 | -0.0901 | -0.0903 |
| Same trades published as A | 1.00 | 0.654 | 0.654 |

Where the two disagree (1,580 diverging winner pairs over 77 symbol-days): mean delta R **+0.0008**, 95% CI [-0.038, +0.039]; cluster
interval by symbol-day [-0.041, +0.042]; OOS +0.0027 [-0.054, +0.058]. Not distinguishable from zero in either direction: the two published sets overlap 65%
(Jaccard) and the outcome does not differ. (Reading it the other way is just as unsupported: N is not "better".)

**How the incumbent actually decides.** Of 4,030 contested cycles, 2,127 were settled by `quality`, 1,861 by `v2_raw` (the
quality plateaus at `stars/3`, so most ties fall through to the next score), and 24 by the id. 1,495 of 3,635 cross-strategy
same-thesis groups (41%) tie on quality. Under N the same cycles are settled by freshness (1,163) and the id (2,758): the pick
among tied strategies is arbitrary but reproducible. That is the honest cost of having no calibrated signal; it does not
change the outcome (above).

**Direction conflicts** (1,435 cycles with executable intents on both sides):

| | A | N |
|---|---|---|
| Held (`opposite_direction_conflict`) | 67 | 176 |
| Decided with the bias | 1,043 | 1,253 |
| Decided against or without the bias | **325** | 6 |

The margin is below 0.15 in 46% of these cycles (median gap 0.183). The incumbent settled 325 conflicts by a quality gap
**against the bias or with none**; N follows the bias or holds. Cost: +109 held conflict cycles (7.6% of these cycles; the 10
fewer published trades). The bias itself is **not** shown to carry an edge either: published trades with / against / neutral bias
average -0.043 / -0.026 / -0.061 R. It stays because it was already the production tiebreak, not because the replay supports it.

Reservation: the incumbent's "better ranked" candidates that the 45-minute corridor blocked would have changed R by -0.038
[-0.076, +0.002] had they displaced the holder (2,008 blocked, 1,548 both filled): ranking higher by quality was not worth a
displacement. Nothing displaces under N.

## 4. What was changed

* `algo-bot/app/autotrade/arbitration.py`: `quality_ranking=False` orders by executable, freshness, id and never consults
  the quality margin; the default stays `True` so the offline tools and older callers are unchanged.
* `config/auto-algo.yml`: `actionability.scanner_gates.quality_ranking: false`. **Rollback: set it to `true`.**
* `analysis-engine/internal/arbitration`: `Config.IgnoreQuality`; `config/analysis.yml` `analysis.arbitration.quality_ranking: false`.
  Telemetry only (it cannot veto an intent), kept equal so it describes what executes. `go_thesis_id` values change (the group
  representative is picked by id); they are labels, shared by the members of a group.
* Not changed: `multi_match.select_primary` (status text only), the confluence-star **floor** in admission
  (`min_confluence: 2`, a threshold: stars 2 vs 3 are indistinguishable in outcome, the floor removes stars 0-1 and was not evaluated here),
  quality as a published field, stored metadata in the corridor reservation.

## 5. Limits

Hypothetical single-leg fills at assumed spreads; the production ladder, risk legs, exposure policy and news guard are not
modelled (the play-forward publishes 2,473 trades in a window where production, with its caps, filled far fewer). Two weeks
and one regime, five symbols. The mean hypothetical R is negative for every model, which says the fixed-rule replay is not a
profitability estimate. The replay cannot say whether a *calibrated* score would help; it only says the current ones do not.
Quality rebuild and a labelled structure check are separate work ([ae-quality-certification.md](ae-quality-certification.md) section 4).

## 6. Reproduce

```
cd analysis-engine && ARBITRATION_DUMP_DIR=/tmp/arb-dump go test ./test/replaycapture -run TestDumpCandidates
cd ../algo-bot && PYTHONPATH=. python -m tools.arbitration_impact /tmp/arb-dump --out ../docs/strategies/arbitration-impact-results.json
```
Deterministic (fixed seed); about 70 seconds.

# AE quality calibration: can the measured components rebuild a score? (2026-10-15)

Tool: `algo-bot/tools/ae_quality_calibration.py`. Result: [ae-quality-calibration-results.json](ae-quality-calibration-results.json).
Follows [ae-quality-certification.md](ae-quality-certification.md) section 4 and
[arbitration-impact-report.md](arbitration-impact-report.md).

**Verdict: no measured component predicts the outcome once the instrument is controlled, so there is nothing to calibrate a
rebuilt formula on, and none is shipped.** `quality.overall` stays a published, uncalibrated measurement; arbitration no longer
reads it. Inventing weights for room cover, displacement or reaction strength would produce a number that looks like a score and
carries no information, which is the defect the certification found. The tool and the acceptance rule are the deliverable: they
decide, mechanically, when a candidate component has earned a place in a score.

## 1. Method (fixed before looking at the result)

Replay records of every committed production capture (15,204 candidates, 20 strategies), thinned to one opportunity per symbol,
strategy, direction and hour; the target is the hypothetical R of the ladder simulation with costs (closed candles, causal fills,
assumed spreads). 5,803 filled observations: development 3,407 (before 2026-10-07), out of sample 2,396. 97 features: the
published components of every strategy, the shared confluence factors, stars, `v2_raw`, quality, and the geometry the executor
sees (room cover, technical RR, entry width and risk in ATR, the executable risk after the envelope floor, age, repeats).

Pooled tests use the **within-strategy mid-rank percentile** of a feature (a score only has to rank candidates against that
strategy's own); the check is repeated with the percentile taken inside **strategy and symbol**. A feature is *supported* only if
its development Spearman has a day-cluster bootstrap interval excluding zero, exceeds the Bonferroni level for 97 tests
(about 0.06 at n=3,407), and has the same sign and at least half the size out of sample.

## 2. Results

| Feature (pooled, within strategy) | dev Spearman [95%] | OOS | supported |
|---|---|---|---|
| `quality.overall` | -0.049 [-0.081, -0.015] | -0.073 | no (wrong sign) |
| stars | -0.046 [-0.080, -0.014] | -0.055 | no (wrong sign) |
| `v2_raw` | -0.052 [-0.108, 0.007] | +0.021 | no |
| room cover (log) | +0.050 [-0.010, 0.107] | +0.090 | no (interval includes 0) |
| technical RR (log) | -0.031 [-0.077, 0.024] | -0.035 | no |
| displacement factor | +0.024 [-0.004, 0.056] | +0.039 | no |
| HTF aligned | -0.084 [-0.165, -0.008] | -0.036 | no (wrong sign) |
| touches / wick / session / structural / fib | all within ±0.04, intervals include 0 or flip OOS | | no |
| executable risk in ATR | -0.119 [-0.159, -0.067] | -0.143 | **yes, pooled** |

The existing quality, as the within-strategy percentile terciles, is **inverted**: mean R low / middle / high is +0.004 / -0.035 / -0.124
in development and -0.069 / -0.137 / -0.188 out of sample.

**The one supported feature does not survive a control.** Executable risk in ATR (a tighter stop relative to volatility does
better, which is mechanical under fixed 1R/2R targets) is -0.119 pooled, but -0.026 [-0.058, +0.006] when the percentile is taken
inside strategy and symbol (OOS -0.061), and by symbol its sign flips (EURUSD dev +0.037 / OOS -0.083; XAU dev -0.038 / OOS +0.019).
It is largely a difference between instruments, not a property of a better thesis. With the instrument controlled **no feature of 97
is supported**. Per strategy (those with enough filled observations in both windows: 9 of 19), only Key Level's executable risk
passes and it is the same mechanical feature; 10 strategies have too few observations to test at all.

Two components the brief named could not be tested well: strategy-specific displacement and reaction components exist in 14 to 58
thinned filled observations each (the strategies are rare), and `component_reaction` has no filled observation at all in the thinned set.

## 3. What this means

* A rebuilt score would currently be a guess. The right posture is the one the certification set: do not threshold, rank or size on
  `quality.overall`; keep publishing it and its components as measurements.
* Room cover is the only geometry quantity with a consistently positive sign (+0.05 / +0.09, not significant): it is cheap to
  keep measuring and the first candidate when more data exists.
* The 3-star bin is not a premium bin: `min_confluence: 2` removes stars 0-1 and nothing above it distinguishes outcomes.

## 4. What would change the answer

1. Broker outcomes instead of hypothetical ones: `realized_pips` has been correct for full stops only since #778 (14 Oct), and
   the 450 `algo_auto` trades so far include only Key Level (106) and Range Edge (100) in the hundreds; every other strategy has fewer than 30.
2. Months of replay, not two weeks, spanning more than one volatility regime; the window includes the CPI shock.
3. Re-run this tool as it is: it takes about two minutes, is deterministic (fixed seed) and states pass/fail by the rule above. A
   formula may be proposed only from features it marks supported in strategy-and-symbol terms, equal-weighted, with no fitted weight,
   and must then be evaluated once on a window it has not seen.

## 5. Reproduce

```
cd analysis-engine && AEQ_DUMP=/tmp/aeq go test ./test/aequality -run TestReplayAllStrategies
cd ../algo-bot && PYTHONPATH=. python -m tools.ae_quality_calibration /tmp/aeq/records.jsonl \
    --out ../docs/strategies/ae-quality-calibration-results.json
```

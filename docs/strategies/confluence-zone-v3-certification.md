# Confluence Zone v3: certification

Status: **for owner review. Nothing is deployed, enabled, disabled or restarted by this change.** The judgements below
are deliberately separate: the technical contract can be certified while the trading evidence is neutral or
negative, and the two must not be combined into one PASS.

| Judgement | Verdict |
|---|---|
| **Technical correctness** | **CERTIFIED for review** against the matrix below: every requirement has passing evidence, three independent adversarial passes (REJECT, APPROVE WITH CHANGES, APPROVE) found and led to fixes of one blocker class (false independence through shared source candles), one design flaw (structures that depended on where price is now) and several defects (see Independent review), and the remaining findings are documented limits. Nothing in the contract depends on the replay result. One change post-dates the third review pass (the opportunity identity is now the side and the confirming candle, after the replay found a duplicate on GBPUSD): it is covered by regression tests and an all-capture invariant, **not by the reviewer's approval**. |
| **Trading evidence** (is there credible evidence of positive XAU expectancy?) | **NO.** The replay shows no edge for v2 or v3 on XAU or FX. v3 on XAU is hypothetically *negative*: pooled over the 14-21 Sep and 1-13 Oct windows 42 executable v3 setups average -0.53 [-1.00, +0.06] R (single target) and -0.30 [-0.58, -0.02] R (ladder); on the held-out 6-13 Oct period 17 setups average -0.62 [-1.00, +0.15] / -0.59 [-0.92, -0.26]. v2 on the same windows: -0.26 [-0.59, +0.09] / +0.04 [-0.20, +0.32] (85 executable setups). The intervals are wide and the samples small; they do not prove an edge is absent, they show none. |
| **FX compatibility** | **Yes, the technical behaviour is preserved and the result is not distinguishable.** The same code reads FX through the same instrument geometry (pip size, proximal slice, stop cap). On four FX pairs v3 publishes 170 decisions against v2's 745 (87 against 406 thinned), with hypothetical single-target -0.23 [-0.49, +0.09] against -0.04 [-0.20, +0.13] R and ladder -0.15 [-0.37, +0.10] against -0.07 [-0.17, +0.04]: neither is distinguishable from zero or from the other. No FX decision is refused for the execution cap except 28 (honest FX stops are within their caps). |
| **Production readiness** (suitable for review before deployment?) | **Yes, for review, not for deployment as such.** The strategy, its configuration, the replay and the reviews are complete; the contract is stricter than v2 and publishes far fewer setups, with no evidence of better expectancy. Deployment, the XAU containment below and any change of risk are the owner's decisions. Nothing is deployed, enabled or disabled by this change. |
| **XAU containment** | **Recommended: temporarily observe-only on XAU, FX unchanged**, until Confluence Zone v3 has produced enough observed XAU setups to judge. It is a risk-appetite recommendation, not a finding of negative expectancy; the minimal optional patch is prepared and **not applied** (section XAU containment). |

Baseline: master `f18179f` (#770). Evidence base: [the forensic audit](confluence-zone-xau-forensic-audit.md) (17
production `algo_auto` trades, 7 on XAU, 4 to 13 Oct 2026), the technical specification
([confluence-zone-v3-technical-spec.md](confluence-zone-v3-technical-spec.md)), the machine-readable replay
([confluence-zone-v3-replay-results.json](confluence-zone-v3-replay-results.json)) and the coefficient sensitivity
([confluence-zone-v3-sensitivity.json](confluence-zone-v3-sensitivity.json)). The audit is on its own branch until merged;
every figure used from it is reproduced here from the committed captures
(`analysis-engine/testdata/confluence-zone-production-trades-20261013.json` holds the 17 trades).

## Technical certification matrix

| Requirement | PASS / FAIL | Evidence |
|---|---|---|
| True intersection geometry | **PASS** | `structuralconfluence.Find` exact sweep: required negative example (A 4100-4105, B 4103-4108 -> 4103-4105, a reaction at 4101 is not confirmation), shared boundary has zero width, strict minimum width, extended chains, nested and disjoint regions; three randomized properties (exactness against a brute-force count of independent support at every half-unit of 300 random populations, maximality, order/duplication invariance); `internal/strategy/confluencezone` entry = region (never the union) and every setup's entry lies inside its region (1,500-market property). Replay: v2 published a band a median 2.1x-3.1x wider than the true overlap |
| Independent source provenance | **PASS** | atoms for displacement leg, break, gap, inversion and every source candle (`derive.go`), unverified facts never counted, CRT never a fact; derivation tests on hand-built frames and on real XAU bars (12 Oct S/D + OB, 13 Oct FVG + iFVG, 9 Oct, 5 Oct, 7 Oct 18:00, 5 Oct 02:10, 6 Oct 19:15); all 5,800+ distinct replayed structures have reproducible provenance |
| No duplicate-origin inflation | **PASS** | every published setup has >= `minimum_facts` independent sources (property test); the +2.5 confluence bump is paid per independent source. Replay: v2 published decisions with fewer than two independent sources in 47 % of its XAU decisions; v3 in none |
| No future-data dependency | **PASS** | availability by closed-candle time; facts as of the touch (`InstancesFromScoredZonesAsOf`, no leak found against a chain re-run on the prefix: 0 of 23,892 facts); engine-level replay tests: appending future candles, randomising them, and dropping 500 candles of warm-up leave every earlier v3 decision unchanged (`TestAppendedFutureCandles...`, `TestMutatingFutureCandles...`, `TestExtraOlderWarmup...`); a prefix oracle over 1,138 real facts found 0 available before they existed |
| Correct reaction location | **PASS** | the touch candle must reach a region supported by structures confirmable before it opened; a reaction at the union boundary but outside the overlap is not considered (negative fixture); the entry is the region |
| Fresh causal confirmation | **PASS** | reaction window measured in time (a data gap never makes a candle fresh), confirmation windows keyed to the confirming candle, refusal when price was accepted through the region, when the reaction failed afterwards (close beyond its extreme or a wick to the stop), when price has run through or away from the entry |
| Measured technical factors | **PASS** | `wick_rejection`, `displacement_grade`, `structural_agreement`, `choch`, `fib_touch` and `htf_aligned` are measurements; unavailable facts are omitted; v2's constants (177/177 XAU, 790/790 FX) are gone (`TestConfluenceZoneV3FactorsAreMeasuredNotAssumed`) |
| Structural invalidation | **PASS** | stop beyond the supporting structures' full extent and the reaction's adverse extreme (property: beyond all three and the entry on every published setup), refused beyond the execution cap, never tightened, a stop already traded to is never published (`TestAStopAlreadyTradedThrough...`, `...BeyondTheStructure...`) |
| Valid opposing liquidity targets | **PASS** | no fixed-reward-risk fallback (replay: every v3 decision is `nearest_opposing_liquidity`; v2 had fixed-2R targets in 36 decisions), the pool is unswept (property), room >= 1 ATR, reward:risk against the stop the execution layer will use, a defended opposing confluence nearer than the room blocks the trade |
| BUY/SELL symmetry | **PASS** | one long-side routine on mirrored prices; exact mirror on 800 random markets; end to end, a reflected capture yields the mirrored v3 decisions (entry and stop exact in all 47 decisions of the XAU Oct capture; the target differs from the mirror in 17 of them, in 1 by more than an ATR, because the engine's frozen pool builder is not mirror-symmetric and hands the two markets different pools (members, latest swing, swept verdict); v3 picks the nearest unswept pool of whatever it is given) |
| Stable candidate identity | **PASS** | opportunity = side + confirming candle (re-keyed after a real-data duplicate was found in part C: see the review section); thesis episode = a function of the supporting facts as of the touch (order- and duplicate-invariant); the touch is searched relative to the confirming candle; `TestAConfirmationKeepsItsEpisodeWhenTheEvaluationMovesOn` (fails on the earlier loop), `TestOneConfirmationIsNeverRenamedBetweenEvaluations` over all six real captures (0 renamed; it failed on GBPUSD before the re-keying), `TestOneConfirmationIsOneOpportunityWhateverEpisodeSupportsIt`, repeated evaluation is one opportunity (`test/strategy`) |
| All seven historical cases reproduced | **PASS** | the three legacy-regime trades by model A's frozen data and the four frozen-regime trades by the v2 re-implementation (band, stop and target within 0.01 of price), with 11 of the 12 frozen-regime production trades reproduced (the twelfth, a USDJPY trade, cannot be reproduced from the capture; the audit found the same); the seven are rebuilt with their structures, provenance and the v3 decision in `confluence-zone-v3-replay-results.json` |
| XAU replay completed | **PASS** | two captures (14-21 Sep, 1-13 Oct), four models, development and held-out periods, thinning, causal fills, bootstrap intervals; result: **no demonstrated edge** (above) |
| FX replay completed | **PASS** | four pairs, 2-13 Oct, same procedure |
| Other 20 strategies unchanged | **PASS** | the other strategies' candidates (ID, direction, entry, stop, target, quality, reaction) are byte-identical to master's on all six captures (`TestCertification`, baseline file taken from master `f18179f`); the confirmed supply/demand envelope golden is byte-identical; `strategyutil` (the frozen publishers) is untouched; the only shared edits are additive (`techniquezone/provenance.go`, `TechniqueSettings.IgnoreProximity`, `Pool.LastIndex`, `LegacyFrame.StructuralInstancesAsOf`) |
| Go unit and integration tests | **PASS** | `go vet ./...` clean; `go test ./...` all packages ok on the part B head (the replay totals golden regenerated in the part B PR); part C head: the packages listed under Reproduce |
| Python adapter regression tests | **PASS** | `test_confluence_zone_v3_adapter.py` (a real v3 payload admitted; resting overlap never executable; one thesis per episode; foreign evidence refused) with the payload-admission and policy suites: 285 passed in the focused run; CI `Python autotrade suite` passes on the part B PR |
| .NET compatibility tests | **PASS** | `dotnet test ctrader-engine/tests/CTraderFeed.Tests.csproj`: 423 passed, 0 failed; CI `C# autotrade suite` passes. No C# or TradePlan V8 change |
| Independent reviewer approval | **PASS WITH QUALIFICATION** | three adversarial passes by a reviewer that did not write the code: the first REJECTED the original part B head (false independence, facts dependent on where price is, a stop already traded through) and every confirmed defect now has a regression fixture; the second, on the fixed head, APPROVED WITH CHANGES and the changes were made; the third, on the head with those changes, APPROVED. A third, short verification pass on the head with those changes APPROVED: it re-ran the earlier cases (episode stability across 1,700 consecutive real-chain evaluations, 0 of 46 confirmations changing episode; a real swept-pool case; the effective reward:risk), verified `Pool.LastIndex` against the zone chain's clustering on about 4,000 real pools (0 wrong), and found the other strategies' envelope ids identical to master's. One low-severity, theoretical residual is documented below. **The later identity re-keying (`55563bf1`) was not seen by the reviewer.** |

## What was replayed

Six committed real production captures (closed M5/M15/H1 candles read from Redis): XAU 14-21 Sep, XAU 1-13 Oct, and
EURUSD, GBPUSD, GBPJPY and USDJPY 2-13 Oct. Each is replayed candle by candle through the production engine path
(`replaycapture.Replay`), and four models are evaluated on the same closed candles:

| Model | What it is | Where it comes from |
|---|---|---|
| **A** | the legacy Go-native running-intersection detector (production until 6 Oct 09:33 UTC) | a frozen data file produced by the engine revision `c458ef6`; a what-if outside its production period |
| **B** | the Python-parity v2 union detector (production from 6 Oct 09:33) | re-implemented in `test/czreplay` from the code on master; reproduces 11 of the 12 frozen-regime production trades (the twelfth cannot be reproduced from the capture) |
| **C** | the v3 structural engine with **v2's** confirmation, stop and target | `test/czreplay`: isolates what independence, true intersection and causality change |
| **D** | the full v3 strategy | the production code, as the engine publishes it |

A and B are frozen baselines; C and D are experimental comparisons, D being the intended contract. Fills are causal and
hypothetical (a fill needs a later candle that trades to the proximal edge; a stop and a target in one candle count as the
stop), with no spread, slippage, commission, M1 trigger or broker behaviour. A decision is *executable* only if its risk
from the proximal edge is within the instrument's execution stop cap (the Algo Bot refuses a plan beyond it); statistics are
on executable, thinned (one per symbol, direction and hour) decisions, and `single_target` uses the published stop and
target while `ladder` is a 1R-4R exit schedule with the stop widened to the instrument's floor. Intervals are seeded 95 %
bootstraps that treat thinned setups as independent, which is optimistic. Development and held-out periods are
chronological and disjoint; no coefficient was chosen on a held-out period.

### v2 reproduces production

For every frozen-regime production trade of a reproducible symbol (4 XAU, 5 FX) the frozen v2 re-implementation
publishes the production band, stop and target (to within 0.01 of price: the wire rounds to two decimals and the capture
holds a little less warm-up than production). The three legacy-regime XAU trades are reproduced by model A's frozen
data. The Go strategy's own analysis and the engine's published candidates agree on every evaluated candle of every
capture.

## v2's structural defects, measured on its own decisions

| Period | v2 decisions | a member not confirmable before the touch | fewer than two independent sources | sweep_reclaim by an opposite-colour candle | reaction already failed at publication | fixed-2R targets | median union / overlap width |
|---|---|---|---|---|---|---|---|
| XAU 14-21 Sep | 83 | 29 (35 %) | 50 (60 %) | 5 | 12 | 0 | 2.44x |
| XAU 1 Oct - 6 Oct 09:33 | 58 | 20 (34 %) | 25 (43 %) | 7 | 5 | 0 | 3.14x |
| XAU 6 Oct 09:33 - 13 Oct | 115 | 39 (34 %) | 45 (39 %) | 15 | 18 | 3 | 2.09x |
| XAU, every window pooled | 256 | 88 (34 %) | 120 (47 %) | 27 | 35 | 3 | 2.32x |
| FX 2 - 8 Oct | 415 | 131 (32 %) | 164 (40 %) | 55 | 32 | 25 | 2.48x |
| FX 8 - 13 Oct | 330 | 112 (34 %) | 131 (40 %) | 28 | 36 | 8 | 2.17x |
| FX, 2 - 13 Oct pooled | 745 | 243 (33 %) | 295 (40 %) | 83 | 68 | 33 | 2.28x |

Of v2's XAU decisions in the frozen period, **45 of 115 (39 %)** had fewer than two independent sources among their
members, 39 (34 %) had a member not confirmable before the touch candle opened and 18 (16 %) had a reaction that had
already failed when it was published. v2's decisions are also frequently not executable at all: on XAU only
44 of 67 thinned v2 decisions in the frozen period had a risk within the instrument's stop cap.

## Results

**XAU 14-21 Sep (Python-origin detector era)** (development; 2026-09-01 00:00 to 2026-10-01 00:00 UTC)

| Model | Decisions | Thinned | Executable | Filled | Single-target R [95 %] | Ladder R [95 %] | Median stop (pips / ATR) | Median reward:risk | Median MFE / MAE (R) |
|---|---|---|---|---|---|---|---|---|---|
| A legacy intersection (frozen data) | 112 | 65 | 58 | 58 | +0.02 [-0.46, +0.54] (n=58) | -0.04 [-0.35, +0.29] | 43 / 0.99 | 1.94 | 0.73 / 1.12 |
| B v2 union (frozen publisher) | 83 | 59 | 23 | 23 | -0.20 [-0.91, +0.82] (n=23) | -0.04 [-0.55, +0.49] | 65.6 / 1.85 | 1.4 | 0.53 / 1.19 |
| C v3 structure + v2 confirmation/stop/target | 43 | 29 | 16 | 16 | -0.01 [-0.80, +1.02] (n=16) | -0.69 [-1.00, -0.35] | 60.2 / 1.76 | 1.81 | 0.5 / 1.16 |
| D v3 full contract | 23 | 14 | 14 | 13 | +0.02 [-1.00, +1.64] (n=13) | -0.43 [-0.89, +0.17] | 54.1 / 1.19 | 2.57 | 0.94 / 1.38 |

**XAU 1 Oct - 6 Oct 09:33 (legacy running-intersection detector in production)** (development; 2026-10-01 00:00 to 2026-10-06 09:33 UTC)

| Model | Decisions | Thinned | Executable | Filled | Single-target R [95 %] | Ladder R [95 %] | Median stop (pips / ATR) | Median reward:risk | Median MFE / MAE (R) |
|---|---|---|---|---|---|---|---|---|---|
| A legacy intersection (frozen data) | 75 | 48 | 43 | 43 | -0.23 [-0.70, +0.33] (n=43) | +0.05 [-0.28, +0.40] | 32.3 / 0.77 | 2.9 | 1.1 / 1.3 |
| B v2 union (frozen publisher) | 58 | 41 | 18 | 18 | -0.86 [-1.00, -0.59] (n=18) | -0.18 [-0.69, +0.36] | 63.6 / 1.92 | 1.47 | 0.48 / 1.33 |
| C v3 structure + v2 confirmation/stop/target | 51 | 29 | 14 | 14 | -0.36 [-1.00, +0.41] (n=14) | -0.14 [-0.70, +0.44] | 60.2 / 1.71 | 1.4 | 0.28 / 1.07 |
| D v3 full contract | 21 | 12 | 12 | 12 | -1.00 [-1.00, -1.00] (n=12) | +0.23 [-0.30, +0.78] | 53.9 / 1.39 | 3.29 | 1.2 / 1.15 |

**XAU 6 Oct 09:33 - 13 Oct (frozen technique publishers in production)** (held-out; 2026-10-06 09:33 to 2026-10-14 00:00 UTC)

| Model | Decisions | Thinned | Executable | Filled | Single-target R [95 %] | Ladder R [95 %] | Median stop (pips / ATR) | Median reward:risk | Median MFE / MAE (R) |
|---|---|---|---|---|---|---|---|---|---|
| A legacy intersection (frozen data) | 107 | 61 | 60 | 58 | +0.59 [+0.01, +1.18] (n=58) | +0.26 [-0.06, +0.57] | 33.9 / 0.97 | 1.9 | 1.27 / 1.09 |
| B v2 union (frozen publisher) | 115 | 67 | 44 | 44 | -0.04 [-0.48, +0.46] (n=44) | +0.18 [-0.15, +0.53] | 62.6 / 2 | 1.15 | 0.89 / 1.19 |
| C v3 structure + v2 confirmation/stop/target | 91 | 40 | 25 | 25 | -0.20 [-0.63, +0.25] (n=25) | -0.19 [-0.58, +0.23] | 59.6 / 2.12 | 1 | 0.5 / 1.1 |
| D v3 full contract | 26 | 18 | 18 | 17 | -0.62 [-1.00, +0.15] (n=17) | -0.59 [-0.92, -0.26] | 55.6 / 1.55 | 2.84 | 0.83 / 1.08 |

**XAU, every window pooled (14-21 Sep and 1-13 Oct)** (pooled; 2026-09-01 00:00 to 2026-10-14 00:00 UTC)

| Model | Decisions | Thinned | Executable | Filled | Single-target R [95 %] | Ladder R [95 %] | Median stop (pips / ATR) | Median reward:risk | Median MFE / MAE (R) |
|---|---|---|---|---|---|---|---|---|---|
| A legacy intersection (frozen data) | 294 | 173 | 160 | 158 | +0.17 [-0.13, +0.49] (n=158) | +0.09 [-0.10, +0.28] | 36.3 / 0.95 | 2.15 | 0.99 / 1.19 |
| B v2 union (frozen publisher) | 256 | 167 | 85 | 85 | -0.26 [-0.59, +0.09] (n=85) | +0.04 [-0.20, +0.32] | 63.1 / 1.93 | 1.25 | 0.56 / 1.22 |
| C v3 structure + v2 confirmation/stop/target | 185 | 98 | 55 | 55 | -0.19 [-0.54, +0.19] (n=55) | -0.32 [-0.57, -0.03] | 59.6 / 1.84 | 1.2 | 0.46 / 1.09 |
| D v3 full contract | 70 | 44 | 44 | 42 | -0.53 [-1.00, +0.06] (n=42) | -0.30 [-0.58, -0.02] | 54.6 / 1.44 | 2.88 | 0.97 / 1.21 |

**FX (4 pairs) 2 - 8 Oct** (development; 2026-10-02 00:00 to 2026-10-08 00:00 UTC)

| Model | Decisions | Thinned | Executable | Filled | Single-target R [95 %] | Ladder R [95 %] | Median stop (pips / ATR) | Median reward:risk | Median MFE / MAE (R) |
|---|---|---|---|---|---|---|---|---|---|
| A legacy intersection (frozen data) | 536 | 254 | 254 | 248 | -0.04 [-0.26, +0.20] (n=248) | -0.02 [-0.15, +0.11] | 3.9 / 0.92 | 2.36 | 1.12 / 1.16 |
| B v2 union (frozen publisher) | 415 | 223 | 223 | 221 | -0.11 [-0.30, +0.10] (n=221) | -0.15 [-0.29, -0.00] | 10.2 / 2.37 | 1.06 | 0.77 / 1.09 |
| C v3 structure + v2 confirmation/stop/target | 404 | 156 | 156 | 155 | -0.01 [-0.22, +0.22] (n=155) | -0.12 [-0.28, +0.04] | 11.4 / 2.7 | 0.82 | 0.95 / 1.04 |
| D v3 full contract | 103 | 54 | 54 | 50 | -0.28 [-0.60, +0.10] (n=50) | -0.22 [-0.48, +0.07] | 15.3 / 2.58 | 2.35 | 0.89 / 1.08 |

**FX (4 pairs) 8 - 13 Oct** (held-out; 2026-10-08 00:00 to 2026-10-14 00:00 UTC)

| Model | Decisions | Thinned | Executable | Filled | Single-target R [95 %] | Ladder R [95 %] | Median stop (pips / ATR) | Median reward:risk | Median MFE / MAE (R) |
|---|---|---|---|---|---|---|---|---|---|
| A legacy intersection (frozen data) | 487 | 245 | 245 | 240 | -0.24 [-0.43, -0.03] (n=240) | -0.06 [-0.19, +0.07] | 3.2 / 0.87 | 2.47 | 1.11 / 1.22 |
| B v2 union (frozen publisher) | 330 | 183 | 183 | 180 | +0.04 [-0.19, +0.30] (n=180) | +0.04 [-0.11, +0.19] | 7.6 / 2.29 | 1.22 | 0.86 / 1.06 |
| C v3 structure + v2 confirmation/stop/target | 275 | 118 | 118 | 115 | -0.20 [-0.45, +0.08] (n=115) | -0.07 [-0.26, +0.13] | 9.2 / 2.42 | 1.06 | 0.77 / 1.08 |
| D v3 full contract | 67 | 33 | 33 | 31 | -0.15 [-0.60, +0.38] (n=31) | -0.02 [-0.41, +0.39] | 11.6 / 2.88 | 3.76 | 0.77 / 1.1 |

**FX (4 pairs), 2 - 13 Oct pooled** (pooled; 2026-10-02 00:00 to 2026-10-14 00:00 UTC)

| Model | Decisions | Thinned | Executable | Filled | Single-target R [95 %] | Ladder R [95 %] | Median stop (pips / ATR) | Median reward:risk | Median MFE / MAE (R) |
|---|---|---|---|---|---|---|---|---|---|
| A legacy intersection (frozen data) | 1023 | 499 | 499 | 488 | -0.14 [-0.28, +0.02] (n=488) | -0.04 [-0.13, +0.06] | 3.5 / 0.9 | 2.44 | 1.11 / 1.19 |
| B v2 union (frozen publisher) | 745 | 406 | 406 | 401 | -0.04 [-0.20, +0.13] (n=401) | -0.07 [-0.17, +0.04] | 9.1 / 2.33 | 1.12 | 0.81 / 1.07 |
| C v3 structure + v2 confirmation/stop/target | 679 | 274 | 274 | 270 | -0.09 [-0.25, +0.08] (n=270) | -0.10 [-0.22, +0.03] | 10.2 / 2.6 | 0.9 | 0.81 / 1.06 |
| D v3 full contract | 170 | 87 | 87 | 81 | -0.23 [-0.49, +0.09] (n=81) | -0.15 [-0.37, +0.10] | 13.7 / 2.63 | 2.89 | 0.86 / 1.1 |

**Reading the tables.**

1. **No model has demonstrated an edge.** The one positive interval that excludes zero is model A on the XAU held-out period (+0.59 [+0.01, +1.18] single target, 58 setups), and model A is a what-if outside its production period (before 6 Oct 09:33 it ran live: -0.23 [-0.70, +0.33] over 43 setups) and pooled it is +0.17 [-0.13, +0.49] / +0.09 [-0.10, +0.28]. A result that flips sign between adjacent windows of the same instrument is noise, not an edge.
2. **v3 on XAU is hypothetically negative, v2 neutral.** v3, pooled: -0.53 [-1.00, +0.06] single, -0.30 [-0.58, -0.02] ladder over 42 executable setups; held-out (6-13 Oct): -0.62 [-1.00, +0.15] / -0.59 [-0.92, -0.26] over 17. v2, pooled: -0.26 [-0.59, +0.09] / +0.04 [-0.20, +0.32] over 85. The samples are small (the bootstrap treats thinned setups as independent, which flatters them); the direction is consistent, the proof is not.
3. **The structure alone (model C) does not help.** It keeps v2's confirmation, stop and target and takes only the independent-source region: -0.19 [-0.54, +0.19] / -0.32 [-0.57, -0.03] on XAU pooled. What v3 changes is correctness, not selection.
4. **On FX nothing is distinguishable** (pooled single target: v2 -0.04 [-0.20, +0.13], v3 -0.23 [-0.49, +0.09]).
5. **The geometry is what differs.** v2's median stop is 1.93 ATR on executable XAU decisions, v3's 1.44; v3's stops are structural (a far edge plus the adverse extreme) rather than a sliver, and its targets are real opposing liquidity, which is farther (median reward:risk 2.88 against 1.25). On FX the stops are wider than v2's. The replay finds no net benefit from either.

### v3 against v2

| Period | Baseline filled | v2 losers removed | v2 winners removed | v3 decisions with no v2 counterpart | v3 thinned | v2 mean R of retained / removed |
|---|---|---|---|---|---|---|
| XAU 14-21 Sep | 59 | 39 of 41 | 17 of 18 | 10 | 14 | -0.09 / +0.06 |
| XAU 1 Oct - 6 Oct 09:33 | 41 | 30 of 32 | 9 of 9 | 11 | 12 | -1.00 / -0.25 |
| XAU 6 Oct 09:33 - 13 Oct | 67 | 44 of 45 | 19 of 22 | 13 | 18 | +0.79 / +0.08 |
| XAU, every window pooled | 167 | 113 of 118 | 45 of 49 | 34 | 44 | +0.10 / -0.01 |
| FX 2 - 8 Oct | 221 | 144 of 157 | 61 of 64 | 37 | 54 | -0.46 / -0.08 |
| FX 8 - 13 Oct | 180 | 107 of 116 | 60 of 64 | 14 | 33 | +0.01 / +0.04 |
| FX, 2 - 13 Oct pooled | 401 | 250 of 273 | 121 of 128 | 51 | 87 | -0.28 / -0.02 |

v3 publishes far fewer setups and removes v2's winners and losers in nearly the same proportion: it is a **technical
correction, not a filter that finds the winners**. On XAU, pooled, v3 removes 113 of v2's 118 losing and 45 of its 49 winning setups (96 % and 92 %); on FX 250 of 273 and 121 of 128.

Structure only (model C) against v2:

| Period | Baseline filled | v2 losers removed | v2 winners removed | structure-only decisions with no v2 counterpart |
|---|---|---|---|---|
| XAU 14-21 Sep | 59 | 29 of 41 | 16 of 18 | 12 |
| XAU 1 Oct - 6 Oct 09:33 | 41 | 17 of 32 | 5 of 9 | 9 |
| XAU 6 Oct 09:33 - 13 Oct | 67 | 29 of 45 | 17 of 22 | 15 |
| XAU, every window pooled | 167 | 75 of 118 | 38 of 49 | 36 |
| FX 2 - 8 Oct | 221 | 93 of 157 | 37 of 64 | 59 |
| FX 8 - 13 Oct | 180 | 66 of 116 | 44 of 64 | 43 |
| FX, 2 - 13 Oct pooled | 401 | 158 of 273 | 81 of 128 | 102 |

### XAU against FX

| | XAU pooled | FX pooled |
|---|---|---|
| v2 decisions / thinned / executable | 256 / 167 / 85 | 745 / 406 / 406 |
| v3 decisions / thinned / executable | 70 / 44 / 44 | 170 / 87 / 87 |
| v2 single / ladder R | -0.26 [-0.59, +0.09] / +0.04 [-0.20, +0.32] | -0.04 [-0.20, +0.13] / -0.07 [-0.17, +0.04] |
| v3 single / ladder R | -0.53 [-1.00, +0.06] / -0.30 [-0.58, -0.02] | -0.23 [-0.49, +0.09] / -0.15 [-0.37, +0.10] |
| median stop, v2 (pips / ATR) | 63.1 / 1.93 | 9.1 / 2.33 |
| median stop, v3 (pips / ATR) | 54.6 / 1.44 | 13.7 / 2.63 |
| median reward:risk, v2 / v3 | 1.25 / 2.88 | 1.12 / 2.89 |

The XAU result differs from FX in one structural way the audit found and v3 keeps visible: the instrument's execution stop cap. 82 of v2's 167 thinned XAU decisions carried a risk beyond the cap (the Algo Bot would refuse them: this is most of why a live capture shows 7 XAU trades against dozens of v2 decisions), against none on FX. v3 refuses them itself and states why. 318 of its XAU Oct rejections are `stop_exceeds_execution_envelope`.

## The seven XAU trades

Each is rebuilt from the committed capture and the production ledger; the v3 structures, their provenance and the
decision are in `confluence-zone-v3-replay-results.json` (`seven_xau_trades`). v3 publishes **none** of the seven.

| Trade (close, UTC) | Production signal (detector) | v3 signal | Geometry changed | Independent sources among the structures at the trade | v3 reasons recorded around the trade |
|---|---|---|---|---|---|
| 10-05 14:31 BUY | BUY 4139.39-4140.13 (7 pips), stop 4136.5, target 4147.26, strong_reclaim, 3* (legacy intersection); -1.00 R | not published | true overlap 7 pips (1x narrower) | 2 | no_directional_confirmation, stop_exceeds_execution_envelope, structure_not_yet_available |
| 10-05 16:03 BUY | BUY 4137.13-4138.05 (9 pips), stop 4134.33, target 4148.68, strong_reclaim, 3* (legacy intersection); -1.00 R | not published | true overlap 9 pips (1x narrower) | 3 | no_directional_confirmation, price_accepted_through_region, stop_exceeds_execution_envelope |
| 10-06 03:53 BUY | BUY 4127.32-4127.8 (5 pips), stop 4124.36, target 4139.06, strong_reclaim, 3* (legacy intersection); +0.60 R | not published | true overlap 5 pips (1x narrower) | 2 | no_directional_confirmation, price_accepted_through_region, single_independent_origin, stop_exceeds_execution_envelope |
| 10-09 13:18 SELL | SELL 4178.77-4183.24 (45 pips), stop 4185.65, target 4168.62, sweep_reclaim, 3* (v2 union); -1.00 R | not published | true overlap 21 pips (2.13x narrower) | 2 | no_directional_confirmation, price_accepted_through_region, structure_not_yet_available |
| 10-09 19:52 BUY | BUY 4187.6-4192.6 (50 pips), stop 4185.99, target 4198.59, sweep_reclaim, 3* (v2 union); +0.42 R | not published | true overlap 34 pips (1.47x narrower) | 3 | no_directional_confirmation, single_independent_origin, stop_exceeds_execution_envelope |
| 10-12 22:01 SELL | SELL 4167.51-4172.51 (50 pips), stop 4173.76, target 4162.51, sweep_reclaim, 3* (v2 union); -1.00 R | not published | true overlap 31 pips (1.63x narrower) | 2 | no_directional_confirmation, single_independent_origin, stop_exceeds_execution_envelope |
| 10-13 02:25 SELL | SELL 4166.95-4170.5 (36 pips), stop 4172.52, target 4162.51, sweep_reclaim, 2* (v2 union); -1.00 R | not published | true overlap 10 pips (3.38x narrower) | 3 | no_directional_confirmation, single_independent_origin, stop_exceeds_execution_envelope |

Reading the table: v3 rejecting a historical *winner* (6 Oct, 9 Oct 19:52) is not a defect or an improvement: their
prerequisites were not met (the 6 Oct setup's honest stop is wider than the execution cap; the 9 Oct 19:52 demand zone was
created by the very candle that reacted). The three losses of 9, 12 and 13 Oct fail on different grounds (a confirmation
that did not meet the measured close strength; one structure counted twice; one structure counted twice), which is why no
single rule removes them.

## Funnel

| Capture | Evaluated candles | Structures | with reproducible provenance | Supported regions | Confirmed setups before the floor | Published | v2 published | Structure-only published | Rejections |
|---|---|---|---|---|---|---|---|---|---|
| EURUSD | 2000 | 960 | 960 | 323 | 41 | 32 | 160 | 89 | no_directional_confirmation 611, structure_not_yet_available 480, single_independent_origin 412, price_accepted_through_region 179, overlap_too_narrow 96, target_blocked_by_opposing_structure 82, reward_risk_below_minimum 74, reaction_failed_after_confirmation 31, entry_too_far 10, no_opposing_liquidity 9, stop_exceeds_execution_envelope 3, confluence_floor 2, insufficient_target_room 1 |
| GBPJPY | 2000 | 863 | 863 | 341 | 104 | 82 | 192 | 206 | no_directional_confirmation 1047, structure_not_yet_available 482, single_independent_origin 345, price_accepted_through_region 261, reward_risk_below_minimum 158, target_blocked_by_opposing_structure 77, overlap_too_narrow 64, reaction_failed_after_confirmation 58, entry_too_far 14, stop_exceeds_execution_envelope 9, no_opposing_liquidity 7, price_through_entry 5, insufficient_target_room 1 |
| GBPUSD | 2000 | 903 | 903 | 383 | 57 | 39 | 201 | 187 | no_directional_confirmation 942, structure_not_yet_available 458, single_independent_origin 357, reward_risk_below_minimum 251, price_accepted_through_region 184, overlap_too_narrow 100, target_blocked_by_opposing_structure 58, reaction_failed_after_confirmation 48, no_opposing_liquidity 11, entry_too_far 10, confluence_floor 1 |
| USDJPY | 2000 | 873 | 873 | 380 | 25 | 17 | 192 | 197 | no_directional_confirmation 1105, structure_not_yet_available 523, single_independent_origin 319, reward_risk_below_minimum 249, price_accepted_through_region 246, target_blocked_by_opposing_structure 123, overlap_too_narrow 83, reaction_failed_after_confirmation 57, stop_exceeds_execution_envelope 16, no_opposing_liquidity 13, entry_too_far 4, price_through_entry 1 |
| XAU Oct | 2223 | 1351 | 1351 | 529 | 59 | 47 | 173 | 142 | no_directional_confirmation 1074, structure_not_yet_available 1022, single_independent_origin 537, price_accepted_through_region 449, stop_exceeds_execution_envelope 318, reaction_failed_after_confirmation 62, target_blocked_by_opposing_structure 46, reward_risk_below_minimum 38, overlap_too_narrow 36, entry_too_far 10, confluence_floor 5, price_through_entry 5, no_opposing_liquidity 3 |
| XAU Sep | 1500 | 894 | 894 | 265 | 29 | 23 | 83 | 43 | structure_not_yet_available 584, no_directional_confirmation 432, single_independent_origin 358, price_accepted_through_region 218, stop_exceeds_execution_envelope 129, reaction_failed_after_confirmation 26, overlap_too_narrow 25, entry_too_far 6, confluence_floor 2, target_blocked_by_opposing_structure 2, reward_risk_below_minimum 1 |

## Coefficient sensitivity

One coefficient moved at a time, every capture replayed with the v3 strategy alone. This is not a search: the configured
values are the shared v3 conventions, and the table shows how much the outcome depends on them.

| Coefficient moved | XAU frozen period: filled / single R / ladder R | XAU all (Sep + Oct) filled | FX held-out: filled / single R / ladder R | frozen-regime XAU trades it would publish |
|---|---|---|---|---|
| configured | 17 / -0.62 / -0.59 | 42 | 31 / -0.15 / -0.02 | 0 of 4 |
| minimum_facts 3 | 3 / -1.00 / -0.53 | 4 | 12 / +1.15 / +0.97 | 0 of 4 |
| minimum_overlap_atr 0 and pips 0.1 | 17 / -0.62 / -0.59 | 41 | 27 / -0.16 / +0.05 | 0 of 4 |
| minimum_overlap_atr 0.15 | 14 / -0.53 / -0.50 | 33 | 32 / -0.09 / +0.02 | 0 of 4 |
| rejection_wick_ratio 0.15 | 17 / -0.62 / -0.59 | 43 | 31 / -0.16 / -0.07 | 0 of 4 |
| rejection_wick_ratio 0.40 | 16 / -0.59 / -0.65 | 38 | 28 / -0.26 / -0.07 | 0 of 4 |
| rejection_close_strength 0.50 | 17 / -0.62 / -0.59 | 42 | 33 / -0.20 / -0.08 | 1 of 4 |
| rejection_close_strength 0.70 | 15 / -0.54 / -0.53 | 35 | 30 / -0.29 / -0.08 | 0 of 4 |
| minimum_reclaim_atr 0.10 | 17 / -0.59 / -0.59 | 42 | 30 / -0.12 / +0.01 | 0 of 4 |
| displacement_body_ratio 0.5 and range 0.4 | 19 / -0.66 / -0.63 | 44 | 35 / -0.25 / -0.14 | 0 of 4 |
| invalidation_buffer_atr 0.25 | 20 / -0.63 / -0.49 | 55 | 31 / -0.12 / -0.02 | 0 of 4 |
| invalidation_buffer_atr 0.75 | 12 / -0.56 / -0.35 | 29 | 31 / -0.19 / -0.03 | 0 of 4 |
| minimum_reward_risk 1.0 | 17 / -0.62 / -0.59 | 46 | 32 / -0.18 / -0.05 | 0 of 4 |
| minimum_reward_risk 1.5 | 12 / -1.00 / -0.77 | 31 | 19 / -0.36 / +0.13 | 0 of 4 |
| minimum_target_distance_atr 0.5 | 17 / -0.62 / -0.59 | 40 | 30 / -0.12 / +0.01 | 0 of 4 |
| minimum_target_distance_atr 1.5 | 17 / -0.62 / -0.59 | 42 | 34 / -0.23 / -0.04 | 0 of 4 |

Across the 15 variants, pooled XAU filled setups range 4 to 55, single-target mean R from -1.00 to -0.20 and ladder from -0.65 to -0.21. No variant has a positive pooled mean on both measures; the conclusion (no demonstrated edge, and a sample far too small to find one) does not depend on the configured values. How many of the four frozen-regime XAU production trades each variant would publish is in the last column: the trades fail on independence and on the execution cap, which no coefficient tested here changes. On FX, `minimum_facts 3` (34 filled setups: +0.29 single, +0.21 ladder) is the only variant above zero on both measures: a sample too small, and a variant noticed after the fact, so it is a hypothesis for the forward record, not a finding.

## Execution and risk impact

- **Entry bands**: v3 enters the region of independent overlap, not the union: the three legacy-regime trades were entered on bands of 5 to 9 pips that were already the true overlap; the four frozen-regime trades on v2's union bands of 45, 50, 50 and 36 pips, which were 1.5 to 3.4 times the genuine overlap of their independent structures. v3 enters only the supported region.
- **Stop distance**: stops are structural and are refused, never tightened, beyond the cap:
   on XAU v3's median is 54.6 pips / 1.44 ATR against v2's 63.1 / 1.93 on executable decisions (v2 also publishes many stops beyond the cap, which v3 refuses); on FX v3's is 13.7 / 2.63 against v2's 9.1 / 2.33.
- **Target room**: v3 publishes no target without real opposing liquidity; median reward:risk 2.88 on XAU (v2 1.25), 2.89 on FX (v2 1.12).
- **Order eligibility**: every v3 decision is within the execution stop cap by construction; v2 publishes decisions the Algo Bot cannot place (82 of 167 on XAU) and v3 does not.
- **Exposure**: frequency falls sharply (XAU 44 thinned v3 decisions against 167 for v2 over the same 24 days, FX 87 against 406; of v2's XAU decisions only 85 were executable); position size, risk percent, ladder mechanics, take-profit
  allocation, breakeven, exposure limits, arbitration ranking and the other strategies are unchanged.
- **Other strategies**: the candidate set of the other strategies is byte-identical to master's on all six captures
  (`confluence-zone-other-strategies-baseline-master-f18179f.json`, asserted by `TestCertification`), and the confirmed
  supply/demand envelope golden is unchanged.

## Independent review

The review was done by a separate reviewer that had not written the code and was asked to break it rather than confirm it (the twelve points of the brief, plus the new machinery each time). Three passes:

| Pass | Verdict | What it found |
|---|---|---|
| 1, on part B as first written | **REJECT** (APPROVE WITH CHANGES once items 1-3 were fixed) | **1. High**: independence atoms missed shared *source candles*; about a third of the published setups were "independent" only through candle-sharing structures (an FVG and an iFVG made by one impulse candle; an FVG cut from the three candles of a supply/demand base; a second zone based on the first leg). **2. High**: facts came from the chain's "now" instances, so the reacting candle (an FVG it swept) or later price decided which structures existed at the touch, and one confirmation could change episode, entry and stop between evaluations. **3. Medium**: a stop already traded to by a later wick was still published. 4. Medium: the stop sat on the tradable slice, inside a still-valid zone. 5. Medium: "unswept" was not enforced for the target. 6. Medium: no minimum candle size, and a wick rejection could close inside the region. 7. Low-medium: reward:risk ignored the execution layer's stop floor. 8. Low: touches measured at a time that depended on when the setup was evaluated, config errors non-deterministic, a no-op adapter test line |
| 2, on the fixed head | **APPROVE WITH CHANGES** | all eight confirmed fixed with real-chain evidence (no leak against a chain re-run on the prefix: 0 of 23,892 facts; as-of at the present equals the old collection, 0 differences on 300 windows). New: **N1 Medium**, the setup chosen for one confirmation could change when its earlier touch aged out of the evaluated window; **N2**, `unswept` membership tolerance let a nearby later swing hide a sweep; **N5**, published reward:risk was the technical figure; **N6**, a documentation sentence; N3 (the candle-atom cliff at the chain's three-candle base) and N4 (dropping price proximity changes the population: `strong_reclaim` became a common pattern) are judgement calls to document |
| 3, on the head with those changes | **APPROVE** | N1, N2, N5 and N6 fixed and verified on real data; `Pool.LastIndex` correct on about 4,000 real pools; the other strategies unchanged |

Every confirmed defect has a regression fixture (`structuralconfluence/derive_test.go`, `test/structuralconfluence/real_test.go`, `internal/strategy/confluencezone/negative_test.go`, `test/czreplay/invariance_test.go`); `TestAConfirmationKeepsItsEpisodeWhenTheEvaluationMovesOn` fails on the earlier code. N3 and N4 are documented in the specification (section 11).

**Found after the review, fixed, not re-reviewed.** The residual the reviewer's pass 3 left open (a confirmation being published under a different episode when the touch that supported the first-selected episode is dropped by a later check) was exercised on all six captures in part C and **did occur on real data**: one GBPUSD confirmation (the 12 Oct 23:25 UTC candle) was published under two opportunity IDs, with an identical entry, stop and target, in two successive evaluations, and the replay counted 3 XAU and 4 FX such duplicates in its decision list. The fix is in part B (`55563bf1`): the opportunity ID (`SetupKey`) is now the side and the confirming candle, and the episode remains the thesis identity; at most one confirmed candidate per direction is published, so the key is never ambiguous. `TestOneConfirmationIsNeverRenamedBetweenEvaluations` (all six captures) failed before the change and passes after, and `TestOneConfirmationIsOneOpportunityWhateverEpisodeSupportsIt` pins the key. **The reviewer did not see this change** (it post-dates their third pass); it is small, but it is not covered by their approval and is listed as such in the matrix.

What the review did **not** cover: the replay harness (`test/czreplay`) and the certification arithmetic were not audited by the reviewer; they are checked by the invariants asserted in `TestCertification` (v2 reproduces production, other strategies identical to master, the strategy's own analysis equals the engine's published candidates). Stars and zone scores come from the chain's scoring at the evaluated candle (untested for leakage; they feed quality and the star floor only).

## XAU containment

**Question.** Should `confluence_zone` stay live on XAU, or be made temporarily observe-only?

**What is established.**
1. The strategy live on XAU today is v2. Its contract is technically unsound (the audit; and 120 of 256 of its XAU decisions in the replay had fewer than two independent sources). Seven live trades: five full stops and two partial wins, -3.98 R.
2. v3 is the correction, and it is **not** a better trader on this evidence: on XAU its hypothetical result is negative (above), on FX indistinguishable from zero and from v2.
3. v2 on XAU: most of its decisions are not executable (their risk is beyond the instrument cap), so the live strategy trades a small, unrecorded subset of what it publishes (the route-outcome funnel was overwritten, so which subset is **unverified**).
4. None of this is statistical proof of negative expectancy: the intervals include zero for v2, and v3's XAU held-out sample is 17 setups.

**Recommendation (separate from the technical certification): contain `confluence_zone` on XAU, temporarily.** Reasons: the live XAU strategy has confirmed integrity defects and a -3.98 R live record, the replacement shows no edge, XAU is the instrument where its decisions are least executable and its results least stable, and containing it costs no information (the Go engine keeps publishing, storing and deciding its opportunities with reason `execution_contained`, so the observed record keeps growing for the next review). It does not cancel, modify or close anything already published or open, changes no sizing, risk percent, exposure limit or group risk, and FX is untouched (`TestOptionalXAUContainmentPatchDoesOnlyWhatItSays`).

**What would change it.** A forward observed XAU record of at least 30 v3 setups (hypothetical, causal fills) with an interval excluding the loss, or the owner's explicit risk-appetite decision to keep running an unproven strategy. Conversely, if the owner prefers to keep XAU live, the fixes in this change (v3) are the safer thing to run than v2, and nothing here argues for deploying v2 further.

**The optional patch** ([confluence-zone-v3-xau-containment.patch](confluence-zone-v3-xau-containment.patch)) adds one name to `instruments.XAU.overrides.execution.go_opportunity.observe_only_strategies` and a comment. It is **not applied**: `config/instruments.yml` is unchanged by this pull request. Apply with `git apply docs/strategies/confluence-zone-v3-xau-containment.patch` after review; roll back by removing the name (containment is read at match creation and at plan admission, so it stops *new* plans only). `liquidity_sweep`, `impulse_pullback`, the M15 structure containment, `ifvg` and `session_level` (both enabled on 13 Oct) and every other instrument override are preserved.

## Reproduce

```
cd analysis-engine
go test ./internal/structuralconfluence ./internal/strategy/confluencezone ./test/structuralconfluence ./test/strategy ./test/czreplay
CZ_RESULTS_OUT=../docs/strategies/confluence-zone-v3-replay-results.json go test ./test/czreplay -run TestCertification
CZ_SENSITIVITY_OUT=../docs/strategies/confluence-zone-v3-sensitivity.json go test ./test/czreplay -run TestSensitivity -timeout 60m
```

`TestCertification` fails if the committed results drift from the recomputed ones, if the strategy's analysis disagrees
with the engine's published candidates, if any other strategy's candidates differ from master's, or if the v2
re-implementation stops reproducing the production trades.

# Range Edge v3: replay certification

Status: **for owner review. Nothing is deployed, enabled, disabled or restarted by this change.** Detector change only
(PR B, #775): no stop, target, sizing, ladder, arbitration, exposure or enablement setting differs. The contract is
[range_edge.md](range_edge.md#v3-the-edge-must-still-be-a-range-edge); the companion exposure work is
`docs/risk/same-direction-exposure-incident.md` (PR A, #774).

| Judgement | Verdict |
|---|---|
| **Technical correctness** | **Certified for review, with stated qualifications.** The contract is implemented as written, causal, an exact mirror on identical inputs, and the other strategies are byte-identical to master's. Two adversarial reviews by a reviewer that did not write it found one defect class after another (a reclaim read as final, an `outermost` rule that retired intact tops and then exempted its own break, a one-candle gap, a stale reaction, pressure refusing a shooting star); every confirmed one was fixed and pinned by a test. **The last fix (a cluster made by the break cannot exempt it) has not been seen by a reviewer.** Two limits are stated, not fixed: the identity of a re-established range is not stable across window slides, and a break is forgotten when its cluster leaves the 48-bar lookback. |
| **Evidence that v3 improves expectancy** | **No.** v3 refuses 26% of v2's FX decisions, 20% on USDJPY and 18% on XAU, and what it removes is a mix: USDJPY 9 winners and 8 losers (-0.15R), FX 34 and 41 (-0.14R), XAU 5 and 8 (-0.32R). The resulting ladder means (FX pooled -0.07R to -0.04R, USDJPY -0.08R to -0.08R, XAU -0.23R to -0.18R) lie well inside each other's 95 % intervals (about +/-0.15R to 0.3R); neither version shows a positive expectancy in this sample. It is a correction of a failure mode, not a filter that finds winners. |
| **The incident** | **The continuation is refused; the fades are not.** The replay publishes, id for id, the six opportunities production published on 12 Oct (v2). v3 publishes the fades of the intact range (05:55, 06:45) and none while the advance climbed (07:05 to 11:00), where v2 published three; it also refuses the 11:05 SELL (the one production winner). It cannot prevent the fade of a range that breaks afterwards: that is what the exposure policy bounds. |
| **FX and XAU regression** | **Documented, no regression demonstrated** (tables below); every other strategy's candidates are byte-identical to master's on all six captures (asserted). |
| **Production readiness** | Suitable for owner review, not for automatic deployment. The exposure policy (PR A) and this detector fix are independent and complementary: the first bounds the damage of any thesis that fails, the second removes one that fails by construction. |

Baseline: master `80e1be21`. Branches: A `agent/exposure-integrity` (#774), B `agent/range-edge-v3` (#775), this
certification `agent/p3-certification`.

## Replay (v2 against v3)

`analysis-engine/test/rereplay`, six committed production captures (USDJPY, EURUSD, GBPUSD, GBPJPY 2-13 Oct; XAU 14-21
Sep and 1-13 Oct), closed candles, causal hypothetical fills (no spread, slippage, commission, M1 trigger or broker
behaviour): the order is placed when the evaluated candle closes and fills only on a later candle that trades to the proximal
edge; the fill candle can only stop the trade out; a stop and a target in one candle count as the stop; the published
invalidation is widened to the instrument's scalp floor (USDJPY 18 pips) and a stop beyond the instrument cap is not executable;
FX exits 1R/2R half each with break-even after the first, XAU 1R-4R 40/20/20/20; held up to 24 hours (production's Range Edge
winners took up to 15). "Thinned" keeps one decision per symbol, direction and hour. Intervals are seeded 95 % bootstraps
that treat thinned setups as independent, which is optimistic. v2 is what master published on the same captures
(`testdata/range-edge-v2-baseline.json`, produced on master code and re-checked by the reviewer: 373 decisions, exact).
Machine-readable: [range-edge-v3-replay-results.json](range-edge-v3-replay-results.json).

**Development and held-out.** Decisions from 10 Oct 00:00 UTC are reported separately, but they are **not a clean held-out
sample**: that period contains the 12-13 Oct incident that motivated v3. No coefficient was fitted to a trade result (they are the
mirror of the builder's existing break rule and the shared conventions in `config/analysis.yml`), but the failure mode was known
when the contract was designed.

| Scope | Model | Decisions | Thinned | Ladder R [95 %] | 1R win rate | Same-range repeats | Dev / held-out ladder R |
|---|---|---|---|---|---|---|---|
| USDJPY | v2 | 84 | 68 | -0.08 [-0.33, +0.18] (n=65) | 0.46 | 30 | -0.04 (n=53) / -0.25 (n=12) |
|  | v3 | 70 | 60 | -0.08 [-0.36, +0.20] (n=56) | 0.45 | 21 | -0.02 (n=46) / -0.35 (n=10) |
| FX_other (EURUSD, GBPUSD, GBPJPY) | v2 | 215 | 185 | -0.07 [-0.23, +0.08] (n=168) | 0.46 | 58 | -0.10 (n=137) / +0.04 (n=31) |
|  | v3 | 178 | 151 | -0.03 [-0.22, +0.15] (n=131) | 0.49 | 54 | -0.04 (n=114) / +0.01 (n=17) |
| FX_all | v2 | 299 | 253 | -0.07 [-0.21, +0.07] (n=233) | 0.46 | 88 | -0.08 (n=190) / -0.04 (n=43) |
|  | v3 | 248 | 211 | -0.04 [-0.19, +0.11] (n=187) | 0.48 | 75 | -0.03 (n=160) / -0.12 (n=27) |
| XAU_Sep | v2 | 28 | 22 | -0.19 [-0.60, +0.27] (n=21) | 0.43 | 8 | -0.19 (n=21) / +0.00 (n=0) |
|  | v3 | 23 | 19 | +0.02 [-0.54, +0.65] (n=17) | 0.47 | 6 | +0.02 (n=17) / +0.00 (n=0) |
| XAU_Oct | v2 | 46 | 43 | -0.25 [-0.55, +0.05] (n=42) | 0.43 | 10 | -0.29 (n=34) / -0.07 (n=8) |
|  | v3 | 39 | 36 | -0.27 [-0.55, +0.03] (n=35) | 0.43 | 9 | -0.32 (n=31) / +0.05 (n=4) |
| XAU_all | v2 | 74 | 65 | -0.23 [-0.48, +0.02] (n=63) | 0.43 | 18 | -0.26 (n=55) / -0.07 (n=8) |
|  | v3 | 62 | 55 | -0.18 [-0.45, +0.10] (n=52) | 0.44 | 15 | -0.20 (n=48) / +0.05 (n=4) |

What v3 did to v2's decisions (a v2 decision is preserved when v3 publishes the same direction and confirmation; a removed
one is valued by its own simulated ladder):

| Scope | v2 decisions | Preserved | Removed (ladder outcome of the removed) | Removed mean R | v3 first published on a different confirmation |
|---|---|---|---|---|---|
| FX_all | 299 | 220 | 79 (34 W / 41 L / 4 unfilled) | -0.14 | 25 |
| FX_other (EURUSD, GBPUSD, GBPJPY) | 215 | 153 | 62 (25 W / 33 L / 4 unfilled) | -0.13 | 22 |
| USDJPY | 84 | 67 | 17 (9 W / 8 L / 0 unfilled) | -0.15 | 3 |
| XAU_Oct | 46 | 38 | 8 (3 W / 5 L / 0 unfilled) | -0.25 | 1 |
| XAU_Sep | 28 | 23 | 5 (2 W / 3 L / 0 unfilled) | -0.44 | 1 |
| XAU_all | 74 | 61 | 13 (5 W / 8 L / 0 unfilled) | -0.32 | 2 |

Refusals by reason (distinct per break or hour, six captures): `edge_accepted_break` 281, `range_not_reestablished` 212, `break_pending_unreclaimed` 66, `reaction_failed_after_confirmation` 56, `rejection_not_back_inside` 32, `data_gap` 18, `pressure_into_edge` 5.

Re-established ranges (a market that broke out and then made a range of its own stays tradable): 12 of v3's 70 USDJPY
decisions, 32 of 248 on FX and 12 of 62 on XAU carry a break's identity.

### The USDJPY incident, decision by decision

(v2's hypothetical ladder result. Production's Algo Bot admitted five of v2's six SELLs, the sixth (08:20) being refused as
overlapping; all but the 11:12 one stopped out.)

| Evaluated (UTC) | Dir | v2 opportunity | v2 entry | v3 | v2 ladder R |
|---|---|---|---|---|---|
| 2026-10-12 01:40 | SELL | `opp_1e84ba92` | 158.174-158.191 | published as opp_076b5c00 | -1.00 |
| 2026-10-12 02:40 | SELL | `opp_9e6b3f40` | 158.201-158.218 | published as opp_0fa44c97 | -1.00 |
| 2026-10-12 05:55 | SELL | `opp_4ac72614` | 158.381-158.396 | published as opp_26c2e46b | -1.00 |
| 2026-10-12 06:45 | SELL | `opp_ab2e1a3a` | 158.364-158.380 | published as opp_7f0e14bc | -1.00 |
| 2026-10-12 08:10 | SELL | `opp_17d2b4fc` | 158.465-158.484 | not published | -1.00 |
| 2026-10-12 08:20 | SELL | `opp_d6fe8047` | 158.443-158.462 | not published | -1.00 |
| 2026-10-12 08:55 | SELL | `opp_f5cd83b5` | 158.444-158.459 | not published | -1.00 |
| 2026-10-12 11:05 | SELL | `opp_b7d810e8` | 158.629-158.641 | not published | +1.50 |
| 2026-10-12 13:45 | BUY | `opp_c169c622` | 158.580-158.593 | published as opp_7b7064bb | +0.00 |

**Reading it.** v3 refuses the three entries that rebuilt an edge above the 07:05 break with a floor formed before it (08:10,
08:20, 08:55; -1R each) and the 11:05 SELL, which is the same advance (+1.5R in the simulation: the cost of the rule is
a winner). It fades the intact range at 05:55 and 06:45 exactly as v2 did (-1R each): v3 cannot know on 05:55 that 07:05 will
break the top. The two fades are two opportunities under two ids (the identity is not stable), which is what the exposure
policy's one-group cap and the corridor guard are for.

### Limits, and what this does not change

- **Hypothetical**: no spread, slippage, commission or broker behaviour; the ladder counts +0.5R break-even exits as wins; after
  the first target in a candle the break-even stop is not tested in that candle (mildly optimistic).
- **Small samples**: the intervals above are wide; the later-period sets have n=4 to 32 setups.
- **Identity is not stable.** A range re-established after a break is named by the earliest break its edge sits beyond, but that
  break can leave the 48-bar lookback or another broken cluster can start or stop qualifying, so one range can still be published
  under more than one id; and a second, higher range after a second break shares the first break's identity while it stays in the
  lookback. Same-range repeats: USDJPY 30 to 21, FX 88 to 75, XAU 18 to 15. v3 does not deduplicate; the entry-corridor guard and the exposure policy do.
- **Retirement is bounded by the builder's lookback.** A break is known only while its cluster has at least two touches inside
  the 48-bar window; afterwards edges rebuilt above it are initial again (there is no cooldown timer).
- The frozen scalp structure builder is not exactly mirror-symmetric (greedy clustering in ascending price order): end to end a
  reflected market shares about a third of its decisions with the original (EURUSD: 17 of 51, each within an ATR). The v3
  integrity logic is an exact mirror on identical inputs (unit tests and a 200,000-input fuzz by the reviewer).
- **No production telemetry for refusals.** `Analyze` records each refusal and its reason for tests and replay; the live engine
  publishes only opportunities. A follow-up should log the refusal reasons.
- **Entry and stop, audited, unchanged.** The published entry is the barrier band (about 0.8 pip wide); the Algo Bot's fills in
  production landed up to 1.1 pips outside it (12 Oct 06:51 SELL: band 158.364-158.380, filled 158.353; 08:16: band
  158.465-158.484, filled 158.461). The technical invalidation (`range_edge_accepted_break`, 0.7-0.9 pip beyond the band) is not
  the broker stop: the FX scalp envelope floors the stop at 18 pips (about six M5 ATR on a quiet USDJPY), so a position stays
  open for hours after its thesis is technically dead (both 12 Oct 06:51-07:00 entries stayed open to 09:39 with the edge
  broken at 07:05). The targets the plan trades are the instrument's fixed 1R/2R (18 and 36 pips), not the strategy's own
  targets (the equilibrium and the opposite edge sat about 3 and 7 pips away in the 12 Oct entries): median reward:risk to the
  equilibrium in the replay is 0.3, a consequence of that floor. Closing an open scalp on its own technical invalidation, or
  sizing the scalp envelope to the strategy, are execution-contract decisions that need a separate approval; nothing here
  changes an open position, a stop, a target, a volume or a ladder. A plan whose edge is retired while it waits is not
  cancelled by this change (the existing lifecycle cancels a plan when its Go opportunity is invalidated).

## Independent review

Two adversarial reviewers that did not write the code, one per PR, each asked to break it and to reproduce every claim
(repro scripts are theirs; the regression tests that came out of them are in the PRs).

| PR | Pass | Verdict | What it found | Status |
|---|---|---|---|---|
| A, exposure | 1, at `f15eb33e` | APPROVE WITH CHANGES | an atomicity hole (a stale runtime read plus another worker's cleanup let two groups through under `max_groups=1`); claim leak on a Redis error; slot released although the plan was on the stream; unsized plans unknown under money limits; sticky `recovery_required` blocking the symbol; override dropping money caps; structure-id duplicate only vs reservations; unknown refusal terminal; closed plans holding a slot; 8 surviving mutants | all fixed with tests |
| A | 2, at `6dd0d143` | APPROVE WITH CHANGES | an unsized plan beyond the TTL; a 30 s prune window; `recovery_required` with live orders; structure id lost when a plan key expires; M3a/M3b survivors | fixed (two-minute prune, reservation refreshed while unsized, recovery with orders is unknown, two tests); structure-id expiry and the connection-drop races **stated as residuals**; **these last changes have not been seen by a reviewer** |
| B, Range Edge | 1, at `c96df152` | APPROVE WITH CHANGES | reclaim read as final; `outermost` false positive (mid-range cluster) and false negative (stale far barrier); the 48-bar window as a clock; identity flips; one missing candle passing; stale reaction; pressure refusing a shooting star; held-out and fill claims overstated; ~25 surviving mutants | fixed with tests, except the window and the identity, **stated** |
| B | 2, at `d5d1077d` | APPROVE WITH CHANGES | an edge made by the first accepted close exempted its own break (20 of 279 decisions, the 11:05 winner among them); `epoch = min` shares an id across a second range; the doc narrated 11:05 wrongly; 10 surviving mutants | N1 fixed and boundary tests added; the shared-episode identity **stated**; **the N1 fix has not been seen by a reviewer** |

What the reviewers confirmed sound: the policy is inert when undeclared and no committed config declares it; the sizing mirror
against `VolumePlanner.cs`; the Lua atomicity on a real Redis; BUY/SELL mirror of the integrity logic (200,000-input fuzz);
causality of every break date; blast radius (no other consumer of the new fields); the v2 baseline (373 decisions, exact against
master) and the byte-identical results; the doc's numeric claims against the fixture and the JSON.

**No claim of independent certification is made beyond this record.** Each PR's last fixes post-date its last review.

## Deployment and rollback (prepared, not executed)

**Order and independence.** A (exposure) and B (Range Edge) are independent and can be merged and deployed separately; neither
needs the other. This PR changes no runtime behaviour.

**A, exposure policy.** Merging and deploying algo-bot changes nothing by itself: no instrument declares the policy.
To activate, the owner applies `docs/risk/same-direction-exposure-usdjpy.patch` (USDJPY `max_groups: 1`; XAU, EURUSD, GBPUSD and
GBPJPY stay unlimited: the replay does not support a one-group cap on them) and restarts algo-bot only (no executor restart, no
sizing or risk change). Watch the algo-bot log for `v8 group exposure refused` and the rejection reasons
`same_direction_group_limit` / `exposure_unknown`; `exposure_unknown` means the executor snapshot is stale or missing (its
`updated_at` should move every few seconds) and the setup is retried, not dropped. **Rollback:** remove the `same_direction` block,
restart algo-bot; the previous behaviour returns exactly. Leftover reservations (`autotrade:group_exposure:*`) expire by TTL
(600 s) and do not matter once the policy is gone.

**B, Range Edge v3.** Deploy `analysis-engine` with `config/analysis.yml` together (new required keys: `reclaim_closes`,
`pressure_bars`, `pressure_minimum_rise_atr`, `minimum_reclaim_atr`, `rejection_close_strength`; without them the strategy fails to
construct). Opportunity ids change with the version, so range_edge opportunities published under v2 are not continued: deploy
outside an active range if a pending v2 plan matters. Expect about 18-26% fewer Range Edge opportunities (replay: USDJPY 20%, XAU 18%, FX 26%). **Rollback:** redeploy the
previous analysis-engine image and config (the v2 code ignores the extra keys); no data migration. Nothing in Redis, Kafka or
Postgres has a schema change.

**Not changed, by instruction:** risk %, sizing, the XAU ladder, FX single-entry, TP allocations, breakeven/trailing, broker
executor semantics, exposure limits of other instruments, Kafka/Redis ownership, the other 20 strategies and global arbitration.

## Reproduce

```
cd analysis-engine
go test ./internal/techniquezone ./internal/strategy/rangeedge ./test/rereplay
RE_RESULTS_OUT=$PWD/../docs/strategies/range-edge-v3-replay-results.json go test ./test/rereplay -run '^TestCertification$'
```

`TestCertification` fails if the committed results are stale, `TestOtherStrategiesAreUntouched` if any other strategy's
candidates differ from master's, and the incident tests if the baseline stops reproducing production's opportunities or v3
publishes during the climb again. The v2 baseline was produced by dumping the engine's published `range_edge` candidates on
master code over the same captures, before the detector change (`TestDumpBaseline` is the equivalent dump).

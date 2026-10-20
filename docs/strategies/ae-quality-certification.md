# Analysis Engine quality certification (2026-10-14)

Audit of all 21 registered strategies against their own contracts, the execution contract
that trades them, and what they actually produced. Principle: quality over quantity. A
strategy is kept for what it demonstrably detects and pays, not for its history, and is not
removed for a handful of losses.

Status of this document: **proposal**. It changes no live behaviour. Every enablement,
containment, stop, target or sizing change it recommends needs explicit owner approval and
is listed in [ae-strategy-retirement-review.md](ae-strategy-retirement-review.md).
Measurements live in [ae-quality-replay-results.json](ae-quality-replay-results.json)
(`strategy_matrix` is the machine-readable matrix; the human half is
[ae-strategy-matrix.yml](ae-strategy-matrix.yml)).

## 1. What was measured, and what it can and cannot prove

| Evidence | Source | Size | Use |
|---|---|---|---|
| Production trades | `auto_trade_results` joined to `analysis_opportunities` (read-only), production at master 13836f2 (deployed 2026-10-14 09:38 UTC) | 421 algo-auto trades; 172 carry `realized_pips` and so an R | realised behaviour |
| Causal replay | production Redis closed bars (M5/M15/H1; XAU also M1), 16 captures, Sep 14 - Oct 14 2026, all five instruments, through the production engine path | 16,527 published setups, 6,277 thinned episodes (one per symbol, strategy, direction and hour), 5,284 filled | geometry, detection, hypothetical outcome |

What the replay is: closed candles only; an order placed at the evaluated bar's close and
filled by a later bar that touches the proximal edge of the entry band; the fill bar can only
stop the trade; stop first when a stop and a target share a bar; the instrument's stop floor
applied and a stop above the cap refused; constant assumed spreads; the real execution
ladder (XAU zone 1R-4R 40/20/20/20, scalps and FX 1R/2R 50/50, break-even after 1R).
Intervals are seeded cluster bootstraps (clusters are symbol and UTC day, because one thesis
repeats for hours). Development is before 2026-10-07 UTC, out of sample after it.

What it is not: broker P&L. No slippage model beyond the constant spread, no rejects, no
partial fills, one month, one regime. A hypothetical outcome never turns a strategy
"profitable" in this document; it can only add or remove evidence against it.

### Data defect found: realised pips exclude the stop-out loss

`auto_trade_results.realized_pips` is the executor's `volume_weighted_pips`. On every full-stop
group it is exactly 0.0 (91 of 91 full-stop rows that carry it) because the stop-out loss is
never added (`ctrader-engine/src/TradePlanRuntime.cs`, `VolumeWeightedPips`, ingested as
`realized_pips` in `algo-bot/app/autotrade/stats_ingestion.py`). Read naively the pooled
production R is **+0.58R**, which is wrong. This audit counts a full stop as -1R and the
rest from `realized_pips / risk`: pooled **+0.056R, 95% CI [-0.159, +0.283], 172 trades**,
i.e. no demonstrated edge in either direction. The legacy `result_pips` of full-stop rows
is -1.03x the stop, which corroborates -1R. The defect is reported, not fixed here (an
executor change needs its own PR and approval).

For scalps the exit paths alone bound the mean R on all closed trades (full stop -1R,
tp1+BE +0.5R, tp2 +1.5R, tp1+stop in [0, +0.5R]); that bound is reported next to the R-known
figure wherever the R-known sample is small.

## 2. Verdicts

> **Owner decisions after this audit (2026-10-14):** Range Sweep removed; Trendline reworked (v4); nothing is observe-only, so the
> RETAIN_RESTRICTED and OBSERVE_ONLY recommendations below were not adopted and every strategy trades live. The table is the
> audit's own judgement at the time and is kept as the evidence; see [ae-strategy-retirement-review.md](ae-strategy-retirement-review.md).

Four separate verdicts per strategy. Technical: does it detect what it says. Execution: does
its own technical target pay for the stop the executor can place (`room_cover` = technical
reward / executable risk; below 1 the fixed 1R TP1 lies beyond the strategy's own objective).
Performance: replay and production together. Evidence: how much weight they carry.

| Strategy | Ver | Technical | Execution (median room cover; share < 1) | Performance | Evidence | Recommendation |
|---|---|---|---|---|---|---|
| key_level | v3 | PASS | PARTIAL (0.78; 61%) | CONFLICTING | MODERATE | **RETAIN** |
| confluence_zone | v3 | PASS | COMPATIBLE (2.11; 0%) | NEGATIVE (XAU, GBPJPY) | MODERATE | **RETAIN_RESTRICTED**: observe-only XAU, GBPJPY |
| supply | v2 | PASS_WITH_DEFECTS | PARTIAL (0.62; 68%) | NEUTRAL | MODERATE | **RECONSTRUCT** geometry |
| demand | v2 | PASS_WITH_DEFECTS | PARTIAL (0.52; 78%) | NEGATIVE | MODERATE | **RECONSTRUCT** geometry |
| order_block | v2 | PASS | PARTIAL (0.73; 63%) | NEGATIVE (EURUSD, GBPJPY) | MODERATE | **RETAIN_RESTRICTED**: observe-only EURUSD, GBPJPY |
| fvg | v2 | PASS | PARTIAL (0.72; 64%) | NEGATIVE (XAU) | MODERATE | **RETAIN_RESTRICTED**: observe-only XAU |
| ifvg | v3 | PASS | PARTIAL (1.18; 45%) | UNPROVEN | WEAK | **RETAIN** |
| crt | v3 | PASS | COMPATIBLE (2.47; 0%) | UNPROVEN | INSUFFICIENT | **RETAIN** |
| flip_zone | v3 | PASS_WITH_DEFECTS | PARTIAL (0.87; 56%) | NEGATIVE (XAU) | MODERATE | **RETAIN_RESTRICTED**: observe-only XAU |
| session_level | v4 | PASS | PARTIAL (0.85; 55%) | NEUTRAL | WEAK | **RETAIN** |
| trendline | v3 | **FAIL** | **INCOMPATIBLE** (0.05; 100%) | UNPROVEN | INSUFFICIENT | **RECONSTRUCT** |
| range_edge | v3 | PASS | **INCOMPATIBLE on FX** (0.27-0.30; 99-100%) | NEGATIVE | MODERATE | **RETAIN_RESTRICTED**: observe-only on FX |
| box_breakout | v3 | PASS | PARTIAL (0.59; 72%) | UNPROVEN | INSUFFICIENT | **RETAIN** |
| break_retest | v3 | PASS | PARTIAL (0.97; 52%) | NEGATIVE | WEAK | **RETAIN** (quality constant must be rebuilt) |
| momentum_ride | v2 | PASS | COMPATIBLE (2.58; 14%) | UNPROVEN | INSUFFICIENT | **RETAIN** |
| snap_back | v2 | PASS | PARTIAL (1.07; 48%) | UNPROVEN | INSUFFICIENT | **RETAIN** |
| fade_scalp | v2 | PASS | PARTIAL (1.18; 43%) | CONFLICTING | WEAK | **RETAIN** |
| liquidity_sweep | v3 | PASS | PARTIAL (0.96; 50%) | UNPROVEN | INSUFFICIENT | **RETAIN** (XAU already observe-only) |
| range_sweep | v3 | **FAIL** | PARTIAL | **NEGATIVE** | MODERATE | **RETIRE** |
| impulse_pullback | v3 | PASS | PARTIAL | NEGATIVE | WEAK | **OBSERVE_ONLY** (as configured) |
| scalp_breakout_retest | v3 | PASS_WITH_DEFECTS | PARTIAL (1.00; 63%) | CONFLICTING | WEAK | **RECONSTRUCT** target |

Counts: RETAIN 10, RETAIN_RESTRICTED 5, RECONSTRUCT 4, OBSERVE_ONLY 1, RETIRE 1.

Reading the table honestly: in one month the replay finds **no strategy with a
demonstrated positive edge** and several with a demonstrated negative one in a specific
instrument. "RETAIN" means no evidence of harm and a sound detector, not proven profit. The
one retirement is a technical failure, not a losing streak. The restrictions are the
instrument-level cases where the replay interval lies entirely below zero.

## 3. Most serious defects, in order

1. **Range Sweep does not detect liquidity sweeps** (section 5). Technical FAIL, and the realised
   exit paths are the profile of noise entries.
2. **Executor stop-out loss missing from `realized_pips`** (section 1). Distorts every
   performance statistic built on it; not a strategy defect, a measurement one.
3. **Range Edge on FX is geometrically incompatible with the executable stop** (section 6):
   the technical target is 0.27-0.30x the stop in the median setup and shorter than the stop
   in 99-100% of FX setups.
4. **Trendline's target is arithmetically unrelated to its risk**: `entry +/- 2 x |entry - band edge|`,
   not twice the published stop. Median room cover 0.05; every setup has technical RR < 1
   against the executable stop. No production trades, so nothing has been lost yet.
5. **Quality scores cannot rank** (section 4). Nine strategies publish `stars / 3` (two values on
   confirmed setups), Break & Retest and Trendline a constant.
6. **Supply and Demand geometry**: technical RR < 1 in 57% / 62% of setups; the stop is wider
   than the target the strategy itself found.
7. **Scalp Breakout Retest and Flip Zone use fixed-R first targets** (68% / 32% and 28% of
   setups) that are not technical objectives.

## 4. Quality score math

`quality.overall` is a per-strategy number. Audit of what it is:

| Class | Strategies | Consequence |
|---|---|---|
| `stars / 3` (0.667 or 1.0 only on confirmed setups; the scorer publishes 2 or 3 stars) | key_level, box_breakout, crt, flip_zone, ifvg, session_level, confluence_zone, fvg, order_block | Cannot discriminate within a strategy; two setups of very different quality tie. |
| `stars / 3` plus a reaction term (8 values) | supply, demand | Discriminates a little, on the reaction term only. |
| constant | break_retest (0.75), trendline (1.0) | Cannot be calibrated at all. |
| continuous, own measurements (distinct values at 1% resolution) | range_edge (24), snap_back (28), momentum_ride (24), liquidity_sweep (19), scalp_breakout_retest (13), range_sweep (9), fade_scalp (4), impulse_pullback | Can discriminate; calibration below. |

Rebuilt v3/v4 strategies (CRT, iFVG, Break & Retest, Session Level, Range Edge) publish their
raw measurements (ATR multiples, counts) as components beside `Overall`; those are not on a
[0, 1] scale and the audit invariant therefore checks only that they are finite. Nothing is
missing-as-perfect: a missing reaction on a reaction-required strategy is a resting
observation, excluded from the confirmed set.

Calibration against hypothetical R (Spearman, replay, thinned): key_level -0.06, supply -0.01,
demand -0.07, fvg -0.13, order_block 0.00, confluence_zone -0.02, range_edge -0.04,
fade_scalp -0.11, snap_back -0.12, ifvg -0.05, box_breakout -0.15. Against realised R the two
strategies with enough trades: Key Level +0.11 (n=41), Range Edge **-0.18** (n=62), the
Range Edge terciles being +0.20R (high), -0.87R (middle), +0.06R (low). **No strategy's quality
score is monotone in outcome**, and the confluence "touches" factor is not monotone either
(supply and demand, fvg, order_block: the three-or-more-touch bucket is not better than none).
The conclusion is the one the P1 arbitration audit reached for ranking: do not use a quality
score as a threshold or a tiebreaker beyond what the arbitration impact report approved, and
treat any new formula as unproven until it is calibrated chronologically out of sample.

No universal minimum `quality.overall` is proposed, and the P1 global arbitration algorithm is
untouched. A quality reconstruction changes an arbitration-facing number, so it needs its own
offline impact report before any PR.

## 5. Range Sweep (removed in #779)

Historical record: the strategy, its tests and its config no longer exist, so the figures below are the
ones measured at audit time and cannot be reproduced from the repository.

**Code (v3, `internal/strategy/rangesweep` and `internal/strategyutil/scalpcontext.go`).** The Go
detector is a bar-for-bar port of the frozen Python scalp lane (parity proven earlier); the
defects below are in that design, not in the port.

- The "range" is the high and low of the last 24 M5 bars (two hours), accepted when at least
  25 pips wide (XAU). Nothing requires that price ever respected those edges: no touch
  count, no prior reaction, no swing structure.
- A "sweep" is any of the last few M1 bars whose low (high) is within a buffer of the edge,
  that closes back at or inside it, in the trade direction. Price does **not** have to trade
  beyond the edge, so no resting liquidity need have been taken. Sweep and reclaim are the
  same candle, so there is no later confirmation.
- A dealing-range position filter (buy only in the lower part, sell only in the upper part,
  from M15 swings) is the one real context test.
- The entry band is `[edge - buffer, edge + 2 x buffer]`, around the level rather than at
  the reclaim. The stop is measured from the worst fill to the sweep extreme minus a buffer,
  widened to the floor or refused above the cap.
- The target is a fixed 2R (else 1R) multiple of that stop, accepted when it fits the room
  to the opposite range edge: a reward:risk, not a technical objective. The earlier revision
  published the opposite M5 range edge instead.
- The specification (`range_sweep.md`) describes a different strategy: "canonical M5 range
  state", "an M1 excursion beyond one established edge by the configured ATR distance" and the
  opposite range edge as target. The code implements none of those three. Spec and
  implementation disagree, so neither the card nor the journal can be read against the spec.
- Quality is the reclaim close's distance from the edge over the M5 ATR (the `reclaim`
  component is the constant 1). All 39 production values lie between 0.01 and 0.38, so it never
  expresses a strong setup.

**Production (39 trades, 30 on the earlier revision, 9 on v3):**

| Revision (target) | Trades | Full stops | tp1 + BE | tp1 + stop | tp2 | Published target / executed stop (median) | Median entry band |
|---|---|---|---|---|---|---|---|
| opposite M5 range edge | 30 | 20 | 2 | 3 | 4 | 3.7x | 2.1 pips |
| 2R/1R fabricated (v3), XAU | 9 | 6 | 1 | 0 | 2 | n/a | 35.9 pips |

(One further trade of the earlier revision was a manual close.) 26 of 38 closed trades are
full stops (68%); the exit paths bound the mean at **-0.41R to -0.37R** per trade (95% cluster
interval of the midpoint [-0.62, -0.11], which excludes zero). The 6
trades with a recorded R average -0.08R with a 95% interval of [-1.0, +1.6]: too few to say
anything, which is why the exit-path bound, not that figure, is the evidence.

**Replay:** the M1 captures cover XAU Oct 2-6 and Oct 14 only; they publish 10 setups, 9
fill, -0.11R. Not evidence of anything; consistent with, not proof of, the production figure.

**Decision: RETIRE (proposed).** A strategy that can neither be shown to detect its thesis
nor shown to pay for it, and whose quality cannot express confidence, is not worth a V4.
Forcing one was explicitly ruled out. The defensible core idea (a genuine sweep of a
confirmed range, reclaimed) is what Liquidity Sweep and CRT already cover.

## 6. Range Edge

V3 (merged) fixed the detector: the technical thesis is now sound and independently
certified ([range-edge-v3-certification.md](range-edge-v3-certification.md)). The remaining
contradiction is execution, not detection:

| Symbol | Episodes / filled | Median technical risk (pips) | Median room cover | Share room cover < 1 | Hypothetical R [95% CI] |
|---|---|---|---|---|---|
| EURUSD | 77 / 53 | 2.4 | 0.27 | 100% | -0.09 [-0.34, +0.13] |
| GBPUSD | 97 / 79 | 2.8 | 0.27 | 100% | +0.15 [-0.12, +0.39] |
| USDJPY | 96 / 85 | 3.3 | 0.30 | 100% | -0.14 [-0.37, +0.12] |
| GBPJPY | 83 / 65 | 4.3 | 0.28 | 99% | -0.19 [-0.44, +0.13] |
| XAU | 80 / 67 | 28.6 | 1.52 | 16% | -0.22 [-0.45, -0.01] |
| all | 433 / 349 | | | | **-0.09 [-0.21, +0.02]**; dev +0.03, **OOS -0.25 [-0.40, -0.10]** |

A FX range edge scalp has a 2-4 pip technical risk and targets the range equilibrium, a median
0.3 of the stop the executor must place (the instrument scalp floor: EURUSD 12, GBPUSD 15,
USDJPY 18, GBPJPY 22 pips), so the first target usually lies beyond the thesis;
the strategy cannot honestly pay for that stop. This is an execution-contract decision for
the owner, not something a detector change can repair. Production agrees on direction:
-0.19R on 62 R-known trades (CI -0.50 to +0.17; the exit-path bound on all 94 is -0.13R).
Quality is not monotone (section 4).

**Decision: RETAIN_RESTRICTED (proposed).** Observe-only on the four FX pairs. XAU stays,
under review: its geometry is compatible (room cover 1.5), its replay interval is below zero
and its production sample is 15 trades. No re-tuning: the owner's rule that the planner owns
every price stands, and the audit does not widen stops or shorten targets.

## 7. Geometry versus execution (all strategies)

Go owns technical facts; the Algo Bot owns admission, sizing and exposure. This audit reports
the gap and does not close it silently. `technical_RR` (technical reward / technical risk)
and `executable_RR` (technical reward / executable risk) differ because the executor widens
a stop to the instrument floor and refuses one above the cap. Pooled, the share of setups
whose technical reward is shorter than the executable stop is: Trendline 100%, Range Edge
86% (FX 99-100%), Demand 78%, Key Level 61%, Supply 68%, FVG 64%, Order Block 63%, Scalp
Breakout Retest 63%, Box Breakout 72%. Strategies whose targets are structural and distant
(CRT, Confluence Zone, Momentum Ride) sit above 1.

A room cover below 1 is not by itself a defect: the planned ladder may still be positive. It
is a defect when the strategy publishes a target as its thesis and the executor then trades
a ladder that ignores it, which is what makes the published card and the order disagree.

## 8. Test evidence

Run on this branch (nothing skipped):

- `gofmt -l .` clean; `go vet ./...` clean; `go test ./...` in `analysis-engine`: 69 packages
  with tests, all ok, none failing; `test/aequality` runs the all-strategy replay and
  invariants in about 70 s.
- Permanent invariants asserted on every replayed setup of every strategy (`test/aequality`,
  16,527 setups; the test fails on any violation): direction is BUY or SELL; entry band
  finite, positive and ordered; invalidation beyond the entry band on the stop side; a first
  target beyond the entry band on the profit side; `quality.overall` finite and in [0, 1]
  and every component finite; created at or before expiry and at or before the evaluated
  closed bar; formed no later than created; positive ATR; a valid stop envelope. Result: zero
  violations. Absence of future dependence is **not** asserted here; it is covered per
  strategy by the earlier replay suites (`test/ifvgreplay`, `test/crtreplay`, `test/brreplay`,
  `test/czreplay`, `test/rereplay`), and the harness itself only reads closed bars.
- `algo-bot/tests/test_ae_quality_eval_tool.py`: 27 passed (matrix covers exactly the 21 registered
  strategies and uses the documented vocabulary; a restriction, rebuild or retirement always
  carries `owner_approval_required`; a retirement needs a technical FAIL; the committed results
  file matches the matrix; the containment patch names only registered strategies and touches only
  `config/instruments.yml`; a full stop is -1R; exit-path bounds follow the 1R/2R ladder).
- The wider Python allowlist and the .NET suites were not re-run: this PR changes no application
  or executor source (the executor defect in section 1 is reported, not fixed).
- The two config patches were checked with `git apply --check` only, not against the test
  suites.

## 9. Reproduce

```
cd analysis-engine && AEQ_DUMP=/tmp/aeq go test ./test/aequality -run TestReplayAllStrategies -v
psql -f trades.sql                      # the read-only export below, run on production
cd ../algo-bot && PYTHONPATH=. python -m tools.ae_quality_eval /tmp/aeq/records.jsonl \
    --trades /tmp/trades.csv --out ../docs/strategies/ae-quality-replay-results.json
```

The read-only trade export (`trades.sql`):

```sql
\copy (
  select r.group_id,
    r.trade_stream,
    r.symbol,
    r.setup_type,
    r.direction,
    r.closed_at,
    r.result_pips,
    r.realized_pips,
    r.risk_pips,
    r.stop_pips,
    r.exit_path,
    r.planned_reward_risk,
    r.booked_tp_count,
    o.opportunity_id,
    o.strategy,
    o.created_at as opp_created,
    o.state as opp_state,
    o.creation_envelope->'payload'->'entry'->>'low' elow,
    o.creation_envelope->'payload'->'entry'->>'high' ehigh,
    o.creation_envelope->'payload'->'invalidation'->>'price' inval,
    o.creation_envelope->'payload'->'targets'->0->'price'->>'price' t1,
    o.creation_envelope->'payload'->'targets'->0->'price'->>'label' t1label,
    o.creation_envelope->'payload'->'quality'->>'overall' q,
    o.creation_envelope->'payload'->'technical_context'->'confluence'->>'selected_stars' stars,
    o.creation_envelope->'payload'->'technical_context'->'confluence'->>'v2_raw' v2raw,
    o.creation_envelope->'payload'->'technical_context'->>'atr' atr,
    o.creation_envelope->'payload'->>'timeframe' tf,
    o.creation_envelope->'payload'->>'formed_at' formed_at,
    o.creation_envelope->'payload'->'stop_envelope'->>'floor_pips' floor_pips,
    o.creation_envelope->'payload'->'stop_envelope'->>'cap_pips' cap_pips,
    (select string_agg(e->>'code', ',') from jsonb_array_elements(o.creation_envelope->'payload'->'evidence') e) evidence,
    (select count(*) from auto_trade_fills f where f.group_id=r.group_id) nfills,
    (select round(avg(entry_price)::numeric,5) from auto_trade_fills f where f.group_id=r.group_id) avg_entry,
    (select sum(volume) from auto_trade_fills f where f.group_id=r.group_id) vol,
    (select min(filled_at) from auto_trade_fills f where f.group_id=r.group_id) first_fill 
  from auto_trade_results r left join analysis_opportunities o on o.opportunity_id = 'opp_' || substring(r.group_id from 'go_opp_(.*)$') order by r.closed_at
) to '/tmp/trades.csv' csv header
```

The trade export (`\copy ... to '/tmp/trades.csv' csv`) joins `auto_trade_results` to
`analysis_opportunities` on `opportunity_id = 'opp_' || substring(group_id from 'go_opp_(.*)$')`
and reads `creation_envelope` (entry, invalidation, first target and label, quality, stop
envelope, evidence) plus `auto_trade_fills` for the average entry. It is not committed; it
contains account data. The committed captures are closed bars only.

## 10. Limits of this audit

- One month, one regime; production samples are 1 to 95 trades per strategy, 172 with an R.
- The replay's fills are causal but hypothetical; it cannot see slippage, rejects or partial
  fills, and constant spreads understate news bars.
- Independent review is **pending** for every verdict here. The author of the harness is also
  the reviewer. Prior separate certifications exist for Range Edge v3, Confluence Zone v3,
  iFVG, CRT and Break & Retest; this audit relies on them for detector correctness and does
  not repeat them.
- Spec against implementation was compared only for Range Sweep (where they disagree, section 5)
  and Range Edge (certified separately). The other 19 specifications were not re-read line by line.
- Rebuilt strategies' internal claims (CRT, iFVG, Session Level, Break & Retest, Confluence
  Zone v3, Range Edge v3) were checked here for geometry, quality and replay behaviour, not
  re-derived line by line.

## 11. Q1 update (2026-10-20): technical validity, FX stop context and matched random entries

Master at `4c13eafe` (F-7 provenance and opposing-zone fixes in). Tool: `algo-bot/tools/q1_opportunity_quality.py` over the
replay of `analysis-engine/test/aequality` (now with the reaction timestamps and a matched-random baseline,
`TestMatchedRandomBaseline`). 16 active strategies, 14,970 unique opportunities, 6,631 thinned episodes (one per symbol, strategy, direction
and UTC hour), 17 production captures; development is before 2026-10-07 UTC, out of sample (OOS) after. Hypothetical outcomes only
(closed candles, constant spreads, the real 1R/2R ladder, 24 h), never broker P&L.

### 11.1 Technical validity of what is published

Every strategy passes the geometry invariants (stop beyond the band, target beyond the band, finite, ordered). New checks:

| Check | Result |
|---|---|
| Confirmation causal (touch <= confirmation <= the closed bar) | 0 violations in 11,840 reaction-bearing opportunities |
| Confirmation not stale | confirmation age <= 2 bars (lookback 3) for all but 9 (key_level 3, fvg 3, demand 1, plus 2 FVG that bridge the weekend gap: the bar lookback counts Friday's last bars as "current" on Sunday's open); Algo Bot's wall-clock `reaction_not_current_observation` rejects them |
| Price already beyond the invalidation on the publishing bar | 0 |
| Price already at/through the first technical target | range_edge 85 of 508 (17%, mostly XAU), fade 6, flip 8, key_level 5, demand 4, supply 3; their hypothetical R is not different (-0.03 vs -0.11 for range_edge) and the executed ladder does not use that target, so left as is |
| Technical stop beyond the instrument cap (cannot be executed) | demand 25%, supply 18%, flip_zone 16%, key_level 9%, fade 7%, fvg 4%, order_block 4%, others 0-1%: wide zones, filtered by `stop_exceeds_go_invalidation_envelope` |
| Target made of | `nearest_opposing_liquidity` or the strategy's own structure; fixed reward:risk only for key_level (8%), flip_zone (28%), demand (4%), supply (3%), fvg (2%), order_block (1%), liquidity_sweep (100%) and fade_scalp when no pool is far enough |
| Constant components (carry no information) | `reaction`=1 in key_level, flip, crt, trendline, supply, demand, OB, FVG, fade; `room`=1 in range_edge (saturated by its gate); `reclaim`=1 liquidity_sweep; `zone_strength_quality`=`relevance_quality`=1 on the resting zone candidates (F-6) |
| Evidence that is the same on every opportunity | key_level `role_ambiguous` (5,444 of 5,444), range_edge's five codes, fade `legacy_pd_location` |

**Confirmed defects (all fixed in this change):**
1. *Fade Scalp entry zone unit error.* The frozen compat flip-zone retest took a default pip size of 0.1 (gold's), so the band was level +- 0.1
   on every other instrument: 10 EURUSD/GBPUSD fade opportunities were published with a 2,000-pip entry band (up to 1,065 ATR), and the
   JPY pairs carried 20-pip instead of 2-pip bands (p90 of band/ATR 4.28 -> 1.16). Now the instrument's pip size
   (`analysis.legacy_read.compat.pip_size: 0`); the oracle golden is still proven with the frozen 0.1 (`freezeCompatPip`). Effect on the replay: 21 of 182
   fade opportunities change geometry (14 disappear, 7 appear); no other strategy moves.
2. *Target labels.* `fade_scalp` labelled a fixed reward:risk target `fade_opposing_liquidity`; `liquidity_sweep` labelled its fixed 2R target
   `sweep_reversion_objective`. Both now say `fixed_reward_risk` (key_level and the technique strategies already did).

**Verified but not changed (decision-neutral or owner decisions):**
* *Key Level never has an explicit role.* All 3,310 frozen-oracle decisions and all 5,444 replayed Key Level opportunities have role `ambiguous`
  (the role is read from the level kind, and the kinds are `reaction`/`round`). GBPJPY's `require_explicit_role: true`
  therefore rejects every GBPJPY level: **Key Level on GBPJPY is silently disabled** (0 opportunities in three captures, 0 production trades), although the
  override's own comment says it was meant to raise quality, not to disable. Owner decision: keep it disabled on purpose, or drop the unsatisfiable requirement.
* *Technique publishers assert `WickRejection` and `StructuralAgreement` true* whatever the reaction type. For supply/demand/OB/FVG/flip/CZ the stars come from the zone
  score (factors are bypassed), so only the published factor flags and `v2_raw` are untrue; for zero-score instances (the iFVG/CRT helper path) the constants lift the stars and
  measuring them removes 73-84% of those decisions (iFVG 641 -> 175, CRT 94 -> 15), so the oracle parity would break. Left as the frozen contract.
* `SessionContext` is `true` in key_level/ifvg/session_level: the configured sessions (Asia 22-07, London 07-13, NY 13-22) cover all 24 hours, so it is true by construction: an
  information-free +2 raw points (a candidate effectively needs 6, not 8, of the other factors for two stars).
* A frame with fewer than 14 bars gets the detector-ATR fallback 1.0 (price units): one GBPUSD Key Level was published with a 10,000-pip band at the start of a capture. Only a cold start
  with an empty history can do this; admission refuses it.

### 11.2 FX stop context (research only: no stop was changed)

Thinned FX opportunities (n = 4,876). The executed stop is the technical invalidation widened to the instrument floor; compared with ordinary volatility,
spread, protected structure and reachable reward (the nearer of the strategy's own target and the nearest valid opposing barrier of the F-7 book):

| | n | tech stop (pips, median) | executed stop | M5 ATR | executed / ATR | spread / executed stop | widened to the floor | stop beyond nearest M5 / M15 / H1 pivot | technical target < 1R of the executed stop | reachable reward < 1R |
|---|---|---|---|---|---|---|---|---|---|---|
| EURUSD | 1,203 | 4.8 | 12 | 2.75 | 4.4 | 7% | 96% | 95% / 87% / 64% | 74% | 80% |
| GBPUSD | 1,400 | 5.7 | 15 | 3.36 | 4.5 | 7% | 97% | 99% / 98% / 88% | 78% | 81% |
| USDJPY | 1,429 | 8.0 | 18 | 4.48 | 4.2 | 4% | 91% | 97% / 95% / 85% | 71% | 80% |
| GBPJPY | 844 | 11.3 | 22 | 6.59 | 3.6 | 9% | 84% | 97% / 95% / 92% | 73% | 79% |

Reading: the existing executed stops sit **3.6-4.5 M5 ATR** away, **beyond the nearest confirmed M5 pivot in 95-99% and beyond the nearest M15 pivot in 87-98%** of
opportunities (inside the nearest M15 pivot in 2-12%), with spread 4-9% of the stop. They protect the thesis against ordinary noise and structure; the 4-9-pip
micro-invalidation is a different concept (where the pattern fails, not where the position is wrong). The economics are the problem, not the stop: for ~72-78% of FX opportunities the strategy's own
objective is nearer than the 1R rung of the ladder, and for ~80% the reachable reward (own target or first valid opposing wall) is smaller than the executed risk, so
the fixed 1R/2R ladder usually sits beyond what the market structure offers. Splitting by reachable reward / executed stop (< 0.5R, 0.5-1R, >= 1R, buckets fixed in advance) does **not** separate
outcomes stably: difference to matched random entries is +0.06 / -0.14, -0.01 / -0.08 and -0.13 / +0.18 in development / OOS, signs flip in every bucket, so "only take opportunities with reachable
reward >= 1R" is a hypothesis without support, not a rule. Suitable and unsuitable examples are in the PR description. No stop floor, cap or ladder is proposed.

### 11.3 Against matched random entries (chronological)

Each opportunity is compared with the mean of 40 random entries of the same instrument and direction, same technical risk (so the same executable risk after the floor), cost, ladder and 24 h horizon. Cluster bootstrap by symbol-day.

| Window | n | mean R (opportunity) | mean R (random) | difference [95%] |
|---|---|---|---|---|
| development | 3,162 | -0.060 | -0.053 | -0.007 [-0.052, +0.037] |
| out of sample | 2,282 | -0.147 | -0.055 | **-0.092 [-0.152, -0.011]** |

By strategy (development / OOS difference to random; only strategies with >= 50 observations in both windows): key_level (982/680) -0.02 / -0.07 [-0.18, +0.02]; demand (364/288) -0.00 / -0.13 [-0.36, +0.12];
supply (382/315) +0.00 / -0.04; fvg (559/395) -0.01 / -0.11 [-0.23, -0.01]; order_block (201/152) -0.04 / -0.15; confluence_zone (196/98) -0.02 / -0.28 [-0.54, +0.02]; range_edge (194/154) **+0.12 / -0.16 [-0.31, -0.02]**
(the development advantage does not survive); flip_zone (84/63) -0.08 / +0.10. By instrument OOS: EURUSD -0.05, GBPUSD -0.09, USDJPY -0.04, GBPJPY **-0.18 [-0.32, -0.05]**, XAU **-0.13 [-0.23, -0.04]** (GBPJPY was +0.12 in development). Fewer than 50 per window (insufficient): the other strategies.

Classification: **proven**: the signals do not beat random entries in development and are worse in the pooled OOS window; no strategy shows a stable positive edge in both windows. **Plausible, unproven**: ifvg (+0.20 / +0.33,
58/40 observations, intervals include 0); flip_zone EURUSD; fvg and order_block weaker out of sample. **Insufficient**: break_retest, crt, impulse_pullback, liquidity_sweep, session_level, trendline.

Production (`trade_stream = 'algo_auto'`, 503 trades with a known stop, Range Sweep excluded, a full stop counted as -1R): all -0.017R (+-0.109), FX +0.043R (n=307, +-0.131), XAU -0.111R (n=196, +-0.189); FX development +0.214R
(n=92, +-0.240) then OOS -0.030R (n=215, +-0.155). By strategy and FX pair every cell has n < 30; EURUSD Supply/Demand (n=13: 1 win, mean -0.886R, interval -1.12 to -0.66) is the only cell whose interval excludes zero and
is too small to act on. A high `quality.overall` has still not been shown to mean a profitable trade.

## 12. Q2 update (2026-10-20): a structure cannot confirm itself

Tool: `algo-bot/tools/q2_before_after.py` over two replays of `analysis-engine/test/aequality` (master `60115600` vs this change; same 17 captures,
14,970 vs 13,892 unique opportunities). Hypothetical outcomes only (closed candles, constant spreads, the real 1R/2R ladder, 24 h), never broker P&L.

### 12.1 What was inspected

The whole path from the closed bar to the published candidate: the zone chain (`techniquezone`), the reaction routine (`reaction`), the shared qualification
(`strategyutil/legacy_detect.go`: `Reaction`, `Finish`), the technique publishers (`technique_decision.go`, `technique_candidate.go`), the confluence scorer (`confluence/score.go`),
Flip Zone, Key Level, Fade Scalp, Range Edge, iFVG, CRT, Session Level, Trendline and Confluence Zone v3 (`structuralconfluence`), and the opposing-zone/target geometry (F-7, unchanged).

| Strategy | Result |
|---|---|
| Fair Value Gap (`fvg`) | **corrected**: the gap's own impulse candle confirmed it (480 of 695 confirmed opportunities) |
| Supply, Demand | **corrected**: the displacement leg's own bars confirmed the zone (222 of 1,742, 252 of 1,894) |
| Order Block | **corrected**: bars up to the qualifying break of structure confirmed it (20 of 549) |
| Flip Zone | **corrected**: the break bar that made the flip confirmed it (194 of 222) |
| iFVG, CRT, Session Level, Fade Scalp, Trendline, Range Edge | checked: every replayed touch and confirmation is after the structure's formation (iFVG v3 had already fixed this for itself, `test/ifvgreplay` reproduces the frozen v2 defect) |
| Confluence Zone v3 | unchanged: the entry is the true intersection of facts (`structuralconfluence.Find` cuts elementary segments and keeps those covered by >= `minimum_facts` *independent* groups, every fact timestamped by availability). The union geometry (`BuildConfluenceBands`) is the frozen oracle's and feeds no production strategy |
| Key Level, Break Retest, Liquidity Sweep, Impulse Pullback | unchanged (levels and pools have no formation bar; see 12.4) |

### 12.2 The defect

`reaction.Evaluate` has a `MinIndex` ("a structure cannot react before it exists") that `strategyutil.ConfirmReaction` passes for the strategies that use it, but the technique
publishers and Flip Zone called the detector without it. They searched the last three bars for a touch and a confirmation, so the bars a structure was *built on* could be its reaction:

* a fair value gap is anchored on its third candle; the middle (impulse) candle spans the gap by construction, and as a bearish gap's impulse it closes back below the gap's top with a wick above it, which is
  the `strong_reclaim` pattern. 431 of the 480 removed FVG opportunities have the touch and the confirmation on the bar before the gap's anchor;
* a supply/demand zone is complete when its displacement leg ends (`Zone.BreakIndex`), an order block at its break of structure; a touch on the leg is the leg;
* a flip zone is made by the structure break (its `Origin` and `BreakIndex` are the break bar); the break candle closes through the band it creates and "reclaims" it. Of the 91 oracle flip decisions 80 were the break bar itself.

`MarkMitigation` already counts a zone's touches only from the bar after `max(origin, break)`; the reaction check now uses the same boundary (`Instance.TouchableFrom`, `Zone` via `LegacyDetector.ZoneReaction`).
Higher-timeframe supply/demand instances are bound at the close of the higher bar that completed them. CRT instances are not narrowed (H1-anchored; its sweep/reclaim is checked separately).

**Also corrected, with no change of decisions.** The factors published for the four technique strategies asserted a wick rejection and a structural agreement on every decision and a displacement whenever the zone score was 8 (a zone-quality threshold named as a candle fact). They now follow the confirmation:

| factor published true | before (supply / demand / FVG / OB) | after |
|---|---|---|
| wick rejection | 100% / 100% / 100% / 100% | 26% / 26% / 48% / 14% |
| structural agreement | 100% / 100% / 100% / 100% | 54% / 57% / 24% / 76% |
| displacement | 98% / 98% / 99% / 100% | 20% / 17% / 28% / 10% |

Stars and `quality.overall` of every kept opportunity are identical (a scored zone is rated by its zone score; the factors only matter when the zone score is 0, where the constants lifted the decision to the star floor by themselves).

### 12.3 Before and after

| | before | after | removed | later reaction appears |
|---|---|---|---|---|
| fvg | 1,688 | 1,209 | 480 | 1 |
| demand | 2,627 | 2,430 | 252 | 55 |
| supply | 2,454 | 2,266 | 222 | 34 |
| order_block | 737 | 717 | 20 | 0 |
| flip_zone | 222 | 28 | 194 | 0 |
| all other strategies | 7,242 | 7,242 | 0 | 0 |

Confirmed-opportunity stars (2 / 3): FVG 626 / 69 -> 210 / 6, Demand 1,706 / 188 -> 1,613 / 84, Supply 1,574 / 168 -> 1,495 / 59, Order Block 532 / 17 -> 520 / 9, Flip Zone 61 / 161 -> 10 / 18. Flip Zone's stars were highest where the formation bar was the reaction.

Were the removed opportunities good? Against matched random entries (same instrument, direction, technical risk, cost, ladder and horizon; 40 draws; cluster bootstrap by symbol-day):

| set | n | mean R | random | difference [95%] |
|---|---|---|---|---|
| removed (all) | 828 | -0.065 | -0.045 | -0.021 [-0.107, +0.068] |
| removed, development / out of sample | 457 / 371 | -0.095 / -0.030 | -0.052 / -0.035 | -0.042 [-0.153, +0.074] / +0.006 [-0.125, +0.151] |
| kept (all) | 5,181 | -0.098 | -0.055 | -0.044 [-0.092, +0.002] |
| kept, development / out of sample | 2,997 / 2,184 | -0.057 / -0.155 | -0.053 / -0.057 | -0.004 [-0.056, +0.042] / **-0.098 [-0.160, -0.016]** |

Per removed strategy the difference to random is within noise (FVG -0.05, Demand +0.04, Supply -0.05, Order Block +0.10 on n=16, Flip Zone +0.02). The removal is a **validity** correction: it does not improve or worsen
the hypothetical expectancy in any way these samples can resolve, and nothing here says the remaining opportunities have an edge (11.3 still applies). Replay cases: an invalid FVG
(`XAU Sep` SELL 1789410600, touch and confirmation 1789410000, the bar before the gap's anchor 1789410300: published, then a +2.2R target hit that the structure had not earned), the same outcome class
for a valid one (`GBPUSD early` SELL 1790639700, touch 1790638800 four bars after the anchor 1790634900: +1.5R) and a valid loss (`XAU Sep` SELL 1789640700, anchor 1789636800, touch 1789639800: -1R).

### 12.4 Not changed, and what cannot be proven

* `quality.overall` stays stars / 3 for the technique strategies and its `reaction` component stays 1: a candidate exists only with a confirmed reaction, so the component is informationless but not false; no consumer ranks by it (`quality_ranking: false`).
* The reaction routine does not require the confirming bar itself to sit at the band (a touch within three bars, then any rejection pattern on a later bar). That is the frozen contract; whether a stricter rule would help cannot be settled from these samples and was not changed.
* `SessionContext` constant, Key Level GBPJPY disabled by an unsatisfiable `require_explicit_role` and the star cap for touched zones are as reported in section 11.
* Production closed trades (`trade_stream = 'algo_auto'`) carry no reaction timestamps, so the trades that came from a formation-bar reaction cannot be separated; FX auto-trades come from Key Level, Range Edge, Supply/Demand (44), Fade and Confluence Zone only, so FVG and Flip Zone have no production sample at all.
* No claim of better profitability is made: the corrected rules remove opportunities that were not a reaction to the structure they claimed, nothing more.

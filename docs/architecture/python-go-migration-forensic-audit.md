# Python → Go migration: forensic audit of the Analysis Engine

Written for: the owner and the engineer who will decide which findings become fix PRs.
Status: read-only audit. No live detector, execution rule, sizing, risk setting, strategy enablement, instrument config, schema or
service was changed; production was touched only through read-only queries (`auto_trade_results`, `analysis_opportunities` shape). No fix PR
is opened here; the findings are for review first.

Deliverables of this audit: this file, [python-go-parity-matrix.json](python-go-parity-matrix.json) (function level),
[python-go-zone-diff.json](python-go-zone-diff.json), [python-go-quality-diff.json](python-go-quality-diff.json),
[python-go-strategy-diff.json](python-go-strategy-diff.json), [python-go-runtime-contract.md](python-go-runtime-contract.md), and the tools under
`analysis-engine/test/migrationaudit/` (section 17 reproduces everything).

## 0. Answers

| # | Question | Answer | Evidence |
|---|----------|--------|----------|
| 1 | Was the migration mathematically faithful? | **Yes for the analysis layers, measured; with three confirmed porting defects, none of which reaches a trade today.** Against the frozen Python (`1c9f323`) the Go frame equals the oracle on 4,353 frame-checkpoints (1,451 hourly checkpoints × 3 timeframes, 11 real captures, 5 instruments): 1,433,265 compared elements, 0 oracle-only, 0 Go-only, largest numeric difference 2.5e-16 (Wilder ATR), tolerance 1e-9. Against the profitable-week Python (`a1c77584`) the same holds except the GBPJPY level book (F-1) and the profit-week contract zones' scores (F-2). | §5, §6, §7 |
| 2 | Did Go preserve Python's structures and zones? | **Structures: yes** (swings, breaks, structure kind, legs, levels, pools, grabs, sessions, dealing range, momentum, scalp structure: exact). **Zones: the faithful population exists and is exact, but it is one of three** (§7); the production M5 zone population and the zone book that execution reads are different populations with fabricated fields (F-6, F-7). | §7 |
| 3 | Did quality / confluence preserve formulas and factual inputs? | **Confluence formula: exact** (62,208 combinations vs both references, 0 mismatches). **Inputs: truthful where the strategy supplies its own detector confluence; engine-derived (V2 inputs, constants, keyword inference) for impulse_pullback and resting zone candidates.** **Quality has no Python counterpart**: it is Go-native, and for 11 of 16 strategies it is `stars/3`, i.e. two values (0.667, 1.0), because stars are floored at 2. | §8, §9 |
| 4 | Which later changes intentionally modified decisions? | 21 Sep Python behaviour changes (wick touches, reversal order blocks, technique zone validity); the Go v3/v4 rebuilds of break_retest, crt, ifvg, confluence_zone, range_edge, session_level, trendline, liquidity_sweep; supply/demand >2-touch cut; strategy independence (+1,642 publications the frozen publisher suppressed); arbitration without quality; FX stop-floor increases (+2 to +8 pips). Each is dated in §3 and classified in §10. | §3, §10 |
| 5 | Are current Go opportunities technically correct? | **Correct where parity is proven (key_level, flip_zone, fade_scalp on 3 symbols, the technique publishers through the helper path) and consistent with the frozen contract. For the nine reconstructed strategies parity is not the question; their own replay certifications check internal invariants (no future dependence, causal order), not market correctness.** impulse_pullback is **unverified**: its parity proof was deleted on 14 Oct (F-4). | §10 |
| 6 | Which differences materially affect real trades? | **The zone book that gates target room (F-7)** is the only *porting* difference found that reaches execution: Go's book is built from other zones than the frozen one (§7.5): the room in front of an entry is 71% of the time smaller (median -19 pips), and the hard-block proxy fires 37.6% against 1.3-5.3% of the time; the gate produced 3,415 distinct rejections in 14 days against 424 trades. Next: **execution geometry** (a design layer, not a port): FX stops are the instrument floor (about 3x the technical stop) and targets are fixed 1R/2R of that stop, so no parity statement about a candidate's geometry describes the traded geometry; **bias source** (F-5: the adapter's `htf_bias` agrees with the frozen Python's on 61.1% of checkpoints and with_bias/counter_bias softens the room gate); the reconstructed strategies. F-1, F-2, F-3 have no measured effect. | §7.5, §12, runtime contract |
| 7 | Is there evidence the migration itself caused the performance degradation? | **No evidence for it, and none against it that the data can reach.** The only profitable reference is 9 trades (6W/3L, win rate 95% interval 35%-88%); production data begin 29 Sep, after the cut-over, so there is no Python-era production sample; Key Level, the one strategy proven exact, trades at -0.04R (±0.24, n=120). The investigation in PR #791 found no edge in signals against random entries. A faithful port of a signal without edge reproduces the absence of edge. | §13 |

## 1. Scope, method and honest limits

**Method.** A source-to-source oracle, never an inference from names, comments or certification documents. The frozen Python
(`a1c77584`, the end of the profitable week; `1c9f32303b7e9f15fc8e3767b5045ac1e8a37adc`, the last Python analysis) runs in isolated git worktrees
under the recorded historical settings (`python-effective-settings`: zigzag 0, swing fractal 2, ATR 14, zone width body,
`zone_reconcile_mode: shadow`, per-instrument `key_level_min_touches` GBPUSD/EURUSD/USDJPY/XAU 2, GBPJPY 3). The *same*
committed closed-bar captures go through the real Go engine (`replaycapture.Replay`). Both sides dump the same JSON layers at every
hourly checkpoint (`oracle_dump.py`, `dump_test.go`); `oracle_compare.py` matches elements **by value** (key), never by position, and
attributes each difference to the earliest diverging layer. Python is a test oracle only; nothing here re-activates it as an authority.

**Captures.** 11 committed production captures: XAU (21 Sep, 6 Oct M1, 14 Oct), EURUSD (6, 14 Oct), GBPUSD (5, 14 Oct), GBPJPY (6, 14 Oct),
USDJPY (5, 14 Oct); M5 150 / M15 250 / H1 400 bar windows exactly as the frozen scanner loaded them; 1,451 hourly checkpoints.

**Limits, stated so they are not mistaken for results.**
* The oracle covers the analysis chain and the detectors that have goldens; it does **not** cover MAD, candle confirmation, the fib ladder itself, or the executor (UNVERIFIED in the matrix).
* Hourly checkpoints, not every bar, for layer comparisons (decision goldens are per bar).
* The production DB begins on 29 Sep 20:32 UTC. There is no Python-era production sample, so "did the port degrade performance" cannot be answered by comparing like with like (§13).
* The Go analysis cannot see broker commission, swap or real spread; neither could the reference.
* No claim in this report depends on a Go comment. Comments are recorded in the matrix as *claims*.

## 2. Mission restrictions honoured

Read-only on production (three SELECTs on `auto_trade_results`). No change to any live detector, Algo Bot execution logic, sizing, risk,
enablement, instrument config, Kafka schema, TradePlan V8, executor, or existing position. No deployment or restart. The only repository
changes are `analysis-engine/test/migrationaudit/` (tools and non-gating tests) and `docs/architecture/`.

## 3. Version timeline

| Date (UTC+7 unless noted) | Event | Commit / PR |
|---|---|---|
| 14-18 Sep | **Profitable week**: +567 pips, 6W/3L (XAU, 9 trades) | ends at `a1c77584` (19 Sep 00:22) |
| 21 Sep | Python analysis changes: wick touches count toward key-level touches; reversal (CHoCH) order blocks and dead-zone removal; technique zones stay valid while they hold; technique instances use unmerged zones with their own origin; with-bias entries exempt from the premium/discount gate | `15b0c29d`, `54bbcdc6`, `6fab6b02`, `04db63bc`, `c65bafdf` |
| 22 Sep | Go migration starts | `e47037c4` |
| 23 Sep | Phases S4-S9: canonical session, fib, key level, trendline V2, 7-strategy slice, Kafka publication | `a3944658` … `56c3e285` |
| 28 Sep | Go becomes the sole automatic source; first Go key_level port | `3382580f`, `791fdd3a`, `730789b9` |
| **29 Sep 20:32 UTC** | **First production trade in the DB** (Go-sourced). The strategies on trial were loose ports | - |
| 1 Oct | Python zone chain ported with parity (`legacyzone`/`techniquezone`), technique instances, CRT port, per-instrument Key Level rules (GBPJPY: 3 touches, explicit role) | `5353de7a`, `ae3d57b0`, `2b108521`, `ab0aee91` |
| 2 Oct | Python analysis retired from production; **frozen oracle** | `fb048c82`, `1c9f3230` (PR #719) |
| 2 Oct | Confluence scoring ported | `1b34061b` |
| 4-5 Oct | "Restore legacy detector parity", "Restore break-retest and range-edge parity", "Prove detector parity against the frozen oracle and fix what it found" | `b512da11`, `150d9506`, `74949234` |
| 6 Oct | **Profit-week Key Level restored and proven** on six captures; technique publishers proven on five instruments | `32771ed8`, `2ed77d10`, `5eaee376` |
| 8 Oct | Session Level, Flip Zone, Trendline, Box Breakout restored to frozen decisions; Impulse Pullback ported; strategy independence | `c9663738`, `7d398bbd`, `8e5a20a2` |
| 9-14 Oct | **Reconstructions** away from the frozen behaviour: CRT v3, Break & Retest v3 (9 Oct), iFVG v3, Session Level v4 (12 Oct), Confluence Zone v3 (13 Oct), Range Edge v3 (14 Oct), Trendline v4, Supply/Demand v3 (>2 touches), "allow every strategy live" (14 Oct) | `93dd105f` … `7e399bd2` |
| 14 Oct | Range Sweep removed; **its removal deletes the only Impulse Pullback parity tests** | `9d705a02` |
| 15 Oct | Arbitration reads no quality; Snap-Back, Momentum Ride, Breakout Retest Scalp and Box Breakout purged (16 strategies remain) | `f6a6d6ec`, `498366da`, `00c28055` |

Two facts about this timeline matter for every performance statement. First, **production ran loose ports from 29 Sep to 3-8 Oct**,
before any parity proof existed; the parity restoration is dated 4-8 Oct. Second, **the execution layer was not constant**: between
`a1c77584` and now the FX stop envelopes rose (EURUSD 10-18 → 12-20, GBPUSD 12-22 → 15-25, USDJPY 10-18 → 18-28, GBPJPY 15-30 → 22-35 pips; XAU
50-60 unchanged), and `execution_policy.py` changed by 868 lines. A comparison of "Python week" against "Go weeks" therefore changes the signal source *and* the
execution policy at once.

## 4. Evidence base

| Instrument | Count |
|---|---|
| Frozen Python references | `a1c77584`, `1c9f323` (worktrees, historical `config/trading-bot.yml`) |
| Captures / checkpoints | 11 / 1,451 hourly (113-155 per capture) |
| Frame-checkpoints compared | 4,353 per reference (checkpoint × M5/M15/H1) |
| Layers | atr, swings, structure, breaks, levels, legs, zones, technique_zones, technique_instances, pools, grabs, sessions, dealing_range, regime, momentum, scalp_barriers, scalp_range (+ htf_bias, barrier book) |
| Confluence grid | 2⁷ factor combinations × touches 0-5 × zone score × zone touches × MAD = 62,208 per reference |
| Strategy goldens | key-level (a1c): 3,310 decisions; technique (1c9): 2,958; detector (1c9): fade_scalp 94, range_edge 319, break_retest 28; legacy (1c9): flip 91, session_level 316, trendline 8 |
| Execution traces | every registered Python detector evaluated at checkpoints of 16 captures with call tracing (`oracle_trace.py`) |

**Tolerance.** Relative 1e-9 (absolute for values near zero); timestamps, indices, kinds and counts exact. A difference is reported with its
maximum absolute and relative error and an example.

## 5. Numeric parity and ATR conventions

The frozen Python used three different ATR notions and the port kept each in the consumer that used it:

| Python notion | Python consumers | Go counterpart | Measured |
|---|---|---|---|
| `atr_series`: simple rolling mean of true range, `min_periods=1` | swings/zigzag, key levels, displacement legs, zones, pools, regime, scalp structure | `LegacyFrame.ATR` (`indicator.SimpleATR`) | last value and 5-bar tail: 0 difference on 4,353 frames |
| `atr_scalar`: median of that series | regime height, scalp ranges | `techniquezone.ATRScalar` | 0 |
| `indicators.atr`: Wilder RMA (pandas_ta) | detector bands, reaction windows, stops (`_atr(ind)`) | `LegacyFrame.DetectorATR` (`indicator.WilderATR`) | max abs error 2.5e-16 |
| *(none)* | | canonical ATR (config: simple, 14), published as `Technical.ATR` and used by V2 structure/zone | not a port; `Technical.ATR` differs from the detector ATR by the ratios below |

The conventions are not interchangeable. Over the checkpoints, Wilder/simple has a median of 1.005-1.032 but ranges 0.68-1.71, and the scalar
median/simple ranges 0.18-3.95 (M5 EURUSD). A consumer that took the wrong one would be wrong by tens of percent at individual bars; the port did not.
The one place the published ATR is not the detector ATR is the Kafka `technical_context.atr` (§ runtime contract stage 3); the adapter uses it only for
the thesis bucket size.

**Layer result against `1c9f323`** (4,353 frame-checkpoints, 11 captures):

| Layer | Matched elements | Oracle-only | Go-only | Max abs error |
|---|---:|---:|---:|---|
| atr (simple last/tail, scalar, Wilder) | scalar fields | 0 | 0 | 2.5e-16 |
| swings | 194,902 | 0 | 0 | 0 |
| breaks | 68,836 | 0 | 0 | 0 |
| structure kind | scalar | 0 | 0 | 0 |
| legs | 79,229 | 0 | 0 | 0 |
| levels | 47,331 | 0 | 0 | 0 |
| pools | 47,269 | 0 | 0 | 0 |
| grabs | 420,260 | 0 | 0 | 0 |
| sessions | 55,854 | 0 | 0 | 0 |
| dealing_range, momentum | scalar | 0 | 0 | 0 |
| scalp_barriers / scalp_range | 13,053 | 0 | 0 | 0 |
| zones (merged, scored) | 94,644 | 0 | 0 | score 0; reasons: formatting only (F-3) |
| technique_zones | 406,999 | 0 | 0 | score 0; reasons: formatting only (F-3) |
| technique_instances | 4,888 | 0 | 0 | 0 |
| regime | scalar | 0 | 0 | `kind`, `legacy_kind` 0; `new_kind` differs in 35 of 4,353 (0.8%) |
| `htf_bias` (legacy) | 1,451 | - | - | 1,451/1,451 equal |

`regime.new_kind` is a Python telemetry field (read only by `scanner.py:3145` for log text); the Go port does not read it (`regime.go` retains it as
a label). It carries no decision.

**Layer result against `a1c77584`**: identical to the above for swings, breaks, structure, legs, pools, grabs, sessions, atr, dealing range,
momentum, scalp structure, regime. Differences: `technique_zones`/`technique_instances` do not exist in that Python (introduced 21 Sep), `levels` differs on GBPJPY
(F-1: 2,628 oracle-only, 4,453 Go-only elements), and contract `zones` differ in score (F-2: 50,317 of 93,934 matched) and, on GBPJPY, in membership (516/520).

## 6. Structure and causality

* **Confirmation timing.** Across 194,902 reported swings in each implementation, **0** is reported before its fractal is confirmed
  (`confirmed_index_t <= checkpoint time`); the frozen Python and Go are identical (`causality_check.py`).
* **Repaint.** Between consecutive hourly checkpoints, 490 of 184,238 swings (0.27%) present in the earlier one are absent from the later one although inside both
  windows. This is the frozen algorithm's consolidation (a later, more extreme swing of the same kind replaces an earlier one); Go reproduces it exactly (same 490).
  It uses no future data.
* **Window dependence of breaks.** 12,985 of 67,176 breaks (19.3%) seen at one checkpoint are labelled differently, or absent, at the next: BOS/CHoCH and the
  structure they anchor to depend on where the 150/250/400-bar window starts. The port reproduces this exactly (12,985 for both). It is a property of the
  frozen design, not of the port, and it is a reason why a decision made at bar *t* may not be reproducible by re-reading the same history through a longer window
  (see the 400-bar zone population, §7.3).
* **Causal structure.** `build_context(causal_structure=False)` is what the frozen scanner called; `_structure_breaks_causal` was never executed in production and has no comparison.
* **As-of / no future dependence.** Established for the rebuilt strategies by their replay suites (`test/crtreplay`, `czreplay`, `rereplay`, `slreplay`), which re-run every bar with and without the future in the input; this audit did not repeat them.

## 7. Zones

### 7.1 Three populations (and a fourth consumer)

1. **Legacy frame** (`legacyread`, `techniquezone`): the faithful port of `engine._analyze_tf`; exact (§5). It carries the profitable-week contract (`SwingLevels`, `ContractZones`) and the post-21-Sep contract (`Levels`, `Zones`, `TechniqueZones`). Every *executable* strategy reads it.
2. **V2 canonical** (`zone.Update`): a Go redesign (hierarchical structure, strength 0-1, relevance 0-3, layers, richer states). It is the zone state of the engine snapshot.
3. **Production M5 `Context.Zones`** (`techniqueZoneState`): technique instances re-built on **400** bars (hard-coded, not configuration) and published with `Strength = 1`, `Relevance = Immediate`, `Layer = Intermediate`, state `Fresh|Touched`.
4. **The zone book** that Algo Bot's execution-time target-room check reads (§7.5) is built from population 2, **not** from 1 or 3.

### 7.2 Population 1 versus the frozen Python

Exact (§5): 94,644 merged zones and 406,999 technique zones matched by (side, bottom, top, origin, source) with identical touches, mitigated, break kind/index, source(s) and score.
The only difference is the text of score reasons (F-3): `round 1.1340000000000001` for `round 1.134`, on 54,750 of 501,643 reason lists; after normalising the number every one of the 501,643 matches.
Python's `reconcile_opposing` ran in shadow mode only (`zone_reconcile_mode: shadow`: "zone reconcile aborted" in every probe) and never changed a published zone, so its absence from Go is not a divergence (`reconcile_probe.py`).

### 7.3 Population 3 versus the frozen technique instances (zone-level table: `python-go-zone-diff.json`)

M5, 11 captures, 1,451 checkpoints; match key (technique, side, low, high, origin candle):

| Class | Count | Root cause |
|---|---:|---|
| Matched | 2,205 | geometry exact |
| Matched, Python `mitigated` | 1,342 | Go publishes `Touched`; the M5 population has no mitigated/invalidated state |
| Matched, strength | 1,573 comparable | Go `Strength` is the constant 1; Python score 2.0-20.0: not comparable scales (strength delta = 1 − score) |
| Python-only, same origin, geometry differs | 246 | **243** entry-clipped instances: Go deliberately publishes the structural boundary (`technique_zones.go:74-77`); 2 un-clipped, 1 clipped-other: window effect |
| Python-only, absent from Go | 85 | present in the faithful 150-bar frame, absent from the 400-bar population: 81 mitigated, **4 live** |
| Go-only | 856 | 471 originate before the frozen 150-bar window, 385 inside it: the longer history changes legs, ATR and swings, so different zones exist; 183 `fresh`, 673 `touched` |
| Go-native V2 kinds (flip, breaker) carried through | 860 | not in the technique chain |

So **28% of the production M5 supply/demand/OB/FVG/iFVG population does not exist in the faithful 150-bar frame**, and 4 live frozen zones are missing.
None of this reaches a trade directly: executable strategies read population 1, and resting candidates built on population 3 are rejected at the adapter (`reaction_confirmation_unavailable`). Population 3 is read by the resting paths of supply, demand, order_block and fvg and by the engine-derived confluence (`confluence_facts.go:147,196`). `Strength = 1` makes the resting `quality.overall` of supply/demand/order_block/fvg depend on touches alone
(7 distinct values, 0.733-1.0).

Field-by-field fidelity of `techniqueZoneState` (population 3) against the frozen instance it republishes:

| Field | Fidelity | Measured |
|---|---|---|
| origin (time of the origin candle) | exact | all 2,205 matched zones share it by construction of the key |
| boundaries | exact except entry-clipped instances | clipped instances publish the structural band (243 of 246 geometry differences); un-clipped: exact |
| side / technique (source) | exact for the five technique kinds; **merged sources are not carried** (a `Zone` has one `Kind`) | `sources` lists of the merged zones do not exist in this population |
| touches | exact on M5 | 0 disagreements on 2,205 matched zones (`touch_count` = frozen touches) |
| mitigation | **lost** | every frozen-mitigated matched zone (1,342) is `Touched`; there is no mitigated state |
| strength / score | **fabricated, frozen score absent** | `Strength` is the constant 1; `LegacyScore` holds the instance's own small score (e.g. 1 or 1.5), not the frozen zone score (2.0-20.0): equal on 0 of 223 comparable zones |
| relevance / layer | **fabricated** | `Immediate` / `Intermediate` for every zone |
| availability (when it may be traded) | not represented | `CreatedAt` = origin time, i.e. earlier than the frozen break confirmation |
| invalidation | not represented | a zone the frozen engine would no longer publish is absent only if the longer window changes it; otherwise it stays `Fresh|Touched` |
| timeframe | constant `M5` | correct for the primary timeframe only (the population is built for M5 alone) |
| provenance | partial | id `technique:<kind>:<side>:<origin>:<low>:<high>`; no sources list, no score reasons |

The M15/H1 comparison of `production_zones` (V2) against the frozen instruments is in the JSON for completeness; it compares different designs (the V2 population keeps invalidated zones: about 180,000 invalidated M15 zones against 1,003 matches) and is not a parity statement.

### 7.4 Profit-week contract zones (`ContractZones`) - F-1 and F-2

* **F-1 (CONFIRMED_PORTING_BUG, latent)**: `legacyread.go:189` builds `SwingLevels` with `cfg.LevelMinimumTouches` (2). The frozen engine built *every* level with the instrument's
  `analysis.levels.minimum_key_touches` (`engine.py:341-347`; GBPJPY 3). Measured on the GBPJPY captures: 505 of 1,284 Go swing levels have 2 touches (the frozen engine kept none), `levels` 0/113 and 1/154 identical checkpoints,
  oracle-only 170/191, Go-only 482/701. **Experiment** (one-line change applied to a scratch tree and reverted): with the instrument value the GBPJPY levels become identical (617/617 and 733/733 elements, 0 mismatches).
  Impact: the Key Level strategy applies its own touches gate (`applyKeyLevelOverrides`, `keylevel.go:206`), so the decisions are unchanged; what still differs is the GBPJPY level book, the flip zones built from it and the
  opposing-zone check. GBPJPY has 0 Key Level decisions in the oracle (the parity there is vacuous) and 0 Key Level production trades. Severity: low. Smallest fix: pass `frameTouches` instead of `cfg.LevelMinimumTouches` at `legacyread.go:189`.
* **F-2 (CONFIRMED_PORTING_BUG, latent)**: the frozen engine scored every zone against the higher timeframes' zones (`_apply_mtf_zone_scores`, "HTF zone" +3). `legacyread.Build` re-scores `Zones` (`:240-243`) and `TechniqueZones` (`:245-249`) but never `ContractZones`.
  Measured on EURUSD 14 Oct: "HTF zone" appears in 6,825 `Zones` reason lists and in **0** of 7,939 `ContractZones`; against `a1c77584`, 50,317 contract-zone scores differ (max 7.0). Impact: Key Level reads the contract zones only for the optional `minimum_sell_zone_score` floor,
  which is unset (0) in `config/*.yml`, and for the opposing-zone veto, which does not use the score. Severity: low. Smallest fix: apply the same HTF re-score to `ContractZones`.
* Reproduction: `AUDIT_FINDINGS=1 go test ./test/migrationaudit -run 'TestF[12]' -v` (measures); `AUDIT_STRICT=1` makes them assert the Python behaviour and fail until fixed.

### 7.5 The zone book that execution reads (F-7)

`algo-bot/app/autotrade/go_zone_book.py` says: "Go owns technical structure. It computes the barrier set - live zones only…". The book is built by
`redis.BuildZoneBook(snapshot.Zones)` (`main.go:181`): the **V2 zone state** of M5/M15/H1, supply/demand kinds only, `Score = z.Strength`, `Live = fresh|touched|partially_mitigated`, every barrier `TierZone`
(`barrier.go:45`: "the zone book carries no multi-timeframe scoring reason"). The frozen Python book pooled each timeframe's **merged, HTF-boosted `zones`**, excluded mitigated zones (`structural_barriers.py:133`), tiered by score
(level / zone / major) and applied a confluence boost before cross-side reconciliation.

**Measured** (`barrier_compare.py`, 1,451 hourly checkpoints, 11 captures, [python-go-barrier-diff.json](python-go-barrier-diff.json)): the frozen book (its own `build_structural_barrier_book`, run on the `a1c77584` analysis) holds 6,050 barriers (2,561 zone tier, 3,489 major); Go's holds 8,792, **all** `zone` tier (the live Redis book read on 16 Oct agrees: XAU 7, EURUSD 13 barriers, all `zone`). 4,029 of the frozen barriers have a Go barrier overlapping at least half of the narrower band; 2,021 (33%) have none, and 5,670 of Go's (64.5%) have no frozen counterpart.

What execution consumes is the room in front of the entry: the distance to the nearest opposing barrier, compared where both books have one (2,326 direction-checkpoints), is within 1 pip in 171 (7.4%); Go's room is smaller in 1,657 (71.2%) and larger in 498 (21.4%), median difference -19.4 pips (p10 -202, p90 +13). Python has no opposing barrier where Go has one in 424 cases, the reverse in 76. The hard block of `structural_target_room` (the planned entry inside an opposing barrier) would fire on 153 of 2,902 BUY/SELL evaluations with the frozen book (5.3%) and on 1,091 (37.6%) with Go's; 1,032 blocks are Go-only, 94 Python-only. Against the narrower book the frozen *worker* actually checked a Python-origin plan with at TradePlan time (`_structural_barrier_zone_book`: supply/demand zones of each timeframe, mitigated dropped, no HTF boost; 4,556 barriers) the picture is the same or stronger: room within 1 pip in 295 of 2,198 (13.4%), Go smaller in 1,681 (76.5%), larger in 222, median -13.8 pips; the hard-block proxy fires on 37 of 2,902 (1.3%) against Go's 1,091 (37.6%). Against `1c9f323`'s scanner book the result equals the `a1c77584` one (145 vs 1,091 blocks; median -19.4 pips). The checkpoint close stands in for the planned entry (which in production is a zone band), so read this as the *relative* size of the effect, not as the production blocking rate.

**Production corroboration.** The gate is the largest technical filter in production: from the Algo Bot logs of 2-15 Oct, 3,415 distinct (setup, reason) rejections belong to the opposing-barrier family (`opposing_entry_overlap` 2,217, `opposing_barrier_room_below_cost` 550, `opposing_entry_contained` 487, `opposing_barrier_no_target` 161), against 424 trades. The gate is documented as decision-relevant (`structural_target_room.py:67-95`: loosening it once changed Key Level from 68 trades/+628 pips to 8 trades/-165 pips). `barrier.go` calls itself "a faithful port of the normalization"; the normalization is, but its inputs are not, so the book is a different object. Which book is *right* cannot be decided by parity (the V2 zones may be better evidence than the frozen merged zones); that is a ground-truth question (F-7). Mitigation is also treated differently: the frozen book dropped mitigated zones (`structural_barriers.py:133`), Go keeps every `fresh|touched|partially_mitigated` V2 zone as a live barrier.

## 8. Confluence

* **Formula.** `confluence/score.go` against the frozen `_confluence_from_zone`, `_confluence_v2_score`, `_stars_from_v2_ratio` over 62,208 factor combinations (configuration `v1`, 0.585 / 0.390 / 4.0 / 2.0 / 2.5) for **both** references: **0 mismatches** in stars, raw score and V2 raw. The grid is `oracle_confluence.py` + `confluence_parity_test.go`.
* **Truthfulness of the inputs.**
  * For key_level, fade_scalp, range_edge, confluence_zone, session_level, break_retest, ifvg, liquidity_sweep, crt and the technique helper the stars come from the strategy's own detector confluence (`DetectorConfluence`, `worker.go:237`); the stars equal the frozen detector's on every golden decision (3,310 key-level + 2,958 technique + 94 fade + 91 flip decisions).
  * Strategies **without** `DetectorConfluence` (impulse_pullback; resting zone candidates) get the engine's value (`confluence_facts.go:25`): HTF alignment from Go-native biases, zone touches from the V2 zone, fib touch, CHoCH recency, and
    **keyword inference** over evidence codes (`wick|rejection`, `displacement|engulf`, `reclaim|structure|break`). Adversarial reading: the inference is substring-based and negation-blind (`m5_no_break_of_structure` or `m5_no_rejection` would set a factor; `break` also matches `breakout`). With the evidence codes impulse_pullback actually emits (`m5_qualified_impulse`, `m1_bounded_pullback`, `m1_continuation_trigger`), none matches a keyword, so the inference is inert for it today. Resting candidates are rejected at the adapter. No measured trade impact; the construct is unsafe for any future non-zone strategy.
  * `SessionContext` is `true` unconditionally in session_level and ifvg (constant factor: +1 raw point on every candidate, and the session part of the 3-star ladder is therefore not evidence).
  * `confluence_scoring_version` is `v1` for all instruments, so the MAD bonus (V2 only) is inert.

## 9. Quality (`python-go-quality-diff.json`)

Python never had a per-strategy quality; its comparable quantity is the confluence stars (exact, §8). `Quality.Overall` is Go-native. Inventory, with the distribution over 15,204 replay candidates:

| Strategy | `Overall` | Distinct values (replay) | Note |
|---|---|---:|---|
| key_level, flip_zone, supply/demand/OB/FVG (confirmed), ifvg, crt, session_level, trendline, confluence_zone (confirmed) | `stars/3` | 1-2 (0.667, 1.0) | stars floored at 2; correlation with stars 1.0 by construction |
| supply/demand/order_block/fvg (resting) | (strength + relevance + touch)/3 | 6-9 | strength and relevance are the hard-coded 1 / Immediate; depends on touches only |
| confluence_zone (resting) | `MinGroups/4` | 1 (0.5) | constant on 432 of 432 |
| break_retest | constant 0.75 | 1 | |
| range_edge | .25 + .06·min(score,10) + .15·gradeA | 35 | corr with stars 0.08 |
| fade_scalp | .55 + .15·gradeA + .05·min(touches,3) | 4 | `reaction`, `location` components constant 1 |
| liquidity_sweep | reclaim-bar body / ATR | 20 | `reclaim` component constant 1 |
| impulse_pullback | displacement / (2·threshold) | 1 | |

Arbitration reads none of these (`quality_ranking: false`, `config/analysis.yml:808`; `_neutral_rank`). Two residual reads exist and neither decides: `multi_match.py:65` (status representative) and `entry_overlap` (stored). No ranking was re-enabled and no score was invented.

## 10. Strategies (`python-go-strategy-diff.json`)

Sixteen current strategies (discovered from `config/analysis.yml`, `internal/strategy/*`); positive and negative evidence is stated per row.

| Strategy | Verdict | Positive bars | Negative bars | Gap |
|---|---|---|---|---|
| key_level v3 | **EXACT_PARITY** (profit-week contract) | 3,310 (EURUSD 598, GBPUSD 660, USDJPY 691, XAU 638, XAU_LATER 723) | 6 × 1,351 bars, 0 extra Go decisions | GBPJPY has 0 oracle decisions (vacuous); F-1 |
| flip_zone v3 | **EXACT_PARITY** | 91 | asserted bar by bar | - |
| fade_scalp v2 | **EXACT_PARITY** (3 symbols) | 94 (GBPUSD 33, USDJPY 43, XAU 18) | 3 × 1,351 | EURUSD, GBPJPY not covered |
| supply / demand v3 | PARITY_WITH_KNOWN_DIFFERENCES | 1,164 via the helper path | | live strategies add the >2-touch cut and independence; resting path is Go-native |
| order_block v2, fvg v2 | PARITY_WITH_KNOWN_DIFFERENCES | 26, 161 | | 26 positives is thin |
| ifvg v3, crt v3, confluence_zone v3 | INTENTIONAL_RECONSTRUCTION | helper path proven: 641, 94, 871 | | the parity test does not run the live v3 strategies |
| session_level v4 | INTENTIONAL_RECONSTRUCTION | 316 oracle decisions; **368 measured divergences** | | parity not asserted by design |
| trendline v4 | INTENTIONAL_RECONSTRUCTION | 8; 1 divergence | | 0 production trades |
| range_edge v3, break_retest v3 | INTENTIONAL_RECONSTRUCTION | 319, 28 (no longer asserted) | | frozen v2 baselines kept in `rereplay`, `brreplay` |
| liquidity_sweep v3 | INTENTIONAL_RECONSTRUCTION | no Python predecessor | | unit tests only |
| impulse_pullback v3 | **UNVERIFIED** | historical: 18/18 synthetic, 0/0 real (1,939 cycles) | | F-4: test deleted |
| box_breakout, snap_back, momentum_ride, scalp_breakout_retest, range_sweep | RETIRED | | | purged 14-15 Oct |

**Challenge of previous parity claims** (README `LEGACY_PARITY_PROVEN`): confirmed for key_level, flip_zone, fade_scalp (3 symbols) and, through the helper path only, supply, demand, order_block, fvg. **Not supported as written**: ifvg, crt, session_level, range_edge, break_retest, confluence_zone (the label describes a contract that no longer exists; the table also lists v2 where config says v3/v4), impulse_pullback ("on synthetic data only", but the test is gone), and the claim in `docs/strategies/README.md:55` that `test/scalpparity` proves it.

**The technique parity tests exercise the `strategyutil` helper, not the live strategies.** `techniqueparity` calls `TechniqueExcludingConfluenceCoverage` / `ConfirmedTechnique`; ifvg, crt and confluence_zone v3 are separate contracts. Passing `go test ./test/techniqueparity` says nothing about them.

### Python defects the port corrected (PYTHON_BUG_CORRECTED)

1. **`_zone_grabs_for` without `pip_size`** (technique and confluence_zone publishers): the sweep that confirms a zone is matched with a hard-coded 0.1-pip tolerance on every instrument. Exact on XAU, ten times too wide on 0.01-pip pairs: a sweep 7 pips below a USDJPY FVG+iFVG band "confirmed" it (USDJPY bar 1790888700). Go uses the instrument's pip size; the one resulting difference is listed in `techniqueparity`.
2. **CRT H1 index applied to M5/M15 bars.** The frozen collector returns an H1 position that callers index into M5 bars. Independently reproduced by this audit: in the frozen oracle, **112 of 114** CRT instances have `origin_ts` (the H1 candle) different from the time of the bar at `origin_index` (31 of 31 on M5, 81 of 83 on M15). CRT v3 uses timestamps.

## 11. Runtime pipeline

See [python-go-runtime-contract.md](python-go-runtime-contract.md). Highlights: the adapter's bias is a new source (F-5); the traded stop is the instrument floor and the traded targets are fixed R multiples of it (about 3× the technical stop on FX); the zone book is a different population (F-7).

## 12. Technical versus execution geometry

For FX, 71-81% of executed stops sit exactly on the floor, about three times the technical distance; targets are 1R/2R of that stop, breakeven at 1R. For XAU the band is widened to 30-50 pips and the ladder is 1R/2R/3R/4R 40/20/20/20. The technical read the strategies describe (invalidation at the structure, target at opposing liquidity) is used only to size the *room* check. This is not a porting error: the execution layer owns risk (`widen_to_minimum=True`, "Go owns the technical invalidation, while Algo Bot owns the execution-risk floor"). It does mean that no parity statement about a Go candidate says anything about the traded geometry, and that the floors changed after the profitable week (§3).

## 13. Replay quantification and attribution (stages A / B / C / D)

| Stage | Definition | What exists | Result |
|---|---|---|---|
| A | frozen Python decisions | goldens (§10) | the reference |
| B | the first Go that claimed parity (28 Sep - 6 Oct, loose ports) | no harness existed then; the Key Level parity harness arrives with `3f18eff7`/`2ed77d10` (6 Oct) | **not replayed**: the goldens cannot be run against pre-harness commits without porting the harness; the first proven state is 6 Oct |
| C | current Go | this audit | exact on the strategies and layers above |
| D | production | `auto_trade_results`, `algo_auto`, 29 Sep-15 Oct | below |

Production, `trade_stream = 'algo_auto'`, R per trade as in PR #791 (net of what the executor recorded, not of commission):

| Period | What was live | Trades | Mean R | 95% half-width | Win rate |
|---|---|---:|---:|---:|---:|
| 29 Sep - 3 Oct | loose ports, no parity proof | 72 | -0.044 | ±0.265 | 39% |
| 4 - 7 Oct | parity restored (legacy, technique, key level) | 127 | +0.171 | ±0.215 | 51% |
| 8 - 15 Oct | restored + rebuilt v3/v4 strategies | 251 | -0.090 | ±0.152 | 43% |
| all | | 450 | -0.009 | ±0.113 | 44% |

Periods by trade close time; Range Sweep excluded, the other purged scalps included (they were live). Every interval contains zero and every adjacent pair overlaps, so **the data cannot distinguish the three periods**: neither "parity improved results" nor "the rebuilds degraded them" is supported.
By strategy (all periods): Key Level (exact parity) n=120, -0.04R ±0.24; Range Edge Scalp n=106, -0.13R ±0.21; Supply Demand n=52, -0.15R ±0.32; Confluence Zone n=31, +0.27R ±0.45; Fade Scalp n=21, +0.49R ±0.52; Impulse Pullback n=12, -0.38R ±0.47.
Key Level on XAU, the profit-week strategy and the one proven exact: n=77, -0.16R ±0.33.

**Attribution.** The earliest-diverging-layer attribution of every analysis difference found in this audit: levels (F-1), zone scoring (F-2), reason formatting (F-3), and the population/design differences of §7.3 and §7.5. None of them diverges *before* the layer it is reported in; the swing, structure and break layers, from which everything else is built, are exact, so no downstream difference is inherited from an earlier layer.

**Does the evidence show the migration degraded performance?** No. (1) The baseline is 9 trades (6W/3L; Wilson 95% interval for the win rate 35%-88%): any production win rate from 35% to 88% is compatible with it. (2) Production begins after the cut-over: there is no Python-era production sample to compare. (3) The strategy proven bar-for-bar identical (Key Level) shows the same near-zero expectancy as the rest. (4) The signals have no measurable edge over random entries under the production geometry (PR #791: -3.5 pp, z = -3.6; technical geometry -0.028R, CI [-0.101, +0.045]). A faithful port of a signal without a demonstrated edge cannot be shown to have lost one. (5) The execution layer changed independently (§3, §12). What *can* be said is negative: nothing measured in the analysis port is large enough, in the executable path, to explain a change in outcomes.

## 14. Adversarial invariants

Run in this audit (all on real captures, both implementations unless stated):

| Invariant | Result |
|---|---|
| No swing is reported before its fractal is confirmed | 0 of 194,902 violate it (Python and Go) |
| A swing reported at *t* is reported at *t+1h* if both windows contain it | 490 of 184,238 are consolidated away; identical in Python and Go |
| BOS/CHoCH labels are stable across window shifts | not true: 19.3% change; identical in Python and Go (inherited) |
| Go zone geometry ⊂ frozen zone geometry (population 1) | exact, 0 Go-only |
| A matched zone's mitigation is represented | **violated** in population 3: 1,342 mitigated zones are published as Touched (F-6) |
| Confluence stars are monotone in every factor | holds on the 62,208-combination grid; implied by exact equality with the frozen scorer |
| Evidence keywords cannot invent a factor | **not guaranteed** (negation-blind); inert for the current codes (§8) |
| Quality is not used to rank | holds (`quality_ranking: false`; two residual reads, neither decides) |
| Strategy independence (no strategy suppresses another) | `techniqueparity` independence test: 1,642 previously suppressed publications now emitted; not re-run here |
| Mirror symmetry, no future dependence | covered by `czreplay`/`crtreplay` (not repeated) |

## 15. Findings register

| ID | Class | Severity | Finding | Evidence | Smallest fix |
|---|---|---|---|---|---|
| F-1 | CONFIRMED_PORTING_BUG | low (latent) | `SwingLevels` ignore the instrument's `minimum_key_touches` (GBPJPY) | §7.4; Python `engine.py:341-347`; Go `legacyread.go:189`; experiment 617/617, 733/733 identical; `TestF1…` | `frameTouches` at `legacyread.go:189` |
| F-2 | CONFIRMED_PORTING_BUG | low (latent) | `ContractZones` never get the HTF-zone score | §7.4; `legacyread.go:240-249`; `TestF2…` | re-score `ContractZones` like `Zones` |
| F-3 | CONFIRMED_PORTING_BUG | cosmetic | score reasons print unrounded floats (`round 1.1340000000000001`) | 54,750 reason lists; `numberText` at `zonescore.go:89`; `TestF3…` | round to the instrument digits in `numberText` |
| F-4 | CONFIRMED_TECHNICAL_BUG (test coverage) | medium | impulse_pullback's Python parity tests were deleted with Range Sweep (`9d705a02`); docs still cite them | §10; reproduced at `9d705a02^`: 18/18 synthetic, 0/0 real; code unchanged since | restore `TestImpulsePullback…` and its oracles (`git show 9d705a02^:analysis-engine/test/scalpparity/range_sweep_test.go`) |
| F-5 | INTENTIONAL_RECONSTRUCTION, UNVERIFIED | medium | adapter `htf_bias` and `with/counter_bias` come from Go-native V2 bias, not the frozen `_htf_bias` | 887/1,451 (61.1%) agreement with Python's HTF bias; V2 primary bias 63.1% / 54.8% | decide the intended source, then test it against ground truth; do not tune to Python |
| F-6 | CONFIRMED_TECHNICAL_BUG | low (not executable) | production M5 `Context.Zones`: 400-bar window, `Strength = 1`, `Relevance = Immediate`, mitigation collapsed to `Touched` | §7.3; `technique_zones.go:24,86-92` | publish mitigation/invalidated state and Python's score; make the window configuration, default 150 |
| F-7 | UNINTENDED_DIVERGENCE (measured); correctness UNVERIFIED | **high** (the one finding that reaches execution) | the execution-time barrier book is built from the V2 zones (strength as score, one tier, no confluence boost, mitigated-but-touched zones kept live), not from the frozen merged HTF-boosted non-mitigated zones | §7.5; 71% smaller room, 1.3-5.3% vs 37.6% hard-block proxy; 3,415 production rejections in 14 days | do not tune: first decide, against ground truth, which zone population should gate room; the faithful alternative is to feed `barrier.Build` from `LegacyFrame.Zones` with the frozen tiers and confluence boost |
| F-8 | CONFIRMED_TECHNICAL_BUG (doc) | low | documentation states parity/versions/containment that the tree no longer has | §16 | update the README matrix |
| P-1 | PYTHON_BUG_CORRECTED | - | `_zone_grabs_for` hard-coded 0.1-pip tolerance | §10 | none (fixed in Go) |
| P-2 | PYTHON_BUG_CORRECTED | - | CRT H1 index used on M5 | §10; 112/114 | none (CRT v3) |

## 16. Documentation reconciliation

* `README.md` strategy table: lists confluence_zone, ifvg, crt, range_edge, break_retest at v2 and session_level at v3 (config: v3, v3, v3, v3, v3, v4); labels all of them `LEGACY_PARITY_PROVEN` although each was rebuilt away from the frozen behaviour (§10); lists "XAU observe-only" for ifvg, session_level and liquidity_sweep and impulse_pullback "observe-only on every instrument" although `observe_only_strategies: []` for all five instruments (`instruments.yml:312-476`) since 14 Oct (`52c5453f`).
* `docs/strategies/README.md:22` "17 remain" (16 since the Box Breakout purge); `:55` cites `test/scalpparity` as proof for impulse_pullback; the test no longer exists.
* `analysis-engine/internal/engine/stop_envelope.go:13` says "one of the 20 catalog IDs"; the map has 16.
* Dated audit documents (`arbitration-quality-audit.md` "21 strategies", `independence-audit.md` "20 strategies", `ae-quality-calibration.md` "20 strategies") are historical records and correct for their date; they should not be read as the current catalogue.
* `docs/architecture.md` (16 strategies, PR #790) is current.

## 17. Reproduce

```
# frozen Python oracles (isolated worktrees)
git worktree add /tmp/py-a1c a1c77584 ; git worktree add /tmp/py-1c9 1c9f32303b7e9f15fc8e3767b5045ac1e8a37adc
python analysis-engine/test/migrationaudit/oracle_dump.py --oracle /tmp/py-1c9 --capture <capture.json> --out <x>.py-1c9f323.jsonl
# Go side, same checkpoints
AUDIT_DUMP_DIR=<dir> AUDIT_CAPTURES=<a.json,b.json> go test ./test/migrationaudit -run TestDumpFrames
python analysis-engine/test/migrationaudit/oracle_compare.py --oracle … --go … --out …      # layers (add --go-zones contract_zones --go-levels swing_levels for a1c)
python …/oracle_confluence.py … ; AUDIT_CONFLUENCE_ORACLE=… go test ./test/migrationaudit -run Confluence
python …/zone_diff.py ; …/barrier_compare.py ; …/causality_check.py ; …/oracle_trace.py ; …/build_parity_matrix.py ; …/build_strategy_quality.py
AUDIT_FINDINGS=1 go test ./test/migrationaudit -run 'TestF' -v   # F-1..F-3 (AUDIT_STRICT=1 asserts the Python behaviour)
```

Residual UNVERIFIED (not examined): MAD and candle evidence against Python, the fib ladder itself, the executor, M1 scalp lane beyond the deleted impulse test, zone behaviour at M15/H1 as strategy inputs (no oracle), Go's behaviour under live (not replayed) latency.

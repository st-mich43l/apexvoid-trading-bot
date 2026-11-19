# Key Level


## Strategy ID / version

`key_level`, `v11`. Implemented in
`analysis-engine/internal/strategy/keylevel`. Algorithm versions:
`structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`.

**Entry freshness (KL11, v11).** The full reaction band (the band as read, before any stop-budget cut, rounded outward to the instrument's price digits so it always contains the published entry) is now the opportunity's published `structure` (M5, formed at the touch bar; no swing, no zone id), and the engine's structure-close rule applies to Key Level: the first M5 **close** beyond the band's far edge (a BUY's low, a SELL's high) by more than `structure_close_buffer_atr` M5 ATRs (shipped 0.0: any close beyond it) withdraws the opportunity (`STRUCTURE_CLOSED_THROUGH`; counter `go_opportunity_structure_closed_through{strategy="key_level"}`); a wick that closes back inside never does. Algo Bot receives the usual terminal event and its tombstone cancels unfilled legs; filled legs keep their stop and TP management. Until v10 a Key Level opportunity ended only on an M5 close beyond its **stop** or at expiry. This rule concerns whether a published *entry* is still valid, not what the level's *role* is: the role (v9, `breakout_accept_bars: 2`) is unchanged. A later reaction at the same level has a new `SetupKey` (touch and confirmation times) and is a new opportunity; a withdrawn one is never reopened. Key Level is deliberately **not** a ZL-1 `ZoneReporter`: its `StructuralID` is the level's price, which drifts bar to bar as clusters re-average, so "zone not reported" would expire live opportunities spuriously. `expiry_hours` stays 24.0: the wait from creation to admission did not separate November outcomes (see "KL11 evidence").

**Reaction confirmation (T1.3, v10).** Key Level owns its reaction confirmation (`keylevel/confirm.go`); the shared evaluation (`internal/reaction`) is unchanged and serves the other strategies. The five patterns, their priority, the touch and confirmation windows (`reaction_lookback_bars`), the latest-valid-confirmation rule and every candle threshold are the same as before (a control run with the three corrections switched off reproduces v9 exactly). Three requirements make the confirmation belong to this level and this reaction: (1) the level exists before it is reacted to: the touch and the confirmation are bars at or after the bar on which the level became known, the confirmation bar of the swing that brought its band to `minimum_touches` swings (a bar that is itself what completes the level cannot also be the reaction off it); (2) `rejection_choch` needs a CHoCH in the reaction direction on a bar from the touch bar to the confirmation bar, so one that broke before the touch (the trend the price arrived in) or after the confirmation bar (evidence the confirmation did not have) no longer upgrades a wick rejection, and the candidate is the pattern the bar shows (`strong_reclaim` or `wick_rejection`); (3) a rejection happens at the level: a wick rejection needs the confirmation bar to reach the band, an engulfing needs the engulfing bar or the bar it engulfs to reach it. `sweep_reclaim` (a grab of this level's own pool on the confirmation bar) and `strong_reclaim` are unchanged. `strong_reclaim` is a candle that pierces the band's far edge (the lower edge for a BUY) and closes back at or inside that edge in the reaction direction: a reclaim of the far edge, not of the whole band (the close may lie inside the band or beyond its near edge); the older comment, "back through the near edge", described something the code never did, and the behaviour is kept. A confirmation can still be published one or two bars after its confirmation bar (the window is three bars and entry validity is checked at publication): that is the lookback, not a defect, and is unchanged. Evidence and timestamps are unchanged: `ConfirmedAt` and the confirmation bar time are the bar of the pattern, and the pattern named is the event that happened.

**Role from structure (T1.2, v9).** Until v8 every level was ambiguous: `keylevel.Role` read the role from the level's kind label by substring ("support", "resist", "high", "low"), and the level primitive only ever produces `reaction` and `round`. Every published opportunity carried `m5_key_level_role_ambiguous` (1,474 of 1,474 since 1 Nov), the direction fell back to which side of the level price stood on (302 of 1,142 replayed candidates were a BUY at a level made only of swing highs or a SELL at a level made only of swing lows), and `require_explicit_role` could never be satisfied (GBPJPY published nothing). v9 reads the role from the market structure that formed the level (`keylevel.StructuralRole`; section "Role contract"). The level's kind, band, touches, the reaction confirmation, invalidation, entry band, target (T1.1), stop budget, `StructuralID` and the quality model are unchanged. This is a deliberate difference from the frozen Python detector (which had no explicit roles either): Key Level now trades the direction its structure implies.

**Target basis (T1.1, v8).** The target is read from the opposing liquidity ahead of the entry, and only from liquidity that is still there. An opposing pool is a pool on the side the trade runs toward that (a) lies strictly ahead of the proximal edge of the entry band (a pool within a tick or 5% of an ATR of the edge shares its boundary and is the same wall), (b) has not been swept: no closed frame bar after the swing that last defined it has traded beyond its outer edge (a high above `level + band` for a buy-side pool, a low below `level - band` for a sell-side one; the sweep test liquidity grabs use), and (c) is not the candidate's own wall (inside the full reaction band, before any deep-entry cut, or the level's own cluster). The nearest such pool is the target (`nearest_opposing_liquidity`) at whatever distance: a close pool leaves little room and the candidate now says so. Only when no such pool stands in the way is the target `fallback_target_r` times the **effective** risk beyond the entry (`fixed_reward_risk`, meaning "nothing ahead"). Effective risk is the structural distance from the proximal edge to the invalidation, or the instrument stop floor when that is tighter (`StopBudget.EffectiveRisk`): execution widens a tighter stop to the floor, so an FX candidate with a 5-pip structural stop carries 15 pips of risk. v7 skipped pools nearer than `minimum_target_distance_atr` and treated pools already swept as live, so a close obstacle could be passed over for a farther pool or for the fixed fallback, and liquidity that had been taken could be the objective. `minimum_target_distance_atr` is removed (no consumer). The invalidation, the entry band, the stop budget, the confirmation, `StructuralID` and the quality dimensions are unchanged; the quality model gains one measurement, `room_r_effective` (the target's distance in effective risks), outside `Overall`. Nothing is refused for room. Orders are unchanged: for the fixed-ratio instrument packs the Go target is provenance and the ladder comes from the pack (`docs/execution.md`); the execution policy reads it only for an advisory room note that denies nothing.

**Deep entry (PR-E1, v7).** When the structural stop from the proximal edge of the entry band is wider than the instrument cap, the strategy no longer refuses at once: `strategyutil.StopBudget.Fit` keeps the invalidation where structure puts it and cuts the entry band to the part from which the stop is within the cap (BUY: prices <= invalidation + cap; SELL: prices >= invalidation - cap, the cut edge rounded to the tick toward the inside). The candidate's entry band is the cut band (evidence `stop_budget_deep_entry`, quality measure `deep_entry_pips` = how far the proximal edge moved); room and target are recomputed from the new proximal edge. A band whose far edge is still beyond the cap, or a cut band narrower than `deep_entry_minimum_width_pips` (instrument, XAU 3.0, FX 0.5), is refused and counted as before. The identity (`StructuralID`, `SetupKey`) does not contain the entry band, so it is unchanged. The band cut is the reaction band; Key Level publishes no `Structure`, so the card shows the cut band only.

## Purpose / thesis

Key Level reacts at a clustered key level, in the direction its role implies.
`v3` reproduces the Python detector behind the profitable XAU week of 14-18 Sep
2026 (+567 pips, 6W/3L), `key_level_reaction` at commit `a1c77584`, on the same
detector-contract frame as Break & Retest, so the same bars give the same level, role, direction, reaction
and entry.

## Decision

1. Levels are walked nearest first; a level with fewer than
   `minimum_touches` touches is skipped.
2. The reaction band is the wider of the level's own band and the proximal band
   (`proximal_band_atr · ATR`).
3. The **role** (`internal/keylevel.StructuralRole`, v9: from the confirmed swings in the level's band and the closed bars since, see "Role contract") decides the
   direction tried: support → BUY, resistance → SELL; an ambiguous level is bought when price is above it, sold when below and, when price is inside the band,
   tried both ways. A role broken by acceptance (`breakout_accept_bars` consecutive closes beyond the band on the side it defends) is `broken_*` and skipped, and so
   is an ambiguous level price has accepted beyond: Break & Retest owns it. `require_explicit_role` (an instrument override) skips ambiguous levels.
4. A live opposing zone overlapping the band of an ambiguous level contradicts
   the naive reading: both sides are tried over the widened window and the zone's
   edge is the level the opposite side reacts off.
5. A direction needs a confirmed structural reaction off the band
   (`confirmReaction`, v10, see "Reaction confirmation": sweep reclaim, CHoCH rejection, strong
   reclaim, wick rejection, engulfing). A level both sides confirm is a
   contradiction and yields nothing.
6. The reaction passes the shared qualification (`_finish`): the level on the
   right side of price, the entry no further than `maximum_entry_atr`, and
   confluence at or above `confluence_floor`.
7. Of every level that qualifies **one** candidate is kept: the highest
   confluence stars, the nearest level winning ties.

Key Level is never vetoed by the higher-timeframe bias; a counter-bias reaction
trades with fewer stars (it loses the 4-point `htf_aligned` factor).

## Detector contract

`detector_contract: profit_week` reads the structure as it stood in the
profitable week: levels counted from fractal swings only, and order blocks
qualified by a BOS break only. `current` selects the structure the other
detectors read (wick touches counted, CHoCH order blocks), which changes Key
Level's decisions on about 18% of the bars where either fires; the permanent
parity test fails if `current` is selected.

## Gates

`minimum_strength`, `proximity_atr` and an opposing liquidity pool as a
prerequisite are not gates. The reported target is the nearest unswept opposing
liquidity pool ahead of the entry (own wall excluded), else `fallback_target_r`
times the effective risk beyond the entry (see "Target basis"); whether the
available room suffices is the execution policy's decision.

## Candidate

Entry is the reaction band; invalidation is beyond its outer edge by
`invalidation_buffer_atr · ATR`. `StructuralID` is the level
(`keylevel:<kind>:<price>`), not the confirmation, so a re-confirmation of the
same level is the same thesis.

**Reaction-anchored invalidation (v4).** A candidate with a confirmed reaction publishes the far edge of the structure or, if the bars from the touch to the confirmation traded beyond it, that extreme, in both cases one `invalidation_buffer_atr*ATR` further out (Buy: `min(structure low, lowest low touch..confirm) - buffer`; Sell: `max(structure high, highest high touch..confirm) + buffer`). A sweep or strong reclaim is confirmed by a wick that pierces the band, and a stop inside that wick has already been traded. The buffer is unchanged and has no pip floor; when the reaction's extreme set the price the candidate carries the evidence code `invalidation_beyond_reaction_extreme`. A resting candidate (no confirmation) keeps the structural price, and the fixed-reward:risk fallback target, the quality geometry and the published invalidation follow the new price. Decisions and entry are unchanged (`LEGACY_PARITY_PROVEN`); the invalidation differs from the frozen publisher by design. `DetectorConfluence` carries the stars and the
machine-readable factors (`htf_aligned`, `touches`, `wick_rejection`,
`displacement_grade`, `structural_agreement`, `session_context`, `fib_touch`,
`choch`).

**Instrument stop budget (v5).** A candidate whose structural stop (distance from the proximal entry edge to the invalidation above) exceeds the instrument stop cap (XAU 70, EURUSD 20, GBPUSD 25, GBPJPY 35, USDJPY 28 pips) is **not published**: refused, never tightened, and neither the entry nor the invalidation is moved to fit. One inside [floor, cap] carries `stop_budget_structural`; one below the floor carries `stop_budget_floor` and execution widens it.

**Forward labels (informational only).** Every candidate also publishes six measurements about its level, so realised outcomes can later be cut by what kind of level it was. They are
never a score, a gate or a star, and they do not change `Overall`: `level_kind` (0 reaction, 1 round), `level_round_class` (how round the level's *price* is: 0 none, 1 a multiple of 5, 2 of 10,
3 of 50, 4 of 100), `level_origin` (+1 formed by swing lows, -1 by highs, 0 mixed, from the swings within the band known at the confirmation bar), `level_age_bars` (bars at the confirmation since
the newest member swing; the bars read when no swing is within the band), `level_band_atr` (the level's own band over the ATR) and `level_coverage` (the share of the window's [low, high] inside any level band
at the evaluation: the selectivity of the definition). They travel with the candidate: Go `Quality.Components` -> the Kafka `quality.components` payload -> `match.quality_components` in algo-bot ->
`auto_trade_plan_snapshots.quality_components` (one immutable JSONB row per published trade plan, indexed by `opportunity_id`, which realised trades join on). A candidate that never becomes a published
plan has no snapshot row. 

## Quality model v2 (Q-KL1)

`key_level.quality.v2` (published as `quality_model_version` 2). Seven dimensions in [0, 1], `Overall` is their unweighted mean, informational: no gate, ranking or star reads it (`quality_ranking: false`). Entry, invalidation, target, ladder, evidence and every other published field are unchanged, and so is the strategy version (v10): only the three dimensions below changed, the four others (`level_significance`, `htf_level`, `level_integrity`, `rejection`) are byte-identical to v1. The shared `quality` helpers other strategies use are untouched.

| Dimension | v1 | v2 | Demonstrated defect |
|---|---|---|---|
| `room` | `Ramp(reward / structural risk; 1, 3)` | `Ramp(reward / effective risk; 1, 3)`, effective risk = `max(structural stop, instrument floor)` (`StopBudget.EffectiveRisk`); 0 for a fixed-ratio target as before | Execution widens a stop tighter than the instrument floor, so the trade risks the floor, not the structure. 84-98 percent of FX candidates are widened (XAU 1 percent). A GBPUSD BUY scored `room` 1.00 for 3.08R to structure while the executed trade has 1.03R. |
| `entry_location` | `Fall(abs(price - proximal edge) / ATR; 0.25, 1.5)` | `Fall(max(0, ran away from the proximal edge in the trade's favour) / ATR; 0.25, 1.5)` | The absolute value penalised price that had come back *into* the band, a better fill than the edge (550 of 907 replayed candidates; in all of them price was inside the published band, median 0.31 ATR, at most 0.47 of the stop distance). |
| `structure` | change of character anywhere in the last `reaction_lookback_bars` bars before the decision bar | change of character on a bar from the touch bar to the confirmation bar (the attribution `rejection_choch` uses) | The v1 window credited a reversal that broke before the touch (preceded the reaction) or after the confirmation (followed it): 41 of 948 candidates. |

Not changed, on the evidence: an unmeasured integrity (fewer than 10 bars of history) and an unavailable higher-timeframe frame score 0, as the quality model defines for missing evidence; neither occurred in the replay in a way that moved a ranking (integrity was never unmeasured, 18 of 907 candidates lacked an HTF frame). `level_significance` is 1.0 for 75 percent of candidates (sd 0.08): uninformative but not wrong, and not retuned. A clear-path (`fixed_reward_risk`) target still scores 0 for `room` (3 of 907 candidates): the ratio is chosen by the strategy and the market's room is unmeasured; an evidence-backed value needs outcome labels (`docs/research-data.md`).

The measurements `room_reward_risk` (structural) and `room_r_effective` are both still published. The unweighted mean has no tuned weights; no weight was introduced.

## Evidence

- `test/keylevelparity`: replays six committed real captures — XAU of the
  profitable week (14–21 Sep), XAU of the incident window (28 Sep – 6 Oct),
  EURUSD, GBPUSD, GBPJPY and USDJPY, 6 × 1351 closed M5 bars — and requires the
  same decision as the Python detector on every bar: 3310 decisions, 0
  mismatches, silence where Python was silent, and the same level, role, kind,
  touches, direction, entry band, confluence stars and reaction bars. The
  golden is produced by running the Python (`generate_oracle_golden.py`), never
  by Go. (GBPJPY has no decisions: its instrument rule requires an explicit
  support/resistance role, which the level primitive never produced, so Key Level
  was silent there in Python. Since v9 the Go role is structural and GBPJPY publishes; the parity contract no longer
  applies to the role, by design.)
- `internal/strategy/keylevel` unit tests: support BUY, resistance SELL,
  broken support/resistance skipped, ambiguous resolution by price, opposing-zone
  contradiction and widened window, both-sides discard, best-of-several (and the
  nearest on a tie), counter-bias, entry too far, confluence floor, minimum
  touches, explicit-role requirement, target selection, contract selection.

## Role contract (T1.2, v9)

Input at the decision bar `n`: the level's price and band (`level.Band`, the cluster tolerance that formed it), the frame's swings **confirmed at `n`** (a fractal swing is known
only when its right-hand bars have closed, `ConfirmedIndex <= n`: a pivot still forming cannot give a level its role), and the closed bars up to `n`. A swing is a member
of the level when its price is within the level's band. `minimum_touches` is the fewest swings of one kind that make a role.

| Role | Evidence | Direction |
|---|---|---|
| `support` | at least `minimum_touches` confirmed swing lows in the band and **no** swing high: price reacted up from it, and only from it | BUY |
| `resistance` | at least `minimum_touches` confirmed swing highs and no swing low | SELL |
| `ambiguous` | anything else: highs and lows in one band (a level price has reacted to from both sides, including a level flipped earlier), fewer than `minimum_touches` swings of one kind, or a round number with no swing structure | the old fallback, only where `require_explicit_role` is off: the side price stands on, tried both ways inside the band, a contradiction (both sides confirmed) discarded |
| `broken_support` | a support with `breakout_accept_bars` consecutive closed-bar closes **below** the band | none (skipped; Break & Retest owns it) |
| `broken_resistance` | a resistance with that many closes **above** the band | none (skipped) |

Transitions: a role changes only by acceptance, on the side it defends. A wick beyond the band, or a sweep that closes back inside, accepts nothing and leaves the role. One close
beyond the band accepts nothing. Closes on the *other* side (a support with price closing above it) are price moving away from the level, not through it: the role stands.
A broken role is reported and never flipped into its opposite: a flip needs the retest confirmation that Break & Retest (and Trendline) own. A level whose swings change (a new
confirmed swing of the other kind arrives) is re-read on the next bar: support plus a confirmed swing high in its band becomes ambiguous, which is the honest reading of a level
price now reacts to from both sides. No confidence value exists; no threshold was added (`minimum_touches` and `breakout_accept_bars` are the existing parameters).

Invariants (checked on a scenario table and on replay): BUY and SELL are exact mirrors (swap lows and highs, reflect the closes about the band: the mirrored role comes back);
every published `support` is a BUY and every `resistance` a SELL; no role uses information confirmed after the decision bar (a replay on data truncated at a cutoff reproduces
every earlier decision exactly); `keylevel.Role` (the kind-label classifier) is unchanged and still serves Flip Zone and Impulse Pullback.

`m5_key_level_role_support`, `_resistance` or `_ambiguous` on a published candidate now describes the structure; `level_origin` (a quality measurement) is not the rule and is unchanged.

## Q2: the session context is measured

`session_context` was set on every Key Level decision, which made a reaction with no higher-timeframe agreement reach two stars on candle evidence alone (touches 3 + pattern 3 + a constant 2). It is now true only when a finalized
session, previous-day or previous-week level supporting the trade was swept and reclaimed in the reaction's window. `test/keylevelparity` keeps proving the
frozen detector with `frozen_session_context` set. On the 17-capture replay: 5,444 -> 4,174 opportunities (2/3 stars: 2,890/2,554 -> 3,122/1,052; 257 new ones where the better of two attempts changed). The 1,527 removed were not worse than the rest
(hypothetical +0.004R against -0.102R), so the change removes an unconditional constant, it is not a performance rule.

## Status and execution

- **Certification**: `LEGACY_PARITY_PROVEN` ([catalog](README.md#catalog)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument. XAU stop is held to the 50-60 pip envelope and scales in; FX takes one entry.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by execution eligibility, then freshness, then id trades
  (no quality score is part of the order) and the rest are suppressed ([execution](../execution.md#same-thesis-arbitration)).

## KL11 evidence

Measured on the five production captures (M5, XAU from 6 Nov, FX from 9 Nov, to 18 Nov 02:30 UTC), master against this change; descriptive, no P&L claim.

* **Published set.** 1 702 Key Level opportunities, identical in entry, invalidation, target, evidence and quality before and after; only `structure` is new (and the id, because the version is in it). All 1 751 events of the other strategies are identical except `config_fingerprint`. Every payload validates against `opportunity-v1`.
* **Withdrawn by the rule.** 1 078 of 1 702 (63%): XAU 104/149, EURUSD 93/137, GBPUSD 366/572, USDJPY 452/725, GBPJPY 63/119; median 6-13 M5 bars after creation (XAU 6, EURUSD 7, GBPUSD 8, USDJPY 7, GBPJPY 13), 49-74% within the first hour.
* **First fills prevented: none.** A BUY closes below its band only after trading through it, so the bar that closes through has already touched the entry. In the replay every opportunity that would have filled before still does (1 686 of 1 686). The rule removes what remains after a close-through (unfilled legs, a later retest), which only matters where the first pass was not filled in production.
* **Real November trades.** Of the 47 Key Level plans whose creation-to-admission interval the captures cover, the band had been closed through before admission for **2** (both FX, -0.51R net) and for **none of the 25 XAU** plans (-9.67R). The XAU Key Level loss is therefore not made of fills of an already-failed band. Wait time itself (admission minus creation) does not separate outcomes either (XAU, under 15 min: 32 trades -0.186R; 15 min or more: 14 trades -0.471R, difference -0.285R, 95% interval -0.87..+0.27; all Key Level, 60 min or more minus under 60: +0.159R, -0.31..+0.63), which is why `expiry_hours` is unchanged.
* **Reading.** KL11 keeps Key Level from offering a level that has already failed; the measurements say that is hygiene, not the cause of the XAU loss.

## Q3 validation

The 5,444 -> 4,174 drop of Q2 removed an unconditional credit, not measurably worse opportunities: the removed set is not worse than the retained one against matched random entries, in either window, with intervals that include zero (FX flips sign), and HTF alignment, now the practical admission criterion, has never shown a positive relation to outcomes. Nothing restores the artificial confluence. Q3 only stops a session-level sweep on the reaction's own candle from earning the session point a second time (4,174 -> 4,165). Details: [quality](../analysis-engine.md#acceptance-and-quality).

## KL12 (XAU exit/stop): close everything at 1R (REVERTED 19 Nov 2026)

> **Reverted by the owner on 19 Nov 2026: XAU Key Level uses the instrument's default 1R-4R ladder (40/20/20/20, breakeven after 1R), like every other XAU strategy.** The shipped `config/instruments.yml` has no `targeting_by_strategy` block. What remains of KL12: the read-only replay script (`algo-bot/scripts/kl_replay.py`), the strategy-scoped targeting mechanism (inert without a block, validated when one is configured), and the `tp1_full` journal label for one-target plans. The text below is the record of what was measured and decided, kept for the re-check with the OBS1 excursion columns.


KL12 gave XAU Key Level its own exit ladder (reverted, see above): **one target at 1R closing 100% of the position, no breakeven move**, through `overrides.execution.targeting_by_strategy.key_level` in `config/instruments.yml` (`mode: fixed_rr`, `reward_risk: 1.0`, `target_r_multiples: [1.0]`, `close_ratios: [1.0]`, `breakeven_after_r: null`). Every other XAU strategy keeps the instrument's `targeting` (the pack's 1R-4R 40/20/20/20 ladder with breakeven after 1R), and so does Key Level on every other instrument. Entries, stops, the risk leg and the analysis are unchanged; only the exit ladder differs. The block has the same leaves and validation as `targeting` and is checked when the instrument is built (ratios positive and summing to 1, `reward_risk` equal to the last multiple, ascending positive multiples, `breakeven_after_r` null or positive, no unknown leaf, a fixed_rr instrument); a strategy is matched by key, so "Key Level" and `key_level` are the same. A completed one-target group is journalled `exit_path = tp1_full` (it was `tp1_stop`, which reads as "TP1 booked, the rest stopped").

**Why.** `algo-bot/scripts/kl_replay.py` (read-only: SELECT-only Postgres session, ZRANGE-only Redis) replays the closed trades on the bars Redis still holds. Decision rule fixed in advance: V0 (the live rules) must have the same sign as the actual result on at least 70% of the replayed trades and total within 0.25R per trade; a variant is eligible with n >= 15, at least +0.25R per trade over the actual result on the same trades, positive expectancy, and a better result in **both** halves of the period (by close time); the winner is the eligible variant with the largest improvement. Applied to the run since 2 Nov: fidelity 36/46 (78%) and |-3.67 - (-12.55)| = 8.88R <= 11.5R, so the replay is usable; **only V1 is eligible** (+0.360R per trade over the actual, expectancy +0.087R, halves +0.254 / +0.465); V2 (+0.262, expectancy -0.011), V3 (+0.132), V4 (+0.112) and V5 (+0.344 on 24 trades, expectancy -0.017) are not. V5 would enter the whole volume at one price, which the owner rule of 2026-11-06 forbids; it is not implemented and is not eligible.

**How much to trust it.** The replay enters the whole position at the first fill (the live trade scaled in), V0 is more optimistic than reality (-3.67R against -12.55R actual), 22 of the 46 trades are replayed on M15 (coarse), and 46 trades is a small sample. V1's advantage is +0.528R per trade on the 24 M5 trades and +0.176R on the 22 M15 trades; its own expectancy is +0.087R, i.e. close to zero. Closing everything at 1R also gives up the 2R-4R upside of the trades that run (the eight trades that reached `tp1_be` or `tp2_full` since 9 Nov). Re-check after about 40 trades closed since the deploy with the OBS1 excursion columns (`mfe_r`, `mae_r`); if `exit_path` shows mostly `full_stop` again at near-zero MFE the trades are wrong from the start and the problem is detection, not exits.

**Rollback (done 19 Nov 2026).** The `targeting_by_strategy` block under XAU `overrides.execution` in `config/instruments.yml` was deleted (Algo Bot only; no Analysis Engine change).

### Replay, trades closed since 2 Nov (`--since 2026-11-02`)

# Replay: XAU %key%level% closed since 2026-11-02

Trades: 46; replayed: 46 (M5: 24, M15: 22); skipped: 0 (none)

## 1. Wrong from the start, or stopped before 1R? (MFE before the stop, live trades that were full stops)

| MFE before stop | n | actual net R |
|---|---|---|
| < 0.3R | 14 | -13.9 |
| 0.3-0.6R | 4 | -4.0 |
| 0.6-1R | 2 | -2.0 |
| >= 1R | 8 | -7.93 |

## 2. Fidelity: V0 (live rules, single entry) vs actual

Actual net R -12.55 vs V0 -3.67 over 46 trades; same sign on 36/46. If these are far apart, read section 3 as direction only.

## 3. The same trades under each alternative

Each variant is compared with the ACTUAL result of exactly the trades it could replay (V4 needs an ATR, V5 the confirmation bar).

| variant | rule | n | net R | expectancy | win | actual net R, same trades | difference |
|---|---|---|---|---|---|---|---|
| actual | what happened | 46 | -12.55 | -0.273 | 0.37 | | |
| V0_live_ladder | 1/2/3/4R 40/20/20/20, BE after 1R (the live XAU rules; fidelity check vs actual) | 46 | -3.67 | -0.08 | 0.54 | -12.55 | 8.88 |
| V1_1R_full | close everything at 1R | 46 | 4.0 | 0.087 | 0.54 | -12.55 | 16.55 |
| V2_1R_half_BE_2R | 50% at 1R, stop to BE, 50% at 2R | 46 | -0.5 | -0.011 | 0.54 | -12.55 | 12.05 |
| V3_ladder_no_BE | live ladder without the breakeven move | 46 | -6.47 | -0.141 | 0.24 | -12.55 | 6.08 |
| V4_wider_stop | live ladder, stop 0.5 M5 ATR further out (targets scale with the new R); refused (0R) if the stop would exceed the cap | 46 | -7.4 | -0.161 | 0.13 | -12.55 | 5.15 |
| V5_enter_reaction_close | live ladder, entered at the close of the confirmation bar instead of the ladder fill, same stop | 24 | -0.42 | -0.017 | 0.5 | -8.67 | 8.25 |

Caveats: one position at the first fill (the live trade scaled in); bid bars with the SELL spread added; a bar touching stop and
target counts as a stop; M15-replayed trades are coarse; small n, so a variant must beat the actual by a wide margin to mean anything.

### Replay, trades closed since 9 Nov (`--since 2026-11-09`, the v10/v11 population)

# Replay: XAU %key%level% closed since 2026-11-09

Trades: 25; replayed: 25 (M5: 24, M15: 1); skipped: 0 (none)

## 1. Wrong from the start, or stopped before 1R? (MFE before the stop, live trades that were full stops)

| MFE before stop | n | actual net R |
|---|---|---|
| < 0.3R | 5 | -5.0 |
| 0.3-0.6R | 4 | -4.0 |
| 0.6-1R | 2 | -2.0 |
| >= 1R | 5 | -4.94 |

## 2. Fidelity: V0 (live rules, single entry) vs actual

Actual net R -9.67 vs V0 -4.47 over 25 trades; same sign on 19/25. If these are far apart, read section 3 as direction only.

## 3. The same trades under each alternative

Each variant is compared with the ACTUAL result of exactly the trades it could replay (V4 needs an ATR, V5 the confirmation bar).

| variant | rule | n | net R | expectancy | win | actual net R, same trades | difference |
|---|---|---|---|---|---|---|---|
| actual | what happened | 25 | -9.67 | -0.387 | 0.32 | | |
| V0_live_ladder | 1/2/3/4R 40/20/20/20, BE after 1R (the live XAU rules; fidelity check vs actual) | 25 | -4.47 | -0.179 | 0.56 | -9.67 | 5.2 |
| V1_1R_full | close everything at 1R | 25 | 3.0 | 0.12 | 0.56 | -9.67 | 12.67 |
| V2_1R_half_BE_2R | 50% at 1R, stop to BE, 50% at 2R | 25 | -2.0 | -0.08 | 0.56 | -9.67 | 7.67 |
| V3_ladder_no_BE | live ladder without the breakeven move | 25 | -9.67 | -0.387 | 0.12 | -9.67 | 0.0 |
| V4_wider_stop | live ladder, stop 0.5 M5 ATR further out (targets scale with the new R); refused (0R) if the stop would exceed the cap | 25 | -4.0 | -0.16 | 0.12 | -9.67 | 5.67 |
| V5_enter_reaction_close | live ladder, entered at the close of the confirmation bar instead of the ladder fill, same stop | 24 | -0.42 | -0.017 | 0.5 | -8.67 | 8.25 |

Caveats: one position at the first fill (the live trade scaled in); bid bars with the SELL spread added; a bar touching stop and
target counts as a stop; M15-replayed trades are coarse; small n, so a variant must beat the actual by a wide margin to mean anything.

## Per-instrument overrides

`config/instruments.yml` can tighten Key Level for one pair (`applyKeyLevelOverrides`): `overrides.analysis.levels.minimum_key_touches` (also raises the touch minimum of the frame levels the other techniques read on that pair), `overrides.auto_algo.strategies.reaction.key_level.require_explicit_role` (skip levels whose role is ambiguous) and `...require_reaction_sweep` (publish a confirmed reaction only when it swept the level: traded strictly beyond the band's far edge).

| Instrument | Override | Why |
|---|---|---|
| XAU | `require_reaction_sweep: true` | 28 Oct 2026 owner report: swept setups held their edge in every period, unswept ones lost it (measured on XAU only). |
| GBPJPY | `minimum_key_touches: 3`, `require_explicit_role: true` | 25 Aug 2026: Key Level was 0/4 (-118 pips). Silent until v9 (no level had an explicit role); since v9 it publishes levels whose structure is pure swing lows (BUY) or pure swing highs (SELL), 63 candidates in a 7-day replay. |
| EURUSD | `minimum_key_touches: 3`, `require_reaction_sweep: true` | 6 Nov 2026 owner decision: 51 live trades, 55% full stops, -6.64 R on the 23 with a net R. Not measured on FX: replay 461 -> 165 setups, stop rate 38% -> 42%, no measured gain. `require_explicit_role` is not used: every EURUSD setup carries `m5_key_level_role_ambiguous`, so it would disable the strategy on the pair. Review after about 100 trades. |

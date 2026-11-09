# Key Level


## Strategy ID / version

`key_level`, `v9`. Implemented in
`analysis-engine/internal/strategy/keylevel`. Algorithm versions:
`structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`.

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
   (`evaluate_structural_reaction`: sweep reclaim, CHoCH rejection, strong
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

## Q3 validation

The 5,444 -> 4,174 drop of Q2 removed an unconditional credit, not measurably worse opportunities: the removed set is not worse than the retained one against matched random entries, in either window, with intervals that include zero (FX flips sign), and HTF alignment, now the practical admission criterion, has never shown a positive relation to outcomes. Nothing restores the artificial confluence. Q3 only stops a session-level sweep on the reaction's own candle from earning the session point a second time (4,174 -> 4,165). Details: [quality](../analysis-engine.md#acceptance-and-quality).

## Per-instrument overrides

`config/instruments.yml` can tighten Key Level for one pair (`applyKeyLevelOverrides`): `overrides.analysis.levels.minimum_key_touches` (also raises the touch minimum of the frame levels the other techniques read on that pair), `overrides.auto_algo.strategies.reaction.key_level.require_explicit_role` (skip levels whose role is ambiguous) and `...require_reaction_sweep` (publish a confirmed reaction only when it swept the level: traded strictly beyond the band's far edge).

| Instrument | Override | Why |
|---|---|---|
| XAU | `require_reaction_sweep: true` | 28 Oct 2026 owner report: swept setups held their edge in every period, unswept ones lost it (measured on XAU only). |
| GBPJPY | `minimum_key_touches: 3`, `require_explicit_role: true` | 25 Aug 2026: Key Level was 0/4 (-118 pips). Silent until v9 (no level had an explicit role); since v9 it publishes levels whose structure is pure swing lows (BUY) or pure swing highs (SELL), 63 candidates in a 7-day replay. |
| EURUSD | `minimum_key_touches: 3`, `require_reaction_sweep: true` | 6 Nov 2026 owner decision: 51 live trades, 55% full stops, -6.64 R on the 23 with a net R. Not measured on FX: replay 461 -> 165 setups, stop rate 38% -> 42%, no measured gain. `require_explicit_role` is not used: every EURUSD setup carries `m5_key_level_role_ambiguous`, so it would disable the strategy on the pair. Review after about 100 trades. |

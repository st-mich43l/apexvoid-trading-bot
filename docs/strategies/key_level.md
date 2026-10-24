# Key Level

> **Quality review (2026-10-14): RETAIN** (proposal, not applied). Technical PASS, execution PARTIAL, performance CONFLICTING, evidence MODERATE. Scope: all instruments; quality score to be treated as non-discriminating until reconstructed. See [ae-quality-certification.md](ae-quality-certification.md) and [ae-strategy-retirement-review.md](ae-strategy-retirement-review.md). Independent review: pending.

## Strategy ID / version

`key_level`, `v5`. Implemented in
`analysis-engine/internal/strategy/keylevel`. Algorithm versions:
`structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`.

## Purpose / thesis

Key Level reacts at a clustered key level, in the direction its role implies.
`v3` reproduces the Python detector behind the profitable XAU week of 14-18 Sep
2026 (+567 pips, 6W/3L), `key_level_reaction` at commit `a1c77584`, on the same
detector-contract frame as Fade Scalp, Range Edge and
Break & Retest, so the same bars give the same level, role, direction, reaction
and entry.

## Decision

1. Levels are walked nearest first; a level with fewer than
   `minimum_touches` touches is skipped.
2. The reaction band is the wider of the level's own band and the proximal band
   (`proximal_band_atr · ATR`).
3. The closed-bar **role** (`internal/keylevel.Role`, ported 1:1 from
   `key_level_role.py`) decides the direction tried: support → BUY,
   resistance → SELL; an ambiguous level is bought when price is above it, sold
   when below and, when price is inside the band, tried both ways. An accepted
   break (`breakout_accept_bars` consecutive closes beyond the band) is
   `broken_*` and skipped — Break & Retest owns it. `require_explicit_role`
   (an instrument override) skips ambiguous levels.
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
prerequisite are not gates. The reported target is the nearest opposing
liquidity pool at least `minimum_target_distance_atr · ATR` away, else
`fallback_target_r` times the risk beyond the entry; whether the available room
suffices is the execution policy's decision.

## Candidate

Entry is the reaction band; invalidation is beyond its outer edge by
`invalidation_buffer_atr · ATR`. `StructuralID` is the level
(`keylevel:<kind>:<price>`), not the confirmation, so a re-confirmation of the
same level is the same thesis.

**Reaction-anchored invalidation (v4).** A candidate with a confirmed reaction publishes the far edge of the structure or, if the bars from the touch to the confirmation traded beyond it, that extreme, in both cases one `invalidation_buffer_atr*ATR` further out (Buy: `min(structure low, lowest low touch..confirm) - buffer`; Sell: `max(structure high, highest high touch..confirm) + buffer`). A sweep or strong reclaim is confirmed by a wick that pierces the band, and a stop inside that wick has already been traded. The buffer is unchanged and has no pip floor; when the reaction's extreme set the price the candidate carries the evidence code `invalidation_beyond_reaction_extreme`. A resting candidate (no confirmation) keeps the structural price, and the fixed-reward:risk fallback target, the quality geometry and the published invalidation follow the new price. Measured on the committed captures in [reaction-anchored-stop.md](reaction-anchored-stop.md). Decisions and entry are unchanged (`LEGACY_PARITY_PROVEN`); the invalidation differs from the frozen publisher by design. `DetectorConfluence` carries the stars and the
machine-readable factors (`htf_aligned`, `touches`, `wick_rejection`,
`displacement_grade`, `structural_agreement`, `session_context`, `fib_touch`,
`choch`).

**Instrument stop budget (v5).** A candidate whose structural stop (distance from the proximal entry edge to the invalidation above) exceeds the instrument stop cap (XAU 70, EURUSD 20, GBPUSD 25, GBPJPY 35, USDJPY 28 pips) is **not published**: refused, never tightened, and neither the entry nor the invalidation is moved to fit. One inside [floor, cap] carries `stop_budget_structural`; one below the floor carries `stop_budget_floor` and execution widens it. Measured, with the Python side, in [legacy-stop-budget.md](legacy-stop-budget.md).

## Evidence

- `test/keylevelparity`: replays six committed real captures — XAU of the
  profitable week (14–21 Sep), XAU of the incident window (28 Sep – 6 Oct),
  EURUSD, GBPUSD, GBPJPY and USDJPY, 6 × 1351 closed M5 bars — and requires the
  same decision as the Python detector on every bar: 3310 decisions, 0
  mismatches, silence where Python was silent, and the same level, role, kind,
  touches, direction, entry band, confluence stars and reaction bars. The
  golden is produced by running the Python (`generate_oracle_golden.py`), never
  by Go. (GBPJPY has no decisions: its instrument rule requires an explicit
  support/resistance role, which the level primitive never produces, so Key Level
  was already silent there in Python and stays so.)
- `internal/strategy/keylevel` unit tests: support BUY, resistance SELL,
  broken support/resistance skipped, ambiguous resolution by price, opposing-zone
  contradiction and widened window, both-sides discard, best-of-several (and the
  nearest on a tie), counter-bias, entry too far, confluence floor, minimum
  touches, explicit-role requirement, target selection, contract selection.

## Q2: the session context is measured

`session_context` was set on every Key Level decision, which made a reaction with no higher-timeframe agreement reach two stars on candle evidence alone (touches 3 + pattern 3 + a constant 2). It is now true only when a finalized
session, previous-day or previous-week level supporting the trade was swept and reclaimed in the reaction's window (see [shared-structure-evidence.md](shared-structure-evidence.md)). `test/keylevelparity` keeps proving the
frozen detector with `frozen_session_context` set. On the 17-capture replay: 5,444 -> 4,174 opportunities (2/3 stars: 2,890/2,554 -> 3,122/1,052; 257 new ones where the better of two attempts changed). The 1,527 removed were not worse than the rest
(hypothetical +0.004R against -0.102R), so the change removes an unconditional constant, it is not a performance rule.

## Status and execution

- **Certification**: `LEGACY_PARITY_PROVEN` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument. XAU stop is held to the 50-60 pip envelope and scales in; FX takes one entry.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).

## Q3 validation

The 5,444 -> 4,174 drop of Q2 removed an unconditional credit, not measurably worse opportunities: the removed set is not worse than the retained one against matched random entries, in either window, with intervals that include zero (FX flips sign), and HTF alignment, now the practical admission criterion, has never shown a positive relation to outcomes. Nothing restores the artificial confluence. Q3 only stops a session-level sweep on the reaction's own candle from earning the session point a second time (4,174 -> 4,165). Details: [q3-quality-validation.md](q3-quality-validation.md).

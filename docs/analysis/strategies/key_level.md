# Key Level

## Strategy ID / version

`key_level`, `v3`. Implemented in
`analysis-engine/internal/strategy/keylevel`. Algorithm versions:
`structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`.

## Purpose / thesis

Key Level is the strategy that produced the profitable XAU week of
14–18 Sep 2026 (+567 pips, 6W/3L, 67 %). `v3` is the Python detector of that
week, `app/analysis/detectors.py::key_level_reaction` at commit `a1c77584`,
running on the same detector-contract frame as Snap Back, Fade Scalp,
Momentum Ride, Range Edge and Break & Retest, so the same bars give the same
level, role, direction, reaction and entry.

Why a rewrite: the previous Go `v2` only looked like that detector. Replayed
over the same real bars, `v2` reproduced the Python entry band on **1 of 638**
XAU decisions (its reaction band was the level's own narrow band, not the wider
proximal band), missed 194 of them, fired 495 times where Python was silent,
and emitted several candidates on 24 % of bars (it kept one per level and left
the choice to downstream arbitration).

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

`detector_contract: profit_week` reads the structure as it was in the profitable
week: levels counted from fractal swings only, and order blocks qualified by a
BOS break only. Two later Python changes (21 Sep: wick-touch episodes counted
into a level's touches; CHoCH-caused order blocks) moved Key Level's decisions:
over the same XAU bars the later detector gave a different decision on 121 of
the 686 bars where either fired (18 %) — most often 3 stars where the
profitable week gave 2, and 48 bars only it fired on. `current` selects the structure the other
detectors read. The permanent parity test fails (121 mismatches on XAU)
if `current` is selected.

## Filters Go had that the profitable system did not

`minimum_strength`, `proximity_atr` and the requirement of an opposing liquidity
pool as a target were Go additions, not Python behavior. They are removed as
gates. The reported target is the nearest opposing liquidity pool at least
`minimum_target_distance_atr · ATR` away, else `fallback_target_r` times the
risk beyond the entry; whether the available room suffices is the execution
policy's decision, as it was.

## Candidate

Entry is the reaction band; invalidation is beyond its outer edge by
`invalidation_buffer_atr · ATR`. `StructuralID` is the level
(`keylevel:<kind>:<price>`), not the confirmation, so a re-confirmation of the
same level is the same thesis. `DetectorConfluence` carries the stars and the
machine-readable factors (`htf_aligned`, `touches`, `wick_rejection`,
`displacement_grade`, `structural_agreement`, `session_context`, `fib_touch`,
`choch`).

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

# Fade Scalp Strategy V3

`fade_scalp` is the liquidity-sweep fade: price runs through a pool of equal highs or lows, the stops resting there are taken, the market closes back and shows a
confirmed reaction, and the trade is the reversion to the local mean. It runs on the engine's detector-contract frame, so the direction (the engine's structural
direction), the premium/discount gate, the chop gate, the sweep grade and the confluence floor are the frozen detector's. v3 (Q2) replaces v2, which invented a 2R
target when no structural objective existed, set the rejection factor on every decision and put the stop inside the sweep's wick.

## What must be true

For a BUY (the SELL is the exact mirror), in this order, every fact read from closed candles only:

1. **A liquidity event.** An equal-low pool (a sell-side pool with at least two touches) with a graded A or B sweep: a candle traded below the pool and the sweep graded
   by the close back. Swing-only (single-touch) pools and Grade-C sweeps never qualify; in chop the sweep must be Grade A at the correct edge of the range.
2. **A confirmed reaction** at the pool's entry zone (the shared closed-bar reaction: touch, then a recognised pattern in the reaction direction).
3. **Failed continuation.** No candle after the sweep has traded beyond the sweep's extreme (by more than one pip): a market that keeps running through the swept level is accepting it,
   not reverting. A strong displacement that was not reclaimed is therefore never faded.
4. **Location.** Strict premium/discount: a BUY must lie in discount.
5. **Invalidation.** The stop is `min(entry low, sweep low) - invalidation_buffer_atr * ATR`: the thesis fails when the sweep's own extreme is exceeded (v2 put it inside the wick).
6. **A structural objective with room.** The target is the NEAREST of the local mean and the nearest opposing pool beyond the entry:
   the mean is the established range's equilibrium when the frame has an unbroken one (`techniquezone.BuildScalpStructure`: confirmed, post-impulse or provisional), else the dealing range's.
   The nearest decides: a too-near mean is not skipped in favour of a farther pool, because the market would have to go through it. The distance must offer at least
   `minimum_reward_risk` (1.0) times the risk. With no structural objective there is no opportunity; there is no fixed reward:risk target.

## Measurements published (never entry triggers)

`Quality.Components`: `extension_atr = |mean - sweep extreme| / ATR`, the sweep candle's `body_ratio`, `upper_wick`, `lower_wick` (each `0` for a zero-range candle),
`sweep_penetration_atr`, `equal_level_touches`, `reward_risk`, `target_room_atr`, `reaction_displacement` (when the reaction showed a displacement leg) and `confluence_stars`.
`Quality.Overall` is `stars / 3`, the scale arbitration has always read.

## Confluence factors (measured)

| Factor | Set when |
|---|---|
| `htf_aligned` | the engine's higher-timeframe bias agrees |
| `touches` | the equal pool's touches |
| `wick_rejection` | the sweep candle itself is a rejection (`reaction.WickRejection`: wick at least the body, close in the reaction-side third) |
| `displacement_grade` | the grab was followed by a displacement leg (`Grab.ReactionDisplacement`), not because its grade was A |
| `structural_agreement`, `choch` | a CHoCH confirmation, as before |
| `session_context` | never: the equal pool and a session level that the same candle swept are one event (see [shared-structure-evidence.md](shared-structure-evidence.md)) |

v2 set `wick_rejection` on every decision.

## Configuration

`analysis.strategies.fade_scalp`: `invalidation_buffer_atr` (0.25), `minimum_reward_risk` (1.0), `expiry_hours`, `chop_edge_fraction`, `strict_premium_discount`. `target_r` was removed.

## Status and execution

- **Certification**: `GO_NATIVE_VALIDATED`: pinned by tests of its own contract (`internal/strategy/fadescalp`). It no longer reproduces the frozen Python detector (`test/detectorparity` still proves
  the analysis layer the detector reads: bias, regime and premium/discount, bar by bar; the oracle golden for Fade Scalp is kept untouched).
- **Entry-zone units**: the compat entry zone uses the instrument's own pip size (`analysis.legacy_read.compat.pip_size: 0`); `test/detectorparity` sets the frozen 0.1 to keep proving the oracle and pins the corrected band.
- **Symbols**: all instruments. **Execution**: unchanged; the stop envelope, ladder and sizing are Algo Bot's.
- **Arbitration**: opportunities on the same symbol and direction that share a thesis group, a structure or an entry corridor compete; the best by quality, confluence, structural quality and
  freshness trades and the rest are suppressed ([execution](../execution.md#same-thesis-arbitration)).
- **Q3 validation** ([q3-quality-validation.md](q3-quality-validation.md)): every Fade Scalp is a with-structure pullback entry (its direction is the engine's structural direction), not an exhaustion detection; the detector does not test for exhaustion. Measured against matched random entries, the published set is not better (FX 27 filled theses: -0.43 R [-0.82, -0.01]; XAU 14: +0.45 R, interval -0.67 to +1.21). The reward:risk gate avoided losers (-0.49 R, 76 theses), the continuation gate was neutral, and the confluence floor is not shown to select better setups. `fadescalp.Analyze` exports every considered sweep with the gate that refused it (`TestFadeConsiderations`). Disabling Fade Scalp on FX is proposed in the report and **not applied**; it needs explicit owner approval.

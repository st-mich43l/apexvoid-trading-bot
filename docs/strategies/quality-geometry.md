# Quality geometry (SQ-2)

Technical quality (`Quality.Overall`, the mean of the `score_*` components) must describe the entry the trade is actually made at, the risk it is actually exposed to and the structural room it actually has. SQ-2 corrects the geometry the quality models measure from. It changes no trade: entries, invalidations, targets, stop envelopes, evidence, identity and publication are byte-identical to before; only `quality` moved. `quality_ranking` stays `false`.

The geometry is shared (`internal/quality/geometry.go`, `internal/strategyutil/quality_geometry.go`); every strategy keeps its own thesis dimensions.

## Rules

**Entry location, trade-aware.** `chase = max(0, price - reference)` for a BUY, `max(0, reference - price)` for a SELL, where `reference` is the published proximal edge (BUY: the band's high, SELL: its low). `score_entry_location = Fall(chase / ATR; 0.25, 1.5)`. Price deeper in the band is not a chase and scores in full; it earns nothing more (`entry_favourable_depth_atr` is diagnostic). A missing ATR or a non-finite price scores 0. Published: `entry_chase_atr`, `entry_favourable_depth_atr`. Whether the price is still a valid entry stays with each strategy's own validity rules.

**Effective risk.** `structural_risk = |reference - invalidation|` (the invalidation must be behind the entry), `effective_risk = max(structural_risk, execution stop floor)`. The floor is the existing instrument `StopBudget` (`StopBudget.FloorPrice`, the value `EffectiveRisk` already used); no new configuration. A 6-pip structure under a 15-pip floor with a 30-pip objective is 2R, not 5R. Published: `structural_risk_pips`, `effective_risk_pips`, `effective_room_r`. Invalid geometry scores 0 and publishes none.

**Room.** Measured from the same reference. A target must be ahead of the entry (BUY `target > reference`); a target behind it is no reward (it is never `abs`-ed into one). The room is the smaller of the target's distance and the nearest valid opposing barrier's, less the barrier buffer once (`strategyutil.MeasureQualityRoom`, the canonical zone book through `internal/barrier`, as in SQ-1: live zones only, existing at the decision, side-correct, no same-wall exemption, an entry inside a wall has no room). `score_room = Ramp(room / effective_risk; 1, 3)`.
Provenance matters: an **observed** target (opposing liquidity, an opposing structure, a confirmed pivot, the opposite edge of a range) is structural room. A **fixed reward:risk** or a **projection** (a 1.272 extension, a measured move, a round handle) is a target hypothesis: it earns structural room only up to a barrier that is actually ahead of the entry, otherwise it scores 0. Published: `room_structural`, `target_room_pips`, `available_room_pips`, `barrier_ahead`, `room_limited_by_barrier`, `barrier_room_pips` (with a barrier), `room_reward_risk` (the target's own reward over the structural risk, the older measurement).

The opposing-barrier policy (`barrier_buffer_atr` with the instrument override, `execution_zone_max_width_atr/pips`) is injected by the engine (`applyQualityBarrierPolicy`) into the eleven strategies below; without it a strategy has no barrier book and a projection earns no structural credit.

## Audit and change per strategy

| Strategy | Reference / invalidation | Target and origin | Room before | Model | Defects found |
|---|---|---|---|---|---|
| supply, demand | proximal edge; zone far edge + buffer, reaction-anchored | nearest opposing structure (frame obstacles), else fixed R:R | `GeometryRoom` over the structural stop | v1 -> **v2** | `LocationScore` abs; room over structural, not effective, risk |
| order_block, fvg | same | same | same | v1 -> **v2** | same |
| flip_zone, trendline | proximal edge; structure + reaction extreme | nearest opposing liquidity, else fixed R:R (`SharedReactionDimensions`) | same | v1 -> **v2** | same |
| break_retest | proximal edge; protected structure | opposing confirmed pivot (observed) | abs location; structural risk, no floor | v1 -> **v2** | the execution floor was never read |
| confluence_zone | proximal edge; beyond supports and the reaction extreme | nearest opposing liquidity (observed) | structural risk although admission uses the floor | v1 -> **v2** | quality and admission disagreed on the risk |
| round_number | proximal edge; beyond the sweep/retest | liquidity (observed) or next handle (hypothesis) | abs location | v1 -> **v2** | floor not read |
| asia_range | proximal edge; beyond the sweep/retest | liquidity or opposite edge (observed); range midpoint, measured move (hypotheses) | same | v1 -> **v2** | floor not read |
| shock_retest | proximal edge; beyond the retest extreme | shock extreme (observed) or the **1.272 extension (a projection)** | `structural = true` for both | v1 -> **v2** | **a projected extension scored as structural room**; floor not read |
| impulse_pullback | worst planned entry; structural stop (30-40 pips) | corridor edge (SQ-1) | location abs | v2 -> **v3** | `LocationScore` abs (the room was fixed in SQ-1) |
| key_level, ifvg, crt | proximal edge | observed | already trade-aware and effective-risk (v2) | v2 (**no bump**) | scores unchanged; now use the shared helpers (a target behind the entry can no longer become a reward) and publish the risk geometry |
| range_scalp, impulse_retrace | SQ-1 | SQ-1 | SQ-1 | v2 | untouched |

## Where quality and execution room disagree

Quality is stricter than execution on purpose, and the differences are documented rather than hidden:

1. The Algo Bot's target-room check exempts a same-wall overlap for technique and confluence strategies (`allow_same_wall_overlap`) and hard-blocks only on containment or a raw room of zero. Quality scores an entry inside an opposing wall as having no room. On the replay this is common for the zone family (a SELL supply entry overlapping a live demand wall): the trade still publishes and executes as before; its `score_room` is 0.
2. Execution treats the buffered room as telemetry; quality scores the buffered room.
3. The barrier book holds the supply/demand kinds only (the Algo Bot's own scope). A zone-family strategy's admission uses its own frame obstacles for the target; quality limits the room by the canonical book as well, so the two can name different walls.

None of this changes admission, targets or execution barriers.

## Replay (real captures, XAU, EURUSD, GBPUSD, USDJPY, GBPJPY)

5,294 envelopes before and after. For every one the entry, invalidation, targets, stop envelope, evidence, formation/expiry, identity and publication are identical; the adapter accepts the same 5,043 and refuses the same 251 (250 reaction-less confluence observations and one stale reaction). Only `quality` changed: `score_entry_location` rose for 1,428 envelopes and fell for none (favourable depth is no longer a chase); `score_room` fell for 3,216 and rose for none (effective risk, observed provenance, barriers). Counts only: no outcome was simulated and nothing here is a claim about win rate or profitability.

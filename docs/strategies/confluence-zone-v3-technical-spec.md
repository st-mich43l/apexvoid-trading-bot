# Confluence Zone v3: technical specification

Status: **live.** Part A: `analysis-engine/internal/structuralconfluence` (the facts, independence, regions and causal
availability). Part B: `analysis-engine/internal/strategy/confluencezone` (the reaction, the honest stop, the target and the
strategy itself). Part C, the replay certification, is pinned by `analysis-engine/test/czreplay` against
`analysis-engine/testdata/confluence-zone-v3-replay-results.json`.

The contract this strategy answers:

> Are at least two genuinely independent, causally valid structures supporting the same directional trade at the
> same price location, with real confirmation, structural invalidation and sufficient target room?

Why v2 could not answer it (from the XAU forensic audit, in the repository history): `minimum_facts = 2` counted technique *labels*, the entry zone was the *union* of the
members, a member could be born on the reaction candle, and two quality factors were constants.

## 1. A structural fact

A fact is a structure that exists independently of the candle that later reacts to it. `structuralconfluence.Fact`
carries:

| Field | Meaning |
|---|---|
| `ID` | `source:side:originOpenTime:structuralLow:structuralHigh`. The same structure always has the same ID. |
| `Source`, `Side` | `supply_demand`, `order_block`, `fvg`, `ifvg` (or `crt`, never verified, see below); `buy` or `sell`. |
| `Low`, `High` | the **eligible** bounds: the band the zone chain publishes as tradable (the proximal slice of a wide zone). All confluence geometry is computed on these. |
| `StructuralLow`, `StructuralHigh` | the original, unclipped bounds. |
| `FormedAt` | open time of the candle the structure is anchored to. |
| `AvailableAt` | the **close** time of the first candle on which it was confirmable. |
| `SourceTimes` | open times of every candle the structure was built from. |
| `Atoms` | the underlying events it derives from (below). |
| `ParentID` | the structure it was derived from (an iFVG's FVG). |
| `Verified` / `UnverifiedReason` | whether the derivation could be reproduced. |
| `Touches`, `Mitigated`, `Score` | the zone chain's own lifecycle and score. |

Facts are derived from the zone chain's instances and the legs, breaks and swings of the same frame
(`DeriveFacts`). The matching rules live beside the constructors in `techniquezone/provenance.go`
(`LegOfSupplyDemand`, `LegOfOrderBlock`, `BrokenSwing`, `GapOfFVG`, `ParentGapOfIFVG`, `LegContaining`): they
re-run each constructor's own rule, so a derivation that the chain could not have produced is not found.

### Availability (closed-candle convention)

`Candle.Time` is the OPEN time; a candle is known only when it has closed. For bar index *i*,
`close(i) = open(i) + period`.

| Source | Confirmable when |
|---|---|
| supply/demand | the displacement leg that made it is complete: `close(leg.End)` |
| order block | the leg is complete, the structure break that qualified it has closed, and the swing that break closed beyond is a confirmed fractal swing: `close(max(leg.End, break.Index, swing.ConfirmedIndex))`. The frozen structure code decides a break against swings that need two later candles to exist; this makes that dependency explicit instead of letting a block count before its own swing could be known. |
| FVG | the candle that closes the gap: `close(gap.Newest)` |
| iFVG | the candle whose close inverts the gap: `close(inversion)` |
| CRT | never: the CRT instance embeds the reaction it is paired with (it is built from a sweep and reclaim on the very bars that react), so it is not a fact that exists independently of the reaction. It is returned unverified. |

A reaction whose touch candle opens at time *t* may use only facts with `AvailableAt <= t`.

### Population: structures are facts, as they stood before the touch

The frozen publishers' instances (`LegacyFrame.Techniques`) are the structures *price is at this moment*, judged on the
present: a zone drops out the candle after price leaves it, an FVG the reacting candle swept is "filled" and gone, and the
supporting set of one confirmation changes between two evaluations. v3 reads
`LegacyFrame.StructuralInstancesAsOf(i)` instead: the same supply/demand, order-block, FVG and iFVG instances with the
retest-proximity test off (`TechniqueSettings.IgnoreProximity`, default false, set only by this source), judged **on the
candles before candle `i`** (`techniquezone.InstancesFromScoredZonesAsOf`: only zones anchored before `i`, mitigation
re-counted on the prefix, spent / invalidated / mis-sized / filled tested on the prefix). A reaction touching candle `i`
therefore uses exactly the structures that existed, intact, when it opened, whatever that candle or any later one did to
them, and the same confirmation yields the same facts, episode, entry and stop on every later evaluation. Whether price is
*at* a structure is the reaction's business (the touch) and the entry's (`price_through_entry`, `entry_too_far`).
Strategies that read `Techniques` are unaffected (`TestCertification` asserts the other strategies' candidates are
byte-identical to master's on six real captures).

## 2. Independence

Two labels are not two confirmations. Every fact carries **provenance atoms**, the identities of the underlying events and source candles it derives from:

| Atom | Meaning |
|---|---|
| `leg:<dir>:<startOpenTime>` | a displacement leg |
| `break:<dir>:<openTime>` | a structure break |
| `gap:<olderBarOpenTime>` | one three-candle FVG episode |
| `inversion:<openTime>` | one inversion episode |
| `candle:<openTime>` | one source candle |

| Source | Atoms |
|---|---|
| supply/demand | its leg and every candle it was cut from: the three before the leg and the leg's own candles |
| order block | its leg, its break, its origin candle and the leg's candles |
| FVG | its gap, its three candles, and the leg that contains the gap's middle candle, if any |
| iFVG | its parent gap and the gap's three candles and leg; its inversion and its inversion candle; the leg that contains the inversion candle |

The candle atoms close a gap the first version had: a structure can share a *candle* with another without a qualified
displacement leg linking them. An FVG whose middle candle is the very candle that inverts a gap into an iFVG (one impulse,
two labels), an FVG cut from the three candles of a supply/demand base, and a second zone whose base is the first zone's
leg were each counted as two sources until an independent review reproduced them on real bars.

Facts that share any atom are **one source**. Dependence is transitive (A and C share *x*, C and B share *y*:
A, B, C are one event), computed by union-find, so an intermediate parent cannot hide it. A supply/demand zone
and an order block cut from one leg; an FVG that forms inside the displacement that made a zone; an FVG and the
iFVG made of it; a structure cut from the displacement that inverted a gap: each is one event, never two.
Different legs, different gaps and different inversions are independent, **including two structures of the same
technique** (two separate supply zones defending one price are two facts; v2 required two different labels).

`Groups` partitions the verified facts; `IndependentCount` is the number of groups. A group's representative is
its smallest ID, so nothing depends on input order. A fact whose derivation cannot be reproduced is
**unverified**: it is in no group, never counted, and reported. The same ID delivered twice is one fact; one
structure delivered under two IDs shares its atoms and is one group.

## 3. Regions

For one side, with eligible intervals *[L<sub>i</sub>, H<sub>i</sub>]*, the supported region is

    R = { p : the facts covering p contain at least minimum_facts independent groups }

and the strategy trades the maximal contiguous components of *R* wider than `minimum_width`.

Pairwise: `I = [max(L_i, L_j), min(H_i, H_j)]`, width `min(H) - max(L)`; a width of zero (two zones sharing
only a boundary) is not an overlap. For A = 4100-4105 and B = 4103-4108 the union is 4100-4108 and the region
4103-4105: a reaction at 4101 is not a reaction at the A+B overlap, one at 4104 can be.

More than two facts: the region is **not** the intersection of every fact (an extended chain A, B, C with
A ∩ C = ∅ would then have none) and not the union. `Find` sorts every bound, takes each elementary interval
between consecutive bounds, collects the facts covering all of it, counts their independent groups and merges
contiguous qualifying intervals: O(n² log n) for n facts, deterministic, no subset enumeration. Each segment keeps
its own supporting facts, so a region can be supported by a different subset in each part (`Region.Segments`).

A region can therefore be wider than any pairwise overlap (a zone tiled from below by one structure and from above by
another, each overlapping a third): that is intended. Every price of it is supported by at least `minimum_facts`
independent sources; it is the *supported area*, not "the intersection of two particular structures".

Rejections are returned with a reason: `single_independent_origin` (overlapping facts, fewer than the required
groups), `overlap_too_narrow` (a supported region not wider than `minimum_width`),
`provenance_unverified`, `structure_not_yet_available`.

### Identity

`EpisodeID(side, supportIDs)` is a hash of the side and the sorted IDs of the facts supporting the region. It is a
function of the set, so reordering, duplicating or re-delivering facts cannot change it. A region is evaluated
**as of the touch**: `Find(facts, side, touchOpenTime, …)` admits only facts confirmable at that instant, so a
later fact joining the same price does not rename an episode that was already established, and cannot revive it.

## 4. Reaction: measured at the supported region

A reaction is a (touch, confirmation) pair of closed candles inside the reaction window
(`reaction_lookback_bars`, three candles, measured in **time**, so a gap in the data never makes an old candle
fresh). Regions are evaluated **as of the touch** (section 3): the touch candle must reach a region supported by
facts confirmable before it opened. The touch may be the confirming candle itself.

Both sides are decided by one long-side routine on mirrored prices (p -> -p), so a SELL is the exact mirror image
of a BUY by construction and a randomized test proves it. For a long, region `[lo, hi]`, candle `O H L C`,
`range = H - L`:

    wick_ratio     = (min(O, C) - L) / range          the rejecting wick
    close_strength = (C - L) / range                  1 = closed at the high
    body_ratio     = |C - O| / range
    sweep_depth    = lo - L                            positive when the wick went through the far edge

| Pattern | Measured conditions |
|---|---|
| `sweep_reclaim` | `sweep_depth >= max(minimum_sweep_pips x pip, minimum_sweep_atr x ATR)`, `wick_ratio >= rejection_wick_ratio`, `close_strength >= rejection_close_strength`, `C >= lo + minimum_reclaim_atr x ATR` |

The first two also need `range >= minimum_rejection_range_atr x ATR`: a doji inside a region shows nothing, whatever its
proportions.
| `wick_rejection` | no sweep of that depth, the wick reaches the region (`L <= hi`), `wick_ratio` and `close_strength` as above, and the close in the upper half of the region or above it (`C >= (lo + hi) / 2`) |
| `strong_reclaim` | a bullish body, `body_ratio >= displacement_body_ratio`, `range >= displacement_range_atr x ATR`, `close_strength` as above, `C > hi` (the candle closes through the region) |

The colour of the body is **not** part of the first two tests, only where the candle rejected and where it closed:
a bearish-bodied candle with a long lower wick closing near its high is a demand rejection, a bullish-bodied one
that closes at its low is not. (The frozen publisher confirmed a grab candle whatever its direction; 22 of 177 XAU
confirmations in the audit were opposite-coloured.) Engulfing is not supported: it rejects the previous candle's
body, not the region's boundary.

A reaction is **refused** when:

- price closed below the region between the touch and the confirming candle (`price_accepted_through_region`);
- any later closed candle closes beyond the extreme the reaction itself traded to, or trades to the stop
  (`reaction_failed_after_confirmation`), so a rejection that has already failed, or a stop already hit by a wick, is never
  published or filled;
- the entry is no longer valid: price has closed below the entry band (`price_through_entry`) or has run more than
  `maximum_entry_atr` ATR above it (`entry_too_far`).

## 5. Quality evidence

Nothing is true because a candidate was emitted. The shared confluence rubric is scored from measurements:

| Factor | Measured as |
|---|---|
| `htf_aligned` | the engine's higher-timeframe read for the direction |
| `touches` | the largest touch count among the supporting structures |
| `wick_rejection` | the confirming candle's `wick_ratio >= rejection_wick_ratio` (false for a displacement candle with no wick) |
| `displacement_grade` | the confirming candle's body (`body_ratio x range / ATR`) `>= displacement_grade_atr` |
| `structural_agreement` | the engine's own M5 structure is `up` for a BUY, `down` for a SELL |
| `choch` | a change of character in the direction within the reaction window |
| `fib_touch` | the entry level touches a fibonacci level (engine computation, unchanged) |

v2 hard-coded `wick_rejection` and `structural_agreement` to true on every decision (177 of 177 XAU, 790 of 790 FX
in the audit) and its `reaction` component to 1. A measurement that is unavailable is **omitted** (an unknown
structure publishes no `structural_alignment`), never replaced by a neutral number.

`Quality.Overall` (2026-10-22) is the mean of the seven `score_*` dimensions of `confluence_zone.quality.v1` ([quality](../analysis-engine.md#acceptance-and-quality));
the confluence stars, from the unchanged `confluence.Evaluate`, remain the diagnostic and the floor. The region's zone score is the best supporting structure's score plus the frozen
contract's +2.5 for **every additional independent source** (v2 paid it per label, so a supply zone and an order
block of one leg earned it twice). No new live scoring formula was introduced and no arbitration input changed;
the measured components are in `Quality.Components` for the journal and the replay:

`independent_fact_count` (independent sources supporting every price of the region),
`independent_source_count`, `source_dependency_count` (supporting facts minus independent sources),
`overlap_width_atr`, `overlap_width_pips`, `overlap_integrity` (the share of the region price had not already
closed through before the touch), `source_integrity` (the share of supporting structures untouched),
`source_age_bars` (since the newest supporting structure became confirmable, at the touch),
`reaction_freshness_bars`, `rejection_wick_ratio`, `rejection_close_strength`, `displacement_strength` (body in ATR),
`reclaim_depth_atr`, `sweep_depth_atr`, `structural_alignment` and `htf_alignment` (only when readable),
`target_room_r`, `target_room_atr`, `technical_risk_pips`, `confluence_stars`.

## 6. Invalidation

The entry band is the supported region, clipped to the instrument's proximal slice (`fvg_entry_max_width_price`)
when wider. The stop is

    long :  min(protected_far_edge, adverse_reaction_extreme) - max(pip, invalidation_buffer_atr x ATR)
    short:  max(protected_far_edge, adverse_reaction_extreme) + max(pip, invalidation_buffer_atr x ATR)

where the *protected far edge* is the lowest **structural** low (highest structural high) among the structures that support the region (not just the tradable slice the entry overlaps: a stop inside a zone that is still valid would sit inside the structure it claims to protect) and the
*adverse reaction extreme* is the lowest low (highest high) the reaction traded to between the touch and the
confirming candle. The entry (confluence) and the stop (invalidation) deliberately do not share boundaries: the
region may be narrower than the structures that make it, and the structure, not the region, is what failing
invalidates. By construction the stop is beyond the whole entry band, the protected edge and the adverse wick (a
randomized test checks it on every published setup).

The instrument's execution stop cap (`instruments.<symbol>.stop_envelope.max_pips`, injected as
`execution_stop_max_pips`) is a gate, not a target: risk is measured from the proximal edge of the entry band to the
stop, and a setup over the cap is **refused** (`stop_exceeds_execution_envelope`). The stop is never clamped into
the wick and the envelope, the volume and the ladder are untouched. The instrument's floor (`execution_stop_min_pips`) is
used only to judge reward:risk: the execution layer widens a tighter stop to it, so the trade's reward:risk is measured
against that stop, not the technical one.

## 7. Target

The target is the nearest **unswept** opposing liquidity pool strictly beyond everything price has traded since the
touch (a pool is unswept only if no candle after its latest member swing has traded beyond it; one whose member swings
cannot be found cannot be shown unswept and is not a target) and at least `minimum_target_distance_atr` from the entry. There is no fallback: v2 invented
`fixed_reward_risk` (entry + 2 x risk) when no pool qualified and labelled the result as opposing liquidity; v3
publishes nothing instead (`no_opposing_liquidity`, `insufficient_target_room`). Further tests:

- `reward / risk >= minimum_reward_risk` (`reward_risk_below_minimum`), risk and reward measured from the same
  proximal reference;
- no **defended** opposing structure nearer than the room the trade needs: an opposing region supported, by the same
  standard, by `minimum_facts` independent sources whose near edge lies closer than `minimum_reward_risk x risk` blocks
  the target (`target_blocked_by_opposing_structure`);
- pools are computed from the closed candles of the evaluated prefix, so no target is derived from a future bar.

The TradePlan ladder and position management are the Algo Bot's and are not touched.

## 8. Lifecycle and identity

`Evaluate` emits, per direction, at most one confirmed candidate (the highest stars) and the resting observations of
regions price is at. A confirmed candidate carries `Reaction` with the touch and confirmation times and the pattern;
the Algo Bot's adapter still requires it (`requires_reaction`) and rejects a candidate without one, so a resting
overlap can never be executed. A resting observation claims no reaction in its evidence
(`m5_distinct_zone_overlap`, `m5_confluence_independent_facts`; a confirmation adds `m5_confluence_reaction`) -
v2's resting candidate named `m5_confluence_reaction` and a confirmation time it did not have.

The thesis identity (`StructuralID`, `Reaction.ZoneID`) is the **episode** (section 3). The opportunity ID (`SetupKey`) is a function
of the **side and the confirming candle**, not of the episode, the touch, the evaluation time or the order of any input
list. One confirmation is one reaction: the touches within its window can see slightly different supporting structures
over the same price band (different episodes), and keying the ID on the episode published one confirmation under two
IDs, with an identical entry, stop and target, on real GBPUSD data (found by the replay invariant
`TestOneConfirmationIsNeverRenamedBetweenEvaluations`). A re-evaluation of the same confirmation on the next candle is
the same opportunity, even if the episode selected for it changes (its thesis identity and geometry are then updated);
a later fact joining the same price does not revive an earlier episode; a genuinely new confirming candle is a new
opportunity on the same structures. At most one confirmed candidate per direction is published, so one ID per
(side, confirmation) is never ambiguous.

## 9. Configuration

`config/analysis.yml` `strategies.confluence_zone` (version `v3`). Every coefficient is required (no code
default). The close-strength, wick and displacement values are the ones iFVG v3 uses; none was fitted to a trade,
and part C shows how the result moves when they change.

| Key | Value | Meaning |
|---|---|---|
| `minimum_facts` | 2 | independent sources that must support every price of the region (>= 2) |
| `minimum_overlap_pips`, `minimum_overlap_atr` | 1.0, 0.05 | a region must be strictly wider than the larger |
| `reaction_lookback_bars` | 3 | touch and confirmation window (confirming candle at most 2 candles old) |
| `minimum_sweep_pips`, `minimum_sweep_atr` | 1.0, 0.05 | the depth that makes a wick a sweep |
| `minimum_reclaim_atr` | 0.0 | the close must be at least this far back inside after a sweep |
| `rejection_wick_ratio`, `rejection_close_strength` | 0.25, 0.6 | the rejecting wick and where the candle closes |
| `displacement_body_ratio`, `displacement_range_atr`, `displacement_grade_atr` | 0.6, 0.5, 1.0 | the displacement rejection and the displacement factor |
| `minimum_rejection_range_atr` | 0.25 | the smallest candle, in ATR, that can show a rejection |
| `invalidation_buffer_atr` | 0.5 | unchanged from v2 |
| `minimum_target_distance_atr`, `minimum_reward_risk` | 1.0, 1.15 | target room (the reward:risk is iFVG v3's) |
| `expiry_hours` | 12 | unchanged from v2 |

The shared detector leaves (`maximum_entry_atr`, the confluence floor and star ratios, `fvg_entry_max_width_price`,
`pip_size`) are injected by the engine as before, and `execution_stop_max_pips` is injected per instrument
(`applyConfluenceZoneGeometry`, the same mechanism as iFVG v3).

## 10. Verification

- `internal/structuralconfluence`: the required negative example, shared boundaries, strict minimum width,
  extended chains, contiguous support with varying subsets, nested intersections, disjoint regions, one origin
  under two labels, three labels over two origins, transitive dependence, duplicate IDs, one structure under two
  IDs, unverified facts, the availability cutoff to the second, and three randomized properties over 900+ random
  populations: the result is identical for every permutation and duplication of the input; a price is in a region
  exactly when a brute-force count of its covering facts' independent groups reaches the minimum; regions are
  maximal and disjoint.
- Derivation on hand-built frames (legs, breaks, swings, gaps and inversions with known indexes), including the
  order block that must wait for its swing's confirming candle, and the unverified cases.
- `test/structuralconfluence`: the same on real XAU bars (the 13 Oct 2026 production capture), reproducing the
  facts of the v2 trades: 12 Oct (supply/demand + order block: one leg), 13 Oct (FVG + iFVG: one down leg),
  9 Oct 17:10 (the demand zone is created by the very candle that reacted), 5 Oct 14:05 (the iFVG is born on the
  rejection candle) and 9 Oct 13:10 (two sources, the FVG and the iFVG being one impulse candle; the supported region is narrower than the union).
  Plus that extra warm-up history and appended future candles do not change a fact.

- `internal/strategy/confluencezone`: positive fixtures (two independent demand structures, the exact SELL mirror,
  three facts over two origins, nested and disjoint regions, a sweep reclaim, a wick rejection, a displacement
  reclaim, a fresh retest, the nearest valid target, XAU and EURUSD precision) and negative fixtures that each assert
  the specific reason: one origin, a duplicated ID, two labels of one leg, an FVG with its own iFVG, a dependency
  through an intermediate parent, a structure created on or after the reaction candle, an unverified fact, a shared
  boundary, an overlap narrower than the minimum, a reaction at the union boundary but outside the overlap, a weak
  confirmation, colour-independent direction, conflicting directions, a later member that must not rename an
  episode, a failed reaction, price accepted through the region, the stop envelope, target room (none, too close,
  below reward:risk, already swept, blocked by an opposing confluence), duplicate and out-of-order candles, a missing
  ATR, too little history, a gap in the data and a price that has run through or away from the entry. Randomized
  properties over 1,500+ markets: every published setup satisfies the whole contract (stop beyond the adverse
  extreme, the structure and the entry; target beyond the entry with the required reward:risk; supporting facts
  confirmable before the touch; nothing closed beyond the reaction since); reordering or duplicating the inputs does
  not change the analysis; older history does not change a decision; BUY and SELL are exact mirror images.
- `test/strategy`: the strategy through the engine's context (candidate fields, measured factors, resting
  observation never a confirmation, one opportunity across repeated evaluations, one-origin refusal, missing
  configuration fails closed).

Part C adds the replay certification.

- `test/czreplay` (part C): six real production captures replayed through the engine with four models (A legacy
  intersection, B Python-parity union, C v3 structure with v2 confirmation, D full v3), causal hypothetical fills,
  chronological development/held-out periods, the frozen v2 re-implementation reproducing every reproducible frozen-regime
  production trade, the seven XAU trades rebuilt, the other strategies' candidates byte-identical to master's, and the
  engine-level invariants: appending future candles, randomising them, removing 500 candles of warm-up, and reflecting the
  whole market do not change (or exactly mirror) the v3 decisions, and one confirmation is never published under two
  overlapping opportunities. The results are committed as
  `analysis-engine/testdata/confluence-zone-v3-replay-results.json` and a coefficient sensitivity as
  `analysis-engine/testdata/confluence-zone-v3-sensitivity.json`.

## 11. Known limits

- iFVG facts carry no zone-chain score and no touch count (the chain does not score an inverted gap), so those two quality
  inputs are constant for them; the region score is the best *scored* supporting structure plus the independence bump.
- A liquidity pool carries no formation index; its member swings are recovered from the frame's swings (within three pool
  bands of its level), and a pool whose members cannot be found is not a target.
- The zone chain's structure code is the frozen one: its swings and breaks are decided on the whole window, not
  causally. v3 does not trust it: an order block is confirmable only when the swing its break closed beyond is a confirmed
  fractal swing, and a supply/demand zone only when its leg is complete.
- Stars and the zone score come from the chain's multi-factor scoring at the evaluated candle (they are known when the
  decision is made), not as of the touch.
- The engine's liquidity-pool builder (frozen, shared by every strategy) is not mirror-symmetric: on a reflected XAU capture
  entries and stops of all 47 v3 decisions are exact mirrors, but 17 targets differ (1 by more than an ATR) because the two
  markets are handed different pools. The v3 logic itself is an exact mirror on identical inputs (property test).
- CRT instances embed their own reaction and never count as facts.
- The candle atoms make the independence rule depend on the zone chain's three-candle base: two legs separated by two or
  fewer pullback candles are one source (the second zone's base contains the first leg), three or more are two. The
  cliff is that of the chain's own supply/demand construction; the cases it merges that were inspected on real bars
  (an FVG and an iFVG made by one impulse candle, an FVG cut from a zone's base, a flag) are one event.
- The structure population is every instance not spent or invalidated in the engine's 400-candle window, wherever price
  is, so a displacement rejection (`strong_reclaim`) is a real, common pattern (it was nearly unreachable while facts had to
  be at the price at the evaluated candle); `entry_too_far` is its only distance protection.
- `Setup.RewardRisk` and the `target_room_r` component are reward over the risk the execution layer will run (the stop
  widened to the instrument floor); `technical_reward_risk` is reward over the stop itself.

## 12. What this specification does not claim

It is a technical contract. It makes **no** profitability claim: the replay does not show an edge for v2 or v3 on XAU or
FX, and a hypothetical outcome is not a broker outcome. The audit's open limits (broker deal P&L, per-leg fills,
commission and swap) are unchanged.

# Confluence Zone v3: technical specification

Status: **parts A and B implemented** (A: `analysis-engine/internal/structuralconfluence`, the facts, independence,
regions and causal availability; B: `analysis-engine/internal/strategy/confluencezone`, the reaction, the honest
stop, the target and the strategy itself). **Part C**, the replay certification, completes this page in its own
pull request. Nothing here is deployed or enabled by being merged: Confluence Zone's enablement, containment, risk
and arbitration settings are unchanged.

The contract this strategy answers:

> Are at least two genuinely independent, causally valid structures supporting the same directional trade at the
> same price location, with real confirmation, structural invalidation and sufficient target room?

Why v2 could not answer it is in [the forensic audit](confluence-zone-xau-forensic-audit.md) (on the audit's own
branch until merged): `minimum_facts = 2` counted technique *labels*, the entry zone was the *union* of the
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

### Population: structures are facts, not "where price is now"

The frozen publishers' instances (`LegacyFrame.Techniques`) are the structures *price is at this moment* (a zone drops
out of the list the candle after price leaves it). That makes a structure vanish the candle after it rejected price and
would let the supporting set of one episode change between two evaluations of the same confirmation. v3 reads
`LegacyFrame.StructuralInstances` instead: the same supply/demand, order-block, FVG and iFVG instances with the
retest-proximity test off (`TechniqueSettings.IgnoreProximity`, default false, set only by this source). A structure is
still dropped when it is spent, invalidated, mis-sized or a filled FVG; whether price is *at* it is the reaction's
business (the touch) and the entry's (`price_through_entry`, `entry_too_far`). Strategies that read `Techniques` are
unaffected (`TestCertification` asserts the other strategies' candidates are byte-identical to master's on six real
captures; `TestStructuresAreFactsIndependentOfWhereThePriceIsNow` shows the frozen instances are always a subset).

## 2. Independence

Two labels are not two confirmations. Every fact carries **provenance atoms**, the identities of the underlying
events it derives from:

| Atom | Meaning |
|---|---|
| `leg:<dir>:<startOpenTime>` | a displacement leg |
| `break:<dir>:<openTime>` | a structure break |
| `gap:<olderBarOpenTime>` | one three-candle FVG episode |
| `inversion:<openTime>` | one inversion episode |

| Source | Atoms |
|---|---|
| supply/demand | its leg |
| order block | its leg, its break |
| FVG | its gap; the leg that contains the gap's middle candle, if any |
| iFVG | its parent gap and that gap's leg; its inversion; the leg that contains the inversion candle |

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
| `wick_rejection` | no sweep of that depth, the wick reaches the region (`L <= hi`), `wick_ratio` and `close_strength` as above, `C >= lo` |
| `strong_reclaim` | a bullish body, `body_ratio >= displacement_body_ratio`, `range >= displacement_range_atr x ATR`, `close_strength` as above, `C > hi` (the candle closes through the region) |

The colour of the body is **not** part of the first two tests, only where the candle rejected and where it closed:
a bearish-bodied candle with a long lower wick closing near its high is a demand rejection, a bullish-bodied one
that closes at its low is not. (The frozen publisher confirmed a grab candle whatever its direction; 22 of 177 XAU
confirmations in the audit were opposite-coloured.) Engulfing is not supported: it rejects the previous candle's
body, not the region's boundary.

A reaction is **refused** when:

- price closed below the region between the touch and the confirming candle (`price_accepted_through_region`);
- any later closed candle closes beyond the extreme the reaction itself traded to (`reaction_failed_after_confirmation`),
  so a rejection that has already failed is never published or filled;
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

`Quality.Overall` keeps the scale live arbitration has always seen: the confluence stars out of three, from the
unchanged `confluence.Evaluate`. The region's zone score is the best supporting structure's score plus the frozen
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

where the *protected far edge* is the lowest low (highest high) among the structures that support the region and the
*adverse reaction extreme* is the lowest low (highest high) the reaction traded to between the touch and the
confirming candle. The entry (confluence) and the stop (invalidation) deliberately do not share boundaries: the
region may be narrower than the structures that make it, and the structure, not the region, is what failing
invalidates. By construction the stop is beyond the whole entry band, the protected edge and the adverse wick (a
randomized test checks it on every published setup).

The instrument's execution stop cap (`instruments.<symbol>.stop_envelope.max_pips`, injected as
`execution_stop_max_pips`) is a gate, not a target: risk is measured from the proximal edge of the entry band to the
stop, and a setup over the cap is **refused** (`stop_exceeds_execution_envelope`). The stop is never clamped into
the wick and the envelope, the volume and the ladder are untouched.

## 7. Target

The target is the nearest **unswept** opposing liquidity pool strictly beyond everything price has traded since the
touch and at least `minimum_target_distance_atr` from the entry. There is no fallback: v2 invented
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

The thesis identity (`StructuralID`, `Reaction.ZoneID`) is the **episode** (section 3). The opportunity ID is a function
of the episode and the **confirmation** time, not of the touch, the evaluation time or the order of any input list.
A re-evaluation of the same confirmation on the next candle is the same opportunity; a later fact joining the same
price does not rename or revive an earlier episode; a genuinely new reaction is a new opportunity on the same
structures.

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
  rejection candle) and 9 Oct 13:10 (three separate origins; the supported region is narrower than the union).
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

# Confluence Zone v3: technical specification

Status: **in construction.** Part A (this section set: structural facts, independence, regions, causal
availability) is implemented in `analysis-engine/internal/structuralconfluence` and is **not yet wired into the
strategy**. Parts B (confirmation, invalidation, target, strategy wiring) and C (replay certification) follow in
their own pull requests and complete this page.

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

## 4. Verification

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

Parts B and C add: the measured reaction at the region, the structural stop and target, the strategy wiring and
the replay certification.

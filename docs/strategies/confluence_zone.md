# Confluence Zone Strategy V3


`confluence_zone` is the one approved compositional exception: it trades a price
region that at least `minimum_facts` (two) **genuinely independent**, already-canonical
structures support at the same time, after a measured directional rejection of that
region. It reads the zone chain's supply/demand, order-block, FVG and iFVG instances as
neutral facts; it never invokes another strategy and never suppresses one (a valid
FVG, iFVG, Supply, Demand or Order Block candidate is published whether or not it is
part of a confluence).

v3 replaces the Python-parity v2, whose contract could not answer "are two
independent structures supporting the same price?": it counted technique *labels*,
published the *union* of the members as the entry, let a member be born on the
reacting candle, hard-coded two quality factors, and invented a 2R target. The XAU forensic audit
that found this predates v3 and is in the repository history; the full contract, the mathematics and the verification are in
[confluence-zone-v3-technical-spec.md](confluence-zone-v3-technical-spec.md). This
page is the summary.

## Status and execution

- **Owner**: Go Analysis Engine. Packages `internal/structuralconfluence` (facts,
  independence, regions, causal availability: pure) and `internal/strategy/confluencezone`
  (reaction, stop, target, candidate). The Algo Bot applies execution policy and
  arbitration only; it keeps no second decision.
- **Certification**: `analysis-engine/test/czreplay`, pinned against `analysis-engine/testdata/confluence-zone-v3-replay-results.json`
  (technical certification and the trading evidence are separate judgements).
- **Symbols**: all instruments. Pip size, the proximal entry slice
  (`fvg_entry_max_width_price`) and the execution stop cap (`execution_stop_max_pips`)
  come from the instrument configuration.
- **Execution**: unchanged by this document. The Algo Bot's scope needs a confirmed
  reaction and an evidence code prefixed `m5_distinct_zone_overlap` or
  `m5_confluence_reaction`; both are carried. Enablement, containment, risk and
  arbitration are not changed by v3.
- **Arbitration**: opportunities on the same symbol and direction that share a thesis
  group, a structure or an entry corridor compete; the best by execution eligibility, then freshness, then id
  trades and the rest are suppressed ([execution](../execution.md#same-thesis-arbitration)). `Quality.Overall` is the mean of this strategy's `score_*` dimensions ([quality](../analysis-engine.md#acceptance-and-quality)); the confluence stars remain a compatibility diagnostic and the only quality-based gate (`min_confluence`).

## The contract in one page

1. **Facts.** Every supporting structure is a `Fact` with a canonical ID, its eligible and
   structural bounds, the candles it was built from, the **closed-candle time it became
   confirmable** and its provenance (the displacement leg, structure break, gap and
   inversion it derives from).
2. **Independence.** Facts that share any provenance event are one source (transitively):
   a supply/demand zone and an order block of one leg, an FVG formed inside the
   displacement that made a zone, an FVG and its own iFVG, a structure cut from the
   displacement that inverted a gap. Different legs, gaps and inversions are independent,
   including two zones of the same technique. A fact whose derivation cannot be
   reproduced is unverified and never counted.
3. **Region.** The supported region is the set of prices covered by at least
   `minimum_facts` independent sources, found by an exact interval sweep: never the
   union, never zero-width, and not required to be one common intersection of every
   fact. A is 4100-4105, B is 4103-4108: the region is 4103-4105; a reaction at 4101 is
   not a reaction at the overlap.
4. **Causality.** The region is evaluated as of the touch candle's open, on the structures
   as they stood before that candle (not spent, invalidated or filled by it): a structure
   created by the reacting candle, or later, cannot support it, and one the reacting
   candle swept is still the structure it was.
5. **Reaction.** A touch and a confirming candle inside a three-candle window, measured
   from the candle (rejecting wick, close location, sweep depth, displacement), not from
   its colour; refused if price was accepted through the region, if the reaction failed
   afterwards, or if price has run through or away from the entry.
6. **Quality.** Every factor is a measurement; an unavailable one is omitted.
7. **Stop.** Beyond the whole supporting structures' full extent *and* the reaction's own
   adverse extreme, plus a buffer; refused (never tightened) beyond the instrument's
   execution cap; a stop a later wick already traded to is never published.
8. **Target.** The nearest unswept opposing liquidity with room (a pool price has already
   traded beyond is not a target); none is invented, a defended opposing confluence nearer
   than the required room blocks the trade, and reward:risk is judged against the stop the
   execution layer will use.
9. **Identity.** The thesis is the confluence episode (side + supporting facts as of the
   touch); the opportunity is the side and the confirming candle (one confirmation is one opportunity,
   whichever touch or supporting set it is seen through). Reordering inputs,
   re-evaluating on the next candle or a later structure joining the price changes
   nothing.

Both sides are one routine on mirrored prices; BUY and SELL are exact mirror images.

## Resting versus confirmed

A resting observation (a region price is at, no confirmation) carries no `Reaction` and
no `m5_confluence_reaction` evidence and can never be executed (the adapter requires a
reaction). A confirmed opportunity carries the touch, the confirmation and the pattern.

## Verification

`internal/structuralconfluence` and `internal/strategy/confluencezone` (positive and
negative fixtures, randomized properties), `test/structuralconfluence` (real XAU bars),
`test/strategy` (through the engine's context), `test/engine` (configuration and the
injected stop cap) and `algo-bot/tests/test_confluence_zone_v3_adapter.py` (the real v3
payload through the adapter). The replay certification is part C.

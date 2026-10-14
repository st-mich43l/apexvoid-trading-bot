# Range Edge Strategy V3

`range_edge` trades the edge of the engine's local scalp range, built on the
detector-contract frame by a faithful port of the frozen scalp structure:

- **Barriers.** Wick-rejection contacts and micro-swing contacts are clustered
  (tolerance from ATR, candle noise, pip size and a configured minimum), merged
  into touch episodes, and kept only with the configured touches and at least two
  wick rejections. Body holds raise the score and grade but are not wick
  evidence. A barrier with `break_closes` consecutive accepted closes is broken.
- **Fallback barrier.** When exactly one side exists, a controlled fallback may
  create the other from the local extreme after the first edge's first touch,
  within the configured width range, and only with the configured number of
  independent confirmations (touches, a wick rejection, a session level, inside
  closes). It is never created from a round number alone.
- **Range states.** `confirmed`, `post_impulse` (a large displacement followed by
  a contracted rotation), `provisional` (one fallback edge, when enabled) or
  `broken` (accepted breakout displacement), ranked confirmed, post-impulse,
  provisional, then quality, distance to equilibrium, width and levels.
  Minimum/maximum width, minimum room and `minimum_inside_closes` gate a range.

The structure and the reaction below are the frozen detector's; **v3 adds the technical integrity of
the edge** (next section). The detector evaluates the nearer edge first, widens only the touch window to
reach the established barrier while keeping confirmation recent, and applies the
touch, wick-rejection (a Grade-A grab waives both minimums), accepted-close and
room gates and the shared reaction. The first edge that passes all of that is
the decision: if it then fails the confluence floor the other edge is not tried.
The first target is the equilibrium and the second the opposing edge.

## V3: the edge must still be a range edge

Range Edge is a mean-reversion strategy: it fades a boundary the market has shown it respects and aims for the range's
equilibrium. It must not keep fading a boundary whose technical basis has failed. The frozen detector could not tell,
because the range builder discards a barrier price has accepted through and the sliding lookback then builds a new one
higher up, with a new first-touch time, which is a new thesis for every guard downstream.

**The incident (production 12 Oct, USDJPY).** The replay reproduces, id for id, the opportunities production published
(`opp_4ac72614`, `ab2e1a3a`, `17d2b4fc`, `d6fe8047`, `f5cd83b5`, `b7d810e8`): the range top at 158.388 (three wick
rejections, 05:20-05:45) was accepted through at 07:00-07:05 (two closes above it; the builder then called the range
broken), and from then on every new upper barrier (158.477 first touched 07:05, 158.452 07:30, 158.452 07:10, 158.635
10:25) was built from contacts made *after* the break above a lower edge (158.291) formed at 06:20, **before** it: one advance
climbing a staircase, rebuilt as a fresh range five times. v2 published six SELLs for the advance; the Algo Bot admitted five
(the sixth was refused as overlapping): two before the break (06:05, 06:51) and three after it (08:16, 09:01, 11:12; the last
a winner). All but the 11:12 one stopped out.

### Contract (`internal/strategy/rangeedge/integrity.go`, one routine for both sides)

"Adverse" is up against a resistance (a SELL) and down against a support (a BUY); every test is written once in those
terms, so BUY and SELL are exact mirrors (asserted on mirrored inputs).

1. **Kept, not discarded.** The scalp structure builder now returns the barriers it dropped because price accepted
   through them (`ScalpStructure.Broken`: level, touches, wick rejections, first and last touch, the bar on which
   `break_closes` accepted closes completed, the tolerance used). Nothing in the barriers or the range changes.
2. **Retired edge** (`edge_accepted_break`, `range_not_reestablished`). A fade is refused when its edge is at or beyond an
   *outermost* broken edge of its side (a cluster inside the range that price ran through on the way to the edge is not the
   range breaking), unless
   - price **reclaimed** it: `reclaim_closes` consecutive closes back on the range side of the broken level less its
     tolerance (the mirror of `break_closes`); or
   - a **genuinely new range** has formed: the edge and the nearest floor (the live barrier on the other side nearest price)
     are both made of contacts *after* the break, and price has closed inside them `minimum_inside_closes` times since. A
     stale floor, or price that never came back inside, is the old advance continuing.
3. **Break pending** (`break_pending_unreclaimed`). The builder tolerates one close beyond the edge (`break_closes`
   is two). While price is still beyond the edge there is nothing to fade; a sweep that closes back inside is the strategy's own
   thesis and stays tradable.
4. **Fresh rejection** (`rejection_not_back_inside`). The confirming candle must close back on the range side of the edge.
5. **Pressure** (`pressure_into_edge`). `pressure_bars` candles ending at the confirming one, each with a higher low and a
   higher close (mirrored for a support) and rising at least `pressure_minimum_rise_atr` in total, are price pressing the
   edge, not rejecting it; a confirming candle that closes lower breaks the run.
6. **Gaps.** A missing candle inside the reaction or pressure window fails closed (`data_gap`): a gap is never mistaken for a
   fresh reaction.
7. **Identity.** The thesis is the range episode: `range:<side>:<first-touch time>` as before, and
   `range:<side>:break:<time of the break>` for a range re-established after a break, so every edge the lookback rebuilds in
   that range is one opportunity, and the next break is a new episode. A reclaimed break returns the edge to its own episode.
8. **Measured quality components** (no change to `Quality.Overall`): `rejection_close_location`, `reclaim_depth_atr`,
   `break_distance_atr`.

Lifecycle states, as an edge is tested: `range_valid` (intact) -> `break_pending` (closes beyond, not yet accepted) ->
`range_broken` (accepted, not reclaimed; retired). A reaction on a valid edge is the confirmed reaction. Requalification is
the reclaim or the re-established range above, never a clock.

Coefficients (`config/analysis.yml` `strategies.range_edge`): `reclaim_closes 2`, `pressure_bars 4`,
`pressure_minimum_rise_atr 1.0`, `minimum_reclaim_atr 0.0`; `break_closes` and `minimum_inside_closes` are the builder's.
They are shared conventions (the mirror of the existing break rule), none chosen on a trade.

The replay certification (v2 against v3 on six production captures, the 12 October reconstruction, the entry and stop audit,
limits) is [range-edge-v3-certification.md](range-edge-v3-certification.md).

## Status and execution

- **Certification**: v3 is a technical contract rebuilt on the frozen detector, certified by replay (above); it no longer reproduces the frozen Python detector decision for decision, so it left the parity oracle (the v2 decisions remain reproducible as the frozen baseline in `test/rereplay`).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument. Enters only with the quote inside the
  card's entry zone (plus the contract tolerance): a quote past the edge waits for
  a retest and is never chased at the quote, so the order price is the price on the
  card (production 2026-10-08: card 4,125-4,127, order at 4124.19).
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).

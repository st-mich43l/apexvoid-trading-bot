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
   *outermost* broken edge of its side, unless
   - price **reclaimed** it: `reclaim_closes` consecutive closes back on the range side of the broken level less its
     tolerance (the mirror of `break_closes`), **and has not since accepted beyond it again**: the latest event decides
     (`techniquezone.BreakStatus`), and the bar of the latest accepted run is the break that dates the retirement and the
     identity; or
   - a **genuinely new range** has formed: the edge and the nearest floor (the live barrier on the other side nearest price,
     not the builder's selected one) are both made of contacts *after* the break, and price has closed inside them
     `minimum_inside_closes` times since. A stale floor, or price that never came back inside, is the old advance continuing.

   "Outermost" means no other cluster of the side, live or broken, that was already forming before price began accepting beyond
   the edge (its first touch is before the first close of the accepted run that broke it; a cluster whose first contact is that
   close was made by the break) lay beyond it by more than the tolerance and within the builder's maximum range width
   (`maximum_width_atr`): a cluster inside the range that price runs through on its way to the edge is not the range
   breaking, and a barrier from another range, further away than a range can be wide, is not the same range.
3. **Break pending** (`break_pending_unreclaimed`). The builder tolerates one close beyond the edge (`break_closes`
   is two). While price is still beyond the edge there is nothing to fade; a sweep that closes back inside is the strategy's own
   thesis and stays tradable. A later close beyond the edge after the confirming candle fails the reaction
   (`reaction_failed_after_confirmation`).
4. **Fresh rejection** (`rejection_not_back_inside`). The confirming candle must close back on the range side of the edge.
5. **Pressure** (`pressure_into_edge`). `pressure_bars` candles ending at the confirming one, each with a higher low and a
   higher close (mirrored for a support) and rising at least `pressure_minimum_rise_atr` in total, are price pressing the
   edge, not rejecting it; a confirming candle that closes lower breaks the run, and one that closes at least
   `rejection_close_strength` of its range away from the edge (a shooting star) is a rejection whatever the approach.
6. **Gaps.** Any missing candle inside the reaction or pressure window fails closed (`data_gap`): a gap is never mistaken
   for a fresh reaction.
7. **Identity.** The thesis is the range episode: `range:<side>:<first-touch time>` as before, and
   `range:<side>:break:<time of the earliest break the edge sits beyond>` for a range re-established after a break (the latest
   event of each broken cluster dates it). This makes the identity name the advance rather than the barrier the lookback
   happened to rebuild (one advance, one episode: a second, higher range after a second break shares the first break's identity
   while that break stays in the lookback, so a thesis-keyed guard can treat it as the same thesis), but it is **not stable**: the identity changes when the break that names it leaves the 48-bar lookback or
   another broken cluster starts or stops qualifying, so one range can still be published under more than one id (measured in
   the certification: the same-range repeats fall only from 30 to 28 on USDJPY). Do not rely on it to deduplicate; the
   Algo Bot's entry-corridor guard and the same-direction exposure policy (`docs/risk/same-direction-exposure-incident.md`) are
   the duplicate protection.
8. **Measured quality components** (no change to `Quality.Overall`): `rejection_close_location`, `reclaim_depth_atr`,
   `break_distance_atr`.

Lifecycle states, as an edge is tested: `range_valid` (intact) -> `break_pending` (closes beyond, not yet accepted) ->
`range_broken` (accepted, not reclaimed; retired). A reaction on a valid edge is the confirmed reaction. Requalification is
the reclaim or the re-established range above; there is no cooldown timer, but a break is remembered only while its cluster is in
the builder's lookback (48 bars), after which the market has had four hours to form a range of its own.

Coefficients (`config/analysis.yml` `strategies.range_edge`): `reclaim_closes 2`, `pressure_bars 4`,
`pressure_minimum_rise_atr 1.0`, `minimum_reclaim_atr 0.0`, `rejection_close_strength 0.6` (the shared convention); `break_closes`,
`minimum_inside_closes` and `maximum_width_atr` are the builder's.
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

# Execution

TradePlan V8 is the only execution contract. Algo Bot declares one complete,
versioned plan (exact entry instruction, absolute stop, absolute targets) and
cTrader Engine executes it without recomputing any of it. Autonomous setups and
owner-armed manual `/algo` signals travel the same path. When ownership or
exposure evidence is uncertain, execution fails closed.

## One closed bar, one decision cycle

For every closed bar of a live symbol, Algo Bot:

1. **Loads** the live Go opportunities of the symbol (adapted from
   `analysis.opportunity.v1`; terminal events withdraw them).
2. **Admits** each one: not already terminal or published, auto trading and the
   strategy enabled, instrument not observing the strategy or its structure timeframe (`observe_only_strategies`, `observe_only_structure_timeframes`; checked at match creation and again here),
   fresh and not expired, confluence at or above the global floor, quote and
   spread inside the entry contract. A failure records a route outcome with a
   reason code; terminal failures also retire the setup.
3. **Arbitrates** the admitted intents ([below](#same-thesis-arbitration)).
4. **Publishes** at most one TradePlan per symbol per cycle, trying the ranked
   winners in order and falling back only when a winner is terminally rejected.
5. The executor re-checks exposure and submits.

### Same-thesis arbitration

Strategies stay independent, so several can fire on one idea in the same cycle.
Intents on the same symbol and direction compete for one executable thesis when
they share a Go thesis group (`go_thesis_id`), the same structural id, or entry
zones that overlap after a one-ATR pad. One winner per thesis is published; the
rest are suppressed with `same_thesis_suppressed`.

The winner is chosen by a fixed hierarchy, with no strategy favouritism:

1. execution eligibility (the quote can enter the entry contract now);
2. strategy quality (Go `quality.overall`);
3. confluence;
4. structural/source quality (detector confluence raw score);
5. freshness of the confirmation;
6. intent id, as the deterministic tie-break.

Opposite directions never share a thesis. When BUY and SELL intents are both
executable, the direction whose best intent leads by `conflict_margin_quality`
wins; a closer call is held (`opposite_direction_conflict`) unless exactly one
direction agrees with the higher-timeframe bias.

### Atomic corridor reservation

The winner reserves its entry corridor in one Redis script
(`autotrade:entry_overlap:{SYMBOL}`) before anything is published, and holds it
for 45 minutes whether or not the position is still open. A later same-direction
intent whose corridor overlaps is rejected (`entry_zone_overlap_same_direction`),
which also closes the stop-out re-entry hole. The script reads, decides and
writes in one round trip, so two workers cannot both conclude they own a
corridor. If Redis cannot run it the intent waits (`entry_zone_reservation_unavailable`);
it never publishes unreserved.

### Ownership and duplicates

- `auto_trade:cycle_route_lock:{SYMBOL}:{cycle_id}` serializes evaluation of a
  cycle's ranked list; `auto_trade:route_lock:{SYMBOL}:{match_id}` stops two
  workers evaluating the same match. Both use random owner tokens, a finite TTL
  and compare-and-delete release.
- `auto_trade:cycle_owner:{SYMBOL}:{cycle_id}` records the winner, so a duplicate
  event delivery cannot replace it.
- `route_in_progress`, `cycle_conflict`, `duplicate_reaction`, `duplicate_thesis`
  and `publication_unavailable` preserve the preferred route and block lower
  intents; only an intent-specific `terminal_reject` permits fallback.
- A thesis claim allows one autonomous initial plan per thesis.
- The plan id is derived from the match id and `execution:plan_dedup:{plan_id}`
  tombstones publication, so a retried publish, a redelivered Kafka record, a
  consumer restart or a Redis reconnect cannot create a second plan. The Kafka
  consumer writes the match idempotently and commits only after the ledger write.
- The executor claims each plan once and persists state, so a process restart
  resumes work instead of resubmitting.

## Exposure

Opposite-direction exposure is decided per instrument from `instruments.yml`
(`exposure.opposite_position`), never by strategy or scalp status:

| Instrument | Rule |
|---|---|
| FX (EURUSD, GBPUSD, GBPJPY, USDJPY) | any opposite exposure blocks the plan at any distance (`fx_opposite_position_not_allowed`) |
| XAU | entry must be at least 150 pips (inclusive) from every opposite group (`xau_opposite_position_too_close`) |

With XAU `pip_size` 0.1, a BUY at 4300.0 blocks a SELL at 4314.9 and allows one at
4315.0. A missing or invalid policy fails closed: Algo Bot rejects with
`opposite_exposure_policy_unavailable` and the executor refuses the order.
Algo Bot (`evaluate_opposite_exposure`) and the executor (`OppositeExposureFence`,
run before the first broker mutation of a plan, against tracked plan state plus
real broker positions and pending orders) enforce the same rule independently.
Same-direction stacking is a separate rule: a non-scalp plan waits until every
live same-direction plan has booked TP2.

## Expiry and cancellation

A plan past its entry validity expires in the executor; one whose opportunity is
invalidated or expires gets a durable cancel intent from Algo Bot
(`execution:plan_cancel:{plan_id}`, a tombstone written even before the plan
exists). The executor honours either before submitting anything:

| Stage | Result |
|---|---|
| not yet submitted | never sent; plan cancelled |
| resting orders | every unfilled leg cancelled at the broker |
| partially filled | unfilled legs withdrawn; filled legs keep their stop and TP/BE management |
| open position | never closed by a cancel or an invalidation |

A plan with no exposure left retires. A cancel the broker answers with
`ORDER_NOT_FOUND` cannot succeed on retry, so the leg is resolved from the live
positions: a position carrying its client order id is adopted as a fill,
otherwise the leg is written off as already gone.

## Entry, stop and targets

- **XAU** scales in: the first leg (80% of the volume) enters at the proximal
  edge of the zone, either at market when the quote is inside the entry contract
  or as a resting limit, and the second leg (20%) rests one `scale_step_atr`
  (0.1 ATR) deeper, capped at the far edge. The structure stop is held to the
  50-60 pip envelope, targets are 1R/2R/3R/4R closing 40/20/20/20 with
  break-even after 1R, and unfilled legs are cancelled after TP. The optional
  XAU risk leg (0.02/0.05 lots by equity, 15 pips inside the stop) is declared
  by the planner in `entry.risk_leg` (the Auto Algo root card does not print it);
  the executor places exactly the legs the plan declares and never adds one
  (production 2026-10-08: an executor-injected leg the plan did not hold).
  `execution.reaction_risk_leg` holds its switch and sizing table, read by the
  planner; technical provenance never changes the leg set. Manual `/algo` keeps
  its own zone ladder (shallow at the near edge, deep at the midpoint).
  `contracts/autotrade/xau-ladder-spec.json` pins the manual entry prices and
  the risk leg in both languages.
- **The card price is the order price.** Every price on a card (entry zone,
  stop, targets, risk leg) is what the plan holds and the executor places. Range
  Edge and Breakout Retest therefore enter only with the quote inside the card's
  zone (plus the contract tolerance); past the edge they wait for a retest and
  never chase at the quote. Range Sweep and Impulse Pullback keep their bounded
  momentum chase.
- **FX** takes one precise entry per plan with 1R/2R targets closing 50/50 and
  break-even after 1R; the stop envelope is per pair (EURUSD 12-20 pips,
  GBPJPY 22-35, USDJPY 18-28).
- **Scalps** (`range_sweep`, `scalp_breakout_retest`) keep their own M1 stop
  book and ladder; they are XAU-only.

## Operator controls

`/trade_close`, `/trade_close_auto`, `/trade_sl`, `/auto_close_all` and
`/trade_cancel` act on the plan runtime: closes and stop moves go through the
executor, cancels through the cancel intent. The owner pause
(`auto_trade:paused`) holds new submissions.

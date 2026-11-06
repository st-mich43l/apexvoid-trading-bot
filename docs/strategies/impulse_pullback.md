# Impulse Pullback Strategy V4


**Contract.** Ported from the Python scalp lane's `discover_impulse_pullback`: 4 ATR displacement with 0.5 body dominance, a corrective pullback (below 0.7 of the impulse body, 25-75% retracement, confirmed extreme), an unmitigated M5 zone or nearby key level whose closed-bar role agrees, location in the dealing range, an M1 confirmation at the reference, the structural stop and the corridor target. XAU only. The real XAU capture contains no Python decision, so parity is proven on seeded synthetic captures only; XAU stays observe-only.

**v4 (5 Nov 2026): the stop sits outside the M5 noise.** v3 measured on real M1 (below): every opportunity filled and was stopped within 3-13 minutes, because the stop was `ref.Level -/+ buffer` with the M1-ATR buffer (`max(1.2 x M1 ATR, 1.5 x maximum spread)`, about 7-15 pips), a few M1 ATR behind the reference and inside the M5 noise (XAU M5 ATR 15-40 pips); when the pullback had already traded beyond the reference it could sit inside the pullback's own wick. v4 follows the rule "structure sets the level, noise sets the buffer; never compress the stop into the noise":

- **Level:** the deeper of the reference and the pullback's own extreme (`min(ref.Level, PullbackExtreme)` for a BUY, `max` for a SELL). The extreme is already confirmed (`pullback_extreme_confirm_bars`), so nothing after the decision bar is read.
- **Buffer:** `max(buffer_m5_atr_multiple x the structure (M5) ATR, buffer_minimum_spread_multiple x maximum_spread_pips x pip)`, with `buffer_m5_atr_multiple: 0.5`. The M1-ATR buffer (`buffer_m1_atr_multiple`) still sizes the entry band around a level reference and no longer sets the stop.
- **Bounds:** `auto_algo.strategies.scalping.stop` is now 30 to 70 pips (was 12 to 45; 70 is the XAU instrument cap used everywhere else). A stop wider than 70 is refused, never tightened; a narrower one is widened to 30.
- **Target and detection unchanged:** the `reward_risk_ladder` rung that clears `minimum_net_target_pips` and fits 0.9 of the corridor room. With wider stops fewer setups fit their room, which is intended: a setup whose room cannot pay 1R on an honest stop is refused. Invalidation label `impulse_reference_lost`, the 4 ATR displacement, the 15-minute expiry, the 80-pip scalp opposite separation and `scalp_outcome` are unchanged.
- **Downstream:** the Algo Bot takes the M1 scalp stop verbatim from the invalidation price (`plan_scalp_invalidation_stop`, no clamp) and the Go stop envelope (kind `stopEnvelopeM1Scalp`) reads the same `scalping.stop` leaves, so 30/70 flows through; the only hard-coded scalp caps were the fallback defaults in `protective_stop.py` (12/45, now 30/70). The cTrader engine has no scalp stop cap.
- **Judging it:** the daily M1 export in [operations](../operations.md#m1-capture-trail) keeps the M1 history Redis drops after about 33 hours.

`impulse_pullback` qualifies a directional M5 impulse, then requires an M1
correction within configured retracement bounds and a continuation close.
Entry is the continuation band, invalidation is behind the pullback extreme and
target is configured R. Identity anchors the M5 impulse. Too-small impulses,
shallow/deep corrections and absent continuation reject. It is enabled in the
live Go opportunity stream; Algo Bot applies execution policy before any
TradePlan is published.

## Status and execution

- **Certification**: parity proven on synthetic M1 captures only ([catalog](README.md#catalog)).
- **Symbols**: XAU.
- **Execution**: live on XAU since the owner rule of 2026-10-14 (no observe-only on any instrument). Before that it was observe-only: net -49 pips over 11 live trades of the earlier simplified thesis.
- **Confluence floor**: not applied. The global `min_confluence` is the zone-reaction rubric (touches, wick rejection, displacement grade, session); this strategy has no zone reaction and always scored 1 star (all 7 opportunities since 28 Oct), so the floor refused every one at admission. It is judged by its own detector, M1 confirmation, location and stop book. A terminal admission refusal now ends as `scalp_outcome ... terminal=admission_rejected:<code>` (it used to be recorded as `waiting_retest_expired` at expiry).
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by execution eligibility, then freshness, then id trades
  (no quality score is part of the order) and the rest are suppressed ([execution](../execution.md#same-thesis-arbitration)).

## Exposure lane

Impulse Pullback Scalp is the only strategy whose catalog profile carries `m1_scalp`, so it is the only one in the
`scalp` exposure lane ([execution](../execution.md#exposure)): on XAU it may open against an opposite group at 80 pips
(`exposure.opposite_position.scalp_minimum_separation_pips`, owner decision 2026-11-03) instead of 150, FX still never
allows an opposite position, and the opposing-structure (zone book) gates are skipped for it. The same-direction rule is unchanged.

## Terminal outcome of every opportunity

Every Go opportunity of the scalp lane ends in exactly one outcome, written once as
`scalp_outcome strategy=… opportunity_id=… terminal=… reason=… elapsed_s=…` and counted at Redis
`scalp:metric:outcome:{strategy}:{terminal}`: `plan_published`, `admission_rejected:<reason_code>`, `waiting_retest_expired`,
`invalidated_before_entry:<reason>`, `activation_rejected:<reason_code>`, `stale_on_arrival:<verdict>`, `adapter_rejected:<reason>`
(`app/autotrade/scalp_outcome.py`). The first outcome wins; the one supersession is `plan_published` followed by an executor
rejection (`activation_rejected`). Before this, ten production opportunities (7 invalidated by `IMPULSE_REFERENCE_LOST`, 3
expired) left no order attempt and no Algo Bot log line.

## Measurement on real M1 (publish counts and simulated outcomes, not P&L)

`analysis-engine/test/ipreplay` replays the real XAU captures that carry M1 bars (`replay-xau-m1-production-capture-20261006`,
`replay-xau-production-capture-20261014`, and the fresh `scalpwindow-xau-20261103`: M1 30 Oct 18:32 to 3 Nov 06:56 UTC, i.e. the
sessions after the 25 Oct London and 1 Nov New York clock changes) and simulates a resting limit at the proximal edge of each entry
band on the following M1 bars: a fill needs the ask to reach the limit (3-pip spread charged), the stop is the invalidation price
(a bid or ask trade-through), the ladder is the adapter's (1R and 2R, or 1R only when the target offers less than 2R), the stop is
taken first when one bar spans it and a target, and a fill is followed for 4 hours. Nothing here is an edge claim.

| Variant | Opportunities | Filled | Stopped | Reached 1R (not 2R) | Reached 2R | Invalidated before a fill | Expired unfilled |
|---|---|---|---|---|---|---|---|
| as configured (expiry 15 min, displacement 4.0 ATR) | 5 | 5 | 5 | 0 | 0 | 0 | 0 |
| expiry 60 min, displacement 4.0 | 5 | 5 | 5 | 0 | 0 | 0 | 0 |
| expiry 120 min, displacement 4.0 | 5 | 5 | 5 | 0 | 0 | 0 | 0 |
| expiry 15 min, displacement 3.0 | 8 | 8 | 7 | 1 | 0 | 0 | 0 |
| expiry 60 min, displacement 3.0 | 8 | 8 | 7 | 1 | 0 | 0 | 0 |
| expiry 120 min, displacement 3.0 | 8 | 8 | 7 | 1 | 0 | 0 | 0 |

Reading it:

- **The replay reproduces production.** The four 2 November opportunities of `scalpwindow-xau-20261103` carry the same ids as the
  production ones (`opp_981c63fb`, `opp_dcb0f136`, `opp_e9584b65`, `opp_8b0d91f4`), and the simulated stop times (07:33, 07:33,
  13:01, 13:01 UTC) are the times production invalidated them with `IMPULSE_REFERENCE_LOST`. Those opportunities were stop-outs, not
  missed trades: the quote was already in the band on the next bar, a resting limit filled at once, and price went through the stop
  within 3 to 13 minutes.
- **The expiry does not matter.** Every fill happens on the first or second bar and every outcome within 13 minutes, so 15, 60 and
  120 minutes produce identical rows.
- **Lowering the displacement to 3 ATR adds three opportunities, not edge.** Of the 8 distinct setups at 3 ATR (the 5 of the configured setting plus 3), 7 are stopped and 1 reaches 1R.
- Small samples (5 to 8 setups over about 90 hours of M1). They say the strategy rarely publishes and that what it publishes
  fails quickly in this window; they cannot say it has no edge. The configuration (`analysis.yml` `impulse_pullback`) is unchanged;
  the owner decides from this table.

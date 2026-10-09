# Scalp sizing and shared enablement audit

Date 2026-10-09, master with #760 and #761. Read-only audit plus one alignment fix.

## Sizing

Volume is not derived from the stop. Every Go plan is sized `equity_table`: the owner table
(`VolumePlanner.LotsForEquity`) gives the lots for the account equity, and a scalp books the
configured `auto_algo.risk.sizing.range_max_risk_multiplier` (1.5x) on top
(`risk_multiplier_for_tier(range_scalp=True)`; the executor re-applies it,
`EquityTableLots`). The stop only decides the dollar risk. Production equity 2026-10-09 was
$2,591, table 0.15 lots, scalp 0.23 lots, gold about $10 per pip per lot:

| Trade | Lots | Stop | Risk | % of equity |
|---|---|---|---|---|
| Range Edge Scalp before #760 | 0.23 | 50 | $115 | 4.4% |
| Range Edge Scalp, Go invalidation (23 pip) | 0.23 | 23 | $53 | 2.0% |
| Scalp at the new 45 pip cap | 0.23 | 45 | $104 | 4.0% |
| Scalp at the 15 pip floor | 0.23 | 15 | $35 | 1.3% |
| Structural gold, 50 / 70 pip | 0.15 | 50 / 70 | $75 / $105 | 2.9% / 4.1% |

Findings:

1. **Fixed (this PR).** The plan builder decided "scalp plan" from `family == "scalp"` or
   `mode == "scalp_m1"`. All five Go scalps carry family `range_reversion` and a `go_m5_*` mode,
   so none was a scalp plan: they got `risk_percent` 1.0 instead of the scalp 0.5, and the
   configured scalp `sizing_mode` could never reach them. The live config says `equity_table`
   for both, so lots did not change, but a switch to `risk` sizing would have skipped every
   Go scalp. It now uses the taxonomy (`is_scalp_strategy`), like every other scalp gate.
2. **Open, owner decision.** `risk.max_group_risk_percent` (2.0) is written into every plan
   and enforced nowhere (Python builder and `TradePlan.cs` only carry it). A scalp at the 45 pip
   cap risks 4.0% of equity, more than a structural 50 pip trade (2.9%), because the 1.5x boost
   was set when scalp stops were small. Options: size scalps by risk (`sizing_mode: risk`, now
   reachable), scale the boost down with the stop, or lower the XAU scalp cap. No change made.
3. `DegradeScalpLadderForMinVolume` (C#) keys on family `scalp` and `risk` sizing, so it never
   applies to Go scalps. Inert today (`equity_table`); noted for the day sizing mode changes.

## Shared enablement

Every one of the 21 reviewed strategies resolves to a profile and an enable switch (all true).
Two switches are shared:

| Switch | Strategies |
|---|---|
| `auto_algo.strategies.scalping.mode == live` | impulse_pullback, range_sweep, scalp_breakout_retest |
| `auto_algo.strategies.technique.sd.enabled` | supply, demand |

These are lane master switches, not per-strategy controls. Independent control exists below
them: Go has `analysis.strategies.<id>.enabled` per strategy, and per-instrument
`execution.go_opportunity.observe_only_strategies` switches any one strategy to observe-only
(it is how impulse_pullback is off on XAU and EURUSD). `range_reversion.enabled` (shared with the
retired Range Box / One-Sided / Chop) is read by no Go strategy; Range Edge uses its own
`range_edge.enabled`, Fade Scalp `scalp.fade_scalp_enabled`. A test now pins the shared set.

Not scalps by the catalog (so not covered above): Snap-Back and Liquidity Sweep.

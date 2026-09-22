# ADR-005: Canonical `SymbolState` — one analytical truth per symbol

## Status
Accepted (2026-09-22).

## Context
The Go migration audit (§2.2, §2.3, §2.4, §2.5) found the same technical
computation (ATR, swings, zones) independently re-derived up to four times
for the same symbol/timeframe/moment across the current Python codebase:
`_analyze_tf`'s canonical pipeline, `detectors.build_context()`'s
`indicator_sets` (different ATR formula), `structure.py`'s wrapper
functions (hardcoded ATR length), `app/scalping/context.py`'s own swing
algorithm, and `worker.py`'s three independent HTF-zone recomputes. These
aren't just wasted CPU — §2.1 measured the two ATR formulas 6.7% apart on
real data, meaning two code paths computing "the same" zone geometry for
the same candle window can legitimately disagree.

## Decision
`analysis-engine` maintains exactly one `SymbolState` per symbol — the
one analytical truth for that symbol:

```go
type SymbolState struct {
    Symbol        market.Symbol
    Structure     *structure.Book
    Liquidity     *liquidity.Book
    Zones         *zone.Book
    Context       context.MarketContext
    Opportunities *opportunity.Book
}
```

Every strategy reads `SymbolState`/`MarketContext`. **No strategy
recomputes ATR, swings, or zones itself** (§28's explicit forbidden
example: `BreakoutRetest → recalculate ATR → recalculate swings → rebuild
zones`). A strategy may derive strategy-specific values from what it
reads, but never re-derive a value `SymbolState` already owns.

## Consequences
- The ATR-canonicalization decision (V1 in `service-boundaries.md`) is now
  a **prerequisite**, not a nice-to-have — `SymbolState.Context` can only
  hold one ATR series, so `analysis-engine`'s Go port cannot proceed past
  the current Stage 1/2 boundary without the owner picking simple-mean or
  Wilder-RMA as canonical.
- `worker.py`'s three independent HTF-zone recomputes (V5) become
  collapsible once `SymbolState` exists and is reachable from the
  autotrade path — explicitly called out as a Stage 5+ item, not fixed by
  this ADR.
- `AnalysisSnapshot` (the network-safe projection of `SymbolState` +
  current opportunities, per §37) is what leaves the service boundary;
  `SymbolState` itself is never exposed as a wire contract.

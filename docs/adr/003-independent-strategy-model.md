# ADR-003: Independent strategy model (and the opportunity/strategy ordering fix)

## Status
Accepted (2026-09-22).

## Context
The legacy Python system organizes detectors into broad generic families
(`reaction/{supply,demand,key_level,trendline,liquidity}`,
`strategy_taxonomy.py`'s frozensets, `execution_policy._STRATEGY_FAMILY`)
that share entry/confirmation/invalidation/quality/targeting/expiry logic
across genuinely different market theses. This produced real, live bugs
this session alone: a Supply/Demand zone's hold-based validity, an M5
confirmation-stamp format, and an opposing-barrier liveness check each had
to be fixed once and then found to silently affect every other family
sharing the same generic path, because the families weren't actually
independent — they were one mechanism wearing different labels.

Separately, the source architecture task's own text contains an internal
inconsistency: §51 orders `strategy` before `opportunity` in its
dependency chain (implying `strategy` must not import `opportunity`), but
§21's own `Strategy` interface snippet has `Evaluate` return
`[]opportunity.Candidate`, which requires `strategy` to import
`opportunity`. See `dependency-rules.md` for the full text of both
sections.

## Decision
1. **Reject the generic `reaction` family model.** Every strategy under
   `internal/strategy/<name>/` owns its full thesis: market condition,
   setup thesis, required structure, required location, trigger,
   confirmation, entry geometry, technical invalidation, technical target
   thesis, expiry, and strategy-specific quality model (§22). The only
   shared contract is the `Strategy` interface — an engineering contract
   (how the engine calls a strategy), never shared trading behavior (how
   the strategy decides).
2. **`internal/confluence` is the sole, documented compositional
   exception** — it may combine independent evidence (demand + OB + FVG +
   Fib + liquidity + flip zone) from multiple strategies' outputs. No
   other strategy inherits from a generic confluence base.
3. **`StrategyQuality` is per-strategy, not universal**: `Overall float64`
   plus a `map[string]float64` of strategy-specific named components
   (e.g. breakout-retest's `breakout_quality`/`retest_quality`/
   `structure_quality`/`location_quality`; a liquidity sweep's completely
   different set). No forced universal scoring formula.
4. **Ordering fix**: `internal/opportunity` holds pure, behavior-free
   result types (`Candidate`, `Evidence`, `Target`, `StrategyQuality`,
   `StrategyID`) with zero dependency on `strategy`, and sits below
   `strategy` in the dependency graph. `strategy` is the only side that
   imports the other, matching the literal `Strategy.Evaluate` signature
   the source task itself specifies. Full text: `dependency-rules.md`.

## Consequences
- Strategy package slots (`breakoutretest`, `liquiditysweep`, `orderblock`,
  `fvg`, `keylevel`, `supply`, `demand`, `trendline`, `sessionlevel`,
  `rangeedge`, `boxbreakout`, `impulsepullback`, `rangesweep`,
  `snapback`) are architectural names from the source task, not
  directories this task creates — only strategies that survive the
  upcoming market-structure specification get a real package (see
  `analysis-engine.md`).
- Porting `detectors.py`/`technique_detectors.py` is explicitly a
  **rewrite**, not a translation, per the migration map — the generic
  family structure they're built on is exactly what's being rejected.
- A future strategy needing a genuinely new evidence type or quality
  dimension never requires touching the shared `Strategy` interface or
  any other strategy's code.

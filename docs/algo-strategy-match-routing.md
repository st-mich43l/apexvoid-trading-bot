# Algo Strategy-Match Routing

## Objective

Let ApexVoid Algo execute scanner price-action strategies as typed decisions.
A detector match is the strategy decision; `Range Edge Scalp` is one strategy,
not a market regime and not a confirmation gate.

## Data flow

```text
closed M5 bar -> scanner detectors -> versioned StrategyMatch in Redis
closed M1 bar -> Algo builds structural Market Map from M5/M15/M30
              -> mapped zone touch + M1 rejection -> StrategyMatch
either match
  -> Algo worker checks execution safety only
  -> auto_trade:candidates
  -> cTrader executor
```

No rendered Telegram message is an input. Telegram delivery, notification
deduplication, and Market Map rendering do not control execution.

## Strategy selection

- Scanner detectors decide whether their own structure is present.
- `Mapped Zone Reaction` is a separate M1 execution strategy. It reads the
  structural Market Map model, requires a usable FVG/OB/supply/demand band and
  an M1 touch/rejection, and ignores display-only round-number fallback levels.
  A mapped band must also satisfy
  `max(AUTO_TRADE_MAP_ZONE_MIN_WIDTH_ATR × M1 ATR,
  AUTO_TRADE_MAP_ZONE_MIN_WIDTH_ABS)` before it can become a target.
- HTF-aligned and counter-bias mapped reactions are both enabled. Counter-bias
  mean reversion is a distinct quality path: the zone must be fresh, meet the score
  floor, and carry enough structural tags (or a nearby same-side trendline
  level). It does not use the display tier as a quality proxy, still requires
  M1 rejection, and ends at box EQ rather than the opposite range boundary.
- The digest ranks matches by confluence, zone score, then distance.
- The worker does not require another M1 rejection, M5 hold, Market Map rail,
  or `chop`/`trend`/`breakout` label.
- A scanner match has priority over the private OHLC strategies for that tick.
- A scanner match has priority over a simultaneous M1 mapped-zone match.
- If private strategies overlap, the higher-confluence match wins. Regime
  classification remains observable telemetry and may inform a detector, but
  it is not a global veto.

`Range Edge Scalp` keeps its own range-specific execution plan: BUY at the
matched lower edge or SELL at the matched upper edge, then close the full
position at the largest configured adaptive target
(`AUTO_TRADE_RANGE_TARGETS_PIPS`, default `20,30,40,50,70`) that fits the
available room. Other strategies use the configured target ladder.

Matches carry an execution `tier` (`A`/`B`/`C`) and `risk_multiplier`. Tier B
raises frequency at reduced risk; Tier C is analysis-only. Multiple typed
matches are stored at `auto_trade:strategy_matches:{symbol}` while the primary
legacy key remains `auto_trade:strategy_match:{symbol}`.

## Execution safety

After a strategy matches, only execution invariants remain: enabled/paused
state, typed-contract validity, match age, symbol and confluence, fresh quote,
spread, **strategy-aware** entry drift, guarded news, structure stop bounds,
opposing-zone stop safety, account authorization, idempotency, and exposure
rules. These checks can refuse an unsafe order but cannot reinterpret the PA
setup. Zone-fill geometry that places the proximal edge on the wrong limit
side falls back to single-entry instead of hard-rejecting.

## Redis contract

```text
SETEX auto_trade:strategy_match:{SYMBOL} <ttl> <StrategyMatch JSON>
SETEX auto_trade:strategy_matches:{SYMBOL} <ttl> <StrategyMatch JSON array>
```

The payload includes a stable `match_id`, source timeframe/event, detector
strategy and mode, direction, entry/key level, ATR, structure swing, target
plan, confluence, reasons, `tier`, `risk_multiplier`, and optional `family` /
`range_state`. Optional range fields are valid only for a range-edge strategy.
Malformed, expired, or symbol-mismatched data fails closed.

Every Market Map evaluation also replaces this one-hour diagnostic snapshot:

```text
SETEX auto_trade:map_strategy:actionable:{SYMBOL} 3600 <JSON array>
```

Each item contains the side, raw `lo`/`hi`, tier, score, and `contains_price`.
`/auto_status` shows total entries seen, survivors, the three nearest entries,
and per-rule side/actionability/width/distance counts. The last owner-rendered
map is cached separately so a reason can explicitly flag a strategy/display
divergence instead of citing a band the owner cannot see.

## Controls and telemetry

```text
AUTO_TRADE_STRATEGY_MATCH_ENABLED=true
AUTO_TRADE_STRATEGY_MATCH_MAX_AGE_SECONDS=420
AUTO_TRADE_MAP_ZONE_MIN_WIDTH_ATR=0.15
AUTO_TRADE_MAP_ZONE_MIN_WIDTH_ABS=1.0
AUTO_TRADE_MAP_COUNTER_BIAS_ENABLED=true
AUTO_TRADE_MAP_COUNTER_BIAS_MIN_SCORE=6.0
AUTO_TRADE_MAP_COUNTER_BIAS_MIN_CONFLUENCE=2
```

Legacy `AUTO_TRADE_STRATEGY_BRIDGE_ENABLED`,
`AUTO_TRADE_FORMING_GATE_ENABLED`, and
`AUTO_TRADE_FORMING_MAX_AGE_SECONDS` names remain read aliases during rollout.
`/auto_status` and `auto_trade:last_gate*` expose the active strategy name,
direction, source timeframe, source event, reasons, and candidate ID.

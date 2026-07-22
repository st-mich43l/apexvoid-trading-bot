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
  same structural Market Map model, requires an HTF-aligned FVG/OB/supply/
  demand zone and an M1 touch/rejection, and ignores display-only round-number
  fallback levels.
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
position at +50 or +70 pips while the detector continues to validate the box.
Other strategies use the configured target ladder.

## Execution safety

After a strategy matches, only execution invariants remain: enabled/paused
state, typed-contract validity, match age, symbol and confluence, fresh quote,
spread, entry drift, guarded news, structure stop bounds, opposing-zone stop
safety, account authorization, idempotency, and exposure rules. These checks
can refuse an unsafe order but cannot reinterpret the PA setup.

## Redis contract

```text
SETEX auto_trade:strategy_match:{SYMBOL} <ttl> <StrategyMatch JSON>
```

The payload includes a stable `match_id`, source timeframe/event, detector
strategy and mode, direction, entry/key level, ATR, structure swing, target
plan, confluence, and reasons. Optional range fields are valid only for a
range-edge strategy. Malformed, expired, or symbol-mismatched data fails closed.

## Controls and telemetry

```text
AUTO_TRADE_STRATEGY_BRIDGE_ENABLED=true
AUTO_TRADE_STRATEGY_MATCH_MAX_AGE_SECONDS=420
```

Legacy `AUTO_TRADE_FORMING_GATE_ENABLED` and
`AUTO_TRADE_FORMING_MAX_AGE_SECONDS` names remain read aliases during rollout.
`/auto_status` and `auto_trade:last_gate*` expose the active strategy name,
direction, source timeframe, source event, reasons, and candidate ID.

# Session Level

**Contract.** Restored to the frozen `session_level_reaction`: the frame's session levels (frozen `session_liquidity` rules), nearest to price first, a reaction off the level's reaction band, a swept level valid only with a reclaim-type confirmation, the shared entry-validity and confluence floor, and the best-scored qualifying level. Only confirmed reactions are emitted. Proven on 316 oracle decisions (`test/legacyparity`). XAU stays observe-only.

## Strategy ID / version

`session_level`, `v2`. Implemented in
`analysis-engine/internal/strategy/sessionlevel`. Algorithm versions:
`structure=v2`, `liquidity=v1`, `zone=v1` (unused directly, carried for
provenance-contract parity), `config=3`.

## Purpose / thesis

An unswept session extreme, close enough to current price to matter, is a
standing reaction level — a HIGH level (`_H` suffix, or `PDH`/`PWH`)
implies resistance (sell reaction); a LOW level (`_L` suffix, or
`PDL`/`PWL`) implies support (buy reaction). `internal/session` already
decided what the level IS (Asia/London/NY highs-lows, PDH/PDL, PWH/PWL)
and whether it has been swept; this strategy decides tradeability of a
not-yet-swept extreme only.

## Supported instruments / timeframes

Any symbol; single timeframe `M5`.

## Required regime / structure / liquidity / location

- `level.Swept == false`.
- `levelDirection(level.Name)` recognizes the name (fail-closed — an
  unrecognized name is skipped, never guessed at).
- `|currentPrice - level.Price| / ATR <= proximity_atr`.
- An opposing-side liquidity pool exists at the required distance.
- `ctx.Volatility.ATR > 0` and a current-price proxy is derivable (same
  Micro-layer-swing proxy as `key_level.md` — see that doc for the full
  reasoning; the same `MarketContext` limitation applies here).

## Formation / trigger / confirmation

Owned by `internal/session`. `levelDirection` reads direction straight
off the level's own canonical name suffix.

## Entry / invalidation / target

- **Entry**: `levelPrice ± 0.05*ATR` (a session extreme has no primitive
  band the way a key level's `Band` does, so this strategy defines a
  narrow ATR-relative entry window of its own).
- **Invalidation**: HIGH level → `levelPrice + invalidation_buffer_atr*ATR`;
  LOW level → `levelPrice - invalidation_buffer_atr*ATR`.
- **Target**: nearest opposing-side pool at least
  `minimum_target_distance_atr*ATR` from the level.

## Expiry

`expiry_hours` after the most recent Micro-layer swing time used for the
price proxy.

## Failure cases / anti-patterns

Already-swept levels, unrecognized level names, too far from current
price, no structural swing yet, no opposing liquidity, zero ATR.

## Quality components / evidence codes

`proximity_quality` only — a single, honestly-scoped dimension. An
unswept session level carries no touch/strength score the way key-level
clustering does, so this strategy does not fabricate additional
dimensions with no real signal behind them (unlike every other strategy
in this slice, which has at least two independent quality components).

Evidence codes: `session_level_<lowercase name>` (e.g. `session_level_asia_h`),
`session_level_unswept`.

## Bullish / bearish examples

`ASIA_H` unswept, current price just below it → Sell. `PDL` unswept,
current price just above it → Buy.

## Valid / invalid examples

Valid: as above. Invalid: `Swept=true`, an unrecognized name (e.g. a
future session-window name this strategy doesn't yet parse — fails
closed rather than guessing a direction), or too far from current price
(`test/strategy/sessionlevel/sessionlevel_test.go`).

## Status and execution

- **Certification**: `GO_NATIVE_VALIDATED` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Not a port of the Python `session_level_reaction`: it matches the oracle's bar on 227 of 316 decisions and emits about six times as many confirmed bar-states. Trades on FX (EURUSD +48, GBPJPY +67, USDJPY -17 pips live). On XAU it is observe-only after 0 of 5 live trades won (-204 pips, all in Asia). Session here is the thesis level, not a session filter.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).

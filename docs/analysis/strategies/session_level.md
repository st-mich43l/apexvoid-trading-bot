# Session Level (Phase S7)

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

## Known old-engine false positive

`internal/strategy/sessionlevel`'s own `levelDirection` had a real bug
caught by this phase's own test-writing (not by manual review): its
second return value — meant to distinguish "HIGH level" from "LOW
level" for picking the invalidation side and liquidity-pool side — was
hardcoded `true` in **both** matched cases, so every unswept LOW level
(`PDL`/`PWL`/`_L`-suffixed) was silently treated as a HIGH level (wrong
invalidation direction, wrong liquidity side searched, meaning a real
support level would have produced a search for sell-side liquidity
instead of buy-side, ultimately never producing a candidate for that side
at all). Fixed in the same PR that added
`TestSessionLevel_UnsweptLowLevelProducesABuyCandidate`, which failed
against the original code and passes against the fix.

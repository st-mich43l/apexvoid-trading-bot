# Key Level (Phase S7)

## Strategy ID / version

`key_level`, `v2`. Implemented in
`analysis-engine/internal/strategy/keylevel`. Algorithm versions:
`structure=v2`, `liquidity=v1`, `zone=v1` (unused directly — carried for
provenance-contract parity with the zone-anchored strategies), `config=3`.

## Purpose / thesis

A sufficiently touched, sufficiently strong key level, close enough to
current price to matter, is a standing reaction zone — support (buy) if
price sits above it, resistance (sell) if below.

## Supported instruments / timeframes

Any symbol; single timeframe `M5`.

## Required regime / structure / liquidity / location

- `level.Touches >= minimum_touches` and `level.Strength >= minimum_strength`.
- `|currentPrice - level.Price| / ATR <= proximity_atr`.
- An opposing-side liquidity pool exists at the required distance.
- `ctx.Volatility.ATR > 0` and a "current price" proxy is derivable (see
  next section).

## Formation / trigger / confirmation

`internal/keylevel` owns level clustering (price-clustered swings +
round-number levels + wick-touch enrichment). Unlike `internal/zone`,
`keylevel.Level` carries no `Relevance` field, and `MarketContext`
exposes no raw candle/current-price feed a strategy could read directly —
only already-computed causal facts. This strategy therefore derives a
**"current price" proxy** from the primary timeframe's own most recently
formed Micro-layer swing
(`structure.LayerState.LastHigh`/`LastLow`, whichever is more recent) —
the smallest, closest-to-price structural fact already available, rather
than re-deriving price from raw OHLC this package cannot see. This is a
real, documented, non-obvious limitation of the current primitive set,
not a shortcut: `internal/keylevel.Role` (the primitive's own live
support/resistance/broken classifier) needs a real closes series this
strategy also cannot supply, so it is deliberately not used here either.

## Entry / invalidation / target

- **Entry**: `levelPrice ± level.Band`.
- **Invalidation**: support (`currentPrice >= levelPrice`, Buy) →
  `levelPrice - level.Band - invalidation_buffer_atr*ATR`; resistance
  (Sell) → `levelPrice + level.Band + invalidation_buffer_atr*ATR`.
- **Target**: nearest opposing-side pool at least
  `minimum_target_distance_atr*ATR` from the entry band's far edge.

## Expiry

`expiry_hours` after the most recent Micro-layer swing time used for the
price proxy.

## Failure cases / anti-patterns

Insufficient touches, below-threshold strength, too far from current
price, no Micro-layer swing yet (a genuinely fresh symbol — "insufficient
history"), no opposing liquidity, zero ATR.

## Quality components / evidence codes

`proximity_quality` (`1 - distanceATR/proximityLimitATR`), `touch_quality`
(`Touches/5`, capped at 1), `strength_quality` (`level.Strength`) — its
own independent dimension set (proximity has no equivalent in the
zone-anchored strategies, since `keylevel.Level` has no `Relevance`).

Evidence codes: `m5_key_level_<kind>` (`reaction`/`round`),
`m5_key_level_touches_sufficient`.

## Bullish / bearish examples

Price above the level → Buy (support). Price below → Sell (resistance).
Example: level at 2020, current price 2021, `Touches=3`, `Strength=0.8`,
ATR=1.0 → Buy candidate, entry 2019.5–2020.5.

## Valid / invalid examples

Valid: as above. Invalid: `Touches` below the configured floor, price >
`proximity_atr` away, or no structural swing yet to derive a price proxy
from (`test/strategy/keylevel/keylevel_test.go`).

## Known old-engine false positive

None specifically flagged for the reaction-to-key-level thesis itself in
the catalog (row 1) beyond the general primitive/strategy split — the
level clustering (`levels.py`) was `TECHNIQUE_ONLY` even in the legacy
audit; this strategy is a genuinely new, explicit tradeability layer on
top of it.

## Real bug found via Phase S8 replay (dedup identity)

`internal/keylevel` re-clusters from the current swing window on every
closed bar, so `levelPrice` can differ by a tiny amount between two
evaluations of what is really the same ongoing level. The original
`setupKey` used `levelPrice` at raw 6-decimal precision, so that jitter
produced a new `SetupKey` (and therefore a new deterministic opportunity
ID) almost every evaluation — running against real XAU M5 data
(`cmd/replay`) surfaced 147 "live" `key_level` opportunities across a
300-bar/25-hour window for what was really a much smaller number of
distinct levels.

S11 removed that self-referential bucket. A reaction level now inherits a
stable ID from its earliest canonical structural swing; a round level is
anchored to its configured round price. Clustering, wick enrichment and small
band/price changes preserve that ID. Direction remains part of the opportunity
identity, so a genuine support/resistance role transition is still distinct.

The same real 300-bar XAU M5 replay now discovers 18 `key_level`
opportunities rather than the prior 147, while deterministic variation tests
prove that ATR/band jitter preserves identity and distinct anchors remain
distinct. This is structural identity repair, not an output-count cap.

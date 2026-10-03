# Analysis Engine V2 strategy catalog

This is the canonical list of automatic technical theses. Each entry has one
Go package, one versioned configuration entry in `config/analysis.yml`, and a
strategy-specific specification under [`strategies/`](strategies/). All 20
entries are enabled in the live Go opportunity stream as of 2026-10-01.

The Analysis Engine publishes technical opportunities only. Algo Bot remains
responsible for freshness, quote/spread checks, account and exposure policy,
risk allocation, TradePlan construction, Telegram, and broker routing. No
strategy in this catalog may import another strategy; `confluence_zone` is the
single approved exception and consumes only canonical zone facts.

| ID | Go package | Primary inputs | Specification |
|---|---|---|---|
| `key_level` | `strategy/keylevel` | M5 key-level clusters, structure, reaction, liquidity | [key level](strategies/key_level.md) |
| `confluence_zone` | `strategy/confluencezone` | M5 canonical zone overlap, reaction, liquidity | [confluence](strategies/confluence_zone.md) |
| `supply` | `strategy/supply` | canonical supply zones, lifecycle/relevance, liquidity | [supply](strategies/supply.md) |
| `demand` | `strategy/demand` | canonical demand zones, lifecycle/relevance, liquidity | [demand](strategies/demand.md) |
| `order_block` | `strategy/orderblock` | canonical order-block zones, lifecycle/relevance, liquidity | [order block](strategies/order_block.md) |
| `fvg` | `strategy/fvg` | canonical FVG zones, lifecycle/relevance, reaction, liquidity | [FVG](strategies/fvg.md) |
| `ifvg` | `strategy/ifvg` | canonical inverted FVG zones, lifecycle/relevance, reaction, liquidity | [iFVG](strategies/ifvg.md) |
| `crt` | `strategy/crt` | closed H1 range and M5 sweep/reclaim | [CRT](strategies/crt.md) |
| `flip_zone` | `strategy/flipzone` | canonical flipped zones, structure, reaction, liquidity | [flip zone](strategies/flip_zone.md) |
| `session_level` | `strategy/sessionlevel` | canonical session levels, liquidity, reaction | [session level](strategies/session_level.md) |
| `trendline` | `strategy/trendline` | causal trendline anchors and interaction state | [trendline](strategies/trendline.md) |
| `range_edge` | `strategy/rangeedge` | M5 range context and edge rejection | [range edge](strategies/range_edge.md) |
| `box_breakout` | `strategy/boxbreakout` | M5 compression box, accepted break, retest | [box breakout](strategies/box_breakout.md) |
| `momentum_ride` | `strategy/momentumride` | M5 displacement sequence and opposing liquidity | [momentum ride](strategies/momentum_ride.md) |
| `snap_back` | `strategy/snapback` | canonical key level, extension, reversal close | [snap-back](strategies/snap_back.md) |
| `fade_scalp` | `strategy/fadescalp` | equal-level sweep/reclaim, PD, reaction, chop edge | [fade scalp](strategies/fade_scalp.md) |
| `liquidity_sweep` | `strategy/liquiditysweep` | canonical liquidity pool sweep/reclaim | [liquidity sweep](strategies/liquidity_sweep.md) |
| `range_sweep` | `strategy/rangesweep` | M5 range and M1 edge excursion/reclaim | [range sweep](strategies/range_sweep.md) |
| `impulse_pullback` | `strategy/impulsepullback` | M5 impulse and M1 bounded correction | [impulse pullback](strategies/impulse_pullback.md) |
| `scalp_breakout_retest` | `strategy/scalpbreakoutretest` | M5 compression and M1 acceptance/retest | [scalp breakout retest](strategies/scalp_breakout_retest.md) |

## Common candidate contract

Every strategy emits a deterministic identity, direction, entry zone,
technical invalidation, one or more technical targets, machine-readable
evidence, strategy-owned quality components, formation/creation/expiry times,
whole-document configuration provenance, causal technical context, and a
strategy-owned stop envelope. A missing technical fact is unavailable and is
fail-closed; Algo Bot must not reconstruct it with a Python detector.

The lifecycle is `CREATED → ACTIVE → DUPLICATE` for a still-valid thesis, with
terminal `INVALIDATED` or `SETUP_EXPIRED`. Terminal events are never eligible
for a shadow decision or executable TradePlan. Kafka publication carries the
strategy ID/version, opportunity identity, transition, provenance, and the
technical facts required by the policy adapter.

## Canonical analysis dependencies

All strategies read the Go-owned `MarketContext`: canonical ATR, structure and
protected levels, liquidity pools/sweeps, zone geometry and lifecycle,
trendlines, key levels, session state, dealing range/fibonacci, regime, MAD
context, and candle evidence. The former `legacyzone` compatibility rebuild is
not used by the live worker. The Python technical import inventory is enforced
in CI; presentation, manual, accounting, and research modules are not
automatic technical authorities.

## Verification

The registry test requires every catalog ID to have a concrete factory and a
versioned enabled configuration. Real XAU replay coverage proves the canonical
zone families produce opportunities without legacy structural IDs; individual
strategy packages carry their focused positive/negative and causal tests.
Cross-service Python, Go, C#, contract, and deployment checks remain required
for every production change.

## Retired automatic theses

Non-scalp `Break & Retest` is historical/manual only. `box_breakout` owns
the M5 compression/break/retest thesis, while `scalp_breakout_retest` owns the
distinct M5-context/M1-confirmation thesis. Neither retired label is a registry
ID, Go opportunity source, or automatic execution-policy adapter.

# Strategies

The canonical list of automatic technical theses. Each has one Go package in
`analysis-engine/internal/strategy/`, one versioned entry in
`config/analysis.yml` and a specification in this directory. All 21 are enabled in
the live Go opportunity stream; the instrument decides whether an opportunity may
trade.

Analysis Engine publishes technical opportunities only. Algo Bot owns freshness,
quote and spread checks, same-thesis arbitration, exposure and risk policy,
TradePlan construction, Telegram and broker routing
([execution](../execution.md)). Strategies are independent: none imports another,
and `confluence_zone` is the single compositional exception, consuming only
canonical zone facts. A strategy never decides which of several overlapping
opportunities trades; the execution cycle ranks them on quality, not on strategy
class.

## Certification

Every strategy has exactly one status:

- `LEGACY_PARITY_PROVEN`: its decisions reproduce the frozen Python publisher on
  committed real captures (a golden generated from the oracle, never from Go).
- `GO_NATIVE_VALIDATED`: pinned by tests of its own contract. It does **not** claim
  parity: `liquidity_sweep` has no live Python predecessor, while `flip_zone`,
  `session_level`, `trendline`, `box_breakout` and `impulse_pullback` do and are
  measured against it in the matrix (parity not achieved).
- `OBSERVE_ONLY`: analysed and published, never traded.

| ID | Inputs | Legacy equivalent | Proof | Symbols | XAU execution | Status | XAU opportunities, Sep 14-21 / Sep 28-Oct 7 |
|---|---|---|---|---|---|---|---|
| `key_level` | key-level clusters, role, structure, reaction | Python `key_level_reaction` (a1c77584, the profitable XAU week) | `test/keylevelparity` golden | all | live | LEGACY_PARITY_PROVEN | 507 / 709 |
| `confluence_zone` | overlapping canonical zones, reaction | `confluence_zone_reaction` (1c9f323) | `test/techniqueparity` golden, 5 symbols | all | live | LEGACY_PARITY_PROVEN | 270 / 400 |
| `supply` | canonical supply zones, reaction, liquidity | `supply_demand_technique_reaction` (1c9f323) | `test/techniqueparity` golden, 5 symbols | all | live | LEGACY_PARITY_PROVEN (M5 zones; M15 zones are a Go-native addition, no oracle) | 128 / 168 |
| `demand` | canonical demand zones, reaction, liquidity | `supply_demand_technique_reaction` (1c9f323) | `test/techniqueparity` golden, 5 symbols | all | live | LEGACY_PARITY_PROVEN (M5 zones; M15 zones are a Go-native addition, no oracle) | 141 / 188 |
| `order_block` | canonical order-block zones, reaction | `order_block_technique_reaction` (1c9f323) | `test/techniqueparity` golden, 5 symbols | all | live | LEGACY_PARITY_PROVEN | 23 / 30 |
| `fvg` | canonical FVG zones, reaction | `fvg_technique_reaction` (1c9f323) | `test/techniqueparity` golden, 5 symbols | all | live | LEGACY_PARITY_PROVEN | 99 / 120 |
| `ifvg` | canonical inverted FVG zones, reaction | `ifvg_technique_reaction` (1c9f323) | `test/techniqueparity` golden, 5 symbols | all | **contained** (analysis only) | LEGACY_PARITY_PROVEN | 157 / 201 |
| `crt` | closed H1 range, M5 sweep and reclaim | `crt_technique_reaction` (1c9f323) | `test/techniqueparity` golden, 5 symbols | all | live | LEGACY_PARITY_PROVEN | 11 / 5 |
| `break_retest` | broken trendline or key level, retest, hold | Python detector (1c9f323) | `test/detectorparity` golden, 473/473 decisions | all | live | LEGACY_PARITY_PROVEN | 8 / 12 |
| `range_edge` | M5 range context, edge rejection | Python detector (1c9f323) | `test/detectorparity` golden | all | live | LEGACY_PARITY_PROVEN | 28 / 50 |
| `snap_back` | key level or zone, extension, graded grab | Python detector (1c9f323) | `test/detectorparity` golden | all | live | LEGACY_PARITY_PROVEN | 6 / 13 |
| `momentum_ride` | displacement sequence, opposing liquidity | Python detector (1c9f323) | `test/detectorparity` golden | all | live | LEGACY_PARITY_PROVEN | 8 / 5 |
| `fade_scalp` | equal-level sweep and reclaim, PD, chop edge | Python detector (1c9f323) | `test/detectorparity` golden | all | live | LEGACY_PARITY_PROVEN | 10 / 20 |
| `range_sweep` | M5 range, M1 edge excursion and reclaim | Python scalp lane (a1c77584) | `test/scalpparity` real and synthetic M1 captures, 0 mismatches | XAU only | live | LEGACY_PARITY_PROVEN | 3 (Oct 2-6 M1 capture) |
| `scalp_breakout_retest` | M5 compression, M1 acceptance and retest | Python scalp lane (a1c77584) | `test/scalpparity` real and synthetic M1 captures, 0 mismatches | XAU only | live | LEGACY_PARITY_PROVEN | 8 (Oct 2-6 M1 capture) |
| `flip_zone` | canonical flipped zones, structure, reaction | none (Go zone-domain concept) | 10 behavioural tests in `test/strategy/flipzone` | all | live | GO_NATIVE_VALIDATED | 66 / 107 |
| `session_level` | canonical session levels, reaction | `session_level_reaction` (1c9f323) | **no parity**: same bar on 227 of 316 oracle decisions, same entry on 163, and Go emits about 6x as many confirmed bar-states (1831); own tests: behavioural tests in `test/strategy/sessionlevel` | all | **contained** (analysis only) | GO_NATIVE_VALIDATED | 268 / 412 |
| `trendline` | causal trendline anchors and interaction state | `trendline_reaction` (1c9f323) | **partial**: same bar and entry on 6 of 8 oracle decisions, 22 Go confirmed bar-states; 8 decisions is too few to judge; own tests: build, causality and interaction tests in `test/trendline` | all | live | GO_NATIVE_VALIDATED | 18 / 10 |
| `liquidity_sweep` | lone-extreme pool sweep and reclaim, graded grab | none (Go-native) | contract tests in `internal/strategy/liquiditysweep` | all | **contained** (analysis only) | GO_NATIVE_VALIDATED | 2 / 2 |
| `box_breakout` | M5 compression box, accepted break, retest | `box_breakout` (1c9f323, dark by default in Python) | **no parity**: same bar on 0 of 36 oracle decisions; own tests: same-thesis arbitration test against `scalp_breakout_retest` | all | live | GO_NATIVE_VALIDATED | 1 / 4 |
| `impulse_pullback` | M5 impulse, M1 bounded correction | `discover_impulse_pullback` (scalp lane, a1c77584), not ported | **not ported**: Go is a simpler thesis (93 lines) than the Python archetype's role, 4 ATR displacement, body dominance, corrective ratio and location gates; own tests: none beyond the catalog fixture; net negative live | all instruments contained | **contained** (analysis only) | OBSERVE_ONLY | 2 + 11 on M1 (Oct 2-6 M1 capture) |

Proof is scoped to the committed captures (XAU, EURUSD, GBPUSD, GBPJPY, USDJPY for
the technique goldens; XAU, GBPUSD, USDJPY for the detector golden; XAU M1 for the
scalp lane). Replay counts are opportunities created over real XAU bar history
(M5 1500 bars Sep 14-21; M5 2000 bars Sep 28-Oct 7). Replay shows what a
strategy emits, not whether it earns.

### Execution containment

`config/instruments.yml` lists strategies an instrument observes without trading,
each with the evidence behind it:

| Instrument | Observe-only | Evidence |
|---|---|---|
| XAU | `ifvg` | matches Python bar for bar, but lost on XAU in both independent windows reviewed |
| XAU | `liquidity_sweep` | 4 of 5 live XAU trades stopped out (-165 pips); fires about twice a week |
| XAU | `session_level` | 0 of 5 live XAU trades won (-204 pips, all Asia); stays live on FX where it earned |
| every instrument | `impulse_pullback` | net -49 pips over 11 live trades; not a port of the Python scalp archetype |

Containment is reversible by removing the name. Nothing re-enables itself.

## Common candidate contract

Every strategy emits a deterministic identity, direction, entry zone, technical
invalidation, targets, machine-readable evidence, strategy-owned quality
components, formation/creation/expiry times, whole-document configuration
provenance, causal technical context and a strategy-owned stop envelope. A
missing technical fact is unavailable and fails closed; Algo Bot never rebuilds
it.

The lifecycle is `CREATED → ACTIVE → DUPLICATE` for a still-valid thesis, ending
`INVALIDATED` or `SETUP_EXPIRED`. Terminal events are never executable.

## Breakout boundaries

`break_retest` owns a broken canonical M5 trendline or key level followed by a
same-side retest and current rejection. `box_breakout` owns M5 compression,
accepted box break and retest. `scalp_breakout_retest` owns the distinct
M5-context/M1-confirmation thesis. They are separate IDs; when two of them retest
the same corridor, same-thesis arbitration lets exactly one trade.

## Detector-contract strategies

`break_retest`, `range_edge`, `snap_back`, `momentum_ride` and `fade_scalp`
reproduce frozen detector decisions on the detector-contract read
(`internal/legacyread`): bounded M5/M15/H1 windows, the detector swing algorithm,
scored zones, pools and grabs, regime and higher-timeframe bias. Regenerate their
golden with `test/detectorparity/generate_oracle_golden.py` against the frozen
oracle; never from Go output.

## Specifications

- [key_level](key_level.md)
- [confluence_zone](confluence_zone.md)
- [supply](supply.md)
- [demand](demand.md)
- [order_block](order_block.md)
- [fvg](fvg.md)
- [ifvg](ifvg.md)
- [crt](crt.md)
- [break_retest](break_retest.md)
- [range_edge](range_edge.md)
- [snap_back](snap_back.md)
- [momentum_ride](momentum_ride.md)
- [fade_scalp](fade_scalp.md)
- [range_sweep](range_sweep.md)
- [scalp_breakout_retest](scalp_breakout_retest.md)
- [flip_zone](flip_zone.md)
- [session_level](session_level.md)
- [trendline](trendline.md)
- [liquidity_sweep](liquidity_sweep.md)
- [box_breakout](box_breakout.md)
- [impulse_pullback](impulse_pullback.md)

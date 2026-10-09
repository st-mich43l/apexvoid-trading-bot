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
and `confluence_zone` consumes only canonical zone facts to publish its own
opportunity beside theirs. A strategy never suppresses, modifies or waits for another,
and never decides which of several overlapping opportunities trades; the execution
cycle correlates opportunities on one thesis afterwards and ranks them on quality,
keeping every strategy's attribution. There is no strategy family: each strategy's
execution profile is its own row in `algo-bot/app/autotrade/strategy_catalog.py`.

## Certification

Every strategy has exactly one status:

- `LEGACY_PARITY_PROVEN`: its decisions reproduce the frozen Python publisher on
  committed real captures (a golden generated from the oracle, never from Go).
- `GO_NATIVE_VALIDATED`: pinned by tests of its own contract. It does **not** claim
  parity: `liquidity_sweep` has no live Python predecessor.
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
| `break_retest` | own M5 key level or trendline, accepted break, fresh retest and rejection | none (Go v3; the Python detector 1c9f323 was v2) | contract tests in `internal/strategy/breakretest`, `test/brreplay` | all | live | GO_NATIVE_VALIDATED | 2 / 3 (v2: 8 / 12) |
| `range_edge` | M5 range context, edge rejection | Python detector (1c9f323) | `test/detectorparity` golden | all | live | LEGACY_PARITY_PROVEN | 28 / 50 |
| `snap_back` | key level or zone, extension, graded grab | Python detector (1c9f323) | `test/detectorparity` golden | all | live | LEGACY_PARITY_PROVEN | 6 / 13 |
| `momentum_ride` | displacement sequence, opposing liquidity | Python detector (1c9f323) | `test/detectorparity` golden | all | live | LEGACY_PARITY_PROVEN | 8 / 5 |
| `fade_scalp` | equal-level sweep and reclaim, PD, chop edge | Python detector (1c9f323) | `test/detectorparity` golden | all | live | LEGACY_PARITY_PROVEN | 10 / 20 |
| `range_sweep` | M5 range, M1 edge excursion and reclaim | Python scalp lane (a1c77584) | `test/scalpparity` real and synthetic M1 captures, 0 mismatches | XAU only | live | LEGACY_PARITY_PROVEN | 3 (Oct 2-6 M1 capture) |
| `scalp_breakout_retest` | M5 compression, M1 acceptance and retest | Python scalp lane (a1c77584) | `test/scalpparity` real and synthetic M1 captures, 0 mismatches | XAU only | live | LEGACY_PARITY_PROVEN | 8 (Oct 2-6 M1 capture) |
| `flip_zone` | zones born of an accepted role flip, key-level role, reaction | `flip_demand_zone_reaction` / `flip_supply_zone_reaction` (1c9f323) | `test/legacyparity` golden, 5 symbols: 48 BUY and 43 SELL oracle decisions, identical (presence, direction, entry, stars) and silent elsewhere | all | live | LEGACY_PARITY_PROVEN | 34 / 16 |
| `session_level` | session / previous-day / previous-week highs and lows, reaction | `session_level_reaction` (1c9f323) | `test/legacyparity` golden, 5 symbols: 316 oracle decisions, identical and silent elsewhere | all | **contained** (analysis only) | LEGACY_PARITY_PROVEN | 48 / 38 |
| `trendline` | causal trendline anchors, health and live interaction, reaction | `trendline_reaction` V2 (1c9f323) | `test/legacyparity` golden, 5 symbols: 8 oracle decisions, identical and silent elsewhere (few decisions: the gate is narrow) | all | live | LEGACY_PARITY_PROVEN | 4 / 0 |
| `liquidity_sweep` | lone-extreme pool sweep and reclaim, graded grab | none (Go-native) | contract tests in `internal/strategy/liquiditysweep` | all | **contained** (analysis only) | GO_NATIVE_VALIDATED | 2 / 2 |
| `box_breakout` | accepted break of the regime box, accepting-bar or retest entry | `box_breakout` (1c9f323; `box_breakout_enabled` is False in Python code, True in the deployed config) | `test/legacyparity` golden, 5 symbols: 36 oracle decisions, identical and silent elsewhere | all | live | LEGACY_PARITY_PROVEN | 10 / 6 |
| `impulse_pullback` | M5 impulse, corrective pullback, structural reference and role, M1 confirmation | `discover_impulse_pullback` (scalp lane, a1c77584) | `test/scalpparity`: the real 33 h XAU capture holds **no** Python decision (0 of 1,939 cycles); 18 identical decisions and silence elsewhere on nine seeded synthetic M1 captures (labelled synthetic) | XAU | **contained** (analysis only; also contained on FX) | LEGACY_PARITY_PROVEN on synthetic data only | 2 + 11 on M1 (Oct 2-6 M1 capture, pre-port) |

Proof is scoped to the committed captures (XAU, EURUSD, GBPUSD, GBPJPY, USDJPY for
the technique goldens; XAU, GBPUSD, USDJPY for the detector golden; XAU M1 for the
scalp lane). Replay counts are opportunities created over real XAU bar history
(M5 1500 bars Sep 14-21; M5 2000 bars Sep 28-Oct 7). Replay shows what a
strategy emits, not whether it earns.

### Break & Retest v3 is not a port

`break_retest` v3 is deliberately *not* the frozen Python detector. The frozen one
took the first retest after the latest break at any age, attached a fixed 0.75
quality and hard-coded evidence, tried trendlines before key levels, and set the
stop inside the retest zone (`docs/strategies/break_retest.md`, "What v2 got wrong";
reproduced on real engine contexts by `test/brreplay`: 42 of 59 replayed v2 setups
were published more than two candles after their retest). v3 builds its own
references from closed M5 pivots, accepts a break on measured force, requires a
fresh retest strictly after the acceptance, and places the stop and target by
structure. The oracle golden is untouched; `break_retest` simply left the
`test/detectorparity` covered list, and v2 stays reproducible as the frozen baseline in
`test/brreplay`. Replay: 59 v2 setups over the six captures become 32 v3 setups whose
hypothetical outcome is *not* better (held-out mean R -0.59 over 26 fills against -0.23
over 47): the change is a technical correction, with no improvement claim.

### Deliberate departure from the frozen Python oracle

The frozen technique publishers skipped every instance that a confluence band
covered, so a valid FVG, Order Block, Supply, Demand, iFVG or CRT setup vanished
whenever Confluence Zone overlapped it. That coupling is removed: each technique
evaluates its own instances (`TechniqueSource.Technique`), and Confluence Zone
publishes its own opportunity from the same facts. The oracle golden is unchanged and
still proven bar for bar through `TechniqueExcludingConfluenceCoverage`, which exists
only for `test/techniqueparity`; `TestConfluenceCoverageNeverSuppressesAnIndependentTechniqueCandidate`
pins the new behavior (every oracle decision is still published; bars the band
previously silenced now publish the technique's own setup). On the XAU M1 production
capture (Oct 2-6) the change adds 124 opportunities (1,663 to 1,787): `ifvg` +45,
`demand` +25, `order_block` +26, `fvg` +13, `crt` +10, `supply` +5. Nine supply/demand opportunities that were already published now carry a different instance of the same technique, because the best setup is chosen among all of its instances rather than only the uncovered ones; their identities are unchanged. The Go replay golden
(`replay-go-supply-demand-confirmed-envelopes-xau-20260921.jsonl`, generated by Go and never a Python oracle) was regenerated and reviewed: 229 to 263 confirmed envelopes, 1,576 to 1,691 discovered. Exposure is
unchanged by design: same-thesis arbitration and the entry corridor still admit one
trade per corridor.

### Execution containment

`config/instruments.yml` lists strategies an instrument observes without trading,
each with the evidence behind it:

| Instrument | Observe-only | Evidence |
|---|---|---|
| XAU | `ifvg` | matches Python bar for bar, but lost on XAU in both independent windows reviewed |
| XAU | `liquidity_sweep` | 4 of 5 live XAU trades stopped out (-165 pips); fires about twice a week |
| XAU | `session_level` | 0 of 5 live XAU trades won (-204 pips, all Asia), taken before this port restored the frozen decisions; parity is not evidence of edge, so it stays contained; live on FX |
| every instrument | `impulse_pullback` | net -49 pips over 11 live trades of the earlier simplified thesis; the port (XAU only) is proven on synthetic data only, not on the real capture, so it stays contained |
| every instrument | any setup whose structure timeframe is `M15` (`observe_only_structure_timeframes`) | the M15 supply/demand extension has no Python predecessor and no execution record; detected and published, not traded |

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

`break_retest` owns its own M5 key level or trendline, an accepted break of it and
the fresh retest and rejection that follows. `box_breakout` owns M5 compression,
accepted box break and retest. `scalp_breakout_retest` owns the distinct
M5-context/M1-confirmation thesis. They are separate IDs; when two of them retest
the same corridor, same-thesis arbitration lets exactly one trade.

## Detector-contract strategies

`range_edge`, `snap_back`, `momentum_ride` and `fade_scalp`
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

Audits: [independence](independence-audit.md), [arbitration and quality (P1)](arbitration-quality-audit.md).

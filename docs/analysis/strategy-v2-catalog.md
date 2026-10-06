# Analysis Engine V2 strategy catalog

This is the canonical list of automatic technical theses. Each entry has one
Go package, one versioned configuration entry in `config/analysis.yml`, and a
strategy-specific specification under [`strategies/`](strategies/). All 21
entries are enabled in the live Go opportunity stream as of 2026-10-05.

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
| `break_retest` | `strategy/breakretest` | M5 broken trendline or key level, retest, hold, rejection | [break & retest](strategies/break_retest.md) |
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
context, and candle evidence. The technique-zone builder is
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

## Breakout strategy boundaries

`break_retest` owns a broken canonical M5 trendline or key level followed by a
same-side retest and current rejection. `box_breakout` owns M5 compression,
accepted box break and retest. `scalp_breakout_retest` owns the distinct
M5-context/M1-confirmation thesis. They are separate registry IDs and adapters;
one detector must not be substituted for another.

## Detector-contract strategies

`break_retest`, `range_edge`, `snap_back`, `momentum_ride`, `fade_scalp` and
`key_level` reproduce the frozen Python detectors' decisions (Key Level those of
the profitable XAU week, 14–18 Sep 2026, see its page). They read the engine's
detector-contract frame (swings, structure, levels, scored zones, liquidity
pools and grabs, sessions, trendlines, the scalp range, regime and
higher-timeframe bias computed over the frozen bounded windows) and share one
qualification step: clipped entry band, valid-side level, entry distance,
fibonacci touch and the confluence floor. Their confluence is the detector's own
and is published as the candidate's technical confluence. The permanent golden
test in `analysis-engine/test/detectorparity` replays the committed XAU, GBPUSD
and USDJPY captures and requires every decision to match the frozen oracle bar
by bar.

## Technique publishers (zone family)

Supply, Demand, Order Block, FVG, iFVG, CRT and Confluence Zone publish the
confirmed reaction of the frozen Python technique publishers
(`technique_detectors.py`) rather than judging resting zones themselves. The
engine collects the same technique instances the frozen analysis did — the
unmerged supply/demand, order-block and FVG zones, mitigation-stamped and scored
(`LegacyFrame.TechniqueZones`), the iFVG inversions discovered from the FVGs, and
the CRT ranges the H1 frame and the execution window produce — and
`strategyutil.TechniqueSource` reproduces `_technique_reaction` and
`confluence_zone_reaction` over them: instances covered by a confluence band
(two or more distinct techniques overlapping by at least half, merged up to three
ATR wide) are left to Confluence Zone, each remaining instance is qualified by
the structural reaction and the shared confluence floor, and the single best per
publisher (most stars, nearest entry) is published. Resting-zone observations
are unchanged and are never confirmed.

`test/techniqueparity` replays the committed XAU, EURUSD, GBPUSD, GBPJPY and
USDJPY captures and requires every decision to match the frozen oracle bar by
bar (2,957 decisions: presence, direction, entry band, confluence stars). One bar
differs, deliberately: the frozen publishers match the confirming sweep grab with
a hard-coded 0.1 pip tolerance on every instrument (they call `_zone_grabs_for`
without its `pip_size`); that is XAU's real pip, so XAU is exact, but it is ten
times too wide on a 0.01-pip pair, and Go uses the instrument's own pip size.

## XAU execution containment

An instrument may *observe* a strategy without trading it:
`instruments.<SYMBOL>.overrides.execution.go_opportunity.observe_only_strategies`.
Go still produces and publishes the strategy's opportunities, Algo Bot stores
them and records the decision `execution_contained`, but no match (hence no
TradePlan) is built, and the engine leaves them out of arbitration so a
contained setup can never suppress or hold an executable one. XAU observes
`ifvg` and `liquidity_sweep`: both lost on XAU in both independent windows
reviewed. Liquidity Sweep has no Python predecessor to prove against, and iFVG
is held until its decision parity with the frozen technique detector is shown.
Range Sweep was contained earlier as an unproven simplified port; it is now the
Go port of the frozen M5-setup/M1-confirm scalp lane (`test/scalpparity`: 5/5
real M1 decisions and 124/124 synthetic decisions match) and executes. Removing
a name re-enables its execution; nothing else changes. The other instruments contain nothing.

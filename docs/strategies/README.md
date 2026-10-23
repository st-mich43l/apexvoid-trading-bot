# Strategies

The canonical list of automatic technical theses. Each has one Go package in
`analysis-engine/internal/strategy/`, one versioned entry in
`config/analysis.yml` and a specification in this directory. All 20 are enabled in
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

## Quality audit

The 2026-10-14 quality audit of all 21 strategies (since then Range Sweep, Snap-Back, Momentum Ride and the Breakout Retest Scalp were removed; 17 remain) (technical, execution, performance and
evidence verdicts, quality-score math, replay and production R) is in
[ae-quality-certification.md](ae-quality-certification.md); its retirement and restriction
proposals, none applied, are in [ae-strategy-retirement-review.md](ae-strategy-retirement-review.md);
the machine-readable matrix is [ae-quality-replay-results.json](ae-quality-replay-results.json).

## Certification

Every strategy has exactly one status:

- `LEGACY_PARITY_PROVEN`: its decisions reproduce the frozen Python publisher on
  committed real captures (a golden generated from the oracle, never from Go).
- `GO_NATIVE_VALIDATED`: pinned by tests of its own contract. It does **not** claim
  parity: a strategy with no live Python predecessor (`ifvg`, `confluence_zone`, `break_retest`) is validated by tests of its own contract.
- `OBSERVE_ONLY`: analysed and published, never traded.

| ID | Inputs | Legacy equivalent | Proof | Symbols | XAU execution | Status | XAU opportunities, Sep 14-21 / Sep 28-Oct 7 |
|---|---|---|---|---|---|---|---|
| `key_level` | key-level clusters, role, structure, reaction | Python `key_level_reaction` (a1c77584, the profitable XAU week) | `test/keylevelparity` golden | all | live | LEGACY_PARITY_PROVEN | 507 / 709 |
| `confluence_zone` | at least `minimum_facts` independent canonical structures supporting one price region, measured reaction | none (Go v3; the Python publisher 1c9f323 was v2, see [confluence_zone](confluence_zone.md)) | contract tests in `internal/structuralconfluence` and `internal/strategy/confluencezone`, `test/czreplay` ([certification](confluence-zone-v3-certification.md)) | all | live (XAU containment recommended, see certification) | GO_NATIVE_VALIDATED | 23 / 20 (v2: 83 / 58) |
| `supply` | canonical supply zones, reaction, liquidity | `supply_demand_technique_reaction` (1c9f323) | `test/techniqueparity` golden, 5 symbols | all | live | LEGACY_PARITY_PROVEN (M5 zones; M15 zones are a Go-native addition, no oracle) | 128 / 168 |
| `demand` | canonical demand zones, reaction, liquidity | `supply_demand_technique_reaction` (1c9f323) | `test/techniqueparity` golden, 5 symbols | all | live | LEGACY_PARITY_PROVEN (M5 zones; M15 zones are a Go-native addition, no oracle) | 141 / 188 |
| `order_block` | canonical order-block zones, reaction | `order_block_technique_reaction` (1c9f323) | `test/techniqueparity` golden, 5 symbols | all | live | LEGACY_PARITY_PROVEN | 23 / 30 |
| `fvg` | canonical FVG zones, reaction | `fvg_technique_reaction` (1c9f323) | `test/techniqueparity` golden, 5 symbols | all | live | LEGACY_PARITY_PROVEN | 99 / 120 |
| `ifvg` | own M5 fair-value gap, its closing-price inversion, a fresh retest and rejection | none (Go v3; the Python publisher 1c9f323 was v2, see [ifvg](ifvg.md)) | contract tests in `internal/strategy/ifvg`, `test/ifvgreplay` (defect reproduction on the real captures, no future dependence, invariants, golden) | all | live (XAU enabled 13 Oct 2026; FX unchanged) | GO_NATIVE_VALIDATED | 7 / 14 (v2 confirmed: 110 / 120) |
| `crt` | closed H1 range, M5 sweep, reclaim and structure shift | none (v3 departs deliberately from `crt_technique_reaction`, see below) | `internal/strategy/crt` positive and negative fixtures; `test/crtreplay`: no future dependence on 6 captures bar for bar, technical invariants on every setup found, and a v3 golden | all | live (an honest stop beyond the instrument cap is refused) | GO_NATIVE_VALIDATED | 1 / 0 |
| `break_retest` | own M5 key level or trendline, accepted break, fresh retest and rejection | none (Go v3; the Python detector 1c9f323 was v2) | contract tests in `internal/strategy/breakretest`, `test/brreplay` | all | live | GO_NATIVE_VALIDATED | 1 / 2 (v2: 8 / 12) |
| `fade_scalp` | equal-level sweep, failed continuation, reversion to the local mean (v3) | none (Go v3; the Python detector 1c9f323 was v2) | contract tests in `internal/strategy/fadescalp` | all | live | GO_NATIVE_VALIDATED | 10 / 20 |
| `flip_zone` | zones born of an accepted role flip, key-level role, reaction | `flip_demand_zone_reaction` / `flip_supply_zone_reaction` (1c9f323) | `test/legacyparity` golden, 5 symbols: 48 BUY and 43 SELL oracle decisions, identical (presence, direction, entry, stars) and silent elsewhere | all | live | LEGACY_PARITY_PROVEN | 34 / 16 |
| `trendline` | causal trendline anchors, health and live interaction, reaction | `trendline_reaction` V2 (1c9f323) | `test/legacyparity` golden, 5 symbols: 8 oracle decisions, identical and silent elsewhere (few decisions: the gate is narrow) | all | live | LEGACY_PARITY_PROVEN | 4 / 0 |
| `impulse_pullback` | M5 impulse, corrective pullback, structural reference and role, M1 confirmation | `discover_impulse_pullback` (scalp lane, a1c77584) | `test/scalpparity`: the real 33 h XAU capture holds **no** Python decision (0 of 1,939 cycles); 18 identical decisions and silence elsewhere on nine seeded synthetic M1 captures (labelled synthetic) | XAU | **contained** (analysis only; also contained on FX) | LEGACY_PARITY_PROVEN on synthetic data only | 2 + 11 on M1 (Oct 2-6 M1 capture, pre-port) |

Proof is scoped to the committed captures (XAU, EURUSD, GBPUSD, GBPJPY, USDJPY for
the technique goldens; XAU, GBPUSD, USDJPY for the detector golden; XAU M1 for the
scalp lane). Replay counts are opportunities created over real XAU bar history
(M5 1500 bars Sep 14-21; M5 2000 bars Sep 28-Oct 7). Replay shows what a
strategy emits, not whether it earns.

### CRT v3 is not a port

`crt` v3 is deliberately *not* the frozen `crt_technique_reaction`. The frozen
discovery mixed an H1 candle position with M5 candles, accepted any one-tick pierce
of the range, confirmed on any reaction touching the whole H1 range, and stopped
inside the sweep wick (`docs/strategies/crt.md`, "What v2 got wrong"). v3 reads the
anchor, sweep, reclaim and an M5 structure shift from closed candles by time, and
places the stop beyond the real manipulation extreme. The frozen port
(`techniquezone.DiscoverCRT` / `CollectCRT`) is unchanged, the oracle golden still
proves it bar for bar, and it still supplies the CRT instance Confluence Zone
aggregates (see the unresolved risks in the CRT page). Replay: 127 v2 setups over the
six captures become 1 published v3 setup, and 105 of those 127 had their stop inside the
sweep wick.

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
`test/brreplay`. Replay: 59 v2 setups over the six captures become 22 v3 setups whose
hypothetical outcome is *not* better (held-out mean R -0.73 over 17 fills against -0.23
over 47): the change is a technical correction, with no improvement claim.

### Confluence Zone v3 is not a port

`confluence_zone` v3 is deliberately *not* the frozen `confluence_zone_reaction`. The frozen publisher counted technique
*labels* as facts, published the union of its members as the entry, let a member be created by the reacting candle,
hard-coded two quality factors true and invented a 2R target (`docs/strategies/confluence-zone-xau-forensic-audit.md`).
v3 requires independent sources (structures sharing a displacement, gap, inversion or source candle are one), trades the
region they actually overlap as of the touch, measures the confirmation from the candle, sets the stop beyond the
structures' full extent and refuses it beyond the execution cap, and publishes only a real, unswept opposing-liquidity
target. The frozen `TechniqueSource.ConfluenceZone` is unchanged and still proves the oracle golden bar for bar. Replay:
the six captures' v2 decisions (about 190 per symbol) become 18-59 v3 decisions per capture, removing v2's winners and
losers in nearly the same proportion: the change is a technical correction, with no improvement claim
([certification](confluence-zone-v3-certification.md)).

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
| every instrument | `impulse_pullback` | net -49 pips over 11 live trades of the earlier simplified thesis; the port (XAU only) is proven on synthetic data only, not on the real capture, so it stays contained |
| every instrument | any setup whose structure timeframe is `M15` (`observe_only_structure_timeframes`) | the M15 supply/demand extension has no Python predecessor and no execution record; detected and published, not traded |

Containment is reversible by removing the name. Nothing re-enables itself.

**Enabled on XAU (13 Oct 2026, owner's decision).** `ifvg` (v3) was taken off XAU's list after technical certification; the hypothetical XAU replay is not evidence of an edge.

## Quality

`Quality.Overall` is a strategy-specific technical score (mean of documented `score_*` dimensions, in [0, 1]), informational only: see [quality-scoring.md](quality-scoring.md) and its outcome validation [q53-quality-expectancy.md](q53-quality-expectancy.md) (no positive separation). It is not a probability, it is not used for ranking (`quality_ranking: false`) and the confluence stars (`min_confluence`) remain the only quality-based gate. Whether the room in front of an admitted plan, read in R of the executable stop, should gate admission was evaluated and is not supported: [q54-executable-room.md](q54-executable-room.md). Whether the replay measures what production trades (it does for FX, not for the XAU entry), and the durable plan-time evidence: [q55-replay-parity.md](q55-replay-parity.md).

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
the fresh retest and rejection that follows. It is the only breakout strategy.

## Detector-contract strategies

`key_level`, `flip_zone`, `trendline` and the technique strategies read the detector-contract
frame; `fade_scalp` was rebuilt as v3 and no longer reproduces the frozen detector. The frame is
the detector-contract read
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
- [fade_scalp](fade_scalp.md)
- [flip_zone](flip_zone.md)
- [trendline](trendline.md)
- [impulse_pullback](impulse_pullback.md)

Audits: [independence](independence-audit.md), [arbitration and quality (P1)](arbitration-quality-audit.md).

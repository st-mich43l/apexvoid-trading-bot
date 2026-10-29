# Analysis Engine

The Go service in `analysis-engine/`. It reads closed bars from Redis, keeps the
technical state of every symbol, runs every strategy, owns each opportunity's
lifecycle and publishes that lifecycle to Kafka. It never sizes, routes or
executes a trade.

## One closed bar

`engine.SymbolWorker.ApplyWithResult` (`internal/engine/worker.go`) runs once per
closed bar, per symbol and timeframe, in this order:

```mermaid
flowchart TD
  BAR["closed bar from Redis<br/>bars:{SYMBOL}:{TF} + bars:new"] --> HIST[marketdata history append]
  HIST --> ATR[canonical ATR]
  ATR --> STATE["structure · zone V2 · liquidity<br/>trendline · key levels · session · fib"]
  STATE --> CTX["rebuild MarketContext<br/>+ legacyread frames M5/M15/H1<br/>(scored technique zones)"]
  CTX --> EVAL["strategy registry: every enabled strategy<br/>that depends on this timeframe"]
  EVAL --> INV["Book.InvalidateByClose<br/>(close beyond a live candidate's invalidation)<br/>Book.InvalidateByStructureClose<br/>(order_block: close of the structure's own timeframe<br/>beyond its far edge)"]
  INV --> ENR["per candidate: provenance, first observation,<br/>geometry normalize, defended-level block,<br/>technical context, stop envelope"]
  ENR --> OBS["Book.Observe<br/>CREATED → ACTIVE → DUPLICATE"]
  OBS --> EXP[Book.Expire]
  EXP --> ARB["arbitration: thesis grouping<br/>(telemetry, never decides a trade)"]
  ARB --> SNAP["snapshot: zone book → Redis<br/>analysis:zone_book:{SYMBOL}"]
  OBS -. "Created / Invalidated / Expired" .-> K[("Kafka<br/>analysis.opportunity.*")]
  INV -.-> K
  EXP -.-> K
  ARB -. decisions .-> K
```

Invalidation runs before the new observations, so a setup whose confirmed close
already broke its threshold cannot be re-observed on that bar. A close only
invalidates candidates observed on the same timeframe, except an `order_block`
structure, which only a close of the structure's OWN timeframe (M5, M15 or H1)
can withdraw ([order-block-definition](strategies/order-block-definition.md)).

## Packages

```mermaid
flowchart TB
  R0["rank 0 · market, telemetry"]
  R1["rank 1 · indicator, marketdata, config, session, mad, candle"]
  R2["rank 2 · structure"]
  R3["rank 3 · zone, liquidity, trendline, keylevel, fib,<br/>barrier, momentum, quality, reaction, regime"]
  R4["rank 4 · techniquezone"]
  R5["rank 5 · context, opportunity, confluence, structuralconfluence"]
  R6["rank 6 · strategy, strategyutil, legacyread, state, arbitration"]
  R7["rank 7 · strategy/* implementations, transport/kafka, transport/redis"]
  R8["rank 8 · engine"]
  R8 --> R7 --> R6 --> R5 --> R4 --> R3 --> R2 --> R1 --> R0
```

An arrow means "may import". A package imports only from strictly lower ranks;
the table in `analysis-engine/test/architecture/dependency_test.go` is the
authority and the test fails the build on a violation.
Strategies never import one another, Redis, Kafka, Postgres, Telegram, cTrader or
Algo Bot.

| Package | Role |
|---|---|
| `structure`, `liquidity`, `zone`, `trendline`, `keylevel`, `session`, `fib` | the canonical, causal technical state (V2 book) |
| `techniquezone` | the frozen-Python technique geometry (supply/demand, order block, FVG, iFVG, CRT) that the confirmed zone strategies trade |
| `legacyread` | bounded M5/M15/H1 frames with the detector swing algorithm, scored zones, pools and grabs, regime and HTF bias, derived from the same closed bars |
| `strategyutil` | the shared confirmation spine: technique source, reaction, `Finish`, stop budget, candidate builder |
| `opportunity` | candidate validation and the lifecycle book |
| `arbitration` | thesis grouping (`go_thesis_id`) and winner/suppressed/conflict telemetry |

## Two zone populations

The engine holds zones twice. Knowing which one a strategy reads matters when a
zone rule changes.

```mermaid
flowchart LR
  BARS[closed bars] --> V2["internal/zone (V2 book)<br/>lifecycle: analysis.techniques.*"]
  BARS --> TZ["internal/techniquezone<br/>lifecycle: ProductionTechniqueSettings()<br/>(hardcoded parity values)"]
  V2 --> REST["resting zone observations<br/>(not executable)"]
  V2 --> ZB["zone book → Algo Bot<br/>opposing-barrier check"]
  TZ --> CONF["confirmed technique strategies<br/>supply · demand · order_block · fvg"]
  TZ --> CZ["Confluence Zone facts"]
```

Changing `analysis.techniques.*` in `config/analysis.yml` changes the V2 book only.
The traded supply, demand, order block and FVG setups are judged by
`techniquezone.ProductionTechniqueSettings()` plus strategy-scoped keys injected in
`engine/technique_zones.go` (the FVG definition is the pattern to copy).

## Confirmed zone setups

The path every confirmed `supply`, `demand`, `order_block` and `fvg` opportunity
takes (`strategyutil.ConfirmedTechnique`):

```mermaid
flowchart TD
  Z["technique zones of one frame<br/>M5, or M15/H1 bound to M5"] --> MM["MarkMitigation: touch count<br/>(rising edge into the band)"]
  MM --> SP{"spent?<br/>touches > 30 or invalidated"}
  SP -- yes --> X1[dropped]
  SP -- no --> NI{"notInvalidated<br/>close beyond far edge by<br/>> max(eps, 0.5 × median ATR)?"}
  NI -- "broken, not reclaimed in 6 bars,<br/>or > 2 reclaimed episodes" --> X1
  NI -- holds --> PX{"price at zone?<br/>(M5 frame only; HTF ignores proximity)"}
  PX -- no --> X1
  PX -- yes --> W{"width ≤ max ATR,<br/>OB body ≥ 0.6 + BOS,<br/>FVG definition"}
  W -- fail --> X1
  W -- pass --> OBD{"order_block v6: any close of this frame<br/>beyond the far edge by > buffer × ATR at that bar?<br/>(no reclaim forgiveness)"}
  OBD -- yes --> X1
  OBD -- no --> PUB["TechniqueSource.publish"]
  PUB --> TC{"maximum_zone_touches<br/>(supply/demand/order_block: 2)"}
  TC -- exceeded --> X2[not published]
  TC -- ok --> EV{"EntryValid<br/>price ≤ maximum_entry_atr (2.0) from zone"}
  EV -- no --> X2
  EV -- yes --> RX{"reaction after the zone formed<br/>wick_rejection · sweep_reclaim ·<br/>rejection_choch · strong_reclaim · engulfing"}
  RX -- none --> X2
  RX -- found --> FIN{"Finish: level and entry valid,<br/>confluence stars ≥ floor (2)"}
  FIN -- no --> X2
  FIN -- yes --> CAND["TechniqueCandidate<br/>stop = beyond structure and reaction extreme + buffer<br/>stop budget: refuse above instrument cap<br/>target = nearest opposing liquidity"]
  CAND --> OUT[opportunity.Candidate]
```

A supply/demand zone that closed beyond its far edge but was reclaimed inside the
sweep window is still live: the reclaim counts as a liquidity sweep. **An order block
no longer is** (v6): any close of its own frame beyond the far edge by more than
`confirmed_break_buffer_atr` of that bar's ATR retires it, reclaimed or not; only a
wick that closes back inside is the sweep. The tolerance is measured
in the frame's median ATR over the 400-bar window, not the ATR of the breach bar.

## Opportunity lifecycle

```mermaid
stateDiagram-v2
  [*] --> CREATED: first valid observation
  CREATED --> ACTIVE: observed again
  ACTIVE --> ACTIVE: DUPLICATE (no event)
  CREATED --> INVALIDATED: close beyond invalidation price<br/>or order_block structure closed through
  ACTIVE --> INVALIDATED: close beyond invalidation price<br/>or order_block structure closed through
  CREATED --> EXPIRED: past expires_at
  ACTIVE --> EXPIRED: past expires_at
  INVALIDATED --> [*]
  EXPIRED --> [*]
```

Only CREATED, INVALIDATED and EXPIRED are published. A strategy that stops
emitting a candidate does not end it: a published opportunity lives until a
close beyond its own invalidation price (the stop level, not the structure edge),
for an `order_block` a close of its structure's timeframe beyond the structural
far edge (`STRUCTURE_CLOSED_THROUGH`), or until `expires_at` (formation time + the strategy's `expiry_hours`, 24 h for
the zone strategies). Algo Bot executes only opportunities that are live in this
book.

## Acceptance and quality

Three kinds of decision exist, and only one of them uses a quality number.

```mermaid
flowchart LR
  V["Validity<br/>(mandatory facts)"] --> G["Confluence floor<br/>(the one quality gate)"] --> S["Selection among valid<br/>siblings of one strategy"] --> P[published]
  P --> AB["Algo Bot: quality is informational<br/>(quality_ranking: false)"]
```

1. **Validity.** A failed fact rejects whatever the score: closed candles only and
   no look-ahead; the structure exists before the reaction
   (`Instance.TouchableFrom`, `ReactionFrom`); level and entry valid for the price
   (`LegacyDetector.Finish` runs `LevelValid`/`EntryValid` before scoring); the
   strategy's own formation; technical invalidation never tightened by the
   envelope; target room; `Candidate.Validate()`.
   `TestMaximalEvidenceCannotRescueAnInvalidSetup` pins the order.
2. **Confluence floor.** `Finish` refuses a valid setup below 2 stars
   (`legacy_detect.go`); iFVG, CRT and Break & Retest apply the same floor to
   their own factors, Confluence Zone to its independent-fact count. v1 factor
   points: HTF 4, touches up to 3, wick 3, displacement 3, session 2, structural
   3, fib 2.5, CHoCH 2 (of 20.5); 2 stars at 8, 3 at 12.
3. **Selection.** When one strategy has several valid candidates on a bar it
   publishes the one with more stars, then the nearest entry.

After Kafka, `quality.overall`, the stars and the tier change nothing: arbitration
ignores quality (`quality_ranking: false`), a confluence below the policy minimum
is a preference note, and volume does not depend on the tier
(`algo-bot/tests/test_quality_is_informational.py`,
`test_arbitration_neutral_ranking.py`). `Quality.Overall` is the mean of each
strategy's documented `score_*` dimensions in [0, 1]; replays found no positive
separation of outcome by it, which is why it does not rank.

## Strategy independence

Every strategy owns its candidates. Disabling one never changes another's, a
confluence overlap never removes a technique's setup (Confluence Zone publishes
its own opportunity beside theirs), and there is no strategy family in any
decision. Each strategy's execution profile is its own row in
`algo-bot/app/autotrade/strategy_catalog.py`; `strategy_family` on TradePlan V8 is
a record label no decision reads.

## Session and regime

Session is context and a confluence input, never a clock gate: no London/NY
window blocks a plan. The MAD Asia-phase classifier (accumulation, manipulation,
expansion) is soft context stamped onto opportunities.

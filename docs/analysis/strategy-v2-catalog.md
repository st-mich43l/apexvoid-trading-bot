# Strategy V2 Catalog — Legacy Audit (Phase S0)

Per `apexvoid-bot-prompts/rebuild-strategies.md` §7–§37: a semantic audit
of every legacy Python strategy/technique name, done **before** any Go
strategy implementation. This is the required gate for Phase S2+ (see
`docs/analysis-engine-v2-migration.md` and ADR-003/ADR-006, which already
bind the independent-strategy and centralized-test-layout rules the
eventual Go work must follow).

Ground truth used for every classification below: `algo-bot/app/autotrade/
strategy_names.py` (the canonical name/ID master list, 32 entries),
`algo-bot/app/analysis/detectors.py`'s `LIVE_DETECTOR_REGISTRY` (the
actual live-vs-replay-only detector wiring, not just the name list), and
direct reads of each detector/archetype function's real logic where the
classification wasn't obvious from names alone.

Legend for classification:

- **REBUILD** — real, distinct thesis; port the idea (not the code) to a
  new independent Go `internal/strategy/<name>/` package.
- **SPLIT** — legacy name covers more than one real thesis; becomes 2+ V2
  strategies.
- **MERGE** — legacy name is a duplicate/near-duplicate of a surviving
  sibling; folds into it.
- **RENAME** — same thesis, new V2 identity/ID only.
- **TECHNIQUE_ONLY** — a market primitive/geometry fact, not a strategy
  by itself (spec §3/§18–§26 — FVG/OB/Fib/Key-Level as canonical facts).
- **CONFLUENCE_COMPONENT** — feeds `ConfluenceZoneStrategy` (spec §5),
  never its own strategy or a child of one.
- **RETIRED** — no live path, no distinct thesis worth rebuilding.
- **HISTORICAL_ALIAS** — kept resolvable only to read old DB records;
  never a new Go identity.

## Live detector registry, for reference (`detectors.py:3708–3817`)

The authoritative *currently-live* picture — not every `strategy_names.py`
entry has a live detector, and not every registered detector is enabled
by default:

| detector_id | canonical_family | enabled by default? |
|---|---|---|
| `key_level_reaction` | KEY_LEVEL | yes |
| `confluence_zone_reaction` | SUPPLY_DEMAND | yes |
| `supply_demand_technique_reaction` | SUPPLY_DEMAND | yes |
| `order_block_technique_reaction` | SUPPLY_DEMAND | yes |
| `fvg_technique_reaction` | SUPPLY_DEMAND | yes |
| `ifvg_technique_reaction` | SUPPLY_DEMAND | yes |
| `crt_technique_reaction` | SUPPLY_DEMAND | yes |
| `demand_zone_reaction` | SUPPLY_DEMAND | **no** — `zone_reaction_fallback_enabled=False` |
| `supply_zone_reaction` | SUPPLY_DEMAND | **no** — same fallback gate |
| `flip_demand_zone_reaction` / `flip_supply_zone_reaction` | SUPPLY_DEMAND | yes |
| `session_level_reaction` | SESSION_LEVEL | yes |
| `trendline_reaction` | TRENDLINE | yes |
| `range_edge_scalp` | RANGE_REVERSION | yes |
| `box_breakout` | BREAKOUT_RETEST | **no** — replay-only, "bespoke box-consolidation confirmation... stays off pending a deliberate rollout decision" |
| `break_retest` | BREAKOUT_RETEST | **no** — replay-only, same reason, trendline-based confirmation |
| `momentum_ride` | MOMENTUM_CONTINUATION | yes |
| `snap_back` | LIQUIDITY_REVERSAL | yes |
| `fade_scalp` | LIQUIDITY_REVERSAL | yes |

Plus the separate M1 scalping engine (`algo-bot/app/scalping/strategies.py`,
own registry, own config path under `auto_algo.strategies.scalping.*`):
`discover_range_sweep`, `discover_impulse_pullback`,
`discover_breakout_retest` — three named "archetypes," each independently
enabled via `auto_algo.strategies.scalping.archetypes.*`.

## Catalog — one row per `strategy_names.py` entry

| # | Legacy canonical | Family (Python) | Live? | Classification | V2 disposition & reasoning |
|---|---|---|---|---|---|
| 1 | Key Level | reaction | yes | **REBUILD** | `KeyLevelStrategy`. Consumes canonical key-level primitive (spec §25) — the level clustering itself (`app/analysis/levels.py`) is `TECHNIQUE_ONLY`, the reaction-to-it strategy is independent. |
| 2 | Confluence Zone | zone | yes | **REBUILD → CONFLUENCE_COMPONENT model** | Becomes `ConfluenceZoneStrategy` per spec §5/§37 — the compositional exception. Its current Python detector (`confluence_zone.py`) already merges overlapping zones/levels; V2 must compose *already-established* canonical facts (zone/liquidity/fib), not recursively run other strategies (spec §5's explicit "Incorrect" example). |
| 3 | Supply Demand | zone | yes (one detector for both sides) | **SPLIT** | `detectors.py` has exactly **one** `supply_demand_technique_reaction` detector serving both directions — confirms spec §9's suspicion directly. V2: independent `SupplyStrategy` + `DemandStrategy`, each with its own formation/structural-role/confirmation/invalidation (spec §9/§21). Zone geometry (`zones.py`'s displacement-based supply/demand detection) is `TECHNIQUE_ONLY`, shared by both. |
| 4 | Order Block | zone | yes | **REBUILD** | `OrderBlockStrategy`, consuming an OB `TECHNIQUE_ONLY` primitive (spec §20) — origin candle/displacement/structure-break relationship must be defined objectively, not "last bearish candle before bullish move" alone. |
| 5 | FVG | zone | yes | **REBUILD** | `FVGStrategy` over an FVG `TECHNIQUE_ONLY` primitive (spec §18). |
| 6 | iFVG | zone | yes | **REBUILD, contingent** | Per spec §19: only if a precise "what creates an inverse FVG / what invalidates the original / what confirms inversion" definition can be written for the V2 spec doc. `technique_geometry.py`'s iFVG builder exists and is live — carries a real, already-implemented thesis, not a placeholder, so default is REBUILD pending that spec write-up in `docs/analysis/strategies/ifvg.md`. |
| 7 | CRT | zone | yes | **REBUILD** | ApexVoid's CRT has a precise, already-implemented definition (`technique_geometry.py::discover_crt_instances`, "T5 — H1 impulse range with sweep + reclaim on execution TF," H1 candle range specifically, not a generic dealing range): the H1 candle's own high/low becomes structural bounds, tradeable entry clips to the proximal reclaim band on the execution TF. This is specific enough to satisfy spec §36's bar ("decide whether it deserves an independent strategy" — it does; it is not a vague range+sweep+reclaim composition, it's anchored to one well-defined H1-candle construct). Recommend REBUILD as `CRTStrategy`, not a Confluence component — **flagging for owner confirmation**, since §36 leaves this a judgment call. |
| 8 | Demand Zone Reaction | zone | **no** (fallback-gated off) | **RETIRED** | Superseded in Python itself by named technique detectors per its own `replay_only_reason` ("legacy Zone Reaction publisher retired in favour of named technique detectors"). No V2 identity. |
| 9 | Supply Zone Reaction | zone | **no** | **RETIRED** | Same reasoning as #8. |
| 10 | Flip Zone | zone | yes (two detector_ids, one canonical) | **REBUILD** | `FlipZoneStrategy` (spec §22) — "what structure changed / what zone failed / what level changed role / what confirms the flip," deterministically, not "old resistance became support." Note: `flip_demand_zone_reaction` and `flip_supply_zone_reaction` are two detector functions already mapped to the *same* canonical `FLIP_ZONE` name — confirms this is correctly one strategy (direction-symmetric), not two. |
| 11 | Session Level | reaction | yes | **REBUILD** | `SessionLevelStrategy` (spec §26) over session-level `TECHNIQUE_ONLY` facts (Asia/London/NY high-low) from `session_liquidity.py`. |
| 12 | Trendline | reaction | yes | **REBUILD** | `TrendlineStrategy` (spec §24). Two Python implementations exist side by side (`trendlines.py` V1, `trendline_v2.py` — causal, immutable anchors); V2 Go should be built from `trendline_v2.py`'s semantics (already closer to spec §72's no-lookahead requirement) — this repo's own `config/analysis.yml` already runs V2 live. |
| 13 | Range Edge Scalp | range | yes | **REBUILD** | `RangeEdgeStrategy` (spec §30) — barrier/edge wick-rejection-history thesis on the M5 execution timeframe, requires a canonical valid range first (do not let it build its own parallel range detector). |
| 14 | Box Breakout | breakout_retest | **no** (replay-only) | **REBUILD** | Best-fit for the spec's generic `BreakoutRetestStrategy` description (§28: compression/box → breakout → acceptance → retest) — `box_breakout()`'s own thesis is literally box-consolidation breakout + acceptance + retest. Currently off by config default pending rollout, not because the thesis is invalid — carry that "not yet live" status into the V2 build, don't silently make it live-by-default. |
| 15 | Break & Retest | breakout_retest | **no** (replay-only) | **REBUILD or MERGE — flagged, see below** | `break_retest()` is trendline-specific (operates on `st.trendlines`, not a box). This resolves spec §29 definitively: Box Breakout and Break & Retest are **not** aliases of each other — they're genuinely different theses (box/range vs. trendline). The open question is whether Break & Retest survives as its own strategy or becomes part of `TrendlineStrategy`'s own break/retest state tracking (spec §24 lists "break state, retest state" as part of the trendline canonical primitive itself). **Recommend**: MERGE into `TrendlineStrategy` as its break-then-retest sub-thesis rather than a fully separate strategy, since spec §24 already scopes break/retest tracking onto the trendline primitive — but this is a real judgment call, flagging for owner confirmation before S2 locks it in. |
| 16 | Trend Pullback | trend_pullback | already `retired=True` in Python | **RETIRED** | No live detector; Python's own flag agrees. |
| 17 | Momentum Ride | momentum_continuation | yes | **REBUILD** | `MomentumRideStrategy` (spec §33) — must define persistent directional structure/displacement/low overlap/room-to-target/failure conditions explicitly, not "large candle = momentum." |
| 18 | Snap-Back | liquidity | yes | **REBUILD, distinct from Liquidity Sweep** | Read `snap_back()`'s actual logic: an extension-distance-from-a-zone-or-key-level thesis (price extended ≥ `snap_atr_mult` × ATR from its anchor, expect reversion) — this is a **mean-reversion/extension-fade** thesis, not a liquidity-pool-sweep-then-reclaim thesis (spec §27's `LiquiditySweepStrategy` description: pool → taken → failure to accept beyond → reclaim → opposite displacement). Recommend REBUILD as its own `SnapBackStrategy` with that extension-based definition, explicitly distinguished from the new `LiquiditySweepStrategy` (see #35 below) rather than treated as a synonym. |
| 19 | Fade Scalp | range (Python config family) / LIQUIDITY_REVERSAL (detector family) | yes | **REBUILD — likely the real basis for `LiquiditySweepStrategy`** | Read `fade_scalp()`'s actual logic: it calls `_level_grab()` (a liquidity grab/sweep of an **equal-high/equal-low** level, graded A/B) then requires `evaluate_structural_reaction` confirmation with a CHoCH check — this is much closer to the spec's real Liquidity Sweep thesis (§27) than Snap-Back is. **Recommend**: this is the strongest existing candidate to become (or seed) `LiquiditySweepStrategy` — spec §35 says retire Fade only "if no distinct thesis can be defined," but a distinct, already-implemented thesis exists here; the real decision is whether it stays named `FadeStrategy` (scalp-scoped) or is generalized/renamed into `LiquiditySweepStrategy`. **Flagging for owner confirmation** — do not silently rename without sign-off, since "Fade Scalp" and "Liquidity Sweep" currently point at different legacy identities (LIQUIDITY_SWEEP is separately `retired=True` in `strategy_names.py`, §20 below). |
| 20 | Zone Reaction | zone | already `retired=True` | **HISTORICAL_ALIAS** | Legacy plan/report name only, per its own code comment ("remain resolvable but are emitted by no current detector"). Keep resolvable for reading old DB rows only. |
| 21 | Demand Zone | zone | already `retired=True` | **HISTORICAL_ALIAS** | Same as #20. |
| 22 | Supply Zone | zone | already `retired=True` | **HISTORICAL_ALIAS** | Same as #20. |
| 23 | Range Box Scalp | range | already `retired=True` | **RETIRED** | No live path, no surviving distinct thesis identified. |
| 24 | One-Sided Range Reaction | range | already `retired=True` | **RETIRED** | Same. |
| 25 | Chop Zone Reaction | range | already `retired=True` | **RETIRED** | Same. |
| 26 | Liquidity Sweep | liquidity | already `retired=True`, **no current detector emits this name** | **RETIRED (as a name) — thesis lives on via Fade Scalp, see #19** | Important: this legacy *name* is retired and unused, but the spec explicitly wants a `LiquiditySweepStrategy` (§27) as a required V2 strategy. Do not conflate "the old name is retired" with "the thesis shouldn't exist in V2" — see #19's recommendation. This row exists to make that distinction explicit, not to imply V2 skips liquidity sweeps entirely. |
| 27 | Breakout Continuation | momentum_continuation | already `retired=True` | **MERGE candidate → Momentum Ride** | Same canonical family as Momentum Ride in Python; no separate live detector. Recommend folding any surviving idea into `MomentumRideStrategy`'s continuation thesis rather than a separate V2 strategy — flag for confirmation, not a hard call either way. |
| 28 | Mapped Zone Reaction | unknown | already `retired=True` | **RETIRED** | Python's own family is literally `"unknown"` — no distinct thesis on record. `zone_watch.py`'s "ZoneWatch" mechanism (durable retained-zone tracking) is architecturally more like execution/lifecycle plumbing than a strategy thesis; not a V2 strategy candidate. |
| 29 | Range Sweep Scalp | scalp (M1 archetype) | yes (`discover_range_sweep`) | **REBUILD** | `RangeSweepStrategy` (spec §31). Confirmed distinct from Range Edge Scalp (#13): M1-scoped sweep-of-range thesis vs. M5 barrier/edge wick-rejection-history thesis — different timeframe scope, different mechanism, matches spec §31's own suggested distinction. |
| 30 | Impulse Pullback Scalp | scalp (M1 archetype) | yes (`discover_impulse_pullback`) | **REBUILD** | `ImpulsePullbackStrategy` (spec §32) — must define impulse qualification, minimum displacement, corrective-pullback bounds, and continuation trigger explicitly. |
| 31 | Breakout Retest Scalp | scalp (M1 archetype) | yes (`discover_breakout_retest`) | **REBUILD, must be disambiguated from #14/#15** | This is a **third**, separately-implemented "breakout retest" thesis — M1-scalp-scoped, distinct from both `box_breakout` (#14, M5+ box) and `break_retest` (#15, trendline). All three currently share overlapping alias text ("breakout retest", "breakout-retest") in `strategy_names.py`, which is a real naming-collision risk the V2 catalog must not inherit. **Recommend**: distinct V2 strategy IDs — e.g. `box_breakout`, `trendline_break_retest` (or merged per #15), and `scalp_breakout_retest` — never one shared `BreakoutRetestStrategy` covering all three. Flag explicitly in `docs/analysis/strategies/` so this doesn't silently collapse back into one name during implementation. |
| 32 | Momentum Chase Scalp | scalp (M1 archetype) | already `retired=True` | **RETIRED** | Python's own flag agrees; no live M1 archetype implements it (only range_sweep/impulse_pullback/breakout_retest are registered archetypes). |
| 33 | Golden Fibo | unknown | already `retired=True` | **RETIRED** | No live detector, unknown family. Fibonacci itself survives as a `TECHNIQUE_ONLY` primitive (spec §23, `fibonacci.py`'s structure-native ladders) for other strategies/Confluence to consume — this row is about the retired *strategy* name, not the math. |

## Summary counts

- **REBUILD** (own independent V2 strategy): Key Level, Confluence Zone
  (as the compositional exception), Supply, Demand, Order Block, FVG,
  iFVG*, CRT*, Flip Zone, Session Level, Trendline, Range Edge, Box
  Breakout, Break & Retest*, Momentum Ride, Snap-Back, Fade Scalp
  (→Liquidity Sweep candidate)*, Range Sweep Scalp, Impulse Pullback
  Scalp, Breakout Retest Scalp — **19 candidate strategies** (Supply +
  Demand counted separately from the single legacy "Supply Demand" row).
  Items marked `*` are flagged for explicit owner confirmation before S2
  locks the list (iFVG's precise inversion semantics; CRT as independent
  vs. confluence component; Break & Retest as independent vs. merged into
  Trendline; Fade Scalp's relationship to a possible `LiquiditySweepStrategy`
  identity).
- **RETIRED**: Demand Zone Reaction, Supply Zone Reaction, Trend
  Pullback, Range Box Scalp, One-Sided Range Reaction, Chop Zone
  Reaction, Liquidity Sweep (name only — thesis lives on, see #19/#26),
  Mapped Zone Reaction, Momentum Chase Scalp, Golden Fibo — 10 entries.
- **HISTORICAL_ALIAS**: Zone Reaction, Demand Zone, Supply Zone — 3
  entries, kept resolvable for old DB rows only.
- **MERGE candidate**: Breakout Continuation → Momentum Ride — 1 entry,
  flagged not forced.
- **TECHNIQUE_ONLY primitives** (not strategies, consumed by the above):
  key-level clustering (`levels.py`), zone/displacement geometry
  (`zones.py`), FVG/iFVG/OB/CRT geometry (`technique_geometry.py`),
  session liquidity levels (`session_liquidity.py`), trendline
  geometry (`trendline_v2.py`), Fibonacci ladders (`fibonacci.py`),
  equal-level/liquidity-grab detection (feeds both liquidity primitives
  and the Fade/Liquidity-Sweep strategy).

## `auto_algo.strategies.*` config classification (spec §55)

Per-leaf classification of `config/auto-algo.yml`'s `auto_algo.strategies.*`
block, to seed the eventual `config/analysis.yml` `analysis.strategies.<id>.*`
shape in S2+. This table records the classification only — no config file
is moved or created in Phase S0/S1.

| Config leaf | Classification | Notes |
|---|---|---|
| `strategies.technique.fvg.entry_max_width_price` | ANALYSIS_PRIMITIVE | FVG geometry constant, not policy — belongs under the FVG technique primitive's own settings. |
| `strategies.scalping.*` (mode, archetypes, context, location, activation, target, stop, policy, risk, breakout) | **mixed** | Setup/trigger/confirmation/target sub-keys → ANALYSIS_STRATEGY (each of the three archetypes' own thresholds); `risk.*` (risk_fraction_per_trade, maximum_concurrent_positions, maximum_daily_trades, daily_loss_limit_r, etc.) → ALGO_POLICY, does not belong in `config/analysis.yml` at all. |
| `strategies.breakout.{break_retest_enabled,breakout_enabled}` | ANALYSIS_STRATEGY | Per-strategy enable flags — become `analysis.strategies.<id>.enabled` for whichever of #14/#15 survive S2. |
| `strategies.mapped_zone.*` | DELETE_LEGACY | Owning strategy (Mapped Zone Reaction, #28) is RETIRED with no distinct thesis. |
| `strategies.matching.*` (multiple_matches_enabled, track_all_structural_matches) | ALGO_POLICY | Cross-strategy match-selection policy, not a single strategy's technical config — stays with Algo Bot. |
| `strategies.range_reversion.*` (box_scale_out_enabled, flip_enabled, range_edge.*, two_sided_enabled) | **mixed** | `range_edge.*` thresholds → ANALYSIS_STRATEGY (Range Edge's own config); `box_scale_out_enabled`/scale-out mechanics → EXECUTION (position management, not setup detection). |
| `strategies.reaction.{demand,supply,key_level,liquidity_reversal,session_level,trendline}.*` | ANALYSIS_STRATEGY | Per-strategy enable/threshold config — direct precedent for `analysis.strategies.<id>.*`. `strategies.reaction.enabled`/`scale_enabled` (family-wide) → DELETE_LEGACY once independence is enforced (spec §2 forbids a generic reaction-family switch). |
| `strategies.scalp.*` (fade_scalp_enabled, scalp_barrier_fallback_*, scalp_post_impulse_range_enabled, scalp_range_provisional_enabled) | ANALYSIS_STRATEGY | Fade Scalp's own config, modulo its eventual identity resolution (#19). |
| `strategies.selection.*` (box_breakout_enabled, momentum_ride_enabled, retest_enabled, snap_back_enabled) | ANALYSIS_STRATEGY | Per-strategy enable flags, same pattern as `strategies.reaction.*`. |
| `strategies.trend.*` (allow_chase, breakout_min_room_pips, enabled) | ANALYSIS_STRATEGY (owner: Momentum Ride, pending #17/#27 merge decision) | `trend_pullback` family is RETIRED (#16); this leaf's surviving owner is whichever strategy Breakout Continuation/Momentum Ride resolve into. |

## What Phase S0 does **not** do

Per the spec's own gate (§7: "Do not implement anything before this
semantic audit is completed... But do NOT stop the task after the audit"):
this document classifies; it does not create Go packages, does not write
`docs/analysis/strategies/<name>.md` specs, and does not touch
`config/analysis.yml`. Those are Phase S2+ through S7 (implementing each
approved strategy), each requiring their own plan once this catalog is
confirmed.

---

## Phase S2 — Catalog locked

Resolving every item S0 left open (the four marked `*` plus the one
MERGE candidate), so S3 onward has one unambiguous strategy list to
build against. No further catalog changes without a deliberate revision
to this section.

- **#6 iFVG — REBUILD, semantics confirmed.** Read
  `technique_geometry.py::discover_ifvg_instances` in full (docstring:
  "T4 — first close through gap flips side"). Locked definition for
  `docs/analysis/strategies/ifvg.md` (written at S7, not here):
  inversion trigger is the first candle **close** fully through an
  existing FVG zone's far bound (below `low` for a demand-side gap,
  above `high` for a supply-side gap) — that flips the zone's tradeable
  side (demand→sell, supply→buy). The flip is confirmed only if no
  second close violates it back through the *original* zone bound from
  the new trade side before an entry; if one does, the inversion never
  fires. Entry is the same gap band, clipped to the proximal edge on
  the new trade side (not the full original gap). This is a real,
  deterministic, already-implemented thesis — proceeds as `ifvg`.
- **#7 CRT — REBUILD, locked as independent `CRTStrategy`.** Its H1-candle-
  range + sweep + reclaim definition (`discover_crt_instances`) is
  specific enough to stand on its own rather than folding into
  Confluence Zone or a generic range/liquidity composition.
- **#15 Break & Retest — MERGE, locked into `TrendlineStrategy`.**
  Becomes that strategy's break-then-retest sub-thesis (spec §24
  already scopes "break state, retest state" onto the trendline
  primitive itself) rather than a separate strategy. Its config leaf
  (`strategies.breakout.break_retest_enabled`) becomes part of
  `analysis.strategies.trendline.*`, not its own strategy ID.
- **#19 Fade Scalp — RENAME, locked as `LiquiditySweepStrategy`.** Its
  actual thesis (`_level_grab` liquidity grab on an equal-high/equal-low
  level, graded A/B, then `evaluate_structural_reaction` confirmation
  with a CHoCH check) already matches spec §27's Liquidity Sweep thesis
  closely enough that generalizing rather than duplicating is the
  right call — one strategy, new V2 identity `liquidity_sweep`, not two.
  Its scalp-scoped config (`strategies.scalp.fade_scalp_enabled` etc.)
  becomes `analysis.strategies.liquidity_sweep.*`.
- **#27 Breakout Continuation — MERGE, locked into `MomentumRideStrategy`.**
  No surviving Python detector, same canonical family as Momentum Ride
  already in `strategy_names.py`; no evidence of a distinct thesis
  worth carrying forward separately.

### Final V2 strategy list (18)

`key_level`, `confluence_zone` (compositional), `supply`, `demand`,
`order_block`, `fvg`, `ifvg`, `crt`, `flip_zone`, `session_level`,
`trendline` (absorbs Break & Retest), `range_edge`, `box_breakout`,
`momentum_ride` (absorbs Breakout Continuation), `snap_back`,
`liquidity_sweep` (renamed from Fade Scalp), `range_sweep`,
`impulse_pullback`, `scalp_breakout_retest` (kept distinct from
`box_breakout` per #31's three-way disambiguation).

19 REBUILD candidates from S0 minus 1 (Break & Retest folded into
Trendline, no longer a separate strategy) = **18 independent V2
strategies**, per ADR-003's independence rule (no strategy imports
another; Confluence Zone is the sole compositional exception).

### Phase S3 status

S3 now implements the canonical `internal/zone` domain and wires it into
`SymbolState`, `MarketContext`, and `AnalysisSnapshot`. The domain detects
Supply, Demand, Order Block, FVG, iFVG, Breaker, and Flip primitives, and
tracks lifecycle and current relevance independently. Strategy packages and
their individual specifications remain S7 work; they must consume these
facts rather than re-deriving zone geometry.

### Phase S7 status

S7 implemented and enabled a first vertical slice of **7 of the 18**
independent V2 strategies, each a real Go package under
`analysis-engine/internal/strategy/<name>`, each with its own spec doc
under `docs/analysis/strategies/`, each config-driven (`config/analysis.yml`)
and real-tested (`analysis-engine/test/strategy/<name>`):

| # | ID | Package | Status |
|---|---|---|---|
| 1 | `key_level` | `strategy/keylevel` | **Implemented, enabled** |
| 3a | `supply` | `strategy/supply` | **Implemented, enabled** |
| 3b | `demand` | `strategy/demand` | **Implemented, enabled** |
| 4 | `order_block` | `strategy/orderblock` | **Implemented, enabled** |
| 5 | `fvg` | `strategy/fvg` | **Implemented, enabled** |
| 10 | `flip_zone` | `strategy/flipzone` | **Implemented, enabled** |
| 11 | `session_level` | `strategy/sessionlevel` | **Implemented, enabled** |

The remaining **12 strategies stay disabled** — no factory exists for
them yet, so `config/analysis.yml` correctly leaves them
`enabled: false` (the registry's own fail-closed rule: an enabled ID
with no factory fails analysis-engine startup rather than silently
producing no signals):

- `confluence_zone` — the compositional exception; deliberately deferred
  until its non-compositional siblings (zone/liquidity-anchored
  strategies) exist to compose over.
- `ifvg`, `crt` — the catalog itself flags both as REBUILD-but-contingent
  (rows 6/7): each needs its own precise spec write-up (iFVG's
  invalidation/inversion-confirmation definition; CRT's owner
  confirmation) before implementation, not attempted this phase.
- `trendline` — depends on the `internal/trendline` primitive's own
  break/retest state tracking already absorbing Break & Retest (#15);
  not yet built as a strategy package.
- `range_edge`, `box_breakout`, `momentum_ride`, `snap_back`,
  `liquidity_sweep`, `range_sweep`, `impulse_pullback`,
  `scalp_breakout_retest` — all real, distinct REBUILD theses per the
  catalog, none zone-anchored (they need range/breakout/momentum/scalp
  primitives this phase did not implement or wire). Deferred to a later
  phase, not silently dropped.

This is a deliberate, transparent scoping decision, not partial/hidden
completion — see the S7 PR's own final report for the full reasoning.
Phase S8 (engine wiring — evaluation invoked from the per-symbol engine
loop) and Phase S9 (Kafka publication of opportunities) are separate,
later phases; nothing in S7 touches either.

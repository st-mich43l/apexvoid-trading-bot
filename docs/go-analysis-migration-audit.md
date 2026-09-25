# Go Analysis Engine Migration — Audit (Stage 0)

Source prompt: `apexvoid-bot-prompts/rebuild-analysis-engine.md`.
Scope: read the Python analysis stack, trace real runtime callers, and
document the computation graph and every duplicate calculation *before*
writing Go code, per that prompt's Sections 2–3 and 34.

This is not a line-by-line read of all ~28k lines in scope. The pure-math /
structure files (the Stage 1–5 targets) were read in full. `detectors.py`,
`scanner.py` and `worker.py` (3.8k / 3.6k / 9.0k lines) were traced by
grepping every call site of each core computation (`atr_series`,
`find_swings`, `key_levels`, `displacement`, `supply_demand`,
`order_blocks`, `mark_mitigation`, `structure_breaks`, `market_structure`,
`liquidity_pools`/`liquidity_grabs`, `dealing_range`) rather than read
end to end — sufficient to answer "who computes this and from where," which
is what Section 3 asks for. Their detector-level *strategy* logic (the
actual trading rules) is unaudited and is explicitly out of scope until
Stage 6 (detector migration) in the source prompt's own staging.

## 1. The canonical pipeline (already exists, mostly correct)

`app/analysis/engine.py::analyze()` → `_analyze_tf()` is the real single
per-timeframe computation the prompt wants preserved in Go:

```
_analyze_tf(df, settings):
  atr        = atr_series(df, settings.atr_length)          # ONE call
  swings     = find_swings(df, ..., atr, as_of=...)           # uses atr
  structure  = market_structure(swings)
  breaks     = structure_breaks(swings, df, causal=..., fractal_n=...)
  trendlines = find_trendlines(swings, df, atr, nested_cfg)   # uses atr
  legs       = displacement(df, atr, ...)                     # uses atr
  zones      = supply_demand/order_blocks/fvg/flip_zones(...) # uses legs/atr
  zones      = mark_mitigation(...) -> merge_zones -> score_zones
  liquidity  = liquidity_pools/liquidity_grabs(swings, df)
  key_levels = key_levels(swings, atr, ...)
  momentum   = momentum_state(df, atr, ...)
  dealing_range, regime, scalp_barriers, fib_levels, session_levels
  -> TimeframeAnalysis(df, atr, swings, structure, breaks, key_levels,
       legs, supply_demand_zones, order_blocks, flip_zones, fvg_zones,
       zones, liquidity_pools, liquidity_grabs, momentum, momentum_state,
       fib_levels, nearest_fib, session_levels, dealing_range, regime,
       trendlines, box_break, scalp_barriers, scalp_range, ...)
```

`analyze()` runs this once per timeframe, then `_attach_technique_instances`
(technique_geometry.py) and `_apply_mtf_zone_scores` run once over the
resulting `per_tf` dict. `TimeframeAnalysis` is already, field-for-field,
very close to the `TimeframeState` sketched in the source prompt's Section
5 — the Go port should treat it as the reference shape, not redesign it.

`AnalysisContext{frames, per_tf, htf_bias, dealing_range, regime}` is the
existing analogue of the prompt's `SymbolState`. `DetectionContext` (in
`detectors.py`) wraps it again for detector consumption (`frames`,
`indicators`, `structures`, `htf_bias`, `settings`, `spot_price`, ...) —
this second wrapper is where the first duplicate below is introduced.

`trendlines.py::trendlines()` is a single public entrypoint that
internally dispatches to `_trendlines_v1` or `trendline_v2.py`'s
`build_causal_trendlines` by config (`tl_version`) — **not** a duplicate,
despite two files; noted so it isn't mistaken for one.

## 2. Duplicate / divergent computations found (live, in the production path)

### 2.1 Two different ATR formulas — CRITICAL, not just duplicate effort

- `app/analysis/math_utils.py::atr_series()` — simple rolling mean of true
  range (`tr.rolling(length, min_periods=1).mean()`). This is the one
  `_analyze_tf` uses, and by extension what every zone/swing/structure/
  liquidity/momentum/dealing-range/regime/scalp-range/trendline
  computation in the canonical pipeline is built on. 19 call sites across
  `app/analysis/*` and `app/autotrade/*` (engine.py x3, zones.py, swings.py,
  momentum.py x2, liquidity.py x2, structure.py x2, worker.py x5, trend.py
  x2, scale_context.py, setups_report.py, zone_execution_cutover.py,
  scalping/lab_event_builder.py).
- `app/analysis/indicators.py::atr()` — thin wrapper over
  `pandas_ta.atr(..., mamode="rma")`, i.e. **Wilder's smoothing**, a
  genuinely different algorithm, not a rounding variant. Two call sites:
  `detectors.py::_indicator_set()` (feeds `IndicatorSet.atr`, read via the
  `_atr(ind)` helper at **12 call sites in `detectors.py`** — zone-band
  widths, key-level reaction bands, stop-distance math, entry validity —
  plus 5 more in `technique_detectors.py`) and `worker.py:7688` (a
  separate M1 ATR read).

**Measured live divergence** (XAU M5, 300 real closed bars, length 14):
simple-mean ATR = 4.4214, Wilder RMA ATR = 4.7411 — **6.7% apart** at that
snapshot, and the two series diverge bar-to-bar, not just at one point,
because they're different filters over the same true-range series.

**Why this matters**: `_analyze_tf` builds every zone/level/liquidity band
in `ctx.structures[tf]` using the simple-mean ATR. `detectors.py` then
independently re-derives band widths (e.g. `_key_level_reaction_band`) and
stop math using the Wilder-RMA ATR from `IndicatorSet`, for the *same*
timeframe, in the *same* detection pass. The two families of geometry
computed from the same candle window are not on the same ATR. This is
exactly the divergence class Section 3 asks to be found — it was not
previously documented anywhere in the codebase.

**Do not silently pick one during translation** (source prompt Section
17). Flag for the owner: which ATR is intended to be canonical. The
pervasiveness of `math_utils.atr_series` strongly suggests it, not the
`pandas_ta`/Wilder one, is the real canonical implementation and that
`detectors.py`'s `IndicatorSet.atr` is the accidental one — but that is a
product decision, not mine to make while porting.

### 2.2 `build_context()`'s `indicator_sets` — the duplication the source prompt already named

`app/analysis/detectors.py::build_context()`:

```python
analysis_ctx = analyze(frames, ...)                          # computes ATR internally, per tf
indicator_sets = {
  name: _indicator_set(df, settings.atr_length)               # RECOMPUTES ATR, different formula (2.1)
  for name, df in frames.items()
}
```

Confirmed present, unchanged. This is not just wasted CPU — per 2.1, it
also silently swaps in a different ATR value for everything downstream
that reads `ctx.indicators[tf]` instead of `ctx.structures[tf]`.

### 2.3 `app/analysis/structure.py` — a second, parallel structure/zone stack

`structure.py` ("Pure structure layer plus compatibility helpers") defines
its own `swings()`, `key_levels()`, `order_blocks()`, `fvg()`,
`flip_zones()`, `entry_zone()`, `find_retest()`, `equal_highs_lows()`. Each
of these, given a raw `df`, independently recomputes ATR
(`atr_series(df)`, unparameterized — always `length=14` regardless of
`settings.atr_length`) and swings (`find_swings(df, 2, 0.0, 0.0, atr)`,
fixed params) from scratch, then calls into the *same* `zones.py`
primitives `_analyze_tf` already called for that timeframe.

`engine.py` itself only imports `market_structure` and `structure_breaks`
from this module (which *are* the canonical break/bias logic, correctly
shared — not a duplicate). The rest of `structure.py` is not used by the
canonical pipeline at all. It is used by:

```
app/analysis/detectors.py imports: entry_zone, equal_highs_lows,
  find_retest, fvg, key_levels, market_structure, order_blocks, swings
```

Confirmed live call sites (not just imports): `find_retest(...)` at
`detectors.py:1970,2125` and `entry_zone(...)` at
`detectors.py:2239,2383,2582` — e.g. the Snap-Back detector's fallback
path, when the structural zone selection from `ctx.structures[tf]` comes
back empty, calls `entry_zone(df, nearest.price, direction, ...)`, which
internally does `order_blocks(df) + flip_zones(df) + fvg(df)` — a full
fresh order-block/FVG/flip-zone recompute from raw OHLC, discarding the
`order_blocks`/`fvg_zones` already sitting in `ctx.structures[tf]` for
that exact window, computed moments earlier in the same pass, with
`settings.atr_length` correctly threaded through instead of the hardcoded
14 this path uses.

This is the single largest "duplicate paths to remove" item in Section 3.
For the Go port: `structure.go`/equivalent should expose only
`MarketStructure`/`StructureBreaks` as their own functions (mirroring
`structure.py`'s two legitimately-shared ones); there is no Go analogue
needed for the rest of `structure.py` — detector logic that currently
falls back to it should be ported to read `TimeframeState` instead, which
is a *behavior-affecting* fix, not a pure translation, and should be
flagged to the owner rather than done silently (Section 17).

### 2.4 Scalping lane: a third, independent swing algorithm

`app/scalping/context.py::_swings_from_ohlc()` — "Lightweight swing marks
for dealing-range construction" — a bare fractal detector (`>=`/`<=`
against the full centered window, no zigzag/ATR filter), **not** the same
algorithm as `swings.py::find_swings`'s hybrid fractal+zigzag (different
tie-breaking: strict `<`/`>` against every other bar in
`find_swings`/`_fractal_candidates` vs `>=`/`<=` against the window
including itself here). Called fresh on every `build_scalp_context()` call
against the M15 (or M5 fallback) frame, feeding `dealing_range(swings,
price)` for the scalp context snapshot — independent of the swings
`_analyze_tf` already computed for that same M15/M5 window in the same
pass. `microstructure.py`'s own comment
(`m5_structure_flip_candidates`) confirms the team is already aware reuse
matters here ("no new M5 swing computation here") — this one function
just doesn't reuse it yet.

`engine.py::scalp_structure()` (a fourth, lighter recompute: fresh ATR +
`find_swings` + `key_levels` + `displacement` + `supply_demand`, "Build
scalp structure without the trendline/technique stack") has **no live
callers** found anywhere outside its own module — only a comment reference
in `microstructure.py`. Flagged as likely dead code; confirm with the
owner before deleting (do not delete Python during audit — Section 2).

`app/scalping/lab_event_builder.py` computes 3 separate ATR windows
(14/short/long) directly via `atr_series` — this file's own docstring says
"Offline-first... Redis dump is a short-window smoke helper only," i.e.
research/labeling tooling, not a live decision path. Out of scope for
migration (source prompt Section 28 — "offline notebooks/research
helpers" stay Python); listed here only for completeness.

### 2.5 `worker.py::_htf_zones` and friends — self-documented, still real duplication

`app/autotrade/worker.py` independently recomputes ATR → `displacement` →
`supply_demand` → `mark_mitigation` three times (`_htf_zones` around line
1275; a key-levels variant around line 1363; a third `tf_frame` variant
around line 1408), each a fresh `atr_series` + primitives call over a raw
HTF frame. Unlike 2.1/2.3/2.4, this one is *already* self-documented in
the code:

> "Independent of gate.py/trend.py's own M1 legs - this is the one place
> the shared analysis stack enters the autotrade path, and it enters only
> as a veto input, never as a signal."

I.e. the author knows this is a separate computation and has an
architectural reason (autotrade's veto path doesn't have the scanner's
freshly-built `AnalysisContext` in scope). Still duplicate CPU work and
still a place where `worker.py`'s HTF zones and the scanner's HTF zones
for the *same* closed candle could disagree if `atr_length` or any zone
setting drifts between the two call sites — worth collapsing once
`SymbolState` exists and is reachable from the autotrade path, per the
source prompt's Section 21 ("Where worker logic independently computes...
replace those reads with the canonical Go-produced state when cutover
occurs"). Not a translation-time fix; a Stage 5+ architecture change.

### 2.6 `app/analysis/candle_geometry.py` vs `app/scalping/math_features.py`

`candle_geometry.py`'s own docstring already discloses and justifies a
second `CandleGeometry`/`candle_geometry()` in
`app.scalping.math_features` ("a different consumer with a different shape
of output") and states `m1_trigger.py`'s own `_BarGeometry` was
*refactored* to build on this module rather than duplicate it a third
time. Treat as intentional; note it for Go package boundaries
(`internal/market/candle.go` likely needs two geometry views, matching
scanner-side and scalp-side consumers, not one).

### 2.7 Confirmed NOT duplicated (checked, ruled out)

Six `app/autotrade/*` files named in the audit scope
(`structural_barriers.py`, `structural_target_room.py`,
`range_context.py`, `range_lifecycle.py`, `entry_activation.py`,
`execution_confirmation.py`) were grepped for direct calls to
`atr_series`/`find_swings`/`key_levels`/`displacement`/`supply_demand`/
`order_blocks`/`mark_mitigation`/`structure_breaks`/`market_structure`.
None call any of them — they are pure consumers of already-built
zones/analysis (`StructuralBarrier` from zones, opposing-entry evaluation,
activation gates), not independent recomputation. No action needed for
these beyond the normal "read `TimeframeState` instead of `ctx.structures`
once Go is authoritative" cutover.

## 3. Computation table

| Computation | Current implementation(s) | Current callers | Canonical future owner | Duplicate paths to remove | Parity test required |
|---|---|---|---|---|---|
| True range / ATR (simple) | `math_utils.true_range`, `math_utils.atr_series` | `_analyze_tf` (x3), `zones.py`, `swings.py`, `momentum.py` (x2), `liquidity.py` (x2), `structure.py` (x2), `worker.py` (x5), `trend.py` (x2), `scale_context.py`, `setups_report.py`, `zone_execution_cutover.py` | `indicator/atr.go` | — (this is the canonical one) | Yes — exact-value fixture across warmup/short-window/flat-market cases |
| True range / ATR (Wilder RMA via pandas_ta) | `indicators.atr` | `detectors._indicator_set` (→ 12+5 `_atr(ind)` reads), `worker.py:7688` | **Decision needed** — likely retire in favor of the simple one, or promote to canonical; not mine to decide | `indicators.py` itself, once decided | Yes, both directions — must prove which one detector geometry ends up on today |
| Swings (hybrid fractal+zigzag) | `swings.find_swings` (+`as_of` causal mode) | `_analyze_tf`, `structure.py::swings()`/`order_blocks()`, `engine.scalp_structure` | `structure/swing.go` | `structure.py`'s independent re-derivation (2.3) | Yes — causal vs live, warmup, flat market |
| Swings (bare fractal, scalp) | `scalping/context.py::_swings_from_ohlc` | `build_scalp_context` → `dealing_range` | fold into `structure/swing.go` (project a lightweight view) or keep as an explicit, documented cheaper variant | Yes, if the owner wants one swing algorithm | Yes if unified; else document as intentionally distinct |
| Market structure (bias) / structure breaks (BOS/CHoCH) | `structure.market_structure`, `structure.structure_breaks` | `_analyze_tf`, `trend.py`, `scale_context.py` | `structure/market_structure.go`, `structure/break.go` | None found — already single-owner | Yes — causal vs lookahead |
| Key levels | `levels.key_levels` (clustering + wick-touch), `structure.key_levels` (wrapper, fixed ATR=14) | `_analyze_tf`, `engine.scalp_structure`, `structure.py` wrapper (detectors.py fallback), `worker.py` (x1) | `structure/level.go` | `structure.py`'s wrapper (2.3) | Yes — clustering, round-number join, wick-touch episodes |
| Displacement | `zones.displacement` | `_analyze_tf`, `engine.scalp_structure`, `worker.py` (x3), `structure.order_blocks` | `technique/displacement.go` | worker.py's independent recompute (2.5, keep documented if not collapsed) | Yes |
| Supply/demand | `zones.supply_demand` | `_analyze_tf`, `engine.scalp_structure`, `worker.py` (x3) | `technique/supply_demand.go` | same as displacement | Yes |
| Order blocks | `zones.order_blocks`, `structure._legacy_order_blocks` (fallback) | `_analyze_tf`, `structure.order_blocks` (2.3) | `technique/order_block.go` | `structure.py` wrapper | Yes, incl. legacy fallback path |
| FVG / iFVG | `zones.fvg`, `technique_geometry.discover_ifvg_instances` | `_analyze_tf`, `structure.fvg` (2.3), technique layer | `technique/fvg.go`, `technique/ifvg.go` | `structure.py` wrapper | Yes |
| Breaker / flip zones | `zones.breaker_blocks`, `zones.flip_zones` | `_analyze_tf`, `structure.flip_zones` (2.3) | `technique/breaker.go`, `technique/flip_zone.go` | `structure.py` wrapper | Yes |
| Mitigation | `zones.mark_mitigation` | `_analyze_tf`, `engine.scalp_structure`, `worker.py` (x3) | folded into zone lifecycle in `technique/*` | worker.py (2.5) | Yes |
| Zone merge/score/reconcile | `zones.merge_zones`, `zones.score_zones`, `zones.reconcile_opposing`, `as_single_zones` | `_analyze_tf` only | `technique/geometry.go` | None found | Yes — reconcile circuit-breaker (dropped/aborted counters) |
| Liquidity pools/grabs | `liquidity.liquidity_pools`, `liquidity.liquidity_grabs` | `_analyze_tf`, `structure.equal_highs_lows` (2.3) | `structure/liquidity.go` | `structure.py` wrapper | Yes |
| Fibonacci | `fibonacci.fib_ladder`/`fib_from_swings`/`nearest_fib` | `_analyze_tf` | `technique/fibonacci.go` | None found | Yes |
| Momentum | `momentum.momentum`/`momentum_state` | `_analyze_tf` | `indicator/momentum.go` | None found | Yes |
| Dealing range | `dealing_range.dealing_range` | `_analyze_tf` (canonical swings), `scalping/context.py` (own swings, 2.4) | `regime/dealing_range.go` | scalping's own swing feed (2.4) | Yes |
| Regime | `regime.accepted_box_break`/`displacement_grade` | `_analyze_tf` | `regime/regime.go` | None found | Yes |
| Trendlines | `trendlines.trendlines` (v1) / `trendline_v2.build_causal_trendlines` (v2), dispatched internally by `tl_version` | `_analyze_tf` | `regime/trendline.go` (single entrypoint, versioned) | None — confirmed single entrypoint | Yes, both versions |
| Scalp structure/ranges | `scalp_ranges.build_scalp_structure(_detailed)` | `_analyze_tf` (barriers/range), `engine.scalp_structure` (own recompute — likely dead, 2.4) | `scalp/structure.go` | `engine.scalp_structure` if confirmed dead | Yes |
| Technique geometry/instances | `technique_geometry.collect_technique_instances` + friends | `_attach_technique_instances` (post-`per_tf` pass), `technique_detectors.py` | `technique/geometry.go` | None found beyond 2.1's ATR read via `_atr(ind)` in `technique_detectors.py` | Yes |
| MAD phase | `mad_phase.classify_mad_phase` + scoring/gating; Redis I/O mixed into the same module | `evaluate_mad_for_cycle`/`refresh_mad_for_symbol` (worker/scanner) | `mad/phase.go` (pure) + Redis I/O kept at the service boundary | Redis I/O currently inline in a "pure" analysis file — separate in Go per Section 9/23 | Yes — this is explicitly flagged as risky in the source prompt (Section 18); exact-parity critical |
| Candle geometry/displacement/rejection/sequences (V2 evidence) | `candle_geometry.py`, `candle_displacement.py`, `candle_evidence.py`, `candle_rejection.py`, `candle_sequences.py` | detector-level pattern scoring (not traced call-by-call; large surface, deferred to Stage 6) | `market/candle.go` + technique-family evidence packages | 2.6 (documented, intentional) | Yes |

## 4. What stays Python (confirmed, per source prompt Section 28)

- `app/scalping/lab_event_builder.py` and any other offline/`--dump`-driven
  research tooling under `app/scalping/` (research plane).
- Everything in `app/bot/`, `app/autotrade/delivery.py`, `setup_card.py`,
  Telegram formatting/command handling, owner DM, weekly reports, calendar
  sync, manual signal parsing (control plane).
- `app/autotrade/structural_barriers.py`, `structural_target_room.py`,
  `range_context.py`, `range_lifecycle.py`, `entry_activation.py`,
  `execution_confirmation.py` are consumers of analysis output, not
  computation sources (2.7) — they *will* need their Python read sites
  repointed at Go-produced state at cutover (Stage 8), but the decision
  logic in them is not itself a migration target under this prompt's
  scope ("decision engine," i.e. detectors/actionability, is — these are
  closer to execution-gating logic and were not named in Section 1's list).

## 5. Risky mathematical areas (Section 17 / 31 — exact parity required, no "improving" the formula)

1. **ATR** — two formulas live today (2.1); whichever becomes canonical,
   its Go port must match that Python implementation bit-for-bit within
   float tolerance, not "the more correct" Wilder/simple choice.
2. **MAD phase** — `mad_phase.py`, 1355 lines, already explicitly flagged
   in the source prompt (Section 18) as soft/research confluence that must
   never silently become a hard gate; Asia-range sealing, double-sweep
   handling, and stale-day handling are stateful (Redis-backed) and easy
   to get subtly wrong in a straight port.
3. **Swings** — `_fractal_candidates`' tie-breaking (`<`/`>` against every
   other bar in the window) vs the scalping lane's `_swings_from_ohlc`
   (`>=`/`<=` against the full window) are NOT the same rule; whichever
   Go implementation is used for which caller must match its Python
   original exactly, not be unified silently.
4. **Causal vs live structure** (`causal_structure` / `as_of` in
   `find_swings`, `causal` in `structure_breaks`) — confirmed real and
   threaded consistently through `_analyze_tf`; must be preserved exactly
   per Section 11.
5. **Zone reconciliation** (`zones.reconcile_opposing`) has a circuit
   breaker (dropped/aborted counters surfaced on `TimeframeAnalysis`) —
   non-obvious control flow, easy to port incorrectly without preserving
   the abort condition exactly.
6. **Trendline v1 vs v2** dispatch and touch/validation scoring
   (`trendlines.py` 498 lines + `trendline_v2.py` 634 lines) — large
   surface, not yet read function-by-function; flagged as a Stage 3/4 deep
   read, not audited beyond confirming the single-entrypoint dispatch.

## 6. Proposed first migration slice (per source prompt's own preference)

```
OHLC → ATR → Swings → Market Structure → Zones → AnalysisSnapshot
```

Scoped to `math_utils.py`, `indicators.py` (both ATR formulas, ported
side by side and clearly labeled — see 2.1), `candle_geometry.py`,
`swings.py`, `structure.py`'s two canonical functions only
(`market_structure`, `structure_breaks`), and `zones.py`'s core zone
primitives (`displacement`, `supply_demand`, `order_blocks`, `fvg`,
`flip_zones`, `mark_mitigation`, `merge_zones`, `score_zones`). Explicitly
excludes: technique geometry, MAD, trendlines, scalp ranges, regime,
liquidity, fibonacci, momentum, dealing range (Stage 3/4 per the source
prompt's own staging) and all of `structure.py`'s non-canonical wrapper
functions (2.3 — those are a duplication to *remove*, not port).

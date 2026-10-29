# Python → Go migration audit: the runtime pipeline contract

Written for: an engineer who has to decide whether a number printed by the Analysis Engine is the number the broker receives.
Read-only: nothing here changed a runtime path. Companion to [python-go-migration-forensic-audit.md](python-go-migration-forensic-audit.md).

The pipeline is **Redis bars → Go engine → Kafka → Python adapter → TradePlan V8 → cTrader executor → broker**. This file records, stage by
stage, what is transformed, who owns the result, and how far it is from the technical read the strategies describe. Line references are to
the audited tree (master `3597c13f`).

## 1. Stage table

| # | Stage | Owner | Input → output | Transformation that changes a number |
|---|-------|-------|----------------|---------------------------------------|
| 0 | Closed bars | Redis `bars:<SYM>:<TF>` | M1/M5/M15/H1 closed candles | none (the frozen Python windowed M5 150 / M15 250 / H1 400; Go retains up to 1,000 M5 bars for its V2 books and cuts the frozen windows for the legacy frame) |
| 1 | Context | Go `engine.rebuildContext` | candles → V2 structure, zones, liquidity, `LegacyFrame` per timeframe | three zone populations coexist (section 3); one canonical ATR (config: simple, 14) beside per-consumer legacy ATRs |
| 2 | Strategy | Go `strategy/*` | context → `Candidate{entry band, invalidation, targets, evidence, quality}` | the **technical geometry**: invalidation at the structure, targets at opposing liquidity or `fallback_target_r` |
| 3 | Enrichment | Go `worker.go:236-251` | candidate → `Technical` (ATR, MAD, bias, higher-timeframe biases, candle evidence), `Confluence`, `StopEnvelope` | `ATR` published is the **canonical** ATR (`worker.go:441`), not the detector ATR; `Confluence` is the detector's own when the strategy supplies one, else an engine-derived one (section 4); `StopEnvelope` = per-instrument floor/cap/desired (`stop_envelope.go`) |
| 4 | Lifecycle + publish | Go `opportunity.Book`, `OpportunityPublisher` | created / invalidated / expired transitions → Kafka | dedup by setup key; `id = opp_<hash>`; expiry |
| 5 | Adapter | Python `go_opportunity_policy.build_strategy_match` | Kafka envelope → `StrategyMatch` in Redis | see section 2 |
| 6 | Arbitration | Python `arbitration.py` | matches → one decision per symbol/direction | `quality_ranking: false` (config/analysis.yml:808, auto-algo.yml:43): order = executable first, freshness, id; quality, stars and structural quality read nowhere |
| 7a | Room gate | Python `structural_target_room` on Go's published barrier book | match + barriers → allow / hard block / fitted targets | hard rejection when the entry is inside an opposing barrier or has no room; see section 2b |
| 7 | Execution policy | Python `execution_policy.evaluate_execution_policy` | match → planned stop, route, targets | the **execution geometry** (section 5): the stop is Go's invalidation **widened to the instrument floor**; targets are **fixed R multiples of the executed stop**; XAU zone bands are widened/trimmed to 30-50 pips |
| 8 | Plan | Python `trade_plan_builder` | measured → `TradePlan V8` | targets absolute prices, `close_ratio`s sum to 1, breakeven after 1R, expiry re-anchored to publication time |
| 9 | Executor | C# `TradePlanRuntime` | plan → broker orders | not audited here beyond its inputs (read-only mandate); XAU risk-leg parity is pinned by `contracts/autotrade/xau-ladder-spec.json` |

## 2. The adapter (stage 5), rule by rule

`algo-bot/app/autotrade/go_opportunity_policy.py`. "Pure translation. Raises AdapterRejection rather than approximating."

1. **Allowlist.** `REVIEWED_SCOPES` must equal the Go catalog (16 ids); one row per strategy fixes `allowed_timeframes` (M5; `impulse_pullback` M1), `requires_reaction` and the required evidence-code prefixes.
2. **`requires_reaction`.** True for confluence_zone, supply, demand, order_block, fvg, ifvg, crt, flip_zone, session_level, trendline, range_edge. A *resting* zone opportunity (no `technical_context.confirmation`) is rejected `reaction_confirmation_unavailable`. `key_level` is not in this list because it only ever emits confirmed reactions.
3. **Recency.** The confirming bar may be at most `structural_reaction_lookback_bars - 1` bars before the observation bar.
4. **Evidence prefixes.** A match whose evidence codes carry none of its strategy's prefixes is rejected `strategy_evidence_mismatch`.
5. **Targets.** Each Go target becomes whole pips from the proximal edge (`round(distance / pip)`); a target under 1 pip rejects the match (`target_not_beyond_entry`). The farthest becomes `absolute_target_price`.
6. **Stop.** `go_invalidation_price = invalidation`; `stop_pips = |proximal - stop| / pip`.
7. **Bias (a source change).** `relation` = `with_bias` iff `technical_context.bias.direction == direction` (the Go **primary-timeframe V2 bias**); `htf_bias` = H1, else H4, else M15 `higher_timeframes` direction mapped to up/down, else neutral. The frozen Python used the legacy `_htf_bias` (first decided higher timeframe). Measured (1,451 hourly checkpoints, 11 captures): the legacy bias equals the frozen Python on 1,451/1,451; the adapter's `htf_bias` equals the frozen Python's on **887/1,451 (61.1%)**; the V2 primary bias equals Python's HTF bias on 916/1,451 (63.1%) and its M5 structure on 795/1,451 (54.8%). In 564 checkpoints the two sources disagree, often by direction (e.g. XAU: Python "down", adapter "up" at 147 checkpoints). Consumers: the `with_bias`/`counter_bias` tag, the conflict tie-break in `arbitration.py:236-244`, and the card text. No execution gate reads it in the audited configuration (`allow_counter_trend: true`; the counter-bias reasons in `execution_policy.py:291-292` are soft telemetry names).
8. **Confluence.** `confluence = confluence.selected_stars` (Go); the evidence-count fallback applies only to retained legacy events. `classify_tier(confluence)`: ≥3 → A (full risk), 2 → B (reduced), else C; `risk_multiplier_for_tier` follows.
9. **Quality** is copied (`quality_overall`, `quality_components`). It is read by `multi_match.py:65` (status representative only) and stored by `entry_overlap` (never used to decide). Nothing ranks on it.
10. **Thesis identity.** With a reaction, `zone_id` is the Go zone id; otherwise `catalog_id:round(mid / bucket)` with `bucket = max(0.5·ATR, 15 pips)`, so two opportunities of one strategy whose entry mids fall in one bucket are one thesis (incident 2026-09-29).
11. **Impulse Pullback ladder.** `scalp_target_r_multiples` = (1R, 2R) if the Go target offers ≥2R of the stop, (1R) if ≥1R, else none.
12. **Expiry.** `now >= expires_at` rejects. The plan's own expiry is later re-anchored to publication time (`trade_plan_builder.py:305` docstring).

## 2b. The room gate (stage 7) and the book it reads

Before the execution geometry is planned, `worker.py:1796-1860` runs `evaluate_structural_target_room` against `opposing_entries_for_go_match`: the barrier book Go publishes to Redis (`analysis:zone_book:<SYMBOL>`, `barriers`). The gate is a **hard** block for every tier except `level`: the planned entry inside an opposing barrier (`opposing_entry_contained` / `_overlap`), or no raw room in front of it (`opposing_barrier_no_target`, `opposing_major_no_room`); room under the cost floor rejects (`opposing_barrier_room_below_cost`). Technique and confluence strategies may overlap the *same* wall (`allow_same_wall_overlap`); a `counter_bias` match survives a soft rejection when its native room clears `minimum_capped_target_pips` (15), so the bias relation of section 2 rule 7 changes which setups survive.

Go builds the book from the **V2 zone state** of M5, M15 and H1 (`main.go:181` → `BuildZoneBook(snapshot.Zones)`, supply/demand kinds, `Score = strength`, every barrier `zone` tier). The frozen book pooled each timeframe's merged, HTF-boosted `zones`, dropped mitigated zones, tiered by score (`level`/`zone`/`major`), boosted by overlapping key levels/sessions/trendlines, and reconciled cross-side overlaps. Measured difference: audit §7.5 (71-77% smaller room, 1.3-5.3% vs 37.6% block proxy). In production (2-15 Oct logs) the family accounts for 3,415 distinct (setup, reason) rejections against 424 trades.

## 3. Three zone populations (stage 1)

| Population | Built by | Window | Fields | Read by |
|---|---|---|---|---|
| V2 canonical (`zone.Update`) | Go-native, hierarchical structure | retained history (up to 1,000 M5 bars) | kind, strength 0-1, relevance 0-3, layer, state (fresh/touched/partially_mitigated/invalidated) | the engine snapshot's zone state, hence the **Redis zone book and its barriers** (execution target room); M15/H1 `Context.Zones` |
| Legacy frame (`legacyread`, `techniquezone`) | faithful port of `engine._analyze_tf` | M5 150 / M15 250 / H1 400 | Python's own fields: touches, mitigated, score, reasons | every executable strategy (`ConfirmedTechnique`, key level, flip zone, fade scalp, …) |
| Production M5 `Context.Zones` (`techniqueZoneState`, `technique_zones.go:33`) | `techniquezone.TechniqueInstances` on **400** bars (`DefaultTechniqueZoneSettings`, `technique_zones.go:24`, not configurable) + V2 kinds that are not supply/demand/OB/FVG/iFVG | 400 bars | `Strength = 1`, `Relevance = Immediate`, `Layer = Intermediate`, `State ∈ {Fresh, Touched}` hard-coded; `LegacyScore` = the instance's own score (not the frozen zone score) | resting candidates of supply/demand/order_block/fvg (never executable: stage 5 rule 2) and the engine-derived confluence (`confluence_facts.go:147,196`); **not** the zone book (built from the V2 snapshot, §7.5 of the audit) |

Measured against the frozen technique instances on 11 captures (1,451 checkpoints, M5): 2,205 zones match by (technique, side, low, high, origin); 856 Go zones have no Python counterpart (471 originate before the frozen 150-bar window, 385 inside it: the longer history changes legs, ATR and swings, so different zones exist), 331 Python zones are absent or differ (243 are entry-clipped instances for which Go publishes the *structural* boundary on purpose, 85 are absent because of the 400-bar window, of which 81 mitigated and 4 live). Mitigation is not carried: every matched zone that Python marks `mitigated` (1,342) is published by Go as `Touched`, never as mitigated; Go's M5 technique population has no mitigated state at all. None of this reaches a trade today, because the executable path reads the 150-bar legacy frame (the technique parity tests prove it) and the resting path is rejected at the adapter.

## 4. Where the confluence stars come from (stage 3)

* A strategy that supplies `DetectorConfluence` (keylevel, rangeedge, confluencezone, sessionlevel, breakretest, ifvg, liquiditysweep, crt, and the technique helper) overrides the engine value (`worker.go:237`). The formula behind it is exact against both Python references on 62,208 factor combinations.
* Everything else (impulse_pullback, resting zone candidates) gets the engine's `confluenceContext` (`confluence_facts.go:25`): HTF alignment from the Go higher-timeframe biases, touches from the **V2** canonical zone, `SessionContext = (session active)`, fib touch, CHoCH recency, and **keyword inference** over evidence codes (`wick|rejection`, `displacement|engulf`, `reclaim|structure|break`). The inference is negation-blind (`m5_no_break_of_structure` would set `StructuralAgreement`), but none of the static evidence codes currently emitted by impulse_pullback matches a keyword, so it is inert there today.
* The engine value is computed for every candidate and then replaced; it has no side effect.

## 5. Technical geometry vs execution geometry (stages 2 and 7)

| Quantity | Technical (Go candidate) | Executed (Algo Bot) |
|---|---|---|
| Stop | the strategy's invalidation (median 4-9 pips beyond the FX entry in production) | `plan_go_invalidation_stop(..., widen_to_minimum=True)` widens it to the instrument floor (`execution_policy.py:840-858`): EURUSD 12-20, GBPUSD 15-25, USDJPY 18-28, GBPJPY 22-35, XAU 50-60 pips (`instruments.yml`); the cap rejects a wider one (`stop_exceeds_go_invalidation_envelope`) |
| Targets | opposing liquidity, or `fallback_target_r` | **not used as targets.** `target_policy_mode = fixed_rr`: FX 1R/2R 50/50 of the *executed* stop, breakeven after 1R; XAU 1R/2R/3R/4R 40/20/20/20, breakeven after 1R; M1 scalps `scalp_rr`. The Go targets only measure `available_target_room`; room below the preferred ladder falls back to 1R all-or-nothing, below 1R rejects (`fixed_rr_room_insufficient`) |
| Entry | the strategy's band | XAU zone strategies: band widened to ≥30 pips toward the stop or trimmed to the 50 pips nearest the near edge; FVG/iFVG ≥30; Break & Retest widened to 50 (`execution_policy.py:151-158`). Shallow leg at the near edge, deep leg at the midpoint |
| Size | none | `lots_for_equity` by equity table and tier multiplier, **not** by stop distance (`performance-investigation-2026-10.md` §6) |

Consequence, measured in the performance investigation (PR #791, 424 trades): 71-81% of FX stops sit exactly on the floor, about three times the technical distance, and the targets are 1R and 2R of that stop. The thesis a detector describes (a 5-pip invalidation) is not the trade the account takes (a 12-22 pip stop); a technical RR of 1.9 becomes a fixed 1R/2R ladder. This is a design decision of the execution layer, not a porting error, but it means **no parity statement about a Go candidate's geometry says anything about the executed trade's geometry**.

## 6. Verification status of the contract

| Link | Evidence | Status |
|---|---|---|
| bars → legacy frame | 1,451 checkpoints × 3 timeframes × 11 captures vs frozen Python (`oracle_compare.py`) | exact (see audit §5) |
| legacy frame → candidate (executable strategies) | strategy goldens (audit §9) | exact for key_level, flip_zone, technique helper path; reconstructions elsewhere |
| candidate → Kafka envelope → `StrategyMatch` | `test/legacyparity/payload_dump_test.go` feeds real Go payloads through the Python adapter | exercised, not measured in this audit |
| adapter bias / htf_bias | this audit | **different source from the frozen Python; 61-63% agreement; UNVERIFIED which is correct** |
| execution policy → plan | performance investigation: replay predicts production on 88.5% of decided cases | consistent; not a parity proof |
| executor → broker | not audited (read-only mandate) | UNVERIFIED |

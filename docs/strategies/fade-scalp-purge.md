# Fade Scalp is purged

Branch `agent/purge-fade-scalp`, base `master` at `46671fea`. Owner decision 2026-10-29: Fade Scalp is retired, and retired means **removed, not disabled**: no observe-only mode, no disabled flag. Same rule as the Q2 purge of Range Edge, Session Level and the standalone Liquidity Sweep (commit `e101a3d`). Nothing else changed: no other strategy's threshold, stop envelope, ladder, sizing, enablement, arbitration or quality model.

## 1. Why

- **AE set on XAU.** The 21 committed M5 captures give 11 Fade Scalp candidates in 26 XAU days. Only 3 are tradable inside the 15-45 pip scalp band, at -0.56 R above random. There is effectively no XAU output.
- **AE set on FX.** 27 distinct setups at +0.03 R above random. The structural target is a median 0.5 R once the stop is widened to the instrument floor. The Q3 validation measured -0.43 R [-0.82, -0.01] on 27 filled FX theses ([q3-quality-validation.md](q3-quality-validation.md)).
- **Concept test, independent of the AE gates:** fade a reclaimed sweep of equal highs/lows, stop beyond the sweep extreme + 0.25 ATR, single target at 1-2 R.
  - XAU entering at the next open: -0.20 R [-0.37, -0.00], 86 events.
  - FX: +0.05 R [-0.04, +0.15], 283 events.
  - The variant waiting for a confirming bar: XAU +0.58 R but only n = 15; FX +0.01 R.
  - No robust edge on this data.
- **The design is redundant.**
  - Its direction is locked to the engine's structural direction, so it is a with-structure pullback after a sweep, not an exhaustion fade.
  - On XAU, Key Level v6 (#819, [keylevel-sweep-first.md](keylevel-sweep-first.md)) already trades exactly "level swept, then reclaimed".
  - CRT trades the sweep and reclaim of an H1 range.
- **Production, recorded here and not decisive:** 18 trades, of which 8 have a known R, at +0.99 R (95% interval [+0.24, +1.85]; exit paths 6 full stops, 5 first target then break-even, 7 second target; 6 XAU, 7 GBPJPY, 2 EURUSD, 2 USDJPY, 1 GBPUSD); the exit paths of all 18 bound the mean at +0.39 R (source: the removed `fade_scalp` row of `ae-strategy-matrix.yml` / `ae-quality-replay-results.json`). The sample is too small to outweigh the above.

## 2. The scalp lane after this change

**Impulse Pullback (XAU) is the only scalp left.** Impulse Pullback's own `m1_scalp` stop envelope (`strategies.scalping.stop.*`) and sizing are untouched.

## 3. What was removed

Deleted files: `analysis-engine/internal/strategy/fadescalp/{fadescalp,fadescalp_test,quality,quality_test}.go`, `analysis-engine/test/aequality/fade_considered_test.go` (the Fade-only consideration replay), `docs/strategies/fade_scalp.md`.

- **Go.** The factory and `knownIDs` entry (the catalog is now 12 strategies), the architecture layer entry, `legacyDetectorStrategies`, the `fade_scalp` stop-envelope kind and everything that only it read: the `stopEnvelopeRangeRoom` kind, `StopEnvelopeConfig.RangeMinRR/RangeRoomFloorPips/InstrumentScalp*`, `Settings.InstrumentScalpStop*`, `config.Document.ScalpStopEnvelopeFor`, and the startup reads of `execution.range.min_rr` and `execution.range.room_stop_floor_pips`. Dead helpers: `strategyutil.ChopEdgeAllows` and `strategyutil.MeanReference` (and the `MeanRange*/MeanDealing*` constants). Test updates: `test/aequality` (the fade watch, shadow records, the `fade_scalp` scalp flag and component pin), `test/architecture`, `test/engine`, `test/detectorparity`, the envelope/budget/independence tests.
- **Config.** `config/analysis.yml` `strategies.fade_scalp`; `config/auto-algo.yml` `strategies.scalp.fade_scalp_enabled` (the `scalp:` block was left empty and is gone); `config/instruments.yml` `instruments.XAU.stop_envelope.scalp_min_pips/scalp_max_pips` (read only for the range-room scalp band). A configuration that still names `fade_scalp` fails startup (`strategy.ValidateConfigs`; pinned by `TestAConfigurationThatStillNamesFadeScalpFailsStartup` and `TestPurgedStrategiesHaveNoFactoryAndNoCatalogEntry`).
- **Python.** The `Fade Scalp` catalog profile, `go_opportunity_policy.REVIEWED_SCOPES["fade_scalp"]`, `CATALOG_STRATEGY_IDS`, the `execution.range.room_stop_floor_pips` instrument mapping from `scalp_min_pips`, the C# executor's scalp list entry (`TradePlanRuntime.cs`, as for Range Edge Scalp). Tools: `q3_quality_validation.py` loses `fade_gates` and the `--fade` inputs (their producer test is gone), `ae_quality_eval.py` and `q55_replay_parity.py` lose the Fade Scalp special cases. Tests that used Fade Scalp as the generic range scalp now use Range Box Scalp (a retired range-lane profile) or Impulse Pullback; `test_purged_strategies.py` pins the purge.
- **Kept on purpose.** `FADE_SCALP = "Fade Scalp"` in `strategy_names.py` stays as a **retired, profile-less display alias** so persisted trades, plans, events and stats still decode to a name (pinned by `test_purged_strategies.py`). The recorded production-incident fixtures (`tests/fixtures/exposure_groups_20261005_20261014.json` and the incident replay in `test_exposure_policy_replay.py`) keep the strategy name they were recorded with.
- **Range-lane code that stays, and who still reaches it.** The Python range lane (`RANGE_STRATEGIES`, the `range_room` stop branch of `protective_stop`, `min_rr_from_range_config`, `range_risk_ceiling`, `range_lifecycle`, `execution.range.*`) is still served by the retired Python-era profiles Range Box Scalp, One-Sided Range Reaction and Chop Zone Reaction (`range_lane=True`) and, for `min_rr_from_range_config` / `range_risk_ceiling`, Impulse Pullback Scalp and Momentum Chase Scalp. Nothing in the Go catalog can publish a range-lane strategy any more; removing that lane is a separate decision about those profiles, not part of this purge. `analysis.legacy_read.range_structure` stays: the opposing-barrier book reads it.

## 4. Goldens and results touched (Fade Scalp rows only)

| artifact | change |
|---|---|
| `analysis-engine/testdata/detector-parity-oracle.json` (`test/detectorparity`) | the `fade_scalp` decision rows are dropped: **94** (GBPUSD 33, USDJPY 43, XAU 18); the bias/regime/premium-discount layer and the `break_retest` rows are untouched, and the file was verified to round-trip byte-for-byte before the edit. `generate_oracle_golden.py` no longer lists `fade_scalp`. |
| `tests/fixtures/go_payloads_by_strategy.json` | the `fade_scalp` payload entry |
| `tests/fixtures/execution_profiles_family_era.json` | the `Fade Scalp` row is now the profile-less default row (identical to the `""` row) |
| `docs/strategies/ae-strategy-matrix.yml` and `ae-quality-replay-results.json` | the `fade_scalp` matrix row and its replay, production-by-strategy and exit-path entries (13 -> 12 strategies); pooled figures in that file are as they were measured and are not recomputed |
| `analysis-engine/testdata/confluence-zone-other-strategies-baseline-master-f18179f.json` and `docs/strategies/confluence-zone-v3-replay-results.json` (`test/czreplay`, the other strategies' candidates per capture) | XAU 13 Oct 3,121 -> 3,110 (-11), EURUSD 4,406 -> 4,405 (-1), GBPUSD 6,007 -> 6,003 (-4), GBPJPY 4,439 -> 4,436 (-3), USDJPY 5,957 -> 5,954 (-3); XAU 21 Sep (1,518) has no Fade Scalp and keeps its count and hash. Counts and hashes of the five captures only; the Confluence Zone figures are unchanged. |

No other golden was regenerated. The four parity suites (`techniqueparity`, `legacyparity`, `detectorparity`, `keylevelparity`) pass with `-timeout 60m`.

## 5. Replay count change

Per capture, engine as configured (`cmd/replay -capture ... -envelopes-out`), `origin/master` before and this branch after. Counts are discovered opportunities per capture (overlapping captures replay overlapping periods, so they are not additive across a symbol). After = before - Fade Scalp in every capture: **no other strategy's count changes**.

| capture | before | Fade Scalp removed | after |
|---|---:|---:|---:|
| eurusd-20261006 | 877 | 2 | 875 |
| eurusd-20261013 | 1,256 | 1 | 1,255 |
| eurusd-20261014 | 1,297 | 2 | 1,295 |
| eurusd-20261022 | 1,729 | 2 | 1,727 |
| gbpjpy-20261006 | 690 | 2 | 688 |
| gbpjpy-20261013 | 997 | 2 | 995 |
| gbpjpy-20261014 | 935 | 1 | 934 |
| gbpjpy-20261022 | 728 | 1 | 727 |
| gbpusd-20261005 | 1,083 | 2 | 1,081 |
| gbpusd-20261013 | 1,728 | 3 | 1,725 |
| gbpusd-20261014 | 1,568 | 5 | 1,563 |
| gbpusd-20261022 | 1,430 | 4 | 1,426 |
| usdjpy-20261005 | 1,277 | 3 | 1,274 |
| usdjpy-20261013 | 1,457 | 2 | 1,455 |
| usdjpy-20261014 | 1,385 | 2 | 1,383 |
| usdjpy-20261022 | 1,477 | 3 | 1,474 |
| xau-20260921 | 382 | 0 | 382 |
| xau-20261012 | 631 | 7 | 624 |
| xau-20261013 | 771 | 8 | 763 |
| xau-20261014 | 698 | 7 | 691 |
| xau-20261022 | 648 | 3 | 645 |
| **all 21** | **23,044** | **62** | **22,982** |

## 6. Deploy

The engine and the instrument/analysis config go together. The rendered production configuration must drop, in the same deploy as the `st-mich43l/ansible-library` group_vars (not edited here), or `config-compiler`/startup fails:

- `analysis.strategies.fade_scalp` (the whole block: `version`, `enabled`, `invalidation_buffer_atr`, `minimum_reward_risk`, `expiry_hours`, `chop_edge_fraction`, `strict_premium_discount`);
- `auto_algo.strategies.scalp.fade_scalp_enabled` (and the then-empty `auto_algo.strategies.scalp`);
- `instruments.XAU.stop_envelope.scalp_min_pips` and `instruments.XAU.stop_envelope.scalp_max_pips`.

`execution.range.min_rr` and `execution.range.room_stop_floor_pips` stay (Python still reads them for the legacy range-lane profiles). Open or pending Fade Scalp plans at deploy time keep their name and are managed by the existing lifecycle; the C# executor will treat them as non-scalp from then on. Rollback is a revert of this PR plus the config leaves.

## 7. Historical records

Frozen measurement records that name Fade Scalp are left as they were measured (as the Q2 purge left Range Edge in its records): `CHANGELOG.md` history, the Q3/Q5 validation and certification documents, the migration and parity audits, and their result JSON/CSV files under `docs/`. They describe what was true when they were produced and are evidence for this decision.

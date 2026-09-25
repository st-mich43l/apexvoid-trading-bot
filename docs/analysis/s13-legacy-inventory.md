# S13 classified legacy dependency inventory

- Legacy modules: 63 (32958 lines)
- Unclassified modules: 0
- Unreachable from process entrypoints: app.scalping.lab_event_builder, app.scalping.mad_phase, app.scalping.mad_replay, app.scalping.performance, app.scalping.replay_lab
- Production importers that block deletion: 19

| Module | Role | Retirement | Reachable | Lines |
|---|---|---|---|---|
| `app.analysis` | package_marker | retain | yes | 0 |
| `app.analysis.actionability` | technical_detection | replace_with_go | yes | 946 |
| `app.analysis.bar_event_dispatcher` | technical_orchestration | replace_with_go | yes | 248 |
| `app.analysis.candle_displacement` | technical_detection | replace_with_go | yes | 275 |
| `app.analysis.candle_evidence` | technical_detection | replace_with_go | yes | 264 |
| `app.analysis.candle_geometry` | technical_detection | replace_with_go | yes | 145 |
| `app.analysis.candle_rejection` | technical_detection | replace_with_go | yes | 305 |
| `app.analysis.candle_sequences` | technical_detection | replace_with_go | yes | 395 |
| `app.analysis.confluence_zone` | technical_detection, execution_policy_input | replace_with_go | yes | 597 |
| `app.analysis.dealing_range` | technical_detection | replace_with_go | yes | 83 |
| `app.analysis.detectors` | technical_detection | replace_with_go | yes | 3866 |
| `app.analysis.engine` | technical_orchestration | replace_with_go | yes | 1162 |
| `app.analysis.entry_location` | technical_detection, execution_policy_input | replace_with_go | yes | 548 |
| `app.analysis.execution_eligibility` | execution_policy_input | retain | yes | 119 |
| `app.analysis.fibonacci` | technical_detection | replace_with_go | yes | 111 |
| `app.analysis.indicators` | technical_shared_math | replace_with_go_facts | yes | 8 |
| `app.analysis.key_level_role` | technical_detection | replace_with_go | yes | 75 |
| `app.analysis.levels` | technical_detection | replace_with_go | yes | 160 |
| `app.analysis.liquidity` | technical_detection | replace_with_go | yes | 212 |
| `app.analysis.m1_trigger` | technical_detection, execution_policy_input | replace_with_go | yes | 539 |
| `app.analysis.mad_phase` | research | retain_offline | yes | 1355 |
| `app.analysis.market_map` | technical_detection | replace_with_go | yes | 1501 |
| `app.analysis.market_map_delivery` | presentation | retain | yes | 269 |
| `app.analysis.math_utils` | technical_shared_math | replace_with_go_facts | yes | 71 |
| `app.analysis.momentum` | technical_detection | replace_with_go | yes | 104 |
| `app.analysis.ohlc_source` | market_data_access | retain_until_no_python_ohlc_consumer | yes | 160 |
| `app.analysis.regime` | technical_detection | replace_with_go | yes | 117 |
| `app.analysis.scalp_ranges` | technical_detection | replace_with_go | yes | 1022 |
| `app.analysis.scanner` | technical_orchestration | replace_with_go | yes | 3571 |
| `app.analysis.session_liquidity` | technical_detection | replace_with_go | yes | 201 |
| `app.analysis.structural_reaction_support` | technical_detection, execution_policy_input | replace_with_go | yes | 503 |
| `app.analysis.structure` | technical_detection | replace_with_go | yes | 286 |
| `app.analysis.swings` | technical_detection | replace_with_go | yes | 117 |
| `app.analysis.technique_detectors` | technical_detection | replace_with_go | yes | 361 |
| `app.analysis.technique_geometry` | technical_detection | replace_with_go | yes | 958 |
| `app.analysis.trendline_v2` | technical_detection | replace_with_go | yes | 634 |
| `app.analysis.trendlines` | technical_detection | replace_with_go | yes | 498 |
| `app.analysis.types` | shared_types | retain_until_policy_consumes_go_facts | yes | 125 |
| `app.analysis.zones` | technical_detection | replace_with_go | yes | 950 |
| `app.scalping` | package_marker | retain | yes | 30 |
| `app.scalping.activation` | execution_policy_input | retain | yes | 231 |
| `app.scalping.context` | execution_policy_input | retain | yes | 422 |
| `app.scalping.lab_event_builder` | research | retain_offline | NO | 379 |
| `app.scalping.lifecycle` | outcome_accounting | retain | yes | 130 |
| `app.scalping.mad_phase` | compatibility | delete_with_target | NO | 3 |
| `app.scalping.mad_replay` | research | retain_offline | NO | 456 |
| `app.scalping.math_features` | technical_detection | replace_with_go | yes | 405 |
| `app.scalping.math_strategies` | technical_detection | replace_with_go | yes | 464 |
| `app.scalping.microstructure` | technical_detection | replace_with_go | yes | 1375 |
| `app.scalping.models` | outcome_accounting | retain | yes | 526 |
| `app.scalping.outcomes` | outcome_accounting | retain | yes | 773 |
| `app.scalping.performance` | research | retain_offline | NO | 212 |
| `app.scalping.publish` | technical_orchestration | replace_with_go | yes | 299 |
| `app.scalping.ranking` | technical_detection | replace_with_go | yes | 165 |
| `app.scalping.replay` | research | retain_offline | yes | 274 |
| `app.scalping.replay_lab` | research | retain_offline | NO | 420 |
| `app.scalping.research_stamp` | research | retain_offline | yes | 340 |
| `app.scalping.risk` | outcome_accounting | retain | yes | 340 |
| `app.scalping.rollout` | execution_policy_input | retain | yes | 243 |
| `app.scalping.runtime` | technical_orchestration | replace_with_go | yes | 940 |
| `app.scalping.strategies` | technical_detection | replace_with_go | yes | 1421 |
| `app.scalping.telemetry` | outcome_accounting | retain | yes | 42 |
| `app.scalping.unified_context` | technical_detection | replace_with_go | yes | 207 |

## Startup tasks

| Task | Module | Legacy | Conditional |
|---|---|---|---|
| `analysis_opportunity_consumer_loop` | `app.analysis_client.consumer` | no | yes |
| `auto_trade_events_loop` | `app.autotrade.delivery` | no | no |
| `auto_trade_stats_ingestion_loop` | `app.autotrade.stats_ingestion` | no | no |
| `bar_event_dispatcher_loop` | `app.analysis.bar_event_dispatcher` | yes | no |
| `bridge_intents_loop` | `app.signals.manual_execution` | no | no |
| `calendar_sync_loop` | `app.signals.calendar` | no | no |
| `owner_dm_daily_wipe_loop` | `app.bot.owner_dm_journal` | no | no |
| `reconcile_events_loop` | `app.signals.manual_execution` | no | no |
| `setup_expiry_sweeper_loop` | `app.autotrade.setup_expiry_sweeper` | no | no |
| `watcher_loop` | `app.signals.watcher` | no | no |
| `weekly_report_loop` | `app.signals.weekly_report` | no | no |
| `zone_watch_execution_loop` | `app.autotrade.zone_execution_cutover` | no | no |

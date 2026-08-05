"""Complete classification of C# FeedOptions and AutoTradeOptions properties.

Every behaviour-affecting property created by FromEnvironment() must appear
here. Classifications:

  manifest                 — represented in ResolvedRuntimeManifest
  secret_environment       — credentials / tokens; never in the manifest
  bootstrap_environment    — process/path/connection before manifest load
  derived_runtime          — computed at runtime from other inputs
  deprecated_compatibility — retained aliases / audit-only fields
"""

from __future__ import annotations

from typing import Literal

OptionClassification = Literal[
  "manifest",
  "secret_environment",
  "bootstrap_environment",
  "derived_runtime",
  "deprecated_compatibility",
]


class OptionFieldSpec(dict):
  """Typed mapping helper (path keys kept as plain dict for JSON gen)."""


# property → (classification, canonical_env|None, catalog_path|None, notes)
FEED_OPTIONS_CLASSIFICATION: dict[str, tuple[OptionClassification, str | None, str | None]] = {
  'AccessToken': ('secret_environment', 'CTRADER_ACCESS_TOKEN', 'bootstrap.ctrader.credentials.access_token'),
  'AccountId': ('bootstrap_environment', 'CTRADER_ACCOUNT_ID', 'bootstrap.ctrader.credentials.account_id'),
  'BackfillBars': ('manifest', 'CTRADER_BACKFILL_BARS', 'market_data.ctrader_feed.backfill_bars'),
  'BarQualityLookback': ('manifest', 'BAR_QUALITY_LOOKBACK', 'market_data.ctrader_feed.bar_quality_lookback_bars'),
  'BarsChannel': ('manifest', 'BARS_CHANNEL', 'market_data.ctrader_feed.bars_channel'),
  'BarsWindowMax': ('manifest', 'BARS_WINDOW_MAX', 'market_data.ctrader_feed.bars_window_max'),
  'CTraderSymbol': ('manifest', 'CTRADER_SYMBOL', 'market_data.ctrader_feed.symbol'),
  'ClientId': ('bootstrap_environment', 'CTRADER_CLIENT_ID', 'bootstrap.ctrader.credentials.client_id'),
  'ClientSecret': ('secret_environment', 'CTRADER_CLIENT_SECRET', 'bootstrap.ctrader.credentials.client_secret'),
  'ExpectedBroker': ('manifest', 'CTRADER_EXPECTED_BROKER', 'contract.account.expected_broker'),
  'HeartbeatFile': ('bootstrap_environment', 'HEALTH_FILE', 'market_data.ctrader_feed.health_file'),
  'Host': ('bootstrap_environment', 'CTRADER_HOST', 'bootstrap.ctrader.connection.host'),
  'Port': ('bootstrap_environment', 'CTRADER_PORT', 'bootstrap.ctrader.connection.port'),
  'RedisSymbol': ('derived_runtime', None, None),
  'RedisUrl': ('bootstrap_environment', 'REDIS_URL', 'bootstrap.redis.url'),
  'RefreshToken': ('secret_environment', 'CTRADER_REFRESH_TOKEN', 'bootstrap.ctrader.credentials.refresh_token'),
  'RefreshTokenFile': ('bootstrap_environment', 'CTRADER_REFRESH_TOKEN_FILE', 'bootstrap.ctrader.token_rotation.refresh_token_file'),
  'RefreshTokenKey': ('bootstrap_environment', 'CTRADER_REFRESH_TOKEN_KEY', 'bootstrap.ctrader.token_rotation.refresh_token_key'),
  'RequestTimeout': ('bootstrap_environment', 'CTRADER_REQUEST_TIMEOUT', 'bootstrap.ctrader.connection.request_timeout_seconds'),
  'Timeframes': ('manifest', 'CTRADER_TIMEFRAMES', 'market_data.ctrader_feed.timeframes'),
  'TokenCheckInterval': ('bootstrap_environment', 'CTRADER_TOKEN_CHECK_INTERVAL_HOURS', 'bootstrap.ctrader.token_rotation.check_interval_hours'),
  'TokenRefreshLead': ('bootstrap_environment', 'CTRADER_TOKEN_REFRESH_LEAD_DAYS', 'bootstrap.ctrader.token_rotation.refresh_lead_days'),
}

AUTO_TRADE_OPTIONS_CLASSIFICATION: dict[str, tuple[OptionClassification, str | None, str | None]] = {
  'AddCooldownBars': ('manifest', 'AUTO_TRADE_ADD_COOLDOWN_BARS', 'lifecycle.scaling.cooldown_bars'),
  'AddLevelBufferAtr': ('manifest', 'AUTO_TRADE_ADD_LEVEL_BUFFER_ATR', 'execution.scaling.add.level_buffer_atr'),
  'AddMaxAgeBars': ('manifest', 'AUTO_TRADE_ADD_MAX_AGE_BARS', 'lifecycle.scaling.max_age_bars'),
  'AddMaxGroupRiskPct': ('manifest', 'AUTO_TRADE_ADD_MAX_GROUP_RISK_PCT', 'risk.sizing.add_max_group_risk_pct'),
  'AddMinStopPips': ('manifest', 'AUTO_TRADE_ADD_MIN_STOP_PIPS', 'execution.scaling.add.min_stop_pips'),
  'AddPullbackEnabled': ('manifest', 'AUTO_TRADE_ADD_PULLBACK_ENABLED', 'execution.scaling.add.pullback_enabled'),
  'AddPullbackMaxRetrace': ('manifest', 'AUTO_TRADE_ADD_PULLBACK_MAX_RETRACE', 'execution.scaling.add.pullback_max_retrace'),
  'AddPullbackMinRetrace': ('manifest', 'AUTO_TRADE_ADD_PULLBACK_MIN_RETRACE', 'execution.scaling.add.pullback_min_retrace'),
  'AddRequireRiskFree': ('manifest', 'AUTO_TRADE_ADD_REQUIRE_RISK_FREE', 'risk.sizing.add_require_risk_free'),
  'AddRiskFraction': ('manifest', 'AUTO_TRADE_ADD_RISK_FRACTION', 'risk.sizing.add_risk_fraction'),
  'AddSizeRatio': ('manifest', 'AUTO_TRADE_ADD_SIZE_RATIO', 'execution.scaling.add.size_ratio'),
  'AddStopBufferAtr': ('manifest', 'AUTO_TRADE_ADD_STOP_BUFFER_ATR', 'execution.scaling.add.stop_buffer_atr'),
  'AllowConcurrentStrategies': ('manifest', 'AUTO_TRADE_ALLOW_CONCURRENT_STRATEGIES', 'risk.exposure.allow_concurrent_strategies'),
  'AllowCounterBias': ('manifest', 'AUTO_TRADE_ALLOW_COUNTER_BIAS', 'actionability.counter_bias.allowed'),
  'AllowHedgedXau': ('manifest', 'AUTO_TRADE_ALLOW_HEDGED_XAU', 'risk.exposure.allow_hedged_xau'),
  'BoxMinRiskReward': ('manifest', 'AUTO_TRADE_BOX_MIN_RR', 'execution.policy.box_min_rr'),
  'BreakEvenBufferTicks': ('manifest', 'AUTO_TRADE_BE_BUFFER_TICKS', 'execution.stops.be_buffer_ticks'),
  'BreakoutEnabled': ('manifest', 'AUTO_TRADE_BREAKOUT_ENABLED', 'strategies.breakout.breakout_enabled'),
  'BrokerAbsenceConfirmations': ('manifest', 'AUTO_TRADE_BROKER_ABSENCE_CONFIRMATIONS', 'execution.broker_recovery.absence_confirmations'),
  'BrokerAbsenceRecheckSeconds': ('manifest', 'AUTO_TRADE_BROKER_ABSENCE_RECHECK_SECONDS', 'lifecycle.reconciliation.absence_recheck_seconds'),
  'BrokerRecoveryTimeoutSeconds': ('manifest', 'AUTO_TRADE_BROKER_RECOVERY_TIMEOUT_SECONDS', 'lifecycle.reconciliation.recovery_timeout_seconds'),
  'CandidateContractVersion': ('manifest', 'AUTO_TRADE_CANDIDATE_CONTRACT_VERSION', 'contract.versions.candidate'),
  'CandidateMaxAgeSeconds': ('manifest', 'AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS', 'lifecycle.candidate.execution_maximum_age_seconds'),
  'CandidateStorageTtlSeconds': ('manifest', 'AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS', 'lifecycle.candidate.storage_ttl_seconds'),
  'CandidateStream': ('manifest', 'AUTO_TRADE_CANDIDATE_STREAM', 'contract.streams.candidates'),
  'CanonicalSymbol': ('manifest', 'AUTO_TRADE_CANONICAL_SYMBOL', 'contract.instrument.canonical_symbol'),
  'ConfigManifestVersion': ('manifest', None, None),
  'ConfigSources': ('derived_runtime', None, None),
  'ContractMode': ('manifest', 'AUTO_TRADE_CONTRACT_MODE', 'contract.mode'),
  'ContractSize': ('manifest', 'AUTO_TRADE_XAU_CONTRACT_SIZE', 'contract.instrument.contract_units_per_lot'),
  'DeprecatedVariables': ('deprecated_compatibility', None, None),
  'DryRun': ('manifest', 'AUTO_TRADE_DRY_RUN', 'runtime.auto_trade.dry_run'),
  'Enabled': ('manifest', 'AUTO_TRADE_ENABLED', 'runtime.auto_trade.enabled'),
  'EntryContractTolerancePips': ('manifest', 'AUTO_TRADE_ENTRY_CONTRACT_TOLERANCE_PIPS', 'execution.entry.contract_tolerance_pips'),
  'EquityTableVersion': ('manifest', 'AUTO_TRADE_EQUITY_TABLE_VERSION', 'risk.sizing.equity_table_version'),
  'EventStream': ('manifest', 'AUTO_TRADE_EVENT_STREAM', 'contract.streams.events'),
  'ExecutionZoneMaxWidthAtr': ('manifest', 'AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_ATR', 'execution.policy.execution_zone_max_width_atr'),
  'ExecutionZoneMaxWidthPips': ('manifest', 'AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_PIPS', 'execution.policy.execution_zone_max_width_pips'),
  'ExpectedBroker': ('manifest', 'AUTO_TRADE_EXPECTED_BROKER', 'contract.account.expected_broker'),
  'FlipConfirmTimeoutSeconds': ('manifest', 'AUTO_TRADE_FLIP_CONFIRM_TIMEOUT_SECONDS', 'lifecycle.range_flip.confirm_timeout_seconds'),
  'FlipExitBufferPips': ('manifest', 'AUTO_TRADE_FLIP_EXIT_BUFFER_PIPS', 'execution.policy.flip_exit_buffer_pips'),
  'GroupCloseAllocation': ('manifest', 'AUTO_TRADE_GROUP_CLOSE_ALLOCATION', 'execution.policy.group_close_allocation'),
  'InsideZoneMarketEntryEnabled': ('manifest', 'AUTO_TRADE_INSIDE_ZONE_MARKET_ENTRY_ENABLED', 'execution.entry.inside_zone_market_entry_enabled'),
  'Label': ('manifest', 'AUTO_TRADE_LABEL', 'execution.policy.label'),
  'LiquidityReversalEnabled': ('manifest', 'AUTO_TRADE_LIQUIDITY_REVERSAL_ENABLED', 'strategies.reaction.liquidity_reversal.enabled'),
  'ManualAlgoEnabled': ('manifest', 'MANUAL_ALGO_ENABLED', 'manual_algo.runtime.enabled'),
  'MapThesisLockEnabled': ('manifest', 'AUTO_TRADE_MAP_THESIS_LOCK_ENABLED', 'execution.mapped_zone.thesis_lock_enabled'),
  'MappedZoneEnabled': ('manifest', 'AUTO_TRADE_MAPPED_ZONE_ENABLED', 'strategies.mapped_zone.enabled'),
  'MarketMapGuardEnabled': ('manifest', 'AUTO_TRADE_MARKET_MAP_GUARD_ENABLED', 'actionability.gates.market_map_guard_enabled'),
  'MaxEntryDistancePips': ('manifest', 'AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS', 'execution.entry.maximum_chase_distance_pips'),
  'MaxSpreadPips': ('manifest', 'AUTO_TRADE_MAX_SPREAD_PIPS', 'execution.entry.max_spread_pips'),
  'MaxTranches': ('manifest', 'AUTO_TRADE_MAX_TRANCHES', 'execution.policy.max_tranches'),
  'MinConfluence': ('manifest', 'AUTO_TRADE_MIN_CONFLUENCE', 'actionability.gates.min_confluence'),
  'MultiMatchEnabled': ('manifest', 'AUTO_TRADE_MULTI_MATCH_ENABLED', 'strategies.matching.multiple_matches_enabled'),
  'NonHedgedOppositePolicy': ('manifest', 'AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY', 'risk.exposure.non_hedged_opposite_policy'),
  'PipSize': ('manifest', 'AUTO_TRADE_XAU_PIP_SIZE', 'contract.instrument.pip_size'),
  'PipValuePerLot': ('manifest', 'AUTO_TRADE_PIP_VALUE_PER_LOT', 'execution.policy.pip_value_per_lot'),
  'PollMilliseconds': ('manifest', 'AUTO_TRADE_POLL_MS', 'execution.entry.poll_ms'),
  'PositionMissingConfirmations': ('manifest', 'AUTO_TRADE_POSITION_MISSING_CONFIRMATIONS', 'lifecycle.reconciliation.missing_confirmations'),
  'PositionMissingRecheckSeconds': ('manifest', 'AUTO_TRADE_POSITION_MISSING_RECHECK_SECONDS', 'lifecycle.reconciliation.missing_recheck_seconds'),
  'PostFillTargetFallback': ('manifest', 'AUTO_TRADE_POST_FILL_TARGET_FALLBACK', 'execution.targeting.post_fill_target_fallback'),
  'Profile': ('manifest', 'AUTO_TRADE_PROFILE', 'runtime.profile'),
  'RangeBoxMoveSlToBeAfterScaleOut': ('manifest', 'AUTO_TRADE_RANGE_BOX_MOVE_SL_TO_BE_AFTER_SCALE_OUT', 'execution.range.box_move_sl_to_be_after_scale_out'),
  'RangeBoxScaleOutEnabled': ('manifest', 'AUTO_TRADE_RANGE_BOX_SCALE_OUT_ENABLED', 'strategies.range_reversion.box_scale_out_enabled'),
  'RangeBoxScaleOutFraction': ('manifest', 'AUTO_TRADE_RANGE_BOX_SCALE_OUT_FRACTION', 'execution.range.box_scale_out_fraction'),
  'RangeBoxScaleOutThresholdPips': ('manifest', 'AUTO_TRADE_RANGE_BOX_SCALE_OUT_THRESHOLD_PIPS', 'execution.range.box_scale_out_threshold_pips'),
  'RangeBoxScaleOutTriggerPips': ('manifest', 'AUTO_TRADE_RANGE_BOX_SCALE_OUT_TRIGGER_PIPS', 'execution.range.box_scale_out_trigger_pips'),
  'RangeEnabled': ('manifest', 'AUTO_TRADE_RANGE_ENABLED', 'strategies.range_reversion.enabled'),
  'RangeFlipEnabled': ('manifest', 'AUTO_TRADE_RANGE_FLIP_ENABLED', 'strategies.range_reversion.flip_enabled'),
  'RangeTargetsPips': ('manifest', 'AUTO_TRADE_RANGE_TARGETS_PIPS', 'execution.targeting.range_ladder_pips'),
  'RangeTpBufferPips': ('manifest', 'AUTO_TRADE_RANGE_TP_BUFFER_PIPS', 'execution.range.tp_buffer_pips'),
  'RangeTwoSidedEnabled': ('manifest', 'AUTO_TRADE_RANGE_TWO_SIDED_ENABLED', 'strategies.range_reversion.two_sided_enabled'),
  'ReactionEnabled': ('manifest', 'AUTO_TRADE_REACTION_ENABLED', 'strategies.reaction.enabled'),
  'ReactionMarketFraction': ('manifest', 'AUTO_TRADE_REACTION_MARKET_FRACTION', 'execution.reaction.market_fraction'),
  'ReactionScaleEnabled': ('manifest', 'AUTO_TRADE_REACTION_SCALE_ENABLED', 'strategies.reaction.scale_enabled'),
  'ReactionScaleFraction': ('manifest', 'AUTO_TRADE_REACTION_SCALE_FRACTION', 'execution.reaction.scale_fraction'),
  'ReactionScaleInvalidPolicy': ('manifest', 'AUTO_TRADE_REACTION_SCALE_INVALID_POLICY', 'execution.reaction.scale_invalid_policy'),
  'ReactionScaleStepAtr': ('manifest', 'AUTO_TRADE_REACTION_SCALE_STEP_ATR', 'execution.reaction.scale_step_atr'),
  'RedisUrl': ('bootstrap_environment', 'REDIS_URL', 'bootstrap.redis.url'),
  'RequireDemoAccount': ('manifest', 'AUTO_TRADE_REQUIRE_DEMO_ACCOUNT', 'contract.account.require_demo'),
  'RequireDemoOnlyToken': ('manifest', 'AUTO_TRADE_REQUIRE_DEMO_ONLY_TOKEN', 'execution.policy.require_demo_only_token'),
  'RequireFlatForRange': ('manifest', 'AUTO_TRADE_REQUIRE_FLAT_FOR_RANGE', 'risk.exposure.require_flat_for_range'),
  'RetestEnabled': ('manifest', 'AUTO_TRADE_RETEST_ENABLED', 'strategies.selection.retest_enabled'),
  'RiskPercent': ('manifest', 'AUTO_TRADE_RISK_PCT', 'risk.sizing.risk_pct'),
  'SizingMode': ('manifest', 'AUTO_TRADE_SIZING_MODE', 'risk.sizing.mode'),
  'SpotMaxAgeSeconds': ('manifest', 'AUTO_TRADE_SPOT_MAX_AGE_SECONDS', 'market_data.spot.maximum_age_seconds'),
  'StopLossDistance': ('manifest', 'AUTO_TRADE_SL_DISTANCE', 'execution.stops.sl_distance'),
  'StopPushBeyondZone': ('manifest', 'AUTO_TRADE_STOP_PUSH_BEYOND_ZONE', 'execution.stops.stop_push_beyond_zone'),
  'StrategyMatchEnabled': ('manifest', 'AUTO_TRADE_STRATEGY_MATCH_ENABLED', 'runtime.auto_trade.strategy_match_enabled'),
  'StructuralGuardMode': ('manifest', 'AUTO_TRADE_STRUCTURAL_GUARD_MODE', 'actionability.structural_guard.guard_mode'),
  'Symbols': ('manifest', 'AUTO_TRADE_SYMBOLS', 'contract.instrument.symbols'),
  'TargetWeights': ('manifest', 'AUTO_TRADE_TP_WEIGHTS', 'execution.targeting.tp_weights'),
  'TargetsPips': ('manifest', 'AUTO_TRADE_TARGET_PLANS_PIPS', 'execution.targeting.default_ladder_pips'),
  'TrackAllStructuralMatches': ('manifest', 'AUTO_TRADE_TRACK_ALL_STRUCTURAL_MATCHES', 'strategies.matching.track_all_structural_matches'),
  'TradePlanStream': ('manifest', 'AUTO_TRADE_TRADE_PLAN_STREAM', 'contract.streams.trade_plans'),
  'TrendEnabled': ('manifest', 'AUTO_TRADE_TREND_ENABLED', 'strategies.trend.enabled'),
  'TrendStopMaxPips': ('manifest', 'AUTO_TRADE_TREND_STOP_MAX_PIPS', 'execution.trend.stop_max_pips'),
  'TrendStopMinPips': ('manifest', 'AUTO_TRADE_TREND_STOP_MIN_PIPS', 'execution.stops.trend.minimum_pips'),
  'UnfilledLegAfterTpPolicy': ('manifest', 'AUTO_TRADE_UNFILLED_LEG_AFTER_TP_POLICY', 'execution.targeting.unfilled_leg_after_tp_policy'),
  'WickStopBufferAtr': ('manifest', 'AUTO_TRADE_WICK_STOP_BUFFER_ATR', 'execution.stops.wick_stop_buffer_atr'),
  'ZoneCooldownEnabled': ('manifest', 'AUTO_TRADE_ZONE_COOLDOWN_ENABLED', 'lifecycle.zone.cooldown_enabled'),
  'ZoneCooldownMinutes': ('manifest', 'AUTO_TRADE_ZONE_COOLDOWN_MINUTES', 'lifecycle.zone.cooldown_minutes'),
  'ZoneFillEnabled': ('manifest', 'AUTO_TRADE_ZONE_FILL_ENABLED', 'execution.zone_scaling.fill_enabled'),
  'ZoneFillFallbackEnabled': ('manifest', 'AUTO_TRADE_ZONE_FILL_FALLBACK_ENABLED', 'execution.zone_scaling.fill_fallback_enabled'),
  'ZoneFillMinAtr': ('manifest', 'AUTO_TRADE_ZONE_FILL_MIN_ATR', 'execution.zone_scaling.fill_min_atr'),
  'ZoneFillMinLots': ('manifest', 'AUTO_TRADE_ZONE_FILL_MIN_LOTS', 'execution.zone_scaling.fill_min_lots'),
  'ZoneFillTtlBars': ('manifest', 'AUTO_TRADE_ZONE_FILL_TTL_BARS', 'lifecycle.zone.fill_ttl_bars'),
  'ZoneReconcileMode': ('manifest', 'AUTO_TRADE_ZONE_RECONCILE_MODE', 'actionability.zone_reconciliation.mode'),
  'ZoneScaleUndersizedPolicy': ('manifest', 'AUTO_TRADE_ZONE_SCALE_UNDERSIZED_POLICY', 'execution.zone_scaling.scale_undersized_policy'),
}

def assert_complete_classification() -> None:
  missing = [
    name
    for name, (classification, _, _) in {
      **{f"FeedOptions.{k}": v for k, v in FEED_OPTIONS_CLASSIFICATION.items()},
      **{
        f"AutoTradeOptions.{k}": v
        for k, v in AUTO_TRADE_OPTIONS_CLASSIFICATION.items()
      },
    }.items()
    if classification not in {
      "manifest",
      "secret_environment",
      "bootstrap_environment",
      "derived_runtime",
      "deprecated_compatibility",
    }
  ]
  if missing:
    raise RuntimeError(f"unclassified options: {missing}")


def migration_entries() -> list[dict[str, object]]:
  entries: list[dict[str, object]] = []
  for prop, (classification, env, catalog_path) in sorted(
    FEED_OPTIONS_CLASSIFICATION.items()
  ):
    entries.append({
      "options_type": "FeedOptions",
      "property": prop,
      "environment": env,
      "canonical_path": catalog_path,
      "classification": classification,
      "manifest_path": (
        f"feed.{_manifest_key(prop)}" if classification == "manifest" else None
      ),
      "removable_after_cutover": classification == "manifest" and env is not None,
    })
  for prop, (classification, env, catalog_path) in sorted(
    AUTO_TRADE_OPTIONS_CLASSIFICATION.items()
  ):
    entries.append({
      "options_type": "AutoTradeOptions",
      "property": prop,
      "environment": env,
      "canonical_path": catalog_path,
      "classification": classification,
      "manifest_path": (
        f"auto_trade.{_manifest_key(prop)}"
        if classification == "manifest"
        else None
      ),
      "removable_after_cutover": classification == "manifest" and env is not None,
    })
  return entries


def _manifest_key(property_name: str) -> str:
  """PascalCase → snake_case for JSON keys."""
  if property_name == "CTraderSymbol":
    return "ctrader_symbol"
  chars: list[str] = []
  for index, char in enumerate(property_name):
    if char.isupper() and index > 0:
      chars.append("_")
    chars.append(char.lower())
  return "".join(chars)


def classification_counts() -> dict[str, int]:
  values = [
    item[0]
    for item in FEED_OPTIONS_CLASSIFICATION.values()
  ] + [
    item[0]
    for item in AUTO_TRADE_OPTIONS_CLASSIFICATION.values()
  ]
  return {
    key: values.count(key)
    for key in (
      "manifest",
      "secret_environment",
      "bootstrap_environment",
      "derived_runtime",
      "deprecated_compatibility",
    )
  }

using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace ApexVoid.CTraderFeed;

/// <summary>
/// File-based cross-service runtime manifest produced by the Python compiler.
/// Distinct from <see cref="AutoTradeConfigManifest"/> (Redis health snapshot).
/// </summary>
public sealed record ResolvedRuntimeManifest(
  [property: JsonPropertyName("manifest_version")] int ManifestVersion,
  [property: JsonPropertyName("contract_fingerprint")] string ContractFingerprint,
  [property: JsonPropertyName("effective_configuration_fingerprint")]
    string EffectiveConfigurationFingerprint,
  [property: JsonPropertyName("profile")] string Profile,
  [property: JsonPropertyName("global")] JsonElement Global,
  [property: JsonPropertyName("instruments")]
    Dictionary<string, JsonElement> Instruments,
  [property: JsonPropertyName("feed")] ResolvedFeedProjection Feed,
  [property: JsonPropertyName("auto_trade")] ResolvedAutoTradeProjection AutoTrade,
  [property: JsonPropertyName("live_instruments")] IReadOnlyList<string> LiveInstruments,
  [property: JsonPropertyName("instrument_runtimes")]
    Dictionary<string, JsonElement>? InstrumentRuntimes = null
);

public sealed record ResolvedFeedProjection(
  [property: JsonPropertyName("ctrader_symbol")] string CTraderSymbol,
  [property: JsonPropertyName("redis_symbol")] string RedisSymbol,
  [property: JsonPropertyName("timeframes")] IReadOnlyList<string> Timeframes,
  [property: JsonPropertyName("backfill_bars")] int BackfillBars,
  [property: JsonPropertyName("bars_window_max")] int BarsWindowMax,
  [property: JsonPropertyName("bars_channel")] string BarsChannel,
  [property: JsonPropertyName("bar_quality_lookback")] int BarQualityLookback,
  [property: JsonPropertyName("expected_broker")] string ExpectedBroker
);

public sealed record ResolvedAutoTradeProjection(
  [property: JsonPropertyName("enabled")] bool Enabled,
  [property: JsonPropertyName("dry_run")] bool DryRun,
  [property: JsonPropertyName("expected_broker")] string ExpectedBroker,
  [property: JsonPropertyName("stop_loss_distance")] string StopLossDistance,
  [property: JsonPropertyName("targets_pips")] IReadOnlyList<int> TargetsPips,
  [property: JsonPropertyName("target_weights")] IReadOnlyList<int> TargetWeights,
  [property: JsonPropertyName("break_even_buffer_ticks")] int BreakEvenBufferTicks,
  [property: JsonPropertyName("candidate_max_age_seconds")] int CandidateMaxAgeSeconds,
  [property: JsonPropertyName("spot_max_age_seconds")] int SpotMaxAgeSeconds,
  [property: JsonPropertyName("max_spread_pips")] int MaxSpreadPips,
  [property: JsonPropertyName("max_entry_distance_pips")] int MaxEntryDistancePips,
  [property: JsonPropertyName("min_confluence")] int MinConfluence,
  [property: JsonPropertyName("poll_milliseconds")] int PollMilliseconds,
  [property: JsonPropertyName("candidate_stream")] string CandidateStream,
  [property: JsonPropertyName("event_stream")] string EventStream,
  [property: JsonPropertyName("label")] string Label,
  [property: JsonPropertyName("require_demo_only_token")] bool RequireDemoOnlyToken,
  [property: JsonPropertyName("risk_percent")] string RiskPercent,
  [property: JsonPropertyName("sizing_mode")] string SizingMode,
  [property: JsonPropertyName("pip_value_per_lot")] string PipValuePerLot,
  [property: JsonPropertyName("pip_size")] string PipSize,
  [property: JsonPropertyName("contract_size")] string ContractSize,
  [property: JsonPropertyName("max_tranches")] int MaxTranches,
  [property: JsonPropertyName("add_risk_fraction")] string AddRiskFraction,
  [property: JsonPropertyName("add_max_age_bars")] int AddMaxAgeBars,
  [property: JsonPropertyName("add_cooldown_bars")] int AddCooldownBars,
  [property: JsonPropertyName("add_level_buffer_atr")] string AddLevelBufferAtr,
  [property: JsonPropertyName("add_stop_buffer_atr")] string AddStopBufferAtr,
  [property: JsonPropertyName("add_min_stop_pips")] int AddMinStopPips,
  [property: JsonPropertyName("add_require_risk_free")] bool AddRequireRiskFree,
  [property: JsonPropertyName("zone_fill_enabled")] bool ZoneFillEnabled,
  [property: JsonPropertyName("zone_fill_min_lots")] string ZoneFillMinLots,
  [property: JsonPropertyName("zone_fill_min_atr")] string ZoneFillMinAtr,
  [property: JsonPropertyName("zone_fill_ttl_bars")] int ZoneFillTtlBars,
  [property: JsonPropertyName("zone_fill_fallback_enabled")] bool ZoneFillFallbackEnabled,
  [property: JsonPropertyName("inside_zone_market_entry_enabled")]
    bool InsideZoneMarketEntryEnabled,
  [property: JsonPropertyName("box_min_risk_reward")] string BoxMinRiskReward,
  [property: JsonPropertyName("trend_stop_min_pips")] int TrendStopMinPips,
  [property: JsonPropertyName("trend_stop_max_pips")] int TrendStopMaxPips,
  [property: JsonPropertyName("stop_push_beyond_zone")] bool StopPushBeyondZone,
  [property: JsonPropertyName("entry_contract_tolerance_pips")]
    string EntryContractTolerancePips,
  [property: JsonPropertyName("broker_absence_confirmations")]
    int BrokerAbsenceConfirmations,
  [property: JsonPropertyName("broker_absence_recheck_seconds")]
    int BrokerAbsenceRecheckSeconds,
  [property: JsonPropertyName("broker_recovery_timeout_seconds")]
    int BrokerRecoveryTimeoutSeconds,
  [property: JsonPropertyName("wick_stop_buffer_atr")] string WickStopBufferAtr,
  [property: JsonPropertyName("range_flip_enabled")] bool RangeFlipEnabled,
  [property: JsonPropertyName("flip_exit_buffer_pips")] int FlipExitBufferPips,
  [property: JsonPropertyName("flip_confirm_timeout_seconds")]
    int FlipConfirmTimeoutSeconds,
  [property: JsonPropertyName("zone_cooldown_minutes")] int ZoneCooldownMinutes,
  [property: JsonPropertyName("zone_cooldown_enabled")] bool ZoneCooldownEnabled,
  [property: JsonPropertyName("add_pullback_enabled")] bool AddPullbackEnabled,
  [property: JsonPropertyName("add_pullback_min_retrace")] string AddPullbackMinRetrace,
  [property: JsonPropertyName("add_pullback_max_retrace")] string AddPullbackMaxRetrace,
  [property: JsonPropertyName("add_max_group_risk_pct")] string AddMaxGroupRiskPct,
  [property: JsonPropertyName("add_size_ratio")] string AddSizeRatio,
  [property: JsonPropertyName("range_targets_pips")] IReadOnlyList<int> RangeTargetsPips,
  [property: JsonPropertyName("range_tp_buffer_pips")] string RangeTpBufferPips,
  [property: JsonPropertyName("profile")] string Profile,
  [property: JsonPropertyName("require_demo_account")] bool RequireDemoAccount,
  [property: JsonPropertyName("allow_concurrent_strategies")]
    bool AllowConcurrentStrategies,
  [property: JsonPropertyName("allow_hedged_xau")] bool AllowHedgedXau,
  [property: JsonPropertyName("require_flat_for_range")] bool RequireFlatForRange,
  [property: JsonPropertyName("range_two_sided_enabled")] bool RangeTwoSidedEnabled,
  [property: JsonPropertyName("multi_match_enabled")] bool MultiMatchEnabled,
  [property: JsonPropertyName("track_all_structural_matches")]
    bool TrackAllStructuralMatches,
  [property: JsonPropertyName("canonical_symbol")] string CanonicalSymbol,
  [property: JsonPropertyName("candidate_contract_version")]
    int CandidateContractVersion,
  [property: JsonPropertyName("contract_mode")] string ContractMode,
  [property: JsonPropertyName("trade_plan_stream")] string TradePlanStream,
  [property: JsonPropertyName("manual_algo_enabled")] bool ManualAlgoEnabled,
  [property: JsonPropertyName("trend_enabled")] bool TrendEnabled,
  [property: JsonPropertyName("range_enabled")] bool RangeEnabled,
  [property: JsonPropertyName("mapped_zone_enabled")] bool MappedZoneEnabled,
  [property: JsonPropertyName("market_map_guard_enabled")] bool MarketMapGuardEnabled,
  [property: JsonPropertyName("map_thesis_lock_enabled")] bool MapThesisLockEnabled,
  [property: JsonPropertyName("strategy_match_enabled")] bool StrategyMatchEnabled,
  [property: JsonPropertyName("breakout_enabled")] bool BreakoutEnabled,
  [property: JsonPropertyName("retest_enabled")] bool RetestEnabled,
  [property: JsonPropertyName("reaction_enabled")] bool ReactionEnabled,
  [property: JsonPropertyName("liquidity_reversal_enabled")]
    bool LiquidityReversalEnabled,
  [property: JsonPropertyName("allow_counter_bias")] bool AllowCounterBias,
  [property: JsonPropertyName("candidate_storage_ttl_seconds")]
    int CandidateStorageTtlSeconds,
  [property: JsonPropertyName("symbols")] IReadOnlyList<string> Symbols,
  [property: JsonPropertyName("config_manifest_version")] int ConfigManifestVersion,
  [property: JsonPropertyName("non_hedged_opposite_policy")]
    string NonHedgedOppositePolicy,
  [property: JsonPropertyName("structural_guard_mode")] string StructuralGuardMode,
  [property: JsonPropertyName("zone_reconcile_mode")] string ZoneReconcileMode,
  [property: JsonPropertyName("range_box_scale_out_enabled")]
    bool RangeBoxScaleOutEnabled,
  [property: JsonPropertyName("range_box_scale_out_threshold_pips")]
    int RangeBoxScaleOutThresholdPips,
  [property: JsonPropertyName("range_box_scale_out_trigger_pips")]
    int RangeBoxScaleOutTriggerPips,
  [property: JsonPropertyName("range_box_scale_out_fraction")]
    string RangeBoxScaleOutFraction,
  [property: JsonPropertyName("range_box_move_sl_to_be_after_scale_out")]
    bool RangeBoxMoveSlToBeAfterScaleOut,
  [property: JsonPropertyName("execution_zone_max_width_atr")]
    string ExecutionZoneMaxWidthAtr,
  [property: JsonPropertyName("execution_zone_max_width_pips")]
    string ExecutionZoneMaxWidthPips,
  [property: JsonPropertyName("post_fill_target_fallback")]
    string PostFillTargetFallback,
  [property: JsonPropertyName("position_missing_confirmations")]
    int PositionMissingConfirmations,
  [property: JsonPropertyName("position_missing_recheck_seconds")]
    int PositionMissingRecheckSeconds,
  [property: JsonPropertyName("equity_table_version")] string EquityTableVersion,
  [property: JsonPropertyName("zone_scale_undersized_policy")]
    string ZoneScaleUndersizedPolicy,
  [property: JsonPropertyName("group_close_allocation")] string GroupCloseAllocation,
  [property: JsonPropertyName("unfilled_leg_after_tp_policy")]
    string UnfilledLegAfterTpPolicy,
  [property: JsonPropertyName("reaction_market_fraction")] string ReactionMarketFraction,
  [property: JsonPropertyName("reaction_scale_fraction")] string ReactionScaleFraction,
  [property: JsonPropertyName("reaction_scale_enabled")] bool ReactionScaleEnabled,
  [property: JsonPropertyName("reaction_scale_invalid_policy")]
    string ReactionScaleInvalidPolicy,
  [property: JsonPropertyName("reaction_scale_step_atr")] string ReactionScaleStepAtr
);

public enum CtraderConfigurationSource
{
  Environment,
  Manifest,
}

public enum CtraderManifestParityMode
{
  Off,
  Warn,
  Enforce,
}

public static class RuntimeManifestBootstrap
{
  public const string ManifestFileEnv = "APEXVOID_RUNTIME_MANIFEST_FILE";
  public const string ConfigurationSourceEnv = "CTRADER_CONFIGURATION_SOURCE";
  public const string ParityModeEnv = "CTRADER_MANIFEST_PARITY_MODE";

  public static CtraderConfigurationSource ReadSource()
  {
    var raw = (Environment.GetEnvironmentVariable(ConfigurationSourceEnv) ?? "environment")
      .Trim()
      .ToLowerInvariant();
    return raw switch
    {
      "environment" => CtraderConfigurationSource.Environment,
      "manifest" => CtraderConfigurationSource.Manifest,
      _ => throw new InvalidOperationException(
        $"{ConfigurationSourceEnv} must be environment or manifest; got '{raw}'"
      ),
    };
  }

  public static CtraderManifestParityMode ReadParityMode()
  {
    var raw = (Environment.GetEnvironmentVariable(ParityModeEnv) ?? "enforce")
      .Trim()
      .ToLowerInvariant();
    return raw switch
    {
      "off" => CtraderManifestParityMode.Off,
      "warn" => CtraderManifestParityMode.Warn,
      "enforce" => CtraderManifestParityMode.Enforce,
      _ => throw new InvalidOperationException(
        $"{ParityModeEnv} must be off, warn, or enforce; got '{raw}'"
      ),
    };
  }

  public static string RequireManifestPath()
  {
    var path = Environment.GetEnvironmentVariable(ManifestFileEnv);
    if (string.IsNullOrWhiteSpace(path))
    {
      throw new InvalidOperationException(
        $"{ManifestFileEnv} must be set when loading the runtime manifest"
      );
    }
    return path;
  }
}

public static class ManifestDecimal
{
  public static decimal Parse(string value, string path)
  {
    if (!decimal.TryParse(
      value,
      NumberStyles.Number,
      CultureInfo.InvariantCulture,
      out var parsed
    ))
    {
      throw new InvalidOperationException(
        $"runtime manifest decimal parse failed at {path}: '{value}'"
      );
    }
    return parsed;
  }
}

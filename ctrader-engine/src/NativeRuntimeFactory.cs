using System.Globalization;

namespace ApexVoid.CTraderFeed;

/// <summary>
/// Builds the cTrader runtime directly from the resolved categorized YAML
/// document. Secrets and broker session bootstrap remain environment inputs;
/// all trading and instrument policy comes from the document.
/// </summary>
public sealed record NativeRuntimeConfiguration(
  FeedOptions Feed,
  AutoTradeOptions AutoTrade,
  InstrumentRuntimeRegistry Instruments,
  ConfigDocument Document
);

public static class NativeRuntimeFactory
{
  public static NativeRuntimeConfiguration Load(string rootPath)
  {
    var document = ConfigDocument.Resolve(rootPath);
    var account = CTraderAccountOptions.FromEnvironment(document);
    var trade = BuildAutoTrade(document, account.RedisUrl);
    var feed = BuildFeed(document, account);
    var instruments = BuildInstruments(document, feed);
    trade.Validate();
    return new NativeRuntimeConfiguration(feed, trade, instruments, document);
  }

  private static FeedOptions BuildFeed(
    ConfigDocument document,
    CTraderAccountOptions account
  )
  {
    var live = document.LiveInstruments();
    var first = live.Contains("XAU", StringComparer.OrdinalIgnoreCase)
      ? "XAU"
      : live.FirstOrDefault()
      ?? throw new ConfigurationV3Error("instruments has no live instrument");
    var instrument = document.InstrumentSection(first);
    var broker = RequiredString(instrument, "broker_symbol", first);
    var canonical = RequiredString(instrument, "canonical_symbol", first);
    var timeframes = RequiredStrings(instrument, "timeframes", first);
    return new FeedOptions(
      ClientId: account.ClientId,
      ClientSecret: account.ClientSecret,
      AccessToken: account.AccessToken,
      RefreshToken: account.RefreshToken,
      AccountId: account.AccountId,
      Host: account.Host,
      Port: account.Port,
      CTraderSymbol: broker,
      RedisSymbol: canonical,
      Timeframes: timeframes,
      BackfillBars: document.RequiredInt("runtime.feed.backfill_bars"),
      RedisUrl: account.RedisUrl,
      BarsWindowMax: document.RequiredInt("runtime.feed.bars_window_max"),
      BarsChannel: document.RequiredString("runtime.feed.bars_channel"),
      BarQualityLookback: document.RequiredInt("runtime.feed.bar_quality_lookback"),
      HeartbeatFile: account.HeartbeatFile,
      AutoTradeHeartbeatFile: account.AutoTradeHeartbeatFile,
      RefreshTokenKey: account.RefreshTokenKey,
      RefreshTokenFile: account.RefreshTokenFile,
      RequestTimeout: account.RequestTimeout,
      TokenRefreshLead: account.TokenRefreshLead,
      TokenCheckInterval: account.TokenCheckInterval,
      ExpectedBroker: document.RequiredString("runtime.broker.expected_broker")
    );
  }

  private static AutoTradeOptions BuildAutoTrade(
    ConfigDocument c,
    string redisUrl
  )
  {
    var xau = c.InstrumentSection("XAU");
    var geometry = c.GeometryFor("XAU");
    var targets = c.RequiredInts("execution.targeting.default_ladder_pips");
    var weights = c.RequiredInts("execution.targeting.tp_weights");
    var profile = c.RequiredString("runtime.environment").Equals(
      "demo_eval", StringComparison.OrdinalIgnoreCase
    ) ? "demo_eval" : "conservative";
    return new AutoTradeOptions(
      Enabled: c.RequiredBool("auto_algo.enabled"),
      DryRun: c.RequiredBool("auto_algo.dry_run"),
      ExpectedBroker: c.RequiredString("runtime.broker.expected_broker"),
      StopLossDistance: c.RequiredDecimal("execution.stops.sl_distance"),
      TargetsPips: targets,
      TargetWeights: weights,
      BreakEvenBufferTicks: c.RequiredInt("execution.stops.be_buffer_ticks"),
      CandidateMaxAgeSeconds: c.RequiredInt("auto_algo.lifecycle.candidate.execution_maximum_age_seconds"),
      SpotMaxAgeSeconds: c.RequiredInt("analysis.spot.maximum_age_seconds"),
      MaxSpreadPips: c.RequiredInt("execution.entry.max_spread_pips"),
      MaxEntryDistancePips: c.RequiredInt("execution.entry.maximum_chase_distance_pips"),
      MinConfluence: c.RequiredInt("auto_algo.actionability.gates.min_confluence"),
      PollMilliseconds: c.RequiredInt("execution.entry.poll_ms"),
      CandidateStream: c.RequiredString("transport.redis_streams.candidates"),
      EventStream: c.RequiredString("transport.redis_streams.events"),
      Label: c.RequiredString("execution.policy.label"),
      RequireDemoOnlyToken: OptionalBool(c, "execution.policy.require_demo_only_token", false),
      RiskPercent: OptionalDecimal(c, "auto_algo.risk.sizing.risk_pct", 2m),
      SizingMode: OptionalString(c, "auto_algo.risk.sizing.mode", "equity_table"),
      PipValuePerLot: OptionalDecimal(
        xau,
        "contract.pip_value_per_lot",
        (decimal)geometry.PipSize * RequiredDecimal(xau, "contract.contract_units_per_lot", "XAU")
      ),
      PipSize: RequiredDecimal(xau, "contract.pip_size", "XAU"),
      ContractSize: RequiredDecimal(xau, "contract.contract_units_per_lot", "XAU"),
      MaxTranches: c.RequiredInt("execution.policy.max_tranches"),
      AddRiskFraction: c.RequiredDecimal("auto_algo.risk.sizing.add_risk_fraction"),
      AddMaxAgeBars: c.RequiredInt("auto_algo.lifecycle.scaling.max_age_bars"),
      AddCooldownBars: c.RequiredInt("auto_algo.lifecycle.scaling.cooldown_bars"),
      AddLevelBufferAtr: c.RequiredDecimal("execution.scaling.add.level_buffer_atr"),
      AddStopBufferAtr: c.RequiredDecimal("execution.scaling.add.stop_buffer_atr"),
      AddMinStopPips: c.RequiredInt("execution.scaling.add.min_stop_pips"),
      AddRequireRiskFree: c.RequiredBool("auto_algo.risk.sizing.add_require_risk_free"),
      ZoneFillEnabled: c.RequiredBool("execution.zone_scaling.fill_enabled"),
      ZoneFillMinLots: OptionalDecimal(c, "execution.zone_scaling.fill_min_lots", 0.09m),
      ZoneFillMinAtr: OptionalDecimal(c, "execution.zone_scaling.fill_min_atr", 0.5m),
      ZoneFillTtlBars: OptionalInt(c, "auto_algo.lifecycle.zone.fill_ttl_bars", 3),
      ZoneFillFallbackEnabled: c.RequiredBool("execution.zone_scaling.fill_fallback_enabled"),
      InsideZoneMarketEntryEnabled: c.RequiredBool("execution.entry.inside_zone_market_entry_enabled"),
      BoxMinRiskReward: OptionalDecimal(c, "execution.range.min_rr", 1.25m),
      TrendStopMinPips: c.RequiredInt("execution.stops.trend.minimum_pips"),
      TrendStopMaxPips: c.RequiredInt("execution.trend.stop_max_pips"),
      StopPushBeyondZone: c.RequiredBool("execution.stops.stop_push_beyond_zone"),
      EntryContractTolerancePips: c.RequiredDecimal("execution.entry.contract_tolerance_pips"),
      BrokerAbsenceConfirmations: c.RequiredInt("execution.broker_recovery.absence_confirmations"),
      BrokerAbsenceRecheckSeconds: c.RequiredInt("auto_algo.lifecycle.reconciliation.absence_recheck_seconds"),
      BrokerRecoveryTimeoutSeconds: c.RequiredInt("auto_algo.lifecycle.reconciliation.recovery_timeout_seconds"),
      WickStopBufferAtr: c.RequiredDecimal("execution.stops.wick_stop_buffer_atr"),
      RangeFlipEnabled: c.RequiredBool("auto_algo.strategies.range_reversion.flip_enabled"),
      FlipExitBufferPips: OptionalInt(c, "execution.policy.flip_exit_buffer_pips", 10),
      FlipConfirmTimeoutSeconds: OptionalInt(c, "auto_algo.lifecycle.range_flip.confirm_timeout_seconds", 30),
      ZoneCooldownMinutes: OptionalInt(c, "auto_algo.lifecycle.zone.cooldown_minutes", 60),
      ZoneCooldownEnabled: c.RequiredBool("auto_algo.lifecycle.zone.cooldown_enabled"),
      AddPullbackEnabled: c.RequiredBool("execution.scaling.add.pullback_enabled"),
      AddPullbackMinRetrace: c.RequiredDecimal("execution.scaling.add.pullback_min_retrace"),
      AddPullbackMaxRetrace: c.RequiredDecimal("execution.scaling.add.pullback_max_retrace"),
      AddMaxGroupRiskPct: c.RequiredDecimal("auto_algo.risk.sizing.add_max_group_risk_pct"),
      AddSizeRatio: c.RequiredDecimal("execution.scaling.add.size_ratio"),
      RangeTargetsPips: c.RequiredInts("execution.targeting.range_ladder_pips"),
      RangeTpBufferPips: c.RequiredDecimal("execution.range.tp_buffer_pips"),
      Profile: profile,
      RequireDemoAccount: c.RequiredBool("runtime.broker.require_demo"),
      AllowConcurrentStrategies: c.RequiredBool("auto_algo.risk.exposure.allow_concurrent_strategies"),
      AllowHedgedXau: c.RequiredBool("auto_algo.risk.exposure.allow_hedged_xau"),
      RequireFlatForRange: c.RequiredBool("auto_algo.risk.exposure.require_flat_for_range"),
      RangeTwoSidedEnabled: c.RequiredBool("auto_algo.strategies.range_reversion.two_sided_enabled"),
      MultiMatchEnabled: c.RequiredBool("auto_algo.strategies.matching.multiple_matches_enabled"),
      TrackAllStructuralMatches: c.RequiredBool("auto_algo.strategies.matching.track_all_structural_matches"),
      RedisUrl: redisUrl,
      CanonicalSymbol: geometry.CanonicalSymbol,
      CandidateContractVersion: c.RequiredInt("transport.redis_streams.candidate_version"),
      ContractMode: c.RequiredString("runtime.execution_contract.mode"),
      TradePlanStream: c.RequiredString("transport.redis_streams.trade_plans"),
      ManualAlgoEnabled: c.RequiredBool("manual_algo.runtime.enabled"),
      TrendEnabled: c.RequiredBool("auto_algo.strategies.trend.enabled"),
      RangeEnabled: c.RequiredBool("auto_algo.strategies.range_reversion.enabled"),
      MappedZoneEnabled: c.RequiredBool("auto_algo.strategies.mapped_zone.enabled"),
      MarketMapGuardEnabled: c.RequiredBool("auto_algo.actionability.gates.market_map_guard_enabled"),
      MapThesisLockEnabled: c.RequiredBool("execution.mapped_zone.thesis_lock_enabled"),
      StrategyMatchEnabled: c.RequiredBool("auto_algo.strategy_match_enabled"),
      BreakoutEnabled: c.RequiredBool("auto_algo.strategies.breakout.breakout_enabled"),
      RetestEnabled: c.RequiredBool("auto_algo.strategies.selection.retest_enabled"),
      ReactionEnabled: c.RequiredBool("auto_algo.strategies.reaction.enabled"),
      LiquidityReversalEnabled: c.RequiredBool("auto_algo.strategies.reaction.liquidity_reversal.enabled"),
      AllowCounterBias: c.RequiredBool("auto_algo.actionability.counter_bias.allowed"),
      CandidateStorageTtlSeconds: c.RequiredInt("auto_algo.lifecycle.candidate.storage_ttl_seconds"),
      Symbols: c.LiveInstruments(),
      ConfigManifestVersion: 2,
      NonHedgedOppositePolicy: c.RequiredString("auto_algo.risk.exposure.non_hedged_opposite_policy"),
      StructuralGuardMode: c.RequiredString("auto_algo.actionability.structural_guard.guard_mode"),
      ZoneReconcileMode: c.RequiredString("auto_algo.actionability.zone_reconciliation.mode"),
      RangeBoxScaleOutEnabled: c.RequiredBool("auto_algo.strategies.range_reversion.box_scale_out_enabled"),
      RangeBoxScaleOutThresholdPips: c.RequiredInt("execution.range.box_scale_out_threshold_pips"),
      RangeBoxScaleOutTriggerPips: c.RequiredInt("execution.range.box_scale_out_trigger_pips"),
      RangeBoxScaleOutFraction: c.RequiredDecimal("execution.range.box_scale_out_fraction"),
      RangeBoxMoveSlToBeAfterScaleOut: c.RequiredBool("execution.range.box_move_sl_to_be_after_scale_out"),
      ExecutionZoneMaxWidthAtr: c.RequiredDecimal("execution.policy.execution_zone_max_width_atr"),
      ExecutionZoneMaxWidthPips: c.RequiredDecimal("execution.policy.execution_zone_max_width_pips"),
      PostFillTargetFallback: c.RequiredString("execution.targeting.post_fill_target_fallback"),
      PositionMissingConfirmations: c.RequiredInt("auto_algo.lifecycle.reconciliation.missing_confirmations"),
      PositionMissingRecheckSeconds: c.RequiredInt("auto_algo.lifecycle.reconciliation.missing_recheck_seconds"),
      EquityTableVersion: c.RequiredString("auto_algo.risk.sizing.equity_table_version"),
      ZoneScaleUndersizedPolicy: c.RequiredString("execution.zone_scaling.scale_undersized_policy"),
      GroupCloseAllocation: c.RequiredString("execution.policy.group_close_allocation"),
      UnfilledLegAfterTpPolicy: c.RequiredString("execution.targeting.unfilled_leg_after_tp_policy"),
      ReactionMarketFraction: c.RequiredDecimal("execution.reaction.market_fraction"),
      ReactionScaleFraction: c.RequiredDecimal("execution.reaction.scale_fraction"),
      ReactionScaleEnabled: c.RequiredBool("auto_algo.strategies.reaction.scale_enabled"),
      ReactionScaleInvalidPolicy: c.RequiredString("execution.reaction.scale_invalid_policy"),
      ReactionScaleStepAtr: c.RequiredDecimal("execution.reaction.scale_step_atr"),
      ReactionRiskLegEnabled: c.RequiredBool("execution.reaction_risk_leg.enabled")
    );
  }

  private static InstrumentRuntimeRegistry BuildInstruments(
    ConfigDocument c,
    FeedOptions sharedFeed
  )
  {
    var declared = c.Section("instruments");
    var runtimes = new List<InstrumentRuntime>();
    foreach (var symbol in declared.Keys.Order(StringComparer.Ordinal))
    {
      var section = c.InstrumentSection(symbol);
      var rollout = InstrumentRolloutGates.Parse(RequiredString(section, "rollout", symbol));
      if (rollout == InstrumentRollout.Disabled) continue;
      var geometry = c.GeometryFor(symbol);
      var aliases = OptionalStrings(section, "aliases");
      var timeframes = RequiredStrings(section, "timeframes", symbol);
      var pipValue = OptionalDecimal(
        section,
        "contract.pip_value_per_lot",
        (decimal)geometry.PipSize * RequiredDecimal(section, "contract.contract_units_per_lot", symbol)
      );
      runtimes.Add(new InstrumentRuntime
      {
        InstrumentId = symbol,
        Aliases = aliases,
        Feed = new FeedInstrumentOptions(
          symbol,
          geometry.CanonicalSymbol,
          geometry.BrokerSymbol,
          geometry.CanonicalSymbol,
          timeframes,
          sharedFeed.BackfillBars,
          sharedFeed.BarsWindowMax,
          sharedFeed.BarsChannel,
          sharedFeed.BarQualityLookback,
          rollout
        ),
        Execution = new ExecutionInstrumentOptions(
          symbol,
          geometry.CanonicalSymbol,
          rollout,
          (decimal)geometry.PipSize,
          RequiredDecimal(section, "contract.contract_units_per_lot", symbol),
          [geometry.CanonicalSymbol],
          pipValue
        )
      });
    }
    return new InstrumentRuntimeRegistry(runtimes);
  }

  private static string RequiredString(IReadOnlyDictionary<string, object?> map, string path, string symbol) =>
    Value(map, path, symbol) is string value && !string.IsNullOrWhiteSpace(value)
      ? value
      : throw new ConfigurationV3Error($"instrument {symbol}: missing or invalid {path}");

  private static IReadOnlyList<string> RequiredStrings(IReadOnlyDictionary<string, object?> map, string path, string symbol)
  {
    if (Value(map, path, symbol) is not IEnumerable<object?> items)
      throw new ConfigurationV3Error($"instrument {symbol}: missing or invalid {path}");
    var result = items.Select(item => item as string ?? "").Where(item => item.Length > 0).ToArray();
    if (result.Length == 0) throw new ConfigurationV3Error($"instrument {symbol}: {path} is empty");
    return result;
  }

  private static IReadOnlyList<string> OptionalStrings(IReadOnlyDictionary<string, object?> map, string path) =>
    TryValue(map, path, out var raw) && raw is IEnumerable<object?> items
      ? items.Select(item => item as string ?? "").Where(item => item.Length > 0).ToArray()
      : [];

  private static decimal RequiredDecimal(IReadOnlyDictionary<string, object?> map, string path, string symbol) =>
    ToDecimal(Value(map, path, symbol), $"instrument {symbol}: {path}");

  private static object? Value(IReadOnlyDictionary<string, object?> map, string path, string symbol)
  {
    object? cursor = map;
    foreach (var part in path.Split('.'))
    {
      if (cursor is not IReadOnlyDictionary<string, object?> section || !section.TryGetValue(part, out cursor))
        throw new ConfigurationV3Error($"instrument {symbol}: missing {path}");
    }
    return cursor;
  }

  private static bool OptionalBool(ConfigDocument c, string path, bool fallback) =>
    c.Get(path) is null ? fallback : c.RequiredBool(path);

  private static int OptionalInt(ConfigDocument c, string path, int fallback) =>
    c.Get(path) is null ? fallback : c.RequiredInt(path);

  private static decimal OptionalDecimal(ConfigDocument c, string path, decimal fallback) =>
    c.Get(path) is null ? fallback : c.RequiredDecimal(path);

  private static string OptionalString(ConfigDocument c, string path, string fallback) =>
    c.Get(path) is null ? fallback : c.RequiredString(path);

  private static decimal OptionalDecimal(
    IReadOnlyDictionary<string, object?> map,
    string path,
    decimal fallback
  ) => map is null ? fallback : TryValue(map, path, out var value) ? ToDecimal(value, path) : fallback;

  private static bool TryValue(IReadOnlyDictionary<string, object?> map, string path, out object? value)
  {
    value = map;
    foreach (var part in path.Split('.'))
    {
      if (value is not IReadOnlyDictionary<string, object?> section || !section.TryGetValue(part, out value))
      {
        value = null;
        return false;
      }
    }
    return true;
  }

  private static decimal ToDecimal(object? value, string path)
  {
    if (value is null) throw new ConfigurationV3Error($"missing required value {path}");
    try { return Convert.ToDecimal(value, CultureInfo.InvariantCulture); }
    catch (Exception ex) when (ex is FormatException or InvalidCastException or OverflowException)
    { throw new ConfigurationV3Error($"invalid decimal value {path}: {value}", ex); }
  }
}

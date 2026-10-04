namespace ApexVoid.CTraderFeed;

public sealed record AutoTradeOptions(
  bool Enabled,
  bool DryRun,
  string ExpectedBroker,
  decimal StopLossDistance,
  IReadOnlyList<int> TargetsPips,
  IReadOnlyList<int> TargetWeights,
  int BreakEvenBufferTicks,
  int CandidateMaxAgeSeconds,
  int SpotMaxAgeSeconds,
  int MaxSpreadPips,
  int MaxEntryDistancePips,
  int MinConfluence,
  int PollMilliseconds,
  string CandidateStream,
  string EventStream,
  string Label,
  bool RequireDemoOnlyToken = false,
  decimal RiskPercent = 2m,
  string SizingMode = "min",
  decimal PipValuePerLot = 10m,
  decimal PipSize = 0.1m,
  decimal ContractSize = 100m,
  int MaxTranches = 2,
  decimal AddRiskFraction = 0.5m,
  int AddMaxAgeBars = 3,
  int AddCooldownBars = 3,
  decimal AddLevelBufferAtr = 1m,
  decimal AddStopBufferAtr = 0.3m,
  int AddMinStopPips = 30,
  bool AddRequireRiskFree = false,
  bool ZoneFillEnabled = false,
  decimal ZoneFillMinLots = 0.09m,
  decimal ZoneFillMinAtr = 0.5m,
  int ZoneFillTtlBars = 3,
  bool ZoneFillFallbackEnabled = true,
  bool InsideZoneMarketEntryEnabled = true,
  decimal BoxMinRiskReward = 1.25m,
  int TrendStopMinPips = 40,
  int TrendStopMaxPips = 60,
  bool StopPushBeyondZone = true,
  // How far the executable entry may drift from Python's planned entry before
  // the approved absolute stop is no longer trustworthy for this candidate.
  decimal EntryContractTolerancePips = 3m,
  // Consecutive empty broker snapshots required before absence is confirmed.
  int BrokerAbsenceConfirmations = 2,
  // Minimum seconds between absence-confirmation snapshots.
  int BrokerAbsenceRecheckSeconds = 3,
  // Wall-clock budget for one recovery attempt before remaining StillUnknown.
  int BrokerRecoveryTimeoutSeconds = 30,
  decimal WickStopBufferAtr = 0.15m,
  bool RangeFlipEnabled = false,
  int FlipExitBufferPips = 10,
  int FlipConfirmTimeoutSeconds = 30,
  int ZoneCooldownMinutes = 60,
  bool ZoneCooldownEnabled = true,
  bool AddPullbackEnabled = false,
  decimal AddPullbackMinRetrace = 0.20m,
  decimal AddPullbackMaxRetrace = 0.70m,
  decimal AddMaxGroupRiskPct = 3.0m,
  decimal AddSizeRatio = 0.5m,
  IReadOnlyList<int>? RangeTargetsPips = null,
  decimal RangeTpBufferPips = 5m,
  string Profile = "conservative",
  bool RequireDemoAccount = true,
  bool AllowConcurrentStrategies = false,
  bool AllowHedgedXau = false,
  bool RequireFlatForRange = true,
  bool RangeTwoSidedEnabled = false,
  bool MultiMatchEnabled = false,
  bool TrackAllStructuralMatches = false,
  string RedisUrl = "redis://redis:6379/0",
  string CanonicalSymbol = "XAU",
  int CandidateContractVersion = 6,
  // Cross-service contract handshake. "v8_only" is the sole autonomous
  // contract in real deployments. This bare record default stays
  // "legacy_v6" deliberately: ProcessCandidateAsync rejects every
  // autonomous (non-manual-algo) candidate outright when
  // ContractMode == "v8_only", and hundreds of pre-existing tests build
  // AutoTradeOptions directly via a shared Options() helper that never
  // sets ContractMode, feeding autonomous V6 candidates through
  // RunSessionAsync and asserting they get placed - "v8_only" here would
  // make every one of those candidates rejected at the door, breaking
  // mechanical-execution tests (sizing, stops, targets, BE) that have
  // nothing to do with the TradePlan autonomous-path boundary.
  string ContractMode = "legacy_v6",
  string TradePlanStream = "execution:trade_plans",
  bool ManualAlgoEnabled = false,
  bool TrendEnabled = false,
  bool RangeEnabled = true,
  bool MappedZoneEnabled = true,
  bool MarketMapGuardEnabled = true,
  bool MapThesisLockEnabled = true,
  bool StrategyMatchEnabled = true,
  bool BreakoutEnabled = true,
  bool RetestEnabled = true,
  bool ReactionEnabled = true,
  bool LiquidityReversalEnabled = true,
  bool AllowCounterBias = true,
  int CandidateStorageTtlSeconds = 86400,
  IReadOnlyList<string>? Symbols = null,
  string NonHedgedOppositePolicy = "reject",
  string StructuralGuardMode = "balanced",
  string ZoneReconcileMode = "enforce",
  bool RangeBoxScaleOutEnabled = true,
  int RangeBoxScaleOutThresholdPips = 70,
  int RangeBoxScaleOutTriggerPips = 30,
  decimal RangeBoxScaleOutFraction = 0.50m,
  bool RangeBoxMoveSlToBeAfterScaleOut = false,
  decimal ExecutionZoneMaxWidthAtr = 2.0m,
  decimal ExecutionZoneMaxWidthPips = 100m,
  string PostFillTargetFallback = "fill_relative",
  // A tracked position missing from a single broker reconcile snapshot is
  // only "suspected" missing, not closed - it must be independently
  // confirmed absent across this many reconcile passes, each separated by
  // at least PositionMissingRecheckSeconds, before ReconcileAsync
  // terminalises it. See docs on the incident this guards against: a
  // transient reconcile gap must never delete an open position's tracking.
  int PositionMissingConfirmations = 2,
  int PositionMissingRecheckSeconds = 3,
  string EquityTableVersion = "owner_equity_v1",
  string ZoneScaleUndersizedPolicy = "single_entry",
  string GroupCloseAllocation = "pro_rata",
  // cancel = cancel remaining pending entry legs before TP1/BE/trail/
  // manual close/terminal invalidation. keep = leave them resting
  // (requires stop sync; not fully implemented for future fills).
  string UnfilledLegAfterTpPolicy = "cancel",
  // Reaction Key/Session/Trendline market_with_limit_scale: L1 market
  // fraction + L2 deeper-limit fraction. InvalidPolicy=single_market
  // collapses to 100% L1 market when two valid legs cannot be formed.
  decimal ReactionMarketFraction = 0.80m,
  decimal ReactionScaleFraction = 0.20m,
  bool ReactionScaleEnabled = true,
  string ReactionScaleInvalidPolicy = "single_market",
  // Owner 2026-09-16: lowered from 0.50 to match the Python-side default -
  // at 0.5x ATR the far leg's stop distance routinely exceeded the reaction
  // stop envelope (75-79 vs a 60-pip cap on live XAU candidates). Not
  // currently consumed for any leg-spacing math on this side (Python plans
  // leg prices; this engine executes them) - kept in sync for the
  // documented cross-service default, not because anything here reads it.
  decimal ReactionScaleStepAtr = 0.10m,
  // Owner 2026-09-16: same "trade-off" risk leg Manual Algo has always had
  // (see ManualAlgoRiskLegPrice/Volume in AutoTradeEngine.cs), extended to
  // every reaction-family TradePlan v8 entry with a real multi-leg ladder
  // (limit_ladder / market_with_limit_scale). Purely a C# engine addition,
  // same as Manual Algo's own risk leg - Python never knows this leg
  // exists, exactly mirroring how manual_intent.py never knew about
  // Manual Algo's. A kill switch, not a tuning knob - see
  // TradePlanRuntime.ReactionRiskLeg* for the actual pip/lot constants.
  bool ReactionRiskLegEnabled = true
)
{
  // Shared target-selection contract (app/autotrade/range_targets.py on the
  // Python side, same AUTO_TRADE_RANGE_TARGETS_PIPS env var) - previously
  // this executor independently hardcoded FullTakeProfitPips to exactly 50
  // or 70, duplicating a policy Python already owned and drifting from it
  // the moment the Python ladder changed. A null/empty override (e.g. a
  // test fixture that never sets it) falls back to the same "15,20,30,40,
  // 50,70" default Python uses.
  private static readonly IReadOnlyList<int> DefaultRangeTargetsPips =
    new[] { 15, 20, 30, 40, 50, 70 };

  // Only a missing (null) override falls back to the default - an
  // explicitly empty list is a misconfiguration and must fail Validate(),
  // not be silently papered over.
  public IReadOnlyList<int> EffectiveRangeTargetsPips =>
    RangeTargetsPips ?? DefaultRangeTargetsPips;

  public IReadOnlyList<string> EffectiveSymbols =>
    (Symbols ?? [CanonicalSymbol])
      .Select(value => value.Trim().ToUpperInvariant())
      .Where(value => value.Length > 0)
      .Distinct(StringComparer.Ordinal)
      .Order(StringComparer.Ordinal)
      .ToArray();

  public ExposurePolicy ExposurePolicy => (
    AllowConcurrentStrategies,
    AllowHedgedXau
  ) switch
  {
    (true, true) => ExposurePolicy.HedgedConcurrent,
    (true, false) => ExposurePolicy.SameDirectionConcurrent,
    _ => ExposurePolicy.FlatOnly,
  };

  public void Validate()
  {
    if (Profile is not "conservative" and not "demo_eval")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_PROFILE must be conservative or demo_eval"
      );
    }
    if (Profile == "demo_eval" && !RequireDemoAccount)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: demo_eval requires AUTO_TRADE_REQUIRE_DEMO_ACCOUNT=true"
      );
    }
    if (
      CandidateContractVersion != 6
      || string.IsNullOrWhiteSpace(CanonicalSymbol)
      || EffectiveSymbols.Count == 0
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: candidate contract version 6, symbols, and "
        + "canonical symbol must be configured"
      );
    }
    // Accepts legacy_v6 (V6 manage / mechanical tests) and v8_only (live
    // autonomous TradePlan). Historical prior TradePlan contract modes are gone.
    if (ContractMode is not "legacy_v6" and not "v8_only")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_CONTRACT_MODE must be legacy_v6 "
        + "or v8_only"
      );
    }
    if (StopLossDistance <= 0 || StopLossDistance > 6.5m)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_SL_DISTANCE must be greater than zero "
        + "and at most 6.5"
      );
    }
    if (PositionMissingConfirmations < 1)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_POSITION_MISSING_CONFIRMATIONS "
        + "must be at least 1"
      );
    }
    if (PositionMissingRecheckSeconds < 1)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_POSITION_MISSING_RECHECK_SECONDS "
        + "must be at least 1"
      );
    }
    if (TargetsPips.Count != 5 || TargetsPips.Any(value => value <= 0))
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_TARGET_PLANS_PIPS must contain "
        + "five positive targets"
      );
    }
    if (!TargetsPips.SequenceEqual(TargetsPips.OrderBy(value => value)))
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_TARGET_PLANS_PIPS must be ascending"
      );
    }
    if (
      TargetWeights.Count != TargetsPips.Count
      || TargetWeights.Any(value => value <= 0)
      || TargetWeights.Sum() != 100
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_TP_WEIGHTS must match target plans, "
        + "contain positive values, and sum to 100"
      );
    }
    if (BreakEvenBufferTicks < 0 || BreakEvenBufferTicks >= 1000)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_BE_BUFFER_TICKS must be non-negative "
        + "and below 1000"
      );
    }
    if (RiskPercent is < 0.1m or > 10m || PipValuePerLot <= 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: risk percent must be 0.1-10 and pip value positive"
      );
    }
    if (SizingMode is not "min" and not "table" and not "risk" and not "equity_table")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_SIZING_MODE must be one of "
        + "min, table, risk, equity_table"
      );
    }
    if (string.IsNullOrWhiteSpace(EquityTableVersion))
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_EQUITY_TABLE_VERSION must be set"
      );
    }
    if (GroupCloseAllocation is not "pro_rata")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_GROUP_CLOSE_ALLOCATION must be pro_rata"
      );
    }
    if (UnfilledLegAfterTpPolicy is not "cancel" and not "keep")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_UNFILLED_LEG_AFTER_TP_POLICY "
        + "must be cancel or keep"
      );
    }
    if (ZoneScaleUndersizedPolicy is not "single_entry" and not "reject")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_ZONE_SCALE_UNDERSIZED_POLICY "
        + "must be single_entry or reject"
      );
    }
    if (ReactionScaleInvalidPolicy is not "single_market" and not "reject")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_REACTION_SCALE_INVALID_POLICY "
        + "must be single_market or reject"
      );
    }
    if (
      ReactionMarketFraction <= 0
      || ReactionScaleFraction <= 0
      || Math.Abs(ReactionMarketFraction + ReactionScaleFraction - 1m) > 0.0001m
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_REACTION_MARKET_FRACTION + "
        + "AUTO_TRADE_REACTION_SCALE_FRACTION must be positive and sum to 1.0"
      );
    }
    if (ReactionScaleStepAtr < 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_REACTION_SCALE_STEP_ATR must be >= 0"
      );
    }

    if (PipSize <= 0 || ContractSize <= 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_XAU_PIP_SIZE and "
        + "AUTO_TRADE_XAU_CONTRACT_SIZE must be positive"
      );
    }
    var derivedPipValue = ContractSize * PipSize;
    if (PipValuePerLot != derivedPipValue)
    {
      throw new AutoTradeConfigurationException(
        $"Auto trade disabled: pip value inconsistent: PipValuePerLot="
        + $"{PipValuePerLot} but ContractSize {ContractSize} x PipSize "
        + $"{PipSize} = {derivedPipValue}"
      );
    }
    if (
      MaxTranches is < 1 or > 5
      || AddRiskFraction <= 0
      || AddRiskFraction > 1
      || AddMaxAgeBars <= 0
      || AddCooldownBars <= 0
      || AddLevelBufferAtr < 0
      || AddStopBufferAtr < 0
      || WickStopBufferAtr < 0
      || AddMinStopPips <= 0
      || AddMinStopPips > decimal.ToInt32(decimal.Floor(
        StopLossDistance / PipSize
      ))
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: scale-in settings are invalid"
      );
    }
    if (
      AddPullbackMinRetrace < 0
      || AddPullbackMaxRetrace <= AddPullbackMinRetrace
      || AddPullbackMaxRetrace > 1
      || AddMaxGroupRiskPct <= 0
      || AddMaxGroupRiskPct > 100
      || AddSizeRatio <= 0
      || AddSizeRatio > 1
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: pullback add settings are invalid"
      );
    }
    if (
      ZoneFillMinLots <= 0
      || ZoneFillMinAtr <= 0
      || ZoneFillTtlBars <= 0
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: zone-fill settings must be positive"
      );
    }
    if (ZoneCooldownMinutes <= 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_ZONE_COOLDOWN_MINUTES must be positive"
      );
    }
    if (BoxMinRiskReward is < 1m or > 3m)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_BOX_MIN_RR must be between 1 and 3"
      );
    }
    if (FlipExitBufferPips < 0 || FlipConfirmTimeoutSeconds <= 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: range-flip buffer must be non-negative and "
        + "confirmation timeout must be positive"
      );
    }
    if (BrokerAbsenceConfirmations < 2)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_BROKER_ABSENCE_CONFIRMATIONS must be "
        + "at least 2; a single broker snapshot never confirms absence"
      );
    }
    if (BrokerAbsenceRecheckSeconds <= 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_BROKER_ABSENCE_RECHECK_SECONDS must "
        + "be positive; a zero-second interval provides no visibility window"
      );
    }
    if (BrokerRecoveryTimeoutSeconds <= 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_BROKER_RECOVERY_TIMEOUT_SECONDS must "
        + "be positive"
      );
    }
    if (
      BrokerRecoveryTimeoutSeconds
      < BrokerAbsenceRecheckSeconds * (BrokerAbsenceConfirmations - 1)
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_BROKER_RECOVERY_TIMEOUT_SECONDS must "
        + "cover AUTO_TRADE_BROKER_ABSENCE_RECHECK_SECONDS x "
        + "(AUTO_TRADE_BROKER_ABSENCE_CONFIRMATIONS - 1) so the configured "
        + "quorum is achievable"
      );
    }
    if (MinConfluence is < 1 or > 3)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_MIN_CONFLUENCE must be between 1 and 3"
      );
    }
    if (
      TrendStopMinPips <= 0
      || TrendStopMaxPips < TrendStopMinPips
      || TrendStopMaxPips > StopLossDistance / PipSize
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_TREND_STOP_MIN_PIPS/MAX_PIPS must be "
        + "positive and MIN must not exceed MAX"
      );
    }
    if (
      EffectiveRangeTargetsPips.Count == 0
      || EffectiveRangeTargetsPips.Any(value => value <= 0)
      || RangeTpBufferPips < 0
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_RANGE_TARGETS_PIPS must contain "
        + "positive values and AUTO_TRADE_RANGE_TP_BUFFER_PIPS must be "
        + "non-negative"
      );
    }
    if (
      RangeBoxScaleOutThresholdPips <= 0
      || RangeBoxScaleOutTriggerPips <= 0
      || RangeBoxScaleOutTriggerPips >= RangeBoxScaleOutThresholdPips
      || RangeBoxScaleOutFraction <= 0m
      || RangeBoxScaleOutFraction >= 1m
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: Range Box scale-out settings invalid "
        + "(threshold > 0, trigger > 0, trigger < threshold, "
        + "0 < fraction < 1)"
      );
    }
    if (ExecutionZoneMaxWidthAtr <= 0 || ExecutionZoneMaxWidthPips <= 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_ATR and "
        + "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_PIPS must be positive"
      );
    }
    if (
      CandidateMaxAgeSeconds <= 0
      || CandidateStorageTtlSeconds <= 0
      || SpotMaxAgeSeconds <= 0
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: candidate max age, candidate storage TTL, "
        + "and spot max age must be positive"
      );
    }
    if (
      NonHedgedOppositePolicy is not "broker_netting"
        and not "close_then_reverse"
        and not "reject"
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY must be "
        + "broker_netting, close_then_reverse, or reject"
      );
    }
    if (
      StructuralGuardMode is not "observe"
        and not "balanced"
        and not "strict"
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_STRUCTURAL_GUARD_MODE must be "
        + "observe, balanced, or strict"
      );
    }
    if (
      ZoneReconcileMode is not "off"
        and not "shadow"
        and not "enforce"
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_ZONE_RECONCILE_MODE must be "
        + "off, shadow, or enforce"
      );
    }
  }

}

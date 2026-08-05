using System.Globalization;

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
  // Cross-service contract handshake. Must match Python's
  // AUTO_TRADE_CONTRACT_MODE exactly (checked in AutoTradeConfigHealth) -
  // see docs/adr-trade-plan-v7-boundary.md. "v7_only" is the sole
  // autonomous contract in real deployments (FromEnvironment resolves its
  // own default to "v7_only", below). This bare record default stays
  // "legacy_v6" deliberately: ProcessCandidateAsync (line ~1030) rejects
  // every autonomous (non-manual-algo) candidate outright when
  // ContractMode == "v7_only", and hundreds of pre-existing tests build
  // AutoTradeOptions directly via a shared Options() helper that never
  // sets ContractMode, feeding autonomous V6 candidates through
  // RunSessionAsync and asserting they get placed - "v7_only" here would
  // make every one of those candidates rejected at the door, breaking
  // mechanical-execution tests (sizing, stops, targets, BE) that have
  // nothing to do with the V7 autonomous-path boundary this task changed.
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
  int ConfigManifestVersion = 2,
  string NonHedgedOppositePolicy = "reject",
  IReadOnlyDictionary<string, string>? ConfigSources = null,
  IReadOnlyList<string>? DeprecatedVariables = null,
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
  decimal ReactionMarketFraction = 0.70m,
  decimal ReactionScaleFraction = 0.30m,
  bool ReactionScaleEnabled = true,
  string ReactionScaleInvalidPolicy = "single_market",
  decimal ReactionScaleStepAtr = 0.50m
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

  public static AutoTradeOptions FromEnvironment()
  {
    var resolver = new EnvironmentResolver();
    var profile = resolver.String(
      "AUTO_TRADE_PROFILE",
      "conservative"
    ).ToLowerInvariant();
    var demoEval = profile == "demo_eval";
    var profileSource = demoEval ? "profile_demo_eval" : "application_default";
    var requireDemoAccount = resolver.Bool(
      "AUTO_TRADE_REQUIRE_DEMO_ACCOUNT", true, profileSource
    );
    var mappedZoneEnabled = resolver.Bool(
      "AUTO_TRADE_MAPPED_ZONE_ENABLED",
      true,
      "application_default",
      "AUTO_TRADE_MARKET_MAP_STRATEGY_ENABLED"
    );
    var options = new AutoTradeOptions(
    Enabled: resolver.Bool(
      "AUTO_TRADE_ENABLED", demoEval, profileSource
    ),
    DryRun: resolver.Bool(
      "AUTO_TRADE_DRY_RUN", !demoEval, profileSource
    ),
    ExpectedBroker: resolver.String(
      "AUTO_TRADE_EXPECTED_BROKER", "fpmarkets"
    ),
    StopLossDistance: resolver.Decimal("AUTO_TRADE_SL_DISTANCE", 6.5m),
    TargetsPips: resolver.IntList(
      "AUTO_TRADE_TARGET_PLANS_PIPS",
      "30,60,90,120,200",
      "AUTO_TRADE_TP_PIPS"
    ),
    TargetWeights: resolver.IntList(
      "AUTO_TRADE_TP_WEIGHTS", "20,20,20,20,20"
    ),
    BreakEvenBufferTicks: resolver.Int(
      "AUTO_TRADE_BE_BUFFER_TICKS",
      6,
      "application_default",
      "AUTO_TRADE_BE_BUFFER_PIPS"
    ),
    CandidateMaxAgeSeconds: resolver.Int(
      "AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS",
      demoEval ? 420 : 90,
      profileSource,
      "AUTO_TRADE_CANDIDATE_MAX_AGE"
    ),
    SpotMaxAgeSeconds: resolver.Int(
      "AUTO_TRADE_SPOT_MAX_AGE_SECONDS",
      5,
      "application_default",
      "AUTO_TRADE_SPOT_MAX_AGE"
    ),
    MaxSpreadPips: resolver.Int("AUTO_TRADE_MAX_SPREAD_PIPS", 5),
    MaxEntryDistancePips: resolver.Int(
      "AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS", 40
    ),
    MinConfluence: resolver.Int("AUTO_TRADE_MIN_CONFLUENCE", 2),
    PollMilliseconds: resolver.Int("AUTO_TRADE_POLL_MS", 1000),
    CandidateStream: resolver.String(
      "AUTO_TRADE_CANDIDATE_STREAM",
      "auto_trade:candidates",
      "application_default",
      "AUTO_TRADE_STREAM"
    ),
    EventStream: resolver.String(
      "AUTO_TRADE_EVENT_STREAM", "auto_trade:events"
    ),
    Label: resolver.String("AUTO_TRADE_LABEL", "apexvoid-auto"),
    RequireDemoOnlyToken: resolver.Bool(
      "AUTO_TRADE_REQUIRE_DEMO_ONLY_TOKEN", false
    ),
    RiskPercent: resolver.Decimal("AUTO_TRADE_RISK_PCT", 2m),
    SizingMode: resolver.String("AUTO_TRADE_SIZING_MODE", "equity_table"),
    PipValuePerLot: resolver.Decimal(
      "AUTO_TRADE_PIP_VALUE_PER_LOT", 10m
    ),
    PipSize: resolver.Decimal(
      "AUTO_TRADE_XAU_PIP_SIZE",
      0.1m,
      "application_default",
      "AUTO_TRADE_PIP_SIZE"
    ),
    ContractSize: resolver.Decimal(
      "AUTO_TRADE_XAU_CONTRACT_SIZE",
      100m,
      "application_default",
      "AUTO_TRADE_CONTRACT_SIZE"
    ),
    MaxTranches: resolver.Int("AUTO_TRADE_MAX_TRANCHES", 2),
    AddRiskFraction: resolver.Decimal(
      "AUTO_TRADE_ADD_RISK_FRACTION", 0.5m
    ),
    AddMaxAgeBars: resolver.Int("AUTO_TRADE_ADD_MAX_AGE_BARS", 3),
    AddCooldownBars: resolver.Int("AUTO_TRADE_ADD_COOLDOWN_BARS", 3),
    AddLevelBufferAtr: resolver.Decimal(
      "AUTO_TRADE_ADD_LEVEL_BUFFER_ATR", 1m
    ),
    AddStopBufferAtr: resolver.Decimal(
      "AUTO_TRADE_ADD_STOP_BUFFER_ATR", 0.3m
    ),
    AddMinStopPips: resolver.Int("AUTO_TRADE_ADD_MIN_STOP_PIPS", 30),
    AddRequireRiskFree: resolver.Bool(
      "AUTO_TRADE_ADD_REQUIRE_RISK_FREE", false
    ),
    ZoneFillEnabled: resolver.Bool(
      "AUTO_TRADE_ZONE_FILL_ENABLED", demoEval, profileSource
    ),
    ZoneFillMinLots: resolver.Decimal(
      "AUTO_TRADE_ZONE_FILL_MIN_LOTS", 0.09m
    ),
    ZoneFillMinAtr: resolver.Decimal(
      "AUTO_TRADE_ZONE_FILL_MIN_ATR", 0.5m
    ),
    ZoneFillTtlBars: resolver.Int("AUTO_TRADE_ZONE_FILL_TTL_BARS", 3),
    ZoneFillFallbackEnabled: resolver.Bool(
      "AUTO_TRADE_ZONE_FILL_FALLBACK_ENABLED", true
    ),
    InsideZoneMarketEntryEnabled: resolver.Bool(
      "AUTO_TRADE_INSIDE_ZONE_MARKET_ENTRY_ENABLED",
      true
    ),
    BoxMinRiskReward: resolver.Decimal("AUTO_TRADE_BOX_MIN_RR", 1.25m),
    TrendStopMinPips: resolver.Int("AUTO_TRADE_TREND_STOP_MIN_PIPS", 40),
    TrendStopMaxPips: resolver.Int("AUTO_TRADE_TREND_STOP_MAX_PIPS", 60),
    StopPushBeyondZone: resolver.Bool(
      "AUTO_TRADE_STOP_PUSH_BEYOND_ZONE", true
    ),
    EntryContractTolerancePips: resolver.Decimal(
      "AUTO_TRADE_ENTRY_CONTRACT_TOLERANCE_PIPS", 3m
    ),
    BrokerAbsenceConfirmations: resolver.Int(
      "AUTO_TRADE_BROKER_ABSENCE_CONFIRMATIONS", 2
    ),
    BrokerAbsenceRecheckSeconds: resolver.Int(
      "AUTO_TRADE_BROKER_ABSENCE_RECHECK_SECONDS", 3
    ),
    BrokerRecoveryTimeoutSeconds: resolver.Int(
      "AUTO_TRADE_BROKER_RECOVERY_TIMEOUT_SECONDS", 30
    ),
    WickStopBufferAtr: resolver.Decimal(
      "AUTO_TRADE_WICK_STOP_BUFFER_ATR", 0.15m
    ),
    RangeFlipEnabled: resolver.Bool(
      "AUTO_TRADE_RANGE_FLIP_ENABLED", demoEval, profileSource
    ),
    FlipExitBufferPips: resolver.Int(
      "AUTO_TRADE_FLIP_EXIT_BUFFER_PIPS", 10
    ),
    FlipConfirmTimeoutSeconds: resolver.Int(
      "AUTO_TRADE_FLIP_CONFIRM_TIMEOUT_SECONDS",
      30
    ),
    ZoneCooldownMinutes: resolver.Int(
      "AUTO_TRADE_ZONE_COOLDOWN_MINUTES", 60
    ),
    ZoneCooldownEnabled: resolver.Bool(
      "AUTO_TRADE_ZONE_COOLDOWN_ENABLED", !demoEval, profileSource
    ),
    AddPullbackEnabled: resolver.Bool(
      "AUTO_TRADE_ADD_PULLBACK_ENABLED", false
    ),
    AddPullbackMinRetrace: resolver.Decimal(
      "AUTO_TRADE_ADD_PULLBACK_MIN_RETRACE", 0.20m
    ),
    AddPullbackMaxRetrace: resolver.Decimal(
      "AUTO_TRADE_ADD_PULLBACK_MAX_RETRACE", 0.70m
    ),
    AddMaxGroupRiskPct: resolver.Decimal(
      "AUTO_TRADE_ADD_MAX_GROUP_RISK_PCT", 3.0m
    ),
    AddSizeRatio: resolver.Decimal("AUTO_TRADE_ADD_SIZE_RATIO", 0.5m),
    RangeTargetsPips: resolver.IntList(
      "AUTO_TRADE_RANGE_TARGETS_PIPS", "15,20,30,40,50,70"
    ),
    RangeTpBufferPips: resolver.Decimal(
      "AUTO_TRADE_RANGE_TP_BUFFER_PIPS", 3m
    ),
    Profile: profile,
    RequireDemoAccount: requireDemoAccount,
    AllowConcurrentStrategies: resolver.Bool(
      "AUTO_TRADE_ALLOW_CONCURRENT_STRATEGIES",
      demoEval,
      profileSource
    ),
    AllowHedgedXau: resolver.Bool(
      "AUTO_TRADE_ALLOW_HEDGED_XAU", demoEval, profileSource
    ),
    RequireFlatForRange: resolver.Bool(
      "AUTO_TRADE_REQUIRE_FLAT_FOR_RANGE", !demoEval, profileSource
    ),
    RangeTwoSidedEnabled: resolver.Bool(
      "AUTO_TRADE_RANGE_TWO_SIDED_ENABLED",
      demoEval,
      profileSource
    ),
    MultiMatchEnabled: resolver.Bool(
      "AUTO_TRADE_MULTI_MATCH_ENABLED", demoEval, profileSource
    ),
    TrackAllStructuralMatches: resolver.Bool(
      "AUTO_TRADE_TRACK_ALL_STRUCTURAL_MATCHES",
      demoEval,
      profileSource
    ),
    RedisUrl: resolver.String("REDIS_URL", "redis://redis:6379/0"),
    CanonicalSymbol: resolver.String(
      "AUTO_TRADE_CANONICAL_SYMBOL", "XAU"
    ).ToUpperInvariant(),
    CandidateContractVersion: resolver.Int(
      "AUTO_TRADE_CANDIDATE_CONTRACT_VERSION", 6
    ),
    ContractMode: resolver.String(
      "AUTO_TRADE_CONTRACT_MODE", "v7_only"
    ).ToLowerInvariant(),
    TradePlanStream: resolver.String(
      "AUTO_TRADE_TRADE_PLAN_STREAM", "execution:trade_plans"
    ),
    ManualAlgoEnabled: resolver.Bool("MANUAL_ALGO_ENABLED", false),
    TrendEnabled: resolver.Bool(
      "AUTO_TRADE_TREND_ENABLED", demoEval, profileSource
    ),
    RangeEnabled: resolver.Bool("AUTO_TRADE_RANGE_ENABLED", true),
    MappedZoneEnabled: mappedZoneEnabled,
    MarketMapGuardEnabled: resolver.Bool(
      "AUTO_TRADE_MARKET_MAP_GUARD_ENABLED",
      mappedZoneEnabled
    ),
    MapThesisLockEnabled: resolver.Bool(
      "AUTO_TRADE_MAP_THESIS_LOCK_ENABLED",
      true
    ),
    StrategyMatchEnabled: resolver.Bool(
      "AUTO_TRADE_STRATEGY_MATCH_ENABLED",
      true,
      "application_default",
      "AUTO_TRADE_STRATEGY_BRIDGE_ENABLED",
      "AUTO_TRADE_FORMING_GATE_ENABLED"
    ),
    BreakoutEnabled: resolver.Bool("AUTO_TRADE_BREAKOUT_ENABLED", true),
    RetestEnabled: resolver.Bool("AUTO_TRADE_RETEST_ENABLED", true),
    ReactionEnabled: resolver.Bool("AUTO_TRADE_REACTION_ENABLED", true),
    LiquidityReversalEnabled: resolver.Bool(
      "AUTO_TRADE_LIQUIDITY_REVERSAL_ENABLED",
      true
    ),
    AllowCounterBias: resolver.Bool(
      "AUTO_TRADE_ALLOW_COUNTER_BIAS", demoEval, profileSource
    ),
    CandidateStorageTtlSeconds: resolver.Int(
      "AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS",
      demoEval ? 604800 : 86400,
      profileSource,
      "AUTO_TRADE_CANDIDATE_TTL"
    ),
    Symbols: resolver.StringList("AUTO_TRADE_SYMBOLS", "XAU"),
    ConfigManifestVersion: 2,
    NonHedgedOppositePolicy: resolver.String(
      "AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY",
      demoEval ? "broker_netting" : "reject",
      profileSource
    ).ToLowerInvariant(),
    StructuralGuardMode: resolver.String(
      "AUTO_TRADE_STRUCTURAL_GUARD_MODE",
      demoEval ? "observe" : requireDemoAccount ? "balanced" : "strict",
      profileSource
    ).ToLowerInvariant(),
    ZoneReconcileMode: resolver.String(
      "AUTO_TRADE_ZONE_RECONCILE_MODE",
      demoEval ? "shadow" : "enforce",
      profileSource
    ),
    RangeBoxScaleOutEnabled: resolver.Bool(
      "AUTO_TRADE_RANGE_BOX_SCALE_OUT_ENABLED", true
    ),
    RangeBoxScaleOutThresholdPips: resolver.Int(
      "AUTO_TRADE_RANGE_BOX_SCALE_OUT_THRESHOLD_PIPS", 70
    ),
    RangeBoxScaleOutTriggerPips: resolver.Int(
      "AUTO_TRADE_RANGE_BOX_SCALE_OUT_TRIGGER_PIPS", 30
    ),
    RangeBoxScaleOutFraction: resolver.Decimal(
      "AUTO_TRADE_RANGE_BOX_SCALE_OUT_FRACTION", 0.50m
    ),
    RangeBoxMoveSlToBeAfterScaleOut: resolver.Bool(
      "AUTO_TRADE_RANGE_BOX_MOVE_SL_TO_BE_AFTER_SCALE_OUT", false
    ),
    ExecutionZoneMaxWidthAtr: resolver.Decimal(
      "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_ATR", 2.0m
    ),
    ExecutionZoneMaxWidthPips: resolver.Decimal(
      "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_PIPS", 100m
    ),
    PostFillTargetFallback: resolver.String(
      "AUTO_TRADE_POST_FILL_TARGET_FALLBACK",
      "fill_relative"
    ).Trim().ToLowerInvariant(),
    PositionMissingConfirmations: resolver.Int(
      "AUTO_TRADE_POSITION_MISSING_CONFIRMATIONS", 2
    ),
    PositionMissingRecheckSeconds: resolver.Int(
      "AUTO_TRADE_POSITION_MISSING_RECHECK_SECONDS", 3
    ),
    EquityTableVersion: resolver.String(
      "AUTO_TRADE_EQUITY_TABLE_VERSION", "owner_equity_v1"
    ),
    ZoneScaleUndersizedPolicy: resolver.String(
      "AUTO_TRADE_ZONE_SCALE_UNDERSIZED_POLICY", "single_entry"
    ),
    GroupCloseAllocation: resolver.String(
      "AUTO_TRADE_GROUP_CLOSE_ALLOCATION", "pro_rata"
    ),
    UnfilledLegAfterTpPolicy: resolver.String(
      "AUTO_TRADE_UNFILLED_LEG_AFTER_TP_POLICY", "cancel"
    ).Trim().ToLowerInvariant(),
    ReactionMarketFraction: resolver.Decimal(
      "AUTO_TRADE_REACTION_MARKET_FRACTION", 0.70m
    ),
    ReactionScaleFraction: resolver.Decimal(
      "AUTO_TRADE_REACTION_SCALE_FRACTION", 0.30m
    ),
    ReactionScaleEnabled: resolver.Bool(
      "AUTO_TRADE_REACTION_SCALE_ENABLED", false
    ),
    ReactionScaleInvalidPolicy: resolver.String(
      "AUTO_TRADE_REACTION_SCALE_INVALID_POLICY", "single_market"
    ).Trim().ToLowerInvariant(),
    ReactionScaleStepAtr: resolver.Decimal(
      "AUTO_TRADE_REACTION_SCALE_STEP_ATR", 0.50m
    )
  );
  var deprecated = resolver.DeprecatedVariables.ToList();
  if (deprecated.Contains("AUTO_TRADE_BE_BUFFER_PIPS", StringComparer.Ordinal))
  {
    deprecated.Add(
      "AUTO_TRADE_BE_BUFFER_PIPS is deprecated; numeric value is interpreted "
      + "as tick count — use AUTO_TRADE_BE_BUFFER_TICKS"
    );
  }
  return options with
  {
    ConfigSources = resolver.Sources,
    DeprecatedVariables = deprecated
      .Distinct(StringComparer.Ordinal)
      .Order(StringComparer.Ordinal)
      .ToArray(),
  };
  }

  public static AutoTradeOptions FromRuntimeManifest(
    ResolvedRuntimeManifest manifest,
    AutoTradeOptions bootstrapFromEnvironment
  )
  {
    ArgumentNullException.ThrowIfNull(manifest);
    ArgumentNullException.ThrowIfNull(bootstrapFromEnvironment);
    var t = manifest.AutoTrade;
    if (ManifestDecimal.Parse(t.PipSize, "auto_trade.pip_size") <= 0m)
    {
      throw new InvalidOperationException("runtime manifest auto_trade.pip_size must be positive");
    }
    if (ManifestDecimal.Parse(t.ContractSize, "auto_trade.contract_size") <= 0m)
    {
      throw new InvalidOperationException(
        "runtime manifest auto_trade.contract_size must be positive"
      );
    }
    if (!string.Equals(t.CanonicalSymbol, "XAU", StringComparison.OrdinalIgnoreCase))
    {
      throw new InvalidOperationException(
        $"runtime manifest canonical_symbol must be XAU for current production; got '{t.CanonicalSymbol}'"
      );
    }
    return bootstrapFromEnvironment with
    {
      Enabled = t.Enabled,
      DryRun = t.DryRun,
      ExpectedBroker = t.ExpectedBroker,
      StopLossDistance = ManifestDecimal.Parse(t.StopLossDistance, "auto_trade.stop_loss_distance"),
      TargetsPips = t.TargetsPips.ToArray(),
      TargetWeights = t.TargetWeights.ToArray(),
      BreakEvenBufferTicks = t.BreakEvenBufferTicks,
      CandidateMaxAgeSeconds = t.CandidateMaxAgeSeconds,
      SpotMaxAgeSeconds = t.SpotMaxAgeSeconds,
      MaxSpreadPips = t.MaxSpreadPips,
      MaxEntryDistancePips = t.MaxEntryDistancePips,
      MinConfluence = t.MinConfluence,
      PollMilliseconds = t.PollMilliseconds,
      CandidateStream = t.CandidateStream,
      EventStream = t.EventStream,
      Label = t.Label,
      RequireDemoOnlyToken = t.RequireDemoOnlyToken,
      RiskPercent = ManifestDecimal.Parse(t.RiskPercent, "auto_trade.risk_percent"),
      SizingMode = t.SizingMode,
      PipValuePerLot = ManifestDecimal.Parse(t.PipValuePerLot, "auto_trade.pip_value_per_lot"),
      PipSize = ManifestDecimal.Parse(t.PipSize, "auto_trade.pip_size"),
      ContractSize = ManifestDecimal.Parse(t.ContractSize, "auto_trade.contract_size"),
      MaxTranches = t.MaxTranches,
      AddRiskFraction = ManifestDecimal.Parse(t.AddRiskFraction, "auto_trade.add_risk_fraction"),
      AddMaxAgeBars = t.AddMaxAgeBars,
      AddCooldownBars = t.AddCooldownBars,
      AddLevelBufferAtr = ManifestDecimal.Parse(t.AddLevelBufferAtr, "auto_trade.add_level_buffer_atr"),
      AddStopBufferAtr = ManifestDecimal.Parse(t.AddStopBufferAtr, "auto_trade.add_stop_buffer_atr"),
      AddMinStopPips = t.AddMinStopPips,
      AddRequireRiskFree = t.AddRequireRiskFree,
      ZoneFillEnabled = t.ZoneFillEnabled,
      ZoneFillMinLots = ManifestDecimal.Parse(t.ZoneFillMinLots, "auto_trade.zone_fill_min_lots"),
      ZoneFillMinAtr = ManifestDecimal.Parse(t.ZoneFillMinAtr, "auto_trade.zone_fill_min_atr"),
      ZoneFillTtlBars = t.ZoneFillTtlBars,
      ZoneFillFallbackEnabled = t.ZoneFillFallbackEnabled,
      InsideZoneMarketEntryEnabled = t.InsideZoneMarketEntryEnabled,
      BoxMinRiskReward = ManifestDecimal.Parse(t.BoxMinRiskReward, "auto_trade.box_min_risk_reward"),
      TrendStopMinPips = t.TrendStopMinPips,
      TrendStopMaxPips = t.TrendStopMaxPips,
      StopPushBeyondZone = t.StopPushBeyondZone,
      EntryContractTolerancePips = ManifestDecimal.Parse(
        t.EntryContractTolerancePips,
        "auto_trade.entry_contract_tolerance_pips"
      ),
      BrokerAbsenceConfirmations = t.BrokerAbsenceConfirmations,
      BrokerAbsenceRecheckSeconds = t.BrokerAbsenceRecheckSeconds,
      BrokerRecoveryTimeoutSeconds = t.BrokerRecoveryTimeoutSeconds,
      WickStopBufferAtr = ManifestDecimal.Parse(t.WickStopBufferAtr, "auto_trade.wick_stop_buffer_atr"),
      RangeFlipEnabled = t.RangeFlipEnabled,
      FlipExitBufferPips = t.FlipExitBufferPips,
      FlipConfirmTimeoutSeconds = t.FlipConfirmTimeoutSeconds,
      ZoneCooldownMinutes = t.ZoneCooldownMinutes,
      ZoneCooldownEnabled = t.ZoneCooldownEnabled,
      AddPullbackEnabled = t.AddPullbackEnabled,
      AddPullbackMinRetrace = ManifestDecimal.Parse(
        t.AddPullbackMinRetrace,
        "auto_trade.add_pullback_min_retrace"
      ),
      AddPullbackMaxRetrace = ManifestDecimal.Parse(
        t.AddPullbackMaxRetrace,
        "auto_trade.add_pullback_max_retrace"
      ),
      AddMaxGroupRiskPct = ManifestDecimal.Parse(
        t.AddMaxGroupRiskPct,
        "auto_trade.add_max_group_risk_pct"
      ),
      AddSizeRatio = ManifestDecimal.Parse(t.AddSizeRatio, "auto_trade.add_size_ratio"),
      RangeTargetsPips = t.RangeTargetsPips.ToArray(),
      RangeTpBufferPips = ManifestDecimal.Parse(
        t.RangeTpBufferPips,
        "auto_trade.range_tp_buffer_pips"
      ),
      Profile = t.Profile,
      RequireDemoAccount = t.RequireDemoAccount,
      AllowConcurrentStrategies = t.AllowConcurrentStrategies,
      AllowHedgedXau = t.AllowHedgedXau,
      RequireFlatForRange = t.RequireFlatForRange,
      RangeTwoSidedEnabled = t.RangeTwoSidedEnabled,
      MultiMatchEnabled = t.MultiMatchEnabled,
      TrackAllStructuralMatches = t.TrackAllStructuralMatches,
      CanonicalSymbol = t.CanonicalSymbol.ToUpperInvariant(),
      CandidateContractVersion = t.CandidateContractVersion,
      ContractMode = t.ContractMode.ToLowerInvariant(),
      TradePlanStream = t.TradePlanStream,
      ManualAlgoEnabled = t.ManualAlgoEnabled,
      TrendEnabled = t.TrendEnabled,
      RangeEnabled = t.RangeEnabled,
      MappedZoneEnabled = t.MappedZoneEnabled,
      MarketMapGuardEnabled = t.MarketMapGuardEnabled,
      MapThesisLockEnabled = t.MapThesisLockEnabled,
      StrategyMatchEnabled = t.StrategyMatchEnabled,
      BreakoutEnabled = t.BreakoutEnabled,
      RetestEnabled = t.RetestEnabled,
      ReactionEnabled = t.ReactionEnabled,
      LiquidityReversalEnabled = t.LiquidityReversalEnabled,
      AllowCounterBias = t.AllowCounterBias,
      CandidateStorageTtlSeconds = t.CandidateStorageTtlSeconds,
      Symbols = t.Symbols.ToArray(),
      ConfigManifestVersion = t.ConfigManifestVersion,
      NonHedgedOppositePolicy = t.NonHedgedOppositePolicy.ToLowerInvariant(),
      StructuralGuardMode = t.StructuralGuardMode.ToLowerInvariant(),
      ZoneReconcileMode = t.ZoneReconcileMode,
      RangeBoxScaleOutEnabled = t.RangeBoxScaleOutEnabled,
      RangeBoxScaleOutThresholdPips = t.RangeBoxScaleOutThresholdPips,
      RangeBoxScaleOutTriggerPips = t.RangeBoxScaleOutTriggerPips,
      RangeBoxScaleOutFraction = ManifestDecimal.Parse(
        t.RangeBoxScaleOutFraction,
        "auto_trade.range_box_scale_out_fraction"
      ),
      RangeBoxMoveSlToBeAfterScaleOut = t.RangeBoxMoveSlToBeAfterScaleOut,
      ExecutionZoneMaxWidthAtr = ManifestDecimal.Parse(
        t.ExecutionZoneMaxWidthAtr,
        "auto_trade.execution_zone_max_width_atr"
      ),
      ExecutionZoneMaxWidthPips = ManifestDecimal.Parse(
        t.ExecutionZoneMaxWidthPips,
        "auto_trade.execution_zone_max_width_pips"
      ),
      PostFillTargetFallback = t.PostFillTargetFallback.Trim().ToLowerInvariant(),
      PositionMissingConfirmations = t.PositionMissingConfirmations,
      PositionMissingRecheckSeconds = t.PositionMissingRecheckSeconds,
      EquityTableVersion = t.EquityTableVersion,
      ZoneScaleUndersizedPolicy = t.ZoneScaleUndersizedPolicy,
      GroupCloseAllocation = t.GroupCloseAllocation,
      UnfilledLegAfterTpPolicy = t.UnfilledLegAfterTpPolicy.Trim().ToLowerInvariant(),
      ReactionMarketFraction = ManifestDecimal.Parse(
        t.ReactionMarketFraction,
        "auto_trade.reaction_market_fraction"
      ),
      ReactionScaleFraction = ManifestDecimal.Parse(
        t.ReactionScaleFraction,
        "auto_trade.reaction_scale_fraction"
      ),
      ReactionScaleEnabled = t.ReactionScaleEnabled,
      ReactionScaleInvalidPolicy = t.ReactionScaleInvalidPolicy.Trim().ToLowerInvariant(),
      ReactionScaleStepAtr = ManifestDecimal.Parse(
        t.ReactionScaleStepAtr,
        "auto_trade.reaction_scale_step_atr"
      ),
    };
  }

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
      ConfigManifestVersion != 2
      || CandidateContractVersion != 6
      || string.IsNullOrWhiteSpace(CanonicalSymbol)
      || EffectiveSymbols.Count == 0
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: config manifest version 2, candidate contract "
        + "version 6, symbols, and canonical symbol must be configured"
      );
    }
    // Still accepts all four historical values, not just "v7_only" - unlike
    // Python's Settings validation (which IS restricted to "v7_only" only,
    // since Python is what actually decides whether to publish a V6
    // candidate), this Validate() runs against whatever ContractMode a
    // constructed AutoTradeOptions instance actually carries, including
    // the deliberately-"legacy_v6" bare record default hundreds of
    // pre-existing tests rely on (see the ContractMode field comment
    // above). Real deployments still only ever reach "v7_only" (see
    // FromEnvironment's default, below) or a value that fails the
    // cross-service AutoTradeConfigHealth fatal-mismatch check against
    // Python's "v7_only"-only manifest - so this stays lenient here
    // without weakening the actual production guarantee.
    if (ContractMode is not "legacy_v6" and not "shadow_v7"
      and not "v7_primary" and not "v7_only")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_CONTRACT_MODE must be legacy_v6, "
        + "shadow_v7, v7_primary, or v7_only"
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

  private sealed class EnvironmentResolver
  {
    private readonly Dictionary<string, string> _sources =
      new(StringComparer.Ordinal);
    private readonly HashSet<string> _deprecated =
      new(StringComparer.Ordinal);

    public IReadOnlyDictionary<string, string> Sources =>
      new Dictionary<string, string>(_sources, StringComparer.Ordinal);

    public IReadOnlyList<string> DeprecatedVariables =>
      _deprecated.Order(StringComparer.Ordinal).ToArray();

    public string String(
      string canonical,
      string fallback,
      string fallbackSource = "application_default",
      params string[] aliases
    )
    {
      var explicitValue = Environment.GetEnvironmentVariable(canonical);
      var legacyValues = aliases
        .Select(alias => (
          Alias: alias,
          Value: Environment.GetEnvironmentVariable(alias)
        ))
        .Where(item => !string.IsNullOrWhiteSpace(item.Value))
        .Select(item => (item.Alias, Value: item.Value!.Trim()))
        .ToArray();
      foreach (var item in legacyValues)
      {
        _deprecated.Add(item.Alias);
      }
      if (!string.IsNullOrWhiteSpace(explicitValue))
      {
        var normalized = explicitValue.Trim();
        if (legacyValues.Any(item => !string.Equals(
          item.Value, normalized, StringComparison.OrdinalIgnoreCase
        )))
        {
          throw new AutoTradeConfigurationException(
            $"Auto trade disabled: conflicting environment aliases for {canonical}"
          );
        }
        _sources[canonical] = "explicit_env";
        return normalized;
      }
      if (
        legacyValues.Length > 1
        && legacyValues.Skip(1).Any(item => !string.Equals(
          item.Value,
          legacyValues[0].Value,
          StringComparison.OrdinalIgnoreCase
        ))
      )
      {
        throw new AutoTradeConfigurationException(
          $"Auto trade disabled: conflicting legacy aliases for {canonical}"
        );
      }
      if (legacyValues.Length > 0)
      {
        _sources[canonical] = $"deprecated_env:{legacyValues[0].Alias}";
        return legacyValues[0].Value;
      }
      _sources[canonical] = fallbackSource;
      return fallback;
    }

    public bool Bool(
      string canonical,
      bool fallback,
      string fallbackSource = "application_default",
      params string[] aliases
    )
    {
      static bool Parse(string name, string raw) => raw.Trim().ToLowerInvariant() switch
      {
        "true" or "1" or "yes" => true,
        "false" or "0" or "no" => false,
        _ => throw Invalid(name, raw, "true,false,1,0,yes,no"),
      };
      var present = new List<(string Name, bool Value)>();
      var canonicalRaw = Environment.GetEnvironmentVariable(canonical);
      if (!string.IsNullOrWhiteSpace(canonicalRaw))
      {
        present.Add((canonical, Parse(canonical, canonicalRaw)));
      }
      foreach (var alias in aliases)
      {
        var raw = Environment.GetEnvironmentVariable(alias);
        if (string.IsNullOrWhiteSpace(raw))
        {
          continue;
        }
        _deprecated.Add(alias);
        present.Add((alias, Parse(alias, raw)));
      }
      if (present.Count > 1 && present.Skip(1).Any(
        item => item.Value != present[0].Value
      ))
      {
        throw new AutoTradeConfigurationException(
          $"Auto trade disabled: conflicting environment aliases for {canonical}"
        );
      }
      if (present.Count == 0)
      {
        _sources[canonical] = fallbackSource;
        return fallback;
      }
      _sources[canonical] = present[0].Name == canonical
        ? "explicit_env"
        : $"deprecated_env:{present[0].Name}";
      return present[0].Value;
    }

    public int Int(
      string canonical,
      int fallback,
      string fallbackSource = "application_default",
      params string[] aliases
    )
    {
      var raw = String(
        canonical,
        fallback.ToString(CultureInfo.InvariantCulture),
        fallbackSource,
        aliases
      );
      if (
        int.TryParse(
          raw,
          NumberStyles.Integer,
          CultureInfo.InvariantCulture,
          out var value
        )
      )
      {
        return value;
      }
      throw Invalid(canonical, raw, "an integer");
    }

    public decimal Decimal(
      string canonical,
      decimal fallback,
      string fallbackSource = "application_default",
      params string[] aliases
    )
    {
      var raw = String(
        canonical,
        fallback.ToString(CultureInfo.InvariantCulture),
        fallbackSource,
        aliases
      );
      if (
        decimal.TryParse(
          raw,
          NumberStyles.Number,
          CultureInfo.InvariantCulture,
          out var value
        )
      )
      {
        return value;
      }
      throw Invalid(canonical, raw, "a decimal number");
    }

    public IReadOnlyList<int> IntList(
      string canonical,
      string fallback,
      params string[] aliases
    )
    {
      var raw = String(
        canonical,
        fallback,
        "application_default",
        aliases
      );
      try
      {
        return raw
          .Split(
            ',',
            StringSplitOptions.RemoveEmptyEntries
              | StringSplitOptions.TrimEntries
          )
          .Select(value => int.Parse(value, CultureInfo.InvariantCulture))
          .ToArray();
      }
      catch (FormatException)
      {
        throw Invalid(canonical, raw, "a comma-separated integer list");
      }
    }

    public IReadOnlyList<string> StringList(
      string canonical,
      string fallback
    ) => String(canonical, fallback)
      .Split(
        ',',
        StringSplitOptions.RemoveEmptyEntries
          | StringSplitOptions.TrimEntries
      )
      .Select(value => value.ToUpperInvariant())
      .Distinct(StringComparer.Ordinal)
      .Order(StringComparer.Ordinal)
      .ToArray();

    private static AutoTradeConfigurationException Invalid(
      string canonical,
      string value,
      string expected
    ) => new(
      $"Auto trade disabled: {canonical} value '{value}' must be {expected}"
    );
  }
}

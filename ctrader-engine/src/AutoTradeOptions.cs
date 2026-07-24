using System.Globalization;

namespace ApexVoid.CTraderFeed;

public sealed record AutoTradeOptions(
  bool Enabled,
  bool DryRun,
  string ExpectedBroker,
  decimal StopLossDistance,
  IReadOnlyList<int> TargetsPips,
  IReadOnlyList<int> TargetWeights,
  int BreakEvenBufferPips,
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
  int TrendStopMaxPips = 65,
  bool StopPushBeyondZone = true,
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
  int CandidateContractVersion = 5,
  bool ManualAlgoEnabled = false,
  bool TrendEnabled = false,
  bool RangeEnabled = true,
  bool MappedZoneEnabled = true,
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
  string ZoneReconcileMode = "enforce"
)
{
  // Shared target-selection contract (app/autotrade/range_targets.py on the
  // Python side, same AUTO_TRADE_RANGE_TARGETS_PIPS env var) - previously
  // this executor independently hardcoded FullTakeProfitPips to exactly 50
  // or 70, duplicating a policy Python already owned and drifting from it
  // the moment the Python ladder changed. A null/empty override (e.g. a
  // test fixture that never sets it) falls back to the same "30,40,50"
  // default Python uses.
  private static readonly IReadOnlyList<int> DefaultRangeTargetsPips =
    new[] { 20, 30, 40, 50, 70 };

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
    BreakEvenBufferPips: resolver.Int("AUTO_TRADE_BE_BUFFER_PIPS", 3),
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
      "AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS", 10
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
    SizingMode: resolver.String("AUTO_TRADE_SIZING_MODE", "min"),
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
    TrendStopMaxPips: resolver.Int("AUTO_TRADE_TREND_STOP_MAX_PIPS", 65),
    StopPushBeyondZone: resolver.Bool(
      "AUTO_TRADE_STOP_PUSH_BEYOND_ZONE", true
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
      "AUTO_TRADE_RANGE_TARGETS_PIPS", "20,30,40,50,70"
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
      "AUTO_TRADE_CANDIDATE_CONTRACT_VERSION", 5
    ),
    ManualAlgoEnabled: resolver.Bool("MANUAL_ALGO_ENABLED", false),
    TrendEnabled: resolver.Bool(
      "AUTO_TRADE_TREND_ENABLED", demoEval, profileSource
    ),
    RangeEnabled: resolver.Bool("AUTO_TRADE_RANGE_ENABLED", true),
    MappedZoneEnabled: resolver.Bool("AUTO_TRADE_MAPPED_ZONE_ENABLED", true),
    StrategyMatchEnabled: resolver.Bool(
      "AUTO_TRADE_STRATEGY_MATCH_ENABLED", true
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
    ).ToLowerInvariant()
  );
    return options with
    {
      ConfigSources = resolver.Sources,
      DeprecatedVariables = resolver.DeprecatedVariables,
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
      || CandidateContractVersion != 5
      || string.IsNullOrWhiteSpace(CanonicalSymbol)
      || EffectiveSymbols.Count == 0
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: config manifest version 2, candidate contract "
        + "version 5, symbols, and canonical symbol must be configured"
      );
    }
    if (StopLossDistance <= 0 || StopLossDistance > 6.5m)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_SL_DISTANCE must be greater than zero "
        + "and at most 6.5"
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
    if (BreakEvenBufferPips < 0 || BreakEvenBufferPips >= TargetsPips[0])
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_BE_BUFFER_PIPS must be non-negative "
        + "and below TP1"
      );
    }
    if (RiskPercent is < 0.1m or > 10m || PipValuePerLot <= 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: risk percent must be 0.1-10 and pip value positive"
      );
    }
    if (SizingMode is not "min" and not "table" and not "risk")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_SIZING_MODE must be one of "
        + "min, table, risk"
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
      if (!string.IsNullOrWhiteSpace(explicitValue))
      {
        RecordDeprecatedAliases(aliases);
        _sources[canonical] = "explicit_env";
        return explicitValue.Trim();
      }
      foreach (var alias in aliases)
      {
        var legacyValue = Environment.GetEnvironmentVariable(alias);
        if (string.IsNullOrWhiteSpace(legacyValue))
        {
          continue;
        }
        _deprecated.Add(alias);
        _sources[canonical] = $"deprecated_env:{alias}";
        return legacyValue.Trim();
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
      var raw = String(
        canonical,
        fallback ? "true" : "false",
        fallbackSource,
        aliases
      ).ToLowerInvariant();
      return raw switch
      {
        "true" or "1" or "yes" => true,
        "false" or "0" or "no" => false,
        _ => throw Invalid(canonical, raw, "true,false,1,0,yes,no"),
      };
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

    private void RecordDeprecatedAliases(IEnumerable<string> aliases)
    {
      foreach (var alias in aliases)
      {
        if (!string.IsNullOrWhiteSpace(
          Environment.GetEnvironmentVariable(alias)
        ))
        {
          _deprecated.Add(alias);
        }
      }
    }

    private static AutoTradeConfigurationException Invalid(
      string canonical,
      string value,
      string expected
    ) => new(
      $"Auto trade disabled: {canonical} value '{value}' must be {expected}"
    );
  }
}

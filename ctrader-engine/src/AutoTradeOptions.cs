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
  int AddMinStopPips = 15,
  bool AddRequireRiskFree = false,
  bool ZoneFillEnabled = false,
  decimal ZoneFillMinLots = 0.09m,
  decimal ZoneFillMinAtr = 0.5m,
  int ZoneFillTtlBars = 3,
  decimal BoxMinRiskReward = 1.25m,
  int TrendStopMinPips = 40,
  int TrendStopMaxPips = 65,
  bool StopPushBeyondZone = true,
  int ZoneCooldownMinutes = 60
)
{
  public static AutoTradeOptions FromEnvironment() => new(
    Enabled: Bool("AUTO_TRADE_ENABLED", false),
    DryRun: Bool("AUTO_TRADE_DRY_RUN", true),
    ExpectedBroker: Env("AUTO_TRADE_EXPECTED_BROKER", "fpmarkets"),
    StopLossDistance: Decimal("AUTO_TRADE_SL_DISTANCE", 6.5m),
    TargetsPips: IntList("AUTO_TRADE_TP_PIPS", "30,60,90,120,200"),
    TargetWeights: IntList("AUTO_TRADE_TP_WEIGHTS", "20,20,20,20,20"),
    BreakEvenBufferPips: Int("AUTO_TRADE_BE_BUFFER_PIPS", 3),
    CandidateMaxAgeSeconds: Int("AUTO_TRADE_CANDIDATE_MAX_AGE", 90),
    SpotMaxAgeSeconds: Int("AUTO_TRADE_SPOT_MAX_AGE", 5),
    MaxSpreadPips: Int("AUTO_TRADE_MAX_SPREAD_PIPS", 5),
    MaxEntryDistancePips: Int("AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS", 10),
    MinConfluence: Int("AUTO_TRADE_MIN_CONFLUENCE", 2),
    PollMilliseconds: Int("AUTO_TRADE_POLL_MS", 1000),
    CandidateStream: Env("AUTO_TRADE_STREAM", "auto_trade:candidates"),
    EventStream: Env("AUTO_TRADE_EVENT_STREAM", "auto_trade:events"),
    Label: Env("AUTO_TRADE_LABEL", "apexvoid-auto"),
    RequireDemoOnlyToken: Bool("AUTO_TRADE_REQUIRE_DEMO_ONLY_TOKEN", false),
    RiskPercent: Decimal("AUTO_TRADE_RISK_PCT", 2m),
    SizingMode: Env("AUTO_TRADE_SIZING_MODE", "min"),
    PipValuePerLot: Decimal("AUTO_TRADE_PIP_VALUE_PER_LOT", 10m),
    PipSize: Decimal("AUTO_TRADE_PIP_SIZE", 0.1m),
    ContractSize: Decimal("AUTO_TRADE_CONTRACT_SIZE", 100m),
    MaxTranches: Int("AUTO_TRADE_MAX_TRANCHES", 2),
    AddRiskFraction: Decimal("AUTO_TRADE_ADD_RISK_FRACTION", 0.5m),
    AddMaxAgeBars: Int("AUTO_TRADE_ADD_MAX_AGE_BARS", 3),
    AddCooldownBars: Int("AUTO_TRADE_ADD_COOLDOWN_BARS", 3),
    AddLevelBufferAtr: Decimal("AUTO_TRADE_ADD_LEVEL_BUFFER_ATR", 1m),
    AddStopBufferAtr: Decimal("AUTO_TRADE_ADD_STOP_BUFFER_ATR", 0.3m),
    AddMinStopPips: Int("AUTO_TRADE_ADD_MIN_STOP_PIPS", 15),
    AddRequireRiskFree: Bool("AUTO_TRADE_ADD_REQUIRE_RISK_FREE", false),
    ZoneFillEnabled: Bool("AUTO_TRADE_ZONE_FILL_ENABLED", false),
    ZoneFillMinLots: Decimal("AUTO_TRADE_ZONE_FILL_MIN_LOTS", 0.09m),
    ZoneFillMinAtr: Decimal("AUTO_TRADE_ZONE_FILL_MIN_ATR", 0.5m),
    ZoneFillTtlBars: Int("AUTO_TRADE_ZONE_FILL_TTL_BARS", 3),
    BoxMinRiskReward: Decimal("AUTO_TRADE_BOX_MIN_RR", 1.25m),
    TrendStopMinPips: Int("AUTO_TRADE_TREND_STOP_MIN_PIPS", 40),
    TrendStopMaxPips: Int("AUTO_TRADE_TREND_STOP_MAX_PIPS", 65),
    StopPushBeyondZone: Bool("AUTO_TRADE_STOP_PUSH_BEYOND_ZONE", true),
    ZoneCooldownMinutes: Int("AUTO_TRADE_ZONE_COOLDOWN_MINUTES", 60)
  );

  public void Validate()
  {
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
        "Auto trade disabled: AUTO_TRADE_TP_PIPS must contain five positive targets"
      );
    }
    if (!TargetsPips.SequenceEqual(TargetsPips.OrderBy(value => value)))
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_TP_PIPS must be ascending"
      );
    }
    if (
      TargetWeights.Count != TargetsPips.Count
      || TargetWeights.Any(value => value <= 0)
      || TargetWeights.Sum() != 100
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_TP_WEIGHTS must match TP_PIPS, "
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
        "Auto trade disabled: AUTO_TRADE_PIP_SIZE and "
        + "AUTO_TRADE_CONTRACT_SIZE must be positive"
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
  }

  private static string Env(string key, string fallback)
  {
    var value = Environment.GetEnvironmentVariable(key);
    return string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();
  }

  private static bool Bool(string key, bool fallback) =>
    bool.TryParse(Environment.GetEnvironmentVariable(key), out var value)
      ? value
      : fallback;

  private static int Int(string key, int fallback) =>
    int.TryParse(
      Environment.GetEnvironmentVariable(key),
      NumberStyles.Integer,
      CultureInfo.InvariantCulture,
      out var value
    ) ? value : fallback;

  private static decimal Decimal(string key, decimal fallback) =>
    decimal.TryParse(
      Environment.GetEnvironmentVariable(key),
      NumberStyles.Number,
      CultureInfo.InvariantCulture,
      out var value
    ) ? value : fallback;

  private static IReadOnlyList<int> IntList(string key, string fallback) =>
    Env(key, fallback)
      .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
      .Select(value => int.Parse(value, CultureInfo.InvariantCulture))
      .ToArray();
}

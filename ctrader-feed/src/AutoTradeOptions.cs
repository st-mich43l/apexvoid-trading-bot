using System.Globalization;

namespace ApexVoid.CTraderFeed;

public sealed record AutoTradeOptions(
  bool Enabled,
  bool DryRun,
  string ExpectedBroker,
  decimal StopLossDistance,
  IReadOnlyList<int> TargetsPips,
  int CandidateMaxAgeSeconds,
  int SpotMaxAgeSeconds,
  int MaxSpreadPips,
  int MaxEntryDistancePips,
  int MaxDailyTrades,
  int MinConfluence,
  int PollMilliseconds,
  string CandidateStream,
  string EventStream,
  string Label
)
{
  public static AutoTradeOptions FromEnvironment() => new(
    Enabled: Bool("AUTO_TRADE_ENABLED", false),
    DryRun: Bool("AUTO_TRADE_DRY_RUN", true),
    ExpectedBroker: Env("AUTO_TRADE_EXPECTED_BROKER", "Fusion"),
    StopLossDistance: Decimal("AUTO_TRADE_SL_DISTANCE", 6.5m),
    TargetsPips: IntList("AUTO_TRADE_TP_PIPS", "30,50,70,90,130"),
    CandidateMaxAgeSeconds: Int("AUTO_TRADE_CANDIDATE_MAX_AGE", 90),
    SpotMaxAgeSeconds: Int("AUTO_TRADE_SPOT_MAX_AGE", 5),
    MaxSpreadPips: Int("AUTO_TRADE_MAX_SPREAD_PIPS", 5),
    MaxEntryDistancePips: Int("AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS", 10),
    MaxDailyTrades: Int("AUTO_TRADE_MAX_DAILY_TRADES", 6),
    MinConfluence: Int("AUTO_TRADE_MIN_CONFLUENCE", 2),
    PollMilliseconds: Int("AUTO_TRADE_POLL_MS", 1000),
    CandidateStream: Env("AUTO_TRADE_STREAM", "auto_trade:candidates"),
    EventStream: Env("AUTO_TRADE_EVENT_STREAM", "auto_trade:events"),
    Label: Env("AUTO_TRADE_LABEL", "apexvoid-auto")
  );

  public void Validate()
  {
    if (StopLossDistance <= 0 || StopLossDistance > 6.5m)
    {
      throw new InvalidOperationException(
        "AUTO_TRADE_SL_DISTANCE must be greater than zero and at most 6.5"
      );
    }
    if (TargetsPips.Count != 5 || TargetsPips.Any(value => value <= 0))
    {
      throw new InvalidOperationException(
        "AUTO_TRADE_TP_PIPS must contain five positive targets"
      );
    }
    if (!TargetsPips.SequenceEqual(TargetsPips.OrderBy(value => value)))
    {
      throw new InvalidOperationException("AUTO_TRADE_TP_PIPS must be ascending");
    }
    if (MaxDailyTrades <= 0)
    {
      throw new InvalidOperationException("AUTO_TRADE_MAX_DAILY_TRADES must be positive");
    }
    if (MinConfluence is < 1 or > 3)
    {
      throw new InvalidOperationException(
        "AUTO_TRADE_MIN_CONFLUENCE must be between 1 and 3"
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

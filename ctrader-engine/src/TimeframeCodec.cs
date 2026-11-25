namespace ApexVoid.CTraderFeed;

public static class TimeframeCodec
{
  private static readonly Dictionary<string, int> SecondsByCode = new(
    StringComparer.OrdinalIgnoreCase
  )
  {
    ["M1"] = 60,
    ["M5"] = 5 * 60,
    ["M15"] = 15 * 60,
    ["M30"] = 30 * 60,
    ["H1"] = 60 * 60,
  };

  public static IReadOnlyList<string> ParseList(string value) =>
    value
      .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
      .Select(item => item.ToUpperInvariant())
      .Distinct(StringComparer.OrdinalIgnoreCase)
      .ToArray();

  public static int ToSeconds(string code)
  {
    if (SecondsByCode.TryGetValue(code, out var seconds))
    {
      return seconds;
    }
    throw new ArgumentOutOfRangeException(
      nameof(code),
      code,
      "Only M1, M5, M15, M30 and H1 are supported by this feed."
    );
  }

  // PR-FEED1: the calendar span of one historical trendbar request. Spotware documents no per-period span table for
  // ProtoOAGetTrendbarsReq (only the 5 historical requests/second/connection limit), so this stays deliberately small:
  // about 3,000 bars, never under 4 days (wider than a weekend or a holiday close, so two empty windows in a row mean
  // there is no more history) and never over 35 days. The largest window (M1, 4 days) holds at most 5,760 bars.
  private static readonly TimeSpan MinHistoryWindow = TimeSpan.FromDays(4);
  private static readonly TimeSpan MaxHistoryWindow = TimeSpan.FromDays(35);
  private const int HistoryWindowBars = 3000;

  public static TimeSpan HistoryWindow(string code)
  {
    var span = TimeSpan.FromSeconds((double)ToSeconds(code) * HistoryWindowBars);
    if (span < MinHistoryWindow)
    {
      return MinHistoryWindow;
    }
    return span > MaxHistoryWindow ? MaxHistoryWindow : span;
  }

  public static ProtoOATrendbarPeriod ToProto(string code) =>
    code.ToUpperInvariant() switch
    {
      "M1" => ProtoOATrendbarPeriod.M1,
      "M5" => ProtoOATrendbarPeriod.M5,
      "M15" => ProtoOATrendbarPeriod.M15,
      "M30" => ProtoOATrendbarPeriod.M30,
      "H1" => ProtoOATrendbarPeriod.H1,
      _ => throw new ArgumentOutOfRangeException(nameof(code), code, null)
    };

  public static string FromProto(ProtoOATrendbarPeriod period) =>
    period switch
    {
      ProtoOATrendbarPeriod.M1 => "M1",
      ProtoOATrendbarPeriod.M5 => "M5",
      ProtoOATrendbarPeriod.M15 => "M15",
      ProtoOATrendbarPeriod.M30 => "M30",
      ProtoOATrendbarPeriod.H1 => "H1",
      _ => period.ToString().ToUpperInvariant()
    };
}

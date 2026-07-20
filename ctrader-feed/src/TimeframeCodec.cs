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
      "Only M1, M5, M15 and M30 are supported by this feed."
    );
  }

  public static ProtoOATrendbarPeriod ToProto(string code) =>
    code.ToUpperInvariant() switch
    {
      "M1" => ProtoOATrendbarPeriod.M1,
      "M5" => ProtoOATrendbarPeriod.M5,
      "M15" => ProtoOATrendbarPeriod.M15,
      "M30" => ProtoOATrendbarPeriod.M30,
      _ => throw new ArgumentOutOfRangeException(nameof(code), code, null)
    };

  public static string FromProto(ProtoOATrendbarPeriod period) =>
    period switch
    {
      ProtoOATrendbarPeriod.M1 => "M1",
      ProtoOATrendbarPeriod.M5 => "M5",
      ProtoOATrendbarPeriod.M15 => "M15",
      ProtoOATrendbarPeriod.M30 => "M30",
      _ => period.ToString().ToUpperInvariant()
    };
}

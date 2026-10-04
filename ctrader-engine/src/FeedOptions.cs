namespace ApexVoid.CTraderFeed;

public sealed record FeedOptions(
  string ClientId,
  string ClientSecret,
  string AccessToken,
  string RefreshToken,
  long AccountId,
  string Host,
  int Port,
  string CTraderSymbol,
  string RedisSymbol,
  IReadOnlyList<string> Timeframes,
  int BackfillBars,
  string RedisUrl,
  int BarsWindowMax,
  string BarsChannel,
  int BarQualityLookback,
  string HeartbeatFile,
  string AutoTradeHeartbeatFile,
  string RefreshTokenKey,
  string RefreshTokenFile,
  TimeSpan RequestTimeout,
  TimeSpan TokenRefreshLead,
  TimeSpan TokenCheckInterval,
  string ExpectedBroker = "fpmarkets"
)
{
  private static string RedisSymbolFromCTrader(string symbol)
  {
    var normalized = symbol.Replace("/", "", StringComparison.Ordinal).ToUpperInvariant();
    return normalized.EndsWith("USD", StringComparison.Ordinal)
      ? normalized[..^3]
      : normalized;
  }
}

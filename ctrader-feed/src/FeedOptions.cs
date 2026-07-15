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
  string RefreshTokenKey,
  TimeSpan RequestTimeout,
  TimeSpan TokenRefreshInterval
)
{
  public static FeedOptions FromEnvironment()
  {
    var cTraderSymbol = Env("CTRADER_SYMBOL", "XAUUSD");
    return new FeedOptions(
      ClientId: Env("CTRADER_CLIENT_ID", required: true),
      ClientSecret: Env("CTRADER_CLIENT_SECRET", required: true),
      AccessToken: Env("CTRADER_ACCESS_TOKEN", required: true),
      RefreshToken: Env("CTRADER_REFRESH_TOKEN", required: true),
      AccountId: long.Parse(Env("CTRADER_ACCOUNT_ID", required: true)),
      Host: Env("CTRADER_HOST", "demo.ctraderapi.com"),
      Port: int.Parse(Env("CTRADER_PORT", "5035")),
      CTraderSymbol: cTraderSymbol,
      RedisSymbol: RedisSymbolFromCTrader(cTraderSymbol),
      Timeframes: TimeframeCodec.ParseList(Env("CTRADER_TIMEFRAMES", "M5,M15,M30")),
      BackfillBars: int.Parse(Env("CTRADER_BACKFILL_BARS", "1500")),
      RedisUrl: Env("REDIS_URL", "redis://redis:6379/0"),
      BarsWindowMax: int.Parse(Env("BARS_WINDOW_MAX", "1500")),
      BarsChannel: Env("BARS_CHANNEL", "bars:new"),
      BarQualityLookback: int.Parse(Env("BAR_QUALITY_LOOKBACK", "6")),
      HeartbeatFile: Env("HEALTH_FILE", "/tmp/ctrader-feed.heartbeat"),
      RefreshTokenKey: Env("CTRADER_REFRESH_TOKEN_KEY", "ctrader:refresh_token"),
      RequestTimeout: TimeSpan.FromSeconds(int.Parse(Env("CTRADER_REQUEST_TIMEOUT", "30"))),
      TokenRefreshInterval: TimeSpan.FromMinutes(
        int.Parse(Env("CTRADER_TOKEN_REFRESH_MINUTES", "50"))
      )
    );
  }

  private static string Env(
    string key,
    string? fallback = null,
    bool required = false
  )
  {
    var value = Environment.GetEnvironmentVariable(key);
    if (!string.IsNullOrWhiteSpace(value))
    {
      return value;
    }
    if (required)
    {
      throw new InvalidOperationException($"{key} must be set");
    }
    return fallback ?? "";
  }

  private static string RedisSymbolFromCTrader(string symbol)
  {
    var normalized = symbol.Replace("/", "", StringComparison.Ordinal).ToUpperInvariant();
    return normalized.EndsWith("USD", StringComparison.Ordinal)
      ? normalized[..^3]
      : normalized;
  }
}

namespace ApexVoid.CTraderFeed;

/// <summary>
/// Account / process bootstrap options. May only be filled from ENV fields
/// classified as <c>secret_environment</c> or <c>bootstrap_environment</c>.
/// Manifest-owned trading policy must never be read here.
/// </summary>
public sealed record CTraderAccountOptions(
  string ClientId,
  string ClientSecret,
  string AccessToken,
  string RefreshToken,
  long AccountId,
  string Host,
  int Port,
  string RedisUrl,
  string HeartbeatFile,
  string RefreshTokenKey,
  string RefreshTokenFile,
  TimeSpan RequestTimeout,
  TimeSpan TokenRefreshLead,
  TimeSpan TokenCheckInterval
)
{
  public static CTraderAccountOptions FromEnvironment()
  {
    return new CTraderAccountOptions(
      ClientId: Require("CTRADER_CLIENT_ID"),
      ClientSecret: Require("CTRADER_CLIENT_SECRET"),
      AccessToken: Require("CTRADER_ACCESS_TOKEN"),
      RefreshToken: Require("CTRADER_REFRESH_TOKEN"),
      AccountId: long.Parse(Require("CTRADER_ACCOUNT_ID")),
      Host: Env("CTRADER_HOST", "demo.ctraderapi.com"),
      Port: int.Parse(Env("CTRADER_PORT", "5035")),
      RedisUrl: Env("REDIS_URL", "redis://redis:6379/0"),
      HeartbeatFile: Env("HEALTH_FILE", "/tmp/ctrader-feed.heartbeat"),
      RefreshTokenKey: Env("CTRADER_REFRESH_TOKEN_KEY", "ctrader:refresh_token"),
      RefreshTokenFile: Env(
        "CTRADER_REFRESH_TOKEN_FILE",
        "/var/lib/apexvoid/ctrader-token.json"
      ),
      RequestTimeout: TimeSpan.FromSeconds(
        int.Parse(Env("CTRADER_REQUEST_TIMEOUT", "30"))
      ),
      TokenRefreshLead: TimeSpan.FromDays(
        double.Parse(Env("CTRADER_TOKEN_REFRESH_LEAD_DAYS", "5"))
      ),
      TokenCheckInterval: TimeSpan.FromHours(
        double.Parse(Env("CTRADER_TOKEN_CHECK_INTERVAL_HOURS", "6"))
      )
    );
  }

  public static CTraderAccountOptions FromFeedOptions(FeedOptions feed) =>
    new(
      ClientId: feed.ClientId,
      ClientSecret: feed.ClientSecret,
      AccessToken: feed.AccessToken,
      RefreshToken: feed.RefreshToken,
      AccountId: feed.AccountId,
      Host: feed.Host,
      Port: feed.Port,
      RedisUrl: feed.RedisUrl,
      HeartbeatFile: feed.HeartbeatFile,
      RefreshTokenKey: feed.RefreshTokenKey,
      RefreshTokenFile: feed.RefreshTokenFile,
      RequestTimeout: feed.RequestTimeout,
      TokenRefreshLead: feed.TokenRefreshLead,
      TokenCheckInterval: feed.TokenCheckInterval
    );

  private static string Require(string key) =>
    Env(key, required: true);

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
}

/// <summary>Per-instrument market-data subscription options.</summary>
public sealed record FeedInstrumentOptions(
  string InstrumentId,
  string CanonicalSymbol,
  string CTraderSymbol,
  string RedisSymbol,
  IReadOnlyList<string> Timeframes,
  int BackfillBars,
  int BarsWindowMax,
  string BarsChannel,
  int BarQualityLookback,
  InstrumentRollout Rollout
)
{
  public static FeedInstrumentOptions FromFeedOptions(FeedOptions feed) =>
    new(
      InstrumentId: "XAU",
      CanonicalSymbol: "XAU",
      CTraderSymbol: feed.CTraderSymbol,
      RedisSymbol: feed.RedisSymbol,
      Timeframes: feed.Timeframes,
      BackfillBars: feed.BackfillBars,
      BarsWindowMax: feed.BarsWindowMax,
      BarsChannel: feed.BarsChannel,
      BarQualityLookback: feed.BarQualityLookback,
      Rollout: InstrumentRollout.Live
    );
}

/// <summary>Per-instrument execution / risk options slice.</summary>
public sealed record ExecutionInstrumentOptions(
  string InstrumentId,
  string CanonicalSymbol,
  InstrumentRollout Rollout,
  decimal PipSize,
  decimal ContractSize,
  IReadOnlyList<string> EffectiveSymbols,
  decimal PipValuePerLot = 0m,
  decimal LotMultiplier = 1m
)
{
  public decimal EffectivePipValuePerLot =>
    PipValuePerLot > 0m ? PipValuePerLot : PipSize * ContractSize;

  public static ExecutionInstrumentOptions FromAutoTradeOptions(
    AutoTradeOptions trade
  ) =>
    new(
      InstrumentId: trade.CanonicalSymbol.Trim().ToUpperInvariant(),
      CanonicalSymbol: trade.CanonicalSymbol.Trim().ToUpperInvariant(),
      Rollout: InstrumentRollout.Live,
      PipSize: trade.PipSize,
      ContractSize: trade.ContractSize,
      EffectiveSymbols: trade.EffectiveSymbols,
      PipValuePerLot: trade.PipValuePerLot
    );
}

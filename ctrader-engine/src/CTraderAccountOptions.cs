namespace ApexVoid.CTraderFeed;

/// <summary>
/// Account-level cTrader options (credentials, connection, shared streams).
/// Instrument-specific feed/execution fields live in separate records.
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
  TimeSpan TokenCheckInterval,
  string ExpectedBroker,
  string CandidateStream,
  string EventStream,
  bool RequireDemoOnlyToken,
  bool RequireDemoAccount
)
{
  public static CTraderAccountOptions FromFeedAndTrade(
    FeedOptions feed,
    AutoTradeOptions trade
  ) =>
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
      TokenCheckInterval: feed.TokenCheckInterval,
      ExpectedBroker: feed.ExpectedBroker,
      CandidateStream: trade.CandidateStream,
      EventStream: trade.EventStream,
      RequireDemoOnlyToken: trade.RequireDemoOnlyToken,
      RequireDemoAccount: trade.RequireDemoAccount
    );
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
  IReadOnlyList<string> EffectiveSymbols
)
{
  public static ExecutionInstrumentOptions FromAutoTradeOptions(
    AutoTradeOptions trade
  ) =>
    new(
      InstrumentId: trade.CanonicalSymbol.Trim().ToUpperInvariant(),
      CanonicalSymbol: trade.CanonicalSymbol.Trim().ToUpperInvariant(),
      Rollout: InstrumentRollout.Live,
      PipSize: trade.PipSize,
      ContractSize: trade.ContractSize,
      EffectiveSymbols: trade.EffectiveSymbols
    );
}

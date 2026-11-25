namespace ApexVoid.CTraderFeed;

public interface ICTraderFeedClient : IAsyncDisposable
{
  event Action? Heartbeat;
  TokenLifecycleStatus TokenStatus => TokenLifecycleStatus.Unknown;

  Task ConnectAndAuthorizeAsync(CancellationToken cancellationToken);
  Task RefreshTokenAsync(CancellationToken cancellationToken);
  Task<TradingAccountSnapshot> GetFeedAccountAsync(
    CancellationToken cancellationToken
  );
  Task<SymbolInfo> ResolveSymbolAsync(CancellationToken cancellationToken);
  Task<SymbolInfo> ResolveSymbolAsync(
    string cTraderSymbol,
    string redisSymbol,
    CancellationToken cancellationToken
  ) => ResolveSymbolAsync(cancellationToken);

  Task<IReadOnlyList<RawTrendbar>> GetTrendbarsAsync(
    SymbolInfo symbol,
    string timeframe,
    DateTimeOffset from,
    DateTimeOffset to,
    CancellationToken cancellationToken
  );

  /// <summary>
  /// Same request with ProtoOAGetTrendbarsReq.count set: the server returns at most <paramref name="count"/> bars,
  /// counted back from <paramref name="to"/> (the newest ones). cTrader.OpenAPI.Net 1.4.4 has no
  /// ProtoOAGetTrendbarsRes.hasMore, so callers detect a full page by its size.
  /// </summary>
  Task<IReadOnlyList<RawTrendbar>> GetTrendbarsAsync(
    SymbolInfo symbol,
    string timeframe,
    DateTimeOffset from,
    DateTimeOffset to,
    int count,
    CancellationToken cancellationToken
  ) => GetTrendbarsAsync(symbol, timeframe, from, to, cancellationToken);

  Task SubscribeAsync(
    SymbolInfo symbol,
    IReadOnlyCollection<string> timeframes,
    CancellationToken cancellationToken
  );

  IAsyncEnumerable<RawTrendbar> LiveTrendbarsAsync(CancellationToken cancellationToken);
  IAsyncEnumerable<SpotPrice> LiveSpotsAsync(CancellationToken cancellationToken);
}

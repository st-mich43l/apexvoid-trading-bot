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

  Task SubscribeAsync(
    SymbolInfo symbol,
    IReadOnlyCollection<string> timeframes,
    CancellationToken cancellationToken
  );

  IAsyncEnumerable<RawTrendbar> LiveTrendbarsAsync(CancellationToken cancellationToken);
  IAsyncEnumerable<SpotPrice> LiveSpotsAsync(CancellationToken cancellationToken);
}

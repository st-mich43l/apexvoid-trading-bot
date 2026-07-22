namespace ApexVoid.CTraderFeed;

public interface ICTraderFeedClient : IAsyncDisposable
{
  event Action? Heartbeat;
  TokenLifecycleStatus TokenStatus => TokenLifecycleStatus.Unknown;

  Task ConnectAndAuthorizeAsync(CancellationToken cancellationToken);
  Task RefreshTokenAsync(CancellationToken cancellationToken);
  Task<SymbolInfo> ResolveSymbolAsync(CancellationToken cancellationToken);

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

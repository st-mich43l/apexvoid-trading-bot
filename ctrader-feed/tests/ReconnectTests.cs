using System.Runtime.CompilerServices;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class ReconnectTests
{
  [Fact]
  public async Task DisconnectReconnectsReauthsBackfillsAndResubscribes()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var options = TestOptions(temp.Path);
    var sink = new RecordingSink();
    var first = new FakeCTraderClient
    {
      ThrowAfterLiveStart = true,
      Backfill = [Raw(900)]
    };
    var second = new FakeCTraderClient
    {
      CancelOnLiveStart = () => cts.Cancel(),
      Backfill = [Raw(1_200)]
    };
    var clients = new Queue<FakeCTraderClient>(new[] { first, second });
    var runner = new FeedRunner(
      options,
      () => clients.Dequeue(),
      sink,
      new HealthFile(temp.Path),
      _ => TimeSpan.Zero
    );

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunForeverAsync(cts.Token)
    );

    Assert.Equal(1, first.AuthCount);
    Assert.Equal(1, first.ResolveCount);
    Assert.Equal(1, first.SubscribeCount);
    Assert.Equal(1, first.BackfillCount);
    Assert.Equal(1, second.AuthCount);
    Assert.Equal(1, second.ResolveCount);
    Assert.Equal(1, second.SubscribeCount);
    Assert.Equal(1, second.BackfillCount);
    Assert.Contains(sink.Writes, write => write.Bar.Timestamp == 900);
    Assert.Contains(sink.Writes, write => write.Bar.Timestamp == 1_200);
  }

  private static FeedOptions TestOptions(string heartbeatPath) =>
    new(
      ClientId: "client",
      ClientSecret: "secret",
      AccessToken: "access",
      RefreshToken: "refresh",
      AccountId: 123,
      Host: "demo.ctraderapi.com",
      Port: 5035,
      CTraderSymbol: "XAUUSD",
      RedisSymbol: "XAU",
      Timeframes: ["M5"],
      BackfillBars: 1500,
      RedisUrl: "redis://redis:6379/0",
      BarsWindowMax: 1500,
      BarsChannel: "bars:new",
      HeartbeatFile: heartbeatPath,
      RefreshTokenKey: "ctrader:refresh_token",
      RequestTimeout: TimeSpan.FromSeconds(1),
      TokenRefreshInterval: TimeSpan.FromHours(1)
    );

  private static RawTrendbar Raw(long timestamp) =>
    new(
      "M5",
      Low: 410000,
      DeltaOpen: 100,
      DeltaHigh: 200,
      DeltaClose: 150,
      Volume: 100,
      UtcTimestampInMinutes: checked((uint)(timestamp / 60))
    );
}

internal sealed class FakeCTraderClient : ICTraderFeedClient
{
  public event Action? Heartbeat;

  public int AuthCount { get; private set; }
  public int ResolveCount { get; private set; }
  public int BackfillCount { get; private set; }
  public int SubscribeCount { get; private set; }
  public IReadOnlyList<RawTrendbar> Backfill { get; init; } = [];
  public bool ThrowAfterLiveStart { get; init; }
  public Action? OnLiveStart { get; init; }
  public Action? CancelOnLiveStart { get; init; }
  public int HeartbeatsOnLiveStart { get; init; }

  public Task ConnectAndAuthorizeAsync(CancellationToken cancellationToken)
  {
    AuthCount++;
    return Task.CompletedTask;
  }

  public Task RefreshTokenAsync(CancellationToken cancellationToken) =>
    Task.CompletedTask;

  public Task<SymbolInfo> ResolveSymbolAsync(CancellationToken cancellationToken)
  {
    ResolveCount++;
    return Task.FromResult(new SymbolInfo("XAU", "XAUUSD", 7, 2));
  }

  public Task<IReadOnlyList<RawTrendbar>> GetTrendbarsAsync(
    SymbolInfo symbol,
    string timeframe,
    DateTimeOffset from,
    DateTimeOffset to,
    CancellationToken cancellationToken
  )
  {
    BackfillCount++;
    return Task.FromResult(Backfill);
  }

  public Task SubscribeAsync(
    SymbolInfo symbol,
    IReadOnlyCollection<string> timeframes,
    CancellationToken cancellationToken
  )
  {
    SubscribeCount++;
    return Task.CompletedTask;
  }

  public async IAsyncEnumerable<RawTrendbar> LiveTrendbarsAsync(
    [EnumeratorCancellation] CancellationToken cancellationToken
  )
  {
    OnLiveStart?.Invoke();
    for (var i = 0; i < HeartbeatsOnLiveStart; i++)
    {
      Heartbeat?.Invoke();
    }
    CancelOnLiveStart?.Invoke();
    if (ThrowAfterLiveStart)
    {
      throw new IOException("simulated disconnect");
    }
    await Task.Delay(TimeSpan.FromMinutes(5), cancellationToken);
    yield break;
  }

  public async IAsyncEnumerable<SpotPrice> LiveSpotsAsync(
    [EnumeratorCancellation] CancellationToken cancellationToken
  )
  {
    await Task.Delay(TimeSpan.FromMinutes(5), cancellationToken);
    yield break;
  }

  public ValueTask DisposeAsync() => ValueTask.CompletedTask;
}

internal sealed class RecordingSink : IBarSink
{
  public List<(string Symbol, string Timeframe, OhlcBar Bar)> Writes { get; } = [];
  public long? Latest { get; set; }

  public Task WriteClosedBarAsync(
    string symbol,
    string timeframe,
    OhlcBar bar,
    CancellationToken cancellationToken
  )
  {
    Writes.Add((symbol, timeframe, bar));
    Latest = Math.Max(Latest ?? long.MinValue, bar.Timestamp);
    return Task.CompletedTask;
  }

  public Task<long?> GetLatestTimestampAsync(
    string symbol,
    string timeframe,
    CancellationToken cancellationToken
  ) => Task.FromResult(Latest);

  public Task<IReadOnlyList<OhlcBar>> ReadLatestAsync(
    string symbol,
    string timeframe,
    int count,
    CancellationToken cancellationToken
  ) => Task.FromResult<IReadOnlyList<OhlcBar>>([]);

  public Task WriteSpotAsync(SpotPrice spot, CancellationToken cancellationToken) =>
    Task.CompletedTask;
}

internal sealed class TempHeartbeat : IDisposable
{
  public string Path { get; } =
    System.IO.Path.Combine(System.IO.Path.GetTempPath(), $"{Guid.NewGuid()}.heartbeat");

  public void Dispose()
  {
    if (File.Exists(Path))
    {
      File.Delete(Path);
    }
  }
}

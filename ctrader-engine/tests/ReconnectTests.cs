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
    Assert.Equal(
      DateTimeOffset.FromUnixTimeSeconds(1_200),
      Assert.Single(second.BackfillRequests).From
    );
    Assert.Contains(sink.Writes, write => write.Bar.Timestamp == 900);
    Assert.Contains(sink.Writes, write => write.Bar.Timestamp == 1_200);
  }

  [Fact]
  public async Task StartupFullWindowBackfillOverwritesPoisonedStoredBar()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var options = TestOptions(temp.Path) with { BackfillBars = 10 };
    var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    var timestamp = now - (now % 300) - 600;
    var sink = new RecordingSink();
    sink.Seed("XAU", "M5", new OhlcBar(timestamp, 4.101m, 4.102m, 4.1m, 4.1m, 1));
    var client = new FakeCTraderClient
    {
      Backfill = [Raw(timestamp)],
      CancelOnLiveStart = () => cts.Cancel(),
    };
    var runner = new FeedRunner(
      options,
      () => client,
      sink,
      new HealthFile(temp.Path),
      _ => TimeSpan.Zero
    );

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunOneSessionAsync(cts.Token)
    );

    var repaired = sink.Bars[("XAU", "M5", timestamp)];
    Assert.Equal(4.1015m, repaired.Close);
    Assert.True(
      Assert.Single(client.BackfillRequests).From
      < DateTimeOffset.FromUnixTimeSeconds(timestamp)
    );
  }

  [Fact]
  public async Task AutoTradeFaultDoesNotCancelFeedAndBarStillReachesSink()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var sink = new RecordingSink();
    var store = new FaultAutoTradeStore();
    var client = new FakeCTraderClient
    {
      TradingAccountException = new AutoTradeConfigurationException(
        "Auto trade disabled: incident replay"
      ),
      Live = [Raw(1_500), Raw(1_800)],
      CancelAfterLiveBars = () => cts.Cancel(),
    };
    var runner = new FeedRunner(
      TestOptions(temp.Path),
      () => client,
      sink,
      new HealthFile(temp.Path),
      _ => TimeSpan.Zero,
      autoTrade: new AutoTradeEngine(AutoOptions(), store, log: _ => { })
    );

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunOneSessionAsync(cts.Token)
    );

    Assert.Contains(sink.Writes, item => item.Bar.Timestamp == 1_500);
    var error = Assert.Single(store.Events, item => item.Type == "error");
    Assert.Equal("Auto trade disabled: incident replay", error.Message);
  }

  [Fact]
  public async Task ConfigurationFaultDisablesAutoTradeAcrossFeedReconnects()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FaultAutoTradeStore();
    var first = new FakeCTraderClient
    {
      TradingAccountException = new AutoTradeConfigurationException(
        "Auto trade disabled: bad account"
      ),
      ThrowAfterLiveStart = true,
    };
    var second = new FakeCTraderClient
    {
      CancelOnLiveStart = () => cts.Cancel(),
    };
    var clients = new Queue<FakeCTraderClient>([first, second]);
    var runner = new FeedRunner(
      TestOptions(temp.Path),
      () => clients.Dequeue(),
      new RecordingSink(),
      new HealthFile(temp.Path),
      _ => TimeSpan.Zero,
      autoTrade: new AutoTradeEngine(AutoOptions(), store, log: _ => { })
    );

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunForeverAsync(cts.Token)
    );

    Assert.Equal(1, first.TradingAccountRequests);
    Assert.Equal(0, second.TradingAccountRequests);
    Assert.Single(store.Events, item => item.Type == "error");
    Assert.Equal(0, store.CandidateReads);
  }

  [Fact]
  public async Task TransientAutoTradeFaultRetriesOnNextFeedSession()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FaultAutoTradeStore();
    var first = new FakeCTraderClient
    {
      TradingAccountException = new IOException("temporary trading API outage"),
      ThrowAfterLiveStart = true,
    };
    var second = new FakeCTraderClient
    {
      CancelOnLiveStart = () => cts.Cancel(),
    };
    var clients = new Queue<FakeCTraderClient>([first, second]);
    var runner = new FeedRunner(
      TestOptions(temp.Path),
      () => clients.Dequeue(),
      new RecordingSink(),
      new HealthFile(temp.Path),
      _ => TimeSpan.Zero,
      autoTrade: new AutoTradeEngine(AutoOptions(), store, log: _ => { })
    );

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunForeverAsync(cts.Token)
    );

    Assert.Equal(1, first.TradingAccountRequests);
    Assert.Equal(1, second.TradingAccountRequests);
    Assert.Single(store.Events, item => item.Type == "error");
    Assert.Single(store.Events, item => item.Type == "ready");
  }

  [Fact]
  public async Task ProactiveRefreshFailureKeepsFeedSessionAlive()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(1));
    var first = new FakeCTraderClient
    {
      RefreshException = new InvalidOperationException(
        "Trading account is not authorized"
      ),
    };
    var second = new FakeCTraderClient
    {
      CancelOnLiveStart = () => cts.Cancel(),
    };
    var clients = new Queue<FakeCTraderClient>([first, second]);
    var runner = new FeedRunner(
      TestOptions(temp.Path) with
      {
        TokenCheckInterval = TimeSpan.FromMilliseconds(10),
      },
      () => clients.Dequeue(),
      new RecordingSink(),
      new HealthFile(temp.Path),
      _ => TimeSpan.Zero
    );

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunForeverAsync(cts.Token)
    );

    Assert.Equal(1, first.RefreshCount);
    Assert.Equal(0, second.AuthCount);
  }

  [Fact]
  public async Task StartupWarnsThatBrokerPipPositionIsDiagnosticOnly()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var warnings = new List<string>();
    var client = new FakeCTraderClient
    {
      Symbol = new SymbolInfo(
        "XAU",
        "XAUUSD",
        41,
        Digits: 2,
        PipPosition: 2,
        MinVolume: 100,
        StepVolume: 100,
        MaxVolume: 200_000,
        LotSize: 10_000
      ),
      CancelOnLiveStart = () => cts.Cancel(),
    };
    var runner = new FeedRunner(
      TestOptions(temp.Path),
      () => client,
      new RecordingSink(),
      new HealthFile(temp.Path),
      _ => TimeSpan.Zero,
      warningLog: warnings.Add,
      autoTrade: new AutoTradeEngine(
        AutoOptions(),
        new FaultAutoTradeStore(),
        log: _ => { }
      )
    );

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunOneSessionAsync(cts.Token)
    );

    var warning = Assert.Single(warnings);
    Assert.Contains("auto-trade units: pipSize=0.1 (configured)", warning);
    Assert.Contains("brokerPipPosition=2 (->0.01, ignored)", warning);
    Assert.Contains("contractSize=100 pipValuePerLot=10.00", warning);
    Assert.Contains("symbol=XAUUSD digits=2 lotSize=10000", warning);
  }

  [Fact]
  public async Task StrictLiveGrantDisablesAutoTradeWhileFeedKeepsStreaming()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var sink = new RecordingSink();
    var store = new FaultAutoTradeStore();
    var client = new FakeCTraderClient
    {
      Grants = [new(44669326, true), new(47948104, false)],
      Live = [Raw(1_500), Raw(1_800)],
      CancelAfterLiveBars = () => cts.Cancel(),
    };
    var runner = new FeedRunner(
      TestOptions(temp.Path),
      () => client,
      sink,
      new HealthFile(temp.Path),
      _ => TimeSpan.Zero,
      autoTrade: new AutoTradeEngine(
        AutoOptions() with { RequireDemoOnlyToken = true },
        store,
        log: _ => { }
      )
    );

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunOneSessionAsync(cts.Token)
    );

    Assert.Contains(sink.Writes, item => item.Bar.Timestamp == 1_500);
    Assert.Contains(
      store.Events,
      item => item.Type == "warning" && item.Message.Contains("44669326")
    );
    Assert.Contains(
      store.Events,
      item => item.Type == "error" && item.Message.Contains("demo-only token")
    );
    Assert.Equal(0, client.TradingAccountRequests);
    Assert.Equal(0, store.CandidateReads);
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
      BarQualityLookback: 6,
      HeartbeatFile: heartbeatPath,
      RefreshTokenKey: "ctrader:refresh_token",
      RefreshTokenFile: "/tmp/ctrader-token.json",
      RequestTimeout: TimeSpan.FromSeconds(1),
      TokenRefreshLead: TimeSpan.FromDays(5),
      TokenCheckInterval: TimeSpan.FromHours(6)
    );

  private static AutoTradeOptions AutoOptions() => new(
    Enabled: true,
    DryRun: true,
    ExpectedBroker: "Fusion",
    StopLossDistance: 6.5m,
    TargetsPips: [30, 60, 90, 120, 200],
    TargetWeights: [20, 20, 20, 20, 20],
    BreakEvenBufferPips: 3,
    CandidateMaxAgeSeconds: 90,
    SpotMaxAgeSeconds: 5,
    MaxSpreadPips: 5,
    MaxEntryDistancePips: 10,
    MinConfluence: 2,
    PollMilliseconds: 10,
    CandidateStream: "auto_trade:candidates",
    EventStream: "auto_trade:events",
    Label: "apexvoid-auto"
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

internal sealed class FakeCTraderClient : ICTraderFeedClient, ICTraderTradeClient
{
  public event Action? Heartbeat;

  public int AuthCount { get; private set; }
  public int ResolveCount { get; private set; }
  public int BackfillCount { get; private set; }
  public int SubscribeCount { get; private set; }
  public List<(DateTimeOffset From, DateTimeOffset To)> BackfillRequests { get; } = [];
  public IReadOnlyList<RawTrendbar> Backfill { get; init; } = [];
  public bool ThrowAfterLiveStart { get; init; }
  public Action? OnLiveStart { get; init; }
  public Action? CancelOnLiveStart { get; init; }
  public Action? CancelAfterLiveBars { get; init; }
  public int HeartbeatsOnLiveStart { get; init; }
  public IReadOnlyList<RawTrendbar> Live { get; init; } = [];
  public IReadOnlyList<TradingAccountGrant> Grants { get; init; } = [new(123, false)];
  public Exception? TradingAccountException { get; init; }
  public int TradingAccountRequests { get; private set; }
  public Exception? RefreshException { get; init; }
  public int RefreshCount { get; private set; }
  public SymbolInfo Symbol { get; init; } = new("XAU", "XAUUSD", 7, 2);

  public Task ConnectAndAuthorizeAsync(CancellationToken cancellationToken)
  {
    AuthCount++;
    return Task.CompletedTask;
  }

  public Task RefreshTokenAsync(CancellationToken cancellationToken)
  {
    RefreshCount++;
    return RefreshException is null
      ? Task.CompletedTask
      : Task.FromException(RefreshException);
  }

  public Task<SymbolInfo> ResolveSymbolAsync(CancellationToken cancellationToken)
  {
    ResolveCount++;
    return Task.FromResult(Symbol);
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
    BackfillRequests.Add((from, to));
    var matchingLiveBars = Live.Where(raw =>
      DateTimeOffset.FromUnixTimeSeconds(
        checked((long)raw.UtcTimestampInMinutes * 60)
      ) >= from
      && DateTimeOffset.FromUnixTimeSeconds(
        checked((long)raw.UtcTimestampInMinutes * 60)
      ) <= to
    ).ToArray();
    if (matchingLiveBars.Length > 0)
    {
      return Task.FromResult<IReadOnlyList<RawTrendbar>>(matchingLiveBars);
    }
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
    foreach (var raw in Live)
    {
      yield return raw;
    }
    CancelAfterLiveBars?.Invoke();
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

  public Task<IReadOnlyList<TradingAccountGrant>> GetAccountGrantsAsync(
    CancellationToken cancellationToken
  ) => Task.FromResult(Grants);

  public Task<TradingAccountSnapshot> GetTradingAccountAsync(
    CancellationToken cancellationToken
  )
  {
    TradingAccountRequests++;
    return TradingAccountException is not null
      ? Task.FromException<TradingAccountSnapshot>(TradingAccountException)
      : Task.FromResult(new TradingAccountSnapshot(
        123,
        IsLive: false,
        PermissionScope: "ScopeTrade",
        AccessRights: "FullAccess",
        AccountType: "Hedged",
        BrokerName: "Fusion Markets",
        Balance: 1_000m
      ));
  }

  public Task<IReadOnlyList<TradingPosition>> ReconcilePositionsAsync(
    CancellationToken cancellationToken
  ) => Task.FromResult<IReadOnlyList<TradingPosition>>([]);

  public Task<TradeExecution> PlaceMarketOrderAsync(
    MarketOrderRequest order,
    CancellationToken cancellationToken
  ) => throw new NotSupportedException();

  public Task AmendPositionStopLossAsync(
    long positionId,
    decimal stopLoss,
    CancellationToken cancellationToken
  ) => throw new NotSupportedException();

  public Task<TradeExecution> ClosePositionAsync(
    long positionId,
    long volume,
    CancellationToken cancellationToken
  ) => throw new NotSupportedException();
}

internal sealed class FaultAutoTradeStore : IAutoTradeStore
{
  public List<AutoTradeEvent> Events { get; } = [];
  public int CandidateReads { get; private set; }

  public Task<string> GetCursorAsync(CancellationToken cancellationToken) =>
    Task.FromResult("0-0");

  public Task SetCursorAsync(string cursor, CancellationToken cancellationToken) =>
    Task.CompletedTask;

  public Task<string> GetCommandCursorAsync(CancellationToken cancellationToken) =>
    Task.FromResult("0-0");

  public Task SetCommandCursorAsync(string cursor, CancellationToken cancellationToken) =>
    Task.CompletedTask;

  public Task<IReadOnlyList<TradeStreamEntry>> ReadCandidatesAsync(
    string stream,
    string afterId,
    int count,
    CancellationToken cancellationToken
  )
  {
    CandidateReads++;
    return Task.FromResult<IReadOnlyList<TradeStreamEntry>>([]);
  }

  public Task<bool> TryClaimCandidateAsync(
    string candidateId,
    CancellationToken cancellationToken
  ) => Task.FromResult(false);

  public Task<string?> GetCandidateStatusAsync(
    string candidateId,
    CancellationToken cancellationToken
  ) => Task.FromResult<string?>(null);

  public Task CompleteCandidateAsync(
    string candidateId,
    string outcome,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;

  public Task ReleaseCandidateAsync(
    string candidateId,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;

  public Task SavePositionAsync(
    AutoTradePositionState state,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;

  public Task<AutoTradePositionState?> GetPositionAsync(
    long positionId,
    CancellationToken cancellationToken
  ) => Task.FromResult<AutoTradePositionState?>(null);

  public Task<IReadOnlyList<long>> GetTrackedPositionIdsAsync(
    CancellationToken cancellationToken
  ) => Task.FromResult<IReadOnlyList<long>>([]);

  public Task DeletePositionAsync(
    long positionId,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;

  public Task<long> GetDailyTradeCountAsync(
    DateOnly date,
    CancellationToken cancellationToken
  ) => Task.FromResult(0L);

  public Task<long> IncrementDailyTradeCountAsync(
    DateOnly date,
    CancellationToken cancellationToken
  ) => Task.FromResult(1L);

  public Task<bool> IsPausedAsync(CancellationToken cancellationToken) =>
    Task.FromResult(false);

  public Task PublishAutoTradeEventAsync(
    string stream,
    AutoTradeEvent tradeEvent,
    CancellationToken cancellationToken
  )
  {
    Events.Add(tradeEvent);
    return Task.CompletedTask;
  }

  public Task IncrementGateRejectAsync(
    string symbol,
    string condition,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;

  public Task IncrementAddRejectAsync(
    string symbol,
    string mode,
    string condition,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;

  public Task RecordZoneCooldownAsync(
    string symbol,
    string direction,
    decimal entryPrice,
    decimal stopPrice,
    long closedAt,
    int ttlMinutes,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;
}

internal sealed class RecordingSink : IBarSink
{
  public List<(string Symbol, string Timeframe, OhlcBar Bar)> Writes { get; } = [];
  public Dictionary<(string Symbol, string Timeframe, long Timestamp), OhlcBar> Bars { get; }
    = [];
  public long? Latest { get; set; }

  public void Seed(string symbol, string timeframe, OhlcBar bar)
  {
    Bars[(symbol, timeframe, bar.Timestamp)] = bar;
    Latest = Math.Max(Latest ?? long.MinValue, bar.Timestamp);
  }

  public Task WriteClosedBarAsync(
    string symbol,
    string timeframe,
    OhlcBar bar,
    CancellationToken cancellationToken,
    bool publish = true
  )
  {
    Writes.Add((symbol, timeframe, bar));
    Bars[(symbol, timeframe, bar.Timestamp)] = bar;
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

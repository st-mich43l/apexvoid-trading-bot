using System.Runtime.CompilerServices;
using System.Text.Json;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class AutoTradeEngineTests
{
  private static readonly DateTimeOffset Now = DateTimeOffset.FromUnixTimeSeconds(1_000);
  private static readonly SymbolInfo Symbol = new(
    "XAU",
    "XAUUSD",
    7,
    Digits: 2,
    PipPosition: 1,
    MinVolume: 100,
    StepVolume: 100,
    MaxVolume: 100_000,
    LotSize: 10_000
  );

  [Fact]
  public async Task OpensMarketWithSixPointFiveStopAndClosesFiveTargets()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    var logs = new List<string>();
    var engine = new AutoTradeEngine(
      Options(),
      store,
      () => Now,
      logs.Add
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.Orders);
    Assert.Equal(TradeDirection.Buy, order.Direction);
    Assert.Equal(2_100, order.Volume);
    Assert.Equal(650_000, order.RelativeStopLoss);
    Assert.Equal("apexvoid-auto", order.Label);
    Assert.StartsWith("av-", order.ClientOrderId);
    Assert.True(order.Comment.Length <= 100);
    Assert.Equal((91, 3993.7m), Assert.Single(client.StopAmendments));
    Assert.Contains(logs, message => message.Contains("dryRun=False"));

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4020.2m, 4020.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.Equal(
      new long[] { 500, 400, 400, 400, 400 },
      client.Closes.Select(item => item.Volume)
    );
    Assert.Equal(
      new[] { 30, 60, 90, 120, 200 },
      store.Events
        .Where(item => item.Type == "take_profit")
        .Select(item => Assert.IsType<int>(item.TargetPips))
    );
    Assert.Equal(
      new decimal[] { 3993.7m, 4000.5m, 4003.2m, 4006.2m },
      client.StopAmendments.Select(item => item.StopLoss)
    );
    Assert.Equal(3, store.Events.Count(item => item.Type == "stop_moved"));
    Assert.Empty(store.Positions);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RejectsMomentumCandidateAsUnsupported()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson(
      timeframe: "M1",
      setup: "M1 Momentum Scalp",
      mode: "momentum_scalp"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Processed.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Empty(client.Orders);
    Assert.Contains(
      store.Events,
      item => item.Type == "rejected" && item.Message.Contains("unsupported")
    );
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RejectsLegacyM5RangeScalpAsUnsupported()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson(
      timeframe: "M5",
      setup: "Range Edge Scalp",
      mode: "range_scalp"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Processed.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Empty(client.Orders);
    Assert.Contains(
      store.Events,
      item => item.Type == "rejected" && item.Message.Contains("unsupported")
    );
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RejectsLegacyDecisionScalpAsUnsupported()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson(
      timeframe: "M1",
      setup: "M1 Decision Scalp",
      mode: "decision_scalp"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Processed.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Empty(client.Orders);
    Assert.Contains(
      store.Events,
      item => item.Type == "rejected" && item.Message.Contains("unsupported")
    );
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task DrawnDownBalanceUsesConfiguredBandAndKeepsFiveTargets()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { Balance = 875.21m },
    };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.Orders);
    Assert.Equal(900, order.Volume);
    Assert.Contains("|30,60,90,120,200|1,2,3,4,5", order.Comment);
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task TwoStepPositionClosesAtTp1AndTp3WithCorrectLabels()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { Balance = 200m },
    };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.Orders);
    Assert.Equal(200, order.Volume);
    Assert.Contains("|100,100|30,90|1,3", order.Comment);

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4009.2m, 4009.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.Equal(new long[] { 100, 100 }, client.Closes.Select(item => item.Volume));
    Assert.Contains(store.Events, item =>
      item.Type == "take_profit" && item.Message.StartsWith("TP1 ")
    );
    Assert.Contains(store.Events, item =>
      item.Type == "take_profit" && item.Message.StartsWith("TP3 ")
    );
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task EntryDriftIsRejectedOnceAndCursorAdvances()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4003.0m, 4003.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.CursorAdvanced.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Empty(client.Orders);
    var rejected = Assert.Single(store.Events, item => item.Type == "rejected");
    Assert.Contains("entry moved", rejected.Message);
    Assert.DoesNotContain(store.Events, item => item.Type == "error");
    Assert.Equal("1-0", store.Cursor);
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task HardLockRejectsLiveAccountBeforeReadingCandidates()
  {
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { IsLive = true },
    };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });

    var error = await Assert.ThrowsAsync<AutoTradeConfigurationException>(
      () => engine.RunSessionAsync(client, Symbol, CancellationToken.None)
    );

    Assert.Contains("refuses live", error.Message);
    Assert.Empty(client.Orders);
  }

  [Theory]
  [InlineData("ScopeView", "FullAccess", "Hedged", "Fusion Markets")]
  [InlineData("ScopeTrade", "NoTrading", "Hedged", "Fusion Markets")]
  [InlineData("ScopeTrade", "FullAccess", "Netted", "Fusion Markets")]
  [InlineData("ScopeTrade", "FullAccess", "Hedged", "Other Broker")]
  public async Task RequiresTradingScopeFullAccessHedgedFusionAccount(
    string scope,
    string access,
    string type,
    string broker
  )
  {
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with
      {
        PermissionScope = scope,
        AccessRights = access,
        AccountType = type,
        BrokerName = broker,
      },
    };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });

    await Assert.ThrowsAsync<AutoTradeConfigurationException>(
      () => engine.RunSessionAsync(client, Symbol, CancellationToken.None)
    );
    Assert.Empty(client.Orders);
  }

  [Fact]
  public void AccountNotGrantedMessageListsGrantsAndRemediation()
  {
    var error = AutoTradeConfigurationException.AccountNotGranted(
      47948104,
      [new(44669326, true), new(47764564, false)]
    );

    Assert.Contains("account 47948104", error.Message);
    Assert.Contains("44669326 live, 47764564 demo", error.Message);
    Assert.Contains("Re-authorize the app for 47948104", error.Message);
    Assert.Contains("put the new tokens in .env, then restart", error.Message);
    Assert.Contains("cached rotation chain resets automatically", error.Message);
  }

  [Fact]
  public async Task LiveGrantPublishesWarningWithoutBlockingDemoByDefault()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      Grants = [new(44669326, true), new(123, false)],
    };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "warning");

    var warning = Assert.Single(store.Events, item => item.Type == "warning");
    Assert.Equal(
      "token grants live account 44669326 — re-authorize with the demo account only",
      warning.Message
    );
    Assert.True(engine.Enabled);
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task TrailingAmendFailurePublishesOnceAndEngineContinues()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient { FailAmendmentCall = 2 };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4003.2m, 4003.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4006.2m, 4006.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4009.2m, 4009.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.Equal(3, client.Closes.Count);
    Assert.Equal(
      new decimal[] { 3993.7m, 4003.2m },
      client.StopAmendments.Select(item => item.StopLoss)
    );
    var error = Assert.Single(store.Events, item => item.Type == "error");
    Assert.Contains("stop amend after TP1 failed", error.Message);
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task LegacyCommentKeepsItsOwnTargetAndSlicePlan()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    client.SeedPosition(new TradingPosition(
      91,
      Symbol.SymbolId,
      TradeDirection.Buy,
      600,
      4000.2m,
      4000.5m,
      "apexvoid-auto",
      "av1|aaaaaaaaaaaaaaaaaaaaaaaa|800|200,200,400|30,50,70"
    ));
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "ready");

    var adopted = Assert.Single(store.Positions.Values);
    Assert.Equal(new[] { 30, 50, 70 }, adopted.TargetsPips);
    Assert.Equal(new long[] { 200, 200, 400 }, adopted.Slices);
    Assert.Equal(1, adopted.NextTargetIndex);

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4005.2m, 4005.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.Equal((91, 200), Assert.Single(client.Closes));
    Assert.Empty(client.StopAmendments);
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task AdaptiveCommentRestoresTp3OrdinalAfterRestart()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    client.SeedPosition(new TradingPosition(
      91,
      Symbol.SymbolId,
      TradeDirection.Buy,
      100,
      4000.2m,
      4000.5m,
      "apexvoid-auto",
      "av2|aaaaaaaaaaaaaaaaaaaaaaaa|200|100,100|30,90|1,3"
    ));
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "ready");

    var adopted = Assert.Single(store.Positions.Values);
    Assert.Equal(new[] { 1, 3 }, adopted.TargetOrdinals);
    Assert.Equal(1, adopted.NextTargetIndex);

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4009.2m, 4009.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.Equal((91, 100), Assert.Single(client.Closes));
    Assert.Contains(store.Events, item =>
      item.Type == "take_profit" && item.Message.StartsWith("TP3 ")
    );
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  private static async Task WaitForEventAsync(FakeAutoTradeStore store, string type)
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(2));
    while (!store.Events.Any(item => item.Type == type))
    {
      await Task.Delay(10, cts.Token);
    }
  }

  private static AutoTradeOptions Options() => new(
    Enabled: true,
    DryRun: false,
    ExpectedBroker: "Fusion",
    StopLossDistance: 6.5m,
    TargetsPips: [30, 60, 90, 120, 200],
    TargetWeights: [20, 20, 20, 20, 20],
    BreakEvenBufferPips: 3,
    CandidateMaxAgeSeconds: 90,
    SpotMaxAgeSeconds: 5,
    MaxSpreadPips: 5,
    MaxEntryDistancePips: 10,
    MaxDailyTrades: 6,
    MinConfluence: 2,
    PollMilliseconds: 10,
    CandidateStream: "auto_trade:candidates",
    EventStream: "auto_trade:events",
    Label: "apexvoid-auto"
  );

  private static TradingAccountSnapshot ValidAccount() => new(
    123,
    IsLive: false,
    PermissionScope: "ScopeTrade",
    AccessRights: "FullAccess",
    AccountType: "Hedged",
    BrokerName: "Fusion Markets",
    Balance: 2_000m
  );

  private static string CandidateJson(
    string timeframe = "M1",
    string setup = "Auto Range Scalp",
    string mode = "auto_range_scalp",
    string direction = "BUY"
  ) => JsonSerializer.Serialize(new
  {
    version = 1,
    candidate_id = new string('a', 64),
    symbol = "XAU",
    timeframe,
    setup,
    mode,
    direction,
    trigger_ts = "1000",
    created_at = 1_000,
    spot_ts = 1_000,
    current_price = 4000.1,
    key_level = 4000.0,
    entry_zone = new { low = 3999.5, high = 4000.5 },
    confluence = 2,
    reasons = new[] { "lower barrier x2", "rejection at scored edge" },
  });

  private sealed class FakeTradingClient : ICTraderFeedClient, ICTraderTradeClient
  {
    public event Action? Heartbeat
    {
      add { }
      remove { }
    }
    public TradingAccountSnapshot Account { get; init; } = ValidAccount();
    public IReadOnlyList<TradingAccountGrant> Grants { get; init; } = [new(123, false)];
    public List<MarketOrderRequest> Orders { get; } = [];
    public List<(long PositionId, decimal StopLoss)> StopAmendments { get; } = [];
    public List<(long PositionId, long Volume)> Closes { get; } = [];
    public int? FailAmendmentCall { get; init; }
    private readonly List<TradingPosition> _positions = [];
    private int _amendmentCalls;

    public void SeedPosition(TradingPosition position) => _positions.Add(position);

    public Task<IReadOnlyList<TradingAccountGrant>> GetAccountGrantsAsync(
      CancellationToken cancellationToken
    ) => Task.FromResult(Grants);

    public Task<TradingAccountSnapshot> GetTradingAccountAsync(
      CancellationToken cancellationToken
    ) => Task.FromResult(Account);

    public Task<IReadOnlyList<TradingPosition>> ReconcilePositionsAsync(
      CancellationToken cancellationToken
    ) => Task.FromResult<IReadOnlyList<TradingPosition>>(_positions.ToArray());

    public Task<TradeExecution> PlaceMarketOrderAsync(
      MarketOrderRequest order,
      CancellationToken cancellationToken
    )
    {
      Orders.Add(order);
      _positions.Add(new TradingPosition(
        91,
        order.SymbolId,
        order.Direction,
        order.Volume,
        4000.2m,
        3993.7m,
        order.Label,
        order.Comment
      ));
      return Task.FromResult(new TradeExecution(91, 81, 4000.2m, order.Volume));
    }

    public Task AmendPositionStopLossAsync(
      long positionId,
      decimal stopLoss,
      CancellationToken cancellationToken
    )
    {
      _amendmentCalls++;
      if (_amendmentCalls == FailAmendmentCall)
      {
        throw new IOException("simulated stop amend failure");
      }
      StopAmendments.Add((positionId, stopLoss));
      var position = _positions.Single(item => item.PositionId == positionId);
      _positions[_positions.IndexOf(position)] = position with { StopLoss = stopLoss };
      return Task.CompletedTask;
    }

    public Task<TradeExecution> ClosePositionAsync(
      long positionId,
      long volume,
      CancellationToken cancellationToken
    )
    {
      Closes.Add((positionId, volume));
      var position = _positions.Single(item => item.PositionId == positionId);
      var remaining = position.Volume - volume;
      if (remaining <= 0)
      {
        _positions.Remove(position);
      }
      else
      {
        _positions[_positions.IndexOf(position)] = position with { Volume = remaining };
      }
      return Task.FromResult(new TradeExecution(
        positionId,
        100 + Closes.Count,
        4013.2m,
        volume,
        Math.Max(0, remaining)
      ));
    }

    public Task ConnectAndAuthorizeAsync(CancellationToken cancellationToken) =>
      Task.CompletedTask;
    public Task RefreshTokenAsync(CancellationToken cancellationToken) =>
      Task.CompletedTask;
    public Task<SymbolInfo> ResolveSymbolAsync(CancellationToken cancellationToken) =>
      Task.FromResult(Symbol);
    public Task<IReadOnlyList<RawTrendbar>> GetTrendbarsAsync(
      SymbolInfo symbol,
      string timeframe,
      DateTimeOffset from,
      DateTimeOffset to,
      CancellationToken cancellationToken
    ) => Task.FromResult<IReadOnlyList<RawTrendbar>>([]);
    public Task SubscribeAsync(
      SymbolInfo symbol,
      IReadOnlyCollection<string> timeframes,
      CancellationToken cancellationToken
    ) => Task.CompletedTask;
    public async IAsyncEnumerable<RawTrendbar> LiveTrendbarsAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken
    )
    {
      await Task.CompletedTask;
      yield break;
    }
    public async IAsyncEnumerable<SpotPrice> LiveSpotsAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken
    )
    {
      await Task.CompletedTask;
      yield break;
    }
    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
  }

  private sealed class FakeAutoTradeStore(string payload) : IAutoTradeStore
  {
    private string _cursor = "0-0";
    private readonly Dictionary<string, string> _candidateStatus = [];
    public Dictionary<long, AutoTradePositionState> Positions { get; } = [];
    public List<AutoTradeEvent> Events { get; } = [];
    public TaskCompletionSource<bool> Ordered { get; } = new(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    public TaskCompletionSource<bool> Processed { get; } = new(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    public TaskCompletionSource<bool> CursorAdvanced { get; } = new(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    public string Cursor => _cursor;
    private long _daily;

    public Task<string> GetCursorAsync(CancellationToken cancellationToken) =>
      Task.FromResult(_cursor);
    public Task SetCursorAsync(string cursor, CancellationToken cancellationToken)
    {
      _cursor = cursor;
      CursorAdvanced.TrySetResult(true);
      return Task.CompletedTask;
    }
    public Task<IReadOnlyList<TradeStreamEntry>> ReadCandidatesAsync(
      string stream,
      string afterId,
      int count,
      CancellationToken cancellationToken
    )
    {
      if (afterId != "0-0")
      {
        return Task.FromResult<IReadOnlyList<TradeStreamEntry>>([]);
      }
      return Task.FromResult<IReadOnlyList<TradeStreamEntry>>([
        new TradeStreamEntry("1-0", payload),
      ]);
    }
    public Task<bool> TryClaimCandidateAsync(
      string candidateId,
      CancellationToken cancellationToken
    )
    {
      if (_candidateStatus.ContainsKey(candidateId))
      {
        return Task.FromResult(false);
      }
      _candidateStatus[candidateId] = "processing";
      return Task.FromResult(true);
    }
    public Task<string?> GetCandidateStatusAsync(
      string candidateId,
      CancellationToken cancellationToken
    ) => Task.FromResult(
      _candidateStatus.TryGetValue(candidateId, out var value) ? value : null
    );
    public Task CompleteCandidateAsync(
      string candidateId,
      string outcome,
      CancellationToken cancellationToken
    )
    {
      _candidateStatus[candidateId] = outcome;
      Processed.TrySetResult(true);
      if (outcome.StartsWith("ordered:", StringComparison.Ordinal))
      {
        Ordered.TrySetResult(true);
      }
      return Task.CompletedTask;
    }
    public Task ReleaseCandidateAsync(
      string candidateId,
      CancellationToken cancellationToken
    )
    {
      _candidateStatus.Remove(candidateId);
      return Task.CompletedTask;
    }
    public Task SavePositionAsync(
      AutoTradePositionState state,
      CancellationToken cancellationToken
    )
    {
      Positions[state.PositionId] = state;
      return Task.CompletedTask;
    }
    public Task<AutoTradePositionState?> GetPositionAsync(
      long positionId,
      CancellationToken cancellationToken
    ) => Task.FromResult(
      Positions.TryGetValue(positionId, out var state) ? state : null
    );
    public Task<IReadOnlyList<long>> GetTrackedPositionIdsAsync(
      CancellationToken cancellationToken
    ) => Task.FromResult<IReadOnlyList<long>>(Positions.Keys.ToArray());
    public Task DeletePositionAsync(
      long positionId,
      CancellationToken cancellationToken
    )
    {
      Positions.Remove(positionId);
      return Task.CompletedTask;
    }
    public Task<long> GetDailyTradeCountAsync(
      DateOnly date,
      CancellationToken cancellationToken
    ) => Task.FromResult(_daily);
    public Task<long> IncrementDailyTradeCountAsync(
      DateOnly date,
      CancellationToken cancellationToken
    ) => Task.FromResult(++_daily);
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
  }
}

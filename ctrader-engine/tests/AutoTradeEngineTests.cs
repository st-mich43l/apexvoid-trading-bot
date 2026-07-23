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
    PipPosition: 2,
    MinVolume: 100,
    StepVolume: 100,
    MaxVolume: 100_000,
    LotSize: 10_000
  );

  [Fact]
  public async Task OpensRiskBoundMarketWithSixPointFiveStopAndClosesFiveTargets()
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
    Assert.Equal(600, order.Volume);
    Assert.Equal(650_000, order.RelativeStopLoss);
    Assert.Equal("apexvoid-auto", order.Label);
    Assert.StartsWith("av-", order.ClientOrderId);
    Assert.True(order.Comment.Length <= 100);
    Assert.Equal((91, 3993.7m), Assert.Single(client.StopAmendments));
    Assert.Contains(logs, message => message.Contains("dryRun=False"));
    Assert.Contains(
      "sizing: mode=min balance=2000.00 → table 0.15 lots · risk 0.06 lots",
      logs
    );

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4020.2m, 4020.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.Equal(
      new long[] { 200, 100, 100, 100, 100 },
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
  public async Task StartupLogExplainsTableSizingAtCurrentBalance()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { Balance = 2_072.02m },
    };
    var logs = new List<string>();
    var engine = new AutoTradeEngine(
      Options() with { SizingMode = "table" },
      store,
      () => Now,
      logs.Add
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "ready");

    Assert.Contains(
      "sizing: mode=table balance=2072.02 → table 0.15 lots · risk 0.06 lots",
      logs
    );
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task BoxRangeScalpClosesFullVolumeAtItsSingleTarget()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 70,
      timeframe: "M5"
    ))
    {
      DailyTradeCount = 100,
    };
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { ZoneFillEnabled = true },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.Orders);
    Assert.True(order.Volume > 0);
    Assert.Equal(101, store.DailyTradeCount);
    Assert.Empty(client.LimitOrders);
    Assert.Contains($"|{order.Volume}|70|1|", order.Comment);
    var opened = Assert.Single(store.Events, item => item.Type == "opened");
    Assert.Equal("algo_auto", opened.Stream);
    Assert.Equal("BUY", opened.Direction);
    Assert.Contains("full TP 70p", opened.Message);
    Assert.Contains("range 4,000.00-4,008.00", opened.Message);
    var stopPips = order.RelativeStopLoss / 10_000m;
    Assert.Equal(stopPips, opened.StopPips);
    Assert.Equal(new[] { 70 }, opened.TargetsPips);

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4007.2m, 4007.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.Equal((91, order.Volume), Assert.Single(client.Closes));
    var takeProfit = Assert.Single(
      store.Events,
      item => item.Type == "take_profit"
    );
    Assert.Equal(70, takeProfit.TargetPips);
    Assert.Equal(stopPips, takeProfit.StopPips);
    Assert.StartsWith("FULL TP +70 pips", takeProfit.Message);
    Assert.DoesNotContain(store.Events, item => item.Type == "stop_moved");
    Assert.Empty(store.Positions);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RangeFlipTargetExitsInsideOpposingEdgeAndClearsPendingOnFill()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(fullTpPips: 70));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { RangeFlipEnabled = true },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var opened = Assert.Single(store.Events, item => item.Type == "opened");
    Assert.Equal(new[] { 68 }, opened.TargetsPips);
    var state = Assert.Single(store.Positions.Values);
    Assert.Equal(4007.0m, state.RangeExitPrice);
    Assert.Equal("xau-8000-8016", state.RangeId);

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4006.9m, 4007.1m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.Empty(client.Closes);

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4007.0m, 4007.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.Single(client.Closes);
    Assert.Contains(store.Events, item =>
      item.Type == "take_profit" && item.TargetPips == 68
    );
    Assert.Null(await store.GetCandidateStatusAsync(
      "flip:XAU:xau-8000-8016", cts.Token
    ));

    store.EnqueueCandidate(BoxCandidateJson(
      fullTpPips: 50,
      direction: "SELL",
      candidate: 'b',
      structureSwing: 4009.5m
    ));
    await WaitUntilAsync(() => client.Orders.Count == 2);

    Assert.Equal(TradeDirection.Sell, client.Orders[1].Direction);
    Assert.Single(client.Closes);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task OppositeRangeCandidateIsRejectedWhileFlipClosePending()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 70,
      direction: "SELL",
      structureSwing: 4009.5m
    ));
    await store.CompleteCandidateAsync(
      "flip:XAU:xau-8000-8016",
      "flip_pending:BUY:1030",
      cts.Token
    );
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { RangeFlipEnabled = true },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4008.0m, 4008.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected" && item.Message.Contains("flip_close_pending")
    );
    Assert.Contains(("XAU", "flip_close_pending"), store.GateRejects);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RangeFlipTimeoutAlertsAndDoesNotBookTarget()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(fullTpPips: 70));
    var client = new FakeTradingClient { BlockClose = true };
    var engine = new AutoTradeEngine(
      Options() with {
        RangeFlipEnabled = true,
        FlipConfirmTimeoutSeconds = 1,
      },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4007.0m, 4007.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.DoesNotContain(store.Events, item => item.Type == "take_profit");
    Assert.Contains(store.Events, item =>
      item.Type == "warning"
      && item.Message.Contains("opposite side not armed")
    );
    Assert.Null(await store.GetCandidateStatusAsync(
      "flip:XAU:xau-8000-8016", cts.Token
    ));

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task BoxRangeScalpNeverScalesIntoAnOpenPosition()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(fullTpPips: 50));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    store.EnqueueCandidate(BoxCandidateJson(
      candidate: 'b',
      direction: "SELL",
      fullTpPips: 50,
      structureSwing: 4006.2m
    ));
    await WaitForEventAsync(store, "rejected");

    Assert.Single(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("waits for flat XAU exposure")
    );

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
  public async Task DrawnDownBalanceUsesRiskBoundFallbackLadder()
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
    Assert.Equal(200, order.Volume);
    Assert.Contains("|30,90|1,3", order.Comment);
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
      Account = ValidAccount() with { Balance = 650m },
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
    Assert.Contains("entry distance rejected", rejected.Message);
    Assert.Contains("raw=2.70 pip=0.1 -> 27.0 pips, cap 10.0", rejected.Message);
    Assert.DoesNotContain(store.Events, item => item.Type == "error");
    Assert.Equal("1-0", store.Cursor);
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ProductionSpreadUsesConfiguredPipDespiteBrokerMetadata()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson(
      entryLow: 4030m,
      entryHigh: 4031m,
      structureSwing: 4024m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4030.12m, 4030.21m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Equal(0.01m, VolumePlanner.BrokerPipSize(Symbol));
    Assert.Single(client.Orders);
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ProductionSpreadShowsOldOneCentPipRejectionArithmetic()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson(
      entryLow: 4030m,
      entryHigh: 4031m,
      structureSwing: 4024m
    ));
    var client = new FakeTradingClient();
    var oldUnits = Options() with
    {
      PipSize = 0.01m,
      ContractSize = 1_000m,
    };
    var engine = new AutoTradeEngine(oldUnits, store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4030.12m, 4030.21m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.CursorAdvanced.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Empty(client.Orders);
    var rejected = Assert.Single(store.Events, item => item.Type == "rejected");
    Assert.Contains(
      "spread rejected: bid=4030.12 ask=4030.21 raw=0.09 "
      + "pip=0.01 -> 9.0 pips, cap 5.0",
      rejected.Message
    );
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

  [Fact]
  public async Task MomentumContinuationOpensIndependentSecondTranche()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, 1_000),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4003.2m, 4003.4m, 1_000),
      cts.Token
    );
    client.EnqueueMarketExecutionPrice(4003.4m);
    store.EnqueueCandidate(CandidateJson(
      candidate: 'b',
      barTs: 1_180,
      structureSwing: 4001.9m,
      entryLow: 4003m,
      entryHigh: 4004m,
      // Scale-in adds are now restricted to the trend regime (see
      // ProcessAddAsync's regime guard) - this add candidate needs an
      // explicit "trend" tag to keep exercising the momentum-add path.
      regime: "trend"
    ));
    await WaitForEventAsync(store, "add");

    Assert.Equal(2, client.Orders.Count);
    Assert.StartsWith("av3|bbbbbbbbbb|aaaaaaaaaa|2|", client.Orders[1].Comment);
    Assert.Equal(600, client.Orders[1].Volume);
    Assert.Equal((92, 4000.4m), client.StopAmendments.Last());
    var add = Assert.Single(store.Events, item => item.Type == "add");
    Assert.Equal(2, add.TrancheIndex);
    Assert.Equal("aaaaaaaaaa", add.GroupId);
    Assert.Contains("add-cap-bound", add.Message);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ReconcileAdoptsTwoTranchesWithIndependentPlans()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    client.SeedPosition(new TradingPosition(
      91, 7, TradeDirection.Buy, 400, 4000m, 4000.3m,
      "apexvoid-auto", "av3|aaaaaaaaaa|aaaaaaaaaa|1|600|200,100,100,100,100|30,60,90,120,200|1,2,3,4,5|1000"
    ));
    client.SeedPosition(new TradingPosition(
      92, 7, TradeDirection.Buy, 300, 4003m, 4001.2m,
      "apexvoid-auto", "av3|bbbbbbbbbb|aaaaaaaaaa|2|300|100,100,100|30,60,90|1,2,3|1180"
    ));
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "ready");

    Assert.Equal(2, store.Positions.Count);
    Assert.Equal(new[] { 1, 2 }, store.Positions.Values
      .OrderBy(state => state.TrancheIndex)
      .Select(state => state.TrancheIndex));
    Assert.All(store.Positions.Values, state =>
      Assert.Equal("aaaaaaaaaa", state.GroupId));
    Assert.Equal(
      new[] { 5, 3 },
      store.Positions.Values.OrderBy(state => state.PositionId)
        .Select(state => state.TargetsPips.Count)
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task WideZonePlacesTwoLimitsAndExpiresUnfilledMidpointLeg()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(CandidateJson(
      entryLow: 3999m,
      entryHigh: 4000.5m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { ZoneFillEnabled = true, SizingMode = "table" },
      store,
      () => now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.4m, 4000.6m, 1_000),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "zone_planned");

    Assert.Equal(2, client.LimitOrders.Count);
    Assert.Equal(
      new[] { 4000.5m, 3999.75m },
      client.LimitOrders.Select(order => order.LimitPrice)
    );
    Assert.Equal(
      new long[] { 800, 700 },
      client.LimitOrders.Select(order => order.Volume)
    );
    Assert.Equal(1_500, client.LimitOrders.Sum(order => order.Volume));
    Assert.All(client.LimitOrders, order => Assert.StartsWith("avz|", order.Comment));
    Assert.Contains(
      store.Events,
      item => item.Type == "zone_planned"
        && item.Message.Contains("sizing=table lots=0.15")
    );

    client.FillPendingOrder(client.PendingOrders[0].OrderId);
    now = Now.AddMinutes(3);
    await WaitForEventAsync(store, "zone_expired");

    Assert.Single(client.CancelledOrders);
    Assert.Empty(client.PendingOrders);
    await WaitUntilAsync(() => store.Positions.Count == 1);
    var filled = Assert.Single(store.Positions.Values);
    Assert.Equal(1, filled.ZoneLeg);
    Assert.Equal(800, filled.InitialVolume);
    Assert.Equal(5, filled.TargetsPips.Count);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task SmallZoneFillPlanFallsBackToSingleEntryAndRecordsReason()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson(
      entryLow: 3999m,
      entryHigh: 4000.5m
    ));
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { Balance = 900m },
    };
    var logs = new List<string>();
    var engine = new AutoTradeEngine(
      Options() with
      {
        ZoneFillEnabled = true,
        ZoneFillMinLots = 0.09m,
        SizingMode = "table",
      },
      store,
      () => Now,
      logs.Add
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.4m, 4000.6m, 1_000),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.Orders);
    Assert.Equal(600, order.Volume);
    Assert.Empty(client.LimitOrders);
    Assert.Contains(
      logs,
      message => message.Contains(
        "zone-fill skipped: 0.06 lots below 0.09 minimum"
      )
    );
    Assert.Contains(
      store.Events,
      item => item.Type == "opened"
        && item.Message.Contains(
          "zone-fill skipped: 0.06 lots below 0.09 minimum"
        )
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task SessionCleanupDoesNotRaceQueuedSpotIntoDisconnectedClient()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient { FailReconcileCall = 2 };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await client.ReconcileFaultEntered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var spot = engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000m, 4000.2m, 1_000),
      cts.Token
    );
    client.ReleaseReconcileFault.TrySetResult(true);

    var error = await Assert.ThrowsAsync<InvalidOperationException>(() => run);
    await spot;
    Assert.Equal("Trading account is not authorized", error.Message);
    Assert.DoesNotContain(
      store.Events,
      item => item.Message.Contains("session is not connected")
    );
  }

  [Fact]
  public async Task TrendPullbackCandidateIsAcceptedInsteadOfUnsupported()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(TrendCandidateJson());
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Single(client.Orders);
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ScannerStrategyMatchIsAcceptedWithoutRegimeRouting()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson());
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { ZoneFillEnabled = true },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.Orders);
    Assert.Empty(client.LimitOrders);
    Assert.Contains("|30,60,90|", order.Comment);
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ScannerStrategyMatchRejectsMissingExecutionContext()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(
      StrategyMatchCandidateJson(targetsPips: [])
    );
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Processed.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("invalid strategy candidate contract")
    );
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task TrendCandidateTargetsPipsDriveItsOwnTargetPlan()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(
      TrendCandidateJson(targetsPips: [25, 55, 85])
    );
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.Orders);
    Assert.Contains("|25,55,85|", order.Comment);
    Assert.DoesNotContain("30,60,90,120,200", order.Comment);
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task TrendCandidateUsesTrendStopBandInsteadOfAddBand()
  {
    // The same raw 20-pip structure stop clamps to the range floor of 30 and
    // the trend family's 40-pip minimum. Both paths share the same 65-pip
    // maximum risk envelope.
    using (var trendCts = new CancellationTokenSource(TimeSpan.FromSeconds(5)))
    {
      var trendStore = new FakeAutoTradeStore(
        TrendCandidateJson(structureSwing: 3998.5m)
      );
      var trendClient = new FakeTradingClient();
      var trendEngine = new AutoTradeEngine(Options(), trendStore, () => Now, _ => { });
      await trendEngine.ObserveSpotAsync(
        new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
        trendCts.Token
      );
      var trendRun = trendEngine.RunSessionAsync(trendClient, Symbol, trendCts.Token);
      await trendStore.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

      var trendOrder = Assert.Single(trendClient.Orders);
      Assert.Equal(400_000, trendOrder.RelativeStopLoss);

      trendCts.Cancel();
      await Assert.ThrowsAnyAsync<OperationCanceledException>(() => trendRun);
    }

    using (var legacyCts = new CancellationTokenSource(TimeSpan.FromSeconds(5)))
    {
      var legacyStore = new FakeAutoTradeStore(
        CandidateJson(structureSwing: 3998.5m)
      );
      var legacyClient = new FakeTradingClient();
      var legacyEngine = new AutoTradeEngine(Options(), legacyStore, () => Now, _ => { });
      await legacyEngine.ObserveSpotAsync(
        new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
        legacyCts.Token
      );
      var legacyRun = legacyEngine.RunSessionAsync(legacyClient, Symbol, legacyCts.Token);
      await legacyStore.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

      var legacyOrder = Assert.Single(legacyClient.Orders);
      Assert.Equal(300_000, legacyOrder.RelativeStopLoss);

      legacyCts.Cancel();
      await Assert.ThrowsAnyAsync<OperationCanceledException>(() => legacyRun);
    }
  }

  [Fact]
  public async Task ScaleInAddRequiresTrendRegimeAndRejectsChop()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
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
    client.EnqueueMarketExecutionPrice(4003.4m);
    store.EnqueueCandidate(CandidateJson(
      candidate: 'b',
      barTs: 1_180,
      structureSwing: 4001.9m,
      entryLow: 4003m,
      entryHigh: 4004m,
      regime: "chop"
    ));
    await WaitForEventAsync(store, "rejected");

    Assert.Single(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("restricted to the trend regime")
    );

    client.EnqueueMarketExecutionPrice(4003.4m);
    store.EnqueueCandidate(CandidateJson(
      candidate: 'c',
      barTs: 1_180,
      structureSwing: 4001.9m,
      entryLow: 4003m,
      entryHigh: 4004m,
      regime: "trend"
    ));
    await WaitForEventAsync(store, "add");

    Assert.Equal(2, client.Orders.Count);
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task StopInsideOpposingZoneIsPushedBeyondItWithBuffer()
  {
    // Default BUY box-scalp stop lands at 3997.70 (structureSwing 3998.0 -
    // AddStopBufferAtr 0.3 * atr 1.0, clamped). An opposing (demand) zone of
    // 3997.00-3998.50 traps that stop inside it - the guard must push the
    // stop below the zone's low edge by another AddStopBufferAtr * atr.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 70,
      opposingZoneLow: 3997.0m,
      opposingZoneHigh: 3998.5m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Single(client.Orders);
    Assert.Equal((91, 3996.7m), Assert.Single(client.StopAmendments));
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task StopInsideOpposingZoneIsRejectedWhenPushWouldExceedMaxStopDistance()
  {
    // Zone wide/far enough that pushing beyond its low edge would demand a
    // stop distance past the 65-pip (6.5 price) non-trend maximum - the
    // candidate must be rejected instead of silently accepting an oversized
    // stop.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 70,
      opposingZoneLow: 3990.0m,
      opposingZoneHigh: 3998.0m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected" && item.Message.Contains("opposing zone")
    );
    Assert.Contains(("XAU", "stop_in_opposing_zone"), store.GateRejects);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task StopInsideOpposingZoneIsRejectedWhenPushDisabledByFlag()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 70,
      opposingZoneLow: 3997.0m,
      opposingZoneHigh: 3998.5m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { StopPushBeyondZone = false },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected" && item.Message.Contains("opposing zone")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task SweepWickBeyondStopEnvelopeRejectsWithDedicatedCounter()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 70,
      sweepLow: 3993.5m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("stop_exceeds_envelope_after_wick")
    );
    Assert.Contains(
      ("XAU", "stop_exceeds_envelope_after_wick"),
      store.GateRejects
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoOpposingZoneMayWidenStopAndNotifiesOwner()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      manualStopLoss: 4002.0m,
      opposingZoneLow: 4001.0m,
      opposingZoneHigh: 4003.0m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(300_000, order.RelativeStopLoss);
    Assert.Contains(store.Events, item =>
      item.Type == "warning"
      && item.Message.Contains("SL widened 4002 -> 4003")
      && item.Message.Contains("cleared opposing zone 4001-4003")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoKeepsOwnerStopWhenZoneWideningExceedsEnvelope()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var logs = new List<string>();
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      manualStopLoss: 4006.0m,
      opposingZoneLow: 4005.5m,
      opposingZoneHigh: 4007.0m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, logs.Add);
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(600_000, order.RelativeStopLoss);
    Assert.Contains(logs, item => item.Contains("kept owner SL 4006"));
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task OpenedEventCarriesSetupRegimeAndConfluence()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 70,
      regime: "chop",
      confluence: 3
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var opened = Assert.Single(store.Events, item => item.Type == "opened");
    Assert.Equal("Range Box Scalp", opened.Setup);
    Assert.Equal("chop", opened.Regime);
    Assert.Equal(3, opened.Confluence);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoCandidateIsAcceptedAndPlacesLimitAtProximalEdgeWhenPriceOutsideZone()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "SELL",
      entryLow: 3999.5m,
      entryHigh: 4000.5m,
      manualStopLoss: 4006.0m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    // Bid 3990.0 sits well outside (below) the zone - previously this
    // exact shape ("unsupported" mode/version combo) would have hit the
    // "unsupported candidate" reject.
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Empty(client.Orders);
    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(TradeDirection.Sell, order.Direction);
    // SELL proximal edge = zone.Low (mirrors ZoneFillPlanner's proximal
    // edge: the side price would touch first approaching from outside).
    Assert.Equal(3999.5m, order.LimitPrice);
    Assert.Equal(600, order.Volume);
    Assert.Equal(650_000, order.RelativeStopLoss);
    Assert.StartsWith("avm|", order.Comment);
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");
    var planned = Assert.Single(store.Events, item => item.Type == "manual_planned");
    Assert.Equal("Manual Algo", planned.Setup);
    Assert.Equal(new[] { 30, 60, 90 }, planned.TargetsPips);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoPlacesLimitAtCurrentPriceWhenPriceAlreadyInsideZone()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "SELL",
      entryLow: 3999.5m,
      entryHigh: 4000.5m,
      manualStopLoss: 4006.0m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    // Bid 4000.0 already sits inside [3999.5, 4000.5].
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(4000.0m, order.LimitPrice);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoUsesAbsoluteStopNotStructureStopMath()
  {
    // No atr/structure_swing anywhere on this candidate - if the manual
    // algo path ever fell through to StructureStopPlanner.Plan (which
    // requires both to be positive decimals), this would be rejected with
    // "structure context unavailable on candidate" instead of an order.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "BUY",
      entryLow: 3999.5m,
      entryHigh: 4000.5m,
      manualStopLoss: 3994.0m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4010.0m, 4010.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(TradeDirection.Buy, order.Direction);
    // BUY proximal edge = zone.High.
    Assert.Equal(4000.5m, order.LimitPrice);
    // |4000.5 - 3994.0| = 6.5 -> 65p, straight from the manual stop, not
    // any structure-swing-derived distance (there is none on this candidate).
    Assert.Equal(650_000, order.RelativeStopLoss);
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoTtlCancelUsesIntentExpiresAtNotZoneFillFormula()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var expiresAt = now.ToUnixTimeSeconds() + 120;
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "SELL",
      entryLow: 3999.5m,
      entryHigh: 4000.5m,
      manualStopLoss: 4006.0m,
      expiresAt: expiresAt
    ));
    var client = new FakeTradingClient();
    // ZoneFillTtlBars=30 -> 1800s, far longer than the manual intent's own
    // 120s expiry: if the manual TTL cancel used zone-fill's bars*60s
    // formula instead of the intent's own absolute expires_at, this order
    // would still be resting at t+121s.
    var engine = new AutoTradeEngine(
      Options() with { ZoneFillTtlBars = 30 },
      store,
      () => now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    Assert.Single(client.LimitOrders);

    now = Now.AddSeconds(121);
    await WaitForEventAsync(store, "manual_expired");

    Assert.Single(client.CancelledOrders);
    Assert.Empty(client.PendingOrders);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualCommandCancelPendingCancelsRealPendingOrder()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(manualStopLoss: 4006.0m));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var orderId = client.PendingOrders.Single().OrderId;

    store.EnqueueCommand(JsonSerializer.Serialize(new
    {
      type = "cancel_pending",
      intent_id = "manual:1:0",
    }));
    await WaitForEventAsync(store, "manual_cancelled");

    Assert.Contains(orderId, client.CancelledOrders);
    Assert.Empty(client.PendingOrders);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  private static async Task<long> OpenManualAlgoPositionAsync(
    FakeAutoTradeStore store,
    FakeTradingClient client,
    Func<DateTimeOffset> clock,
    Action<DateTimeOffset> advanceClock,
    CancellationToken cancellationToken
  )
  {
    var orderId = client.PendingOrders.Single().OrderId;
    client.FillPendingOrder(orderId);
    advanceClock(Now.AddSeconds(16));
    await WaitForEventAsync(store, "manual_opened");
    var opened = store.Events.Single(item => item.Type == "manual_opened");
    Assert.Equal("algo_manual", opened.Stream);
    return opened.PositionId!.Value;
  }

  [Fact]
  public async Task ManualCommandCloseClosesRealPositionAtBrokerPrice()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(ManualCandidateJson(manualStopLoss: 4006.0m));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var positionId = await OpenManualAlgoPositionAsync(
      store, client, () => now, value => now = value, cts.Token
    );

    store.EnqueueCommand(JsonSerializer.Serialize(new
    {
      type = "close",
      intent_id = "manual:1:0",
      position_id = positionId,
    }));
    await WaitForEventAsync(store, "manual_closed");

    var close = Assert.Single(client.Closes);
    Assert.Equal(positionId, close.PositionId);
    Assert.Equal(600, close.Volume);
    var closed = store.Events.Single(item => item.Type == "manual_closed");
    Assert.Equal(4013.2m, closed.Price);
    Assert.Equal(600, closed.Volume);
    Assert.Equal(0, closed.RemainingVolume);
    Assert.Equal("algo_manual", closed.Stream);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualCommandCloseSupportsPartialFraction()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(ManualCandidateJson(manualStopLoss: 4006.0m));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var positionId = await OpenManualAlgoPositionAsync(
      store, client, () => now, value => now = value, cts.Token
    );

    store.EnqueueCommand(JsonSerializer.Serialize(new
    {
      type = "close",
      intent_id = "manual:1:0",
      position_id = positionId,
      frac = 0.5,
    }));
    await WaitForEventAsync(store, "manual_closed");

    var close = Assert.Single(client.Closes);
    Assert.Equal(300, close.Volume);
    var closed = store.Events.Single(item => item.Type == "manual_closed");
    Assert.Equal(300, closed.RemainingVolume);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualCommandMoveSlAmendsRealStopLoss()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(ManualCandidateJson(manualStopLoss: 4006.0m));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var positionId = await OpenManualAlgoPositionAsync(
      store, client, () => now, value => now = value, cts.Token
    );
    var amendmentsBefore = client.StopAmendments.Count;

    store.EnqueueCommand(JsonSerializer.Serialize(new
    {
      type = "move_sl",
      intent_id = "manual:1:0",
      position_id = positionId,
      price = 4008.5,
    }));
    await WaitForEventAsync(store, "manual_sl_moved");

    Assert.Equal(amendmentsBefore + 1, client.StopAmendments.Count);
    Assert.Contains((positionId, 4008.5m), client.StopAmendments);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task PositionClosedEventCarriesLastKnownStopLossAsPrice()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var positionId = client.StopAmendments.Single().PositionId;

    // Simulate a broker-side SL hit or a manual close done directly in the
    // cTrader app: the position simply vanishes from ReconcilePositionsAsync.
    client.RemovePosition(positionId);
    now = Now.AddSeconds(16);
    await WaitForEventAsync(store, "position_closed");

    var closed = store.Events.Single(item => item.Type == "position_closed");
    Assert.Equal(3993.7m, closed.Price);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ReconcileDetectedCloseRecordsZoneCooldown()
  {
    // The engine cannot tell an SL hit from a manual close apart here - both
    // look identical (a tracked position vanishes from the broker snapshot)
    // - so per Fix 3's rule, this branch always records the cooldown marker.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var positionId = client.StopAmendments.Single().PositionId;

    client.RemovePosition(positionId);
    now = Now.AddSeconds(16);
    await WaitForEventAsync(store, "position_closed");

    var cooldown = Assert.Single(store.ZoneCooldowns);
    Assert.Equal(4000.2m, cooldown.EntryPrice);
    Assert.Equal(3993.7m, cooldown.StopPrice);
    Assert.Equal(("XAU", "BUY"), Assert.Single(store.ZoneCooldownDirections));

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task FullTakeProfitCloseNeverRecordsZoneCooldown()
  {
    // A clean TP full-close untracks the position itself (ProcessTargetsAsync)
    // before the next reconcile ever runs, so it must never be mistaken for
    // an ambiguous stop-out/manual close.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(BoxCandidateJson(fullTpPips: 70))
    {
      DailyTradeCount = 100,
    };
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4007.2m, 4007.4m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    await WaitForEventAsync(store, "take_profit");

    // ProcessTargetsAsync already removed the position from the store the
    // instant it closed (state.RemainingVolume <= 0) - it is structurally
    // impossible for a later reconcile tick to ever see it as "stale",
    // since GetTrackedPositionIdsAsync can no longer return it at all.
    Assert.Empty(store.Positions);
    Assert.Empty(store.ZoneCooldowns);
    Assert.DoesNotContain(store.Events, item => item.Type == "position_closed");

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

  private static async Task WaitUntilAsync(Func<bool> predicate)
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(2));
    while (!predicate())
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
    string direction = "BUY",
    char candidate = 'a',
    long createdAt = 1_000,
    long barTs = 1_000,
    decimal? structureSwing = null,
    decimal entryLow = 3999.5m,
    decimal entryHigh = 4000.5m,
    string? regime = null
  ) => JsonSerializer.Serialize(new
  {
    version = 2,
    candidate_id = new string(candidate, 64),
    symbol = "XAU",
    timeframe,
    setup,
    mode,
    direction,
    trigger_ts = "1000",
    created_at = createdAt,
    spot_ts = 1_000,
    current_price = 4000.1,
    key_level = 4000.0,
    entry_zone = new { low = entryLow, high = entryHigh },
    confluence = 2,
    reasons = new[] { "lower barrier x2", "rejection at scored edge" },
    bar_ts = barTs,
    atr = 1.0,
    structure_swing = structureSwing
      ?? (direction == "BUY" ? 3993.5m : 4006.2m),
    displacement_direction = direction == "BUY" ? "up" : "down",
    displacement_age_bars = 1,
    bos_direction = direction == "BUY" ? "up" : "down",
    bos_ts = 1_000,
    opposing_level_distance_atr = 2.0,
    regime,
  });

  private static string TrendCandidateJson(
    string mode = "auto_trend_pullback",
    string setup = "Trend Pullback",
    string direction = "BUY",
    char candidate = 'a',
    long createdAt = 1_000,
    long barTs = 1_000,
    decimal structureSwing = 3993.5m,
    decimal atr = 1.0m,
    decimal entryLow = 3999.5m,
    decimal entryHigh = 4000.5m,
    int[]? targetsPips = null,
    string regime = "trend"
  ) => JsonSerializer.Serialize(new
  {
    version = 3,
    candidate_id = new string(candidate, 64),
    symbol = "XAU",
    timeframe = "M1",
    setup,
    mode,
    direction,
    trigger_ts = "1000",
    created_at = createdAt,
    spot_ts = 1_000,
    current_price = 4000.1,
    key_level = 4000.0,
    entry_zone = new { low = entryLow, high = entryHigh },
    confluence = 2,
    reasons = new[] { "trend pullback into displacement origin zone" },
    bar_ts = barTs,
    atr,
    structure_swing = structureSwing,
    targets_pips = targetsPips ?? new[] { 30, 60, 90 },
    regime,
  });

  private static string StrategyMatchCandidateJson(
    string setup = "Liquidity Sweep",
    string direction = "BUY",
    char candidate = 's',
    int[]? targetsPips = null
  ) => JsonSerializer.Serialize(new
  {
    version = 4,
    candidate_id = new string(candidate, 64),
    symbol = "XAU",
    timeframe = "M5",
    setup,
    mode = "auto_strategy_match",
    direction,
    trigger_ts = "1000",
    created_at = 1_000,
    spot_ts = 1_000,
    current_price = 4000.1,
    key_level = 4000.0,
    entry_zone = new { low = 3999.5m, high = 4000.5m },
    confluence = 3,
    reasons = new[] { "scanner detector matched structure" },
    bar_ts = 1_000,
    atr = 1.0,
    structure_swing = direction == "BUY" ? 3993.5m : 4006.2m,
    targets_pips = targetsPips ?? new[] { 30, 60, 90 },
    regime = "strategy_match",
  });

  private static string BoxCandidateJson(
    int fullTpPips,
    string timeframe = "M1",
    string direction = "BUY",
    char candidate = 'a',
    decimal? structureSwing = null,
    decimal? opposingZoneLow = null,
    decimal? opposingZoneHigh = null,
    decimal? sweepLow = null,
    decimal? sweepHigh = null,
    string? regime = null,
    int? confluence = null
  ) => JsonSerializer.Serialize(new
  {
    version = 3,
    candidate_id = new string(candidate, 64),
    symbol = "XAU",
    timeframe,
    setup = "Range Box Scalp",
    mode = "auto_box_scalp",
    direction,
    trigger_ts = "1000",
    created_at = 1_000,
    spot_ts = 1_000,
    current_price = 4000.1,
    key_level = direction == "BUY" ? 4000.0 : 4008.0,
    entry_zone = direction == "BUY"
      ? new { low = 3999.5m, high = 4000.5m }
      : new { low = 4007.8m, high = 4008.2m },
    confluence = confluence ?? 2,
    reasons = new[] { "M1 range rejection", $"full TP {fullTpPips} pips" },
    bar_ts = 1_000,
    atr = 1.0,
    structure_swing = structureSwing
      ?? (direction == "BUY" ? 3998.0m : 4002.5m),
    range_id = "xau-8000-8016",
    range_low = 4000.0,
    range_high = 4008.0,
    full_take_profit_pips = fullTpPips,
    regime,
    opposing_zone_low = opposingZoneLow,
    opposing_zone_high = opposingZoneHigh,
    sweep_low = sweepLow,
    sweep_high = sweepHigh,
  });

  // Mirrors telegram-bot's manual_execution._intent_to_candidate_payload:
  // no atr/structure_swing at all (the manual-algo path must never need
  // them), manual_stop_loss/manual_expires_at/targets_pips instead.
  private static string ManualCandidateJson(
    string direction = "SELL",
    string candidateId = "manual:1:0",
    long createdAt = 1_000,
    decimal entryLow = 3999.5m,
    decimal entryHigh = 4000.5m,
    decimal manualStopLoss = 4006.0m,
    int[]? targetsPips = null,
    long? expiresAt = null,
    int confluence = 1,
    decimal? opposingZoneLow = null,
    decimal? opposingZoneHigh = null
  ) => JsonSerializer.Serialize(new
  {
    version = 3,
    candidate_id = candidateId,
    symbol = "XAU",
    timeframe = "M1",
    setup = "Manual Algo",
    mode = "manual_algo",
    direction,
    trigger_ts = "1000",
    created_at = createdAt,
    spot_ts = (long?)null,
    current_price = (double)((entryLow + entryHigh) / 2m),
    key_level = (double)((entryLow + entryHigh) / 2m),
    entry_zone = new { low = entryLow, high = entryHigh },
    confluence,
    reasons = new[] { "manual /algo signal" },
    manual_stop_loss = manualStopLoss,
    manual_expires_at = expiresAt,
    targets_pips = targetsPips ?? new[] { 30, 60, 90 },
    opposing_zone_low = opposingZoneLow,
    opposing_zone_high = opposingZoneHigh,
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
    public List<LimitOrderRequest> LimitOrders { get; } = [];
    public List<TradingPendingOrder> PendingOrders { get; } = [];
    public List<long> CancelledOrders { get; } = [];
    public List<(long PositionId, decimal StopLoss)> StopAmendments { get; } = [];
    public List<(long PositionId, long Volume)> Closes { get; } = [];
    public int? FailAmendmentCall { get; init; }
    public int? FailReconcileCall { get; init; }
    public bool BlockClose { get; init; }
    public TaskCompletionSource<bool> ReconcileFaultEntered { get; } = new(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    public TaskCompletionSource<bool> ReleaseReconcileFault { get; } = new(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    private readonly List<TradingPosition> _positions = [];
    private readonly Queue<decimal> _marketExecutionPrices = [];
    private int _amendmentCalls;
    private int _reconcileCalls;
    private long _nextPositionId = 91;
    private long _nextOrderId = 81;

    public void SeedPosition(TradingPosition position) => _positions.Add(position);
    public void EnqueueMarketExecutionPrice(decimal price) =>
      _marketExecutionPrices.Enqueue(price);
    public void RemovePosition(long positionId) =>
      _positions.RemoveAll(position => position.PositionId == positionId);

    public void FillPendingOrder(long orderId)
    {
      var pending = PendingOrders.Single(order => order.OrderId == orderId);
      var request = LimitOrders.Single(order => order.Comment == pending.Comment);
      PendingOrders.Remove(pending);
      var distance = request.RelativeStopLoss / 100_000m;
      var stopLoss = request.Direction == TradeDirection.Buy
        ? request.LimitPrice - distance
        : request.LimitPrice + distance;
      _positions.Add(new TradingPosition(
        _nextPositionId++,
        request.SymbolId,
        request.Direction,
        request.Volume,
        request.LimitPrice,
        stopLoss,
        request.Label,
        request.Comment
      ));
    }

    public Task<IReadOnlyList<TradingAccountGrant>> GetAccountGrantsAsync(
      CancellationToken cancellationToken
    ) => Task.FromResult(Grants);

    public Task<TradingAccountSnapshot> GetTradingAccountAsync(
      CancellationToken cancellationToken
    ) => Task.FromResult(Account);

    public Task<IReadOnlyList<TradingPosition>> ReconcilePositionsAsync(
      CancellationToken cancellationToken
    ) => Task.FromResult<IReadOnlyList<TradingPosition>>(_positions.ToArray());

    public Task<IReadOnlyList<TradingPendingOrder>> ReconcilePendingOrdersAsync(
      CancellationToken cancellationToken
    ) => Task.FromResult<IReadOnlyList<TradingPendingOrder>>(
      PendingOrders.ToArray()
    );

    public async Task<TradingReconcileSnapshot> ReconcileAccountAsync(
      CancellationToken cancellationToken
    )
    {
      _reconcileCalls++;
      if (_reconcileCalls == FailReconcileCall)
      {
        ReconcileFaultEntered.TrySetResult(true);
        await ReleaseReconcileFault.Task.WaitAsync(cancellationToken);
        throw new InvalidOperationException("Trading account is not authorized");
      }
      return new TradingReconcileSnapshot(
        _positions.ToArray(),
        PendingOrders.ToArray()
      );
    }

    public Task<TradeExecution> PlaceMarketOrderAsync(
      MarketOrderRequest order,
      CancellationToken cancellationToken
    )
    {
      Orders.Add(order);
      var fill = _marketExecutionPrices.TryDequeue(out var queued)
        ? queued
        : 4000.2m;
      var positionId = _nextPositionId++;
      var orderId = _nextOrderId++;
      var distance = order.RelativeStopLoss / 100_000m;
      var stopLoss = order.Direction == TradeDirection.Buy
        ? fill - distance
        : fill + distance;
      _positions.Add(new TradingPosition(
        positionId,
        order.SymbolId,
        order.Direction,
        order.Volume,
        fill,
        stopLoss,
        order.Label,
        order.Comment
      ));
      return Task.FromResult(new TradeExecution(
        positionId,
        orderId,
        fill,
        order.Volume
      ));
    }

    public Task<long> PlaceLimitOrderAsync(
      LimitOrderRequest order,
      CancellationToken cancellationToken
    )
    {
      LimitOrders.Add(order);
      var orderId = _nextOrderId++;
      PendingOrders.Add(new TradingPendingOrder(
        orderId,
        order.SymbolId,
        order.Direction,
        order.Volume,
        order.LimitPrice,
        order.Label,
        order.Comment
      ));
      return Task.FromResult(orderId);
    }

    public Task CancelPendingOrderAsync(
      long orderId,
      CancellationToken cancellationToken
    )
    {
      CancelledOrders.Add(orderId);
      PendingOrders.RemoveAll(order => order.OrderId == orderId);
      return Task.CompletedTask;
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

    public async Task<TradeExecution> ClosePositionAsync(
      long positionId,
      long volume,
      CancellationToken cancellationToken
    )
    {
      Closes.Add((positionId, volume));
      if (BlockClose)
      {
        await Task.Delay(Timeout.Infinite, cancellationToken);
      }
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
      return new TradeExecution(
        positionId,
        100 + Closes.Count,
        4013.2m,
        volume,
        Math.Max(0, remaining)
      );
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

  // Must match AutoTradeEngine's private ManualCommandStream constant - not
  // exposed via AutoTradeOptions, see the comment on that constant.
  private const string CommandStreamName = "manual_trade:commands";

  private sealed class FakeAutoTradeStore(string payload) : IAutoTradeStore
  {
    private string _cursor = "0-0";
    private string _commandCursor = "0-0";
    private readonly Dictionary<string, string> _candidateStatus = [];
    private readonly List<string> _payloads = [payload];
    private readonly List<string> _commandPayloads = [];
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
    public TaskCompletionSource<bool> CommandCursorAdvanced { get; } = new(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    public string Cursor => _cursor;
    public string CommandCursor => _commandCursor;
    private long _daily;
    public long DailyTradeCount
    {
      get => _daily;
      init => _daily = value;
    }

    public void EnqueueCandidate(string candidatePayload) =>
      _payloads.Add(candidatePayload);

    public void EnqueueCommand(string commandPayload) =>
      _commandPayloads.Add(commandPayload);

    public Task<string> GetCursorAsync(CancellationToken cancellationToken) =>
      Task.FromResult(_cursor);
    public Task SetCursorAsync(string cursor, CancellationToken cancellationToken)
    {
      _cursor = cursor;
      CursorAdvanced.TrySetResult(true);
      return Task.CompletedTask;
    }
    public Task<string> GetCommandCursorAsync(CancellationToken cancellationToken) =>
      Task.FromResult(_commandCursor);
    public Task SetCommandCursorAsync(string cursor, CancellationToken cancellationToken)
    {
      _commandCursor = cursor;
      CommandCursorAdvanced.TrySetResult(true);
      return Task.CompletedTask;
    }
    public Task<IReadOnlyList<TradeStreamEntry>> ReadCandidatesAsync(
      string stream,
      string afterId,
      int count,
      CancellationToken cancellationToken
    )
    {
      var list = stream == CommandStreamName ? _commandPayloads : _payloads;
      var last = afterId == "0-0"
        ? 0
        : int.Parse(afterId.Split('-')[0]);
      if (last >= list.Count)
      {
        return Task.FromResult<IReadOnlyList<TradeStreamEntry>>([]);
      }
      return Task.FromResult<IReadOnlyList<TradeStreamEntry>>([
        new TradeStreamEntry($"{last + 1}-0", list[last]),
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
    public List<(string Symbol, string Condition)> GateRejects { get; } = [];
    public Task IncrementGateRejectAsync(
      string symbol,
      string condition,
      CancellationToken cancellationToken
    )
    {
      GateRejects.Add((symbol, condition));
      return Task.CompletedTask;
    }
    public List<ZoneCooldownRecord> ZoneCooldowns { get; } = [];
    public List<(string Symbol, string Direction)> ZoneCooldownDirections { get; } = [];
    public Task RecordZoneCooldownAsync(
      string symbol,
      string direction,
      decimal entryPrice,
      decimal stopPrice,
      long closedAt,
      int ttlMinutes,
      CancellationToken cancellationToken
    )
    {
      ZoneCooldowns.Add(new ZoneCooldownRecord(entryPrice, stopPrice, closedAt));
      ZoneCooldownDirections.Add((symbol, direction));
      return Task.CompletedTask;
    }
  }
}

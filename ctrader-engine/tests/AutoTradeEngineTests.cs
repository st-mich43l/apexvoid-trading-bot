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

  [Theory]
  [InlineData(110)]
  [InlineData(80)]
  [InlineData(71)]
  public async Task RangeBoxScaleOutAppliesWhenFullTpExceedsThreshold(int fullTp)
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: fullTp,
      timeframe: "M5"
    ));
    var client = new FakeTradingClient();
    var options = Options() with
    {
      RangeFlipEnabled = false,
      RangeTargetsPips = [20, 30, 40, 50, 70, 71, 80, 110],
      RangeBoxScaleOutEnabled = true,
      RangeBoxScaleOutThresholdPips = 70,
      RangeBoxScaleOutTriggerPips = 30,
      RangeBoxScaleOutFraction = 0.50m,
    };
    var engine = new AutoTradeEngine(options, store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.Orders);
    var opened = Assert.Single(store.Events, item => item.Type == "opened");
    Assert.Equal(new[] { 30, fullTp }, opened.TargetsPips);
    Assert.Contains($"TP1 +30p book 50%", opened.Message);
    Assert.Contains($"Full TP +{fullTp}p", opened.Message);
    var state = Assert.Single(store.Positions.Values);
    Assert.Equal(2, state.Slices.Count);
    Assert.Equal(order.Volume, state.Slices.Sum());
    Assert.Equal(2, state.TargetsPips.Count);
    Assert.Equal(2, state.TargetPrices!.Count);
    Assert.Equal(state.EntryPrice + 30m * 0.1m, state.TargetPrices[0]);
    Assert.Equal(state.EntryPrice + fullTp * 0.1m, state.TargetPrices[1]);
    Assert.False(state.RangeBoxScaleOutBooked);

    // Gap through TP1 — one partial only.
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4003.5m, 4003.7m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.Single(client.Closes);
    Assert.Equal(state.Slices[0], client.Closes[0].Volume);
    var tp1 = Assert.Single(
      store.Events.Where(item => item.Type == "take_profit"),
      item => item.Message.StartsWith("TP1", StringComparison.Ordinal)
    );
    Assert.Equal(30, tp1.TargetPips);
    state = Assert.Single(store.Positions.Values);
    Assert.True(state.RangeBoxScaleOutBooked);
    Assert.Equal(1, state.NextTargetIndex);

    // Repeat above TP1 — no second partial.
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4004.0m, 4004.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.Single(client.Closes);

    // Final TP.
    var finalBid = state.EntryPrice + fullTp * 0.1m;
    var remainingBeforeFinal = state.RemainingVolume;
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", finalBid, finalBid + 0.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.Equal(2, client.Closes.Count);
    Assert.Equal(remainingBeforeFinal, client.Closes[1].Volume);
    Assert.Empty(store.Positions);
    var finalTp = store.Events.Last(item => item.Type == "take_profit");
    Assert.StartsWith("FULL TP", finalTp.Message);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Theory]
  [InlineData(70)]
  [InlineData(60)]
  public async Task RangeBoxScaleOutDoesNotApplyAtOrBelowThreshold(int fullTp)
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: fullTp,
      timeframe: "M5"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with
      {
        RangeFlipEnabled = false,
        RangeTargetsPips = [20, 30, 40, 50, 60, 70],
        RangeBoxScaleOutEnabled = true,
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

    var opened = Assert.Single(store.Events, item => item.Type == "opened");
    Assert.Equal(new[] { fullTp }, opened.TargetsPips);
    Assert.Contains($"full TP {fullTp}p", opened.Message);
    Assert.DoesNotContain("TP1 +30p", opened.Message);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RangeBoxScaleOutSellUsesFillMinusTrigger()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    // Range high for 110p Full TP is ~4012; SELL stop needs swing above entry.
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 110,
      timeframe: "M5",
      direction: "SELL",
      structureSwing: 4013.5m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with
      {
        RangeFlipEnabled = false,
        RangeTargetsPips = [20, 30, 40, 50, 70, 110],
        RangeBoxScaleOutEnabled = true,
      },
      store,
      () => Now,
      _ => { }
    );
    // Spot near the SELL rail (range high ~4012).
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4011.8m, 4012.0m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var state = Assert.Single(store.Positions.Values);
    Assert.Equal(TradeDirection.Sell, state.Direction);
    Assert.Equal(state.EntryPrice - 30m * 0.1m, state.TargetPrices![0]);
    Assert.Equal(state.EntryPrice - 110m * 0.1m, state.TargetPrices[1]);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RangeBoxScaleOutNotReplayedAfterRestartFlag()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 110,
      timeframe: "M5"
    ));
    var client = new FakeTradingClient();
    var options = Options() with
    {
      RangeFlipEnabled = false,
      RangeTargetsPips = [20, 30, 40, 50, 70, 110],
      RangeBoxScaleOutEnabled = true,
    };
    var engine = new AutoTradeEngine(options, store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4003.5m, 4003.7m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.Single(client.Closes);
    var booked = Assert.Single(store.Positions.Values);
    Assert.True(booked.RangeBoxScaleOutBooked);
    Assert.Equal(1, booked.NextTargetIndex);

    // Simulate restart reload: same persisted state still above TP1.
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4005.0m, 4005.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.Single(client.Closes);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task StrategyMatchRangeEdgeDoesNotGetRangeBoxScaleOut()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      setup: "Range Edge Scalp",
      targetsPips: [110]
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with
      {
        RangeBoxScaleOutEnabled = true,
        RangeFlipEnabled = false,
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

    var opened = Assert.Single(store.Events, item => item.Type == "opened");
    Assert.Equal(new[] { 110 }, opened.TargetsPips);
    Assert.DoesNotContain("TP1 +30p", opened.Message);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task BoxRangeScalpClosesFullVolumeAtItsSingleTarget()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 50,
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
    Assert.Contains($"|{order.Volume}|50|1|", order.Comment);
    var opened = Assert.Single(store.Events, item => item.Type == "opened");
    Assert.Equal("algo_auto", opened.Stream);
    Assert.Equal("BUY", opened.Direction);
    Assert.Contains("full TP 50p", opened.Message);
    Assert.Contains("range 4,000.00-", opened.Message);
    var stopPips = order.RelativeStopLoss / 10_000m;
    Assert.Equal(stopPips, opened.StopPips);
    Assert.Equal(new[] { 50 }, opened.TargetsPips);

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4007.2m, 4007.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.Equal((91, order.Volume), Assert.Single(client.Closes));
    var takeProfit = Assert.Single(
      store.Events,
      item => item.Type == "take_profit"
    );
    Assert.Equal(50, takeProfit.TargetPips);
    Assert.Equal(stopPips, takeProfit.StopPips);
    Assert.Equal(130.0m, takeProfit.LegRealizedPips);
    Assert.Equal(order.Volume, takeProfit.GroupInitialVolume);
    Assert.Equal(Symbol.LotSize, takeProfit.LotSize);
    Assert.StartsWith("FULL TP +130.0 pips", takeProfit.Message);
    Assert.DoesNotContain("$", takeProfit.Message);
    Assert.DoesNotContain(store.Events, item => item.Type == "stop_moved");
    Assert.Empty(store.Positions);

    var groupResult = Assert.Single(store.Events, item => item.Type == "group_result");
    Assert.DoesNotContain("$", groupResult.Message);
    Assert.Contains("pips", groupResult.Message);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RangeFlipTargetExitsInsideOpposingEdgeAndClearsPendingOnFill()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(fullTpPips: 50));
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
      fullTpPips: 50,
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
  public async Task BoxRangeScalpRejectsTargetOutsideConfiguredLadder()
  {
    // The executor must validate membership in AUTO_TRADE_RANGE_TARGETS_PIPS
    // (default 30/40/50), not a hardcoded "50 or 70" expression - Python
    // already selected this target upstream, so a value outside the shared
    // ladder means the two sides drifted and must be rejected loudly.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(fullTpPips: 45));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("invalid range-box contract")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task BoxRangeScalpAcceptsNonDefaultConfiguredTarget()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(fullTpPips: 45));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { RangeTargetsPips = [45] },
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

    Assert.Single(client.Orders);
    Assert.DoesNotContain(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("invalid range-box contract")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RangeFlipTimeoutAlertsAndDoesNotBookTarget()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(fullTpPips: 50));
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
    Assert.Contains(("XAU", "range_box_awaiting_flat"), store.GateRejects);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task UnmanagedPositionBlocksNewCandidateAndRecordsCounter()
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
      "some-other-ea",
      "manually opened, not ours"
    ));
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
      && item.Message.Contains("unmanaged XAU position or pending order")
    );
    Assert.Contains(("XAU", "unmanaged_exposure"), store.GateRejects);

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
  [InlineData("ScopeView", "FullAccess", "Hedged", "FP Markets")]
  [InlineData("ScopeTrade", "NoTrading", "Hedged", "FP Markets")]
  [InlineData("ScopeTrade", "FullAccess", "Netted", "FP Markets")]
  [InlineData("ScopeTrade", "FullAccess", "Hedged", "Other Broker")]
  public async Task RequiresTradingScopeFullAccessHedgedExpectedBrokerAccount(
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
    var store = new FakeAutoTradeStore(TrendCandidateJson(
      mode: "auto_trend_breakout",
      setup: "Trend Breakout"
    ));
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
    store.EnqueueCandidate(TrendCandidateJson(
      mode: "auto_trend_breakout",
      setup: "Trend Breakout",
      candidate: 'b',
      barTs: 1_180,
      structureSwing: 4001.9m,
      entryLow: 4003m,
      entryHigh: 4004m,
      parentGroupId: new string('a', 10)
    ));
    await WaitForEventAsync(store, "add");

    Assert.Equal(2, client.Orders.Count);
    Assert.StartsWith("av3|bbbbbbbbbb|aaaaaaaaaa|2|", client.Orders[1].Comment);
    Assert.Equal(500, client.Orders[1].Volume);
    Assert.Equal((92, 3999.4m), client.StopAmendments.Last());
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
  public async Task PriceInsideSellZoneFallsBackToSingleEntryInsteadOfRejectingProximalSide()
  {
    // Production incident: Breakout Continuation SELL with price inside
    // entry zone 4024.37-4027.45 (~4025.59). Classic proximal=zone.Low sits
    // below bid and previously hard-rejected with
    // "zone-fill proximal edge is not on the valid limit-order side".
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson(
      direction: "SELL",
      entryLow: 4024.37m,
      entryHigh: 4027.45m,
      setup: "Auto Range Scalp",
      mode: "auto_range_scalp",
      structureSwing: 4027.45m
    ));
    var client = new FakeTradingClient();
    var logs = new List<string>();
    var engine = new AutoTradeEngine(
      Options() with
      {
        ZoneFillEnabled = true,
        ZoneFillFallbackEnabled = true,
        InsideZoneMarketEntryEnabled = true,
      },
      store,
      () => Now,
      logs.Add
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4025.59m, 4025.79m, 1_000),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "rejected"
        && item.Message.Contains(
          "zone-fill proximal edge is not on the valid limit-order side"
        )
    );
    Assert.Contains(
      logs,
      message => message.Contains("single-entry fallback")
    );
    Assert.NotEmpty(client.Orders);
    Assert.Empty(client.LimitOrders);

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

  [Theory]
  [InlineData("Key Level Reaction", "key_level", "key_level")]
  [InlineData("Demand Zone Reaction", "supply_demand", "supply_demand")]
  [InlineData("Supply Zone Reaction", "supply_demand", "supply_demand")]
  [InlineData("Session Level Reaction", "session_level", "session_level")]
  [InlineData("Trendline Reaction", "trendline", "trendline")]
  public async Task StructuralRouteLifecycleCarriesIdsFromReceivedToFilled(
    string setup,
    string family,
    string structuralSource
  )
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var zoneId = $"{family}-zone-1";
    var reactionId = new string('r', 64);
    var thesisId = new string('t', 64);
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      setup: setup,
      strategyFamily: family,
      structuralSource: structuralSource,
      zoneId: zoneId,
      reactionId: reactionId,
      thesisId: thesisId,
      structuralZoneLow: 3998.0m,
      structuralZoneHigh: 4001.0m
    ));
    store.SeedPublishedCandidate(new string('s', 64));
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

    Assert.Single(client.Orders);
    var position = Assert.Single(store.Positions.Values);
    Assert.Equal(family, position.StrategyFamily);
    Assert.Equal(structuralSource, position.StructuralSource);
    Assert.Equal(zoneId, position.ZoneId);
    Assert.Equal(zoneId, position.StructuralZoneId);
    Assert.Equal(reactionId, position.ReactionId);
    Assert.Equal(thesisId, position.ThesisId);
    Assert.Equal(3998.0m, position.StructuralZoneLow);
    Assert.Equal(4001.0m, position.StructuralZoneHigh);

    var routeEvents = store.LifecycleEvents
      .Where(item => item.CandidateId == new string('s', 64))
      .ToArray();
    Assert.Contains(routeEvents, item => item.Type == "executor_received");
    Assert.Contains(
      routeEvents,
      item => item.State == "order_filled" || item.Type == "opened"
    );
    Assert.All(
      routeEvents.Where(item => !string.IsNullOrWhiteSpace(item.CandidateId)),
      item =>
      {
        Assert.Equal(new string('s', 64), item.CorrelationId);
        Assert.Equal(structuralSource, item.StructuralSource);
        Assert.Equal(zoneId, item.ZoneId);
        Assert.Equal(zoneId, item.StructuralZoneId);
        Assert.Equal(reactionId, item.ReactionId);
        Assert.Equal(thesisId, item.ThesisId);
      }
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ContextOnlyOpposingZoneDoesNotRejectOrPushStop()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 70,
      opposingZoneLow: 3990.0m,
      opposingZoneHigh: 4025.0m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with
      {
        ZoneFillEnabled = true,
        ExecutionZoneMaxWidthPips = 100m,
        ExecutionZoneMaxWidthAtr = 2.0m,
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

    Assert.Single(client.Orders);
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
    var store = new FakeAutoTradeStore(TrendCandidateJson());
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
    store.EnqueueCandidate(TrendCandidateJson(
      candidate: 'b',
      barTs: 1_180,
      structureSwing: 4001.9m,
      entryLow: 4003m,
      entryHigh: 4004m,
      regime: "chop",
      parentGroupId: new string('a', 10)
    ));
    await WaitForEventAsync(store, "rejected");

    Assert.Single(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("restricted to the trend regime")
    );

    client.EnqueueMarketExecutionPrice(4003.4m);
    store.EnqueueCandidate(TrendCandidateJson(
      candidate: 'c',
      barTs: 1_180,
      structureSwing: 4001.9m,
      entryLow: 4003m,
      entryHigh: 4004m,
      regime: "trend",
      parentGroupId: new string('a', 10)
    ));
    await WaitForEventAsync(store, "add");

    Assert.Equal(2, client.Orders.Count);
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task PullbackAddOpensAndTagsTheTrancheAndOrderMessage()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(TrendCandidateJson());
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { AddPullbackEnabled = true },
      store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    // Price runs through the initial BUY's TP1 (entry 4000.2 + 30p),
    // banking a partial (FakeTradingClient.ClosePositionAsync always fills
    // at 4013.2) and moving the stop to breakeven, satisfying the shared
    // "initial reached TP1/breakeven" and "group profitable" invariants.
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4003.2m, 4003.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    client.EnqueueMarketExecutionPrice(4003.4m);

    // Retrace back down into the mapped demand zone: retraceRatio =
    // |4008.0 - 4015.0| / |4000.2 - 4015.0| = 7 / 14.8 = 0.473, inside
    // [0.20, 0.70]. AddEntry (4008.0) still stays above InitialEntry
    // (4000.2) - a pullback, not averaging down.
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4007.8m, 4008.0m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    client.EnqueueMarketExecutionPrice(4008.0m);
    store.EnqueueCandidate(PullbackAddCandidateJson());
    await WaitForEventAsync(store, "add");

    Assert.Equal(2, client.Orders.Count);
    var add = Assert.Single(store.Events, item => item.Type == "add");
    Assert.Contains("add_pullback", add.Message);
    Assert.Contains("add_pullback", add.Setup);
    Assert.Equal(2, add.TrancheIndex);
    var state = Assert.Single(store.Positions.Values, s => s.TrancheIndex == 2);
    Assert.Contains("add_pullback", state.Setup);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task PullbackAddRejectsWhenRequiredStopExceedsEnvelope()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(TrendCandidateJson());
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { AddPullbackEnabled = true },
      store, () => Now, _ => { }
    );
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
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4007.8m, 4008.0m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    // A zone/structure swing 10 price units (100p) below entry pushes the
    // P5 stop far past the 65p trend envelope - must reject, not clamp the
    // stop inside the retrace.
    store.EnqueueCandidate(PullbackAddCandidateJson(
      structureSwing: 3908.0m
    ));
    await WaitForEventAsync(store, "rejected");

    Assert.Single(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected" && item.Message.Contains("envelope")
    );
    Assert.Contains(store.AddRejects, item =>
      item.Mode == "add_pullback" && item.Condition == "stop_exceeds_envelope"
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task PullbackAddRejectsWhenCombinedGroupWorstCaseExceedsCap()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(TrendCandidateJson());
    var client = new FakeTradingClient();
    // AddRiskFraction/AddSizeRatio widened so the add's own risk exceeds
    // the (fake-client-inflated) booked-profit buffer ScaleInPlanner's own
    // budget check already tolerates - P6, not that shared check, is what
    // must fire here. AddMaxGroupRiskPct tightened well below the
    // resulting worst-case percentage.
    var engine = new AutoTradeEngine(
      Options() with {
        AddPullbackEnabled = true,
        AddMaxGroupRiskPct = 0.1m,
        AddSizeRatio = 1.0m,
        AddRiskFraction = 1.0m,
      },
      store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    // Stops short of TP2 (60p/4006.2) so only TP1's partial is booked -
    // retraceRatio = |4005.0 - 4010.0| / |4000.2 - 4010.0| = 5 / 9.8 = 0.51.
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4004.8m, 4005.0m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    client.EnqueueMarketExecutionPrice(4004.8m);
    // structureSwing far below entry pushes the P5 stop to ~64p - inside
    // the 65p envelope, but wide enough that this tranche's own risk
    // outweighs the booked-profit buffer.
    store.EnqueueCandidate(PullbackAddCandidateJson(
      structureSwing: 3998.9m,
      entryLow: 4004.5m,
      entryHigh: 4005.5m,
      opposingZoneLow: 4004.5m,
      opposingZoneHigh: 4005.5m,
      extremePrice: 4010.0m
    ));
    await WaitForEventAsync(store, "rejected");

    Assert.Single(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected" && item.Message.Contains("group worst case")
    );
    Assert.Contains(store.AddRejects, item =>
      item.Mode == "add_pullback" && item.Condition == "group_worst_case_exceeded"
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task PullbackDisabledRejectsPullbackShapedCandidate()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(TrendCandidateJson());
    var client = new FakeTradingClient();
    // AddPullbackEnabled defaults false - not set here on purpose. Momentum
    // itself still opening a second tranche with the flag at this same
    // default is already covered by the pre-existing
    // MomentumContinuationOpensIndependentSecondTranche regression test.
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
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4007.8m, 4008.0m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    // PullbackAddCandidateJson carries no displacement/BOS fields, so
    // momentum can't qualify either - this exercises the "neither mode,
    // pullback disabled" fallthrough at the engine level.
    store.EnqueueCandidate(PullbackAddCandidateJson());
    await WaitForEventAsync(store, "rejected");

    Assert.Single(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("fresh")
      && item.Message.Contains("displacement")
    );

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
      fullTpPips: 50,
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
      fullTpPips: 50,
      opposingZoneLow: 3990.0m,
      opposingZoneHigh: 3998.0m,
      atr: 5.0m
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
      fullTpPips: 50,
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
      fullTpPips: 50,
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
  public async Task ManualAlgoBypassesOpposingZoneAndKeepsOwnerStop()
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
    Assert.Equal(200_000, order.RelativeStopLoss);
    Assert.DoesNotContain(store.Events, item => item.Type == "warning");

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
    Assert.DoesNotContain(logs, item => item.Contains("owner SL"));
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task OpenedEventCarriesSetupRegimeAndConfluence()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 50,
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
    var planned = Assert.Single(
      store.Events,
      item => item.Type == "manual_limit_placed"
    );
    Assert.Equal("Manual Algo", planned.Setup);
    Assert.Equal(new[] { 30, 60, 90 }, planned.TargetsPips);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoFixesFirstLegToPointZeroFiveLotsAboveThreshold()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    // Balance 5000 -> table 0.30 lots; a 3.0-price-unit (30 pip) stop keeps
    // risk-bound sizing (0.333 lots) from binding instead, so the table
    // caps it at exactly 0.30 lots (3000 volume) - well above the 0.13
    // threshold, and evenly across 3 targets would otherwise book 0.10
    // lots (1000) on TP1.
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "SELL",
      entryLow: 3999.5m,
      entryHigh: 4000.5m,
      manualStopLoss: 4002.5m
    ));
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { Balance = 5_000m },
    };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4010.0m, 4010.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(3_000, order.Volume);
    // Comment layout: avm|candidate|group|volume|slices|targets|ordinals|barTs|expiresAt
    var slices = order.Comment.Split('|')[4];
    Assert.Equal("500,1300,1200", slices);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoKeepsEvenSplitAtOrBelowThreshold()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    // Same shape as the existing proximal-edge test: balance 2000 with a
    // 6.5-price-unit stop sizes to 0.06 lots, well under the 0.13
    // threshold, so the fix must not touch the even 3-way split.
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "SELL",
      entryLow: 3999.5m,
      entryHigh: 4000.5m,
      manualStopLoss: 4006.0m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(600, order.Volume);
    var slices = order.Comment.Split('|')[4];
    Assert.Equal("200,200,200", slices);

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

  [Theory]
  [InlineData(TradeDirection.Sell, "BUY")]
  [InlineData(TradeDirection.Buy, "SELL")]
  public async Task ManualAlgoAllowsOppositeAutonomousExposureOnHedgedDemo(
    TradeDirection existingDirection,
    string manualDirection
  )
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var isBuy = manualDirection == "BUY";
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: manualDirection,
      entryLow: 3999.5m,
      entryHigh: 4000.5m,
      manualStopLoss: isBuy ? 3994.0m : 4006.0m
    ));
    var client = new FakeTradingClient();
    client.SeedPosition(new TradingPosition(
      71,
      Symbol.SymbolId,
      existingDirection,
      500,
      4000m,
      existingDirection == TradeDirection.Buy ? 3996m : 4004m,
      "apexvoid-auto",
      "av3|autonomous|autonomous|1|500|500|30|1|900"
    ));
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    var bid = isBuy ? 4010.0m : 3990.0m;
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", bid, bid + 0.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(
      isBuy ? TradeDirection.Buy : TradeDirection.Sell,
      order.Direction
    );
    Assert.DoesNotContain(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("exposure", StringComparison.OrdinalIgnoreCase)
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoRejectsOppositeExposureWhenBrokerIsNotHedged()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "BUY",
      manualStopLoss: 3994.0m
    ));
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { AccountType = "Netted" },
    };
    client.SeedPosition(new TradingPosition(
      71, Symbol.SymbolId, TradeDirection.Sell, 500, 4000m, 4004m,
      "apexvoid-auto", "av3|autonomous|autonomous|1|500|500|30|1|900"
    ));
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4010.0m, 4010.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.LimitOrders);
    Assert.Contains(store.Events, item =>
      item.ReasonCode == "broker_account_not_hedged_for_opposite_manual_order"
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoPersistsAndExecutesExactOwnerTakeProfitPrices()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var ownerTargets = new[] { 3996.25m, 3992.75m, 3988.50m };
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      setup: "Golden Fib",
      manualTakeProfits: ownerTargets
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var placed = Assert.Single(
      store.Events,
      item => item.Type == "manual_limit_placed"
    );
    Assert.Equal(ownerTargets, placed.TargetPrices);
    Assert.Equal("Golden Fib", placed.Setup);
    client.FillPendingOrder(Assert.Single(client.PendingOrders).OrderId);
    now = Now.AddSeconds(16);
    await WaitForEventAsync(store, "manual_opened");
    var state = Assert.Single(store.Positions.Values);
    Assert.Equal(ownerTargets, state.TargetPrices);
    Assert.Equal("Golden Fib", state.Setup);

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3996.0m, 3996.25m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.Single(client.Closes);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoPendingExposureAndDuplicateRemainCandidateScoped()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var payload = ManualCandidateJson(
      direction: "BUY",
      manualStopLoss: 3994.0m
    );
    var store = new FakeAutoTradeStore(payload);
    var client = new FakeTradingClient();
    client.PendingOrders.Add(new TradingPendingOrder(
      70,
      Symbol.SymbolId,
      TradeDirection.Sell,
      500,
      4010m,
      "apexvoid-auto",
      "avz|othercand|othergroup|1|500|500|30|1|900"
    ));
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4010.0m, 4010.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Single(client.LimitOrders);
    store.EnqueueCandidate(payload);
    await WaitUntilAsync(() => store.Cursor == "2-0");

    Assert.Single(client.LimitOrders);
    Assert.Equal(2, client.PendingOrders.Count);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoBypassesAutonomousConfluenceAndRegimeGates()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      confluence: 0,
      regime: "chop"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { MinConfluence = 3 },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Single(client.LimitOrders);
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoMissingStopRejectsWithoutKillingSession()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      manualStopLoss: null
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.LimitOrders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.ReasonCode == "invalid manual algo stop contract"
    );
    Assert.False(run.IsCompleted);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task HedgedDemoPlacesManualBuyWhileManualSellRemainsOpen()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(8));
    var now = Now;
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      candidateId: "manual:sell:1",
      direction: "SELL"
    ));
    var client = new FakeTradingClient
    {
      Account = ValidAccount(),
    };
    var engine = new AutoTradeEngine(
      DemoEvalOptions(),
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

    client.FillPendingOrder(Assert.Single(client.PendingOrders).OrderId);
    now = now.AddSeconds(16);
    await WaitUntilAsync(() => store.Positions.Count == 1);
    Assert.Equal(
      TradeDirection.Sell,
      Assert.Single(store.Positions.Values).Direction
    );

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4010.0m, 4010.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    store.EnqueueCandidate(ManualCandidateJson(
      candidateId: "manual:buy:2",
      createdAt: now.ToUnixTimeSeconds(),
      direction: "BUY",
      manualStopLoss: 3994.0m
    ));
    await WaitUntilAsync(() => client.LimitOrders.Count == 2);

    var buyOrder = Assert.Single(
      client.PendingOrders,
      order => order.Direction == TradeDirection.Buy
    );
    client.FillPendingOrder(buyOrder.OrderId);
    now = now.AddSeconds(16);
    await WaitUntilAsync(() => store.Positions.Count == 2);

    Assert.Equal(
      new[] { TradeDirection.Buy, TradeDirection.Sell },
      store.Positions.Values
        .Select(state => state.Direction)
        .OrderBy(direction => direction)
    );
    Assert.Equal(
      2,
      store.Positions.Values.Select(state => state.GroupId).Distinct().Count()
    );
    Assert.All(store.Positions.Values, state =>
      Assert.Equal("manual", state.StrategyFamily)
    );

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
    await WaitForEventAsync(store, "group_result");

    var close = Assert.Single(client.Closes);
    Assert.Equal(positionId, close.PositionId);
    Assert.Equal(600, close.Volume);
    var closed = store.Events.Single(item => item.Type == "manual_closed");
    Assert.Equal(4013.2m, closed.Price);
    Assert.Equal(600, closed.Volume);
    Assert.Equal(0, closed.RemainingVolume);
    Assert.Equal("algo_manual", closed.Stream);
    Assert.NotNull(closed.GroupRealizedPips);
    Assert.Empty(store.Positions);
    Assert.Contains(store.Events, item => item.Type == "group_result");

    // Reconcile must not re-book the same exit with a stop estimate.
    var before = store.Events.Count(item => item.Type == "position_closed");
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    await Task.Delay(50);
    Assert.Equal(
      before,
      store.Events.Count(item => item.Type == "position_closed")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task CloseAllCommandFlattensTrackedPositionsUsingBrokerFillNet()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 50,
      timeframe: "M5"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with
      {
        RangeFlipEnabled = false,
        RangeTargetsPips = [20, 30, 40, 50, 70],
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
    var state = Assert.Single(store.Positions.Values);
    var entry = state.EntryPrice;

    store.EnqueueCommand(JsonSerializer.Serialize(new { type = "close_all" }));
    await WaitForEventAsync(store, "group_result");

    Assert.Single(client.Closes);
    Assert.Empty(store.Positions);
    var closed = Assert.Single(
      store.Events,
      item => item.Type == "position_closed"
    );
    Assert.Equal(4013.2m, closed.Price);
    Assert.Contains("owner flatten", closed.Message);
    var expectedPips = (4013.2m - entry) / 0.1m;
    Assert.Equal(expectedPips, closed.GroupRealizedPips);
    var result = Assert.Single(
      store.Events,
      item => item.Type == "group_result"
    );
    Assert.Equal(expectedPips, result.GroupRealizedPips);
    Assert.Contains(
      store.Events,
      item => item.Type == "owner_flatten" && item.Message.Contains("complete")
    );

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
  public async Task ReconcileDetectedCloseRecordsWarningOnlyCooldownMarker()
  {
    // The engine cannot tell an SL hit from a manual close apart here, so it
    // records evidence but must not claim a confirmed stop loss.
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
    Assert.Equal("reconciliation_unknown", cooldown.Reason);
    Assert.Equal("unconfirmed", cooldown.Confidence);
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
    var store = new FakeAutoTradeStore(BoxCandidateJson(fullTpPips: 50))
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

  [Theory]
  [InlineData("Trend Pullback", "trend")]
  [InlineData("Mapped Zone Reaction", "mapped_zone")]
  [InlineData("Demand Zone Reaction", "supply_demand")]
  [InlineData("Key Level Reaction", "key_level")]
  [InlineData("Session Level Reaction", "session_level")]
  [InlineData("Trendline Reaction", "trendline")]
  [InlineData("Supply Zone Reaction", "supply_demand")]
  public async Task DemoEvalRangeBoxOpensBesideAnotherStrategy(
    string existingSetup,
    string existingFamily
  )
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var first = StrategyMatchCandidateJson(
      setup: existingSetup,
      direction: "BUY",
      targetsPips: [200],
      groupId: $"existing-{existingFamily}",
      strategyFamily: existingFamily
    );
    var store = new FakeAutoTradeStore(first);
    store.SeedPublishedCandidate(new string('s', 64));
    var client = new FakeTradingClient();
    var options = DemoEvalOptions();
    var engine = new AutoTradeEngine(options, store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4007.8m, 4008.0m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    store.EnqueueCandidate(BoxCandidateJson(
      fullTpPips: 50,
      direction: "SELL",
      candidate: 'r',
      structureSwing: 4010.0m,
      groupId: "range-sell",
      strategyFamily: "range"
    ));
    await WaitUntilAsync(() => client.Orders.Count == 2);

    Assert.Contains(client.Orders, item => item.Direction == TradeDirection.Buy);
    Assert.Contains(client.Orders, item => item.Direction == TradeDirection.Sell);
    Assert.Equal(2, store.Positions.Values.Select(item => item.GroupId).Distinct().Count());
    var lifecycle = store.LifecycleEvents
      .Where(item => item.CandidateId == new string('r', 64))
      .Select(item => item.State)
      .ToArray();
    Assert.Contains("executor_received", lifecycle);
    Assert.Contains("routing_selected", lifecycle);
    Assert.Contains("order_planned", lifecycle);
    Assert.Contains("order_submitted", lifecycle);
    Assert.Contains("order_accepted", lifecycle);
    Assert.Contains("order_filled", lifecycle);
    Assert.Contains("managing", lifecycle);
    Assert.Contains(
      "range_box_would_have_awaited_flat",
      store.Metrics
    );
    Assert.Contains(
      "range_box_executed_with_opposite_exposure",
      store.Metrics
    );
    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "rejected"
        && item.Message.Contains("waits for flat")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task DemoEvalTrendBuyAndSupplyReactionSellSurviveRestartIndependently()
  {
    using var firstCts = new CancellationTokenSource(TimeSpan.FromSeconds(8));
    var store = new FakeAutoTradeStore(TrendCandidateJson());
    var client = new FakeTradingClient
    {
      Account = ValidAccount(),
    };
    var options = DemoEvalOptions();
    var engine = new AutoTradeEngine(options, store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      firstCts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, firstCts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    store.EnqueueCandidate(StrategyMatchCandidateJson(
      setup: "Supply Reaction",
      direction: "SELL",
      candidate: 's',
      groupId: "supply-sell",
      strategyFamily: "reaction"
    ));
    await WaitUntilAsync(() => store.Positions.Count == 2);

    var beforeRestart = store.Positions.Values
      .OrderBy(state => state.Direction)
      .Select(state => (
        state.Direction,
        state.GroupId,
        state.Setup,
        state.CurrentStopLoss
      ))
      .ToArray();
    Assert.Equal(2, beforeRestart.Select(item => item.GroupId).Distinct().Count());
    Assert.Contains(beforeRestart, item =>
      item.Direction == TradeDirection.Buy
      && item.Setup == "Trend Pullback"
    );
    Assert.Contains(beforeRestart, item =>
      item.Direction == TradeDirection.Sell
      && item.Setup == "Supply Reaction"
    );

    firstCts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);

    using var restartCts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var restarted = new AutoTradeEngine(options, store, () => Now, _ => { });
    var restartRun = restarted.RunSessionAsync(client, Symbol, restartCts.Token);
    await WaitUntilAsync(() =>
      store.Events.Count(item => item.Type == "ready") >= 2
    );

    var afterRestart = store.Positions.Values
      .OrderBy(state => state.Direction)
      .Select(state => (
        state.Direction,
        state.GroupId,
        state.Setup,
        state.CurrentStopLoss
      ))
      .ToArray();
    Assert.Equal(beforeRestart, afterRestart);
    Assert.Equal(2, client.Orders.Count);

    restartCts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => restartRun);
  }

  [Fact]
  public async Task DemoEvalKeepsBuyAndSellRangeGroupsIndependent()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 50,
      direction: "SELL",
      candidate: 's',
      structureSwing: 4010.0m,
      groupId: "range-sell",
      strategyFamily: "range"
    ));
    var client = new FakeTradingClient();
    var existing = new AutoTradePositionState(
      CandidateId: "range-buy-candidate",
      PositionId: 77,
      SymbolId: Symbol.SymbolId,
      Direction: TradeDirection.Buy,
      EntryPrice: 4000m,
      InitialVolume: 1000,
      RemainingVolume: 1000,
      Slices: [1000],
      TargetsPips: [200],
      NextTargetIndex: 0,
      OpenedAt: 900,
      CurrentStopLoss: 3993.5m,
      TargetOrdinals: [1],
      GroupId: "range-buy",
      GroupOpenedAt: 900,
      LastTrancheBarTs: 900,
      GroupInitialVolume: 1000,
      InitialTrancheVolume: 1000,
      Setup: "Range Box Scalp",
      RangeId: "range-one",
      RangeLow: 4000m,
      RangeHigh: 4008m,
      RangeExitPrice: 4020m,
      StrategyFamily: "range"
    );
    store.Positions[77] = existing;
    client.SeedPosition(new TradingPosition(
      77,
      Symbol.SymbolId,
      TradeDirection.Buy,
      1000,
      4000m,
      3993.5m,
      DemoEvalOptions().Label,
      "av3|buycandidate|range-buy|1|1000|1000|200|1|900"
    ));
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4007.8m, 4008.0m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Contains(store.Positions.Values, item =>
      item.GroupId == "range-buy" && item.Direction == TradeDirection.Buy
    );
    Assert.Contains(store.Positions.Values, item =>
      item.GroupId == "range-sell" && item.Direction == TradeDirection.Sell
    );
    Assert.DoesNotContain(client.Closes, item => item.PositionId == 77);
    Assert.Contains("range_two_sided_simultaneous", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task DemoEvalBuyRangeDoesNotAdoptOrAmendExistingSellGroup()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 50,
      direction: "BUY",
      candidate: 'b',
      structureSwing: 3998.0m,
      groupId: "range-buy",
      strategyFamily: "range"
    ));
    var client = new FakeTradingClient();
    var existing = new AutoTradePositionState(
      CandidateId: "range-sell-candidate",
      PositionId: 77,
      SymbolId: Symbol.SymbolId,
      Direction: TradeDirection.Sell,
      EntryPrice: 4008m,
      InitialVolume: 1000,
      RemainingVolume: 1000,
      Slices: [1000],
      TargetsPips: [200],
      NextTargetIndex: 0,
      OpenedAt: 900,
      CurrentStopLoss: 4014.5m,
      TargetOrdinals: [1],
      GroupId: "range-sell",
      GroupOpenedAt: 900,
      LastTrancheBarTs: 900,
      GroupInitialVolume: 1000,
      InitialTrancheVolume: 1000,
      Setup: "Range Box Scalp",
      RangeId: "range-one",
      RangeLow: 4000m,
      RangeHigh: 4008m,
      RangeExitPrice: 3988m,
      StrategyFamily: "range"
    );
    store.Positions[77] = existing;
    client.SeedPosition(new TradingPosition(
      77,
      Symbol.SymbolId,
      TradeDirection.Sell,
      1000,
      4008m,
      4014.5m,
      DemoEvalOptions().Label,
      "av3|sellcandid|range-sell|1|1000|1000|200|1|900"
    ));
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Contains(store.Positions.Values, item =>
      item.GroupId == "range-sell"
      && item.Direction == TradeDirection.Sell
      && item.CurrentStopLoss == 4014.5m
    );
    Assert.Contains(store.Positions.Values, item =>
      item.GroupId == "range-buy"
      && item.Direction == TradeDirection.Buy
    );
    Assert.DoesNotContain(client.StopAmendments, item => item.PositionId == 77);
    Assert.DoesNotContain(client.Closes, item => item.PositionId == 77);
    Assert.Contains("range_two_sided_simultaneous", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task DemoEvalPendingOrderDedupIsScopedToCandidateGroup()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 50,
      direction: "SELL",
      candidate: 'p',
      structureSwing: 4010.0m,
      groupId: "range-sell",
      strategyFamily: "range"
    ));
    var client = new FakeTradingClient();
    client.PendingOrders.Add(new TradingPendingOrder(
      55,
      Symbol.SymbolId,
      TradeDirection.Buy,
      500,
      3995m,
      DemoEvalOptions().Label,
      "avz|othercandi|othergroup|2|500|500|30|1|900"
    ));
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4007.8m, 4008.0m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Single(client.Orders);
    Assert.Contains(client.PendingOrders, item => item.OrderId == 55);
    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "rejected"
        && item.Message.Contains("planned zone fill is still pending")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task DemoEvalDuplicateCandidateRemainsIdempotent()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var payload = BoxCandidateJson(
      fullTpPips: 50,
      direction: "BUY",
      candidate: 'd',
      groupId: "range-buy",
      strategyFamily: "range"
    );
    var store = new FakeAutoTradeStore(payload);
    store.EnqueueCandidate(payload);
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitUntilAsync(() => store.Cursor == "2-0");

    Assert.Single(client.Orders);
    Assert.Contains("duplicate_suppressed", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MappedReactionDuplicateCandidateDoesNotSubmitSecondOrder()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    const string reaction = "reaction-shared-abc";
    var first = StrategyMatchCandidateJson(
      setup: "Mapped Zone Reaction",
      candidate: 'm',
      groupId: "mapped-group-1",
      strategyFamily: "mapped_zone",
      reactionId: reaction,
      thesisId: "thesis-1",
      zoneId: "zone-shared"
    );
    var second = StrategyMatchCandidateJson(
      setup: "Mapped Zone Reaction",
      candidate: 'n',
      groupId: "mapped-group-1",
      strategyFamily: "mapped_zone",
      reactionId: reaction,
      thesisId: "thesis-1",
      zoneId: "zone-shared"
    );
    var store = new FakeAutoTradeStore(first);
    store.Values[$"auto_trade:reaction_claim:{reaction}"] =
      "{\"candidate_id\":\"" + new string('m', 64)
      + "\",\"state\":\"claimed\",\"reaction_id\":\"" + reaction + "\"}";
    store.EnqueueCandidate(second);
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitUntilAsync(() => store.Cursor == "2-0");

    Assert.Single(client.Orders);
    Assert.Contains("executor_duplicate_reaction_rejected", store.Metrics);
    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "rejected"
        && item.Message.Contains("duplicate_reaction_active")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MappedThesisDuplicateDifferentReactionDoesNotSubmitSecondOrder()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    const string thesis = "thesis-shared-xyz";
    var first = StrategyMatchCandidateJson(
      setup: "Mapped Zone Reaction",
      candidate: 'p',
      groupId: "mapped-group-a",
      strategyFamily: "mapped_zone",
      reactionId: "reaction-a",
      thesisId: thesis,
      zoneId: "zone-shared"
    );
    var second = StrategyMatchCandidateJson(
      setup: "Mapped Zone Reaction",
      candidate: 'q',
      groupId: "mapped-group-b",
      strategyFamily: "mapped_zone",
      reactionId: "reaction-b",
      thesisId: thesis,
      zoneId: "zone-shared"
    );
    var store = new FakeAutoTradeStore(first);
    store.Values[$"auto_trade:thesis_claim:{thesis}"] =
      "{\"candidate_id\":\"" + new string('p', 64)
      + "\",\"state\":\"managing\",\"thesis_id\":\"" + thesis
      + "\",\"rearm_ready\":false}";
    store.EnqueueCandidate(second);
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitUntilAsync(() => store.Cursor == "2-0");

    Assert.Single(client.Orders);
    Assert.Contains("executor_duplicate_thesis_rejected", store.Metrics);
    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "rejected"
        && item.Message.Contains("active_thesis_group")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task DemoEvalLiveAccountPublishesFatalAndNeverOrders()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(2));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { IsLive = true },
    };
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );

    var error = await Assert.ThrowsAsync<AutoTradeConfigurationException>(
      () => engine.RunSessionAsync(client, Symbol, cts.Token)
    );

    Assert.Contains("live account", error.Message);
    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "config_fatal"
      && item.AccountType == client.Account.AccountType
      && item.Broker == client.Account.BrokerName
    );
  }

  [Fact]
  public async Task DemoEvalFatalContractMismatchStopsBeforeAnyOrder()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(2));
    var store = new FakeAutoTradeStore(CandidateJson());
    store.Values[AutoTradeConfigHealth.PythonManifestKey] =
      """
      {
        "candidate_stream":"different:candidates",
        "redis_database":0,
        "redis_fingerprint":"different",
        "canonical_symbol":"XAU",
        "pip_size":0.1,
        "candidate_contract_version":4,
        "target_plans":[30,60,90,120,200],
        "range_target_plans":[20,30,40,50,70]
      }
      """;
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );

    var error = await Assert.ThrowsAsync<AutoTradeConfigurationException>(
      () => engine.RunSessionAsync(client, Symbol, cts.Token)
    );

    Assert.Contains("configuration mismatch", error.Message);
    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item => item.Type == "config_fatal");
    Assert.Contains("config_mismatch", store.Metrics);
  }

  [Fact]
  public async Task WarningOnlyConfigurationPublishesReadyExecutor()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var options = DemoEvalOptions() with
    {
      CandidateMaxAgeSeconds = 420,
      CandidateStorageTtlSeconds = 604800,
      Symbols = ["XAU"],
    };
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { AccountType = "Netted" },
    };
    var manifest = AutoTradeConfigHealth.Build(
      options,
      client.Account,
      Symbol,
      Now.ToUnixTimeSeconds()
    );
    store.Values[AutoTradeConfigHealth.PythonManifestKey] =
      JsonSerializer.Serialize(
        manifest,
        new JsonSerializerOptions
        {
          PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        }
      );
    var engine = new AutoTradeEngine(options, store, () => Now, _ => { });

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "ready");

    var readiness = JsonDocument.Parse(
      store.Values[AutoTradeConfigHealth.ReadinessKey]
    ).RootElement;
    Assert.True(readiness.GetProperty("ready").GetBoolean());
    Assert.Equal("ready", readiness.GetProperty("state").GetString());
    Assert.Contains(
      readiness.GetProperty("warnings").EnumerateArray(),
      item => item.GetString() == "broker_non_hedged"
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
    ExpectedBroker: "fpmarkets",
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
    Label: "apexvoid-auto",
    ManualAlgoEnabled: true
  );

  private static AutoTradeOptions DemoEvalOptions() => Options() with
  {
    Profile = "demo_eval",
    RequireDemoAccount = true,
    AllowConcurrentStrategies = true,
    AllowHedgedXau = true,
    RequireFlatForRange = false,
    RangeTwoSidedEnabled = true,
    RangeFlipEnabled = true,
    MultiMatchEnabled = true,
    TrackAllStructuralMatches = true,
    TrendEnabled = true,
    RangeEnabled = true,
    MappedZoneEnabled = true,
    StrategyMatchEnabled = true,
    BreakoutEnabled = true,
    RetestEnabled = true,
    ReactionEnabled = true,
    LiquidityReversalEnabled = true,
    AllowCounterBias = true,
  };

  private static TradingAccountSnapshot ValidAccount() => new(
    123,
    IsLive: false,
    PermissionScope: "ScopeTrade",
    AccessRights: "FullAccess",
    AccountType: "Hedged",
    BrokerName: "FP Markets",
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
    string? regime = null,
    string? parentGroupId = null
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
    parent_group_id = parentGroupId,
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
    string regime = "trend",
    string? parentGroupId = null
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
    displacement_direction = direction == "BUY" ? "up" : "down",
    displacement_age_bars = 1,
    bos_direction = direction == "BUY" ? "up" : "down",
    bos_ts = 1_000,
    opposing_level_distance_atr = 2.0,
    regime,
    parent_group_id = parentGroupId,
  });

  // BUY-only: FakeTradingClient.ClosePositionAsync always fills TP legs at
  // a hardcoded 4013.2 (see the class below), which is only a profitable
  // close relative to a ~4000 BUY entry - a SELL scenario would book a
  // loss on "TP1" and never reach a profitable, breakeven group the shared
  // invariants require. Direction-specific pullback math (SELL retrace
  // ratios, zone sides) is already covered independently at the
  // ScaleInTriggerPlanner level (SellIsMirrored, ValidPullback's SELL
  // shape) - this helper only needs to prove the AutoTradeEngine wiring.
  private static string PullbackAddCandidateJson(
    char candidate = 'b',
    long barTs = 1_180,
    decimal structureSwing = 4007.5m,
    decimal atr = 1.0m,
    decimal entryLow = 4007.5m,
    decimal entryHigh = 4008.5m,
    int[]? targetsPips = null,
    decimal? opposingZoneLow = 4007.5m,
    decimal? opposingZoneHigh = 4008.5m,
    string? addZoneSide = "demand",
    long? counterBosTs = null,
    decimal? extremePrice = 4015.0m,
    long? extremeTs = 1_100,
    bool rejectionConfirmed = true
  ) => JsonSerializer.Serialize(new
  {
    version = 3,
    candidate_id = new string(candidate, 64),
    symbol = "XAU",
    timeframe = "M1",
    setup = "Trend Pullback",
    mode = "auto_trend_pullback",
    direction = "BUY",
    trigger_ts = "1000",
    created_at = 1_000,
    spot_ts = 1_000,
    current_price = 4008.0,
    key_level = 4008.0,
    entry_zone = new { low = entryLow, high = entryHigh },
    confluence = 2,
    reasons = new[] { "pullback retrace into demand" },
    bar_ts = barTs,
    atr,
    structure_swing = structureSwing,
    targets_pips = targetsPips ?? new[] { 30, 60, 90 },
    regime = "trend",
    parent_group_id = new string('a', 10),
    opposing_zone_low = opposingZoneLow,
    opposing_zone_high = opposingZoneHigh,
    add_zone_side = addZoneSide,
    counter_bos_ts = counterBosTs,
    extreme_price = extremePrice,
    extreme_ts = extremeTs,
    rejection_confirmed = rejectionConfirmed,
  });

  private static string StrategyMatchCandidateJson(
    string setup = "Liquidity Sweep",
    string direction = "BUY",
    char candidate = 's',
    int[]? targetsPips = null,
    string? groupId = null,
    string? strategyFamily = null,
    string? reactionId = null,
    string? thesisId = null,
    string? zoneId = null,
    string? structuralSource = null,
    decimal? structuralZoneLow = null,
    decimal? structuralZoneHigh = null
  ) => JsonSerializer.Serialize(new
  {
    version = 4,
    candidate_id = new string(candidate, 64),
    match_id = new string(candidate, 64),
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
    group_id = groupId,
    strategy_family = strategyFamily,
    reaction_id = reactionId,
    thesis_id = thesisId,
    zone_id = zoneId,
    structural_source = structuralSource,
    structural_zone_id = zoneId,
    structural_zone_low = structuralZoneLow,
    structural_zone_high = structuralZoneHigh,
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
    int? confluence = null,
    string? groupId = null,
    string? strategyFamily = null,
    decimal atr = 1.0m
  )
  {
    // Range height must cover Full TP distance when flip is disabled.
    var rangeLow = 4000.0m;
    var rangeHigh = Math.Max(4008.0m, rangeLow + fullTpPips * 0.1m + 1.0m);
    var keyLevel = direction == "BUY" ? rangeLow : rangeHigh;
    return JsonSerializer.Serialize(new
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
    current_price = direction == "BUY" ? 4000.1 : (double)(rangeHigh - 0.2m),
    key_level = keyLevel,
    entry_zone = direction == "BUY"
      ? new { low = 3999.5m, high = 4000.5m }
      : new { low = rangeHigh - 0.2m, high = rangeHigh + 0.2m },
    confluence = confluence ?? 2,
    reasons = new[] { "M1 range rejection", $"full TP {fullTpPips} pips" },
    bar_ts = 1_000,
    atr,
    structure_swing = structureSwing
      ?? (direction == "BUY" ? 3998.0m : rangeHigh + 1.5m),
    range_id = "xau-8000-8016",
    range_low = rangeLow,
    range_high = rangeHigh,
    full_take_profit_pips = fullTpPips,
    regime,
    opposing_zone_low = opposingZoneLow,
    opposing_zone_high = opposingZoneHigh,
    sweep_low = sweepLow,
    sweep_high = sweepHigh,
    group_id = groupId,
    strategy_family = strategyFamily,
  });
  }

  // Mirrors telegram-bot's manual_execution._intent_to_candidate_payload:
  // no atr/structure_swing at all (the manual-algo path must never need
  // them), manual_stop_loss/manual_expires_at/targets_pips instead.
  private static string ManualCandidateJson(
    string direction = "SELL",
    string candidateId = "manual:1:0",
    long createdAt = 1_000,
    decimal entryLow = 3999.5m,
    decimal entryHigh = 4000.5m,
    decimal? manualStopLoss = 4006.0m,
    string setup = "Manual Algo",
    int[]? targetsPips = null,
    decimal[]? manualTakeProfits = null,
    long? expiresAt = null,
    int confluence = 1,
    string? regime = null,
    decimal? opposingZoneLow = null,
    decimal? opposingZoneHigh = null
  ) => JsonSerializer.Serialize(new
  {
    version = 3,
    candidate_id = candidateId,
    symbol = "XAU",
    timeframe = "M1",
    setup,
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
    manual_take_profits = manualTakeProfits ?? (
      direction == "BUY"
        ? new[] { entryHigh + 3m, entryHigh + 6m, entryHigh + 9m }
        : new[] { entryLow - 3m, entryLow - 6m, entryLow - 9m }
    ),
    regime,
    group_id = candidateId,
    strategy_family = "manual",
    trigger_id = $"manual:{createdAt}",
    structural_source = "owner_instruction",
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
    public List<AutoTradeEvent> LifecycleEvents { get; } = [];
    public Dictionary<string, string> Values { get; } = [];
    public List<(string RangeId, string Direction, string State)> RangeSides
      { get; } = [];
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

    public void SeedPublishedCandidate(string candidateId) =>
      _candidateStatus[candidateId] = "published";

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
      if (
        _candidateStatus.TryGetValue(candidateId, out var current)
        && !string.Equals(current, "published", StringComparison.Ordinal)
      )
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
    public List<(string Symbol, string Mode, string Condition)> AddRejects { get; } = [];
    public Task IncrementAddRejectAsync(
      string symbol,
      string mode,
      string condition,
      CancellationToken cancellationToken
    )
    {
      AddRejects.Add((symbol, mode, condition));
      return Task.CompletedTask;
    }
    public List<ZoneCooldownRecord> ZoneCooldowns { get; } = [];
    public List<string> Metrics { get; } = [];
    public List<(string Symbol, string Direction)> ZoneCooldownDirections { get; } = [];
    public Task RecordZoneCooldownAsync(
      string symbol,
      string direction,
      ZoneCooldownRecord record,
      int ttlMinutes,
      CancellationToken cancellationToken
    )
    {
      ZoneCooldowns.Add(record);
      ZoneCooldownDirections.Add((symbol, direction));
      return Task.CompletedTask;
    }
    public Task IncrementMetricAsync(
      string symbol,
      string metric,
      CancellationToken cancellationToken
    )
    {
      Metrics.Add(metric);
      return Task.CompletedTask;
    }
    public Task SetValueAsync(
      string key,
      string value,
      CancellationToken cancellationToken
    )
    {
      Values[key] = value;
      return Task.CompletedTask;
    }
    public Task<string?> GetValueAsync(
      string key,
      CancellationToken cancellationToken
    ) => Task.FromResult(
      Values.TryGetValue(key, out var value) ? value : null
    );
    public Task RecordLifecycleEventAsync(
      AutoTradeEvent tradeEvent,
      CancellationToken cancellationToken
    )
    {
      LifecycleEvents.Add(tradeEvent);
      var owner = tradeEvent.CandidateId
        ?? tradeEvent.GroupId
        ?? tradeEvent.CorrelationId
        ?? "service";
      Values[$"auto_trade:lifecycle_state:{owner}"] =
        tradeEvent.State ?? "managing";
      return Task.CompletedTask;
    }
    public Task UpdateRangeSideStateAsync(
      string symbol,
      string rangeId,
      string direction,
      string state,
      string? candidateId,
      long? positionId,
      IReadOnlyList<long>? pendingOrderIds,
      CancellationToken cancellationToken
    )
    {
      RangeSides.Add((rangeId, direction, state));
      return Task.CompletedTask;
    }
  }
}

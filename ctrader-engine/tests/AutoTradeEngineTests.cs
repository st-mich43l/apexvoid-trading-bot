using System.Collections;
using System.Runtime.CompilerServices;
using System.Text.Json;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed partial class AutoTradeEngineTests
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

  // Owner-reported 2026-08-19 (multi-symbol scale-up): a real 5-digit FX
  // symbol, distinct from XAU's Digits=2 - proves manual /algo resolves
  // the candidate's own instrument (ResolveBoundSymbol/ResolveInstrumentUnits)
  // instead of RequireSymbol()'s single session-bound symbol.
  private static readonly SymbolInfo EurUsdSymbol = new(
    "EURUSD",
    "EURUSD",
    1,
    Digits: 5,
    PipPosition: 4,
    MinVolume: 1_000,
    StepVolume: 1_000,
    MaxVolume: 10_000_000,
    LotSize: 100_000
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
      "sizing: mode=min balance=2000.00 → table 0.15 lots · risk 0.07 lots",
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
    // TP3 now trails to TP2 (4006.2) instead of a no-op landing back on
    // TP1, and TP4 advances to TP3 (4009.2) instead of TP2 - each rung
    // trails to the one immediately preceding it.
    Assert.Equal(
      new decimal[] { 3993.7m, 4000.26m, 4003.2m, 4006.2m, 4009.2m },
      client.StopAmendments.Select(item => item.StopLoss)
    );
    Assert.Equal(4, store.Events.Count(item => item.Type == "stop_moved"));
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
      "sizing: mode=table balance=2072.02 → table 0.15 lots · risk 0.07 lots",
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
    var flipStatus = await store.GetCandidateStatusAsync(
      "flip:XAU:xau-8000-8016", cts.Token
    );
    Assert.True(
      string.IsNullOrWhiteSpace(flipStatus)
      || !flipStatus.StartsWith("flip_pending:", StringComparison.Ordinal)
    );

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
    // Seeded directly: an unfenced completion with no lease token is rejected
    // by the store, so the flip rendezvous marker is placed as fixture state.
    store.SeedCandidateState(
      "flip:XAU:xau-8000-8016",
      "flip_pending:BUY:1030"
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
    Assert.Equal(
      "rejected:flip_released",
      await store.GetCandidateStatusAsync("flip:XAU:xau-8000-8016", cts.Token)
    );

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
  public async Task V8OnlyModeRejectsNewAutonomousV6CandidatesButNotManualAlgo()
  {
    // Section L of the TradePlan V8 cutover: v8_only must reject new
    // autonomous V6 candidates as defense-in-depth even if Python is also
    // supposed to have stopped publishing them - and must not touch manual
    // /algo candidates, which are the owner's direct decision.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { ContractMode = "v8_only" }, store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Processed.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Empty(client.Orders);
    Assert.Contains(
      store.Events,
      item => item.Type == "rejected"
        && item.Message.Contains("legacy_candidate_disabled_in_v8_only")
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
    // The group-wide TP1 protection attempt is the injected failure. The
    // booking leg then applies its own TP1 trail, so the engine must recover
    // to protected BE before continuing to TP1 on the next target. TP3 then
    // trails to TP2 (one behind) instead of a TP1 no-op.
    Assert.Equal(
      new decimal[] { 3993.7m, 4000.26m, 4003.2m, 4006.2m },
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
    // 2026-08 R:R dig: TP2 used to move the stop nowhere at all - it now
    // trails to TP1's own price (entry 4000.2 + 30 pips = 4003.2), closing
    // the gap that used to leave the position flat at breakeven all the
    // way through to TP3.
    Assert.Equal((91, 4003.2m), Assert.Single(client.StopAmendments));
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
    Assert.Equal(300, client.Orders[1].Volume);
    Assert.Equal((92, 3999.4m), client.StopAmendments.Last());
    var add = Assert.Single(store.Events, item => item.Type == "add");
    Assert.Equal(2, add.TrancheIndex);
    Assert.Equal("aaaaaaaaaa", add.GroupId);
    Assert.Contains("size-ratio-bound", add.Message);

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
  public async Task ShallowLegTakeProfitReportsPipsFromTheGroupsDeepestFill()
  {
    // Owner-reported 2026-09-10 (real XAU BUY, signal 300): a manual /algo
    // group's shallow leg (entry 4008.0, worse price) hit its own TP1
    // (4011.0) while the group's deep leg (entry 4006.25, already filled,
    // still open) sat untouched. The channel card reported the shallow
    // leg's own entry-to-target distance (+30 pips) - correct for that one
    // clip in isolation, but the owner reads the whole zone as one trade
    // and expects the group's single best (deepest) fill as the reference,
    // not whichever tranche happens to be booking this specific event.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    client.SeedPosition(new TradingPosition(
      91, 7, TradeDirection.Buy, 1000, 4008.0m, 4002.0m,
      "apexvoid-auto", "av3|manual300shal|manual300|1|1000|1000|30|1|1000"
    ));
    client.SeedPosition(new TradingPosition(
      92, 7, TradeDirection.Buy, 200, 4006.25m, 4002.0m,
      "apexvoid-auto", "av3|manual300deep|manual300|2|200|200|120|1|1000"
    ));
    client.CloseExecutionPriceToReturn = 4011.5m;
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "ready");

    // Bid crosses the shallow leg's own TP1 (4011.0) but stays far below
    // the deep leg's own target (4006.25 + 12.0 = 4018.25) - only the
    // shallow leg's take_profit fires.
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4011.5m, 4011.7m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var takeProfit = Assert.Single(
      store.Events, item => item.Type == "take_profit"
    );
    Assert.Equal(91, takeProfit.PositionId);
    // Deepest group fill (4006.25), not this leg's own entry (4008.0).
    Assert.Equal(4006.25m, takeProfit.LegEntryPrice);
    Assert.Equal(52.5m, takeProfit.LegRealizedPips);

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

  // Balance shifted 900->550 (commit 7b7129af raised LotsForEquity(900)
  // 0.06->0.10, which sits above ZoneFillMinLots=0.09 and never falls back
  // any more). 550 lands in the un-touched sub-600 band (0.04 lots),
  // reproducing the below-minimum scenario this test means to exercise.
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
      Account = ValidAccount() with { Balance = 550m },
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
    Assert.Equal(400, order.Volume);
    Assert.Empty(client.LimitOrders);
    Assert.Contains(
      logs,
      message => message.Contains(
        "zone-fill skipped: 0.04 lots below 0.09 minimum"
      )
    );
    Assert.Contains(
      store.Events,
      item => item.Type == "opened"
        && item.Message.Contains(
          "zone-fill skipped: 0.04 lots below 0.09 minimum"
        )
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task StrategyPolicyLimitRequiresZoneFillCapability()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      orderTypePreference: "limit",
      entryDistribution: "zone_split"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { ZoneFillEnabled = false },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, 1_000),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Contains(
      store.Events,
      item => item.Type == "rejected"
        && item.Message.Contains("requires unavailable zone_split")
    );
    Assert.Empty(client.Orders);
    Assert.Empty(client.LimitOrders);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task StrategyPolicyLimitUsesZoneFillWhenCapable()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      orderTypePreference: "limit",
      entryDistribution: "zone_split",
      entryLow: 3999.0m,
      entryHigh: 4000.5m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { ZoneFillEnabled = true, SizingMode = "table" },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.4m, 4000.6m, 1_000),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "zone_planned");

    Assert.Equal(2, client.LimitOrders.Count);
    Assert.Empty(client.Orders);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task NarrowPolicyLimitUsesOnePendingOrderWithoutZoneSplit()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      setup: "Trend Pullback",
      orderTypePreference: "limit",
      entryDistribution: "single",
      entryLow: 3999.8m,
      entryHigh: 4000.0m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { ZoneFillEnabled = true },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, 1_000),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "order_accepted");

    var limit = Assert.Single(client.LimitOrders);
    Assert.Equal(4000.0m, limit.LimitPrice);
    Assert.Empty(client.Orders);
    Assert.DoesNotContain(store.Events, item => item.Type == "zone_planned");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task PausedSingleLimitLeavesNoGroupPlan()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      setup: "Trend Pullback",
      orderTypePreference: "limit",
      entryDistribution: "single",
      entryLow: 3999.8m,
      entryHigh: 4000.0m
    )) { Paused = true };
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000m, 4000.2m, 1_000), cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.DoesNotContain(
      store.Values.Keys,
      key => key.StartsWith("auto_trade:group_plan:")
    );
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ExposureRecheckFailureLeavesNoGroupPlan()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      setup: "Trend Pullback",
      orderTypePreference: "limit",
      entryDistribution: "single",
      entryLow: 3999.8m,
      entryHigh: 4000.0m
    ));
    var client = new FakeTradingClient();
    client.SeedPosition(new TradingPosition(
      999, Symbol.SymbolId, TradeDirection.Buy, 100, 3990m, 3980m,
      "external", "external"
    ));
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000m, 4000.2m, 1_000), cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.DoesNotContain(
      store.Values.Keys,
      key => key.StartsWith("auto_trade:group_plan:")
    );
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task DryRunSingleLimitLeavesNoGroupPlan()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      setup: "Trend Pullback",
      orderTypePreference: "limit",
      entryDistribution: "single",
      entryLow: 3999.8m,
      entryHigh: 4000.0m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { DryRun = true }, store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000m, 4000.2m, 1_000), cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "dry_run");

    Assert.DoesNotContain(
      store.Values.Keys,
      key => key.StartsWith("auto_trade:group_plan:")
    );
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task AcceptedPendingOrderRetainsExpiringGroupPlan()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      setup: "Trend Pullback",
      orderTypePreference: "limit",
      entryDistribution: "single",
      entryLow: 3999.8m,
      entryHigh: 4000.0m
    ));
    var client = new FakeTradingClient();
    var options = Options() with { CandidateStorageTtlSeconds = 900 };
    var engine = new AutoTradeEngine(options, store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000m, 4000.2m, 1_000), cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "order_accepted");

    var planKey = Assert.Single(
      store.Values.Keys,
      key => key.StartsWith("auto_trade:group_plan:")
    );
    Assert.Equal(TimeSpan.FromSeconds(900), store.ValueTtls[planKey]);
    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task PendingFillReconstructsAfterRestartAndTerminalTpDeletesPlan()
  {
    using var firstCts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      setup: "Trend Pullback",
      orderTypePreference: "limit",
      entryDistribution: "single",
      entryLow: 3999.8m,
      entryHigh: 4000.0m
    ));
    var client = new FakeTradingClient();
    var firstEngine = new AutoTradeEngine(
      Options(), store, () => Now, _ => { }
    );
    await firstEngine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000m, 4000.2m, 1_000), firstCts.Token
    );

    var firstRun = firstEngine.RunSessionAsync(
      client, Symbol, firstCts.Token
    );
    await WaitForEventAsync(store, "order_accepted");
    await store.CursorAdvanced.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var planKey = Assert.Single(
      store.Values.Keys,
      key => key.StartsWith("auto_trade:group_plan:")
    );
    firstCts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => firstRun);

    client.FillPendingOrder(Assert.Single(client.PendingOrders).OrderId);
    using var secondCts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var secondEngine = new AutoTradeEngine(
      Options(), store, () => Now.AddSeconds(20), _ => { }
    );
    await secondEngine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000m, 4000.2m, 1_020), secondCts.Token
    );
    var secondRun = secondEngine.RunSessionAsync(
      client, Symbol, secondCts.Token
    );
    await WaitUntilAsync(() => store.Positions.Count == 1);

    Assert.True(store.Values.ContainsKey(planKey));
    await secondEngine.ObserveSpotAsync(
      new SpotPrice("XAU", 4100m, 4100.2m, 1_020), secondCts.Token
    );
    await WaitForEventAsync(store, "group_result");
    Assert.False(store.Values.ContainsKey(planKey));

    secondCts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => secondRun);
  }

  [Fact]
  public async Task PartialZoneFillFailureKeepsGroupPlanUntilBrokerAbsenceIsConfirmed()
  {
    // A failed leg does not prove the request never reached the broker, so the
    // candidate becomes recovery-required and the group plan survives: it is
    // the only map from deterministic client order IDs back to this candidate.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      orderTypePreference: "limit",
      entryDistribution: "zone_split",
      entryLow: 3999.0m,
      entryHigh: 4000.5m
    ));
    var client = new FakeTradingClient { FailLimitOrderCall = 2 };
    var engine = new AutoTradeEngine(
      Options() with { ZoneFillEnabled = true, SizingMode = "table" },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.4m, 4000.6m, 1_000), cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "broker_outcome_unknown");

    // Rollback is evidence-based: only the leg the broker confirmed is
    // cancelled, never the ambiguous one.
    Assert.Single(client.CancelledOrders);
    Assert.Contains(
      store.Values.Keys,
      key => key.StartsWith("auto_trade:group_plan:")
    );
    Assert.Equal(
      CandidateExecutionStates.BrokerOutcomeUnknown,
      store.CandidateState(new string('s', 64))
    );
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");

    // Recovery proves the broker holds nothing for the candidate, which is the
    // only point at which the plan may be dropped and a retry becomes safe.
    await WaitUntilAsync(() => client.LimitOrders.Count == 3);
    Assert.Contains("broker_outcome_confirmed_absent", store.Metrics);
    Assert.Contains("candidate_retry_reclaimed", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RequiredMarketOrderNeverRoutesThroughZoneFill()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      orderTypePreference: "market",
      entryDistribution: "single",
      entryLow: 3999.0m,
      entryHigh: 4000.5m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { ZoneFillEnabled = true },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, 1_000),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Single(client.Orders);
    Assert.Empty(client.LimitOrders);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Theory]
  [InlineData(1.0, 600)]
  [InlineData(0.5, 300)]
  [InlineData(0.375, 200)]
  public async Task CandidateRiskMultiplierScalesAndNormalizesInitialVolume(
    double multiplier,
    long expectedVolume
  )
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson(
      riskMultiplier: (decimal)multiplier
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, 1_000),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Equal(expectedVolume, Assert.Single(client.Orders).Volume);
    Assert.Contains(
      store.Events,
      item => item.Type == "opened"
        && item.RiskMultiplier == (decimal)multiplier
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MissingAutonomousRiskMultiplierIsRejectedExplicitly()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson(riskMultiplier: null));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Contains(
      store.Events,
      item => item.Type == "rejected"
        && item.Message.Contains("invalid autonomous risk_multiplier")
    );
    Assert.Empty(client.Orders);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public void RiskMultiplierSurvivesSnakeCaseJsonDeserialization()
  {
    var candidate = JsonSerializer.Deserialize(
      StrategyMatchCandidateJson(riskMultiplier: 0.375m),
      RedisJsonContext.Default.TradeCandidate
    );

    Assert.NotNull(candidate);
    Assert.Equal(0.375m, candidate.RiskMultiplier);
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
  [InlineData("Zone Reaction", "supply_demand", "supply_demand")]
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
  public async Task FillRelativeTargetsAnchorToBrokerFill()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      targetsPips: [30, 90],
      targetModel: "fill_relative"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, 1_000),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var state = Assert.Single(store.Positions.Values);
    Assert.Equal(4000.2m, state.EntryPrice);
    Assert.Equal(new decimal[] { 4003.2m, 4009.2m }, state.TargetPrices);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task AbsoluteTargetUsesStructuralPrice()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      targetsPips: [60],
      targetModel: "absolute",
      absoluteTargetPrice: 4006.5m,
      // ~40p structure stop so absolute 63p reward clears min RR.
      structureSwing: 3996.5m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, 1_000),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Equal(
      new decimal[] { 4006.5m },
      Assert.Single(store.Positions.Values).TargetPrices
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task HybridTargetsNeverCrossStructuralCap()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(StrategyMatchCandidateJson(
      targetsPips: [30, 60, 90],
      targetModel: "hybrid",
      // Cap must still clear min RR against the 40p trend stop floor.
      absoluteTargetPrice: 4004.8m,
      structureSwing: 3996.5m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, 1_000),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Equal(
      new decimal[] { 4003.2m, 4004.8m, 4004.8m },
      Assert.Single(store.Positions.Values).TargetPrices
    );

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
    // at 4013.2) and moving the stop to protected breakeven, satisfying the
    // shared "initial reached TP1/breakeven" and "group profitable" invariants.
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
    // P5 stop far past the 60p trend envelope - must reject, not clamp the
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
    // structureSwing far below entry pushes the P5 stop to ~58p - inside
    // the 60p trend envelope, but wide enough that this tranche's own risk
    // outweighs the booked-profit buffer.
    store.EnqueueCandidate(PullbackAddCandidateJson(
      structureSwing: 3999.3m,
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
      item.Type == "rejected"
      && item.Message.Contains("stop_inside_opposing_zone")
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
      item.Type == "rejected"
      && item.Message.Contains("stop_inside_opposing_zone")
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
  public async Task CandidateStopContractMismatchRejectsBeforeBrokerSubmission()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(TrendCandidateJson(
      stopContract: true,
      stopMismatch: true
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Empty(client.LimitOrders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("protective_stop_contract_mismatch")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoDerivesDeepLegAtMidpointOfEntryAndStopWhenZoneIsDegenerate()
  {
    // 2026-08 R:R redesign, confirmed directly against the owner's own
    // worked example: BUY 4390 (typed as a single price, so entryLow ==
    // entryHigh - no real zone to split) with SL 4384 -> Deep 4387 (exactly
    // the midpoint between the typed entry and the stop). 2026-09-08: the
    // former Mid leg is gone (2-leg 80/20 ladder); the third order is now
    // the fixed-size risk leg 10 pips from the stop (4384 + 1.0 = 4385.0).
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "BUY",
      entryLow: 4390.0m,
      entryHigh: 4390.0m,
      manualStopLoss: 4384.0m,
      manualTakeProfits: [4396.0m, 4399.0m, 4403.0m],
      manualSingleEntry: false
    ));
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { Balance = 50_000m },
    };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4380.0m, 4380.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Equal(3, client.LimitOrders.Count);
    Assert.Equal(
      new[] { 4390.0m, 4387.0m, 4385.0m },
      client.LimitOrders.Select(order => order.LimitPrice)
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
      opposingZoneHigh: 4003.0m,
      manualSingleEntry: false
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Equal(3, client.LimitOrders.Count);
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");
    // 2026-09-08: 2-leg 80/20 ladder (Shallow, Deep) plus the fixed-size
    // risk leg 10 pips from the stop (4002.0 - 1.0 = 4001.0, between Deep
    // and the stop - deeper/closer to invalidation than Deep itself).
    // 2026-09-09: Deep rests at the zone's own midpoint instead of its far
    // edge (zone 3999.5-4000.5 -> Deep 4000.0).
    Assert.Equal(
      new[] { 3999.5m, 4000.0m, 4001.0m },
      client.LimitOrders.Select(order => order.LimitPrice)
    );
    // SELL shallow/deep/risk vs absolute SL 4002.0 — each leg's relative
    // distance must resolve to that one Shallow-derived absolute price.
    Assert.Equal(
      new long[] { 250_000, 200_000, 100_000 },
      client.LimitOrders.Select(order => order.RelativeStopLoss).ToArray()
    );
    Assert.True(client.LimitOrders.Sum(order => order.Volume) > 0);
    Assert.DoesNotContain(store.Events, item => item.Type == "warning");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoBuyLegsShareAbsoluteStopFromShallowEntry()
  {
    // Owner: "buy 4353-50 SL for all orders must be 4347" / live #104-105.
    // Shallow is zone.High; Deep/risk relative distances differ but every
    // RelativeStopLoss must resolve to the same absolute VIP stop.
    // 2026-09-08: 2-leg 80/20 ladder (Shallow 4353, Deep 4350) plus the
    // fixed-size risk leg 10 pips from the stop (4347 + 1.0 = 4348.0).
    // 2026-09-09: Deep rests at the zone's own midpoint instead of its far
    // edge (zone 4350-4353 -> Deep 4351.5).
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "BUY",
      entryLow: 4350.0m,
      entryHigh: 4353.0m,
      manualStopLoss: 4347.0m,
      manualSingleEntry: false
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4360.0m, 4360.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Equal(3, client.LimitOrders.Count);
    Assert.Equal(
      new[] { 4353.0m, 4351.5m, 4348.0m },
      client.LimitOrders.Select(order => order.LimitPrice)
    );
    Assert.Equal(
      new long[] { 600_000, 450_000, 100_000 },
      client.LimitOrders.Select(order => order.RelativeStopLoss).ToArray()
    );
    foreach (var order in client.LimitOrders)
    {
      var distance = order.RelativeStopLoss / 100_000m;
      Assert.Equal(4347.0m, order.LimitPrice - distance);
    }

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoResolvesTheCandidatesOwnSymbolNotTheSessionsBound()
  {
    // Owner-reported 2026-08-19 (multi-symbol scale-up): before
    // ResolveBoundSymbol/ResolveInstrumentUnits, ProcessManualAlgoAsync used
    // RequireSymbol() - the session's single bound symbol (XAU here, per
    // Symbol below) - for every candidate regardless of its own declared
    // symbol. A EURUSD /algo candidate would have priced, stopped, and sized
    // its order using XAU's SymbolInfo (Digits=2, not EURUSD's real 5) and
    // XAU's pip geometry. Same scenario as
    // ManualAlgoBypassesOpposingZoneAndKeepsOwnerStop (proven-correct 3-leg
    // split), every price divided by 1000 into EURUSD's own scale.
    // RelativeStopLoss is raw price distance * 100_000 (cTrader protocol
    // scaling, not pip-size dependent), so it divides by 1000 too. Volume
    // is lots * LotSize: with EURUSD's own 0.0001 pip size 1000x smaller
    // than XAU's 0.1, the *lot count* this risk sizes to is identical to
    // the XAU test, but EurUsdSymbol's realistic 100_000 LotSize (vs the
    // XAU fixture's 10_000) makes the raw-unit Volume 10x larger - which is
    // real: a standard FX lot is a materially bigger contract than this
    // gold fixture's, so the same risk-% sizing genuinely produces higher
    // raw volume, matching the owner's own "not same as gold" expectation.
    // LimitPrice's decimal precision differs (5 digits, not 2), which
    // XAU's SymbolInfo could never produce.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      manualStopLoss: 4.002m,
      entryLow: 3.9995m,
      entryHigh: 4.0005m,
      opposingZoneLow: 4.001m,
      opposingZoneHigh: 4.003m,
      // Default manual_take_profits (entryLow - 3/6/9) are XAU-scale offsets
      // that go negative at EURUSD's ~4.0 price scale - same 1000x scale-down
      // as every other price in this test, kept positive and below entryLow.
      manualTakeProfits: new[] { 3.9965m, 3.9935m, 3.9905m },
      symbol: "EURUSD"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    engine.BindInstrumentSymbols([Symbol, EurUsdSymbol]);
    // ProcessCandidateAsync's own routing gate (RequireSymbol() vs
    // candidate.Symbol) only resolves a non-session symbol when
    // InstrumentRegistry is set - production always sets this via
    // FeedRunner. Without it here, the candidate is rejected as
    // "unsupported candidate" before ever reaching ProcessManualAlgoAsync.
    engine.InstrumentRegistry = new InstrumentRuntimeRegistry([
      new InstrumentRuntime
      {
        InstrumentId = "EURUSD",
        Feed = new FeedInstrumentOptions(
          InstrumentId: "EURUSD",
          CanonicalSymbol: "EURUSD",
          CTraderSymbol: "EURUSD",
          RedisSymbol: "EURUSD",
          Timeframes: ["M1", "M5"],
          BackfillBars: 100,
          BarsWindowMax: 100,
          BarsChannel: "bars:new",
          BarQualityLookback: 6,
          Rollout: InstrumentRollout.Live
        ),
        Execution = new ExecutionInstrumentOptions(
          InstrumentId: "EURUSD",
          CanonicalSymbol: "EURUSD",
          Rollout: InstrumentRollout.Live,
          PipSize: 0.0001m,
          ContractSize: 100_000m,
          EffectiveSymbols: ["EURUSD"]
        ),
      },
    ]);
    await engine.ObserveSpotAsync(
      new SpotPrice("EURUSD", 4.0000m, 4.0002m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    // FX production uses single-entry; verify EURUSD units place a live order.
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");
    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(3.9995m, order.LimitPrice);
    Assert.True(order.Volume > 0);
    Assert.True(order.RelativeStopLoss > 0);
    // Scale-up: FX manual events must stamp EURUSD, not the session XAU
    // symbol, or Python's per-symbol worker never sees the fill/notify.
    Assert.Contains(
      store.Events,
      item => item.Type == "manual_limit_placed" && item.Symbol == "EURUSD"
    );
    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "manual_limit_placed" && item.Symbol == "XAU"
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task PeriodicReconcileAdoptsFilledFxManualOrderInItsOwnPartition()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      manualStopLoss: 4.002m,
      entryLow: 3.9995m,
      entryHigh: 4.0005m,
      manualTakeProfits: new[] { 3.9965m, 3.9935m, 3.9905m },
      symbol: "EURUSD"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    engine.BindInstrumentSymbols([Symbol, EurUsdSymbol]);
    engine.InstrumentRegistry = new InstrumentRuntimeRegistry([
      new InstrumentRuntime
      {
        InstrumentId = "EURUSD",
        Feed = new FeedInstrumentOptions(
          InstrumentId: "EURUSD",
          CanonicalSymbol: "EURUSD",
          CTraderSymbol: "EURUSD",
          RedisSymbol: "EURUSD",
          Timeframes: ["M1", "M5"],
          BackfillBars: 100,
          BarsWindowMax: 100,
          BarsChannel: "bars:new",
          BarQualityLookback: 6,
          Rollout: InstrumentRollout.Live
        ),
        Execution = new ExecutionInstrumentOptions(
          InstrumentId: "EURUSD",
          CanonicalSymbol: "EURUSD",
          Rollout: InstrumentRollout.Live,
          PipSize: 0.0001m,
          ContractSize: 100_000m,
          EffectiveSymbols: ["EURUSD"]
        ),
      },
    ]);
    await engine.ObserveSpotAsync(
      new SpotPrice("EURUSD", 4.0000m, 4.0002m, now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var pending = Assert.Single(client.PendingOrders);
    client.FillPendingOrder(pending.OrderId);

    // RunSession's periodic reconciliation is account-wide but partitioned
    // by each bound SymbolInfo. Before this regression fix it only filtered
    // the account snapshot to the session XAU SymbolId, so this EURUSD fill
    // remained invisible forever.
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitForEventAsync(store, "manual_opened");

    var opened = Assert.Single(
      store.Events, item => item.Type == "manual_opened"
    );
    Assert.Equal("EURUSD", opened.Symbol);
    Assert.Equal("EURUSD", Assert.Single(store.Positions.Values).Symbol);
    Assert.True(store.Values.ContainsKey("auto_trade:executor_snapshot:EURUSD"));
    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "manual_opened" && item.Symbol == "XAU"
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ReconcileNeverTreatsUnknownTrackedSymbolAsSessionXau()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore("{}");
    store.Positions[9001] = new AutoTradePositionState(
      CandidateId: "unknown-symbol-position",
      PositionId: 9001,
      SymbolId: 999_999,
      Direction: TradeDirection.Buy,
      EntryPrice: 1.2m,
      InitialVolume: 1_000,
      RemainingVolume: 1_000,
      Slices: [1_000],
      TargetsPips: [30],
      NextTargetIndex: 0,
      OpenedAt: Now.ToUnixTimeSeconds(),
      Symbol: "NOT_BOUND"
    );
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    engine.BindInstrumentSymbols([Symbol, EurUsdSymbol]);

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitUntilAsync(() =>
      store.Values.ContainsKey("auto_trade:executor_snapshot:EURUSD")
    );
    await Task.Delay(50, cts.Token);

    Assert.Contains(9001, store.Positions.Keys);
    Assert.DoesNotContain(9001, store.PositionMissing.Keys);
    Assert.DoesNotContain(
      "position_missing_snapshot_suspected",
      store.MetricsSnapshot()
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

    // 2026-08 R:R redesign: the reference entry is now the zone's Shallow
    // edge (zone.Low for a SELL, 3999.5) unconditionally, not the owner's
    // current-price-if-already-inside-the-zone (previously 4000.0) - stop
    // distance from 3999.5 to the 4006.0 owner stop is 65 pips, not 60.
    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(650_000, order.RelativeStopLoss);
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
  public async Task ManualFxSingleEntryPlacesOneLimitOrderWithPolicyWeights()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "SELL",
      entryLow: 3999.5m,
      entryHigh: 3999.5m,
      manualStopLoss: 4006.0m,
      targetsPips: new[] { 30, 60, 90 },
      manualTakeProfits: new[] { 3996.5m, 3993.5m, 3990.5m },
      manualSingleEntry: true,
      manualTargetWeights: new[] { 25, 25, 50 }
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");
    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(TradeDirection.Sell, order.Direction);
    Assert.Equal(3999.5m, order.LimitPrice);
    Assert.Contains(store.Events, item => item.Type == "manual_limit_placed");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoWithAFiveTargetLadderDropsLegTagInsteadOfRejecting()
  {
    // Live 2026-08-19 (candidate manual:86:0): the short-form owner DM
    // syntax auto-fills a 5-level target ladder (30/60/100/130/200 pips).
    // The 9-part avm comment for that many targets already sits close to
    // the 100-char ceiling, and appending |legIndex|legCount (2026-08 R:R
    // redesign) tipped it over - "manual algo comment is 103 chars" -
    // which failed the candidate outright and it was then rejected as
    // stale on retry. legIndex/legCount are observability-only (see
    // ParseManualComment's backward-compat note), so BuildManualComment
    // must drop them and still place the order rather than losing it.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    // Realistic 10-digit unix timestamps for both barTs and expiresAt (not
    // this file's usual tiny placeholders) - a real signal's actual
    // trade-day expiry, not "0"/never-expires, is most of the missing
    // length between this test's earlier 88-89 chars and production's 103.
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "SELL",
      candidateId: "manual:86:0",
      entryLow: 3999.5m,
      entryHigh: 4000.5m,
      manualStopLoss: 4006.0m,
      targetsPips: new[] { 30, 60, 100, 130, 200 },
      manualTakeProfits: new[]
      {
        3999.5m - 3.0m, 3999.5m - 6.0m, 3999.5m - 10.0m,
        3999.5m - 13.0m, 3999.5m - 20.0m,
      },
      expiresAt: 1_787_126_400,
      barTs: 1_787_106_159
    ));
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { Balance = 50_000m, Equity = 50_000m },
    };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");
    Assert.NotEmpty(client.LimitOrders);
    foreach (var order in client.LimitOrders)
    {
      Assert.StartsWith("avm|", order.Comment);
      Assert.True(
        order.Comment.Length <= 100,
        $"comment is {order.Comment.Length} chars: {order.Comment}"
      );
    }

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualRestartRestoresTrancheCountFromPlanWhenCommentHasNoLegSuffix()
  {
    // cTrader persists the compact 9-part form when |legIndex|legCount would
    // exceed its 100-char comment limit. A cold executor must recover the
    // three-leg group from the durable client-order identities, rather than
    // silently treating this broker position as a legacy single-entry trade.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    const string groupId = "manual-res";
    const string comment =
      "avm|manual-res|manual-res|1000|300,300,400|30,60,90|1,2,3|1000|0";
    Assert.Equal(9, comment.Split('|').Length);
    var store = new FakeAutoTradeStore(CandidateJson());
    var plan = new AutoTradeGroupPlan(
      CandidateId: "manual:restart:0",
      GroupId: groupId,
      MatchId: null,
      StrategyFamily: "manual",
      RangeId: null,
      Setup: "Manual Algo",
      Direction: "SELL",
      CreatedAt: Now.ToUnixTimeSeconds(),
      TargetPrices: [3997m, 3994m, 3991m],
      ManualStopLoss: 4006m,
      Route: "manual_limit",
      ClientOrderIds: ["av-manual-res", "av-manual-res-L2", "av-manual-res-L3"],
      ManualRiskStopPips: 60m
    );
    store.Values[$"auto_trade:group_plan:{groupId}"] = JsonSerializer.Serialize(
      plan,
      RedisJsonContext.Default.AutoTradeGroupPlan
    );
    var client = new FakeTradingClient();
    client.SeedPosition(new TradingPosition(
      PositionId: 91,
      SymbolId: Symbol.SymbolId,
      Direction: TradeDirection.Sell,
      Volume: 1_000,
      EntryPrice: 4000m,
      StopLoss: 4006m,
      Label: Options().Label,
      Comment: comment,
      ClientOrderId: "av-manual-res-L2"
    ));
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitUntilAsync(() => store.Positions.ContainsKey(91));

    var recovered = store.Positions[91];
    Assert.Equal(3, recovered.GroupTrancheCount);
    Assert.Equal(60m, recovered.InitialRiskStopPips);
    Assert.Equal("manual:restart:0", recovered.CandidateId);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Theory]
  [InlineData("BUY")]
  [InlineData("SELL")]
  public async Task ManualAlgoThreeLegRiskAndTerminalResultStayGroupCanonical(
    string direction
  )
  {
    // Production #101/#104: all three broker stops were correct, but the
    // event/state risk pips shrank from Shallow to Mid/Deep and a shared-SL
    // snapshot disappearance ultimately published only the final deep leg.
    // One manual intent must retain Shallow's 60p risk contract while the
    // result remains the volume-weighted actual PnL of all three fills.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(8));
    var now = Now;
    var isBuy = direction == "BUY";
    const decimal entryLow = 4350.0m;
    const decimal entryHigh = 4353.0m;
    var ownerStop = isBuy ? 4347.0m : 4356.0m;
    var ownerTargets = isBuy
      ? new[] { 4356.0m, 4359.0m, 4362.0m }
      : new[] { 4347.0m, 4344.0m, 4341.0m };
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: direction,
      candidateId: $"manual:risk:{direction.ToLowerInvariant()}",
      entryLow: entryLow,
      entryHigh: entryHigh,
      manualStopLoss: ownerStop,
      manualTakeProfits: ownerTargets,
      manualSingleEntry: false
    ));
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { Balance = 50_000m, Equity = 50_000m },
      PositionCloseReasonToReturn = PositionCloseReason.StopLossOrTakeProfit,
      PositionCloseExecutionPriceToReturn = ownerStop,
    };
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice(
        "XAU",
        isBuy ? 4360.0m : 4340.0m,
        isBuy ? 4360.2m : 4340.2m,
        now.ToUnixTimeSeconds()
      ),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Equal(3, client.LimitOrders.Count);
    var placed = store.Events
      .Where(item => item.Type == "manual_limit_placed")
      .ToArray();
    Assert.Equal(3, placed.Length);
    Assert.All(placed, item => Assert.Equal(60m, item.StopPips));
    Assert.All(placed, item => Assert.Equal(1_000m, item.RiskBudget));
    var totalVolume = client.LimitOrders.Sum(order => order.Volume);
    Assert.True(client.LimitOrders[0].Volume > client.LimitOrders[1].Volume);
    Assert.True(client.LimitOrders[1].Volume > client.LimitOrders[2].Volume);
    // 2026-09-08: each leg's OWN lots x its OWN distance to the shared
    // absolute stop, not one flat 60p assumed for all three - Shallow is
    // genuinely 60p from the stop (0.24 lots), the risk leg only 10p
    // (0.05 lots).
    // 2026-09-09: Deep rests at the zone's own midpoint (4351.5) instead of
    // its far edge, so its distance to the 4347/4356 stop is now 45p, not
    // 30p (0.06 lots): -(0.24*60 + 0.06*45 + 0.05*10) * 10 = -176.00.
    Assert.All(placed, item => Assert.Equal(-176.00m, item.GroupWorstCase));
    foreach (var order in client.LimitOrders)
    {
      var distance = order.RelativeStopLoss / 100_000m;
      var resolvedStop = isBuy
        ? order.LimitPrice - distance
        : order.LimitPrice + distance;
      Assert.Equal(ownerStop, resolvedStop);
    }

    foreach (var pending in client.PendingOrders.ToArray())
    {
      client.FillPendingOrder(pending.OrderId);
    }
    now = now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.Events.Count(item => item.Type == "manual_opened") == 3
    );
    await Task.Delay(50, cts.Token);
    var states = store.Positions.Values.ToArray();
    Assert.Equal(3, states.Length);
    Assert.All(states, state => Assert.Equal(60m, state.InitialRiskStopPips));
    Assert.All(states, state => Assert.Equal(ownerStop, state.CurrentStopLoss));
    Assert.All(
      store.Events.Where(item => item.Type == "manual_opened"),
      item => Assert.Equal(60m, item.StopPips)
    );
    var expectedActualGroupPips = states.Sum(state => (
      isBuy
        ? ownerStop - state.EntryPrice
        : state.EntryPrice - ownerStop
    ) / 0.1m * state.RemainingVolume) / totalVolume;

    foreach (var state in states)
    {
      client.RemovePosition(state.PositionId);
    }
    now = now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitForEventAsync(store, "group_result");

    var result = Assert.Single(
      store.Events,
      item => item.Type == "group_result"
    );
    Assert.Equal(expectedActualGroupPips, result.GroupRealizedPips);
    Assert.Equal(60m, result.StopPips);
    Assert.Equal(totalVolume, result.GroupInitialVolume);
    Assert.All(
      store.Events.Where(item => item.Type == "position_closed"),
      item => Assert.Equal(60m, item.StopPips)
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task SimultaneousGroupCloseSeedsFromCanonicalTrackedSibling()
  {
    // A crash can leave one durable sibling with a newer booked aggregate
    // than another. If the whole ladder then disappears in one snapshot,
    // the first stale leg must seed from the group max, not its own older
    // field, before the in-pass close accumulator takes over.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    const string groupId = "manual-see";
    const decimal ownerStop = 4006m;
    const long totalVolume = 1_000;
    const decimal priorBookedPipVolume = totalVolume * 12m;
    var store = new FakeAutoTradeStore(CandidateJson());
    var entries = new[] { 4000m, 4000.5m, 4001m };
    var volumes = new long[] { 500, 300, 200 };
    for (var index = 0; index < entries.Length; index++)
    {
      var state = new AutoTradePositionState(
        CandidateId: "manual:seed:0",
        PositionId: 91 + index,
        SymbolId: Symbol.SymbolId,
        Direction: TradeDirection.Sell,
        EntryPrice: entries[index],
        InitialVolume: volumes[index],
        RemainingVolume: volumes[index],
        Slices: [volumes[index]],
        TargetsPips: [30],
        NextTargetIndex: 0,
        OpenedAt: Now.ToUnixTimeSeconds(),
        CurrentStopLoss: ownerStop,
        TargetOrdinals: [1],
        GroupId: groupId,
        TrancheIndex: index + 1,
        GroupTrancheCount: 3,
        InitialStopLoss: ownerStop,
        GroupRealizedPipVolume: index == entries.Length - 1
          ? priorBookedPipVolume
          : 0m,
        GroupInitialVolume: totalVolume,
        InitialTrancheVolume: index == 0 ? volumes[index] : 0,
        Setup: "Manual Algo",
        Stream: "algo_manual",
        StrategyFamily: "manual",
        Symbol: "XAU",
        InitialRiskStopPips: 60m
      );
      store.Positions[state.PositionId] = state;
    }
    var client = new FakeTradingClient
    {
      PositionCloseReasonToReturn = PositionCloseReason.StopLossOrTakeProfit,
      PositionCloseExecutionPriceToReturn = ownerStop,
    };
    var engine = new AutoTradeEngine(
      Options() with { PositionMissingConfirmations = 1 },
      store,
      () => Now,
      _ => { }
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitUntilAsync(() =>
      store.Events.Count(item => item.Type == "position_closed") == 3
    );

    var closePipVolume = entries.Select((entry, index) =>
      (entry - ownerStop) / 0.1m * volumes[index]
    ).Sum();
    var expected = (priorBookedPipVolume + closePipVolume) / totalVolume;
    var result = Assert.Single(store.Events, item => item.Type == "group_result");
    Assert.Equal(expected, result.GroupRealizedPips);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoFixesFirstLegToPointZeroFiveLotsAboveThreshold()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    // Multi-leg XAU ladder: places three entry limits and books TP volume.
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "SELL",
      entryLow: 3999.5m,
      entryHigh: 4000.5m,
      manualStopLoss: 4002.5m,
      manualSingleEntry: false
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

    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");
    Assert.Equal(3, client.LimitOrders.Count);
    // 2026-09-08: 3_000 from the 80/20 ladder (unchanged sizing) plus the
    // fixed-size risk leg (equity 2_000 here, at/above the $1k floor, so
    // the default 0.05 lots = 500 units).
    Assert.Equal(3_500L, client.LimitOrders.Sum(order => order.Volume));
    Assert.Contains(store.Events, item => item.Type == "manual_limit_placed");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoKeepsEvenSplitAtOrBelowThreshold()
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
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");
    var order = Assert.Single(client.LimitOrders);
    Assert.True(order.Volume > 0);
    Assert.StartsWith("avm|", order.Comment);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoPlacesLimitAtShallowEdgeEvenWhenPriceAlreadyInsideZone()
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

    // 2026-08 R:R redesign: entry legs are fixed at the owner's typed zone
    // edges (Shallow/Mid/Deep), not adjusted to the current price even
    // when it already sits inside the zone - Shallow for a SELL is
    // zone.Low (3999.5), unconditionally.
    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(3999.5m, order.LimitPrice);

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
  public async Task ManualAlgoSellWholeHandleBooksWhenAskIsAbovePostedTp()
  {
    // Prod 2026-08-12 #6: VIP posts whole TPs (4408); bid tagged the handle
    // but ask sat a few ticks above, so strict ask<=tp missed the book.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var ownerTargets = new[] { 4425.00m, 4422.00m, 4418.00m, 4415.00m, 4408.00m };
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "SELL",
      entryLow: 4428.0m,
      entryHigh: 4433.0m,
      manualStopLoss: 4436.0m,
      targetsPips: new[] { 30, 60, 100, 130, 200 },
      manualTakeProfits: ownerTargets
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4428.0m, 4428.1m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    client.FillPendingOrder(Assert.Single(client.PendingOrders).OrderId);
    now = Now.AddSeconds(16);
    await WaitForEventAsync(store, "manual_opened");

    // Ask a few ticks above TP5 handle; bid already through — must book.
    now = Now.AddSeconds(30);
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4408.00m, 4408.05m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.True(
      client.Closes.Count >= 1,
      "expected sell whole-handle TP to book with ask above posted TP"
    );
    Assert.Contains(
      store.Events,
      item => item.Type == "take_profit"
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoFirstTpCancelsUnfilledEntryClips()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(8));
    var now = Now;
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "SELL",
      candidateId: "manual:92:0",
      entryLow: 3999.5m,
      entryHigh: 4000.5m,
      manualStopLoss: 4006.0m,
      targetsPips: new[] { 30, 60, 90 },
      expiresAt: 1_787_126_400,
      barTs: 1_787_106_159,
      manualSingleEntry: false
    ));
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { Balance = 50_000m, Equity = 50_000m },
    };
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    Assert.Equal(3, client.PendingOrders.Count);

    // Legs place shallow/mid/deep in order (ManualEntryLegPrices), so
    // PendingOrders[0] is the shallow leg - the most-likely-to-fill,
    // largest (50%) leg. Only it fills here; mid/deep never do.
    var filled = client.PendingOrders[0].OrderId;
    client.FillPendingOrder(filled);
    now = Now.AddSeconds(16);
    await WaitForEventAsync(store, "manual_opened");
    Assert.Equal(2, client.PendingOrders.Count);

    now = Now.AddSeconds(30);
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3996.40m, 3996.45m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    // Owner-reported 2026-08-19: deep-first booking assigned the group's
    // earliest ordinal (TP1) to the deepest (smallest, least-likely-to-
    // fill) leg. Since only shallow ever filled here, a deep-first plan
    // would have shallow itself owning a LATER ordinal (or none at all if
    // mid/deep were meant to fully cover TP1+TP2) - this hit would either
    // book under the wrong label or never fire, and the real TP1/TP2
    // never appear on the channel because no order was ever placed to
    // reach them. Shallow-first booking means the leg that actually
    // filled owns TP1.
    var tp = Assert.Single(store.Events, item => item.Type == "take_profit");
    Assert.StartsWith("TP1 ", tp.Message);
    Assert.Contains(store.Events, item => item.Type == "unfilled_legs_cancelled");
    Assert.Empty(client.PendingOrders);
    Assert.Equal(2, client.CancelledOrders.Count);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoTp1UsesSharedGroupEconomicBreakeven()
  {
    // TP1 profit funds one shared stop for all remaining ladder volume.
    // This guarantees the configured positive group buffer without bunching
    // every clip at its own entry where an ordinary retest can sweep it.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(8));
    var now = Now;
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "SELL",
      candidateId: "manual:93:0",
      entryLow: 3999.5m,
      entryHigh: 4000.5m,
      manualStopLoss: 4006.0m,
      targetsPips: new[] { 30, 60, 90 },
      expiresAt: 1_787_126_400,
      barTs: 1_787_106_159,
      manualSingleEntry: false
    ));
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { Balance = 50_000m, Equity = 50_000m },
      CloseExecutionPriceToReturn = 3996.4m,
    };
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    Assert.Equal(3, client.PendingOrders.Count);

    foreach (var pending in client.PendingOrders.ToArray())
    {
      client.FillPendingOrder(pending.OrderId);
    }
    now = Now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.Events.Count(item => item.Type == "manual_opened") == 3
    );
    Assert.Equal(3, store.Positions.Count);

    now = Now.AddSeconds(30);
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3996.40m, 3996.45m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.Contains(store.Events, item => item.Type == "take_profit");
    Assert.Contains(
      store.Events,
      item => item.Type == "stop_moved" && item.Message.Contains("group BE+6")
    );
    var remainingStates = store.Positions.Values.ToArray();
    var sharedStop = Assert.Single(
      remainingStates.Select(state => state.CurrentStopLoss).Distinct()
    );
    Assert.NotNull(sharedStop);
    var bookedPipVolume = remainingStates.Max(
      state => state.GroupRealizedPipVolume
    );
    var terminalPipVolume = bookedPipVolume + remainingStates.Sum(state => (
      state.EntryPrice - sharedStop!.Value
    ) / 0.1m * state.RemainingVolume);
    var groupInitialVolume = remainingStates.Max(
      state => state.GroupInitialVolume
    );
    Assert.True(
      terminalPipVolume / groupInitialVolume >= 0.6m,
      "shared TP1 stop must protect the configured group-level profit buffer"
    );
    Assert.Contains(
      remainingStates,
      state => sharedStop!.Value > StopTrailPlanner.ProtectedBreakevenStop(
        state.Direction,
        state.EntryPrice,
        Symbol,
        6
      )
    );

    // TP2 advances every surviving clip to the actual shallow entry, even
    // when TP2 consumes the booking leg and its target loop exits. TP1 is
    // deliberately reserved for the later TP3 trail.
    client.CloseExecutionPriceToReturn = 3993.4m;
    now = now.AddSeconds(30);
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3993.4m, 3993.45m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    await WaitUntilAsync(() => store.Events.Any(item =>
      item.Type == "take_profit" && item.TargetPips == 60
    ));
    Assert.NotEmpty(store.Positions);
    Assert.All(
      store.Positions.Values,
      state => Assert.Equal(3999.5m, state.CurrentStopLoss)
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Theory]
  [InlineData("BUY")]
  [InlineData("SELL")]
  public async Task ManualAlgoShallowOnlyFillTrailsToEntryAtTp2ThenTp2AtTp3(
    string direction
  )
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(8));
    var now = Now;
    var isBuy = direction == "BUY";
    var shallow = 3999.5m;
    var ownerTargets = isBuy
      ? new[] { 4002.5m, 4005.5m, 4009.5m, 4012.5m, 4019.5m }
      : new[] { 3996.5m, 3993.5m, 3989.5m, 3986.5m, 3979.5m };
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: direction,
      candidateId: $"manual:shallow-runner:{direction}",
      entryLow: isBuy ? 3996.5m : shallow,
      entryHigh: isBuy ? shallow : 4002.5m,
      manualStopLoss: isBuy ? 3993.0m : 4006.0m,
      targetsPips: new[] { 30, 60, 100, 130, 200 },
      manualTakeProfits: ownerTargets,
      expiresAt: 1_787_126_400,
      barTs: 1_787_106_159,
      manualSingleEntry: false,
      manualTargetWeights: new[] { 40, 15, 15, 15, 15 }
    ));
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { Balance = 1_000m, Equity = 1_000m },
    };
    var engine = new AutoTradeEngine(
      Options() with { SizingMode = "equity_table" },
      store,
      () => now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      isBuy
        ? new SpotPrice("XAU", 4010.0m, 4010.2m, now.ToUnixTimeSeconds())
        : new SpotPrice("XAU", 3990.0m, 3990.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    // 2026-09-08: 2-leg 80/20 ladder (800/200 of the 1_000-unit sizing)
    // plus the fixed-size risk leg (equity 1_000 is at, not below, the
    // $1k floor, so the default 0.05 lots = 500 units).
    Assert.Equal(new long[] { 800, 200, 500 }, client.PendingOrders
      .Select(order => order.Volume));

    client.FillPendingOrder(client.PendingOrders[0].OrderId);
    now = now.AddSeconds(16);
    await WaitForEventAsync(store, "manual_opened");
    var shallowState = Assert.Single(store.Positions.Values);
    // 2026-09-08: shallow is now 800 units (was 700) and there are 3 legs
    // sharing the group's target ladder instead of 2/3, so the shallow-
    // first walk hands off to Deep/the risk leg at different points.
    Assert.Equal(new long[] { 500, 200, 100 }, shallowState.Slices);

    async Task HitAsync(decimal target)
    {
      client.CloseExecutionPriceToReturn = target;
      now = now.AddSeconds(30);
      await engine.ObserveSpotAsync(
        isBuy
          ? new SpotPrice(
            "XAU", target + 0.05m, target + 0.10m, now.ToUnixTimeSeconds()
          )
          : new SpotPrice(
            "XAU", target - 0.10m, target - 0.05m, now.ToUnixTimeSeconds()
          ),
        cts.Token
      );
    }

    await HitAsync(ownerTargets[0]);
    Assert.Empty(client.PendingOrders);
    Assert.Equal(2, client.CancelledOrders.Count);
    var stopAfterTp1 = Assert.Single(store.Positions.Values).CurrentStopLoss;
    Assert.NotNull(stopAfterTp1);
    var amendmentsAfterTp1 = client.StopAmendments.Count;

    await HitAsync(ownerTargets[1]);
    Assert.Equal(amendmentsAfterTp1 + 1, client.StopAmendments.Count);
    Assert.Equal(shallow, Assert.Single(store.Positions.Values).CurrentStopLoss);
    Assert.Contains(
      "manual_tp2_shallow_entry_applied", store.MetricsSnapshot()
    );

    await HitAsync(ownerTargets[2]);
    var runner = Assert.Single(store.Positions.Values);
    Assert.Equal(100, runner.RemainingVolume);
    // TP3 trails to TP2 (the immediately preceding rung, resolved via the
    // full owner ladder's absolute TargetPrices), not TP1 - the ladder grew
    // from 2 to 5 owner levels and "two behind" would skip TP2's entire
    // already-realized gain.
    Assert.Equal(ownerTargets[1], runner.CurrentStopLoss);
    Assert.Equal(5, runner.TargetOrdinals![runner.NextTargetIndex]);

    // TP4 is intentionally absent from the shallow leg's broker plan. It is
    // still a real owner level: notify and trail when price reaches it, but
    // never invent a close/ledger entry for a zero-volume booking.
    await HitAsync(ownerTargets[3]);
    runner = Assert.Single(store.Positions.Values);
    var reachedTp4 = Assert.Single(
      store.Events,
      item => item.Type == "manual_tp_reached" && item.TargetPips == 130
    );
    Assert.Equal(0, reachedTp4.Volume);
    Assert.Contains("no broker volume booked", reachedTp4.Message);
    // TP4 trails to TP3 (one behind), not TP2.
    Assert.Equal(ownerTargets[2], runner.CurrentStopLoss);
    Assert.Contains(4, runner.ReachedTargetOrdinals!);
    // 2026-09-08: shallow's 3rd (final) slice now maps straight to ordinal
    // 5 (see shallowState.Slices above) - only TP1/TP2 have booked shallow
    // volume by TP4.
    Assert.Equal(new long[] { 500, 200 }, client.Closes
      .Select(close => close.Volume));

    await HitAsync(ownerTargets[4]);
    Assert.Empty(store.Positions);
    Assert.Equal(new long[] { 500, 200, 100 }, client.Closes
      .Select(close => close.Volume));

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoShallowOnlyTrailPolicySurvivesRestart()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    const string candidateId = "manual:restart-runner:0";
    const string groupId = "manual-restart-runner";
    var ownerTargets = new[]
    {
      3996.5m, 3993.5m, 3989.5m, 3986.5m, 3979.5m,
    };
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      candidateId: candidateId
    ));
    store.SeedPublishedCandidate(candidateId);
    store.Positions[91] = new AutoTradePositionState(
      CandidateId: candidateId,
      PositionId: 91,
      SymbolId: Symbol.SymbolId,
      Direction: TradeDirection.Sell,
      EntryPrice: 3999.5m,
      InitialVolume: 700,
      RemainingVolume: 400,
      Slices: [300, 200, 100, 100],
      TargetsPips: [30, 60, 100, 200],
      NextTargetIndex: 1,
      OpenedAt: 900,
      CurrentStopLoss: 4000.0m,
      TargetOrdinals: [1, 2, 3, 5],
      GroupId: groupId,
      GroupTrancheCount: 3,
      InitialStopLoss: 4006.0m,
      GroupInitialVolume: 700,
      InitialTrancheVolume: 700,
      Setup: "Manual Algo",
      Stream: "algo_manual",
      StrategyFamily: "manual",
      TargetPrices: ownerTargets,
      Symbol: "XAU"
    );
    var client = new FakeTradingClient
    {
      CloseExecutionPriceToReturn = ownerTargets[1],
    };
    client.SeedPosition(new TradingPosition(
      PositionId: 91,
      SymbolId: Symbol.SymbolId,
      Direction: TradeDirection.Sell,
      Volume: 400,
      EntryPrice: 3999.5m,
      StopLoss: 4000.0m,
      Label: Options().Label,
      Comment: "avm|runner|manual-restart-runner|700|300,200,100,100|30,60,100,200|1,2,3,5|900|0",
      ClientOrderId: "av-runner"
    ));
    var now = Now;
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "ready");

    now = now.AddSeconds(30);
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3993.4m, 3993.45m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.Equal((91, 3999.5m), Assert.Single(client.StopAmendments));
    Assert.Equal(3999.5m, store.Positions[91].CurrentStopLoss);

    client.CloseExecutionPriceToReturn = ownerTargets[2];
    now = now.AddSeconds(30);
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3989.4m, 3989.45m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.Equal(2, client.StopAmendments.Count);
    // TP3 trails to TP2 (one behind), not TP1.
    Assert.Equal((91, ownerTargets[1]), client.StopAmendments[^1]);
    Assert.Equal(100, store.Positions[91].RemainingVolume);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoDustRemainderBeforeFinalTargetStillNotifiesAndTrails()
  {
    // Owner reported 2026-09-04: TP5 needs the full remaining volume to
    // close cleanly, so a broker partial close at TP4 would leave a dust
    // remainder below MinVolume. The runtime already skips that partial and
    // rides the whole remainder to TP5 (see "range-box scale-out skipped at
    // runtime" below) - this proves manual /algo still gets the TP4 level
    // notification and normal trail out of that skip, not silence.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    const string candidateId = "manual:dust-remainder:0";
    const string groupId = "manual-dust-remainder";
    var ownerTargets = new[]
    {
      3996.5m, 3993.5m, 3989.5m, 3986.5m, 3979.5m,
    };
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      candidateId: candidateId
    ));
    store.SeedPublishedCandidate(candidateId);
    store.Positions[92] = new AutoTradePositionState(
      CandidateId: candidateId,
      PositionId: 92,
      SymbolId: Symbol.SymbolId,
      Direction: TradeDirection.Sell,
      EntryPrice: 3999.5m,
      // TP1-TP3 already booked (300+200+100=600 of 705); only a 105-volume
      // dust-prone remainder is left in front of TP4/TP5.
      InitialVolume: 705,
      RemainingVolume: 105,
      Slices: [300, 200, 100, 100, 100],
      TargetsPips: [30, 60, 100, 130, 200],
      NextTargetIndex: 3,
      OpenedAt: 900,
      CurrentStopLoss: ownerTargets[0],
      TargetOrdinals: [1, 2, 3, 4, 5],
      GroupId: groupId,
      GroupTrancheCount: 1,
      InitialStopLoss: 4006.0m,
      GroupInitialVolume: 705,
      InitialTrancheVolume: 705,
      Setup: "Manual Algo",
      Stream: "algo_manual",
      StrategyFamily: "manual",
      TargetPrices: ownerTargets,
      Symbol: "XAU"
    );
    var client = new FakeTradingClient
    {
      CloseExecutionPriceToReturn = ownerTargets[3],
    };
    client.SeedPosition(new TradingPosition(
      PositionId: 92,
      SymbolId: Symbol.SymbolId,
      Direction: TradeDirection.Sell,
      Volume: 105,
      EntryPrice: 3999.5m,
      StopLoss: ownerTargets[0],
      Label: Options().Label,
      Comment: "avm|runner|manual-dust-remainder|705|300,200,100,100,100|30,60,100,130,200|1,2,3,4,5|900|0",
      ClientOrderId: "av-dust-remainder"
    ));
    var now = Now;
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "ready");

    // Price reaches TP4 (130p). A broker close of Slices[3]=100 would leave
    // 5 remaining, under MinVolume=100 - the dust-remainder guard must skip
    // the close but still notify and trail exactly like a real booking.
    now = now.AddSeconds(30);
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3986.45m, 3986.5m, now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.Empty(client.Closes);
    var reachedTp4 = Assert.Single(
      store.Events,
      item => item.Type == "manual_tp_reached"
    );
    Assert.Equal(130, reachedTp4.TargetPips);
    Assert.Equal(0, reachedTp4.Volume);
    Assert.Contains("no broker volume booked", reachedTp4.Message);
    var runner = Assert.Single(store.Positions.Values);
    Assert.Equal(105, runner.RemainingVolume);
    Assert.Contains(4, runner.ReachedTargetOrdinals!);
    // Trail after TP4 steps back to TP3 (one level), same rule a real TP4
    // booking would use.
    Assert.Equal(ownerTargets[2], runner.CurrentStopLoss);
    Assert.Equal((92, ownerTargets[2]), Assert.Single(client.StopAmendments));

    // Price then reaches TP5 - the full 105 remainder closes for real.
    client.CloseExecutionPriceToReturn = ownerTargets[4];
    now = now.AddSeconds(30);
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3979.45m, 3979.5m, now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.Empty(store.Positions);
    Assert.Equal(105, Assert.Single(client.Closes).Volume);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualAlgoFillAmendsAbsoluteVipStopAfterBetterFill()
  {
    // Live 2026-08-17 XAU #64: BUY zone 4385-4388, posted SL 4382. Limit sat
    // at zone high; fill 4385.85 made the relative broker SL drift to ~4378.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      direction: "BUY",
      entryLow: 4385.0m,
      entryHigh: 4388.0m,
      manualStopLoss: 4382.0m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4390.0m, 4390.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var placed = Assert.Single(client.LimitOrders);
    Assert.Equal(4388.0m, placed.LimitPrice);
    client.FillPendingOrder(Assert.Single(client.PendingOrders).OrderId, 4385.85m);
    now = Now.AddSeconds(16);
    await WaitForEventAsync(store, "manual_opened");

    var opened = store.Events.Single(item => item.Type == "manual_opened");
    Assert.Equal(4382.0m, opened.StopLoss);
    Assert.Equal(4382.0m, Assert.Single(store.Positions.Values).CurrentStopLoss);
    Assert.Contains(
      client.StopAmendments,
      item => item.StopLoss == 4382.0m
    );

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
  public async Task ManualAlgoRequiresExplicitAnalysisBypassContract()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      bypassAnalysisGates: false
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Empty(client.LimitOrders);
    Assert.Contains(store.Events, item =>
      item.ReasonCode == "manual_algo_bypass_contract_missing"
    );

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
    Assert.DoesNotContain(
      store.Values.Keys,
      key => key.StartsWith("auto_trade:group_plan:")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ManualCommandCancelPendingCancelsEntireLadderGroup()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(ManualCandidateJson(
      manualStopLoss: 4006.0m,
      manualSingleEntry: false
    ));
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { Balance = 1_000m, Equity = 1_000m },
    };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 3990.0m, 3990.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var oldRevision = client.PendingOrders.ToArray();
    Assert.Equal(3, oldRevision.Length);
    var newRevision = oldRevision.Select((order, index) => order with
    {
      OrderId = 9_000 + index,
      Comment = order.Comment.Replace(
        "manual:1:0", "manual:1:1", StringComparison.Ordinal
      ),
    }).ToArray();
    client.PendingOrders.AddRange(newRevision);
    var orderIds = client.PendingOrders.Select(item => item.OrderId).ToArray();

    store.EnqueueCommand(JsonSerializer.Serialize(new
    {
      type = "cancel_pending",
      intent_id = "manual:1:1",
    }));
    await WaitForEventAsync(store, "manual_cancelled");

    Assert.Equal(orderIds.Order(), client.CancelledOrders.Order());
    Assert.Empty(client.PendingOrders);
    Assert.Single(store.Events, item => item.Type == "manual_cancelled");
    Assert.DoesNotContain(
      store.Values.Keys,
      key => key.StartsWith("auto_trade:group_plan:")
    );

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
    Assert.DoesNotContain(
      store.Values.Keys,
      key => key.StartsWith("auto_trade:group_plan:")
    );

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
    // This test is about the price a confirmed closure carries, not about
    // the missing-snapshot confirmation gate itself (covered separately) -
    // one confirmation keeps its original single-reconcile-pass shape.
    var engine = new AutoTradeEngine(
      Options() with { PositionMissingConfirmations = 1 }, store, () => now, _ => { }
    );
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
    Assert.NotNull(closed.Price);
    Assert.True(closed.Price > 0m);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ReconcileDetectedCloseRecordsWarningOnlyCooldownMarker()
  {
    // The engine cannot tell an SL hit from a manual close apart here, so it
    // records evidence but must not claim a confirmed stop loss. Not about
    // the missing-snapshot confirmation gate itself (covered separately).
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with { PositionMissingConfirmations = 1 }, store, () => now, _ => { }
    );
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
  public async Task MissingPositionSnapshotRequiresTwoConfirmationsBeforeTerminalising()
  {
    // Incident hardening: a single reconcile pass that does not see a
    // tracked position must only "suspect" it missing, not delete its
    // tracking - only a second, time-separated confirmation may terminalise.
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
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );

    // First missing snapshot: still tracked, not yet closed.
    Assert.Contains(positionId, store.Positions.Keys);
    Assert.DoesNotContain(store.Events, item => item.Type == "position_closed");
    Assert.DoesNotContain(
      "position_missing_snapshot_confirmed",
      store.MetricsSnapshot()
    );

    // Second independent snapshot, separated by the recheck interval,
    // confirms the absence. A short real-time settle lets the loop finish
    // recomputing nextReconcile off the *current* now before it is advanced
    // again - otherwise this assignment can race ahead of that bookkeeping
    // and get folded into the same nextReconcile computation, pushing the
    // next eligible reconcile pass out by another 15s for nothing.
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitForEventAsync(store, "position_closed");

    Assert.DoesNotContain(positionId, store.Positions.Keys);
    Assert.Contains("position_missing_snapshot_confirmed", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MissingPositionSnapshotClearsWhenPositionReappearsBeforeConfirmation()
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
    var tracked = store.Positions[positionId];

    client.RemovePosition(positionId);
    now = Now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );

    // The position reappears at the broker before the second confirmation.
    // The short settle delay avoids racing the loop's post-reconcile
    // nextReconcile bookkeeping the same way the confirmation test does.
    client.SeedPosition(new TradingPosition(
      tracked.PositionId,
      Symbol.SymbolId,
      tracked.Direction,
      tracked.RemainingVolume,
      tracked.EntryPrice,
      tracked.CurrentStopLoss ?? tracked.EntryPrice,
      "apexvoid-auto",
      ""
    ));
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_recovered")
    );

    Assert.Contains(positionId, store.Positions.Keys);
    Assert.DoesNotContain(store.Events, item => item.Type == "position_closed");
    Assert.DoesNotContain(
      "position_missing_snapshot_confirmed",
      store.MetricsSnapshot()
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Theory]
  [InlineData(
    PositionCloseReason.StopLossOrTakeProfit,
    "stop_loss_or_take_profit",
    "stop loss / take profit"
  )]
  [InlineData(
    PositionCloseReason.ManualOrExternalOrder,
    "manual_or_external_close",
    "manual or external order"
  )]
  public async Task ConfirmedMissingPositionReportsBestEffortCloseReason(
    PositionCloseReason reason,
    string expectedReasonCode,
    string expectedMessageFragment
  )
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(CandidateJson());
    var executionPrice = reason == PositionCloseReason.StopLossOrTakeProfit
      ? 3994.2m
      : 4009.35m;
    var client = new FakeTradingClient
    {
      PositionCloseReasonToReturn = reason,
      PositionCloseExecutionPriceToReturn = executionPrice,
    };
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
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitForEventAsync(store, "position_closed");

    var closed = store.Events.Single(item => item.Type == "position_closed");
    Assert.Equal(expectedReasonCode, closed.ReasonCode);
    Assert.Contains(expectedMessageFragment, closed.Message);
    Assert.Contains(positionId, client.PositionCloseReasonLookups);
    // The lookup must anchor its search to when the position actually
    // opened, not just a fixed window before confirmation - otherwise a
    // missed reconcile gap (eg. a redeploy) can leave the true close outside
    // the search window entirely.
    Assert.NotEqual(0, client.PositionCloseOpenedAtTimestamps.Single());
    if (reason == PositionCloseReason.StopLossOrTakeProfit)
    {
      Assert.Equal(executionPrice, closed.Price);
    }
    else
    {
      // Manual close uses the broker deal, never the current quote or stop.
      Assert.Equal(executionPrice, closed.Price);
      Assert.NotEqual(client.StopAmendments.Single().StopLoss, closed.Price);
      Assert.Contains("pips", closed.Message);
      Assert.NotNull(closed.GroupRealizedPips);
    }

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ConfirmedMissingPositionReportsRealExecutionPriceWhenLookupFindsIt()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      PositionCloseReasonToReturn = PositionCloseReason.ManualOrExternalOrder,
      PositionCloseExecutionPriceToReturn = 4009.35m,
    };
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
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitForEventAsync(store, "position_closed");

    var closed = store.Events.Single(item => item.Type == "position_closed");
    // The real closing deal price must win over the last-known-stop fallback.
    Assert.Equal(4009.35m, closed.Price);
    Assert.NotEqual(client.StopAmendments.Single().StopLoss, closed.Price);
    Assert.Equal("manual_or_external_close", closed.ReasonCode);
    Assert.Contains("winning", closed.Message);
    Assert.NotNull(closed.GroupRealizedPips);
    Assert.True(closed.GroupRealizedPips > 0);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ConfirmedMissingPositionAtProtectiveStopReportsSlReason()
  {
    // Lookup stays Unknown but recovers an execution price on the protective
    // stop — promote to SL/TP. Without a recovered price the classify would
    // stay unconfirmed (see UnknownWithoutRecoveredExitKeepsAmbiguousMessage).
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
    var stop = client.StopAmendments.Single().StopLoss;
    client.PositionCloseReasonToReturn = PositionCloseReason.Unknown;
    client.PositionCloseExecutionPriceToReturn = stop;

    client.RemovePosition(positionId);
    now = Now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitForEventAsync(store, "position_closed");

    var closed = store.Events.Single(item => item.Type == "position_closed");
    Assert.Equal("stop_loss_or_take_profit", closed.ReasonCode);
    Assert.Contains("stop loss / take profit", closed.Message);
    Assert.DoesNotContain("unconfirmed", closed.Message);
    Assert.Equal(stop, closed.Price);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MissingCloseFillWaitsForBrokerExecutionPrice()
  {
    // Deal-list timeout / Unknown with no execution price must NOT promote
    // to SL/TP just because pip accounting fell back to CurrentStopLoss.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      PositionCloseReasonToReturn = PositionCloseReason.Unknown,
      PositionCloseExecutionPriceToReturn = null,
    };
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var positionId = client.StopAmendments.Single().PositionId;
    var stop = client.StopAmendments.Single().StopLoss;

    client.RemovePosition(positionId);
    now = Now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_close_execution_price_pending")
    );

    // A quote observed after the close is not a close price. Keep the state
    // pending instead of publishing a misleading P/L card.
    Assert.DoesNotContain(store.Events, item => item.Type == "position_closed");
    Assert.Contains(positionId, store.Positions.Keys);

    client.PositionCloseExecutionPriceToReturn = 4009.35m;
    now = now.AddSeconds(61);
    await WaitForEventAsync(store, "position_closed");

    var closed = store.Events.Single(item => item.Type == "position_closed");
    Assert.Null(closed.ReasonCode);
    Assert.Contains("unconfirmed", closed.Message);
    Assert.Equal(4009.35m, closed.Price);
    Assert.NotEqual(stop, closed.Price);
    Assert.Contains("pips", closed.Message);
    Assert.NotNull(closed.GroupRealizedPips);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MissingCloseFillWaitsForActualSlippageInsteadOfUsingQuote()
  {
    // A live quote can move far beyond the stop before reconciliation. The
    // close card must wait for the broker deal and retain the actual stop
    // slippage, rather than inventing either the quote or exact stop price.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      PositionCloseReasonToReturn = PositionCloseReason.Unknown,
      PositionCloseExecutionPriceToReturn = null,
    };
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var positionId = client.StopAmendments.Single().PositionId;
    var stop = client.StopAmendments.Single().StopLoss;
    Assert.True(stop < 4000.0m);

    // Sweep continues well below the protective stop after the position has
    // already vanished from the broker snapshot.
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", stop - 7.0m, stop - 6.8m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    client.RemovePosition(positionId);
    now = Now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_close_execution_price_pending")
    );
    Assert.DoesNotContain(store.Events, item => item.Type == "position_closed");

    var brokerFill = stop - 0.12m;
    client.PositionCloseReasonToReturn = PositionCloseReason.StopLossOrTakeProfit;
    client.PositionCloseExecutionPriceToReturn = brokerFill;
    now = now.AddSeconds(61);
    await WaitForEventAsync(store, "position_closed");

    var closed = store.Events.Single(item => item.Type == "position_closed");
    Assert.Equal("stop_loss_or_take_profit", closed.ReasonCode);
    Assert.Contains("stop loss / take profit", closed.Message);
    Assert.DoesNotContain("unconfirmed", closed.Message);
    Assert.Equal(brokerFill, closed.Price);
    Assert.NotEqual(stop - 7.0m, closed.Price);
    Assert.NotNull(closed.GroupRealizedPips);
    Assert.True(closed.GroupRealizedPips < 0m);
    // Actual broker slippage may exceed the configured stop by a little.
    var maxLossPips = Math.Abs(4000.2m - stop) / 0.1m + 2m;
    Assert.True(Math.Abs(closed.GroupRealizedPips.Value) <= maxLossPips);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MissingCloseFillFallsBackAfterBoundedWaitAndFinalizesState()
  {
    // A missing deal history must not leave a filled position permanently
    // open. The fallback is the last protective stop, explicitly marked
    // unconfirmed, never a later live quote.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      PositionCloseReasonToReturn = PositionCloseReason.Unknown,
      PositionCloseExecutionPriceToReturn = null,
    };
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var positionId = client.StopAmendments.Single().PositionId;
    var stop = client.StopAmendments.Single().StopLoss;

    client.RemovePosition(positionId);
    now = Now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_close_execution_price_pending")
    );
    Assert.DoesNotContain(store.Events, item => item.Type == "position_closed");

    // Owner 2026-09-04: the age bound alone is not enough once it drops
    // below the post-quorum retry cadence - a pass is skipped entirely
    // until CloseHistoryRetrySeconds has elapsed since its own last check,
    // regardless of how far past CloseHistoryMaxWaitSeconds the position's
    // age already is. Advance by whichever bound is larger, exactly what
    // production needs to actually reach the fallback check.
    now = now.AddSeconds(Math.Max(
      AutoTradeEngine.CloseHistoryMaxWaitSeconds,
      AutoTradeEngine.CloseHistoryRetrySeconds
    ) + 1);
    await WaitForEventAsync(store, "position_closed");

    var closed = store.Events.Single(item => item.Type == "position_closed");
    Assert.Equal(stop, closed.Price);
    Assert.Null(closed.ReasonCode);
    Assert.Contains("reason unconfirmed", closed.Message);
    Assert.Contains(
      "position_close_execution_price_fallback",
      store.MetricsSnapshot()
    );
    Assert.DoesNotContain(positionId, store.Positions.Keys);
    Assert.DoesNotContain(positionId, store.PositionMissing.Keys);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MissingCloseHistoryDoesNotBlockAnotherMissingPosition()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      PositionCloseReasonToReturn = PositionCloseReason.Unknown,
      PositionCloseExecutionPriceToReturn = null,
    };
    client.PositionCloseLookupOverrides[92] = new PositionCloseLookup(
      PositionCloseReason.ManualOrExternalOrder,
      4001.2m
    );
    client.SeedPosition(new TradingPosition(
      91, Symbol.SymbolId, TradeDirection.Buy, 400, 4000.2m, 3993.7m,
      Options().Label,
      "av3|aaaaaaaaaa|aaaaaaaaaa|1|400|400|30|1|1000"
    ));
    client.SeedPosition(new TradingPosition(
      92, Symbol.SymbolId, TradeDirection.Buy, 400, 4001.2m, 3994.7m,
      Options().Label,
      "av3|bbbbbbbbbb|bbbbbbbbbb|1|400|400|30|1|1000"
    ));
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitUntilAsync(() => store.Positions.Count == 2);

    client.RemovePosition(91);
    client.RemovePosition(92);
    now = Now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitForEventAsync(store, "position_closed");

    var secondClosed = Assert.Single(
      store.Events,
      item => item.Type == "position_closed" && item.PositionId == 92
    );
    Assert.Equal(4001.2m, secondClosed.Price);
    Assert.Equal("manual_or_external_close", secondClosed.ReasonCode);
    Assert.Contains(91, store.Positions.Keys);

    // See MissingCloseFillFallsBackAfterBoundedWaitAndFinalizesState for why
    // this must be the larger of the two bounds, not just the age bound.
    now = now.AddSeconds(Math.Max(
      AutoTradeEngine.CloseHistoryMaxWaitSeconds,
      AutoTradeEngine.CloseHistoryRetrySeconds
    ) + 1);
    await WaitUntilAsync(() =>
      store.Events.Any(item => item.Type == "position_closed" && item.PositionId == 91)
    );
    Assert.DoesNotContain(91, store.Positions.Keys);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task LabelMismatchDoesNotConfirmMissingWhilePositionIdStillOpen()
  {
    // Broker Label drifted off options.Label but PositionId is still on the
    // symbol — must keep managing, not treat as snapshot-missing.
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
    var tracked = store.Positions[positionId];

    client.RemovePosition(positionId);
    client.SeedPosition(new TradingPosition(
      tracked.PositionId,
      Symbol.SymbolId,
      tracked.Direction,
      tracked.RemainingVolume,
      tracked.EntryPrice,
      tracked.CurrentStopLoss ?? tracked.EntryPrice,
      "",
      tracked.CandidateId
    ));
    now = Now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("tracked_position_label_mismatch_recovered")
    );
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await Task.Delay(100, cts.Token);

    Assert.Contains(positionId, store.Positions.Keys);
    Assert.DoesNotContain(
      "position_missing_snapshot_suspected",
      store.MetricsSnapshot()
    );
    Assert.DoesNotContain(
      "position_missing_snapshot_confirmed",
      store.MetricsSnapshot()
    );
    Assert.DoesNotContain(store.Events, item => item.Type == "position_closed");
    Assert.Contains(
      "tracked_position_label_mismatch_recovered",
      store.MetricsSnapshot()
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ConfirmedMissingPositionAwayFromStopKeepsAmbiguousMessage()
  {
    // Lookup recovered a fill far from the protective stop — do not guess
    // SL/TP; keep the ambiguous message.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient
    {
      PositionCloseReasonToReturn = PositionCloseReason.Unknown,
      PositionCloseExecutionPriceToReturn = 4009.35m,
    };
    var engine = new AutoTradeEngine(Options(), store, () => now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var positionId = client.StopAmendments.Single().PositionId;
    var stop = client.StopAmendments.Single().StopLoss;
    Assert.NotEqual(4009.35m, stop);

    client.RemovePosition(positionId);
    now = Now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitForEventAsync(store, "position_closed");

    var closed = store.Events.Single(item => item.Type == "position_closed");
    Assert.Null(closed.ReasonCode);
    Assert.Contains("reason unconfirmed", closed.Message);
    Assert.Equal(4009.35m, closed.Price);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task FullSlHitWithoutAnyTpReportsSlReason()
  {
    // Range-box / single-tranche SL out before any target books — the case
    // that previously printed "reason unconfirmed" for a clean broker SL.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var now = Now;
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 88,
      timeframe: "M5"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      Options() with
      {
        RangeFlipEnabled = false,
        RangeTargetsPips = [20, 30, 40, 50, 70, 88],
      },
      store,
      () => now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var state = Assert.Single(store.Positions.Values);
    Assert.Equal(0, state.NextTargetIndex);
    var stop = Assert.NotNull(state.CurrentStopLoss ?? state.InitialStopLoss);
    var positionId = state.PositionId;

    client.PositionCloseReasonToReturn = PositionCloseReason.Unknown;
    client.PositionCloseExecutionPriceToReturn = stop;
    client.RemovePosition(positionId);
    now = now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitForEventAsync(store, "position_closed");

    var closed = store.Events.Single(item => item.Type == "position_closed");
    Assert.Equal("stop_loss_or_take_profit", closed.ReasonCode);
    Assert.Contains("stop loss / take profit", closed.Message);
    Assert.DoesNotContain("unconfirmed", closed.Message);
    Assert.NotNull(closed.GroupRealizedPips);
    Assert.True(closed.GroupRealizedPips < 0);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task BeStopOutAfterTp2ReportsAchievedTargetAndSlReason()
  {
    // TP1 + TP2 booked, protective stop trailed to BE, then broker SL hits.
    // Close-reason lookup may stay Unknown, but exit-at-stop after booked
    // targets must not read as "unconfirmed" / diluted VWAP Total.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(8));
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

    var state = Assert.Single(store.Positions.Values);
    var positionId = state.PositionId;
    Assert.Equal(new[] { 30, 60, 90, 120, 200 }, state.TargetsPips.ToArray());

    // Book TP1 (~30p) — moves protective stop to BE+buffer.
    await engine.ObserveSpotAsync(
      new SpotPrice(
        "XAU",
        state.EntryPrice + 3.1m,
        state.EntryPrice + 3.3m,
        now.ToUnixTimeSeconds()
      ),
      cts.Token
    );
    await WaitUntilAsync(() =>
      store.Events.Count(item => item.Type == "take_profit") >= 1
    );
    state = Assert.Single(store.Positions.Values);
    Assert.Equal(1, state.NextTargetIndex);
    Assert.NotNull(state.CurrentStopLoss);

    // Book TP2 (~60p).
    await engine.ObserveSpotAsync(
      new SpotPrice(
        "XAU",
        state.EntryPrice + 6.1m,
        state.EntryPrice + 6.3m,
        now.ToUnixTimeSeconds()
      ),
      cts.Token
    );
    await WaitUntilAsync(() =>
      store.Events.Count(item => item.Type == "take_profit") >= 2
    );
    state = Assert.Single(store.Positions.Values);
    Assert.Equal(2, state.NextTargetIndex);
    var beStop = Assert.NotNull(state.CurrentStopLoss);

    // Remaining runner disappears at the BE stop; order-type lookup fails.
    client.PositionCloseReasonToReturn = PositionCloseReason.Unknown;
    client.PositionCloseExecutionPriceToReturn = beStop;
    client.RemovePosition(positionId);
    now = now.AddSeconds(16);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_missing_snapshot_suspected")
    );
    await Task.Delay(50, cts.Token);
    now = now.AddSeconds(16);
    await WaitForEventAsync(store, "position_closed");

    var closed = store.Events.Single(item => item.Type == "position_closed");
    Assert.Equal("stop_loss_or_take_profit", closed.ReasonCode);
    Assert.Contains("stop loss / take profit", closed.Message);
    Assert.DoesNotContain("unconfirmed", closed.Message);
    // Highest booked target is TP2 = 60, not the VWAP diluted by BE residual.
    Assert.Equal(60m, closed.GroupRealizedPips);
    var result = Assert.Single(
      store.Events,
      item => item.Type == "group_result"
    );
    Assert.Equal(60m, result.GroupRealizedPips);

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
  [InlineData("SELL")]
  [InlineData("BUY")]
  public async Task RejectsIndependentInitialGroupOnNonHedgedAccountBeforeSubmission(
    string incomingDirection
  )
  {
    // Incident regression: a non-hedged (netting) broker cannot hold two
    // independent SL/TP plans as separate positions - a second autonomous
    // initial group, same direction or opposite, would collapse into the
    // existing net position. Covers both directions since the existing
    // opposite-direction guard (TryRejectOppositeInitialGroupAsync) could
    // otherwise mask a same-direction gap in this one.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var first = CandidateJson(direction: "SELL", candidate: 'a');
    var store = new FakeAutoTradeStore(first);
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { AccountType = "Netted" },
    };
    var engine = new AutoTradeEngine(DemoEvalOptions(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    store.EnqueueCandidate(CandidateJson(direction: incomingDirection, candidate: 'b'));
    await WaitForEventAsync(store, "rejected");

    Assert.Single(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.ReasonCode == "independent_strategy_requires_hedged_account"
    );
    Assert.Contains("independent_group_rejected_non_hedged", store.Metrics);
    // Group A's own tracked state must be untouched by the rejected intake.
    var groupA = Assert.Single(store.Positions.Values);
    Assert.Equal(TradeDirection.Sell, groupA.Direction);
    Assert.Equal(new string('a', 64), groupA.CandidateId);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task LegacyScaleInWithParentGroupIdIsUnaffectedByNonHedgedIndependentGroupGuard()
  {
    // A ParentGroupId-linked add must keep working on a non-hedged account -
    // it already shares the parent's broker position by design, so it is
    // not "independent" in the sense the new guard is protecting against.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var first = CandidateJson(direction: "SELL", candidate: 'a');
    var store = new FakeAutoTradeStore(first);
    var client = new FakeTradingClient
    {
      Account = ValidAccount() with { AccountType = "Netted" },
    };
    var engine = new AutoTradeEngine(DemoEvalOptions(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var groupId = Assert.Single(
      store.Positions.Values.Select(item => item.GroupId).Distinct()
    );
    store.EnqueueCandidate(CandidateJson(
      direction: "SELL", candidate: 'b', parentGroupId: groupId
    ));
    await Task.Delay(200, cts.Token);

    Assert.DoesNotContain(store.Events, item =>
      item.Type == "rejected"
      && item.ReasonCode == "independent_strategy_requires_hedged_account"
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task UnexpectedSharedPositionIdDoesNotOverwriteTheExistingGroup()
  {
    // Even on a hedged account (where independent groups are otherwise
    // expected to work), the executor must protect against the broker
    // unexpectedly returning an already-tracked PositionId for a second,
    // genuinely distinct independent group - it must never silently become
    // a scale-in of the first, and no new autonomous group may be admitted
    // until a human resolves the conflict.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var first = CandidateJson(direction: "SELL", candidate: 'a');
    var store = new FakeAutoTradeStore(first);
    var client = new FakeTradingClient();
    // A genuinely hedged account with concurrent strategies allowed - this
    // is exactly the case the task calls out: "even on an account reported
    // as hedged", the executor still must not silently corrupt tracking if
    // the broker reuses a PositionId.
    var options = Options() with
    {
      AllowConcurrentStrategies = true,
      AllowHedgedXau = true,
    };
    var engine = new AutoTradeEngine(options, store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var positionId = client.StopAmendments.Single().PositionId;
    var groupAEntry = store.Positions[positionId].EntryPrice;
    var groupAId = store.Positions[positionId].GroupId;

    // Simulate the broker recycling the ticket number: it no longer reports
    // Group A's position (so the fake client's own fill bookkeeping stays
    // internally consistent), but the executor's own state/store was never
    // told Group A closed - exactly the "unexpected" half of this scenario.
    client.RemovePosition(positionId);
    client.NextPositionId = positionId;
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    store.EnqueueCandidate(CandidateJson(direction: "SELL", candidate: 'b'));
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("position_state_conflict")
    );

    // Group A's own tracked state must survive completely unchanged.
    Assert.Equal(groupAEntry, store.Positions[positionId].EntryPrice);
    Assert.Equal(groupAId, store.Positions[positionId].GroupId);
    Assert.Contains(store.Events, item =>
      item.Type == "error"
      && item.ReasonCode == "broker_position_identity_group_conflict"
    );
    // No "opened" event may claim the second fill is safely managed.
    Assert.DoesNotContain(store.Events, item =>
      item.Type == "opened" && item.CandidateId == new string('b', 64)
    );

    // A third, otherwise-unrelated autonomous candidate must be rejected
    // outright while the conflict is unresolved.
    store.EnqueueCandidate(CandidateJson(direction: "BUY", candidate: 'c'));
    await WaitUntilAsync(() => store.Events.Any(item =>
      item.Type == "rejected"
      && item.ReasonCode == "broker_position_identity_group_conflict"
      && item.CandidateId == new string('c', 64)
    ));

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Theory]
  [InlineData("Trend Pullback", "trend")]
  [InlineData("Mapped Zone Reaction", "mapped_zone")]
  [InlineData("Zone Reaction", "supply_demand")]
  [InlineData("Demand Zone Reaction", "supply_demand")]
  [InlineData("Key Level Reaction", "key_level")]
  [InlineData("Session Level Reaction", "session_level")]
  [InlineData("Trendline Reaction", "trendline")]
  [InlineData("Supply Zone Reaction", "supply_demand")]
  public async Task DemoEvalRejectsRangeBoxOppositeAnotherStrategy(
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
    await WaitForEventAsync(store, "rejected");

    Assert.Contains(client.Orders, item => item.Direction == TradeDirection.Buy);
    Assert.DoesNotContain(
      client.Orders,
      item => item.Direction == TradeDirection.Sell
    );
    Assert.Single(
      store.Positions.Values.Select(item => item.GroupId).Distinct()
    );
    var lifecycle = store.LifecycleEvents
      .Where(item => item.CandidateId == new string('r', 64))
      .Select(item => item.State)
      .ToArray();
    Assert.Contains("executor_received", lifecycle);
    Assert.Contains(
      "executor_opposite_initial_rejected",
      store.Metrics
    );
    Assert.Contains(
      store.Events,
      item => item.Type == "rejected"
        && item.Message.Contains("opposite autonomous initial group")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task DemoEvalTrendBuyRejectsSupplyReactionSellAcrossRestart()
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
    await WaitForEventAsync(store, "rejected");

    var beforeRestart = store.Positions.Values
      .OrderBy(state => state.Direction)
      .Select(state => (
        state.Direction,
        state.GroupId,
        state.Setup,
        state.CurrentStopLoss
      ))
      .ToArray();
    Assert.Single(beforeRestart.Select(item => item.GroupId).Distinct());
    Assert.Contains(beforeRestart, item =>
      item.Direction == TradeDirection.Buy
      && item.Setup == "Trend Pullback"
    );
    Assert.DoesNotContain(
      beforeRestart,
      item => item.Direction == TradeDirection.Sell
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
    Assert.Single(client.Orders);

    restartCts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => restartRun);
  }

  [Fact]
  public async Task DemoEvalRejectsSellRangeWhileBuyRangeIsActive()
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
    await WaitForEventAsync(store, "rejected");

    Assert.Contains(store.Positions.Values, item =>
      item.GroupId == "range-buy" && item.Direction == TradeDirection.Buy
    );
    Assert.DoesNotContain(
      store.Positions.Values,
      item => item.Direction == TradeDirection.Sell
    );
    Assert.DoesNotContain(client.Closes, item => item.PositionId == 77);
    Assert.Contains("executor_opposite_initial_rejected", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task DemoEvalRejectsBuyRangeWithoutAmendingExistingSellGroup()
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
    await WaitForEventAsync(store, "rejected");

    Assert.Contains(store.Positions.Values, item =>
      item.GroupId == "range-sell"
      && item.Direction == TradeDirection.Sell
      && item.CurrentStopLoss == 4014.5m
    );
    Assert.DoesNotContain(
      store.Positions.Values,
      item => item.Direction == TradeDirection.Buy
    );
    Assert.DoesNotContain(client.StopAmendments, item => item.PositionId == 77);
    Assert.DoesNotContain(client.Closes, item => item.PositionId == 77);
    Assert.Contains("executor_opposite_initial_rejected", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task DemoEvalRejectsOppositeInitialWhilePendingOrderExists()
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
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Contains(client.PendingOrders, item => item.OrderId == 55);
    Assert.Contains(
      store.Events,
      item => item.Type == "rejected"
        && item.Message.Contains("opposite autonomous initial group")
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

  [Fact]
  public async Task TransientSessionFaultPublishesDegradedRetryingNotFatal()
  {
    // P1-6: a Redis/network/broker transient error is actively retried by
    // FeedRunner.RunAutoTradeSafelyAsync - HandleSessionFaultAsync must not
    // report "fatal" for something that is, by construction, about to be
    // retried. Only a genuine config/contract incompatibility is fatal.
    var store = new FakeAutoTradeStore(CandidateJson());
    var engine = new AutoTradeEngine(DemoEvalOptions(), store, () => Now, _ => { });

    await engine.HandleSessionFaultAsync(
      new IOException("simulated transient redis failure"), CancellationToken.None
    );

    var readiness = JsonDocument.Parse(
      store.Values[AutoTradeConfigHealth.ReadinessKey]
    ).RootElement;
    Assert.False(readiness.GetProperty("ready").GetBoolean());
    Assert.Equal("degraded_retrying", readiness.GetProperty("state").GetString());
    Assert.Empty(readiness.GetProperty("fatal").EnumerateArray());
    Assert.Contains(
      readiness.GetProperty("warnings").EnumerateArray(),
      item => item.GetString() == "broker_or_redis_connection"
    );
  }

  [Fact]
  public async Task ConfigurationSessionFaultStillPublishesFatal()
  {
    var store = new FakeAutoTradeStore(CandidateJson());
    var engine = new AutoTradeEngine(DemoEvalOptions(), store, () => Now, _ => { });

    await engine.HandleSessionFaultAsync(
      new AutoTradeConfigurationException("Auto trade disabled: config mismatch"),
      CancellationToken.None
    );

    var readiness = JsonDocument.Parse(
      store.Values[AutoTradeConfigHealth.ReadinessKey]
    ).RootElement;
    Assert.False(readiness.GetProperty("ready").GetBoolean());
    Assert.Equal("fatal", readiness.GetProperty("state").GetString());
    Assert.Contains(
      readiness.GetProperty("fatal").EnumerateArray(),
      item => item.GetString() == "service_initialization"
    );
  }

  [Fact]
  public async Task WarningDoesNotCreateOrChangeLifecycleState()
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

    Assert.Contains(store.Events, item => item.Type == "warning");
    Assert.All(
      store.LifecycleEvents.Where(item => item.Type == "warning"),
      item =>
      {
        Assert.False(item.MutatesLifecycle);
        Assert.Null(item.State);
      }
    );
    Assert.DoesNotContain(
      store.Values.Keys,
      key => key == "auto_trade:lifecycle_state:service"
    );
    Assert.Contains("lifecycle_telemetry_no_transition", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ConfigHealthDoesNotSetServiceManagingLifecycle()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "ready");

    Assert.Contains(store.Events, item => item.Type == "config_health");
    Assert.Contains(store.Events, item => item.Type == "account_capability");
    Assert.DoesNotContain(
      store.Values,
      pair => pair.Key == "auto_trade:lifecycle_state:service"
    );
    Assert.Contains("lifecycle_telemetry_no_transition", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task UnknownEventRecordsHistoryWithoutLifecycleSnapshot()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var candidateId = new string('a', 64);
    var store = new FakeAutoTradeStore(CandidateJson(candidate: 'a'));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    var stateBeforeTelemetry = LifecycleStateFor(store, candidateId);
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4003.2m, 4003.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    Assert.Contains(store.LifecycleEvents, item => item.Type == "stop_moved");
    Assert.DoesNotContain(
      store.LifecycleEvents.Where(item => item.Type == "stop_moved"),
      item => item.MutatesLifecycle
    );
    // Telemetry must not reopen or invent a different owner state; mapped
    // take-profit transitions may still advance the same candidate.
    Assert.NotEqual("managing", LifecycleStateFor(store, candidateId));
    Assert.NotNull(stateBeforeTelemetry);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MappedLifecycleTransitionsAdvanceCandidateState()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var candidateId = new string('a', 64);
    var store = new FakeAutoTradeStore(CandidateJson(candidate: 'a'));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var states = store.LifecycleEvents
      .Where(item => item.CandidateId == candidateId && item.MutatesLifecycle)
      .Select(item => item.State)
      .Where(state => !string.IsNullOrWhiteSpace(state))
      .Distinct()
      .ToArray();
    Assert.Contains("executor_received", states);
    Assert.Contains("order_submitted", states);
    Assert.Contains("order_filled", states);
    var current = LifecycleStateFor(store, candidateId);
    Assert.True(
      current is "order_filled" or "managing",
      $"expected order_filled or managing, got {current}"
    );

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4003.2m, 4003.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.Contains(
      store.LifecycleEvents.Where(item => item.CandidateId == candidateId),
      item => item.Type == "take_profit" && item.State == "partially_closed"
    );

    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4020.2m, 4020.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    Assert.Equal("closed", LifecycleStateFor(store, candidateId));

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RejectedCandidateIgnoresLaterTelemetryLifecycleMutation()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var candidateId = new string('b', 64);
    var store = new FakeAutoTradeStore(CandidateJson(
      candidate: 'b',
      createdAt: 2_000,
      barTs: 2_000
    ));
    store.SeedPublishedCandidate(new string('a', 64));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Equal("rejected", LifecycleStateFor(store, candidateId));
    var rejectedEvents = store.LifecycleEvents
      .Where(item => item.CandidateId == candidateId)
      .ToArray();
    var postRejectIndex = Array.FindIndex(
      rejectedEvents,
      item => item.Type == "rejected"
    );
    Assert.All(
      rejectedEvents.Skip(postRejectIndex + 1),
      item => Assert.False(item.MutatesLifecycle)
    );
    Assert.Equal("rejected", LifecycleStateFor(store, candidateId));
    Assert.DoesNotContain(
      store.Values.Keys,
      key => key == "auto_trade:lifecycle_state:service"
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ClosedCandidateIgnoresLaterTelemetryLifecycleMutation()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var candidateId = new string('a', 64);
    var store = new FakeAutoTradeStore(CandidateJson(candidate: 'a'));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4020.2m, 4020.4m, Now.ToUnixTimeSeconds()),
      cts.Token
    );
    await WaitUntilAsync(() => LifecycleStateFor(store, candidateId) == "closed");

    var closedIndex = Array.FindLastIndex(
      store.LifecycleEvents
        .Where(item => item.CandidateId == candidateId)
        .ToArray(),
      item => item.State == "closed"
    );
    Assert.True(closedIndex >= 0);
    var laterMutations = store.LifecycleEvents
      .Where(item => item.CandidateId == candidateId)
      .Skip(closedIndex + 1)
      .Where(item => item.MutatesLifecycle)
      .ToArray();
    Assert.Empty(laterMutations);
    Assert.Equal("closed", LifecycleStateFor(store, candidateId));

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RangeSideUpdatesOnlyForMappedLifecycleTransitions()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(BoxCandidateJson(
      fullTpPips: 50,
      direction: "BUY",
      candidate: 'r',
      groupId: "range-buy",
      strategyFamily: "range"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Contains(store.RangeSides, item => item.State == "ORDER_SUBMITTED");
    Assert.Contains(store.RangeSides, item => item.State == "ORDER_FILLED");
    Assert.DoesNotContain(store.RangeSides, item => item.State == "WARNING");
    Assert.DoesNotContain(store.RangeSides, item => item.State == "STOP_MOVED");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ReconcilesOrphanedGroupPlanFilledAndClosedDuringRestartGap()
  {
    // Owner-reported 2026-09-04: two real manual /algo signals filled AND
    // stopped out while a redeploy had this engine mid-restart - the
    // previous instance never got the chance to publish the fill/close
    // events, and nothing before this fix could ever discover what
    // happened. A still-persisted AutoTradeGroupPlan with no _states entry
    // and no live position/pending order for its ClientOrderIds is exactly
    // that gap - the startup scan must recover it from broker history.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    const long positionId = 555_001;
    var client = new FakeTradingClient();
    client.HistoricalOrders.Add(new HistoricalOrderMatch(
      "av-orphan-leg1", Filled: true, positionId, Symbol.SymbolId, ExecutedVolume: 100
    ));
    client.ClosingDealsByPosition[positionId] =
    [
      new ClosingDeal(
        EntryPrice: 4000.0m, ExitPrice: 3990.0m, ClosedVolume: 100, ExecutionTimestamp: 900_000
      ),
    ];
    await store.SaveGroupPlanAsync(
      new AutoTradeGroupPlan(
        CandidateId: "manual:900:0",
        GroupId: "manual:900",
        MatchId: null,
        StrategyFamily: "manual",
        RangeId: null,
        Setup: "Confluence Zone",
        Direction: "SELL",
        CreatedAt: 900,
        ClientOrderIds: ["av-orphan-leg1"],
        SubmittedAt: 900
      ),
      TimeSpan.FromDays(1),
      cts.Token
    );

    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    engine.BindInstrumentSymbols([Symbol]);
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "group_result");

    var reconciled = Assert.Single(
      store.Events, item => item.Type == "group_result" && item.GroupId == "manual:900"
    );
    Assert.Equal("manual:900:0", reconciled.CandidateId);
    Assert.Equal(100m, reconciled.GroupRealizedPips);
    Assert.Equal(100, reconciled.GroupInitialVolume);
    Assert.Equal(4000.0m, reconciled.LegEntryPrice);
    Assert.Equal("SELL", reconciled.Direction);
    Assert.Equal("orphaned_group_plan_reconciled", reconciled.ReasonCode);
    Assert.False(store.Values.ContainsKey("auto_trade:group_plan:manual:900"));

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ReconcilesOrphanedGroupPlanUsingTheDeepestLegEntryNotTheFirstIterated()
  {
    // Owner-reported 2026-09-10 (signal 300, real XAU BUY): a manual /algo
    // group's shallow leg (worse entry) and deep leg (better entry) can
    // both fill and close during a restart gap. The reconciled group_result
    // must report the group's single most favorable (deepest) fill as
    // LegEntryPrice, not simply whichever leg's closing deal the broker
    // history happened to return first - the shallow leg is seeded first
    // here specifically to prove the old "first deal wins" behavior no
    // longer applies.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    const long shallowPositionId = 555_101;
    const long deepPositionId = 555_102;
    var client = new FakeTradingClient();
    client.HistoricalOrders.Add(new HistoricalOrderMatch(
      "av-orphan-shallow", Filled: true, shallowPositionId, Symbol.SymbolId, ExecutedVolume: 1000
    ));
    client.HistoricalOrders.Add(new HistoricalOrderMatch(
      "av-orphan-deep", Filled: true, deepPositionId, Symbol.SymbolId, ExecutedVolume: 200
    ));
    // Shallow leg: entry 4008.0, closed at TP1 (4011.0) - listed FIRST so a
    // regression back to "first deal wins" would surface as 4008.0 below.
    client.ClosingDealsByPosition[shallowPositionId] =
    [
      new ClosingDeal(
        EntryPrice: 4008.0m, ExitPrice: 4011.0m, ClosedVolume: 1000, ExecutionTimestamp: 900_000
      ),
    ];
    // Deep leg: a materially better entry (4006.25), closed later/deeper.
    client.ClosingDealsByPosition[deepPositionId] =
    [
      new ClosingDeal(
        EntryPrice: 4006.25m, ExitPrice: 4020.0m, ClosedVolume: 200, ExecutionTimestamp: 900_500
      ),
    ];
    await store.SaveGroupPlanAsync(
      new AutoTradeGroupPlan(
        CandidateId: "manual:300:0",
        GroupId: "manual:300",
        MatchId: null,
        StrategyFamily: "manual",
        RangeId: null,
        Setup: "Confluence Zone",
        Direction: "BUY",
        CreatedAt: 900,
        ClientOrderIds: ["av-orphan-shallow", "av-orphan-deep"],
        SubmittedAt: 900
      ),
      TimeSpan.FromDays(1),
      cts.Token
    );

    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    engine.BindInstrumentSymbols([Symbol]);
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "group_result");

    var reconciled = Assert.Single(
      store.Events, item => item.Type == "group_result" && item.GroupId == "manual:300"
    );
    Assert.Equal(4006.25m, reconciled.LegEntryPrice);
    Assert.Equal("BUY", reconciled.Direction);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task LeavesOrphanCandidatePlanAloneWhenBrokerNeverFilledIt()
  {
    // A plan whose legs never reached the broker's history at all (still
    // genuinely pending, or cancelled/expired/rejected) must not be
    // guessed at - no event, and the plan stays for a later restart's scan.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    // No HistoricalOrders seeded - FindHistoricalOrdersAsync reports nothing.
    await store.SaveGroupPlanAsync(
      new AutoTradeGroupPlan(
        CandidateId: "manual:901:0",
        GroupId: "manual:901",
        MatchId: null,
        StrategyFamily: "manual",
        RangeId: null,
        Setup: "Confluence Zone",
        Direction: "SELL",
        CreatedAt: 900,
        ClientOrderIds: ["av-never-filled"],
        SubmittedAt: 900
      ),
      TimeSpan.FromDays(1),
      cts.Token
    );

    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "ready");

    Assert.DoesNotContain(store.Events, item => item.Type == "group_result");
    Assert.True(store.Values.ContainsKey("auto_trade:group_plan:manual:901"));

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task SkipsGroupPlanWhoseLegIsStillOpenRightNow()
  {
    // A leg still currently open belongs to the existing broker-position
    // self-adoption path, not this scan - investigating it here would
    // duplicate (or race) that path instead of deferring to it.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    client.SeedPosition(new TradingPosition(
      555_002, Symbol.SymbolId, TradeDirection.Sell, 100, 4000.0m, 4010.0m,
      "apexvoid-auto", "manual:902", "av-still-open"
    ));
    // Even though history also shows it filled+"closed", the live snapshot
    // is authoritative - this must never fire from stale/duplicate history.
    client.HistoricalOrders.Add(new HistoricalOrderMatch(
      "av-still-open", Filled: true, 555_002, Symbol.SymbolId, ExecutedVolume: 100
    ));
    await store.SaveGroupPlanAsync(
      new AutoTradeGroupPlan(
        CandidateId: "manual:902:0",
        GroupId: "manual:902",
        MatchId: null,
        StrategyFamily: "manual",
        RangeId: null,
        Setup: "Confluence Zone",
        Direction: "SELL",
        CreatedAt: 900,
        ClientOrderIds: ["av-still-open"],
        SubmittedAt: 900
      ),
      TimeSpan.FromDays(1),
      cts.Token
    );

    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "ready");

    Assert.DoesNotContain(store.Events, item => item.Type == "group_result");
    Assert.True(store.Values.ContainsKey("auto_trade:group_plan:manual:902"));

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  private static string? LifecycleStateFor(
    FakeAutoTradeStore store,
    string owner
  )
  {
    if (!store.Values.TryGetValue(
      $"auto_trade:lifecycle_state:{owner}",
      out var raw
    ))
    {
      return null;
    }
    return AutoTradeLifecycle.ParseState(raw);
  }

  private static async Task WaitForEventAsync(FakeAutoTradeStore store, string type)
  {
    for (var attempt = 0; attempt < 200; attempt += 1)
    {
      if (store.Events.Any(item => item.Type == type))
      {
        return;
      }
      await Task.Delay(10);
    }
    var outcomes = string.Join(
      " | ",
      store.Events
        .Where(item => item.Type is "rejected" or "error" or "add")
        .Select(item => $"{item.Type}: {item.Message}")
    );
    throw new Xunit.Sdk.XunitException(
      $"Timed out waiting for '{type}'. Outcomes: {outcomes}"
    );
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
    BreakEvenBufferTicks: 6,
    CandidateMaxAgeSeconds: 90,
    SpotMaxAgeSeconds: 5,
    MaxSpreadPips: 5,
    MaxEntryDistancePips: 10,
    MinConfluence: 2,
    PollMilliseconds: 10,
    CandidateStream: "auto_trade:candidates",
    EventStream: "auto_trade:events",
    Label: "apexvoid-auto",
    ManualAlgoEnabled: true,
    // Quorum requires two time-separated snapshots. The interval must be
    // positive (validation fails closed on zero); recovery tests avoid wall-
    // clock sleeps through the fake store's absence clock and RecoveryDelay.
    BrokerAbsenceConfirmations: 2,
    BrokerAbsenceRecheckSeconds: 1,
    BrokerRecoveryTimeoutSeconds: 30
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
    Balance: 2_000m,
    Equity: 2_000m,
    SnapshotTimestamp: 0
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
    string? parentGroupId = null,
    decimal? riskMultiplier = 1.0m
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
    risk_multiplier = riskMultiplier,
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
    string? parentGroupId = null,
    string? orderTypePreference = null,
    string? entryDistribution = null,
    decimal? riskMultiplier = 1.0m,
    string? targetModel = null,
    decimal? absoluteTargetPrice = null,
    bool stopContract = false,
    bool stopMismatch = false
  ) => JsonSerializer.Serialize(new
  {
    version = stopContract ? 5 : 3,
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
    risk_multiplier = riskMultiplier,
    order_type_preference = orderTypePreference,
    entry_distribution = entryDistribution,
    target_model = targetModel,
    absolute_target_price = absoluteTargetPrice,
    regime,
    parent_group_id = parentGroupId,
    planned_stop_entry_price = stopContract ? 4000.2m : (decimal?)null,
    planned_stop_price = stopContract
      ? (stopMismatch ? 3994.70m : 3994.20m)
      : (decimal?)null,
    planned_stop_distance = stopContract ? 6.0m : (decimal?)null,
    planned_stop_pips = stopContract ? 60m : (decimal?)null,
    planned_stop_raw_price = stopContract ? 3993.2m : (decimal?)null,
    planned_stop_clamped = stopContract ? true : (bool?)null,
    stop_source = stopContract ? "structure" : null,
    stop_plan_version = stopContract ? 1 : (int?)null,
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
    decimal? structuralZoneHigh = null,
    string? orderTypePreference = null,
    decimal entryLow = 3999.5m,
    decimal entryHigh = 4000.5m,
    string? entryDistribution = null,
    decimal? riskMultiplier = 1.0m,
    string? targetModel = null,
    decimal? absoluteTargetPrice = null,
    decimal? structureSwing = null
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
    entry_zone = new { low = entryLow, high = entryHigh },
    confluence = 3,
    reasons = new[] { "scanner detector matched structure" },
    bar_ts = 1_000,
    atr = 1.0,
    structure_swing = structureSwing
      ?? (direction == "BUY" ? 3993.5m : 4006.2m),
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
    order_type_preference = orderTypePreference,
    entry_distribution = entryDistribution,
    risk_multiplier = riskMultiplier,
    target_model = targetModel,
    absolute_target_price = absoluteTargetPrice,
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
    decimal atr = 1.0m,
    decimal? riskMultiplier = 1.0m
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
    risk_multiplier = riskMultiplier,
  });
  }

  // Mirrors algo-bot's manual_execution._intent_to_candidate_payload:
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
    decimal? opposingZoneHigh = null,
    bool bypassAnalysisGates = true,
    long? barTs = null,
    bool manualSingleEntry = true,
    int[]? manualTargetWeights = null,
    string symbol = "XAU"
  ) => JsonSerializer.Serialize(new
  {
    version = 3,
    candidate_id = candidateId,
    symbol,
    timeframe = "M1",
    setup,
    mode = "manual_algo",
    bypass_analysis_gates = bypassAnalysisGates,
    direction,
    trigger_ts = "1000",
    created_at = createdAt,
    bar_ts = barTs,
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
    manual_single_entry = manualSingleEntry,
    manual_target_weights = manualTargetWeights,
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
    public int? FailLimitOrderCall { get; init; }
    public int? FailMarketOrderCall { get; init; }
    public int? FailCancelCall { get; init; }
    public PositionCloseReason PositionCloseReasonToReturn { get; set; } =
      PositionCloseReason.Unknown;
    // By default the fake broker history returns a real closing deal. Tests
    // that exercise delayed history explicitly set this to null first.
    public decimal? PositionCloseExecutionPriceToReturn { get; set; } = 4000.2m;
    public decimal CloseExecutionPriceToReturn { get; set; } = 4013.2m;
    public Dictionary<long, PositionCloseLookup> PositionCloseLookupOverrides { get; } = [];
    public List<long> PositionCloseReasonLookups { get; } = [];
    public List<long> PositionCloseOpenedAtTimestamps { get; } = [];
    public Task<PositionCloseLookup> DeterminePositionCloseReasonAsync(
      long positionId,
      long openedAtTimestamp,
      long approximateCloseTimestamp,
      CancellationToken cancellationToken
    )
    {
      PositionCloseReasonLookups.Add(positionId);
      PositionCloseOpenedAtTimestamps.Add(openedAtTimestamp);
      if (PositionCloseLookupOverrides.TryGetValue(positionId, out var lookup))
      {
        return Task.FromResult(lookup);
      }
      return Task.FromResult(
        new PositionCloseLookup(
          PositionCloseReasonToReturn,
          PositionCloseExecutionPriceToReturn
        )
      );
    }
    // "Broker accepted, response never arrived": the order exists at the broker
    // but the caller only sees a transport failure.
    public int? LoseMarketResponseCall { get; init; }
    public int? LoseLimitResponseCall { get; init; }
    // Number of reconcile snapshots after acceptance that omit the accepted
    // position. The next snapshot after that count reveals it.
    public int HideAcceptedMarketPositionsForReconcileCalls { get; set; }
    public bool BlockClose { get; init; }
    // Broker-call gates: the test releases them once it has rearranged lease
    // ownership, which is how a long broker operation is modelled without
    // wall-clock waits.
    public int? PauseMarketOrderCall { get; init; }
    public int? PauseLimitOrderCall { get; init; }
    public TaskCompletionSource<bool> MarketOrderEntered { get; } = new(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    public TaskCompletionSource<bool> ReleaseMarketOrder { get; } = new(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    public TaskCompletionSource<bool> LimitOrderEntered { get; } = new(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    public TaskCompletionSource<bool> ReleaseLimitOrder { get; } = new(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    public TaskCompletionSource<bool> ReconcileFaultEntered { get; } = new(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    public TaskCompletionSource<bool> ReleaseReconcileFault { get; } = new(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    private readonly List<TradingPosition> _positions = [];
    private readonly List<TradingPosition> _hiddenPositions = [];
    private readonly Queue<decimal> _marketExecutionPrices = [];
    private int _amendmentCalls;
    private int _reconcileCalls;
    private int _limitOrderCalls;
    private int _marketOrderCalls;
    private int _cancelCalls;
    private int _hiddenRemainingReconciles;
    private long _nextPositionId = 91;
    private long _nextOrderId = 81;

    // Test-only hook to force the broker's *next* fill to reuse a specific
    // PositionId, simulating the unexpected-identity-reuse scenario tests
    // exercise (a real cTrader account should never do this, but the
    // executor must not silently corrupt tracking if it ever does).
    public long NextPositionId { set => _nextPositionId = value; }

    public void SeedPosition(TradingPosition position) => _positions.Add(position);
    public void EnqueueMarketExecutionPrice(decimal price) =>
      _marketExecutionPrices.Enqueue(price);
    public void RemovePosition(long positionId) =>
      _positions.RemoveAll(position => position.PositionId == positionId);

    public void FillPendingOrder(long orderId, decimal? fillPrice = null)
    {
      var pending = PendingOrders.Single(order => order.OrderId == orderId);
      var request = LimitOrders.Single(order => order.Comment == pending.Comment);
      PendingOrders.Remove(pending);
      var fill = fillPrice ?? request.LimitPrice;
      var distance = request.RelativeStopLoss / 100_000m;
      // cTrader applies RelativeStopLoss from the fill, not the limit.
      var stopLoss = request.Direction == TradeDirection.Buy
        ? fill - distance
        : fill + distance;
      _positions.Add(new TradingPosition(
        _nextPositionId++,
        request.SymbolId,
        request.Direction,
        request.Volume,
        fill,
        stopLoss,
        request.Label,
        request.Comment,
        request.ClientOrderId
      ));
    }

    public Task<IReadOnlyList<TradingAccountGrant>> GetAccountGrantsAsync(
      CancellationToken cancellationToken
    ) => Task.FromResult(Grants);

    public Task<TradingAccountSnapshot> GetFeedAccountAsync(
      CancellationToken cancellationToken
    ) => Task.FromResult(Account);

    // Pre-submit fault injection: this is read after the candidate is claimed
    // but before any broker mutation, so a failure here is a safe retry.
    public Func<int, bool>? FailAccountCall { get; init; }
    public int AccountCalls { get; private set; }

    public Task<TradingAccountSnapshot> GetTradingAccountAsync(
      CancellationToken cancellationToken
    )
    {
      AccountCalls += 1;
      if (FailAccountCall?.Invoke(AccountCalls) == true)
      {
        throw new IOException("simulated account snapshot failure");
      }
      return Task.FromResult(Account);
    }

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
      if (_hiddenPositions.Count > 0)
      {
        if (_hiddenRemainingReconciles > 0)
        {
          _hiddenRemainingReconciles--;
        }
        else
        {
          _positions.AddRange(_hiddenPositions);
          _hiddenPositions.Clear();
        }
      }
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

    // Test-seeded broker "history" for ReconcileOrphanedGroupPlansAsync -
    // real orders/deals the test wants FindHistoricalOrdersAsync /
    // GetClosingDealsAsync to report, independent of the live _positions /
    // PendingOrders snapshot above (which only models what is open right
    // now, never what already closed).
    public List<HistoricalOrderMatch> HistoricalOrders { get; } = [];
    public Dictionary<long, List<ClosingDeal>> ClosingDealsByPosition { get; } = [];

    public Task<IReadOnlyList<HistoricalOrderMatch>> FindHistoricalOrdersAsync(
      long fromTimestampMs,
      long toTimestampMs,
      CancellationToken cancellationToken
    ) => Task.FromResult<IReadOnlyList<HistoricalOrderMatch>>(
      HistoricalOrders.ToArray()
    );

    public Task<IReadOnlyList<ClosingDeal>> GetClosingDealsAsync(
      long positionId,
      long fromTimestampMs,
      long toTimestampMs,
      CancellationToken cancellationToken
    ) => Task.FromResult<IReadOnlyList<ClosingDeal>>(
      ClosingDealsByPosition.TryGetValue(positionId, out var deals)
        ? deals.ToArray()
        : []
    );

    public async Task<TradeExecution> PlaceMarketOrderAsync(
      MarketOrderRequest order,
      CancellationToken cancellationToken
    )
    {
      _marketOrderCalls++;
      if (_marketOrderCalls == PauseMarketOrderCall)
      {
        MarketOrderEntered.TrySetResult(true);
        await ReleaseMarketOrder.Task.WaitAsync(cancellationToken);
      }
      if (_marketOrderCalls == FailMarketOrderCall)
      {
        throw new IOException("simulated market placement response loss");
      }
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
      var accepted = new TradingPosition(
        positionId,
        order.SymbolId,
        order.Direction,
        order.Volume,
        fill,
        stopLoss,
        order.Label,
        order.Comment,
        order.ClientOrderId
      );
      if (
        _marketOrderCalls == LoseMarketResponseCall
        && HideAcceptedMarketPositionsForReconcileCalls > 0
      )
      {
        _hiddenPositions.Add(accepted);
        _hiddenRemainingReconciles = HideAcceptedMarketPositionsForReconcileCalls;
        throw new IOException("simulated market response loss after acceptance");
      }
      _positions.Add(accepted);
      if (_marketOrderCalls == LoseMarketResponseCall)
      {
        throw new IOException("simulated market response loss after acceptance");
      }
      return new TradeExecution(
        positionId,
        orderId,
        fill,
        order.Volume
      );
    }

    public async Task<long> PlaceLimitOrderAsync(
      LimitOrderRequest order,
      CancellationToken cancellationToken
    )
    {
      _limitOrderCalls++;
      if (_limitOrderCalls == PauseLimitOrderCall)
      {
        LimitOrderEntered.TrySetResult(true);
        await ReleaseLimitOrder.Task.WaitAsync(cancellationToken);
      }
      if (_limitOrderCalls == FailLimitOrderCall)
      {
        throw new IOException("simulated limit placement failure");
      }
      LimitOrders.Add(order);
      var orderId = _nextOrderId++;
      PendingOrders.Add(new TradingPendingOrder(
        orderId,
        order.SymbolId,
        order.Direction,
        order.Volume,
        order.LimitPrice,
        order.Label,
        order.Comment,
        order.ClientOrderId
      ));
      if (_limitOrderCalls == LoseLimitResponseCall)
      {
        throw new IOException("simulated limit response loss after acceptance");
      }
      return orderId;
    }

    public Task CancelPendingOrderAsync(
      long orderId,
      CancellationToken cancellationToken
    )
    {
      _cancelCalls++;
      if (_cancelCalls == FailCancelCall)
      {
        throw new IOException("simulated cancellation response loss");
      }
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
        CloseExecutionPriceToReturn,
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
    private readonly Dictionary<string, (string StreamEventId, string Token)> _candidateLeases =
      [];
    private readonly Dictionary<string, int> _candidateAttempts = [];
    private readonly HashSet<string> _expiredLeases = [];
    // Leases created by recovery claims: a live recovery heartbeat must block
    // a second recovery owner.
    private readonly HashSet<string> _recoveryLeases = [];
    // Durable absence-confirmation progress per candidate.
    private readonly Dictionary<
      string,
      (string StreamEventId, int Confirmations, long LastCheckAt)
    > _absenceProgress = [];
    private readonly List<string> _payloads = [payload];
    private readonly List<string> _commandPayloads = [];
    public Dictionary<long, AutoTradePositionState> Positions { get; } = [];
    // The engine session publishes from async callbacks while tests poll these
    // collections. Enumerate a stable snapshot so a producer cannot mutate a
    // List<T> midway through a LINQ assertion.
    public SnapshotList<AutoTradeEvent> Events { get; } = new();
    public SnapshotList<AutoTradeEvent> LifecycleEvents { get; } = new();
    public Dictionary<string, string> Values { get; } = [];
    public Dictionary<string, TimeSpan> ValueTtls { get; } = [];
    public List<string> Metrics { get; } = [];
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
    public bool Paused { get; init; }

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
    // Mirrors the fenced Redis state machine: exact-token ownership, terminal
    // immutability, reclaimable retry states and typed claim dispositions.
    public Task<CandidateClaimResult> TryClaimCandidateAsync(
      string candidateId,
      string streamEventId,
      TimeSpan leaseDuration,
      CancellationToken cancellationToken,
      CandidateClaimPolicy? policy = null
    )
    {
      var effective = policy ?? CandidateClaimPolicy.Default;
      var attempt = 0;
      string? recoveredFrom = null;
      if (_candidateStatus.TryGetValue(candidateId, out var current))
      {
        var state = StateOf(current);
        var record = CandidateExecutionRecordParser.TryParse(current)
          ?? (state is null
            ? null
            : new CandidateExecutionRecord(
              candidateId,
              streamEventId,
              state,
              null,
              null,
              null,
              0,
              1
            ));
        // A record that already exists in a non-published state was written by
        // an earlier attempt, so a reclaim is at least the second one.
        attempt = _candidateAttempts.TryGetValue(candidateId, out var previous)
          ? previous
          : state == CandidateExecutionStates.Published ? 0 : 1;
        if (state is null || !CandidateExecutionStates.IsKnown(state))
        {
          return Task.FromResult(CandidateClaimResult.Conflict(record));
        }
        if (CandidateExecutionStates.IsTerminal(state))
        {
          if (
            !(effective.AllowRejectedReclaim
              && state == CandidateExecutionStates.Rejected)
          )
          {
            return Task.FromResult(new CandidateClaimResult(
              CandidateClaimDisposition.Terminal,
              null,
              record
            ));
          }
        }
        else if (CandidateExecutionStates.IsRecoveryRequired(state))
        {
          if (!effective.AllowRecoveryAdoption)
          {
            return Task.FromResult(new CandidateClaimResult(
              CandidateClaimDisposition.RecoveryRequired,
              null,
              record
            ));
          }
          // A live recovery heartbeat blocks a second recovery owner. A
          // normal executor's leftover lease on broker_outcome_unknown is
          // modelled as already expired (the fake has no clock).
          if (
            _recoveryLeases.Contains(candidateId)
            && !_expiredLeases.Contains(candidateId)
          )
          {
            return Task.FromResult(new CandidateClaimResult(
              CandidateClaimDisposition.ActiveElsewhere,
              null,
              record
            ));
          }
          recoveredFrom = state;
        }
        else if (CandidateExecutionStates.IsActiveLeaseOwned(state))
        {
          if (!_expiredLeases.Contains(candidateId))
          {
            return Task.FromResult(new CandidateClaimResult(
              CandidateClaimDisposition.ActiveElsewhere,
              null,
              record
            ));
          }
          // Expired broker_submitting is recovery-required for normal intake.
          if (state == CandidateExecutionStates.BrokerSubmitting)
          {
            if (!effective.AllowBrokerSubmittingReclaim)
            {
              return Task.FromResult(new CandidateClaimResult(
                CandidateClaimDisposition.RecoveryRequired,
                null,
                record
              ));
            }
            // A broker side effect may already exist: this is a recovery
            // acquisition, never a normal execution restart.
            recoveredFrom = state;
          }
        }
      }
      _expiredLeases.Remove(candidateId);
      attempt += 1;
      var token = Guid.NewGuid().ToString("N");
      // Recovery claims stay structurally recovery-owned so a crashed
      // recovery worker can never decay into reclaimable normal processing.
      var claimedState = recoveredFrom is null
        ? CandidateExecutionStates.Processing
        : CandidateExecutionStates.BrokerReconciling;
      _candidateAttempts[candidateId] = attempt;
      _candidateStatus[candidateId] = claimedState;
      _candidateLeases[candidateId] = (streamEventId, token);
      if (recoveredFrom is null)
      {
        _recoveryLeases.Remove(candidateId);
      }
      else
      {
        _recoveryLeases.Add(candidateId);
      }
      Claims.Add(candidateId);
      return Task.FromResult(new CandidateClaimResult(
        CandidateClaimDisposition.Claimed,
        new CandidateExecutionLease(
          candidateId,
          streamEventId,
          token,
          DateTimeOffset.UtcNow.Add(leaseDuration)
        ),
        new CandidateExecutionRecord(
          candidateId,
          streamEventId,
          claimedState,
          token,
          DateTimeOffset.UtcNow.Add(leaseDuration).ToUnixTimeSeconds(),
          null,
          0,
          1,
          attempt,
          recoveredFrom
        )
      ));
    }

    // Simulates lease expiry so a successor can reclaim without waiting.
    public void ExpireLease(string candidateId) => _expiredLeases.Add(candidateId);

    public void SeedCandidateState(string candidateId, string state) =>
      _candidateStatus[candidateId] = state;

    // The fake keeps either a bare state name (written by a transition) or a
    // legacy marker string, exactly like a rolling Redis deployment does.
    private static string? StateOf(string? raw) =>
      raw is null
        ? null
        : CandidateExecutionStates.IsKnown(raw)
          ? raw
          : CandidateExecutionRecordParser.TryParse(raw)?.State;

    public string? CandidateState(string candidateId) =>
      _candidateStatus.TryGetValue(candidateId, out var value)
        ? StateOf(value) ?? value
        : null;

    private bool OwnsLease(string candidateId, string leaseToken) =>
      !string.IsNullOrWhiteSpace(leaseToken)
      && _candidateLeases.TryGetValue(candidateId, out var lease)
      && lease.Token == leaseToken;

    public Task<bool> RenewCandidateLeaseAsync(
      string candidateId,
      string streamEventId,
      string leaseToken,
      TimeSpan leaseDuration,
      CancellationToken cancellationToken
    ) => Task.FromResult(
      OwnsLease(candidateId, leaseToken)
      && !_expiredLeases.Contains(candidateId)
      && !LeaseRenewalBlocked
    );

    // Set to model a heartbeat that can no longer prove ownership.
    public bool LeaseRenewalBlocked { get; set; }
    public List<string> Claims { get; } = [];
    public List<string> Transitions { get; } = [];

    public Task<bool> TransitionCandidateStateAsync(
      string candidateId,
      string streamEventId,
      string leaseToken,
      string newState,
      CancellationToken cancellationToken,
      string? lastError = null
    )
    {
      if (!OwnsLease(candidateId, leaseToken))
      {
        return Task.FromResult(false);
      }
      var current = CandidateState(candidateId);
      if (CandidateExecutionStates.IsTerminal(current))
      {
        return Task.FromResult(false);
      }
      if (!CandidateExecutionStates.CanTransition(current, newState))
      {
        return Task.FromResult(false);
      }
      _candidateStatus[candidateId] = newState;
      Transitions.Add($"{current}->{newState}");
      if (newState == CandidateExecutionStates.RetryableError)
      {
        _candidateLeases.Remove(candidateId);
        _recoveryLeases.Remove(candidateId);
      }
      return Task.FromResult(true);
    }

    // Authoritative-clock seam for durable absence confirmations. The default
    // step models a fully elapsed interval so quorum flows never sleep on the
    // wall clock; interval tests disable auto-advance and move it explicitly.
    public long AbsenceClockSeconds { get; set; } = 1_000_000;
    public bool AutoAdvanceAbsenceClock { get; set; } = true;

    public Task<BrokerAbsenceProgress?> TryRecordBrokerAbsenceCheckAsync(
      string candidateId,
      string streamEventId,
      string leaseToken,
      int minIntervalSeconds,
      TimeSpan ttl,
      CancellationToken cancellationToken
    )
    {
      // Fenced exactly like the Redis script: exact lease token, unexpired
      // lease, and the recovery-active state.
      if (
        !OwnsLease(candidateId, leaseToken)
        || _expiredLeases.Contains(candidateId)
        || CandidateState(candidateId) != CandidateExecutionStates.BrokerReconciling
      )
      {
        return Task.FromResult<BrokerAbsenceProgress?>(null);
      }
      if (AutoAdvanceAbsenceClock)
      {
        AbsenceClockSeconds += minIntervalSeconds;
      }
      var now = AbsenceClockSeconds;
      var confirmations = 0;
      long? last = null;
      if (
        _absenceProgress.TryGetValue(candidateId, out var progress)
        && progress.StreamEventId == streamEventId
      )
      {
        confirmations = progress.Confirmations;
        last = progress.LastCheckAt;
      }
      if (last is long previous && now - previous < minIntervalSeconds)
      {
        return Task.FromResult<BrokerAbsenceProgress?>(new BrokerAbsenceProgress(
          false,
          confirmations,
          previous,
          now - previous
        ));
      }
      confirmations += 1;
      _absenceProgress[candidateId] = (streamEventId, confirmations, now);
      return Task.FromResult<BrokerAbsenceProgress?>(new BrokerAbsenceProgress(
        true,
        confirmations,
        now,
        last is long anchor ? now - anchor : -1
      ));
    }

    public Task ClearBrokerAbsenceProgressAsync(
      string candidateId,
      CancellationToken cancellationToken
    )
    {
      _absenceProgress.Remove(candidateId);
      return Task.CompletedTask;
    }

    public (string StreamEventId, int Confirmations, long LastCheckAt)?
      AbsenceProgressFor(string candidateId) =>
        _absenceProgress.TryGetValue(candidateId, out var progress)
          ? progress
          : null;

    public Task<string?> GetCandidateStatusAsync(
      string candidateId,
      CancellationToken cancellationToken
    ) => Task.FromResult(
      _candidateStatus.TryGetValue(candidateId, out var value)
        ? CandidateExecutionRecordParser.LegacyStatus(value)
        : null
    );

    public Task<CandidateExecutionRecord?> GetCandidateRecordAsync(
      string candidateId,
      CancellationToken cancellationToken
    )
    {
      if (!_candidateStatus.TryGetValue(candidateId, out var value))
      {
        return Task.FromResult<CandidateExecutionRecord?>(null);
      }
      var state = StateOf(value) ?? value;
      _candidateLeases.TryGetValue(candidateId, out var lease);
      return Task.FromResult<CandidateExecutionRecord?>(new CandidateExecutionRecord(
        candidateId,
        lease.StreamEventId ?? "",
        state,
        lease.Token,
        null,
        null,
        0,
        1
      ));
    }

    public Task<bool> CompleteCandidateAsync(
      string candidateId,
      string streamEventId,
      string leaseToken,
      string outcome,
      CancellationToken cancellationToken
    )
    {
      if (!OwnsLease(candidateId, leaseToken))
      {
        StaleCompletionsBlocked += 1;
        return Task.FromResult(false);
      }
      if (CandidateExecutionStates.IsTerminal(CandidateState(candidateId)))
      {
        StaleCompletionsBlocked += 1;
        return Task.FromResult(false);
      }
      _candidateStatus[candidateId] = outcome;
      _candidateLeases.Remove(candidateId);
      _recoveryLeases.Remove(candidateId);
      Processed.TrySetResult(true);
      if (outcome.StartsWith("ordered:", StringComparison.Ordinal))
      {
        Ordered.TrySetResult(true);
      }
      return Task.FromResult(true);
    }

    public int StaleCompletionsBlocked { get; private set; }

    public Task<bool> ReleaseCandidateAsync(
      string candidateId,
      string streamEventId,
      string leaseToken,
      CancellationToken cancellationToken,
      string? lastError = null
    ) => TransitionCandidateStateAsync(
      candidateId,
      streamEventId,
      leaseToken,
      CandidateExecutionStates.RetryableError,
      cancellationToken,
      lastError
    );

    public Task<bool> OverrideFlipClaimAsync(
      string claimId,
      string outcome,
      CancellationToken cancellationToken
    )
    {
      if (
        !_candidateStatus.TryGetValue(claimId, out var current)
        || !CandidateExecutionRecordParser.LegacyStatus(current)
          .StartsWith("flip_pending:", StringComparison.Ordinal)
      )
      {
        return Task.FromResult(false);
      }
      _candidateStatus[claimId] = outcome;
      _candidateLeases.Remove(claimId);
      return Task.FromResult(true);
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
    public Dictionary<long, PositionMissingRecord> PositionMissing { get; } = [];
    public Task<PositionMissingRecord?> GetPositionMissingAsync(
      long positionId,
      CancellationToken cancellationToken
    ) => Task.FromResult(
      PositionMissing.TryGetValue(positionId, out var record) ? record : null
    );
    public Task SavePositionMissingAsync(
      long positionId,
      PositionMissingRecord record,
      CancellationToken cancellationToken
    )
    {
      PositionMissing[positionId] = record;
      return Task.CompletedTask;
    }
    public Task ClearPositionMissingAsync(
      long positionId,
      CancellationToken cancellationToken
    )
    {
      PositionMissing.Remove(positionId);
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
      Task.FromResult(Paused);
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
      // The lease heartbeat records metrics from its own loop, so writes are
      // serialised and readers take a snapshot.
      lock (Metrics)
      {
        Metrics.Add(metric);
      }
      return Task.CompletedTask;
    }

    public string[] MetricsSnapshot()
    {
      lock (Metrics)
      {
        return [.. Metrics];
      }
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
    private readonly HashSet<string> _groupPlanIds = [];
    public Task SaveGroupPlanAsync(
      AutoTradeGroupPlan plan,
      TimeSpan ttl,
      CancellationToken cancellationToken
    )
    {
      var key = $"auto_trade:group_plan:{plan.GroupId}";
      Values[key] = JsonSerializer.Serialize(
        plan,
        RedisJsonContext.Default.AutoTradeGroupPlan
      );
      ValueTtls[key] = ttl;
      _groupPlanIds.Add(plan.GroupId);
      return Task.CompletedTask;
    }
    public Task DeleteGroupPlanAsync(
      string groupId,
      CancellationToken cancellationToken
    )
    {
      var key = $"auto_trade:group_plan:{groupId}";
      Values.Remove(key);
      ValueTtls.Remove(key);
      _groupPlanIds.Remove(groupId);
      return Task.CompletedTask;
    }
    public Task<IReadOnlyList<string>> GetGroupPlanIdsAsync(
      CancellationToken cancellationToken
    ) => Task.FromResult<IReadOnlyList<string>>(_groupPlanIds.ToArray());
    public Task RecordLifecycleEventAsync(
      AutoTradeEvent tradeEvent,
      CancellationToken cancellationToken
    )
    {
      LifecycleEvents.Add(tradeEvent);
      var owner = tradeEvent.CandidateId ?? tradeEvent.GroupId;
      if (
        tradeEvent.MutatesLifecycle
        && owner is not null
        && !string.IsNullOrWhiteSpace(tradeEvent.State)
      )
      {
        var transition = AutoTradeLifecycle.TransitionForEvent(
          tradeEvent.Type,
          tradeEvent.RemainingVolume
        );
        Values[$"auto_trade:lifecycle_state:{owner}"] =
          JsonSerializer.Serialize(
            AutoTradeLifecycle.BuildStateRecord(
              tradeEvent,
              owner,
              transition?.Terminal ?? false
            ),
            RedisJsonContext.Default.AutoTradeLifecycleStateRecord
          );
      }
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

  private sealed class SnapshotList<T> : IReadOnlyCollection<T>
  {
    private readonly object _sync = new();
    private readonly List<T> _items = [];

    public int Count
    {
      get
      {
        lock (_sync)
        {
          return _items.Count;
        }
      }
    }

    public void Add(T item)
    {
      lock (_sync)
      {
        _items.Add(item);
      }
    }

    public T[] Snapshot()
    {
      lock (_sync)
      {
        return [.. _items];
      }
    }

    public IEnumerator<T> GetEnumerator() =>
      ((IEnumerable<T>)Snapshot()).GetEnumerator();

    IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
  }
}

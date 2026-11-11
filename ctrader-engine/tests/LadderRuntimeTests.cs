using System.Text.Json;
using Xunit;

namespace ApexVoid.CTraderFeed.Tests;

// ZL-1: what the executor actually does with a DCA ladder - real TradePlanRuntime, in-memory store and a recording broker.
public class LadderRuntimeTests
{
  private const string PlanId = "v8:zl1-ladder";
  private static readonly SymbolInfo Xau = new("XAU", "XAUUSD", 1, 2, 1, 100, 100, 100000, 10000);

  internal sealed class FakeStore : IAutoTradeStore
  {
    public readonly Dictionary<string, string> Values = new();
    public readonly List<TradeStreamEntry> Stream = new();
    public readonly List<AutoTradeEvent> Events = new();
    public string PlanCursor = "0-0";

    public Task<string> GetCommandCursorAsync(CancellationToken ct) => Task.FromResult("0-0");
    public Task SetCommandCursorAsync(string cursor, CancellationToken ct) => Task.CompletedTask;
    public Task<string> GetTradePlanCursorAsync(CancellationToken ct) => Task.FromResult(PlanCursor);
    public Task SetTradePlanCursorAsync(string cursor, CancellationToken ct) { PlanCursor = cursor; return Task.CompletedTask; }
    public Task<string?> GetStringAsync(string key, CancellationToken ct) => Task.FromResult(Values.GetValueOrDefault(key));
    public Task SetStringAsync(string key, string value, CancellationToken ct) { Values[key] = value; return Task.CompletedTask; }
    public Task DeleteStringAsync(string key, CancellationToken ct) { Values.Remove(key); return Task.CompletedTask; }
    public Task<bool> TryClaimStringAsync(string key, string value, TimeSpan ttl, CancellationToken ct)
    {
      if (Values.ContainsKey(key)) return Task.FromResult(false);
      Values[key] = value;
      return Task.FromResult(true);
    }
    public Task<IReadOnlyList<TradeStreamEntry>> ReadCandidatesAsync(string stream, string afterId, int count, CancellationToken ct)
    {
      var fresh = Stream.Where(e => string.CompareOrdinal(e.Id, afterId) > 0).Take(count).ToArray();
      return Task.FromResult<IReadOnlyList<TradeStreamEntry>>(fresh);
    }
    public Task PublishAutoTradeEventAsync(string stream, AutoTradeEvent tradeEvent, CancellationToken ct)
    {
      Events.Add(tradeEvent);
      return Task.CompletedTask;
    }
  }

  internal sealed class FakeBroker : ICTraderTradeClient
  {
    private long _next = 100;
    public readonly List<MarketOrderRequest> Market = new();
    public readonly List<LimitOrderRequest> Limits = new();
    public readonly List<long> Cancelled = new();
    public readonly List<long> Closed = new();
    public readonly List<TradingPosition> Positions = new();
    public readonly List<TradingPendingOrder> Pending = new();
    public decimal MarketFillPrice;

    public Task<TradingAccountSnapshot> GetTradingAccountAsync(CancellationToken ct) =>
      Task.FromResult(new TradingAccountSnapshot(1, false, "trade", "full", "demo", "test", 10000m, 10000m, 0, "test"));
    public Task<IReadOnlyList<TradingPosition>> ReconcilePositionsAsync(CancellationToken ct) =>
      Task.FromResult<IReadOnlyList<TradingPosition>>(Positions.ToArray());
    public Task<IReadOnlyList<TradingPendingOrder>> ReconcilePendingOrdersAsync(CancellationToken ct) =>
      Task.FromResult<IReadOnlyList<TradingPendingOrder>>(Pending.ToArray());
    public Task<TradeExecution> PlaceMarketOrderAsync(MarketOrderRequest order, CancellationToken ct)
    {
      Market.Add(order);
      var id = ++_next;
      Positions.Add(new TradingPosition(id, order.SymbolId, order.Direction, order.Volume, MarketFillPrice, null, order.Label, order.Comment, order.ClientOrderId));
      return Task.FromResult(new TradeExecution(id, id, MarketFillPrice, order.Volume));
    }
    public Task<long> PlaceLimitOrderAsync(LimitOrderRequest order, CancellationToken ct)
    {
      Limits.Add(order);
      var id = ++_next;
      Pending.Add(new TradingPendingOrder(id, order.SymbolId, order.Direction, order.Volume, order.LimitPrice, order.Label, order.Comment, order.ClientOrderId));
      return Task.FromResult(id);
    }
    public Task CancelPendingOrderAsync(long orderId, CancellationToken ct)
    {
      Cancelled.Add(orderId);
      Pending.RemoveAll(o => o.OrderId == orderId);
      return Task.CompletedTask;
    }
    public Task AmendPositionStopLossAsync(long positionId, decimal stopLoss, CancellationToken ct)
    {
      var index = Positions.FindIndex(p => p.PositionId == positionId);
      if (index >= 0) Positions[index] = Positions[index] with { StopLoss = stopLoss };
      return Task.CompletedTask;
    }
    public Task<TradeExecution> ClosePositionAsync(long positionId, long volume, CancellationToken ct)
    {
      Closed.Add(positionId);
      return Task.FromResult(new TradeExecution(positionId, positionId, 0m, volume));
    }
  }

  private static string LadderPlan(string direction, decimal[] prices, decimal[] ratios, decimal? minSpacing, bool withRisk = false)
  {
    var buy = direction == "BUY";
    var legs = string.Join(",", prices.Select((p, i) => $"{{\"leg_id\":\"L{i + 1}\",\"price\":\"{p}\",\"volume_ratio\":\"{ratios[i]}\"}}"));
    var spacing = minSpacing is null ? "" : $",\"min_leg_spacing\":\"{minSpacing}\"";
    var risk = withRisk ? $",\"risk_leg\":{{\"price\":\"{(buy ? 4296.50m : 4306.50m)}\",\"lots\":\"0.05\"}}" : "";
    var stop = buy ? 4295.00m : 4308.00m;
    var target = buy ? 4320.00m : 4285.00m;
    var zoneLow = prices.Min();
    var zoneHigh = prices.Max();
    var json = """
    {"version":8,"plan_id":"@PLAN","thesis_id":"t","setup_id":"s","symbol":"XAU","created_at":1719999600,"expires_at":2000000000,
     "analysis":{"strategy":"Demand","strategy_family":"zone","direction":"@DIR","context_timeframes":["M15"],"formation_timeframe":"M5","confirmation_timeframe":"M5",
       "formation_bar_ts":1719999300,"confirmation_bar_ts":1719999600,"score":3.0,"confluence":3,"bias":"up","regime":"trend","reasons":[],"tags":[]},
     "source_structure":{"structure_id":"z","kind":"demand","timeframe":"M15","low":"@LOW","high":"@HIGH","invalidation_price":"@STOP"},
     "entry":{"type":"limit_ladder","zone_low":"@LOW","zone_high":"@HIGH","expires_at":2000000000,"legs":[@LEGS]@RISK@SPACING},
     "stop":{"type":"absolute","price":"@STOP","source":"structure","structure_id":"z","reason":"zl1"},
     "targets":[{"target_id":"TP1","type":"absolute","price":"@TARGET","close_ratio":"1.0"}],
     "risk":{"risk_percent":"1.0","risk_multiplier":"1.0","max_volume":100000,"max_group_risk_percent":"2.0"},
     "sizing":{"mode":"equity_table","table_version":"owner_equity_v1","entry_distribution":"zone_scale","leg_ratios":[@RATIOS]},
     "management":{"be_after_target_id":null,"be_buffer_ticks":6,"never_worsen_stop":true},
     "execution_policy":{"allow_market":true,"allow_limit":true,"allow_partial_fill":true,"cancel_on_expiry":true},
     "provenance":{"analysis_engine_version":"zl1","market_map_id":"","config_fingerprint":""}}
    """;
    return json
      .Replace("@PLAN", PlanId).Replace("@DIR", direction)
      .Replace("@LOW", zoneLow.ToString()).Replace("@HIGH", zoneHigh.ToString())
      .Replace("@STOP", stop.ToString()).Replace("@TARGET", target.ToString())
      .Replace("@LEGS", legs).Replace("@RISK", risk).Replace("@SPACING", spacing)
      .Replace("@RATIOS", string.Join(",", ratios.Select(r => $"\"{r}\"")));
  }

  private static readonly decimal[] Buy = [4303.00m, 4301.50m, 4300.00m];
  private static readonly decimal[] Sell = [4300.00m, 4301.50m, 4303.00m];
  private static readonly decimal[] Ratios = [0.6m, 0.2m, 0.2m];

  private static TradePlanRuntime NewRuntime(FakeStore store) =>
    new(
      new AutoTradeOptions(true, false, "test", 1000, "events", "zl1", Symbols: ["XAU"]),
      store,
      () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000),
      _ => { },
      resolveBoundSymbol: _ => Xau,
      resolveUnits: _ => (0.1m, 10m)
    );

  private static SpotPrice Quote(decimal bid, decimal ask) => new("XAU", bid, ask, 1_720_000_000);

  private static async Task Poll(TradePlanRuntime runtime, FakeBroker broker, SpotPrice quote) =>
    await runtime.PollAsync(broker, Xau, quote, CancellationToken.None);

  private static FakeStore StoreWith(string payload)
  {
    var store = new FakeStore();
    store.Stream.Add(new TradeStreamEntry("1-0", payload));
    return store;
  }

  [Fact]
  public async Task AThreeLegBuyLadderRestsAtThreeDistinctPricesAboveTheZone()
  {
    var store = StoreWith(LadderPlan("BUY", Buy, Ratios, 1.0m));
    var broker = new FakeBroker();
    await Poll(NewRuntime(store), broker, Quote(4309.80m, 4310.00m));
    Assert.Empty(broker.Market);
    Assert.Equal(new[] { 4303.00m, 4301.50m, 4300.00m }, broker.Limits.Select(o => o.LimitPrice).ToArray());
    Assert.Equal(3, broker.Limits.Select(o => o.LimitPrice).Distinct().Count());
  }

  [Fact]
  public async Task AThreeLegSellLadderRestsAtThreeDistinctPricesBelowTheZone()
  {
    var store = StoreWith(LadderPlan("SELL", Sell, Ratios, 1.0m));
    var broker = new FakeBroker();
    await Poll(NewRuntime(store), broker, Quote(4290.00m, 4290.20m));
    Assert.Empty(broker.Market);
    Assert.Equal(new[] { 4300.00m, 4301.50m, 4303.00m }, broker.Limits.Select(o => o.LimitPrice).ToArray());
  }

  [Fact]
  public async Task QuoteInsideTheZoneFillsL1AtTheQuoteAndKeepsTheDeeperLegsResting()
  {
    var store = StoreWith(LadderPlan("BUY", Buy, Ratios, 1.0m));
    var broker = new FakeBroker { MarketFillPrice = 4303.00m };
    await Poll(NewRuntime(store), broker, Quote(4302.80m, 4303.00m));
    Assert.Single(broker.Market);
    Assert.Equal(new[] { 4301.50m, 4300.00m }, broker.Limits.Select(o => o.LimitPrice).ToArray());
  }

  [Fact]
  public async Task AnL1FillBetterThanPlannedThatLeavesL2LessThanTheSpacingAwayIsNotDoubledUp()
  {
    // L1 planned 4303.00 fills at 4302.20 (the market came to it): L2 at 4301.50 would sit 0.70 from the real fill, under the 1.00 spacing.
    var store = StoreWith(LadderPlan("BUY", Buy, Ratios, 1.0m));
    var broker = new FakeBroker { MarketFillPrice = 4302.20m };
    await Poll(NewRuntime(store), broker, Quote(4302.00m, 4302.20m));
    Assert.Single(broker.Market);
    Assert.Equal(new[] { 4300.00m }, broker.Limits.Select(o => o.LimitPrice).ToArray());
    Assert.Contains("skipped:leg_price_collides_with_sibling", store.Values[$"execution:plan_runtime:{PlanId}"]);
  }

  [Fact]
  public async Task PriceThatAlreadyMovedThroughL2IsNotSubmittedAsAMarketSubstitute()
  {
    var store = StoreWith(LadderPlan("BUY", Buy, Ratios, 1.0m));
    var broker = new FakeBroker { MarketFillPrice = 4301.60m };
    await Poll(NewRuntime(store), broker, Quote(4301.40m, 4301.60m));
    // L1 chases at the quote; L2 (4301.50) is at/through the market: skipped, NOT a second market order. L3 still rests.
    Assert.Single(broker.Market);
    Assert.Equal(new[] { 4300.00m }, broker.Limits.Select(o => o.LimitPrice).ToArray());
    var state = JsonSerializer.Deserialize<JsonElement>(store.Values[$"execution:plan_runtime:{PlanId}"]);
    Assert.Contains("skipped:limit_leg_marketable", state.GetRawText());
    Assert.Contains(store.Events, e => e.Type == "leg_skipped");
  }

  [Fact]
  public async Task AGapThroughEveryDeeperPriceSkipsAllOfThemAndNeverFillsThemTogether()
  {
    var store = StoreWith(LadderPlan("BUY", Buy, Ratios, 1.0m));
    var broker = new FakeBroker { MarketFillPrice = 4299.20m };
    await Poll(NewRuntime(store), broker, Quote(4299.00m, 4299.20m));
    Assert.Single(broker.Market);
    Assert.Empty(broker.Limits);
    Assert.Equal(2, store.Events.Count(e => e.Type == "leg_skipped"));
  }

  [Fact]
  public async Task ARestartNeverSubmitsALegTheGuardSkipped()
  {
    var store = StoreWith(LadderPlan("BUY", Buy, Ratios, 1.0m));
    var broker = new FakeBroker { MarketFillPrice = 4301.60m };
    await Poll(NewRuntime(store), broker, Quote(4301.40m, 4301.60m));
    var ordersBefore = broker.Market.Count + broker.Limits.Count;
    // Process restart: a new runtime over the same durable state, price now far above L2 (L2 would rest happily).
    await Poll(NewRuntime(store), broker, Quote(4309.80m, 4310.00m));
    await Poll(NewRuntime(store), broker, Quote(4309.80m, 4310.00m));
    Assert.Equal(ordersBefore, broker.Market.Count + broker.Limits.Count);
    Assert.DoesNotContain(broker.Limits, o => o.LimitPrice == 4301.50m);
  }

  [Fact]
  public async Task APlanWhoseLadderBreaksItsDeclaredSpacingIsRejectedOnReceipt()
  {
    var store = StoreWith(LadderPlan("BUY", [4303.00m, 4303.00m, 4302.90m], Ratios, 1.0m));
    var broker = new FakeBroker();
    await Poll(NewRuntime(store), broker, Quote(4309.80m, 4310.00m));
    Assert.Empty(broker.Market);
    Assert.Empty(broker.Limits);
    Assert.Contains(store.Values.Keys, key => key.StartsWith("execution:plan_rejection:"));
  }

  [Fact]
  public async Task ARiskLegOnTheSamePriceAsAnEntryLegIsRejectedOnReceipt()
  {
    var plan = LadderPlan("BUY", Buy, Ratios, 1.0m, withRisk: true).Replace("\"4296.50\"", "\"4300.40\"");
    var store = StoreWith(plan);
    var broker = new FakeBroker();
    await Poll(NewRuntime(store), broker, Quote(4309.80m, 4310.00m));
    Assert.Empty(broker.Limits);
    Assert.Contains(store.Values.Keys, key => key.StartsWith("execution:plan_rejection:"));
  }

  [Fact]
  public async Task AZoneThatBecomesTerminalCancelsTheRestingLegsAndKeepsTheFilledPosition()
  {
    var store = StoreWith(LadderPlan("BUY", Buy, Ratios, 1.0m));
    var broker = new FakeBroker { MarketFillPrice = 4303.00m };
    var runtime = NewRuntime(store);
    await Poll(runtime, broker, Quote(4302.80m, 4303.00m));
    Assert.Single(broker.Market);
    Assert.Equal(2, broker.Pending.Count);
    // The analysis engine retires the opportunity (zone invalidated): Algo Bot writes the durable cancel intent.
    store.Values[$"execution:plan_cancel:{PlanId}"] =
      "{\"plan_id\":\"" + PlanId + "\",\"reason\":\"zone_invalidated\",\"source\":\"opportunity_invalidated\",\"requested_at\":1719999999}";
    await Poll(runtime, broker, Quote(4301.00m, 4301.20m));
    Assert.Empty(broker.Pending);          // every unfilled leg withdrawn
    Assert.Equal(2, broker.Cancelled.Count);
    Assert.Empty(broker.Closed);           // the filled position is NOT closed by the cancellation
    Assert.Single(broker.Positions);
    // Duplicate terminal intent and a fresh process: idempotent, still no new exposure.
    await Poll(runtime, broker, Quote(4301.00m, 4301.20m));
    await Poll(NewRuntime(store), broker, Quote(4309.80m, 4310.00m));
    Assert.Single(broker.Market);
    Assert.Empty(broker.Pending);
    Assert.Empty(broker.Closed);
  }

  [Fact]
  public async Task ACancelIntentThatArrivesBeforeTheFirstOrderPreventsEveryOrder()
  {
    var store = StoreWith(LadderPlan("BUY", Buy, Ratios, 1.0m));
    store.Values[$"execution:plan_cancel:{PlanId}"] =
      "{\"plan_id\":\"" + PlanId + "\",\"reason\":\"zone_expired\",\"source\":\"opportunity_expired\",\"requested_at\":1719999999}";
    var broker = new FakeBroker();
    await Poll(NewRuntime(store), broker, Quote(4309.80m, 4310.00m));
    Assert.Empty(broker.Market);
    Assert.Empty(broker.Limits);
  }
}

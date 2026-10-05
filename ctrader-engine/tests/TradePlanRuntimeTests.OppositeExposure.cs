using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// The engine must refuse an autonomous opposite-direction order on its own,
/// before any broker mutation, from tracked plan state plus actual broker
/// positions and pending orders - never trusting Python's admission verdict
/// and never relying on broker netting.
/// </summary>
public sealed partial class TradePlanRuntimeTests
{
  private static readonly SymbolInfo GbpUsd = new(
    "GBPUSD", "GBPUSD", 9, Digits: 5, PipPosition: 4,
    MinVolume: 100, StepVolume: 100, MaxVolume: 100_000, LotSize: 10_000
  );

  private static readonly OppositePositionPolicy FxPolicy = new(false, null, 0.0001m);
  private static readonly OppositePositionPolicy XauPolicy = new(true, 150m, 0.1m);

  private static TradePlanRuntime OppositeRuntime(
    FakeTradePlanStore store,
    OppositePositionPolicy? policy,
    List<string>? logs = null
  ) => new(
    Options(), store, () => DateTimeOffset.UtcNow, (logs ?? []).Add,
    resolveUnits: symbol => symbol.StartsWith("GBP", StringComparison.Ordinal)
      ? (0.0001m, 10m)
      : (0.1m, 1m),
    resolveOppositePolicy: _ => policy
  );

  private static string GbpUsdPlan(string planId, string direction) => PlanJson(
    planId: planId,
    thesisId: $"thesis-{planId}",
    setupId: $"setup-{planId}",
    direction: direction,
    symbol: "GBPUSD",
    zoneLow: 1.2495m,
    zoneHigh: 1.2505m,
    stopPrice: direction == "BUY" ? 1.2470m : 1.2530m,
    targetsJson: direction == "BUY"
      ? """[{"target_id":"TP1","type":"absolute","price":"1.2540","close_ratio":"1.0"}]"""
      : """[{"target_id":"TP1","type":"absolute","price":"1.2460","close_ratio":"1.0"}]""",
    managementJson: """{"be_after_target_id":"TP1","be_buffer_ticks":6,"never_worsen_stop":true}"""
  );

  private static SpotPrice GbpUsdQuote(long ts = 1) =>
    new("GBPUSD", 1.24998m, 1.25002m, ts);

  [Theory]
  [InlineData("BUY", TradeDirection.Sell, 1.2501)]   // scenario A: any strategy
  [InlineData("SELL", TradeDirection.Buy, 1.2000)]   // scenario B
  [InlineData("BUY", TradeDirection.Sell, 1.3500)]   // distance is irrelevant
  public async Task FxBrokerOnlyOppositePositionBlocksOrderBeforeAnyBrokerMutation(
    string incoming, TradeDirection existing, double existingEntry
  )
  {
    // Scenario G: no tracked plan at all - Redis was wiped or the position was
    // opened outside this runtime. Only the broker knows about the exposure.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(GbpUsdPlan("v8:gbp-in", incoming));
    var client = new FakeTradePlanTradingClient();
    client.SeedPosition(
      700, existing, 100, comment: "", entryPrice: (decimal)existingEntry,
      symbolId: GbpUsd.SymbolId, label: "manual"
    );
    var logs = new List<string>();
    var runtime = OppositeRuntime(store, FxPolicy, logs);

    await runtime.PollAsync(client, GbpUsd, GbpUsdQuote(), CancellationToken.None);

    Assert.Empty(client.MarketOrders);
    Assert.Empty(client.LimitOrders);
    Assert.Equal("rejected", store.Value("execution:plan_state:v8:gbp-in"));
    Assert.Contains(store.Events, e => e.Type == "plan_rejected"
      && e.ReasonCode == "fx_opposite_position_not_allowed");
    Assert.Contains(logs, line => line.Contains(
      "reason_code=fx_opposite_position_not_allowed", StringComparison.Ordinal)
      && line.Contains("existing_source=broker_position", StringComparison.Ordinal));
    Assert.Empty(runtime.TrackedStates);
  }

  [Fact]
  public async Task FxBrokerPendingOppositeOrderBlocksOrder()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(GbpUsdPlan("v8:gbp-in", "BUY"));
    var client = new FakeTradePlanTradingClient();
    client.PendingOrders.Add(new TradingPendingOrder(
      800, GbpUsd.SymbolId, TradeDirection.Sell, 100, 1.2800m, "manual", "", ""
    ));
    var runtime = OppositeRuntime(store, FxPolicy);

    await runtime.PollAsync(client, GbpUsd, GbpUsdQuote(), CancellationToken.None);

    Assert.Empty(client.MarketOrders);
    Assert.Equal("rejected", store.Value("execution:plan_state:v8:gbp-in"));
  }

  [Fact]
  public async Task FxSameDirectionBrokerPositionDoesNotTriggerTheOppositeFence()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(GbpUsdPlan("v8:gbp-in", "BUY"));
    var client = new FakeTradePlanTradingClient();
    client.SeedPosition(
      700, TradeDirection.Buy, 100, comment: "", entryPrice: 1.2400m,
      symbolId: GbpUsd.SymbolId, label: "manual"
    );
    var runtime = OppositeRuntime(store, FxPolicy);

    await runtime.PollAsync(client, GbpUsd, GbpUsdQuote(), CancellationToken.None);

    Assert.Single(client.MarketOrders);
  }

  [Fact]
  public async Task OppositePositionOnAnotherBrokerSymbolDoesNotBlock()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(GbpUsdPlan("v8:gbp-in", "BUY"));
    var client = new FakeTradePlanTradingClient();
    client.SeedPosition(
      700, TradeDirection.Sell, 100, comment: "", entryPrice: 1.2500m,
      symbolId: 42, label: "manual"
    );
    var runtime = OppositeRuntime(store, FxPolicy);

    await runtime.PollAsync(client, GbpUsd, GbpUsdQuote(), CancellationToken.None);

    Assert.Single(client.MarketOrders);
  }

  [Fact]
  public async Task PendingPlanRaceExactlyOneOppositeFxPlanReachesTheBroker()
  {
    // Both plans arrive in one poll (Python admitted both before either was
    // visible). Whichever the engine ranks first (earlier created_at, then
    // plan id) gets the book; the other is refused - never both, never neither.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(GbpUsdPlan("v8:gbp-sell", "SELL"));
    store.EnqueuePlan(GbpUsdPlan("v8:gbp-buy", "BUY"));
    var client = new FakeTradePlanTradingClient();
    var runtime = OppositeRuntime(store, FxPolicy);

    await runtime.PollAsync(client, GbpUsd, GbpUsdQuote(), CancellationToken.None);

    var order = Assert.Single(client.MarketOrders);
    var (winner, loser) = order.Direction == TradeDirection.Buy
      ? ("v8:gbp-buy", "v8:gbp-sell")
      : ("v8:gbp-sell", "v8:gbp-buy");
    Assert.Equal("filled", store.Value($"execution:plan_state:{winner}"));
    Assert.Equal("rejected", store.Value($"execution:plan_state:{loser}"));
    Assert.Equal(
      "fx_opposite_position_not_allowed",
      store.Events.Single(e => e.Type == "plan_rejected").ReasonCode
    );
  }

  [Theory]
  [InlineData(4104.10, true)]   // exactly 150.0 pips from the 4089.10 ask
  [InlineData(4104.11, true)]
  [InlineData(4104.09, false)]  // 149.9 pips
  [InlineData(4100.00, false)]
  public async Task XauBrokerOnlyOppositeSeparationBoundaryIsInclusive(
    double existingEntry, bool allowed
  )
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(planId: "v8:xau-in", direction: "BUY"));
    var client = new FakeTradePlanTradingClient();
    client.SeedPosition(
      700, TradeDirection.Sell, 100, comment: "", entryPrice: (decimal)existingEntry,
      label: "manual"
    );
    var logs = new List<string>();
    var runtime = OppositeRuntime(store, XauPolicy, logs);

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    Assert.Equal(allowed ? 1 : 0, client.MarketOrders.Count);
    if (!allowed)
    {
      Assert.Contains(logs, line => line.Contains(
        "reason_code=xau_opposite_position_too_close", StringComparison.Ordinal));
      Assert.Equal("rejected", store.Value("execution:plan_state:v8:xau-in"));
    }
  }

  [Fact]
  public async Task OppositeExposureWithoutAnInstrumentPolicyFailsClosed()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(GbpUsdPlan("v8:gbp-in", "BUY"));
    var client = new FakeTradePlanTradingClient();
    client.SeedPosition(
      700, TradeDirection.Sell, 100, comment: "", entryPrice: 1.2500m,
      symbolId: GbpUsd.SymbolId, label: "manual"
    );
    var runtime = OppositeRuntime(store, policy: null);

    await runtime.PollAsync(client, GbpUsd, GbpUsdQuote(), CancellationToken.None);

    Assert.Empty(client.MarketOrders);
    Assert.Contains(store.Events, e => e.Type == "plan_rejected"
      && e.ReasonCode == "opposite_exposure_policy_unavailable");
  }

  [Fact]
  public async Task BrokerStateUnavailableDefersTheOrderInsteadOfGuessing()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(GbpUsdPlan("v8:gbp-in", "BUY"));
    var client = new FakeTradePlanTradingClient { FailReconcileAccountOnCall = 2 };
    var logs = new List<string>();
    var runtime = OppositeRuntime(store, FxPolicy, logs);

    await runtime.PollAsync(client, GbpUsd, GbpUsdQuote(), CancellationToken.None);

    Assert.Empty(client.MarketOrders);
    Assert.Equal("received", store.Value("execution:plan_state:v8:gbp-in"));
    Assert.Contains(logs, line => line.Contains(
      "reason=broker_state_unavailable", StringComparison.Ordinal));

    await runtime.PollAsync(client, GbpUsd, GbpUsdQuote(2), CancellationToken.None);
    Assert.Single(client.MarketOrders);
  }
}

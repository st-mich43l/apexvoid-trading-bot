using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// Owner broker controls and owner-armed manual /algo plans on the single
/// TradePlan V8 path.
/// </summary>
public sealed partial class TradePlanRuntimeTests
{
  private static string ManualPlanJson(string planId = "manual:7:0", string direction = "BUY") =>
    PlanJson(
      planId: planId,
      thesisId: "manual-thesis:7",
      setupId: planId,
      direction: direction,
      strategy: "Manual Algo",
      strategyFamily: "manual"
    );

  private static async Task<(TradePlanRuntime Runtime, FakeTradePlanStore Store, FakeTradePlanTradingClient Client)>
    OpenedManualPlanAsync(string direction = "BUY")
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(ManualPlanJson(direction: direction));
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    Assert.Single(client.MarketOrders);
    return (runtime, store, client);
  }

  [Fact]
  public void ManualPlansAreIdentifiedByFamily()
  {
    Assert.True(TradePlanRuntime.IsManualPlan(TradePlanJson.DeserializePlan(ManualPlanJson())));
    Assert.False(TradePlanRuntime.IsManualPlan(TradePlanJson.DeserializePlan(PlanJson())));
  }

  [Fact]
  public async Task ManualPlanEventsAreLabelledAlgoManualAndAutonomousOnesAlgoAuto()
  {
    var (_, manualStore, _) = await OpenedManualPlanAsync();
    Assert.All(manualStore.Events, e => Assert.Equal("algo_manual", e.Stream));
    Assert.Contains(manualStore.Events, e => e.Type == "order_filled" && e.CandidateId == "manual:7:0");

    var autoStore = new FakeTradePlanStore();
    autoStore.EnqueuePlan(PlanJson());
    var runtime = new TradePlanRuntime(Options(), autoStore, () => DateTimeOffset.UtcNow, _ => { });
    await runtime.PollAsync(
      new FakeTradePlanTradingClient(), Symbol,
      new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    Assert.All(autoStore.Events, e => Assert.Equal("algo_auto", e.Stream));
  }

  [Fact]
  public async Task ManualPlanIgnoresTheAutonomousOppositeFenceAndSameDirectionRule()
  {
    // An owner instruction is a direct decision: an opposite broker position
    // and a live same-direction plan do not block it.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(planId: "v8:auto-buy", thesisId: "t-auto", setupId: "s-auto"));
    store.EnqueuePlan(ManualPlanJson("manual:8:0", "BUY"));
    var client = new FakeTradePlanTradingClient();
    client.SeedPosition(
      900, TradeDirection.Sell, 100, comment: "", entryPrice: 4089.10m, label: "manual"
    );
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { },
      resolveOppositePolicy: _ => new OppositePositionPolicy(true, 150m, 0.1m)
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    Assert.Contains(runtime.TrackedStates, s => s.PlanId == "manual:8:0");
    Assert.Equal("filled", store.Value("execution:plan_state:manual:8:0"));
  }

  [Fact]
  public async Task OwnerClosePlanClosesEveryOpenLegAtTheBroker()
  {
    var (runtime, _, client) = await OpenedManualPlanAsync();
    var volume = client.MarketOrders[0].Volume;

    var result = await runtime.OwnerClosePlanAsync(
      client, Symbol, "manual:7:0", fraction: null, CancellationToken.None
    );

    Assert.Null(result.Error);
    Assert.Equal(1, result.Affected);
    var close = Assert.Single(client.Closes);
    Assert.Equal(volume, close.Volume);
  }

  [Fact]
  public async Task OwnerClosePlanHonoursAStepAlignedFraction()
  {
    var (runtime, _, client) = await OpenedManualPlanAsync();
    var volume = client.MarketOrders[0].Volume;

    await runtime.OwnerClosePlanAsync(
      client, Symbol, "manual:7:0", fraction: 0.5m, CancellationToken.None
    );

    var close = Assert.Single(client.Closes);
    Assert.True(close.Volume < volume);
    Assert.Equal(0, close.Volume % Symbol.StepVolume);
  }

  [Fact]
  public async Task OwnerClosePlanForAnUnknownPlanReportsAnError()
  {
    var (runtime, _, client) = await OpenedManualPlanAsync();

    var result = await runtime.OwnerClosePlanAsync(
      client, Symbol, "manual:404:0", fraction: null, CancellationToken.None
    );

    Assert.NotNull(result.Error);
    Assert.Empty(client.Closes);
  }

  [Fact]
  public async Task OwnerClosePositionAutonomousOnlyRefusesAManualLeg()
  {
    var (runtime, _, client) = await OpenedManualPlanAsync();
    var positionId = runtime.TrackedStates.Single().Legs!.Single().BrokerPositionId!.Value;

    var refused = await runtime.OwnerClosePositionAsync(
      client, Symbol, positionId, fraction: null, autonomousOnly: true, CancellationToken.None
    );

    Assert.Contains("manual /algo", refused.Error);
    Assert.Empty(client.Closes);

    var allowed = await runtime.OwnerClosePositionAsync(
      client, Symbol, positionId, fraction: null, autonomousOnly: false, CancellationToken.None
    );
    Assert.Null(allowed.Error);
    Assert.Single(client.Closes);
  }

  [Fact]
  public async Task OwnerClosePositionAutonomousOnlyClosesAnAutonomousLeg()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    var positionId = runtime.TrackedStates.Single().Legs!.Single().BrokerPositionId!.Value;

    var result = await runtime.OwnerClosePositionAsync(
      client, Symbol, positionId, fraction: null, autonomousOnly: true, CancellationToken.None
    );

    Assert.Null(result.Error);
    Assert.Single(client.Closes);
  }

  [Fact]
  public async Task OwnerMoveStopAmendsTheBrokerStopAndPublishesTheMove()
  {
    var (runtime, store, client) = await OpenedManualPlanAsync();
    var positionId = runtime.TrackedStates.Single().Legs!.Single().BrokerPositionId!.Value;
    client.StopAmendments.Clear();

    var result = await runtime.OwnerMoveStopAsync(
      client, Symbol, positionId, 4086.00m, CancellationToken.None
    );

    Assert.Null(result.Error);
    Assert.Contains(client.StopAmendments, a => a.PositionId == positionId && a.StopLoss == 4086.00m);
    Assert.Equal(4086.00m, runtime.TrackedStates.Single().CurrentStop);
    var moved = Assert.Single(store.Events, e => e.Type == "manual_sl_moved");
    Assert.Equal(4086.00m, moved.Price);
    Assert.Equal("algo_manual", moved.Stream);
  }

  [Fact]
  public async Task OwnerMoveStopForAnUnknownPositionReportsAnError()
  {
    var (runtime, _, client) = await OpenedManualPlanAsync();

    var result = await runtime.OwnerMoveStopAsync(
      client, Symbol, 424242, 4086.00m, CancellationToken.None
    );

    Assert.NotNull(result.Error);
  }

  [Fact]
  public async Task OwnerFlattenClosesOpenLegsAcrossPlans()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(ManualPlanJson("manual:7:0", "BUY"));
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    var (closed, cancelled) = await runtime.OwnerFlattenAsync(client, Symbol, CancellationToken.None);

    Assert.Equal(1, closed);
    Assert.Equal(0, cancelled);
    Assert.Single(client.Closes);
  }

  [Fact]
  public async Task OwnerPauseHoldsNewOrdersUntilResumed()
  {
    var store = new FakeTradePlanStore();
    await store.SetStringAsync("auto_trade:paused", "1", CancellationToken.None);
    store.EnqueuePlan(ManualPlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    var quote = new SpotPrice("XAU", 4089.05m, 4089.10m, 1);

    await runtime.PollAsync(client, Symbol, quote, CancellationToken.None);
    Assert.Empty(client.MarketOrders);
    Assert.Equal("received", store.Value("execution:plan_state:manual:7:0"));

    await store.DeleteStringAsync("auto_trade:paused", CancellationToken.None);
    await runtime.PollAsync(client, Symbol, quote with { Timestamp = 2 }, CancellationToken.None);
    Assert.Single(client.MarketOrders);
  }

  [Fact]
  public void OwnerCommandJsonRoundTripsThroughTheStreamContract()
  {
    var command = System.Text.Json.JsonSerializer.Deserialize(
      """{"type":"close","intent_id":"manual:7:0","frac":0.5}""",
      RedisJsonContext.Default.ManualTradeCommand
    );

    Assert.NotNull(command);
    Assert.Equal("close", command!.Type);
    Assert.Equal("manual:7:0", command.IntentId);
    Assert.Equal(0.5m, command.Frac);
  }
}

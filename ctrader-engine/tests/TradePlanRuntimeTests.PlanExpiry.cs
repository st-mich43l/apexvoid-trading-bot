using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// A plan declares cancel_on_expiry: when its entry validity ends, every entry
/// order still resting at the broker comes off — with or without an explicit
/// withdrawal. Manual /algo plans expire at the end of the owner's trade day and
/// have no analysis lifecycle that could withdraw them, so before this a pending
/// ladder could rest at the broker for days. Filled legs are positions and stay
/// managed; only the unfilled remainder is withdrawn.
/// </summary>
public sealed partial class TradePlanRuntimeTests
{
  private const long ExpiryNow = 1_720_000_000;

  // The ladder fixture with its entry validity set relative to ExpiryNow.
  private static string ExpiringLadderPlan(long expiresAt, bool cancelOnExpiry = true) =>
    System.Text.RegularExpressions.Regex.Replace(
        LadderPlanJson,
        "\"expires_at\": 2000000000,(\\s*)\"legs\"",
        $"\"expires_at\": {expiresAt},$1\"legs\""
      )
      .Replace("\"cancel_on_expiry\": true", $"\"cancel_on_expiry\": {(cancelOnExpiry ? "true" : "false")}");

  private static (TradePlanRuntime Runtime, Func<long> Clock, Action<long> SetClock) ClockedRuntime(
    FakeTradePlanStore store
  )
  {
    var now = ExpiryNow;
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(now), _ => { }
    );
    return (runtime, () => now, value => now = value);
  }

  [Fact]
  public async Task ExpiredPlanWithdrawsEveryRestingEntryOrder()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(ExpiringLadderPlan(ExpiryNow + 3600));
    var client = new FakeTradePlanTradingClient();
    var (runtime, _, setClock) = ClockedRuntime(store);
    // Above both leg prices: both rest as pending limit orders.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 1), CancellationToken.None
    );
    var orderIds = Assert.Single(runtime.TrackedStates).Legs!.Select(l => l.BrokerOrderId!.Value).ToArray();
    Assert.Equal(2, orderIds.Length);

    // Before the validity ends nothing comes off.
    setClock(ExpiryNow + 3599);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 2), CancellationToken.None
    );
    Assert.Empty(client.CancelledOrderIds);
    Assert.Equal(2, client.PendingOrders.Count);

    setClock(ExpiryNow + 3600);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 3), CancellationToken.None
    );

    Assert.Equal(orderIds.Order(), client.CancelledOrderIds.Order());
    Assert.Empty(client.PendingOrders);
    Assert.Empty(runtime.TrackedStates);
    Assert.Equal("expired", store.Value($"execution:plan_state:{PlanId}"));
    Assert.Contains(store.Events, e => e.Type == "plan_expired");
    Assert.Empty(client.Closes);
  }

  [Fact]
  public async Task ExpiryWithdrawsOnlyTheUnfilledLegAndKeepsThePositionManaged()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(ExpiringLadderPlan(ExpiryNow + 3600));
    var client = new FakeTradePlanTradingClient();
    var (runtime, _, setClock) = ClockedRuntime(store);
    // L1 fills, L2 rests.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 1), CancellationToken.None
    );
    var partial = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, partial.Stage);
    var l1 = Assert.Single(partial.Legs!, leg => leg.LegId == "L1");
    var l2 = Assert.Single(partial.Legs!, leg => leg.LegId == "L2");

    setClock(ExpiryNow + 3601);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 2), CancellationToken.None
    );

    Assert.Equal([l2.BrokerOrderId!.Value], client.CancelledOrderIds);
    Assert.Empty(client.Closes);
    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(l1.BrokerPositionId, Assert.Single(state.Legs!, leg => leg.LegId == "L1").BrokerPositionId);
    Assert.Equal(TradePlanLegStages.Cancelled, Assert.Single(state.Legs!, leg => leg.LegId == "L2").Stage);
    Assert.DoesNotContain(store.Events, e => e.Type == "plan_expired");
  }

  [Fact]
  public async Task AFullyOpenPlanIsNeverTouchedByItsEntryExpiry()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(ExpiringLadderPlan(ExpiryNow + 60));
    var client = new FakeTradePlanTradingClient();
    var (runtime, _, setClock) = ClockedRuntime(store);
    // Both legs fill immediately (quote at or below both leg prices).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4084.00m, 4084.20m, 1), CancellationToken.None
    );
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, Assert.Single(runtime.TrackedStates).Stage);

    setClock(ExpiryNow + 7200);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4084.00m, 4084.20m, 2), CancellationToken.None
    );

    Assert.Empty(client.CancelledOrderIds);
    Assert.Empty(client.Closes);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, Assert.Single(runtime.TrackedStates).Stage);
  }

  [Fact]
  public async Task CancelOnExpiryFalseLeavesTheRestingOrdersAlone()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(ExpiringLadderPlan(ExpiryNow + 60, cancelOnExpiry: false));
    var client = new FakeTradePlanTradingClient();
    var (runtime, _, setClock) = ClockedRuntime(store);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 1), CancellationToken.None
    );
    Assert.Equal(2, client.PendingOrders.Count);

    setClock(ExpiryNow + 7200);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 2), CancellationToken.None
    );

    Assert.Empty(client.CancelledOrderIds);
    Assert.Equal(2, client.PendingOrders.Count);
  }
}

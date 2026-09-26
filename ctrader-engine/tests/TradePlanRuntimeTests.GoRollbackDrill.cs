using System.Text.Json;
using System.Text.Json.Nodes;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// S14H: the executor's half of the emergency rollback, for each stage a Go-origin plan can be in.
/// The cancel intent is the exact bytes Python's rollback wrote (contracts/autotrade/
/// go-rollback-cancel-intent.json, drift-guarded by algo-bot/tests/test_s14h_rollback_drills.py).
/// A rollback withdraws unexecuted work and NEVER closes or abandons an open position: it stays
/// managed to its normal exit.
/// </summary>
public sealed partial class TradePlanRuntimeTests
{
  private static Task WriteRollbackIntent(FakeTradePlanStore store) => store.SetStringAsync(
    IntentKey(GoPlanId), ContractFile("go-rollback-cancel-intent.json"), CancellationToken.None
  );

  private static JsonElement CancelAck(FakeTradePlanStore store) =>
    JsonDocument.Parse(store.Value(AckKey(GoPlanId))!).RootElement.Clone();

  private static string RestingLimitLadderJson()
  {
    var plan = JsonNode.Parse(GoLadderPlanJson(riskLegDisabled: true, entryType: "limit_ladder"))!.AsObject();
    foreach (var leg in plan["entry"]!["legs"]!.AsArray())
    {
      leg!.AsObject().Remove("order_type");
    }
    plan["entry"]!["legs"]!.AsArray()[0]!["price"] = "4354.50";
    return plan.ToJsonString();
  }

  private static readonly SpotPrice TargetHit = new("XAU", 4345.30m, 4345.50m, 9);

  [Fact]
  public async Task RollbackOfAPendingGoPlanThatWasNeverSubmittedCancelsItBeforeAnyOrder()
  {
    var (store, client, runtime, clock) = GoChain();
    await runtime.PollAsync(client, Symbol, new SpotPrice("XAU", 4340.00m, 4340.20m, 1), CancellationToken.None);   // outside the zone: waiting
    Assert.Equal(TradePlanRuntimeStage.Received, Assert.Single(runtime.TrackedStates).Stage);

    await WriteRollbackIntent(store);
    clock.Advance();
    await runtime.PollAsync(client, Symbol, InsideZone, CancellationToken.None);                                   // price now enters the zone

    Assert.Empty(client.MarketOrders);
    Assert.Empty(client.LimitOrders);
    Assert.Empty(runtime.TrackedStates);
    Assert.Equal("cancelled_unsubmitted", CancelAck(store).GetProperty("outcome").GetString());
    Assert.Equal("authority_rollback", CancelAck(store).GetProperty("source").GetString());
  }

  [Fact]
  public async Task RollbackOfASubmittedButUnfilledGoLadderCancelsEveryRestingOrder()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(RestingLimitLadderJson());
    var client = new FakeTradePlanTradingClient { AccountEquity = 2_000m, AccountBalance = 2_000m };
    var clock = new TickingClock();
    var runtime = new TradePlanRuntime(Options(), store, clock.Read, _ => { });
    await runtime.PollAsync(client, Symbol, new SpotPrice("XAU", 4350.00m, 4350.20m, 1), CancellationToken.None);
    Assert.Equal(2, client.PendingOrders.Count);                        // both legs rest below the zone: nothing filled

    await WriteRollbackIntent(store);
    clock.Advance();
    await runtime.PollAsync(client, Symbol, new SpotPrice("XAU", 4350.00m, 4350.20m, 2), CancellationToken.None);

    Assert.Empty(client.PendingOrders);
    Assert.Equal(2, client.CancelledOrderIds.Count);
    Assert.Empty(runtime.TrackedStates);
    Assert.Equal("cancelled_pending_orders", CancelAck(store).GetProperty("outcome").GetString());
  }

  [Fact]
  public async Task RollbackOfAPartiallyFilledGoLadderWithdrawsTheRestingLegAndTheFilledLegKeepsBeingManagedToItsTarget()
  {
    var (store, client, runtime, clock) = LadderChain(GoLadderPlanJson(riskLegDisabled: true), equity: 2_000m);
    await runtime.PollAsync(client, Symbol, LadderQuote, CancellationToken.None);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, Assert.Single(runtime.TrackedStates).Stage);

    await WriteRollbackIntent(store);
    clock.Advance();
    await runtime.PollAsync(client, Symbol, LadderQuote, CancellationToken.None);

    Assert.Empty(client.PendingOrders);                                 // the unfilled L2 is withdrawn
    Assert.Empty(client.Closes);                                        // the position is not
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, Assert.Single(runtime.TrackedStates).Stage);
    Assert.Equal("positions_kept_unfilled_cancelled", CancelAck(store).GetProperty("outcome").GetString());

    clock.Advance();
    client.NextCloseFillPrice = 4345.50m;
    await runtime.PollAsync(client, Symbol, TargetHit, CancellationToken.None);                                    // still managed: TP1 closes it normally
    Assert.NotEmpty(client.Closes);
    Assert.Contains(store.Events, e => e.Type == "position_closed");
    Assert.Empty(runtime.TrackedStates);
  }

  [Fact]
  public async Task RollbackWithAnOpenGoPositionNeverClosesItAndTheStopAndTargetStillApply()
  {
    var (store, client, runtime, clock) = GoChain();
    await runtime.PollAsync(client, Symbol, InsideZone, CancellationToken.None);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, Assert.Single(runtime.TrackedStates).Stage);

    await WriteRollbackIntent(store);
    clock.Advance();
    await runtime.PollAsync(client, Symbol, InsideZone, CancellationToken.None);

    Assert.Empty(client.Closes);
    Assert.Empty(client.CancelledOrderIds);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, Assert.Single(runtime.TrackedStates).Stage);
    Assert.Equal("positions_kept", CancelAck(store).GetProperty("outcome").GetString());
    Assert.DoesNotContain(store.Events, e => e.Type == "plan_cancelled");

    clock.Advance();
    await runtime.PollAsync(client, Symbol, TargetHit, CancellationToken.None);                                    // the plan's own target still closes it
    Assert.NotEmpty(client.Closes);
    Assert.Equal("completed", store.Value($"execution:plan_state:{GoPlanId}"));
  }
}

using System.Text.Json;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// S14B: the executor honours the cancel intent Python writes at
/// execution:plan_cancel:{plan_id} when a Go-derived plan's opportunity is
/// invalidated/expired or its authority scope is rolled back. Covers every
/// stage a plan can be in: never submitted, resting orders, partially
/// filled, fully open, retried broker failure, restart. Positions are never
/// closed by an intent.
/// </summary>
public sealed partial class TradePlanRuntimeTests
{
  private const string PlanId = "v8:plan-1";

  // Shared with algo-bot/tests/test_s14b_plan_cancel_contract.py.
  private static readonly JsonElement CancelContract = LoadCancelContract();

  private static JsonElement LoadCancelContract()
  {
    var directory = new DirectoryInfo(AppContext.BaseDirectory);
    while (directory is not null)
    {
      var candidate = Path.Combine(
        directory.FullName, "contracts", "autotrade", "plan-cancel-intent.json"
      );
      if (File.Exists(candidate))
      {
        return JsonDocument.Parse(File.ReadAllText(candidate)).RootElement.Clone();
      }
      directory = directory.Parent;
    }
    throw new FileNotFoundException(
      "contracts/autotrade/plan-cancel-intent.json was not found above "
      + AppContext.BaseDirectory
    );
  }

  private static string[] ContractList(string name) =>
    CancelContract.GetProperty(name).EnumerateArray().Select(e => e.GetString()!).ToArray();

  private static string IntentKey(string planId) =>
    CancelContract.GetProperty("intent_key").GetString()!.Replace("{plan_id}", planId);

  private static string AckKey(string planId) =>
    CancelContract.GetProperty("ack_key").GetString()!.Replace("{plan_id}", planId);

  private static string CancelIntentJson(string source = "authority_rollback") =>
    $$"""{"plan_id":"{{PlanId}}","reason":"rollback drill","source":"{{source}}","requested_at":1720000000}""";

  private static Task WriteCancelIntent(
    FakeTradePlanStore store, string planId = PlanId, string source = "authority_rollback"
  ) => store.SetStringAsync(
    IntentKey(planId),
    CancelIntentJson(source).Replace(PlanId, planId),
    CancellationToken.None
  );

  private static JsonElement Ack(FakeTradePlanStore store, string planId = PlanId)
  {
    var raw = store.Value(AckKey(planId));
    Assert.NotNull(raw);
    return JsonDocument.Parse(raw!).RootElement.Clone();
  }

  private static TradePlanRuntime NewRuntime(FakeTradePlanStore store) =>
    new(Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000), _ => { });

  [Fact]
  public async Task CancelIntentWrittenBeforeThePlanArrivesPreventsAnySubmission()
  {
    // A tombstone: Python withdrew the opportunity while the plan was still
    // on the stream. The plan is received, then cancelled before submission
    // even though the quote is inside the zone.
    var store = new FakeTradePlanStore();
    await WriteCancelIntent(store);
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = NewRuntime(store);

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    Assert.Empty(client.MarketOrders);
    Assert.Empty(client.LimitOrders);
    Assert.Empty(runtime.TrackedStates);
    Assert.Equal("cancelled", store.Value($"execution:plan_state:{PlanId}"));
    Assert.Contains(store.Events, e => e.Type == "plan_cancelled");
    var ack = Ack(store);
    Assert.Equal("cancelled_unsubmitted", ack.GetProperty("outcome").GetString());
    Assert.Equal("authority_rollback", ack.GetProperty("source").GetString());
  }

  [Fact]
  public async Task CancelIntentAfterReceiptStopsTheNextPollFromSubmitting()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = NewRuntime(store);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.Equal(TradePlanRuntimeStage.Received, Assert.Single(runtime.TrackedStates).Stage);

    await WriteCancelIntent(store, source: "opportunity_invalidated");
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    Assert.Empty(client.MarketOrders);
    Assert.Empty(runtime.TrackedStates);
    Assert.Equal("opportunity_invalidated", Ack(store).GetProperty("source").GetString());
  }

  [Fact]
  public async Task CancelIntentCancelsEveryRestingLadderLegWhenNothingFilled()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var runtime = NewRuntime(store);
    // Above both leg prices: both rest as pending limit orders.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 1), CancellationToken.None
    );
    var submitted = Assert.Single(runtime.TrackedStates);
    var orderIds = submitted.Legs!.Select(leg => leg.BrokerOrderId!.Value).ToArray();
    Assert.Equal(2, orderIds.Length);

    await WriteCancelIntent(store);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 2), CancellationToken.None
    );

    Assert.Equal(orderIds.Order(), client.CancelledOrderIds.Order());
    Assert.Empty(client.PendingOrders);
    Assert.Empty(runtime.TrackedStates);
    Assert.Equal("cancelled", store.Value($"execution:plan_state:{PlanId}"));
    Assert.Contains(store.Events, e => e.Type == "plan_cancelled");
    var ack = Ack(store);
    Assert.Equal("cancelled_pending_orders", ack.GetProperty("outcome").GetString());
    Assert.Equal(2, ack.GetProperty("cancelled_legs").GetInt32());
    Assert.Equal(0, ack.GetProperty("open_legs").GetInt32());
    Assert.Empty(client.Closes);
  }

  [Fact]
  public async Task CancelIntentKeepsTheFilledLegAndWithdrawsOnlyTheUnfilledOne()
  {
    // Partial ladder: L1 is a real position, L2 still rests. The position
    // keeps its stop and management; only the unfilled remainder comes off.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var runtime = NewRuntime(store);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 1), CancellationToken.None
    );
    var partial = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, partial.Stage);
    var l1 = Assert.Single(partial.Legs!, leg => leg.LegId == "L1");
    var l2 = Assert.Single(partial.Legs!, leg => leg.LegId == "L2");

    await WriteCancelIntent(store, source: "opportunity_invalidated");
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 2), CancellationToken.None
    );

    Assert.Equal([l2.BrokerOrderId!.Value], client.CancelledOrderIds);
    Assert.Empty(client.Closes);                       // no position is ever closed
    var state = Assert.Single(runtime.TrackedStates);  // still tracked and managed
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, state.Stage);
    var keptL1 = Assert.Single(state.Legs!, leg => leg.LegId == "L1");
    Assert.Equal(l1.BrokerPositionId, keptL1.BrokerPositionId);
    Assert.Equal(TradePlanLegStages.Cancelled, Assert.Single(state.Legs!, leg => leg.LegId == "L2").Stage);
    Assert.DoesNotContain(store.Events, e => e.Type == "plan_cancelled");
    Assert.NotEqual("cancelled", store.Value($"execution:plan_state:{PlanId}"));
    var ack = Ack(store);
    Assert.Equal("positions_kept_unfilled_cancelled", ack.GetProperty("outcome").GetString());
    Assert.Equal(1, ack.GetProperty("open_legs").GetInt32());
  }

  [Fact]
  public async Task CancelIntentNeverTouchesAFullyOpenPosition()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = NewRuntime(store);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, Assert.Single(runtime.TrackedStates).Stage);

    await WriteCancelIntent(store);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    Assert.Empty(client.Closes);
    Assert.Empty(client.CancelledOrderIds);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, Assert.Single(runtime.TrackedStates).Stage);
    Assert.DoesNotContain(store.Events, e => e.Type == "plan_cancelled");
    Assert.Equal("positions_kept", Ack(store).GetProperty("outcome").GetString());
  }

  [Fact]
  public async Task CancelIntentIsRetriedWhileTheBrokerCancelFailsAndOnlyAcksOnSuccess()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var runtime = NewRuntime(store);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 1), CancellationToken.None
    );
    await WriteCancelIntent(store);
    client.FailCancelCalls = 2;                        // both legs fail on the first attempt

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 2), CancellationToken.None
    );
    Assert.Equal(2, client.PendingOrders.Count);       // still live at the broker
    Assert.Single(runtime.TrackedStates);              // still tracked
    Assert.Null(store.Value(AckKey(PlanId)));
    Assert.DoesNotContain(store.Events, e => e.Type == "plan_cancelled");

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 3), CancellationToken.None
    );
    Assert.Empty(client.PendingOrders);
    Assert.Empty(runtime.TrackedStates);
    Assert.Equal("cancelled_pending_orders", Ack(store).GetProperty("outcome").GetString());
  }

  [Fact]
  public async Task CancelIntentStopsASubmittingPlanFromSendingItsRemainingLeg()
  {
    // L1 accepted, L2 threw: the plan is Submitting and would resend L2 on
    // the next poll. With an intent it must not.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient { ThrowOnCallNumber = 2 };
    var runtime = NewRuntime(store);
    var quote = new SpotPrice("XAU", 4092.00m, 4092.20m, 1);
    await Assert.ThrowsAsync<InvalidOperationException>(
      () => runtime.PollAsync(client, Symbol, quote, CancellationToken.None)
    );
    Assert.Equal(TradePlanRuntimeStage.Submitting, Assert.Single(runtime.TrackedStates).Stage);
    Assert.Single(client.LimitOrders);

    await WriteCancelIntent(store);
    await runtime.PollAsync(client, Symbol, quote, CancellationToken.None);

    Assert.Empty(client.LimitOrders);                  // L1 withdrawn, L2 never sent
    Assert.Single(client.CancelledOrderIds);
    Assert.Empty(runtime.TrackedStates);
    Assert.Equal("cancelled_pending_orders", Ack(store).GetProperty("outcome").GetString());
  }

  [Fact]
  public async Task CancelIntentSurvivesAnExecutorRestart()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var first = NewRuntime(store);
    await first.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 1), CancellationToken.None
    );
    Assert.Equal(2, client.PendingOrders.Count);

    await WriteCancelIntent(store);                    // written while the executor is down
    var restarted = NewRuntime(store);
    await restarted.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 2), CancellationToken.None
    );

    Assert.Empty(client.PendingOrders);
    Assert.Empty(restarted.TrackedStates);
    Assert.Equal("cancelled_pending_orders", Ack(store).GetProperty("outcome").GetString());
  }

  [Fact]
  public async Task CancelIntentIsAppliedWithoutAQuoteSoAStaleFeedCannotKeepOrdersAlive()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var runtime = NewRuntime(store);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 1), CancellationToken.None
    );
    await WriteCancelIntent(store);

    await runtime.PollAsync(client, Symbol, quote: null, CancellationToken.None);

    Assert.Empty(client.PendingOrders);
    Assert.Empty(runtime.TrackedStates);
  }

  [Fact]
  public async Task CancelIntentForAnotherPlanChangesNothing()
  {
    var store = new FakeTradePlanStore();
    await WriteCancelIntent(store, planId: "v8:some-other-plan");
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = NewRuntime(store);

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, Assert.Single(runtime.TrackedStates).Stage);
    Assert.Null(store.Value(AckKey(PlanId)));
  }

  [Fact]
  public async Task AnUnparseableIntentStillCancelsBecauseNotWithdrawingIsWorse()
  {
    var store = new FakeTradePlanStore();
    await store.SetStringAsync(IntentKey(PlanId), "{not json", CancellationToken.None);
    store.EnqueuePlan(PlanJson());
    var runtime = NewRuntime(store);
    var client = new FakeTradePlanTradingClient();

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    Assert.Empty(client.MarketOrders);
    Assert.Equal("unparseable", Ack(store).GetProperty("source").GetString());
  }

  [Fact]
  public async Task DryRunNeverCallsTheBrokerButStillWithdrawsThePlan()
  {
    var store = new FakeTradePlanStore();
    await WriteCancelIntent(store);
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(
      Options() with { DryRun = true }, store,
      () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000), _ => { }
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    Assert.Empty(client.MarketOrders);
    Assert.Empty(client.CancelledOrderIds);
    Assert.Empty(runtime.TrackedStates);
  }

  [Fact]
  public async Task AckMatchesTheSharedContractFieldsAndOutcomes()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var runtime = NewRuntime(store);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 1), CancellationToken.None
    );
    await WriteCancelIntent(store, source: ContractList("sources")[0]);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 2), CancellationToken.None
    );

    var ack = Ack(store);
    var fields = ack.EnumerateObject().Select(p => p.Name).Order(StringComparer.Ordinal).ToArray();
    Assert.Equal(ContractList("ack_fields"), fields);
    Assert.Contains(ack.GetProperty("outcome").GetString(), ContractList("outcomes"));
    Assert.Contains(ack.GetProperty("source").GetString(), ContractList("sources"));
  }

  [Fact]
  public void EveryOutcomeTheExecutorCanWriteIsDeclaredInTheContract()
  {
    // The four outcomes ApplyPlanCancelIntentsAsync can produce; a new one
    // must be added to the contract file (and its Python reader) first.
    string[] produced =
    [
      "cancelled_unsubmitted", "cancelled_pending_orders",
      "positions_kept_unfilled_cancelled", "positions_kept",
    ];
    Assert.Equal(ContractList("outcomes"), produced.Order(StringComparer.Ordinal).ToArray());
  }
}

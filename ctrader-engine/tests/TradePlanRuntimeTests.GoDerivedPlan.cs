using System.Text.Json;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// S14D: the executor half of the Go-origin chain. The plan bytes are the exact
/// TradePlan V8 that Python's real worker published from a Go Kafka event
/// (contracts/autotrade/go-derived-plan-xau-supply.json, produced and drift-guarded by
/// algo-bot/tests/test_s14d_go_full_chain.py). They go through the unchanged
/// TradePlanRuntime against a broker simulator with forced fills and injected broker
/// failures. The events the executor emits are the fixture the Python delivery suite
/// replays (contracts/autotrade/go-derived-plan-executor-events.json); regenerate both
/// with UPDATE_GOLDEN=1 and review the diff.
/// </summary>
public sealed partial class TradePlanRuntimeTests
{
  private const string GoPlanId = "v8:go_opp_chain";
  private const string GoMatchId = "go_opp_chain";
  private const decimal GoEntryBid = 4354.10m;

  private static string ContractFilePath(string name)
  {
    var directory = new DirectoryInfo(AppContext.BaseDirectory);
    while (directory is not null)
    {
      var candidate = Path.Combine(directory.FullName, "contracts", "autotrade", name);
      if (File.Exists(candidate) || Directory.Exists(Path.GetDirectoryName(candidate)!))
      {
        return candidate;
      }
      directory = directory.Parent;
    }
    throw new FileNotFoundException($"contracts/autotrade was not found above {AppContext.BaseDirectory}");
  }

  private static string ContractFile(string name) => File.ReadAllText(ContractFilePath(name));

  // The stream payload Python XADDs is compact JSON.
  private static string GoDerivedPlanJson() =>
    JsonSerializer.Serialize(JsonDocument.Parse(ContractFile("go-derived-plan-xau-supply.json")).RootElement);

  private sealed class TickingClock
  {
    public long Now = 1_790_000_100;
    public DateTimeOffset Read() => DateTimeOffset.FromUnixTimeSeconds(Now);
    public void Advance(long seconds = 60) => Now += seconds;
  }

  private const decimal GoTargetFill = 4345.50m;

  private static (FakeTradePlanStore Store, FakeTradePlanTradingClient Client, TradePlanRuntime Runtime, TickingClock Clock)
    GoChain(Action<FakeTradePlanTradingClient>? configure = null)
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(GoDerivedPlanJson());
    var client = new FakeTradePlanTradingClient { NextMarketFillPrice = GoEntryBid, NextCloseFillPrice = GoTargetFill };
    configure?.Invoke(client);
    var clock = new TickingClock();
    return (store, client, new TradePlanRuntime(Options(), store, clock.Read, _ => { }), clock);
  }

  private static readonly SpotPrice InsideZone = new("XAU", GoEntryBid, 4354.30m, 1);

  [Fact]
  public async Task GoDerivedPlanIsAcceptedSubmittedAndAcknowledgedWithItsProvenance()
  {
    var (store, client, runtime, _) = GoChain();

    await runtime.PollAsync(client, Symbol, InsideZone, CancellationToken.None);

    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, state.Stage);
    Assert.Equal(GoPlanId, state.PlanId);
    Assert.Equal("go_opp_chain", state.SetupId);                     // Python's match id, end to end
    var order = Assert.Single(client.MarketOrders);
    Assert.Equal(TradeDirection.Sell, order.Direction);              // supply -> SELL, never counter-directed
    Assert.True(order.Volume >= Symbol.MinVolume && order.Volume % Symbol.StepVolume == 0);
    Assert.Equal($"{GoPlanId}:L1", order.ClientOrderId);
    var comment = Assert.IsType<string>(order.Comment);
    Assert.Contains(GoPlanId, comment);
    Assert.Contains(state.ThesisId, comment);                        // the stable structural thesis rides on the broker order
    var ownership = TradePlanOwnership.TryParseOwnership(order.Comment, order.ClientOrderId);
    Assert.Equal(GoPlanId, ownership!.PlanId);
    Assert.Equal("L1", ownership.LegId);
    // executor acknowledgement Python can read back
    Assert.Equal("filled", store.Value($"execution:plan_state:{GoPlanId}"));
    var ack = JsonDocument.Parse(store.Value($"execution:plan_ack:{GoPlanId}")!).RootElement;
    Assert.Equal("filled", ack.GetProperty("state").GetString());
    Assert.Equal("apexvoid-auto", ack.GetProperty("executor").GetString());
    // one order_filled event linking the plan back to the Go-derived match
    var filled = Assert.Single(store.Events, e => e.Type == "order_filled");
    Assert.Equal(GoMatchId, filled.MatchId);
    Assert.Equal(GoPlanId, filled.CandidateId);
    Assert.Equal("XAU", filled.Symbol);
  }

  [Fact]
  public async Task AnInjectedBrokerFailureIsRetriedWithoutAnOrphanOrAnUnprotectedFill()
  {
    var (store, client, runtime, clock) = GoChain(c => c.FailMarketCalls = 1);

    await Assert.ThrowsAsync<InvalidOperationException>(
      () => runtime.PollAsync(client, Symbol, InsideZone, CancellationToken.None)
    );
    Assert.Empty(client.MarketOrders);                               // nothing accepted, nothing open
    Assert.DoesNotContain(store.Events, e => e.Type == "order_filled");
    Assert.NotEqual("filled", store.Value($"execution:plan_state:{GoPlanId}"));

    clock.Advance();
    await runtime.PollAsync(client, Symbol, InsideZone, CancellationToken.None);

    var order = Assert.Single(client.MarketOrders);                  // exactly one order after the retry
    Assert.Equal($"{GoPlanId}:L1", order.ClientOrderId);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, Assert.Single(runtime.TrackedStates).Stage);
    Assert.Single(store.Events, e => e.Type == "order_filled");
  }

  [Fact]
  public async Task RedeliveryOfTheSamePlanAfterARestartNeverDuplicatesTheBrokerOrder()
  {
    var (store, client, runtime, clock) = GoChain(c => c.RejectDuplicateClientOrderIds = true);
    await runtime.PollAsync(client, Symbol, InsideZone, CancellationToken.None);
    Assert.Single(client.MarketOrders);

    // Python (or the stream cursor) delivers the same plan again to a restarted executor.
    store.EnqueuePlan(GoDerivedPlanJson());
    var restarted = new TradePlanRuntime(Options(), store, clock.Read, _ => { });
    clock.Advance();
    await restarted.PollAsync(client, Symbol, InsideZone, CancellationToken.None);
    clock.Advance();
    await restarted.PollAsync(client, Symbol, InsideZone, CancellationToken.None);

    Assert.Single(client.MarketOrders);                              // still one broker order
    Assert.Single(store.Events, e => e.Type == "order_filled");
  }

  [Fact]
  public async Task StopLossHitOnTheBrokerSideIsReconciledAsAProtectedLoss()
  {
    var (store, client, runtime, clock) = GoChain();
    await runtime.PollAsync(client, Symbol, InsideZone, CancellationToken.None);
    var positionId = Assert.Single(Assert.Single(runtime.TrackedStates).Legs!).BrokerPositionId!.Value;

    client.PositionCloseReasonToReturn = PositionCloseReason.StopLossOrTakeProfit;
    client.PositionCloseExecutionPriceToReturn = 4359.83m;           // the plan's own stop
    client.RemovePosition(positionId);                               // the broker closed it
    clock.Advance();
    await runtime.PollAsync(client, Symbol, new SpotPrice("XAU", 4359.90m, 4360.10m, 2), CancellationToken.None);

    Assert.Empty(runtime.TrackedStates);                             // reconciled and released
    var closed = Assert.Single(store.Events, e => e.Type == "position_closed");
    Assert.Equal(GoMatchId, closed.MatchId);
    Assert.Empty(client.Closes);                                     // the executor never closed anything itself
    Assert.Equal("completed", store.Value($"execution:plan_state:{GoPlanId}"));
  }

  [Fact]
  public async Task TargetHitClosesTheGoDerivedPositionAndReportsTheFullLifecycle()
  {
    var (store, client, runtime, clock) = GoChain();
    await runtime.PollAsync(client, Symbol, InsideZone, CancellationToken.None);

    clock.Advance();
    await runtime.PollAsync(client, Symbol, new SpotPrice("XAU", 4345.30m, 4345.50m, 2), CancellationToken.None);

    Assert.NotEmpty(client.Closes);                                  // the executor closed at the plan's TP1
    var types = store.Events.Select(e => e.Type).ToArray();
    // Single-target plan: the full close at TP1 is reported as the closing event, not as a partial take_profit.
    Assert.Equal(["order_filled", "position_closed"], types);
    Assert.All(store.Events, e => Assert.Equal(GoMatchId, e.MatchId));
    Assert.Empty(runtime.TrackedStates);
    Assert.Equal("completed", store.Value($"execution:plan_state:{GoPlanId}"));
  }

  [Fact]
  public async Task ExecutorEventsAreTheSharedFixtureTheDeliverySuiteReplays()
  {
    var (store, client, runtime, clock) = GoChain();
    await runtime.PollAsync(client, Symbol, InsideZone, CancellationToken.None);
    clock.Advance();
    await runtime.PollAsync(client, Symbol, new SpotPrice("XAU", 4345.30m, 4345.50m, 2), CancellationToken.None);

    // Exactly what PublishAutoTradeEventAsync XADDs to auto_trade:events (payload field).
    var lines = store.Events
      .Select(e => JsonSerializer.Serialize(e, RedisJsonContext.Default.AutoTradeEvent))
      .Select(json => JsonSerializer.Serialize(JsonDocument.Parse(json).RootElement, new JsonSerializerOptions { WriteIndented = false }))
      .ToArray();
    var document = "[\n  " + string.Join(",\n  ", lines) + "\n]\n";
    var path = ContractFilePath("go-derived-plan-executor-events.json");
    if (Environment.GetEnvironmentVariable("UPDATE_GOLDEN") == "1")
    {
      File.WriteAllText(path, document);
    }
    Assert.Equal(File.ReadAllText(path).ReplaceLineEndings("\n"), document);
  }
}

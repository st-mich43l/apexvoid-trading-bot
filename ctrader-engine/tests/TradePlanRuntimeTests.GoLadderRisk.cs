using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// S14E: the executor-injected XAU risk leg on Go-origin ladder plans, and the worst-case group
/// risk it adds. Every number here comes from the orders the real TradePlanRuntime submits to the
/// broker simulator, not from a re-derivation. Documents (and pins) that max_group_risk_percent is
/// declarative today: the executor sizes from the owner's equity table and never reads it.
/// </summary>
public sealed partial class TradePlanRuntimeTests
{
  private const decimal XauPipSize = 0.1m;
  private const decimal XauPipValuePerLot = 10m;
  private const decimal GoStop = 4359.83m;
  private static readonly SpotPrice LadderQuote = new("XAU", 4354.10m, 4354.30m, 1);

  private static string GoLadderPlanJson(bool riskLegDisabled, decimal maxGroupRiskPercent = 2.0m, string entryType = "market_with_limit_scale")
  {
    var plan = JsonNode.Parse(ContractFile("go-derived-plan-xau-supply.json"))!.AsObject();
    plan["entry"] = JsonNode.Parse($$"""
    {
      "type": "{{entryType}}", "zone_low": "4352.5", "zone_high": "4356.0", "expires_at": 2000000000,
      "max_spread_ticks": 50, "max_slippage_ticks": 10,
      "legs": [
        {"leg_id": "L1", "price": "4354.10", "volume_ratio": "0.80", "order_type": "market"},
        {"leg_id": "L2", "price": "4355.50", "volume_ratio": "0.20", "order_type": "limit"}
      ]
    }
    """);
    var tags = new JsonArray(plan["analysis"]!["tags"]!.AsArray().Select(t => (JsonNode?)JsonValue.Create(t!.GetValue<string>())).Where(t => t!.GetValue<string>() != "risk_leg:disabled").ToArray());
    if (riskLegDisabled)
    {
      tags.Add("risk_leg:disabled");
    }
    plan["analysis"]!["tags"] = tags;
    plan["risk"]!["max_group_risk_percent"] = maxGroupRiskPercent.ToString(CultureInfo.InvariantCulture);
    return plan.ToJsonString();
  }

  private static (FakeTradePlanStore Store, FakeTradePlanTradingClient Client, TradePlanRuntime Runtime, TickingClock Clock)
    LadderChain(string planJson, decimal equity, bool riskLegOption = true)
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(planJson);
    var client = new FakeTradePlanTradingClient { AccountEquity = equity, AccountBalance = equity, NextMarketFillPrice = 4354.10m };
    var clock = new TickingClock();
    var runtime = new TradePlanRuntime(Options() with { ReactionRiskLegEnabled = riskLegOption }, store, clock.Read, _ => { });
    return (store, client, runtime, clock);
  }

  private static decimal WorstCaseLoss(FakeTradePlanTradingClient client, decimal stop)
  {
    decimal loss = 0m;
    foreach (var market in client.MarketOrders)
    {
      loss += (market.Volume / (decimal)Symbol.LotSize) * Math.Abs(GoEntryBid - stop) / XauPipSize * XauPipValuePerLot;
    }
    foreach (var limit in client.LimitOrders)
    {
      loss += (limit.Volume / (decimal)Symbol.LotSize) * Math.Abs(limit.LimitPrice - stop) / XauPipSize * XauPipValuePerLot;
    }
    return loss;
  }

  [Fact]
  public async Task GoOriginPlanTaggedRiskLegDisabledPlacesOnlyTheDeclaredLadder()
  {
    var (_, client, runtime, _) = LadderChain(GoLadderPlanJson(riskLegDisabled: true), equity: 2_000m);

    await runtime.PollAsync(client, Symbol, LadderQuote, CancellationToken.None);

    Assert.Single(client.MarketOrders);
    var limit = Assert.Single(client.LimitOrders);                     // L2 only: no RISK leg even though the executor flag is on
    Assert.EndsWith(":L2", limit.ClientOrderId);
    Assert.DoesNotContain(runtime.TrackedStates.Single().Legs!, leg => TradePlanRuntime.IsReactionRiskLeg(leg.LegId));
  }

  [Fact]
  public async Task WithoutTheTagThePlanGetsTheInjectedRiskLegExactlyAsPythonOwnedPlansDoToday()
  {
    var (_, client, runtime, _) = LadderChain(GoLadderPlanJson(riskLegDisabled: false), equity: 2_000m);

    await runtime.PollAsync(client, Symbol, LadderQuote, CancellationToken.None);

    Assert.Single(client.MarketOrders);
    Assert.Equal(2, client.LimitOrders.Count);                          // L2 and RISK
    var risk = Assert.Single(client.LimitOrders, l => l.ClientOrderId.EndsWith(":RISK", StringComparison.Ordinal));
    // SELL geometry: filled L1 < L2 < RISK < stop, RISK exactly 15 pips inside the stop, fixed 0.05 lots above the equity floor.
    Assert.Equal(GoStop - 15m * XauPipSize, risk.LimitPrice);
    Assert.Equal(500, risk.Volume);
    Assert.True(GoEntryBid < client.LimitOrders.First(l => l.ClientOrderId.EndsWith(":L2", StringComparison.Ordinal)).LimitPrice && client.LimitOrders.First(l => l.ClientOrderId.EndsWith(":L2", StringComparison.Ordinal)).LimitPrice < risk.LimitPrice && risk.LimitPrice < GoStop);
  }

  [Fact]
  public async Task RiskLegFlagOffPlacesNoRiskLegOnAnyPlanRegardlessOfTheTag()
  {
    var (_, client, runtime, _) = LadderChain(GoLadderPlanJson(riskLegDisabled: false), equity: 2_000m, riskLegOption: false);
    await runtime.PollAsync(client, Symbol, LadderQuote, CancellationToken.None);
    Assert.Single(client.LimitOrders);
  }

  public static IEnumerable<object[]> EquityGrid() =>
    new[] { 300m, 500m, 600m, 900m, 1_000m, 1_001m, 1_300m, 2_000m, 2_999m, 3_000m, 5_000m, 10_000m }.Select(e => new object[] { e });

  [Theory]
  [MemberData(nameof(EquityGrid))]
  public async Task WorstCaseGroupRiskIsMeasuredFromTheOrdersActuallySubmittedAndEveryVolumeIsBrokerValid(decimal equity)
  {
    var without = LadderChain(GoLadderPlanJson(riskLegDisabled: true), equity);
    var with = LadderChain(GoLadderPlanJson(riskLegDisabled: false), equity);
    await without.Runtime.PollAsync(without.Client, Symbol, LadderQuote, CancellationToken.None);
    await with.Runtime.PollAsync(with.Client, Symbol, LadderQuote, CancellationToken.None);

    foreach (var client in new[] { without.Client, with.Client })
    {
      var volumes = client.MarketOrders.Select(o => o.Volume).Concat(client.LimitOrders.Select(o => o.Volume)).ToArray();
      Assert.NotEmpty(volumes);
      Assert.All(volumes, v =>
      {
        Assert.True(v >= Symbol.MinVolume && v <= Symbol.MaxVolume, $"volume {v} outside broker limits at equity {equity}");
        Assert.Equal(0, v % Symbol.StepVolume);                          // step aligned
      });
    }
    var baseLoss = WorstCaseLoss(without.Client, GoStop);
    var withLoss = WorstCaseLoss(with.Client, GoStop);
    var riskLegLots = equity < 1_000m ? 0.02m : 0.05m;
    var riskLegLoss = riskLegLots * 15m * XauPipValuePerLot;              // 15 pips inside the stop
    Assert.Equal(riskLegLoss, withLoss - baseLoss);                       // the leg adds exactly its own worst case
    Console.WriteLine(string.Create(CultureInfo.InvariantCulture,
      $"RISKTABLE equity={equity} base_loss={baseLoss:F2} base_pct={baseLoss / equity * 100:F2} risk_leg_loss={riskLegLoss:F2} with_leg_pct={withLoss / equity * 100:F2} cap_pct=2.00"));
  }

  [Fact]
  public async Task MaxGroupRiskPercentIsDeclarativeTheExecutorNeverReadsIt()
  {
    // Pins current behaviour so enforcing it later is a deliberate change: an absurdly tight cap does not stop the order.
    var (_, client, runtime, _) = LadderChain(GoLadderPlanJson(riskLegDisabled: true, maxGroupRiskPercent: 0.0001m), equity: 2_000m);
    await runtime.PollAsync(client, Symbol, LadderQuote, CancellationToken.None);
    Assert.Single(client.MarketOrders);
    var loss = WorstCaseLoss(client, GoStop);
    Assert.True(loss / 2_000m * 100m > 0.0001m);
  }

  [Fact]
  public async Task PartialLadderFillAndRestartNeverResubmitAnyLegAndRiskLegFillsLater()
  {
    var (store, client, runtime, clock) = LadderChain(GoLadderPlanJson(riskLegDisabled: false), equity: 2_000m);
    await runtime.PollAsync(client, Symbol, LadderQuote, CancellationToken.None);
    var partial = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, partial.Stage);
    var ordersBefore = client.MarketOrders.Count + client.LimitOrders.Count;

    // Restart mid-ladder: the same plan is redelivered to a fresh executor process.
    store.EnqueuePlan(GoLadderPlanJson(riskLegDisabled: false));
    var restarted = new TradePlanRuntime(Options() with { ReactionRiskLegEnabled = true }, store, clock.Read, _ => { });
    clock.Advance();
    await restarted.PollAsync(client, Symbol, LadderQuote, CancellationToken.None);
    Assert.Equal(ordersBefore, client.MarketOrders.Count + client.LimitOrders.Count);

    // The resting RISK leg fills near the stop: the group is then fully open on one shared stop.
    var riskOrder = Assert.Single(client.PendingOrders, o => o.ClientOrderId.EndsWith(":RISK", StringComparison.Ordinal));
    client.FillPendingOrder(riskOrder.OrderId, riskOrder.LimitPrice);
    clock.Advance();
    await restarted.PollAsync(client, Symbol, new SpotPrice("XAU", 4358.30m, 4358.50m, 2), CancellationToken.None);
    var state = Assert.Single(restarted.TrackedStates);
    Assert.Contains(state.Legs!, leg => TradePlanRuntime.IsReactionRiskLeg(leg.LegId) && leg.BrokerPositionId is not null);
    Assert.Single(client.PendingOrders);                                  // only L2 still rests; nothing was resubmitted or cancelled
    Assert.Empty(client.CancelledOrderIds);
  }
}

using System.Globalization;
using System.Text.Json.Nodes;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// The executor places exactly the numbers the card prints. These are the incident's plan after
/// the planner rounded it to the card (2026-10-09, XAU Key Level BUY: legs 4147 / 4145, stop 4141,
/// risk leg 4142.5, targets 4153 / 4159 / 4165 / 4171). Every assertion reads what the real
/// TradePlanRuntime sent to the broker simulator.
/// </summary>
public sealed partial class TradePlanRuntimeTests
{
  private static string CardPricePlanJson()
  {
    var plan = JsonNode.Parse(ContractFile("go-derived-plan-xau-supply.json"))!.AsObject();
    plan["analysis"]!["direction"] = "BUY";
    plan["analysis"]!["strategy"] = "Key Level";
    plan["analysis"]!["strategy_family"] = "key_level";
    plan["entry"] = JsonNode.Parse("""
    {
      "type": "market_with_limit_scale", "zone_low": "4143", "zone_high": "4147", "expires_at": 2000000000,
      "max_spread_ticks": 50, "max_slippage_ticks": 10, "price_side": "ask",
      "legs": [
        {"leg_id": "L1", "price": "4147", "volume_ratio": "0.80", "order_type": "market"},
        {"leg_id": "L2", "price": "4145", "volume_ratio": "0.20", "order_type": "limit"}
      ],
      "risk_leg": {"price": "4142.5", "lots": "0.05"}
    }
    """);
    plan["stop"]!["price"] = "4141";
    plan["source_structure"]!["low"] = "4143";
    plan["source_structure"]!["high"] = "4147";
    plan["source_structure"]!["invalidation_price"] = "4141";
    plan["targets"] = JsonNode.Parse("""
    [
      {"target_id": "TP1", "type": "absolute", "price": "4153", "close_ratio": "0.4"},
      {"target_id": "TP2", "type": "absolute", "price": "4159", "close_ratio": "0.2"},
      {"target_id": "TP3", "type": "absolute", "price": "4165", "close_ratio": "0.2"},
      {"target_id": "TP4", "type": "absolute", "price": "4171", "close_ratio": "0.2"}
    ]
    """);
    plan["management"] = JsonNode.Parse("""
    {"be_after_target_id": "TP1", "be_buffer_ticks": 6, "never_worsen_stop": true}
    """);
    return plan.ToJsonString();
  }

  private static (FakeTradePlanStore Store, FakeTradePlanTradingClient Client, TradePlanRuntime Runtime, TickingClock Clock)
    CardPriceChain(decimal marketFill)
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(CardPricePlanJson());
    var client = new FakeTradePlanTradingClient
    {
      AccountEquity = 2_173m, AccountBalance = 2_173m, NextMarketFillPrice = marketFill,
    };
    var clock = new TickingClock();
    return (store, client, new TradePlanRuntime(Options(), store, clock.Read, _ => { }), clock);
  }

  [Fact]
  public async Task TheMarketLegWaitsWhileTheQuoteIsPastTheCardPriceAndFiresOnceItIsWithinTheDeclaredSlippage()
  {
    var (_, client, runtime, clock) = CardPriceChain(marketFill: 4147.05m);

    // The card price is 4147: an ask at 4147.50 is 5 pips past it, over the 10 tick budget.
    await runtime.PollAsync(client, Symbol, new SpotPrice("XAU", 4147.40m, 4147.50m, 1), CancellationToken.None);
    Assert.Empty(client.MarketOrders);
    Assert.Empty(client.LimitOrders);

    clock.Advance();
    await runtime.PollAsync(client, Symbol, new SpotPrice("XAU", 4146.95m, 4147.05m, 2), CancellationToken.None);
    Assert.Single(client.MarketOrders);
  }

  [Fact]
  public async Task TheRestingOrdersSitAtTheCardPricesAndEveryOrderCarriesTheCardStop()
  {
    var (_, client, runtime, _) = CardPriceChain(marketFill: 4147.05m);

    await runtime.PollAsync(client, Symbol, new SpotPrice("XAU", 4146.95m, 4147.05m, 1), CancellationToken.None);

    var market = Assert.Single(client.MarketOrders);
    var l2 = Assert.Single(client.LimitOrders, l => l.ClientOrderId.EndsWith(":L2", StringComparison.Ordinal));
    var risk = Assert.Single(client.LimitOrders, l => l.ClientOrderId.EndsWith(":RISK", StringComparison.Ordinal));
    Assert.Equal(4145.00m, l2.LimitPrice);                      // the deep leg, as printed
    Assert.Equal(4142.50m, risk.LimitPrice);                    // the declared risk leg
    // Each order's stop is the card's 4141.00 measured from where that order fills.
    Assert.Equal(TradePlanJson.RelativeStopLossForEntry(4147.05m, 4141.00m), market.RelativeStopLoss);
    Assert.Equal(TradePlanJson.RelativeStopLossForEntry(l2.LimitPrice, 4141.00m), l2.RelativeStopLoss);
    Assert.Equal(TradePlanJson.RelativeStopLossForEntry(risk.LimitPrice, 4141.00m), risk.RelativeStopLoss);
    // And the group stop the executor verifies on the filled position is the card's.
    Assert.NotEmpty(client.StopAmendments);
    Assert.All(client.StopAmendments, amendment => Assert.Equal(4141.00m, amendment.StopLoss));
  }

  [Fact]
  public async Task TargetsAreTakenAtTheCardPricesNotBefore()
  {
    var (store, client, runtime, clock) = CardPriceChain(marketFill: 4147.05m);
    await runtime.PollAsync(client, Symbol, new SpotPrice("XAU", 4146.95m, 4147.05m, 1), CancellationToken.None);
    foreach (var order in client.PendingOrders.ToList())
    {
      client.FillPendingOrder(order.OrderId);
    }

    // 4152.90 is a hair under TP1 (4153): nothing closes.
    clock.Advance();
    await runtime.PollAsync(client, Symbol, new SpotPrice("XAU", 4152.90m, 4153.00m, 2), CancellationToken.None);
    Assert.Empty(client.Closes);

    // The bid reaches 4153.00: TP1 books.
    clock.Advance();
    await runtime.PollAsync(client, Symbol, new SpotPrice("XAU", 4153.00m, 4153.10m, 3), CancellationToken.None);
    Assert.NotEmpty(client.Closes);
    Assert.Contains(store.Events, e => e.Type == "tp_booked" && e.Message.Contains("TP1", StringComparison.Ordinal));
  }
}

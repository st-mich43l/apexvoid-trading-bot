using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// The executor must not open new risk on a quote that outlived a feed stall or a
/// reconnect. SpotPrice carries the broker tick time (the same field Python's 5s
/// freshness rule reads), but the runtime used to evaluate whatever tick it last saw.
/// </summary>
public sealed partial class TradePlanRuntimeTests
{
  private static readonly DateTimeOffset StaleQuoteNow = DateTimeOffset.FromUnixTimeSeconds(1_800_000_000);

  private static SpotPrice InZoneQuote(long timestamp) =>
    new("XAU", 4089.05m, 4089.10m, timestamp);

  private static TradePlanRuntime StaleQuoteRuntime(FakeTradePlanStore store, int maximumQuoteAgeSeconds) =>
    new(
      Options() with { MaximumQuoteAgeSeconds = maximumQuoteAgeSeconds },
      store,
      () => StaleQuoteNow,
      _ => { }
    );

  [Fact]
  public async Task AStaleQuoteInsideTheZoneOpensNoEntry()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = StaleQuoteRuntime(store, 15);

    await runtime.PollAsync(
      client, Symbol, InZoneQuote(StaleQuoteNow.ToUnixTimeSeconds() - 60), CancellationToken.None
    );

    Assert.Empty(client.MarketOrders);
    Assert.Empty(client.LimitOrders);
    Assert.Equal(TradePlanRuntimeStage.Received, Assert.Single(runtime.TrackedStates).Stage);
  }

  [Fact]
  public async Task AFreshQuoteInsideTheZoneStillOpensTheEntry()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = StaleQuoteRuntime(store, 15);

    await runtime.PollAsync(
      client, Symbol, InZoneQuote(StaleQuoteNow.ToUnixTimeSeconds() - 3), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
  }

  [Fact]
  public async Task TheEntryFiresOnTheFirstFreshTickAfterAStall()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = StaleQuoteRuntime(store, 15);
    var now = StaleQuoteNow.ToUnixTimeSeconds();

    await runtime.PollAsync(client, Symbol, InZoneQuote(now - 120), CancellationToken.None);
    await runtime.PollAsync(client, Symbol, InZoneQuote(now - 119), CancellationToken.None);
    Assert.Empty(client.MarketOrders);

    await runtime.PollAsync(client, Symbol, InZoneQuote(now), CancellationToken.None);
    Assert.Single(client.MarketOrders);
  }

  [Fact]
  public async Task WithTheCheckDisabledAnOldTickBehavesAsBefore()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = StaleQuoteRuntime(store, 0);

    await runtime.PollAsync(
      client, Symbol, InZoneQuote(StaleQuoteNow.ToUnixTimeSeconds() - 3_600), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
  }

  [Fact]
  public async Task AStaleQuoteLeavesThePlanTrackedForTheNextFreshTick()
  {
    // Staleness defers the entry; it neither rejects nor forgets the plan.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = StaleQuoteRuntime(store, 15);
    var stale = InZoneQuote(StaleQuoteNow.ToUnixTimeSeconds() - 60);

    await runtime.PollAsync(client, Symbol, stale, CancellationToken.None);
    Assert.Empty(client.MarketOrders);
    Assert.Single(runtime.TrackedStates);
  }
}

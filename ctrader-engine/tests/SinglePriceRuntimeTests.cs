using Xunit;

namespace ApexVoid.CTraderFeed.Tests;

// TL-LIVE-2: a Trendline plan is ONE resting limit at the projected line: no ladder, no risk leg, never chased, never revived once stale.
public class SinglePriceRuntimeTests
{
  private const string PlanId = "v8:tl-live-2";
  private static readonly SymbolInfo Xau = new("XAU", "XAUUSD", 1, 2, 1, 100, 100, 100000, 10000);
  private const long Now = 1_720_000_000;

  private static string Plan(string direction, decimal price, decimal? proximity, long expiresAt = 2_000_000_000, string id = PlanId)
  {
    var buy = direction == "BUY";
    var stop = buy ? price - 6.00m : price + 6.00m;
    var target = buy ? price + 24.00m : price - 24.00m;
    var json = """
    {"version":8,"plan_id":"@ID","thesis_id":"t","setup_id":"s","symbol":"XAU","created_at":1719999600,"expires_at":@EXP,
     "analysis":{"strategy":"Trendline","strategy_family":"trendline","direction":"@DIR","context_timeframes":["M15"],"formation_timeframe":"M5","confirmation_timeframe":"M5",
       "formation_bar_ts":1719999300,"confirmation_bar_ts":1719999600,"score":3.0,"confluence":3,"bias":"up","regime":"trend","reasons":[],"tags":[]},
     "source_structure":{"structure_id":"trendline:M5:support:1:2","kind":"trendline","timeframe":"M5","low":"@LOW","high":"@HIGH","invalidation_price":"@STOP"},
     "entry":{"type":"single_limit","expires_at":@EXP,"order_price":"@PRICE","legs":[]@PROX},
     "stop":{"type":"absolute","price":"@STOP","source":"structure","structure_id":"trendline:M5:support:1:2","reason":"tl"},
     "targets":[{"target_id":"TP1","type":"absolute","price":"@TARGET","close_ratio":"1.0"}],
     "risk":{"risk_percent":"1.0","risk_multiplier":"1.0","max_volume":100000,"max_group_risk_percent":"2.0"},
     "sizing":{"mode":"equity_table","table_version":"owner_equity_v1","entry_distribution":"single","leg_ratios":[]},
     "management":{"be_after_target_id":null,"be_buffer_ticks":6,"never_worsen_stop":true},
     "execution_policy":{"allow_market":false,"allow_limit":true,"allow_partial_fill":true,"cancel_on_expiry":true},
     "provenance":{"analysis_engine_version":"tl","market_map_id":"","config_fingerprint":""}}
    """;
    return json
      .Replace("@ID", id).Replace("@DIR", direction).Replace("@EXP", expiresAt.ToString())
      .Replace("@PRICE", price.ToString()).Replace("@STOP", stop.ToString()).Replace("@TARGET", target.ToString())
      .Replace("@LOW", (price - 0.4m).ToString()).Replace("@HIGH", (price + 0.4m).ToString())
      .Replace("@PROX", proximity is null ? "" : $",\"proximity\":\"{proximity}\"");
  }

  private static TradePlanRuntime NewRuntime(LadderRuntimeTests.FakeStore store, Func<long>? clock = null) =>
    new(
      new AutoTradeOptions(true, false, "test", 1000, "events", "tl", Symbols: ["XAU"]),
      store,
      () => DateTimeOffset.FromUnixTimeSeconds((clock ?? (() => Now))()),
      _ => { },
      resolveBoundSymbol: _ => Xau,
      resolveUnits: _ => (0.1m, 10m)
    );

  private static SpotPrice Quote(decimal bid, decimal ask, long at = Now) => new("XAU", bid, ask, at);

  private static Task Poll(TradePlanRuntime runtime, LadderRuntimeTests.FakeBroker broker, SpotPrice quote) =>
    runtime.PollAsync(broker, Xau, quote, CancellationToken.None);

  private static LadderRuntimeTests.FakeStore StoreWith(params string[] payloads)
  {
    var store = new LadderRuntimeTests.FakeStore();
    for (var i = 0; i < payloads.Length; i++)
    {
      store.Stream.Add(new TradeStreamEntry($"{i + 1}-0", payloads[i]));
    }
    return store;
  }

  [Fact]
  public async Task QuoteAboveTheLineRestsExactlyOneLimitAtTheProjectedPrice()
  {
    var store = StoreWith(Plan("BUY", 3900.64m, 0.32m));
    var broker = new LadderRuntimeTests.FakeBroker();
    await Poll(NewRuntime(store), broker, Quote(3902.80m, 3903.00m));
    Assert.Empty(broker.Market);
    var order = Assert.Single(broker.Limits);
    Assert.Equal(3900.64m, order.LimitPrice); // to the digit: no rounding to a whole number
    Assert.Single(broker.Pending); // no L2, no L3, no risk leg
  }

  [Fact]
  public async Task ASellRestsOneLimitAtTheLineAbove()
  {
    var store = StoreWith(Plan("SELL", 3910.40m, 0.32m));
    var broker = new LadderRuntimeTests.FakeBroker();
    await Poll(NewRuntime(store), broker, Quote(3908.00m, 3908.20m));
    Assert.Equal(3910.40m, Assert.Single(broker.Limits).LimitPrice);
    Assert.Empty(broker.Market);
  }

  [Fact]
  public async Task PriceTouchingTheLineSubmitsTheSingleLimit()
  {
    var store = StoreWith(Plan("BUY", 3900.64m, 0.32m));
    var broker = new LadderRuntimeTests.FakeBroker();
    await Poll(NewRuntime(store), broker, Quote(3900.44m, 3900.64m));
    Assert.Equal(3900.64m, Assert.Single(broker.Limits).LimitPrice);
  }

  [Fact]
  public async Task PriceAlreadyBeyondTheLineIsNotChasedAndAReturnToTheLineIsStillTaken()
  {
    var store = StoreWith(Plan("BUY", 3900.64m, 0.32m));
    var broker = new LadderRuntimeTests.FakeBroker();
    var runtime = NewRuntime(store);
    await Poll(runtime, broker, Quote(3899.60m, 3899.80m)); // 0.84 under the line, proximity 0.32
    Assert.Empty(broker.Limits);
    Assert.Empty(broker.Market);
    await Poll(runtime, broker, Quote(3901.00m, 3901.20m)); // back above the line, plan still valid
    Assert.Equal(3900.64m, Assert.Single(broker.Limits).LimitPrice);
  }

  [Fact]
  public async Task AStaleProjectionNeverSubmitsAnOrder()
  {
    var store = StoreWith(Plan("BUY", 3900.64m, 0.32m, expiresAt: Now - 1));
    var broker = new LadderRuntimeTests.FakeBroker();
    await Poll(NewRuntime(store), broker, Quote(3902.80m, 3903.00m));
    Assert.Empty(broker.Limits);
    Assert.Empty(broker.Market);
  }

  [Fact]
  public async Task AnUnfilledOrderIsWithdrawnTheMomentItsProjectionGoesStale()
  {
    var store = StoreWith(Plan("BUY", 3900.64m, 0.32m, expiresAt: Now + 600));
    var broker = new LadderRuntimeTests.FakeBroker();
    var clock = Now;
    var runtime = NewRuntime(store, () => clock);
    await Poll(runtime, broker, Quote(3902.80m, 3903.00m, clock));
    Assert.Single(broker.Pending);
    clock = Now + 600; // valid_until reached: the line has materially moved
    await Poll(runtime, broker, Quote(3902.80m, 3903.00m, clock));
    Assert.Empty(broker.Pending);
    Assert.Single(broker.Cancelled);
    // It is withdrawn, never repriced: nothing new is placed afterwards.
    clock = Now + 700;
    await Poll(runtime, broker, Quote(3902.80m, 3903.00m, clock));
    Assert.Single(broker.Limits);
  }

  [Fact]
  public async Task ABrokenLineCancelsThePendingOrderThroughTheOpportunityLifecycle()
  {
    var store = StoreWith(Plan("BUY", 3900.64m, 0.32m));
    var broker = new LadderRuntimeTests.FakeBroker();
    var runtime = NewRuntime(store);
    await Poll(runtime, broker, Quote(3902.80m, 3903.00m));
    Assert.Single(broker.Pending);
    // Analysis Engine retires the opportunity (TRENDLINE_BROKEN); Algo Bot writes the durable cancel intent.
    store.Values[$"execution:plan_cancel:{PlanId}"] =
      "{\"plan_id\":\"" + PlanId + "\",\"reason\":\"trendline_broken\",\"source\":\"opportunity_invalidated\",\"requested_at\":1719999999}";
    await Poll(runtime, broker, Quote(3902.80m, 3903.00m));
    Assert.Empty(broker.Pending);
    Assert.Empty(broker.Closed);
  }

  [Fact]
  public async Task ADuplicateOpportunityPlacesOneOrderAndARestartDoesNotPlaceAnother()
  {
    var store = StoreWith(Plan("BUY", 3900.64m, 0.32m), Plan("BUY", 3900.64m, 0.32m));
    var broker = new LadderRuntimeTests.FakeBroker();
    await Poll(NewRuntime(store), broker, Quote(3902.80m, 3903.00m));
    Assert.Single(broker.Limits);
    await Poll(NewRuntime(store), broker, Quote(3902.80m, 3903.00m)); // executor restart over the same durable state
    await Poll(NewRuntime(store), broker, Quote(3902.80m, 3903.00m));
    Assert.Single(broker.Limits);
    Assert.Single(broker.Pending);
  }

  [Fact]
  public async Task ARestartAfterExpiryWithdrawsTheRestingOrderAndDoesNotReviveIt()
  {
    var store = StoreWith(Plan("BUY", 3900.64m, 0.32m, expiresAt: Now + 600));
    var broker = new LadderRuntimeTests.FakeBroker();
    await Poll(NewRuntime(store), broker, Quote(3902.80m, 3903.00m));
    Assert.Single(broker.Pending);
    var later = Now + 900;
    await Poll(NewRuntime(store, () => later), broker, Quote(3902.80m, 3903.00m, later));
    Assert.Empty(broker.Pending);
    Assert.Single(broker.Limits);
  }

  [Fact]
  public void TheProximityContractIsValidated()
  {
    var negative = TradePlanJson.DeserializePlan(Plan("BUY", 3900.64m, -1m));
    Assert.Throws<TradePlanContractException>(() => TradePlanValidator.Validate(negative));
    var ladder = Plan("BUY", 3900.64m, 0.32m).Replace("\"type\":\"single_limit\"", "\"type\":\"limit_ladder\"")
      .Replace("\"legs\":[]", "\"legs\":[{\"leg_id\":\"L1\",\"price\":\"3900.64\",\"volume_ratio\":\"1\"}]");
    Assert.Throws<TradePlanContractException>(() => TradePlanValidator.Validate(TradePlanJson.DeserializePlan(ladder)));
    TradePlanValidator.Validate(TradePlanJson.DeserializePlan(Plan("BUY", 3900.64m, 0.32m)));
    TradePlanValidator.Validate(TradePlanJson.DeserializePlan(Plan("BUY", 3900.64m, null))); // an older single_limit plan still validates
  }
}

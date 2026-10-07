using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// Manual Algo stop management is the pre-V8 AutoTradeEngine rule, kept for
/// manual plans only: TP1 moves the whole ladder to one shared group-economic
/// stop funded by the booked TP1 profit, TP2 moves it to the actual shallow
/// entry (TP1 is reserved for the TP3 trail), and later targets trail one
/// behind, two behind at the second-to-last rung. Autonomous plans keep
/// BE+buffer on the remaining legs.
/// </summary>
public sealed partial class TradePlanRuntimeTests
{
  private static string ManualFourTargetLadderJson() => """
  {
    "version": 8,
    "plan_id": "manual:9:0",
    "thesis_id": "manual-thesis:9",
    "setup_id": "manual:9:0",
    "symbol": "XAU",
    "created_at": 1719999600,
    "expires_at": 2000000000,
    "analysis": {
      "strategy": "Manual Algo",
      "strategy_family": "manual",
      "direction": "BUY",
      "context_timeframes": ["M15"],
      "formation_timeframe": "H1",
      "confirmation_timeframe": "M15",
      "formation_bar_ts": 1719999000,
      "confirmation_bar_ts": 1719999600,
      "score": 3.0,
      "confluence": 3,
      "bias": "up",
      "regime": "trend",
      "reasons": ["owner"],
      "tags": []
    },
    "source_structure": {
      "structure_id": "manual-zone",
      "kind": "demand",
      "timeframe": "M15",
      "low": "4085.00",
      "high": "4089.50",
      "invalidation_price": "4082.50"
    },
    "entry": {
      "type": "market_with_limit_scale",
      "zone_low": "4085.00",
      "zone_high": "4089.50",
      "expires_at": 2000000000,
      "legs": [
        {"leg_id": "L1", "price": "4089.10", "volume_ratio": "0.80", "order_type": "market"},
        {"leg_id": "L2", "price": "4085.00", "volume_ratio": "0.20", "order_type": "limit"}
      ]
    },
    "stop": {
      "type": "absolute",
      "price": "4082.50",
      "source": "owner_instruction",
      "structure_id": "manual-zone",
      "reason": "owner-entered /algo stop"
    },
    "targets": [
      {"target_id": "TP1", "type": "absolute", "price": "4096.00", "close_ratio": "0.4"},
      {"target_id": "TP2", "type": "absolute", "price": "4104.00", "close_ratio": "0.2"},
      {"target_id": "TP3", "type": "absolute", "price": "4110.00", "close_ratio": "0.2"},
      {"target_id": "TP4", "type": "absolute", "price": "4120.00", "close_ratio": "0.2"}
    ],
    "risk": {
      "risk_percent": "1.0",
      "risk_multiplier": "1.0",
      "max_volume": 100000,
      "max_group_risk_percent": "2.0"
    },
    "sizing": {
      "mode": "equity_table",
      "table_version": "owner_equity_v1",
      "entry_distribution": "zone_scale",
      "leg_ratios": ["0.80", "0.20"]
    },
    "management": {
      "be_after_target_id": "TP1",
      "be_buffer_ticks": 6,
      "never_worsen_stop": true
    },
    "execution_policy": {
      "allow_market": true,
      "allow_limit": true,
      "allow_partial_fill": true,
      "cancel_on_expiry": true
    },
    "provenance": {
      "analysis_engine_version": "manual",
      "market_map_id": "",
      "config_fingerprint": ""
    }
  }
  """;

  private static (TradePlanRuntime Runtime, FakeTradePlanStore Store, FakeTradePlanTradingClient Client)
    NewManualLadderRuntime()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(ManualFourTargetLadderJson());
    var client = new FakeTradePlanTradingClient { AccountEquity = 20_000m, AccountBalance = 20_000m };
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    return (runtime, store, client);
  }

  private static async Task<TradePlanRuntimeState> FillManualLadderAsync(
    TradePlanRuntime runtime, FakeTradePlanTradingClient client
  )
  {
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.00m, 4089.10m, 1), CancellationToken.None
    );
    var open = Assert.Single(runtime.TrackedStates);
    var l2Order = Assert.Single(open.Legs!, leg => leg.LegId == "L2").BrokerOrderId!.Value;
    client.FillPendingOrder(l2Order, fillPrice: 4085.00m);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4085.00m, 4085.10m, 2), CancellationToken.None
    );
    return Assert.Single(runtime.TrackedStates);
  }

  [Fact]
  public async Task ManualLadderTp1MovesTheGroupToTheFundedEconomicStopNotPerLegBreakeven()
  {
    var (runtime, _, client) = NewManualLadderRuntime();
    var filled = await FillManualLadderAsync(runtime, client);
    var l2Fill = Assert.Single(filled.Legs!, leg => leg.LegId == "L2").FillPrice!.Value;

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4096.50m, 4096.55m, 3), CancellationToken.None
    );

    var afterTp1 = Assert.Single(runtime.TrackedStates);
    Assert.True(afterTp1.BreakEvenApplied);
    var remaining = afterTp1.Legs!.Where(leg => leg.RemainingVolume > 0).ToArray();
    var vwap = remaining.Sum(leg => leg.FillPrice!.Value * leg.RemainingVolume)
      / remaining.Sum(leg => leg.RemainingVolume);
    // The shallow leg's TP1 profit funds room: the stop sits below the
    // runners' VWAP and never reaches the per-leg BE+6 ticks the V8 default
    // would have used, yet it never loosens the owner's original stop.
    Assert.True(afterTp1.CurrentStop < vwap, $"stop {afterTp1.CurrentStop} must be below VWAP {vwap}");
    Assert.True(afterTp1.CurrentStop < l2Fill + 6 * 0.01m);
    Assert.True(afterTp1.CurrentStop >= 4082.50m);
    Assert.True(afterTp1.BookedPipVolume > 0m);
    Assert.All(
      client.StopAmendments.TakeLast(remaining.Length),
      amendment => Assert.Equal(afterTp1.CurrentStop, amendment.StopLoss)
    );
  }

  [Fact]
  public async Task ManualLadderTp2MovesEverySurvivorToTheShallowEntryAndTp3TrailsToTp1()
  {
    var (runtime, store, client) = NewManualLadderRuntime();
    var filled = await FillManualLadderAsync(runtime, client);
    var shallowFill = Assert.Single(filled.Legs!, leg => leg.LegId == "L1").FillPrice!.Value;

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4096.50m, 4096.55m, 3), CancellationToken.None
    );
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4104.50m, 4104.55m, 4), CancellationToken.None
    );

    var afterTp2 = Assert.Single(runtime.TrackedStates);
    Assert.Equal(shallowFill, afterTp2.CurrentStop);      // the actual shallow entry, not TP1's 4096.00
    Assert.Contains(store.Events, e => e.Type == "sl_moved" && e.Message.Contains("shallow entry"));

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4110.50m, 4110.55m, 5), CancellationToken.None
    );

    // TP3 is the second-to-last rung of four: two behind, which is TP1.
    Assert.Equal(4096.00m, Assert.Single(runtime.TrackedStates).CurrentStop);
  }

  [Fact]
  public async Task AutonomousLaddersKeepPerLegBreakevenAfterTp1()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(ManualFourTargetLadderJson()
      .Replace("manual:9:0", "v8:auto-9")
      .Replace("\"strategy_family\": \"manual\"", "\"strategy_family\": \"trend_pullback\""));
    var client = new FakeTradePlanTradingClient { AccountEquity = 20_000m, AccountBalance = 20_000m };
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await FillManualLadderAsync(runtime, client);

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4096.50m, 4096.55m, 3), CancellationToken.None
    );

    var afterTp1 = Assert.Single(runtime.TrackedStates);
    var remaining = afterTp1.Legs!.Where(leg => leg.RemainingVolume > 0).ToArray();
    var vwap = remaining.Sum(leg => leg.FillPrice!.Value * leg.RemainingVolume)
      / remaining.Sum(leg => leg.RemainingVolume);
    // BE+6 ticks on the remaining legs' own VWAP: no funded room.
    Assert.Equal(
      decimal.Round(vwap + 6 * 0.01m, 2, MidpointRounding.AwayFromZero),
      afterTp1.CurrentStop
    );
  }

  // Exact numbers of the pre-V8 StopTrailPlannerTests: a 30-pip TP1 on a
  // closed 500-volume shallow slice, 900 and 600 still open of a 3,000 group,
  // BE buffer 6 ticks (0.6 pip).
  [Theory]
  [InlineData("BUY", 4351.5, 4350.0, 4347.0, 4350.02)]
  [InlineData("SELL", 4351.5, 4353.0, 4356.0, 4352.98)]
  public void ManualGroupBreakEvenLeavesTheWholeOriginalGroupPlusTheBuffer(
    string direction, double shallowEntry, double deepEntry, double heldStop, double expected
  )
  {
    var plan = TradePlanJson.DeserializePlan(
      ManualFourTargetLadderJson().Replace("\"direction\": \"BUY\"", $"\"direction\": \"{direction}\"")
    );
    TradePlanLegRuntimeState Leg(string id, double fill, long volume) => new(
      id, (decimal)fill, 0m, volume, 0m, id, BrokerPositionId: 1, FillPrice: (decimal)fill,
      FilledVolume: volume, RemainingVolume: volume, Stage: TradePlanLegStages.Managing
    );

    var result = TradePlanExecutionEngine.CalculateManualGroupBreakEven(
      plan,
      [Leg("L1", shallowEntry, 900), Leg("L2", deepEntry, 600)],
      groupInitialVolume: 3_000,
      bookedPipVolume: 30m * 500m,
      pipSize: 0.1m,
      currentStop: (decimal)heldStop,
      Symbol,
      plannedDeepestEntry: null
    );

    Assert.True(result.Improved);
    Assert.Equal((decimal)expected, result.NewStop);
  }

  [Fact]
  public void ManualGroupBreakEvenNeverWidensAStopAlreadyHeldAndFallsBackToProtectedBreakEven()
  {
    var plan = TradePlanJson.DeserializePlan(ManualFourTargetLadderJson());
    TradePlanLegRuntimeState Leg(string id, decimal fill, long volume) => new(
      id, fill, 0m, volume, 0m, id, BrokerPositionId: 1, FillPrice: fill,
      FilledVolume: volume, RemainingVolume: volume, Stage: TradePlanLegStages.Managing
    );

    var result = TradePlanExecutionEngine.CalculateManualGroupBreakEven(
      plan,
      [Leg("L1", 4351.5m, 900), Leg("L2", 4350.0m, 600)],
      groupInitialVolume: 3_000,
      bookedPipVolume: 30m * 500m,
      pipSize: 0.1m,
      currentStop: 4350.50m,                              // already tighter than the funded stop
      Symbol,
      plannedDeepestEntry: null
    );

    // The funded stop (4350.02) would loosen the held 4350.50, so the
    // remaining volume's own protected breakeven (VWAP 4350.90 + 0.06) wins.
    Assert.True(result.Improved);
    Assert.Equal(4350.96m, result.NewStop);
  }

  [Fact]
  public void ManualTrailTargetsFollowTheOldLadderRule()
  {
    var plan = TradePlanJson.DeserializePlan(ManualFourTargetLadderJson());
    var legs = new[]
    {
      new TradePlanLegRuntimeState(
        "L1", 4089.1m, 1m, 100, 0m, "c", BrokerPositionId: 1, FillPrice: 4089.1m,
        FilledVolume: 100, RemainingVolume: 100, Stage: TradePlanLegStages.Managing
      ),
    };

    // TP3 of four is the second-to-last rung: two behind, which is TP1.
    Assert.Equal(4096.00m, TradePlanExecutionEngine.PlanManualTrailStop(plan, 3, legs, Symbol)!.Value.Stop);
    // The final target has no trail; TP1 has its own breakeven rule.
    Assert.Null(TradePlanExecutionEngine.PlanManualTrailStop(plan, 4, legs, Symbol));
    Assert.Null(TradePlanExecutionEngine.PlanManualTrailStop(plan, 1, legs, Symbol));
    // A single-leg ladder trails TP2 to TP1 (no shallow-entry rule applies).
    Assert.Equal(4096.00m, TradePlanExecutionEngine.PlanManualTrailStop(plan, 2, legs, Symbol)!.Value.Stop);
  }
}

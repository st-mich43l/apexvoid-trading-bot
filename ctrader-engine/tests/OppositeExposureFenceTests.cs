using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// Pure tests for the engine-side opposite-exposure fence: FX never, XAU
/// >= 150 pips (inclusive) from every opposite group, judged from tracked
/// plan state plus actual broker positions/orders.
/// </summary>
public sealed class OppositeExposureFenceTests
{
  private static readonly OppositePositionPolicy Fx = new(false, null, 0.0001m);
  private static readonly OppositePositionPolicy Xau = new(true, 150m, 0.1m);
  private const long XauSymbolId = 7;

  private static OppositeExposureGroup Group(string direction, decimal entry, string id = "g1") =>
    new("plan_runtime", id, direction, entry, PlanId: id);

  [Theory]
  [InlineData("BUY", "SELL", 0.0)]
  [InlineData("SELL", "BUY", 0.1)]
  [InlineData("BUY", "SELL", 25)]
  [InlineData("SELL", "BUY", 150)]
  [InlineData("BUY", "SELL", 150.1)]
  [InlineData("SELL", "BUY", 5000)]
  public void FxOppositeIsBlockedAtAnyDistance(string incoming, string existing, double pips)
  {
    var entry = 1.2500m + (decimal)pips * 0.0001m;
    var verdict = OppositeExposureFence.Evaluate(
      "GBPUSD", incoming, entry, [Group(existing, 1.2500m)], Fx
    );

    Assert.False(verdict.Allowed);
    Assert.Equal("fx_opposite_position_not_allowed", verdict.ReasonCode);
    Assert.Equal(existing, verdict.Nearest!.Direction);
  }

  [Theory]
  [InlineData("SELL", "BUY", 4314.00, false)]
  [InlineData("SELL", "BUY", 4314.99, false)] // 149.9 pips
  [InlineData("SELL", "BUY", 4315.00, true)]  // 150.0 pips, inclusive
  [InlineData("SELL", "BUY", 4315.01, true)]  // 150.1 pips
  [InlineData("BUY", "SELL", 4285.01, false)]
  [InlineData("BUY", "SELL", 4285.00, true)]
  [InlineData("BUY", "SELL", 4284.99, true)]
  public void XauSeparationBoundaryIsInclusiveAt150Pips(
    string incoming, string existing, double entry, bool allowed
  )
  {
    var verdict = OppositeExposureFence.Evaluate(
      "XAU", incoming, (decimal)entry, [Group(existing, 4300.00m)], Xau
    );

    Assert.Equal(allowed, verdict.Allowed);
    Assert.Equal(allowed ? null : "xau_opposite_position_too_close", verdict.ReasonCode);
  }

  [Fact]
  public void XauMustClearEveryOppositeGroup()
  {
    var groups = new[]
    {
      Group("BUY", 4300.00m, "far"),
      Group("BUY", 4400.00m, "near"),
    };

    var blocked = OppositeExposureFence.Evaluate("XAU", "SELL", 4410.00m, groups, Xau);
    Assert.False(blocked.Allowed);
    Assert.Equal("near", blocked.Nearest!.GroupId);
    Assert.True(OppositeExposureFence.Evaluate("XAU", "SELL", 4415.00m, groups, Xau).Allowed);
  }

  [Fact]
  public void NoOppositeGroupsIsClear()
  {
    Assert.True(OppositeExposureFence.Evaluate("GBPUSD", "BUY", 1.25m, [], Fx).Allowed);
  }

  [Fact]
  public void XauPolicyWithoutMinimumFailsLoudly()
  {
    Assert.Throws<InvalidOperationException>(() => OppositeExposureFence.Evaluate(
      "XAU", "SELL", 4310m, [Group("BUY", 4300m)], new OppositePositionPolicy(true, null, 0.1m)
    ));
  }

  // --- group collection from runtime state + broker truth ---------------------

  private static TradePlanRuntimeState State(
    string planId, string direction, TradePlanRuntimeStage stage, string groupStage,
    decimal? intended = null, decimal? groupFill = null, long filled = 0,
    string symbol = "XAU"
  ) => new(
    planId, "thesis", "setup", symbol, direction, "market_watch", stage,
    GroupStage: groupStage, IntendedEntryPrice: intended,
    GroupWeightedFillPrice: groupFill, TotalFilledVolume: filled
  );

  [Fact]
  public void FilledPlanUsesGroupFillAndPendingPlanUsesPlannedEntry()
  {
    var groups = OppositeExposureFence.CollectOppositeGroups(
      "SELL", "XAUUSD", XauSymbolId,
      [
        State("filled", "BUY", TradePlanRuntimeStage.FullyOpen, "fully_open",
          intended: 4290m, groupFill: 4300m, filled: 10),
        State("pending", "BUY", TradePlanRuntimeStage.Received, "received", intended: 4500m),
      ],
      [], []
    );

    Assert.Equal(new[] { 4300m, 4500m }, groups.Select(group => group.EntryPrice).Order());
  }

  [Theory]
  [InlineData(TradePlanRuntimeStage.Closed, "closed")]
  [InlineData(TradePlanRuntimeStage.Received, "rejected")]
  [InlineData(TradePlanRuntimeStage.Received, "expired")]
  [InlineData(TradePlanRuntimeStage.Received, "cancelled")]
  [InlineData(TradePlanRuntimeStage.FullyOpen, "recovery_required")]
  public void TerminalAndRecoveryPlansDoNotBlockOnTheirOwn(
    TradePlanRuntimeStage stage, string groupStage
  )
  {
    var groups = OppositeExposureFence.CollectOppositeGroups(
      "BUY", "GBPUSD", 9,
      [State("p", "SELL", stage, groupStage, intended: 1.3m, symbol: "GBPUSD")],
      [], []
    );

    Assert.Empty(groups);
  }

  [Fact]
  public void RecoveryPlanStillBlocksWhileTheBrokerReportsItsPosition()
  {
    var position = new TradingPosition(
      501, 9, TradeDirection.Sell, 100, 1.3m, null, "apexvoid-auto",
      "v8|v8:rec|thesis|L1", ""
    );
    var groups = OppositeExposureFence.CollectOppositeGroups(
      "BUY", "GBPUSD", 9,
      [State("v8:rec", "SELL", TradePlanRuntimeStage.FullyOpen, "recovery_required",
        intended: 1.3m, symbol: "GBPUSD")],
      [position], []
    );

    var group = Assert.Single(groups);
    Assert.Equal("broker_position", group.Source);
  }

  [Fact]
  public void LadderLegsOfOneTrackedPlanCollapseIntoOneGroup()
  {
    var legs = new[]
    {
      new TradingPosition(501, XauSymbolId, TradeDirection.Buy, 100, 4301m, null,
        "apexvoid-auto", "v8|v8:ladder|thesis|L1", ""),
      new TradingPosition(502, XauSymbolId, TradeDirection.Buy, 100, 4299m, null,
        "apexvoid-auto", "v8|v8:ladder|thesis|L2", ""),
    };
    var groups = OppositeExposureFence.CollectOppositeGroups(
      "SELL", "XAU", XauSymbolId,
      [State("v8:ladder", "BUY", TradePlanRuntimeStage.PartiallyOpen, "partially_open",
        groupFill: 4300m, filled: 200)],
      legs, []
    );

    var group = Assert.Single(groups);
    Assert.Equal(4300m, group.EntryPrice);
    Assert.False(OppositeExposureFence.Evaluate("XAU", "SELL", 4314m, groups, Xau).Allowed);
    Assert.True(OppositeExposureFence.Evaluate("XAU", "SELL", 4315m, groups, Xau).Allowed);
  }

  [Fact]
  public void CompactTokenAndRecordedLegIdsMapBrokerItemsBackToTheirPlan()
  {
    const string planId = "v8:legacy-plan";
    var compact = new TradingPosition(
      501, XauSymbolId, TradeDirection.Buy, 100, 4301m, null, "apexvoid-auto",
      TradePlanOwnership.FormatComment(planId, "thesis", "L1"), ""
    );
    var byLegId = new TradingPosition(
      502, XauSymbolId, TradeDirection.Buy, 100, 4299m, null, "apexvoid-auto", "", ""
    );
    var state = State(planId, "BUY", TradePlanRuntimeStage.PartiallyOpen, "partially_open",
      groupFill: 4300m, filled: 200) with
    {
      Legs =
      [
        new TradePlanLegRuntimeState("L2", 4299m, 0.5m, 100, 0.01m, $"{planId}:L2",
          BrokerPositionId: 502),
      ],
    };

    var groups = OppositeExposureFence.CollectOppositeGroups(
      "SELL", "XAU", XauSymbolId, [state], [compact, byLegId], []
    );

    Assert.Equal("plan_runtime", Assert.Single(groups).Source);
  }

  [Fact]
  public void BrokerOnlyPositionsAndPendingOrdersCountOtherSymbolsAndSideDoNot()
  {
    var positions = new[]
    {
      new TradingPosition(1, 9, TradeDirection.Sell, 100, 1.3000m, null, "manual", "", ""),
      new TradingPosition(2, 9, TradeDirection.Buy, 100, 1.2000m, null, "manual", "", ""),
      new TradingPosition(3, 99, TradeDirection.Sell, 100, 1.3000m, null, "manual", "", ""),
    };
    var orders = new[]
    {
      new TradingPendingOrder(10, 9, TradeDirection.Sell, 100, 1.3100m, "manual", "", ""),
      new TradingPendingOrder(11, 99, TradeDirection.Sell, 100, 1.3100m, "manual", "", ""),
    };

    var groups = OppositeExposureFence.CollectOppositeGroups(
      "BUY", "GBPUSD", 9, [], positions, orders
    );

    Assert.Equal(
      new[] { "order:10", "position:1" },
      groups.Select(group => group.GroupId).Order()
    );
  }

  [Fact]
  public void OppositePolicyParsingFailsClosedOnMissingOrInvalidConfig()
  {
    static IReadOnlyDictionary<string, object?> Section(object? policy) =>
      new Dictionary<string, object?>
      {
        ["exposure"] = new Dictionary<string, object?> { ["opposite_position"] = policy },
      };

    Assert.Throws<ConfigurationV3Error>(() =>
      OppositePositionPolicy.Parse("EURUSD", new Dictionary<string, object?>(), 0.0001m));
    Assert.Throws<ConfigurationV3Error>(() =>
      OppositePositionPolicy.Parse("EURUSD", Section(new Dictionary<string, object?>()), 0.0001m));
    Assert.Throws<ConfigurationV3Error>(() => OppositePositionPolicy.Parse(
      "XAU", Section(new Dictionary<string, object?> { ["allowed"] = true }), 0.1m));
    Assert.Throws<ConfigurationV3Error>(() => OppositePositionPolicy.Parse(
      "EURUSD",
      Section(new Dictionary<string, object?>
      {
        ["allowed"] = false, ["minimum_separation_pips"] = 150L,
      }),
      0.0001m));

    var xau = OppositePositionPolicy.Parse(
      "XAU",
      Section(new Dictionary<string, object?>
      {
        ["allowed"] = true, ["minimum_separation_pips"] = 150L,
      }),
      0.1m);
    Assert.Equal(new OppositePositionPolicy(true, 150m, 0.1m), xau);
  }
}

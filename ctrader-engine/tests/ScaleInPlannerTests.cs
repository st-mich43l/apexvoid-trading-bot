using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class ScaleInPlannerTests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU",
    "XAUUSD",
    7,
    Digits: 2,
    PipPosition: 2,
    MinVolume: 100,
    StepVolume: 100,
    MaxVolume: 100_000,
    LotSize: 10_000
  );

  [Fact]
  public void WorkedExampleAAllowsExposureBoundAdd()
  {
    var decision = Plan(
      bookedPnl: 9m,
      addStopPips: 18m,
      open: [new(TradeDirection.Buy, 4000m, 4000.3m, 1_300)]
    );

    Assert.True(decision.Allowed);
    Assert.Equal(0.02m, decision.Lots);
    Assert.Equal(200, decision.Volume);
    Assert.Equal("exposure-bound", decision.BindingTerm);
    Assert.Equal(9.3m, decision.PostAddWorstCase);
    Assert.Equal(40m, decision.Budget);
    Assert.Contains("headroom_lots 0.02", decision.SizingLog);
  }

  [Fact]
  public void WorkedExampleBRejectsWhenExposureCeilingIsSpent()
  {
    var decision = Plan(
      bookedPnl: 0m,
      addStopPips: 18m,
      open: [new(TradeDirection.Buy, 4000m, 3999m, 2_100)]
    );

    Assert.False(decision.Allowed);
    Assert.Equal(
      "exposure ceiling reached; bank a partial first",
      decision.Reason
    );
  }

  [Fact]
  public void WorkedExampleCRejectsExhaustedLossCeilingBeforeSizing()
  {
    var decision = Plan(
      bookedPnl: 0m,
      addStopPips: 18m,
      open: [new(TradeDirection.Buy, 4000m, 3997.5m, 1_600)]
    );

    Assert.False(decision.Allowed);
    Assert.Contains("group loss ceiling exhausted", decision.Reason);
  }

  [Fact]
  public void RiskFreeModeWaitsForEnoughBookedProfit()
  {
    var open = new[] {
      new TrancheExposure(TradeDirection.Buy, 4000m, 4000.3m, 200),
    };
    var refused = Plan(19m, 18m, open, requireRiskFree: true);
    var allowed = Plan(20m, 18m, open, requireRiskFree: true);

    Assert.False(refused.Allowed);
    Assert.Contains("risk-free mode", refused.Reason);
    Assert.True(allowed.Allowed);
    Assert.True(allowed.PostAddWorstCase >= 0);
  }

  [Theory]
  [InlineData(200, 400, 4)]
  [InlineData(300, 300, 3)]
  public void SelectsFallbackLadderForAvailableSteps(
    long openVolume,
    long expectedAddVolume,
    int expectedTargets
  )
  {
    var decision = ScaleInPlanner.Plan(
      balance: 900m,
      riskPercent: 2m,
      pipValuePerLot: 10m,
      addRiskFraction: 0.5m,
      addStopPips: 15m,
      bookedPnl: 20m,
      openTranches: [new(TradeDirection.Buy, 4000m, 4000.3m, openVolume)],
      requireRiskFree: false,
      pipSize: 0.1m,
      Symbol,
      [30, 60, 90, 120, 200],
      [20, 20, 20, 20, 20]
    );

    Assert.True(decision.Allowed);
    Assert.Equal(expectedAddVolume, decision.Volume);
    Assert.Equal(expectedTargets, decision.TargetPlan!.TargetsPips.Count);
  }

  [Fact]
  public void OneStepAddIsRefusedAsLadderInfeasible()
  {
    var decision = ScaleInPlanner.Plan(
      balance: 900m,
      riskPercent: 2m,
      pipValuePerLot: 10m,
      addRiskFraction: 0.5m,
      addStopPips: 15m,
      bookedPnl: 20m,
      openTranches: [new(TradeDirection.Buy, 4000m, 4000.3m, 500)],
      requireRiskFree: false,
      pipSize: 0.1m,
      Symbol,
      [30, 60, 90, 120, 200],
      [20, 20, 20, 20, 20]
    );

    Assert.False(decision.Allowed);
    Assert.Contains("ladder infeasible", decision.Reason);
  }

  [Fact]
  public void UnrealisedGainCannotEnterBalanceBasedSizing()
  {
    var parameters = typeof(ScaleInPlanner)
      .GetMethod(nameof(ScaleInPlanner.Plan))!
      .GetParameters();

    Assert.DoesNotContain(parameters, parameter =>
      parameter.Name!.Contains("equity", StringComparison.OrdinalIgnoreCase)
      || parameter.Name.Contains("floating", StringComparison.OrdinalIgnoreCase)
    );
    var decision = Plan(
      bookedPnl: 9m,
      addStopPips: 18m,
      open: [new(TradeDirection.Buy, 4000m, 4000.3m, 1_300)]
    );
    Assert.Equal(0.02m, decision.HeadroomLots);
  }

  [Fact]
  public void RandomisedAllowedAddsAlwaysRespectBothCeilings()
  {
    var random = new Random(7821);
    for (var index = 0; index < 500; index++)
    {
      var balance = random.Next(500, 5_001);
      var tableLots = VolumePlanner.LotsForBalance(balance);
      var openLots = Math.Max(0.02m, tableLots - random.Next(2, 10) / 100m);
      var openVolume = VolumePlanner.VolumeForLots(openLots, Symbol);
      var entry = 4000m;
      var stopPips = random.Next(-5, 31);
      var stop = entry + stopPips * 0.1m;
      var booked = random.Next(0, 51);
      var decision = ScaleInPlanner.Plan(
        balance,
        2m,
        10m,
        0.5m,
        random.Next(15, 66),
        booked,
        [new(TradeDirection.Buy, entry, stop, openVolume)],
        false,
        0.1m,
        Symbol,
        [30, 60, 90, 120, 200],
        [20, 20, 20, 20, 20]
      );
      if (!decision.Allowed)
      {
        continue;
      }
      Assert.True(openLots + decision.Lots <= tableLots);
      Assert.True(decision.PostAddWorstCase >= -decision.Budget);
    }
  }

  [Fact]
  public void SizeRatioCapsATrancheToAFractionOfTheInitialTranche()
  {
    // 0.06 lot initial * 0.5 ratio = 0.03 lot - well under the exposure/
    // risk/add-cap terms this fixture would otherwise allow, so the new
    // term must be the one that binds.
    var decision = ScaleInPlanner.Plan(
      balance: 5_000m,
      riskPercent: 2m,
      pipValuePerLot: 10m,
      addRiskFraction: 0.5m,
      addStopPips: 15m,
      bookedPnl: 20m,
      openTranches: [new(TradeDirection.Buy, 4000m, 4000.3m, 600)],
      requireRiskFree: false,
      pipSize: 0.1m,
      Symbol,
      [30, 60, 90, 120, 200],
      [20, 20, 20, 20, 20],
      initialTrancheLots: 0.06m,
      addSizeRatio: 0.5m
    );

    Assert.True(decision.Allowed);
    Assert.Equal(0.03m, decision.Lots);
    Assert.Equal(300, decision.Volume);
    Assert.Equal("size-ratio-bound", decision.BindingTerm);
  }

  [Fact]
  public void SizeRatioBelowBrokerMinimumRejectsRatherThanRoundingToZero()
  {
    // 0.01 lot initial * 0.5 ratio = 0.005 lot, floored below the broker's
    // 0.01 lot (100-unit) minimum volume.
    var decision = ScaleInPlanner.Plan(
      balance: 5_000m,
      riskPercent: 2m,
      pipValuePerLot: 10m,
      addRiskFraction: 0.5m,
      addStopPips: 15m,
      bookedPnl: 20m,
      openTranches: [new(TradeDirection.Buy, 4000m, 4000.3m, 100)],
      requireRiskFree: false,
      pipSize: 0.1m,
      Symbol,
      [30, 60, 90, 120, 200],
      [20, 20, 20, 20, 20],
      initialTrancheLots: 0.01m,
      addSizeRatio: 0.5m
    );

    Assert.False(decision.Allowed);
    Assert.Contains("below broker minimum volume", decision.Reason);
  }

  [Fact]
  public void MissingInitialTrancheLotsLeavesSizeRatioCapInactive()
  {
    var withCap = Plan(bookedPnl: 9m, addStopPips: 18m,
      open: [new(TradeDirection.Buy, 4000m, 4000.3m, 1_300)]);
    var withoutInitial = ScaleInPlanner.Plan(
      balance: 2_000m, riskPercent: 2m, pipValuePerLot: 10m,
      addRiskFraction: 0.5m, addStopPips: 18m, bookedPnl: 9m,
      openTranches: [new(TradeDirection.Buy, 4000m, 4000.3m, 1_300)],
      requireRiskFree: false, pipSize: 0.1m, Symbol,
      [30, 60, 90, 120, 200], [20, 20, 20, 20, 20],
      initialTrancheLots: null, addSizeRatio: 0.5m
    );

    Assert.Equal(withCap.Lots, withoutInitial.Lots);
    Assert.Equal(withCap.BindingTerm, withoutInitial.BindingTerm);
  }

  private static ScaleInSizingDecision Plan(
    decimal bookedPnl,
    decimal addStopPips,
    IReadOnlyList<TrancheExposure> open,
    bool requireRiskFree = false
  ) => ScaleInPlanner.Plan(
    balance: 2_000m,
    riskPercent: 2m,
    pipValuePerLot: 10m,
    addRiskFraction: 0.5m,
    addStopPips,
    bookedPnl,
    open,
    requireRiskFree,
    pipSize: 0.1m,
    Symbol,
    [30, 60, 90, 120, 200],
    [20, 20, 20, 20, 20]
  );
}

using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class ScaleInPlannerTests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU",
    "XAUUSD",
    7,
    Digits: 2,
    PipPosition: 1,
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
    Assert.Equal(0.08m, decision.Lots);
    Assert.Equal(800, decision.Volume);
    Assert.Equal("exposure-bound", decision.BindingTerm);
    Assert.Equal(-1.5m, decision.PostAddWorstCase);
    Assert.Equal(40m, decision.Budget);
    Assert.Contains("headroom_lots 0.08", decision.SizingLog);
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
      new TrancheExposure(TradeDirection.Buy, 4000m, 4000.3m, 1_300),
    };
    var refused = Plan(9m, 18m, open, requireRiskFree: true);
    var allowed = Plan(11m, 18m, open, requireRiskFree: true);

    Assert.False(refused.Allowed);
    Assert.Contains("risk-free mode", refused.Reason);
    Assert.True(allowed.Allowed);
    Assert.True(allowed.PostAddWorstCase >= 0);
  }

  [Theory]
  [InlineData(200, 300, 3)]
  [InlineData(300, 200, 2)]
  public void SelectsFallbackLadderForAvailableSteps(
    long openVolume,
    long expectedAddVolume,
    int expectedTargets
  )
  {
    var decision = ScaleInPlanner.Plan(
      balance: 500m,
      riskPercent: 2m,
      pipValuePerLot: 10m,
      addRiskFraction: 0.5m,
      addStopPips: 15m,
      bookedPnl: 20m,
      openTranches: [new(TradeDirection.Buy, 4000m, 4000.3m, openVolume)],
      requireRiskFree: false,
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
      balance: 500m,
      riskPercent: 2m,
      pipValuePerLot: 10m,
      addRiskFraction: 0.5m,
      addStopPips: 15m,
      bookedPnl: 20m,
      openTranches: [new(TradeDirection.Buy, 4000m, 4000.3m, 400)],
      requireRiskFree: false,
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
    Assert.Equal(0.08m, decision.HeadroomLots);
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
    Symbol,
    [30, 60, 90, 120, 200],
    [20, 20, 20, 20, 20]
  );
}

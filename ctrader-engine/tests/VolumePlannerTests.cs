using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class VolumePlannerTests
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

  [Theory]
  [InlineData(199.99, 0)]
  [InlineData(200, 0.02)]
  [InlineData(500, 0.05)]
  [InlineData(1000, 0.11)]
  [InlineData(2000, 0.20)]
  [InlineData(3000, 0.31)]
  [InlineData(5000, 0.36)]
  [InlineData(10000, 0.36)]
  public void MapsBalanceBandsAndFloorsToOneCentLotStep(
    double balance,
    double expectedLots
  )
  {
    Assert.Equal(
      Convert.ToDecimal(expectedLots),
      VolumePlanner.LotsForBalance(Convert.ToDecimal(balance))
    );
  }

  [Theory]
  [InlineData(25, 0.16)]
  [InlineData(15, 0.20)]
  public void InitialSizeUsesMinimumOfRiskAndEquityTable(
    double stopPips,
    double expectedLots
  )
  {
    var result = VolumePlanner.SizeInitial(
      balance: 2_000m,
      riskPercent: 2m,
      sizingMode: "min",
      stopPips: Convert.ToDecimal(stopPips),
      pipValuePerLot: 10m,
      Symbol,
      [30, 60, 90, 120, 200],
      [20, 20, 20, 20, 20]
    );

    Assert.Equal(Convert.ToDecimal(expectedLots), result.Lots);
    Assert.StartsWith($"sizing=min lots={expectedLots:0.00}", result.BindingTerm);
    Assert.Contains("risk ", result.BindingTerm);
    Assert.Contains("table 0.20", result.BindingTerm);
    Assert.True(result.Lots <= result.TableLots);
    Assert.True(result.Lots * result.StopPips * 10m <= result.Budget);
  }

  [Theory]
  [InlineData(999.99, 0.08, 1000, 0.11)]
  [InlineData(1999.99, 0.15, 2000, 0.20)]
  [InlineData(2999.99, 0.25, 3000, 0.31)]
  public void PreservesIntentionalBoundarySteps(
    double belowBalance,
    double belowLots,
    double boundaryBalance,
    double boundaryLots
  )
  {
    Assert.Equal(
      Convert.ToDecimal(belowLots),
      VolumePlanner.LotsForBalance(Convert.ToDecimal(belowBalance))
    );
    Assert.Equal(
      Convert.ToDecimal(boundaryLots),
      VolumePlanner.LotsForBalance(Convert.ToDecimal(boundaryBalance))
    );
  }

  [Fact]
  public void FloorsEquityTableToOneCentLotStep()
  {
    Assert.Equal(0.20m, VolumePlanner.LotsForBalance(2_098m));
  }

  [Fact]
  public void RejectsSizingBelowBalanceFloor()
  {
    var error = Assert.Throws<VolumePlanningException>(() => Size(
      balance: 199.99m,
      sizingMode: "min"
    ));

    Assert.Contains("below the $200 sizing floor", error.Message);
  }

  [Theory]
  [InlineData("table", 0.20)]
  [InlineData("risk", 0.06)]
  [InlineData("min", 0.06)]
  public void SelectsExplicitSizingMode(string sizingMode, double expectedLots)
  {
    var result = Size(2_072.02m, sizingMode);

    Assert.Equal(Convert.ToDecimal(expectedLots), result.Lots);
    Assert.Equal(0.20m, result.TableLots);
    Assert.Equal(
      $"sizing={sizingMode} lots={expectedLots:0.00} "
        + "(risk 0.06, table 0.20)",
      result.BindingTerm
    );
  }

  [Fact]
  public void TableModeStillEnforcesBrokerMinimumVolume()
  {
    var brokerMinimum = Symbol with { MinVolume = 300 };

    var error = Assert.Throws<VolumePlanningException>(() =>
      VolumePlanner.SizeInitial(
        balance: 200m,
        riskPercent: 2m,
        sizingMode: "table",
        stopPips: 65m,
        pipValuePerLot: 10m,
        brokerMinimum,
        [30, 60, 90, 120, 200],
        [20, 20, 20, 20, 20]
      )
    );

    Assert.Contains("below broker minimum volume", error.Message);
    Assert.Contains("sizing=table", error.Message);
  }

  [Fact]
  public void ConvertsLotsToBrokerVolume()
  {
    Assert.Equal(200, VolumePlanner.VolumeForLots(0.02m, Symbol));
    Assert.Equal(900, VolumePlanner.VolumeForLots(0.09m, Symbol));
  }

  [Fact]
  public void LiveAccountSizingFloorsWithinTwoPercentRiskBudget()
  {
    var result = VolumePlanner.SizeInitial(
      balance: 2_072.02m,
      riskPercent: 2m,
      sizingMode: "min",
      stopPips: 60m,
      pipValuePerLot: 10m,
      Symbol,
      [30, 60, 90, 120, 200],
      [20, 20, 20, 20, 20]
    );

    Assert.Equal(600, result.Volume);
    Assert.Equal(0.06m, result.Lots);
    Assert.Equal(36m, result.Lots * result.StopPips * 10m);
    Assert.True(result.Lots * result.StopPips * 10m <= result.Budget);
  }

  [Fact]
  public void BrokerPipSizeIsDiagnosticOnly()
  {
    Assert.Equal(0.01m, VolumePlanner.BrokerPipSize(Symbol));
  }

  [Theory]
  [InlineData(200, new[] { 30, 90 }, new[] { 1, 3 })]
  [InlineData(300, new[] { 30, 60, 90 }, new[] { 1, 2, 3 })]
  [InlineData(400, new[] { 30, 60, 90, 120 }, new[] { 1, 2, 3, 4 })]
  [InlineData(500, new[] { 30, 60, 90, 120, 200 }, new[] { 1, 2, 3, 4, 5 })]
  public void AdaptsTargetsToAvailableBrokerSteps(
    long volume,
    int[] expectedTargets,
    int[] expectedOrdinals
  )
  {
    var plan = Plan(volume);

    Assert.Equal(expectedTargets, plan.TargetsPips);
    Assert.Equal(expectedOrdinals, plan.TargetOrdinals);
    Assert.Equal(
      Enumerable.Repeat(100L, expectedTargets.Length),
      plan.Slices
    );
  }

  [Fact]
  public void RejectsVolumeThatCannotSupportTwoExits()
  {
    var error = Assert.Throws<VolumePlanningException>(() => Plan(100));

    Assert.Contains("minimum two broker-valid exits", error.Message);
  }

  [Fact]
  public void OneTargetUsesTheEntireBrokerValidVolume()
  {
    var plan = VolumePlanner.BuildTargetPlan(
      100,
      Symbol,
      [70],
      [100]
    );

    Assert.Equal(new long[] { 100 }, plan.Slices);
    Assert.Equal(new[] { 70 }, plan.TargetsPips);
    Assert.Equal(new[] { 1 }, plan.TargetOrdinals);
  }

  [Fact]
  public void WeightedLargestRemainderProducesExactSteps()
  {
    Assert.Equal(
      new long[] { 500, 500, 600, 400 },
      VolumePlanner.SplitWeighted(2_000, Symbol, [25, 25, 30, 20])
    );
  }

  [Fact]
  public void RoundingProneWeightsStayWithinOneStepAndSumExactly()
  {
    var weights = new[] { 17, 19, 23, 41 };
    var slices = VolumePlanner.SplitWeighted(2_300, Symbol, weights);

    Assert.Equal(2_300, slices.Sum());
    for (var index = 0; index < weights.Length; index++)
    {
      var actualSteps = (decimal)slices[index] / Symbol.StepVolume;
      var idealSteps = 23m * weights[index] / weights.Sum();
      Assert.True(Math.Abs(actualSteps - idealSteps) <= 1m);
    }
  }

  private static TargetVolumePlan Plan(long volume) =>
    VolumePlanner.BuildTargetPlan(
      volume,
      Symbol,
      [30, 60, 90, 120, 200],
      [20, 20, 20, 20, 20]
    );

  private static InitialSizingResult Size(
    decimal balance,
    string sizingMode
  ) => VolumePlanner.SizeInitial(
    balance,
    riskPercent: 2m,
    sizingMode,
    stopPips: 65m,
    pipValuePerLot: 10m,
    Symbol,
    [30, 60, 90, 120, 200],
    [20, 20, 20, 20, 20]
  );
}

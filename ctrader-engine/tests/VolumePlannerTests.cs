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
  [InlineData(199, 0)]
  [InlineData(200, 0.02)]
  [InlineData(499, 0.04)]
  [InlineData(500, 0.05)]
  [InlineData(875, 0.09)]
  [InlineData(999, 0.10)]
  [InlineData(1000, 0.11)]
  [InlineData(1500, 0.16)]
  [InlineData(2000, 0.21)]
  [InlineData(2500, 0.26)]
  [InlineData(3000, 0.31)]
  [InlineData(4000, 0.33)]
  [InlineData(4999, 0.35)]
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
  [InlineData(25, 0.16, "risk-bound")]
  [InlineData(15, 0.21, "equity-table-bound")]
  public void InitialSizeUsesMinimumOfRiskAndEquityTable(
    double stopPips,
    double expectedLots,
    string expectedBinding
  )
  {
    var result = VolumePlanner.SizeInitial(
      balance: 2_000m,
      riskPercent: 2m,
      stopPips: Convert.ToDecimal(stopPips),
      pipValuePerLot: 10m,
      Symbol,
      [30, 60, 90, 120, 200],
      [20, 20, 20, 20, 20]
    );

    Assert.Equal(Convert.ToDecimal(expectedLots), result.Lots);
    Assert.Equal(expectedBinding, result.BindingTerm);
    Assert.True(result.Lots <= result.TableLots);
    Assert.True(result.Lots * result.StopPips * 10m <= result.Budget);
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
}

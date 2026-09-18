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
  [InlineData(599.99, 0.04)]
  [InlineData(600, 0.10)]
  [InlineData(900, 0.10)]
  [InlineData(1000, 0.10)]
  [InlineData(1000.01, 0.12)]
  [InlineData(1300, 0.12)]
  [InlineData(1500, 0.12)]
  [InlineData(1999, 0.12)]
  [InlineData(2000, 0.15)]
  [InlineData(3000, 0.25)]
  [InlineData(4000, 0.28)]
  [InlineData(5000, 0.30)]
  [InlineData(10000, 0.30)]
  public void MapsEquityBandsAndRoundsToOneCentLotStep(
    double equity,
    double expectedLots
  )
  {
    Assert.Equal(
      Convert.ToDecimal(expectedLots),
      VolumePlanner.LotsForEquity(Convert.ToDecimal(equity))
    );
  }

  [Fact]
  public void LotsForEquityAboveOneThousandIsTwelveCents()
  {
    Assert.Equal(0.12m, VolumePlanner.LotsForEquity(1_300m));
  }

  [Theory]
  [InlineData(30, 0.13)]
  [InlineData(15, 0.15)]
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
    Assert.Contains("table 0.15", result.BindingTerm);
    Assert.True(result.Lots <= result.TableLots);
    Assert.True(result.Lots * result.StopPips * 10m <= result.Budget);
  }

  [Theory]
  [InlineData(599.99, 0.04, 600, 0.10)]
  [InlineData(1000, 0.10, 1000.01, 0.12)]
  [InlineData(2999.99, 0.15, 3000, 0.25)]
  public void PreservesIntentionalBoundarySteps(
    double belowEquity,
    double belowLots,
    double boundaryEquity,
    double boundaryLots
  )
  {
    Assert.Equal(
      Convert.ToDecimal(belowLots),
      VolumePlanner.LotsForEquity(Convert.ToDecimal(belowEquity))
    );
    Assert.Equal(
      Convert.ToDecimal(boundaryLots),
      VolumePlanner.LotsForEquity(Convert.ToDecimal(boundaryEquity))
    );
  }

  [Theory]
  [InlineData(2_000, 0.15)]
  [InlineData(2_500, 0.18)]
  [InlineData(2_999.99, 0.20)]
  [InlineData(3_000, 0.20)]
  [InlineData(4_000, 0.25)]
  [InlineData(5_000, 0.30)]
  public void FxEquitySizingSmoothsTheThreeThousandBoundary(
    double equity,
    double expectedLots
  )
  {
    Assert.Equal(
      Convert.ToDecimal(expectedLots),
      VolumePlanner.LotsForEquity(Convert.ToDecimal(equity), useFxEquitySizing: true)
    );
  }

  [Fact]
  public void RoundsEquityTableToOneCentLotStep()
  {
    Assert.Equal(0.25m, VolumePlanner.LotsForEquity(3_196m));
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
  [InlineData("table", 0.15)]
  [InlineData("equity_table", 0.15)]
  [InlineData("risk", 0.06)]
  [InlineData("min", 0.06)]
  public void SelectsExplicitSizingMode(string sizingMode, double expectedLots)
  {
    var result = Size(2_072.02m, sizingMode);

    Assert.Equal(Convert.ToDecimal(expectedLots), result.Lots);
    Assert.Equal(0.15m, result.TableLots);
    Assert.Equal(
      $"sizing={sizingMode} lots={expectedLots:0.00} "
        + "(risk 0.06, table 0.15)",
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

  [Fact]
  public void SplitEntryVolumePrefersRatioRoundedAllocationOverSplitWeighted()
  {
    // 0.11 lots / 70-30: Round(0.077, 2 AwayFromZero)=0.08 → L1=800 L2=300.
    // SplitWeighted on the same input yields 700/400 (0.07+0.04).
    var total = VolumePlanner.VolumeForLots(0.11m, Symbol);
    Assert.Equal(1_100, total);

    var slices = VolumePlanner.SplitEntryVolume(
      total, Symbol, [0.70m, 0.30m]
    );

    Assert.Equal(new long[] { 800, 300 }, slices);
    Assert.NotEqual(
      slices,
      VolumePlanner.SplitWeighted(total, Symbol, [70, 30])
    );
  }

  [Fact]
  public void FixFirstLegVolumeOverridesFirstSliceAndSplitsRemainderEvenly()
  {
    var plan = Plan(3_000); // equal 600 x 5 before the fix

    var fixed_ = VolumePlanner.FixFirstLegVolume(plan, 3_000, 500, Symbol);

    Assert.Equal(new long[] { 500, 700, 600, 600, 600 }, fixed_.Slices);
    Assert.Equal(3_000, fixed_.Slices.Sum());
    Assert.Equal(plan.TargetsPips, fixed_.TargetsPips);
    Assert.Equal(plan.TargetOrdinals, fixed_.TargetOrdinals);
  }

  [Fact]
  public void FixFirstLegVolumeLeavesSingleLegPlanUnchanged()
  {
    var plan = VolumePlanner.BuildTargetPlan(1_000, Symbol, [70], [100]);

    var fixed_ = VolumePlanner.FixFirstLegVolume(plan, 1_000, 500, Symbol);

    Assert.Same(plan.Slices, fixed_.Slices);
  }

  [Fact]
  public void FixFirstLegVolumeFailsOpenWhenFixedAmountDoesNotFit()
  {
    var plan = Plan(3_000);

    var fixed_ = VolumePlanner.FixFirstLegVolume(plan, 3_000, 5_000, Symbol);

    Assert.Equal(plan.Slices, fixed_.Slices);
  }

  [Fact]
  public void FixFirstLegVolumeFailsOpenWhenRemainderCannotCoverOtherLegs()
  {
    var plan = Plan(600);

    var fixed_ = VolumePlanner.FixFirstLegVolume(plan, 600, 500, Symbol);

    Assert.Equal(plan.Slices, fixed_.Slices);
  }

  [Fact]
  public void PlanPartialCloseVolumeUsesFilledRemainderAndSnapsToStep()
  {
    // Live: L2 cancelled unfilled; only L1 remaining=800, step=800.
    // 20% TP desired=160 cannot be sent — a single-step position closes
    // fully at this target instead of TRADING_BAD_VOLUME.
    var step800 = Symbol with { MinVolume = 800, StepVolume = 800 };
    Assert.Equal(
      800,
      VolumePlanner.PlanPartialCloseVolume(800, 160, step800, isFinalTarget: false)
    );
    Assert.Equal(
      800,
      VolumePlanner.PlanPartialCloseVolume(800, 160, step800, isFinalTarget: true)
    );
    // Larger filled remainder that can leave a valid leftover: skip partial
    // rather than send a sub-step close.
    Assert.Equal(
      0,
      VolumePlanner.PlanPartialCloseVolume(1600, 160, step800, isFinalTarget: false)
    );
  }

  [Fact]
  public void PlanPartialCloseVolumeBooksValidPartialAgainstFilledL1()
  {
    // Filled L1=1600 after L2 cancel; step=100; 20% → 320 snapped to 300,
    // leftover 1300 stays min/step valid.
    var close = VolumePlanner.PlanPartialCloseVolume(
      1600, 320, Symbol, isFinalTarget: false
    );
    Assert.Equal(300, close);
    Assert.Equal(0, close % Symbol.StepVolume);
    Assert.True(1600 - close >= Symbol.MinVolume);
  }

  [Fact]
  public void PlanPartialCloseVolumeClosesAllWhenLeftoverWouldBeDust()
  {
    // remaining=200, desired=150 → snap 100, leftover 100 == MinVolume ok
    Assert.Equal(
      100,
      VolumePlanner.PlanPartialCloseVolume(200, 150, Symbol, isFinalTarget: false)
    );
    // remaining=150, desired=100 → snap 100, leftover 50 < MinVolume → all
    Assert.Equal(
      150,
      VolumePlanner.PlanPartialCloseVolume(150, 100, Symbol, isFinalTarget: false)
    );
  }

  [Fact]
  public void AllocateProRataSteppedKeepsBrokerStepOnEachLeg()
  {
    var slices = VolumePlanner.AllocateProRataStepped(
      [800, 400],
      300,
      Symbol
    );
    Assert.Equal(300, slices.Sum());
    Assert.All(slices, slice => Assert.Equal(0, slice % Symbol.StepVolume));
    Assert.True(slices[0] <= 800);
    Assert.True(slices[1] <= 400);
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

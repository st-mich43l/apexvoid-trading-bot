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
  public void ConvertsLotsToBrokerVolume()
  {
    Assert.Equal(200, VolumePlanner.VolumeForLots(0.02m, Symbol));
    Assert.Equal(900, VolumePlanner.VolumeForLots(0.09m, Symbol));
  }

  [Fact]
  public void BrokerPipSizeIsDiagnosticOnly()
  {
    Assert.Equal(0.01m, VolumePlanner.BrokerPipSize(Symbol));
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

}

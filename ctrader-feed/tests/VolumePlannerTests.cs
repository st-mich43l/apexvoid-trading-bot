using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class VolumePlannerTests
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

  [Theory]
  [InlineData(999, 0)]
  [InlineData(1000, 0.12)]
  [InlineData(1999, 0.12)]
  [InlineData(2000, 0.20)]
  [InlineData(4999, 0.20)]
  [InlineData(5000, 0.30)]
  [InlineData(25000, 0.30)]
  public void SelectsConfiguredBalanceTier(double balance, double expectedLots)
  {
    Assert.Equal(
      Convert.ToDecimal(expectedLots),
      VolumePlanner.LotsForBalance(Convert.ToDecimal(balance))
    );
  }

  [Theory]
  [InlineData(0.12, 1200)]
  [InlineData(0.20, 2000)]
  [InlineData(0.30, 3000)]
  public void ConvertsLotsToBrokerVolume(double lots, long expected)
  {
    Assert.Equal(
      expected,
      VolumePlanner.VolumeForLots(Convert.ToDecimal(lots), Symbol)
    );
  }

  [Fact]
  public void SplitsPointTwelveAcrossFiveValidPartialCloses()
  {
    Assert.Equal([200, 200, 200, 200, 400], VolumePlanner.SplitFive(1200, Symbol));
  }

  [Theory]
  [InlineData(2000, 400)]
  [InlineData(3000, 600)]
  public void SplitsEvenTiersEqually(long volume, long slice)
  {
    Assert.Equal(
      Enumerable.Repeat(slice, 5),
      VolumePlanner.SplitFive(volume, Symbol)
    );
  }
}

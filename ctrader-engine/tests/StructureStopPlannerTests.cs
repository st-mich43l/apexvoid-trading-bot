using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class StructureStopPlannerTests
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
  [InlineData(TradeDirection.Buy, 3998.5, 3998.2)]
  [InlineData(TradeDirection.Sell, 4001.5, 4001.8)]
  public void UsesSwingInvalidationAndAtrBuffer(
    TradeDirection direction,
    double swing,
    double expectedStop
  )
  {
    var plan = StructureStopPlanner.Plan(
      direction,
      4000m,
      Convert.ToDecimal(swing),
      atr: 1m,
      bufferAtr: 0.3m,
      minimumStopPips: 15,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol
    );

    Assert.Equal(Convert.ToDecimal(expectedStop), plan.StopLoss);
    Assert.Equal(18m, plan.StopPips);
    Assert.False(plan.Clamped);
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 3999.5, 3998.5, 15)]
  [InlineData(TradeDirection.Sell, 4000.5, 4001.5, 15)]
  [InlineData(TradeDirection.Buy, 3990.0, 3993.5, 65)]
  [InlineData(TradeDirection.Sell, 4010.0, 4006.5, 65)]
  public void ClampsToConfiguredStopBand(
    TradeDirection direction,
    double swing,
    double expectedStop,
    int expectedPips
  )
  {
    var plan = StructureStopPlanner.Plan(
      direction,
      4000m,
      Convert.ToDecimal(swing),
      atr: 1m,
      bufferAtr: 0.3m,
      minimumStopPips: 15,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol
    );

    Assert.Equal(Convert.ToDecimal(expectedStop), plan.StopLoss);
    Assert.Equal(expectedPips, plan.StopPips);
    Assert.True(plan.Clamped);
  }
}

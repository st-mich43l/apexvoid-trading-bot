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
      sweepExtreme: null,
      wickBufferAtr: 0.15m,
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
      sweepExtreme: null,
      wickBufferAtr: 0.15m,
      minimumStopPips: 15,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol
    );

    Assert.Equal(Convert.ToDecimal(expectedStop), plan.StopLoss);
    Assert.Equal(expectedPips, plan.StopPips);
    Assert.True(plan.Clamped);
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 3996.0, 3995.85)]
  [InlineData(TradeDirection.Sell, 4004.0, 4004.15)]
  public void UsesWiderSweepWickFloor(
    TradeDirection direction,
    double sweep,
    double expectedStop
  )
  {
    var plan = StructureStopPlanner.Plan(
      direction,
      4000m,
      direction == TradeDirection.Buy ? 3998.5m : 4001.5m,
      atr: 1m,
      bufferAtr: 0.3m,
      sweepExtreme: Convert.ToDecimal(sweep),
      wickBufferAtr: 0.15m,
      minimumStopPips: 30,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol
    );

    Assert.Equal(Convert.ToDecimal(expectedStop), plan.StopLoss);
  }

  [Fact]
  public void RaisesTwelveRawPipsToThirtyPipFloor()
  {
    var plan = StructureStopPlanner.Plan(
      TradeDirection.Buy,
      entryPrice: 4000m,
      structureSwing: 3999.1m,
      atr: 1m,
      bufferAtr: 0.3m,
      sweepExtreme: null,
      wickBufferAtr: 0.15m,
      minimumStopPips: 30,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol
    );

    Assert.Equal(30m, plan.StopPips);
    Assert.Equal(3997m, plan.StopLoss);
  }

  [Fact]
  public void IncidentSweepClearsWickByConfiguredAtrBuffer()
  {
    var plan = StructureStopPlanner.Plan(
      TradeDirection.Buy,
      entryPrice: 4118.8m,
      structureSwing: 4118.0m,
      atr: 2m,
      bufferAtr: 0.3m,
      sweepExtreme: 4117.5m,
      wickBufferAtr: 0.15m,
      minimumStopPips: 30,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol
    );

    Assert.True(plan.StopLoss <= 4117.2m);
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 3993.6)]
  [InlineData(TradeDirection.Sell, 4006.4)]
  public void RejectsWhenSweepWickFloorExceedsEnvelope(
    TradeDirection direction,
    double sweep
  )
  {
    var error = Assert.Throws<VolumePlanningException>(() =>
      StructureStopPlanner.Plan(
        direction,
        4000m,
        direction == TradeDirection.Buy ? 3998.5m : 4001.5m,
        atr: 1m,
        bufferAtr: 0.3m,
        sweepExtreme: Convert.ToDecimal(sweep),
        wickBufferAtr: 0.15m,
        minimumStopPips: 30,
        maximumStopPips: 65,
        pipSize: 0.1m,
        Symbol
      )
    );

    Assert.Equal("stop_exceeds_envelope_after_wick", error.Message);
  }
}

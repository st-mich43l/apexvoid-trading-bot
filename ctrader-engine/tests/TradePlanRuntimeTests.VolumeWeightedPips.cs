using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

// The archived target pips (highest TP reached) is not the trade's result when the
// remaining volume later stops out below it. VolumeWeightedPips is the realized
// figure: pips booked at target closes plus open volume at its exit, over the
// whole filled volume.
public sealed partial class TradePlanRuntimeTests
{
  private static TradePlanLegRuntimeState PipsLeg(
    string id, decimal fill, long filled, long remaining
  ) => new(
    id, fill, 0.5m, filled, 0m, "c-" + id,
    FillPrice: fill, FilledVolume: filled, RemainingVolume: remaining
  );

  private static TradePlan PipsPlan(string direction)
  {
    var json = PlanJson(direction: direction);
    return TradePlanJson.DeserializePlan(json);
  }

  [Fact]
  public void VolumeWeightedPipsHalfBookedThenRunnerStopsAtBreakEvenIsHalfTheTarget()
  {
    // 2 legs of 100 @ 4090.0, pip 0.1. TP1 closed 100 at 4096.0 => +60 pips x 100.
    var legs = new[]
    {
      PipsLeg("L1", 4090.0m, 100, 0),
      PipsLeg("L2", 4090.0m, 100, 100),
    };
    var result = TradePlanRuntime.VolumeWeightedPips(
      PipsPlan("BUY"), legs, bookedPipVolume: 60m * 100, pipSize: 0.1m, _ => 4090.0m
    );
    // (6000 + 0) / 200 = 30 pips - not the 60 archived for TP1.
    Assert.Equal(30m, result);
  }

  [Fact]
  public void VolumeWeightedPipsStoppedRunnerBelowFillSubtracts()
  {
    var legs = new[]
    {
      PipsLeg("L1", 4090.0m, 100, 0),
      PipsLeg("L2", 4090.0m, 100, 100),
    };
    // Runner stops 10 pips under its fill: (6000 - 1000) / 200 = 25.
    Assert.Equal(25m, TradePlanRuntime.VolumeWeightedPips(
      PipsPlan("BUY"), legs, 6000m, 0.1m, _ => 4089.0m
    ));
  }

  [Fact]
  public void VolumeWeightedPipsSellIsSignedFromTheSellSide()
  {
    var legs = new[] { PipsLeg("L1", 4090.0m, 100, 100) };
    // SELL filled 4090.0 closed 4091.0 => -10 pips.
    Assert.Equal(-10m, TradePlanRuntime.VolumeWeightedPips(
      PipsPlan("SELL"), legs, 0m, 0.1m, _ => 4091.0m
    ));
  }

  [Fact]
  public void VolumeWeightedPipsIsNullWhenAnOpenLegHasNoExitPrice()
  {
    var legs = new[] { PipsLeg("L1", 4090.0m, 100, 100) };
    Assert.Null(TradePlanRuntime.VolumeWeightedPips(
      PipsPlan("BUY"), legs, 0m, 0.1m, _ => null
    ));
  }

  [Fact]
  public void VolumeWeightedPipsIsNullWithoutFilledVolume()
  {
    Assert.Null(TradePlanRuntime.VolumeWeightedPips(
      PipsPlan("BUY"), [PipsLeg("L1", 4090.0m, 0, 0)], 0m, 0.1m, _ => 4090.0m
    ));
  }
}

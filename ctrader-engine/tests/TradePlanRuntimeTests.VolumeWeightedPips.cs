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

  private static TradePlanRuntimeState RiskState(params TradePlanLegRuntimeState[] legs) => new(
    "p", "t", "s", "XAU", "SELL", "limit_ladder", TradePlanRuntimeStage.FullyOpen,
    CurrentStop: 4142.0m, Legs: legs
  );

  // Manual Algo's rule, now the executor's too: R divides by the risk from the deepest
  // NON-RISK fill to the ORIGINAL stop, whatever the stop has since been trailed to. The
  // RISK leg never enters it (it still counts in the pips of an archived target).
  [Theory]
  [InlineData(false, false, 50.0)]   // only L1 4142.0 filled -> 4147.0 - 4142.0 = 50 pips
  [InlineData(true, false, 35.0)]    // L2 4143.5 is deeper -> 35 pips
  [InlineData(true, true, 35.0)]     // RISK 4145.5 is deepest overall but ignored -> still 35
  public void GroupRiskPipsIsMeasuredFromTheDeepestFillToTheOriginalStop(
    bool l2Filled, bool riskFilled, double expected
  )
  {
    var plan = TradePlanJson.DeserializePlan(PlanJson(direction: "SELL", stopPrice: 4147.0m));
    var legs = new List<TradePlanLegRuntimeState> { PipsLeg("L1", 4142.0m, 80, 80) };
    if (l2Filled) legs.Add(PipsLeg("L2", 4143.5m, 20, 20));
    if (riskFilled) legs.Add(PipsLeg("RISK", 4145.5m, 5, 5));
    Assert.Equal(
      (decimal)expected, TradePlanRuntime.GroupRiskPips(plan, RiskState([.. legs]), 0.1m, targetArchived: true)
    );
  }

  [Fact]
  public void GroupRiskPipsIsNullWithoutAFillOrAStop()
  {
    var plan = TradePlanJson.DeserializePlan(PlanJson(direction: "SELL", stopPrice: 4147.0m));
    Assert.Null(TradePlanRuntime.GroupRiskPips(plan, RiskState(), 0.1m, true));
    Assert.Null(TradePlanRuntime.GroupRiskPips(plan, null, 0.1m, true));
  }

  // A full stop with no target archived is measured from the weighted non-RISK fill
  // (SignedExitPips), so the RISK leg must not shrink the denominator: -47 pips over
  // 47 pips of risk is -1R, not -3.1R.
  [Theory]
  [InlineData(false, 50.0)]  // L1 only: 4142.0 -> 4147.0
  [InlineData(true, 47.0)]   // L1 80% + L2 20% weighted 4142.3 -> 4147.0, RISK 4145.5 ignored
  public void GroupRiskPipsOfAFullStopIgnoresTheRiskLeg(bool l2Filled, double expected)
  {
    var plan = TradePlanJson.DeserializePlan(PlanJson(direction: "SELL", stopPrice: 4147.0m));
    var legs = new List<TradePlanLegRuntimeState> { PipsLeg("L1", 4142.0m, 80, 80) };
    if (l2Filled) legs.Add(PipsLeg("L2", 4143.5m, 20, 20));
    legs.Add(PipsLeg("RISK", 4145.5m, 5, 5));
    Assert.Equal(
      (decimal)expected,
      TradePlanRuntime.GroupRiskPips(plan, RiskState([.. legs]), 0.1m, targetArchived: false)
    );
  }
}

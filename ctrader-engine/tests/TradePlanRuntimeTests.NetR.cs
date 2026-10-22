using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

// A group's net R is its volume-weighted realized pips over its volume-weighted initial risk, both over the same
// filled legs. These tests pin the accounting for the real ladders (XAU 40/20/20/20 at 1R-4R, FX 50/50 at 1R-2R),
// a stop at break-even, a full stop with the reaction risk leg, a partial stop, legs that entered at different
// prices, and the cases with no measurable risk.
public sealed partial class TradePlanRuntimeTests
{
  private static TradePlan NetRPlan(string direction, decimal stop, string symbol = "XAU") =>
    TradePlanJson.DeserializePlan(PlanJson(direction: direction, stopPrice: stop, symbol: symbol));

  private static TradePlanLegRuntimeState NetRLeg(string id, decimal fill, long filled, long remaining) => PipsLeg(id, fill, filled, remaining);

  private static decimal? NetR(decimal? pips, decimal? risk) => pips is decimal p && risk is decimal r && r > 0m ? decimal.Round(p / r, 4) : null;

  [Fact]
  public void AnXauLadderThatRunsToFourRIsTwoPointTwoR()
  {
    // 100 units at 4100.0, stop 4090.0 (100 pips). 40/20/20/20 closed at 1R, 2R, 3R, 4R.
    var plan = NetRPlan("BUY", 4090.0m);
    var legs = new[] { NetRLeg("L1", 4100.0m, 40, 0), NetRLeg("L2", 4100.0m, 20, 0), NetRLeg("L3", 4100.0m, 20, 0), NetRLeg("L4", 4100.0m, 20, 0) };
    var booked = 100m * 40 + 200m * 20 + 300m * 20 + 400m * 20;
    var pips = TradePlanRuntime.VolumeWeightedPips(plan, legs, booked, 0.1m, _ => null);
    var risk = TradePlanRuntime.VolumeWeightedRiskPips(plan, legs, 0.1m);
    Assert.Equal(220m, pips);
    Assert.Equal(100m, risk);
    Assert.Equal(2.2m, NetR(pips, risk));
  }

  [Fact]
  public void AnFxLadderThatReachesTwoRIsOnePointFiveR()
  {
    // EURUSD, pip 0.0001: 1.1000 with the stop at 1.0980 (20 pips); 50% closed at +20, 50% at +40.
    var plan = NetRPlan("BUY", 1.0980m, "EURUSD");
    var legs = new[] { NetRLeg("L1", 1.1000m, 50, 0), NetRLeg("L2", 1.1000m, 50, 0) };
    var booked = 20m * 50 + 40m * 50;
    var pips = TradePlanRuntime.VolumeWeightedPips(plan, legs, booked, 0.0001m, _ => null);
    var risk = TradePlanRuntime.VolumeWeightedRiskPips(plan, legs, 0.0001m);
    Assert.Equal(1.5m, NetR(pips, risk));
  }

  [Theory]
  [InlineData("XAU", 40, 0.4)]
  [InlineData("EURUSD", 50, 0.5)]
  public void TheFirstTargetThenABreakEvenStopBooksOnlyTheClosedShare(string symbol, int firstShare, double expected)
  {
    var pip = symbol == "XAU" ? 0.1m : 0.0001m;
    var fill = symbol == "XAU" ? 4100.0m : 1.1000m;
    var stop = symbol == "XAU" ? 4090.0m : 1.0980m;
    var oneR = Math.Abs(fill - stop) / pip;
    var plan = NetRPlan("BUY", stop, symbol);
    var legs = new[] { NetRLeg("L1", fill, firstShare, 0), NetRLeg("L2", fill, 100 - firstShare, 100 - firstShare) };
    var held = new[] { legs[0], legs[1] };
    var pips = TradePlanRuntime.VolumeWeightedPips(plan, held, oneR * firstShare, pip, _ => fill);
    var risk = TradePlanRuntime.VolumeWeightedRiskPips(plan, held, pip);
    Assert.Equal((decimal)expected, NetR(pips, risk));
  }

  [Fact]
  public void AFullStopIsExactlyMinusOneREvenWithTheSmallReactionRiskLeg()
  {
    // Market leg 80 at 4100.0 (100 pips of risk) and a risk leg 5 at 4091.0 (10 pips of risk), both stopped at 4090.0.
    var plan = NetRPlan("BUY", 4090.0m);
    var legs = new[] { NetRLeg("L1", 4100.0m, 80, 80), NetRLeg("L2:risk", 4091.0m, 5, 5) };
    var pips = TradePlanRuntime.VolumeWeightedPips(plan, legs, 0m, 0.1m, _ => 4090.0m);
    var risk = TradePlanRuntime.VolumeWeightedRiskPips(plan, legs, 0.1m);
    Assert.Equal(-1m, NetR(pips, risk));
    // The card's reference risk is the market leg's 100 pips alone (the risk leg is excluded by design): the same stop-out reads
    // as -0.94R against it, which is why the net result is normalised by the weighted risk instead.
    Assert.True(pips / 100m > -1m && pips / 100m < -0.9m);
  }

  [Fact]
  public void APartialStopAfterTheFirstTargetIsAFractionOfOneR()
  {
    // 40 closed at +100 pips (1R); the remaining 60 stop 50 pips under the fill: (4000 - 3000) / 100 = +10 pips = 0.1R.
    var plan = NetRPlan("BUY", 4090.0m);
    var legs = new[] { NetRLeg("L1", 4100.0m, 40, 0), NetRLeg("L2", 4100.0m, 60, 60) };
    var pips = TradePlanRuntime.VolumeWeightedPips(plan, legs, 100m * 40, 0.1m, _ => 4095.0m);
    Assert.Equal(0.1m, NetR(pips, TradePlanRuntime.VolumeWeightedRiskPips(plan, legs, 0.1m)));
  }

  [Fact]
  public void LegsThatEnteredAtDifferentPricesAreNormalisedByTheirOwnRisk()
  {
    // 60 at 4100.0 (100 pips) and 40 at 4095.0 (50 pips) behind one stop at 4090.0: weighted risk 80 pips; a full stop loses 80.
    var plan = NetRPlan("BUY", 4090.0m);
    var legs = new[] { NetRLeg("L1", 4100.0m, 60, 60), NetRLeg("L2", 4095.0m, 40, 40) };
    var risk = TradePlanRuntime.VolumeWeightedRiskPips(plan, legs, 0.1m);
    Assert.Equal(80m, risk);
    Assert.Equal(-1m, NetR(TradePlanRuntime.VolumeWeightedPips(plan, legs, 0m, 0.1m, _ => 4090.0m), risk));
  }

  [Fact]
  public void ASellIsMeasuredFromTheSellSide()
  {
    var plan = NetRPlan("SELL", 4110.0m);
    var legs = new[] { NetRLeg("L1", 4100.0m, 100, 100) };
    var risk = TradePlanRuntime.VolumeWeightedRiskPips(plan, legs, 0.1m);
    Assert.Equal(100m, risk);
    Assert.Equal(-1m, NetR(TradePlanRuntime.VolumeWeightedPips(plan, legs, 0m, 0.1m, _ => 4110.0m), risk));
    Assert.Equal(2m, NetR(TradePlanRuntime.VolumeWeightedPips(plan, legs, 0m, 0.1m, _ => 4080.0m), risk));
  }

  [Fact]
  public void NoRiskIsMeasuredWithoutAFilledLegAFillPriceOrAStopBeyondTheFill()
  {
    var plan = NetRPlan("BUY", 4090.0m);
    Assert.Null(TradePlanRuntime.VolumeWeightedRiskPips(plan, new[] { NetRLeg("L1", 4100.0m, 0, 0) }, 0.1m));
    Assert.Null(TradePlanRuntime.VolumeWeightedRiskPips(plan, new[] { NetRLeg("L1", 4085.0m, 100, 100) }, 0.1m)); // stop above the fill
    var noFillPrice = new TradePlanLegRuntimeState("L1", 4100.0m, 0.5m, 100, 0m, "c", FilledVolume: 100, RemainingVolume: 100);
    Assert.Null(TradePlanRuntime.VolumeWeightedRiskPips(plan, new[] { noFillPrice }, 0.1m));
    Assert.Null(TradePlanRuntime.VolumeWeightedRiskPips(plan, new[] { NetRLeg("L1", 4100.0m, 100, 100) }, 0m));
  }

  [Fact]
  public void TheRiskIsTheSameWhateverTheLegsDidAfterTheyFilled()
  {
    // The risk is initial: a leg that has since closed or moved its stop to break-even still carries the risk it entered with.
    var plan = NetRPlan("BUY", 4090.0m);
    var open = new[] { NetRLeg("L1", 4100.0m, 100, 100) };
    var closed = new[] { NetRLeg("L1", 4100.0m, 100, 0) };
    Assert.Equal(TradePlanRuntime.VolumeWeightedRiskPips(plan, open, 0.1m), TradePlanRuntime.VolumeWeightedRiskPips(plan, closed, 0.1m));
  }
}

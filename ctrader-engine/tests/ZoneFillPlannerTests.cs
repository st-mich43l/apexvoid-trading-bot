using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class ZoneFillPlannerTests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU", "XAUUSD", 7, 2, 1, 100, 100, 100_000, 10_000
  );

  [Fact]
  public void WideZoneSplitsAcrossProximalEdgeAndMidpoint()
  {
    var plan = ZoneFillPlanner.Build(
      TradeDirection.Buy,
      new TradeCandidateZone(3998m, 4000m),
      3995m,
      600,
      Symbol,
      [30, 60, 90, 120, 200],
      [20, 20, 20, 20, 20]
    );

    Assert.Equal(3995m, plan.StopLoss);
    Assert.Equal(new[] { 4000m, 3999m }, plan.Legs.Select(leg => leg.LimitPrice));
    Assert.Equal(new long[] { 300, 300 }, plan.Legs.Select(leg => leg.Volume));
    Assert.All(plan.Legs, leg => Assert.Equal(3, leg.TargetPlan.Slices.Count));
    Assert.Equal(600, plan.Legs.Sum(leg => leg.Volume));
  }

  [Fact]
  public void SellUsesLowerZoneEdgeAsProximal()
  {
    var plan = ZoneFillPlanner.Build(
      TradeDirection.Sell,
      new TradeCandidateZone(3998m, 4000m),
      4003m,
      500,
      Symbol,
      [30, 60, 90, 120, 200],
      [20, 20, 20, 20, 20]
    );

    Assert.Equal(new[] { 3998m, 3999m }, plan.Legs.Select(leg => leg.LimitPrice));
    Assert.Equal(new long[] { 300, 200 }, plan.Legs.Select(leg => leg.Volume));
  }

  [Fact]
  public void IncidentZoneLegVolumesSumExactlyToTotalVolume()
  {
    // 23 Jul 2026 incident numbers: BUY demand zone 4,112-4,122, 0.13 lots.
    var plan = ZoneFillPlanner.Build(
      TradeDirection.Buy,
      new TradeCandidateZone(4112m, 4122m),
      4108m,
      1300,
      Symbol,
      [30, 60, 90, 120, 200],
      [20, 20, 20, 20, 20]
    );

    Assert.Equal(1300, plan.Legs.Sum(leg => leg.Volume));
    Assert.Equal(2, plan.Legs.Count);
    // Proximal edge for a BUY is zone.High (4,122) - the planner never
    // prices at the true distal edge (4,112) at all, only proximal +
    // midpoint (4,117). See Fix 2's plan notes: this is a deliberate
    // "no further code change" scope boundary, not an oversight here.
    Assert.Equal(new[] { 4122m, 4117m }, plan.Legs.Select(leg => leg.LimitPrice));
  }

  [Fact]
  public void ZoneMustBeWideEnoughAndBothLegsNeedAFeasibleLadder()
  {
    Assert.True(ZoneFillPlanner.Qualifies(
      new TradeCandidateZone(3999m, 4000m),
      atr: 2m,
      minimumWidthAtr: 0.5m
    ));
    Assert.False(ZoneFillPlanner.Qualifies(
      new TradeCandidateZone(3999.1m, 4000m),
      atr: 2m,
      minimumWidthAtr: 0.5m
    ));
    Assert.Throws<VolumePlanningException>(() => ZoneFillPlanner.Build(
      TradeDirection.Buy,
      new TradeCandidateZone(3998m, 4000m),
      3995m,
      300,
      Symbol,
      [30, 60, 90, 120, 200],
      [20, 20, 20, 20, 20]
    ));
  }
}

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

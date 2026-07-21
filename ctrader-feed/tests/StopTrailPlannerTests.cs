using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class StopTrailPlannerTests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU",
    "XAUUSD",
    7,
    Digits: 2,
    PipPosition: 1
  );

  [Theory]
  [InlineData(TradeDirection.Buy, 4000.5, 4003.2, 4006.2)]
  [InlineData(TradeDirection.Sell, 3999.9, 3997.2, 3994.2)]
  public void HoldsAfterTp2ThenTrailsTwoTargetsBehind(
    TradeDirection direction,
    double afterTp1,
    double afterTp3,
    double afterTp4
  )
  {
    var state = State(direction);
    var tp1 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 0, Symbol, 3)
    );
    Assert.Equal(Convert.ToDecimal(afterTp1), tp1.StopLoss);
    Assert.Equal("BE+3", tp1.Label);
    state = state with { CurrentStopLoss = tp1.StopLoss };

    Assert.Null(StopTrailPlanner.Plan(state, 1, Symbol, 3));

    var tp3 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 2, Symbol, 3)
    );
    Assert.Equal(Convert.ToDecimal(afterTp3), tp3.StopLoss);
    Assert.Equal("TP1", tp3.Label);
    state = state with { CurrentStopLoss = tp3.StopLoss };

    var tp4 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 3, Symbol, 3)
    );
    Assert.Equal(Convert.ToDecimal(afterTp4), tp4.StopLoss);
    Assert.Equal("TP2", tp4.Label);
    Assert.Null(StopTrailPlanner.Plan(state, 4, Symbol, 3));
  }

  [Fact]
  public void UsesOriginalOrdinalsForAdaptiveTargetPlans()
  {
    var state = State(TradeDirection.Buy) with
    {
      Slices = [200, 200, 200, 200],
      TargetsPips = [30, 90, 120, 200],
      TargetOrdinals = [1, 3, 4, 5],
    };

    var move = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 1, Symbol, 3)
    );

    Assert.Equal(4003.2m, move.StopLoss);
    Assert.Equal("TP1", move.Label);
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 4004.0)]
  [InlineData(TradeDirection.Sell, 3996.0)]
  public void IgnoresStopThatWouldMoveBackward(
    TradeDirection direction,
    double currentStop
  )
  {
    var state = State(direction) with
    {
      CurrentStopLoss = Convert.ToDecimal(currentStop),
    };

    Assert.Null(StopTrailPlanner.Plan(state, 0, Symbol, 3));
  }

  private static AutoTradePositionState State(TradeDirection direction) => new(
    "candidate",
    91,
    7,
    direction,
    4000.2m,
    1_000,
    1_000,
    [200, 200, 200, 200, 200],
    [30, 60, 90, 120, 200],
    0,
    1_000,
    direction == TradeDirection.Buy ? 3993.7m : 4006.7m
  );
}

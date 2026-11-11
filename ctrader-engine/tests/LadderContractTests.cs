using Xunit;

namespace ApexVoid.CTraderFeed.Tests;

// ZL-1: one definition of a valid price ladder, and the executor's last check before a limit leg reaches the broker.
public class LadderContractTests
{
  [Fact]
  public void DistinctWellSpacedBuyLadderIsValid()
  {
    Assert.Null(TradePlanLadder.GeometryProblem(true, [4303.00m, 4301.50m, 4300.00m], 1.0m));
  }

  [Fact]
  public void SellLadderStepsUp()
  {
    Assert.Null(TradePlanLadder.GeometryProblem(false, [4300.00m, 4301.50m, 4303.00m], 1.0m));
    Assert.Equal(
      TradePlanLadder.LegPricesNotOrdered,
      TradePlanLadder.GeometryProblem(false, [4303.00m, 4301.50m, 4300.00m], 1.0m)
    );
  }

  [Fact]
  public void TheInvalidLadderFromTheBriefNeverPasses()
  {
    // 4303.00 / 4303.00 / 4302.90
    Assert.Equal(
      TradePlanLadder.DuplicateLegPrice,
      TradePlanLadder.GeometryProblem(true, [4303.00m, 4303.00m, 4302.90m], 1.0m)
    );
    Assert.Equal(
      TradePlanLadder.LegSpacingBelowMinimum,
      TradePlanLadder.GeometryProblem(true, [4303.00m, 4302.90m, 4300.00m], 1.0m)
    );
  }

  [Fact]
  public void BuyLadderThatStepsUpIsRejected()
  {
    Assert.Equal(
      TradePlanLadder.LegPricesNotOrdered,
      TradePlanLadder.GeometryProblem(true, [4300.00m, 4301.50m], null)
    );
  }

  [Fact]
  public void SpacingIsJudgedOnThePricesAsGiven()
  {
    // 0.99 apart is below a 1.00 minimum even though both round to a "nearby" price.
    Assert.Equal(
      TradePlanLadder.LegSpacingBelowMinimum,
      TradePlanLadder.GeometryProblem(true, [4303.00m, 4302.01m], 1.0m)
    );
    Assert.Null(TradePlanLadder.GeometryProblem(true, [4303.00m, 4302.00m], 1.0m));
  }

  private const decimal Tick = 0.01m;

  [Fact]
  public void DeeperBuyLimitMeaningfullyBelowTheAskRests()
  {
    Assert.Null(TradePlanExecutionEngine.GuardRestingLeg(true, 4301.50m, 4302.80m, 4303.00m, Tick, 1.0m, [4303.00m]));
  }

  [Fact]
  public void BuyLimitAtOrAboveTheAskIsMarketableAndSkipped()
  {
    Assert.Equal(
      TradePlanExecutionEngine.LegSkippedMarketable,
      TradePlanExecutionEngine.GuardRestingLeg(true, 4301.50m, 4300.80m, 4301.00m, Tick, 1.0m, [])
    );
    Assert.Equal(
      TradePlanExecutionEngine.LegSkippedMarketable,
      TradePlanExecutionEngine.GuardRestingLeg(true, 4301.00m, 4300.80m, 4301.00m, Tick, 1.0m, [])
    );
  }

  [Fact]
  public void DeeperSellLimitMeaningfullyAboveTheBidRests()
  {
    Assert.Null(TradePlanExecutionEngine.GuardRestingLeg(false, 4301.50m, 4300.00m, 4300.20m, Tick, 1.0m, [4300.00m]));
    Assert.Equal(
      TradePlanExecutionEngine.LegSkippedMarketable,
      TradePlanExecutionEngine.GuardRestingLeg(false, 4301.50m, 4302.00m, 4302.20m, Tick, 1.0m, [])
    );
  }

  [Fact]
  public void AWidenedSpreadMovesTheRestingLineWithIt()
  {
    // 1.20 spread: the buy limit must sit a full spread below the ask to count as resting.
    Assert.Equal(
      TradePlanExecutionEngine.LegSkippedMarketable,
      TradePlanExecutionEngine.GuardRestingLeg(true, 4301.50m, 4301.30m, 4302.50m, Tick, 1.0m, [])
    );
    Assert.Null(TradePlanExecutionEngine.GuardRestingLeg(true, 4301.20m, 4301.30m, 4302.50m, Tick, 1.0m, []));
  }

  [Fact]
  public void AGapThroughSeveralLadderPricesSkipsEveryOne()
  {
    // Price gapped to 4299.00/4299.20: L2 4301.50 and L3 4300.00 are both far above the ask.
    foreach (var price in new[] { 4301.50m, 4300.00m })
    {
      Assert.Equal(
        TradePlanExecutionEngine.LegSkippedMarketable,
        TradePlanExecutionEngine.GuardRestingLeg(true, price, 4299.00m, 4299.20m, Tick, 1.0m, [])
      );
    }
  }

  [Fact]
  public void AFilledSiblingAtOrNearTheLegPriceIsACollision()
  {
    // L1 filled with slippage at 4302.20: L2 at 4301.50 is 0.70 from it, under the 1.00 spacing.
    Assert.Equal(
      TradePlanExecutionEngine.LegSkippedCollision,
      TradePlanExecutionEngine.GuardRestingLeg(true, 4301.50m, 4303.00m, 4303.20m, Tick, 1.0m, [4302.20m])
    );
    // Exactly the same price, with no spacing declared (an older plan): still a collision.
    Assert.Equal(
      TradePlanExecutionEngine.LegSkippedCollision,
      TradePlanExecutionEngine.GuardRestingLeg(true, 4301.50m, 4303.00m, 4303.20m, Tick, null, [4301.50m])
    );
    // A pending sibling a full spacing away is fine.
    Assert.Null(TradePlanExecutionEngine.GuardRestingLeg(true, 4300.00m, 4303.00m, 4303.20m, Tick, 1.0m, [4301.50m]));
  }
}

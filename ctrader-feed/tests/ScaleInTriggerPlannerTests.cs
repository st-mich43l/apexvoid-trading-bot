using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class ScaleInTriggerPlannerTests
{
  [Fact]
  public void AcceptsCompleteFreshMomentumContinuation()
  {
    Assert.Null(ScaleInTriggerPlanner.Validate(Valid()));
  }

  [Theory]
  [InlineData(-1, 4002, "averaging-down guard")]
  [InlineData(10, 3999, "averaging-down guard")]
  public void AveragingDownGuardCannotBeBypassed(
    decimal groupPnl,
    decimal addEntry,
    string reason
  )
  {
    var failure = ScaleInTriggerPlanner.Validate(Valid() with
    {
      GroupPnl = groupPnl,
      AddEntry = addEntry,
    });

    Assert.Contains(reason, failure);
  }

  [Fact]
  public void RequiresFreshDisplacement()
  {
    var missing = ScaleInTriggerPlanner.Validate(Valid() with
    {
      DisplacementDirection = null,
    });
    var stale = ScaleInTriggerPlanner.Validate(Valid() with
    {
      DisplacementAgeBars = 4,
    });

    Assert.Contains("fresh up displacement", missing);
    Assert.Contains("fresh up displacement", stale);
  }

  [Fact]
  public void RequiresBosSinceInitialEntry()
  {
    var failure = ScaleInTriggerPlanner.Validate(Valid() with
    {
      BosTimestamp = 999,
    });

    Assert.Contains("BOS since initial entry", failure);
  }

  [Fact]
  public void RejectsOpposingLevelInsideAtrBuffer()
  {
    var failure = ScaleInTriggerPlanner.Validate(Valid() with
    {
      OpposingLevelDistanceAtr = 0.8m,
    });

    Assert.Contains("opposing level", failure);
  }

  [Fact]
  public void EnforcesCooldownAndLifetimeTrancheCountIndependently()
  {
    var cooldown = ScaleInTriggerPlanner.Validate(Valid() with
    {
      BarTimestamp = 1_179,
    });
    var maximum = ScaleInTriggerPlanner.Validate(Valid() with
    {
      LifetimeTrancheCount = 2,
    });

    Assert.Contains("cooldown", cooldown);
    Assert.Contains("maximum tranche count", maximum);
  }

  [Fact]
  public void SellIsMirrored()
  {
    var valid = Valid() with
    {
      GroupDirection = TradeDirection.Sell,
      CandidateDirection = TradeDirection.Sell,
      InitialEntry = 4000m,
      AddEntry = 3998m,
      DisplacementDirection = "down",
      BosDirection = "down",
    };

    Assert.Null(ScaleInTriggerPlanner.Validate(valid));
    Assert.Contains(
      "averaging-down guard",
      ScaleInTriggerPlanner.Validate(valid with { AddEntry = 4001m })
    );
  }

  private static ScaleInTriggerInput Valid() => new(
    TradeDirection.Buy,
    TradeDirection.Buy,
    InitialEntry: 4000m,
    AddEntry: 4002m,
    GroupPnl: 10m,
    InitialReachedBreakeven: true,
    EveryStopAvailable: true,
    OneGroup: true,
    LifetimeTrancheCount: 1,
    MaximumTranches: 2,
    DisplacementDirection: "up",
    DisplacementAgeBars: 2,
    MaximumDisplacementAgeBars: 3,
    BosDirection: "up",
    BosTimestamp: 1_100,
    GroupOpenedAt: 1_000,
    OpposingLevelDistanceAtr: 2m,
    OpposingLevelBufferAtr: 1m,
    BarTimestamp: 1_180,
    PreviousTrancheBarTimestamp: 1_000,
    CooldownBars: 3
  );
}

using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class ScaleInTriggerPlannerTests
{
  // ---------------------------------------------------------------------
  // Momentum add - unchanged behaviour, adapted to the new result type.
  // ---------------------------------------------------------------------

  [Fact]
  public void AcceptsCompleteFreshMomentumContinuation()
  {
    var result = ScaleInTriggerPlanner.Validate(Valid());

    Assert.True(result.Accepted);
    Assert.Equal("add_momentum", result.Mode);
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
    var result = ScaleInTriggerPlanner.Validate(Valid() with
    {
      GroupPnl = groupPnl,
      AddEntry = addEntry,
    });

    Assert.False(result.Accepted);
    Assert.Equal("shared", result.Mode);
    Assert.Contains(reason, result.RejectReason);
  }

  [Fact]
  public void RequiresFreshDisplacementWhenPullbackDisabled()
  {
    var missing = ScaleInTriggerPlanner.Validate(Valid() with
    {
      DisplacementDirection = null,
    });
    var stale = ScaleInTriggerPlanner.Validate(Valid() with
    {
      DisplacementAgeBars = 4,
    });

    Assert.False(missing.Accepted);
    Assert.Contains("fresh up displacement", missing.RejectReason);
    Assert.False(stale.Accepted);
    Assert.Contains("fresh up displacement", stale.RejectReason);
  }

  [Fact]
  public void RequiresBosSinceInitialEntry()
  {
    var result = ScaleInTriggerPlanner.Validate(Valid() with
    {
      BosTimestamp = 999,
    });

    Assert.False(result.Accepted);
    Assert.Equal("add_momentum", result.Mode);
    Assert.Contains("BOS since initial entry", result.RejectReason);
  }

  [Fact]
  public void RejectsOpposingLevelInsideAtrBuffer()
  {
    var result = ScaleInTriggerPlanner.Validate(Valid() with
    {
      OpposingLevelDistanceAtr = 0.8m,
    });

    Assert.False(result.Accepted);
    Assert.Contains("opposing level", result.RejectReason);
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

    Assert.Contains("cooldown", cooldown.RejectReason);
    Assert.Equal("shared", maximum.Mode);
    Assert.Contains("maximum tranche count", maximum.RejectReason);
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

    var accepted = ScaleInTriggerPlanner.Validate(valid);
    Assert.True(accepted.Accepted);
    Assert.Equal("add_momentum", accepted.Mode);

    var rejected = ScaleInTriggerPlanner.Validate(valid with { AddEntry = 4001m });
    Assert.Contains("averaging-down guard", rejected.RejectReason);
  }

  // ---------------------------------------------------------------------
  // Mode selection
  // ---------------------------------------------------------------------

  [Fact]
  public void CounterDirectionDisplacementWithAllPullbackConditionsSelectsPullback()
  {
    var result = ScaleInTriggerPlanner.Validate(ValidPullback());

    Assert.True(result.Accepted);
    Assert.Equal("add_pullback", result.Mode);
  }

  [Fact]
  public void FreshInDirectionDisplacementWinsEvenWhenPullbackConditionsAlsoHold()
  {
    // Same shape as ValidPullback, but with fresh in-direction displacement
    // layered on top - momentum must win outright, never falling through
    // to pullback evaluation regardless of what else is true.
    var input = ValidPullback() with
    {
      DisplacementDirection = "down",
      DisplacementAgeBars = 1,
      BosDirection = "down",
      BosTimestamp = 1_100,
    };

    var result = ScaleInTriggerPlanner.Validate(input);

    Assert.True(result.Accepted);
    Assert.Equal("add_momentum", result.Mode);
  }

  [Fact]
  public void NeitherModeQualifiesRejectionNamesTheClosestModeAndCondition()
  {
    // Displacement stale/wrong-direction (fails momentum's own gate) and
    // pullback disabled - only momentum was ever actually evaluated.
    var result = ScaleInTriggerPlanner.Validate(Valid() with
    {
      DisplacementDirection = "down",
      PullbackEnabled = false,
    });

    Assert.False(result.Accepted);
    Assert.Equal("add_momentum", result.Mode);
    Assert.Equal("displacement_stale_or_wrong_direction", result.Condition);
  }

  // ---------------------------------------------------------------------
  // Shared invariants, re-verified reachable in pullback mode too
  // ---------------------------------------------------------------------

  [Fact]
  public void PullbackModeStillEnforcesFavorableEntry()
  {
    // 23 Jul setup numbers: SELL, AddEntry above InitialEntry is averaging
    // down regardless of how good the rest of the pullback shape looks.
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      InitialEntry = 4065.17m,
      AddEntry = 4066.00m,
    });

    Assert.False(result.Accepted);
    Assert.Equal("shared", result.Mode);
    Assert.Contains("averaging-down guard", result.RejectReason);
  }

  [Fact]
  public void PullbackModeStillEnforcesGroupProfitability()
  {
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      GroupPnl = -5.00m,
    });

    Assert.False(result.Accepted);
    Assert.Equal("shared", result.Mode);
  }

  [Fact]
  public void PullbackModeStillRequiresInitialBreakeven()
  {
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      InitialReachedBreakeven = false,
    });

    Assert.False(result.Accepted);
    Assert.Equal("shared", result.Mode);
    Assert.Contains("TP1/breakeven", result.RejectReason);
  }

  [Fact]
  public void PullbackModeStillEnforcesCooldown()
  {
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      BarTimestamp = 1_179,
    });

    Assert.False(result.Accepted);
    Assert.Equal("add_pullback", result.Mode);
    Assert.Equal("cooldown", result.Condition);
  }

  [Fact]
  public void PullbackModeStillEnforcesMaxTranches()
  {
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      LifetimeTrancheCount = 2,
    });

    Assert.False(result.Accepted);
    Assert.Equal("shared", result.Mode);
    Assert.Contains("maximum tranche count", result.RejectReason);
  }

  // ---------------------------------------------------------------------
  // Pullback conditions (P1-P4)
  // ---------------------------------------------------------------------

  [Fact]
  public void CounterBosSinceGroupOpenRejectsPullback()
  {
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      CounterBosSinceGroupOpen = true,
    });

    Assert.False(result.Accepted);
    Assert.Equal("add_pullback", result.Mode);
    Assert.Equal("counter_bos", result.Condition);
  }

  [Fact]
  public void RetraceBelowFloorRejectsPullback()
  {
    // extreme 4040, initial 4065.17 -> denom 25.17; want ratio ~0.12
    var input = ValidPullback() with
    {
      InitialEntry = 4065.17m,
      ExtremeSinceGroupOpen = 4040.00m,
      AddEntry = 4043.02m, // |4043.02 - 4040| / 25.17 = 0.12
    };

    var result = ScaleInTriggerPlanner.Validate(input);

    Assert.False(result.Accepted);
    Assert.Equal("add_pullback", result.Mode);
    Assert.Equal("retrace_below_min", result.Condition);
  }

  [Fact]
  public void RetraceAboveCeilingRejectsPullback()
  {
    var input = ValidPullback() with
    {
      InitialEntry = 4065.17m,
      ExtremeSinceGroupOpen = 4040.00m,
      AddEntry = 4059.00m, // 19 / 25.17 = 0.755
    };

    var result = ScaleInTriggerPlanner.Validate(input);

    Assert.False(result.Accepted);
    Assert.Equal("add_pullback", result.Mode);
    Assert.Equal("retrace_above_max", result.Condition);
  }

  [Fact]
  public void RetraceInsideBandWithEverythingElseValidIsAccepted()
  {
    var input = ValidPullback() with
    {
      InitialEntry = 4065.17m,
      ExtremeSinceGroupOpen = 4040.00m,
      AddEntry = 4052.00m, // 12 / 25.17 = 0.477
      AddZoneLow = 4051.00m,
      AddZoneHigh = 4053.00m,
    };

    var result = ScaleInTriggerPlanner.Validate(input);

    Assert.True(result.Accepted);
    Assert.Equal("add_pullback", result.Mode);
  }

  [Fact]
  public void AddEntryOutsideAnyMappedZoneRejectsPullback()
  {
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      AddZoneLow = null,
      AddZoneHigh = null,
      AddZoneSide = null,
    });

    Assert.False(result.Accepted);
    Assert.Equal("zone_missing_or_wrong_side", result.Condition);
  }

  [Fact]
  public void AddEntryInsideDemandZoneOnASellRejectsAsWrongSide()
  {
    // Same bounds, wrong side label - a SELL pullback needs supply.
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      AddZoneSide = "demand",
    });

    Assert.False(result.Accepted);
    Assert.Equal("zone_missing_or_wrong_side", result.Condition);
  }

  [Fact]
  public void UnconfirmedRejectionCandleRejectsPullback()
  {
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      RejectionConfirmed = false,
    });

    Assert.False(result.Accepted);
    Assert.Equal("rejection_not_confirmed", result.Condition);
  }

  // ---------------------------------------------------------------------
  // Worked example: the 23 Jul setup
  // ---------------------------------------------------------------------

  [Fact]
  public void The23JulSetupIsRejectedAtTheOriginalObservedNumbers()
  {
    // retraceRatio = |4059 - 4040| / |4065.17 - 4040| = 19 / 25.17 = 0.755
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      InitialEntry = 4065.17m,
      ExtremeSinceGroupOpen = 4040.00m,
      AddEntry = 4059.00m,
    });

    Assert.False(result.Accepted);
    Assert.Equal("retrace_above_max", result.Condition);
  }

  [Fact]
  public void The23JulSetupStaysRejectedWithADeeperExtreme()
  {
    // retraceRatio = |4059 - 4035| / |4065.17 - 4035| = 24 / 30.17 = 0.795
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      InitialEntry = 4065.17m,
      ExtremeSinceGroupOpen = 4035.00m,
      AddEntry = 4059.00m,
    });

    Assert.False(result.Accepted);
    Assert.Equal("retrace_above_max", result.Condition);
  }

  [Fact]
  public void The23JulSetupIsAcceptedWithAShallowerAddEntry()
  {
    // retraceRatio = |4052 - 4040| / |4065.17 - 4040| = 12 / 25.17 = 0.477
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      InitialEntry = 4065.17m,
      ExtremeSinceGroupOpen = 4040.00m,
      AddEntry = 4052.00m,
      AddZoneLow = 4051.00m,
      AddZoneHigh = 4053.00m,
    });

    Assert.True(result.Accepted);
    Assert.Equal("add_pullback", result.Mode);
  }

  // ---------------------------------------------------------------------
  // Feature flag
  // ---------------------------------------------------------------------

  [Fact]
  public void PullbackDisabledRejectsEvenWhenEveryPullbackConditionWouldPass()
  {
    var result = ScaleInTriggerPlanner.Validate(ValidPullback() with
    {
      PullbackEnabled = false,
    });

    Assert.False(result.Accepted);
    Assert.Equal("add_momentum", result.Mode);
    Assert.Equal("displacement_stale_or_wrong_direction", result.Condition);
  }

  [Fact]
  public void PullbackFlagDoesNotAffectMomentumAcceptDecision()
  {
    var flagOff = ScaleInTriggerPlanner.Validate(Valid() with { PullbackEnabled = false });
    var flagOn = ScaleInTriggerPlanner.Validate(Valid() with { PullbackEnabled = true });

    Assert.True(flagOff.Accepted);
    Assert.True(flagOn.Accepted);
    Assert.Equal(flagOff.Mode, flagOn.Mode);
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

  // A SELL pullback in the shape of the 23 Jul incident but at the
  // "accepted" worked-example numbers (retraceRatio 0.477 - the incident's
  // own actual numbers, retraceRatio 0.755, are deliberately a REJECTING
  // case per the spec and are exercised explicitly in the 23-Jul tests
  // above): displacement stale/counter-direction (so momentum never
  // qualifies), all P1-P4 conditions satisfied, pullback enabled.
  private static ScaleInTriggerInput ValidPullback() => new(
    TradeDirection.Sell,
    TradeDirection.Sell,
    InitialEntry: 4065.17m,
    AddEntry: 4052.00m,
    GroupPnl: 10m,
    InitialReachedBreakeven: true,
    EveryStopAvailable: true,
    OneGroup: true,
    LifetimeTrancheCount: 1,
    MaximumTranches: 2,
    DisplacementDirection: "up",
    DisplacementAgeBars: 10,
    MaximumDisplacementAgeBars: 3,
    BosDirection: "down",
    BosTimestamp: 1_050,
    GroupOpenedAt: 1_000,
    OpposingLevelDistanceAtr: 2m,
    OpposingLevelBufferAtr: 1m,
    BarTimestamp: 1_180,
    PreviousTrancheBarTimestamp: 1_000,
    CooldownBars: 3,
    PullbackEnabled: true,
    CounterBosSinceGroupOpen: false,
    ExtremeSinceGroupOpen: 4040.00m,
    MinRetraceRatio: 0.20m,
    MaxRetraceRatio: 0.70m,
    AddZoneLow: 4051.00m,
    AddZoneHigh: 4053.00m,
    AddZoneSide: "supply",
    RejectionConfirmed: true
  );
}

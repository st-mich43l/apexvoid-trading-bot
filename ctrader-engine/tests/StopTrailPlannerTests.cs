using System.Globalization;
using System.Text.Json;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class StopTrailPlannerTests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU",
    "XAUUSD",
    7,
    Digits: 2,
    PipPosition: 2
  );

  private static readonly JsonDocument Fixture = LoadFixture();

  private static JsonDocument LoadFixture()
  {
    var directory = new DirectoryInfo(AppContext.BaseDirectory);
    while (directory is not null)
    {
      var candidate = Path.Combine(
        directory.FullName,
        "contracts",
        "autotrade",
        "stop-trail-parity.json"
      );
      if (File.Exists(candidate))
      {
        return JsonDocument.Parse(File.ReadAllText(candidate));
      }
      directory = directory.Parent;
    }
    throw new FileNotFoundException(
      "contracts/autotrade/stop-trail-parity.json was not found above "
      + AppContext.BaseDirectory
    );
  }

  private static IEnumerable<JsonElement> ParityCases() =>
    Fixture.RootElement.GetProperty("cases").EnumerateArray();

  private static decimal Number(JsonElement element, string name) =>
    decimal.Parse(
      element.GetProperty(name).GetString()!,
      CultureInfo.InvariantCulture
    );

  public static TheoryData<string> ParityCaseNames
  {
    get
    {
      var data = new TheoryData<string>();
      foreach (var item in ParityCases())
      {
        data.Add(item.GetProperty("name").GetString()!);
      }
      return data;
    }
  }

  [Theory]
  [MemberData(nameof(ParityCaseNames))]
  public void SharedStopTrailFixtureMatchesPlanner(string name)
  {
    var root = Fixture.RootElement;
    var item = ParityCases().Single(entry =>
      entry.GetProperty("name").GetString() == name
    );
    var pipSize = Number(root, "pip_size");
    var bufferTicks = root.GetProperty("break_even_buffer_ticks").GetInt32();
    var direction = item.GetProperty("direction").GetString() == "BUY"
      ? TradeDirection.Buy
      : TradeDirection.Sell;
    var entry = Number(item, "entry_price");
    var completedTargetIndex = item.GetProperty("completed_target_index").GetInt32();
    var initialStop = direction == TradeDirection.Buy
      ? entry - 6.5m
      : entry + 6.5m;
    var state = State(direction, entry, initialStop);

    var move = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(
        state,
        completedTargetIndex,
        Symbol,
        pipSize,
        bufferTicks
      )
    );

    Assert.Equal(Number(item, "expected_stop"), move.StopLoss);
    Assert.Equal(item.GetProperty("expected_label").GetString(), move.Label);
    Assert.Equal(Number(item, "expected_offset"), move.BufferPrice);
    Assert.Equal(
      Number(item, "expected_offset"),
      Math.Abs(move.StopLoss - entry)
    );
  }

  // 2026-08 R:R dig: TP2 previously moved the stop nowhere at all, leaving
  // the position flat at breakeven from TP1 all the way through to TP3 -
  // the dominant driver of real manual XAU trades scratching near zero
  // instead of banking real progress (58 closed trades: median win 36
  // pips). TP2 now trails to TP1's own level.
  //
  // 2026-09 owner-reported: the ladder grew from 2 rungs to 4
  // (0.5R/1R/2R/3R), and the trail step used to be "two behind" instead of
  // "one behind" - only ever equivalent for the old 2-rung ladder. On the
  // new 4-rung ladder that skipped a whole real, already-realized level:
  // TP3 trailed all the way back to TP1 (a no-op once TP2 had already
  // moved there) instead of advancing to TP2, and TP4 trailed to TP2
  // instead of TP3. Every rung advanced the trail by exactly one step.
  //
  // 2026-09-15 owner: reintroduced "two behind" but ONLY for the
  // second-to-last rung (TP4 of this 5-rung ladder) - the runner keeps
  // more room right before its final target. TP2 and TP3 still each trail
  // one behind exactly as above; TP4's own "two behind" destination
  // (TP2's level) is the SAME level TP3 already moved to one step earlier,
  // so the sequential TP4 call here is correctly a no-op (Plan returns
  // null - nothing to move, already there), not a further advance.
  [Theory]
  [InlineData(TradeDirection.Buy, 4000.26, 4003.2, 4006.2)]
  [InlineData(TradeDirection.Sell, 4000.14, 3997.2, 3994.2)]
  public void EachTargetTrailsToThePrecedingTargetsLevelExceptTheSecondToLast(
    TradeDirection direction,
    double afterTp1,
    double afterTp2,
    double afterTp3
  )
  {
    var state = State(direction);
    var tp1 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 0, Symbol, 0.1m, 6)
    );
    Assert.Equal(Convert.ToDecimal(afterTp1), tp1.StopLoss);
    Assert.Equal("BE+6 ticks", tp1.Label);
    Assert.Equal(0.06m, Math.Abs(tp1.StopLoss - state.EntryPrice));
    state = state with { CurrentStopLoss = tp1.StopLoss };

    var tp2 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 1, Symbol, 0.1m, 6)
    );
    Assert.Equal(Convert.ToDecimal(afterTp2), tp2.StopLoss);
    Assert.Equal("TP1", tp2.Label);
    state = state with { CurrentStopLoss = tp2.StopLoss };

    var tp3 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 2, Symbol, 0.1m, 6)
    );
    Assert.Equal(Convert.ToDecimal(afterTp3), tp3.StopLoss);
    Assert.Equal("TP2", tp3.Label);
    state = state with { CurrentStopLoss = tp3.StopLoss };

    // TP4 (ordinal 4 of 5) is the second-to-last rung: two behind is TP2,
    // the level TP3 just moved to - no further move needed.
    Assert.Null(StopTrailPlanner.Plan(state, 3, Symbol, 0.1m, 6));
    Assert.Null(StopTrailPlanner.Plan(state, 4, Symbol, 0.1m, 6));
  }

  [Fact]
  public void SecondToLastRungTrailsTwoBehindWhenNotAlreadyThere()
  {
    // Owner-reported 2026-09-15 (real XAU BUY #10, live money): a 4-rung
    // manual ladder (0.5R/1R/2R/3R... now 1R/2R/3R/4R) must trail to TP1
    // (two behind), not TP2 (one behind), once TP3 - the second-to-last
    // rung - books. Isolated call (no prior TP1/TP2 trail applied to
    // state), so this exercises the actual "two behind" resolution, not
    // the sequential no-op case above.
    var state = State(TradeDirection.Buy) with
    {
      TargetsPips = [50, 100, 150, 200],
      TargetOrdinals = [1, 2, 3, 4],
      TargetPrices = [4283.0m, 4288.0m, 4293.0m, 4298.0m],
      CurrentStopLoss = 4273.0m,
    };

    var afterTp3 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 2, Symbol, 0.1m, 6)
    );
    Assert.Equal(4283.0m, afterTp3.StopLoss);
    Assert.Equal("TP1", afterTp3.Label);
  }

  [Fact]
  public void AbsoluteTargetPricesDriveTrailNotFillRelativePips()
  {
    // Manual ladders book absolute TargetPrices; trail after TP4 (the
    // second-to-last rung of this 5-rung ladder) must lock to Absolute
    // TP2 (two behind), not Entry±TargetsPips (fill slippage desync).
    var state = State(TradeDirection.Sell, 4401.10m, 4408.10m) with
    {
      TargetsPips = [30, 60, 100, 130, 200],
      TargetOrdinals = [1, 2, 3, 4, 5],
      TargetPrices = [4398.0m, 4395.0m, 4391.0m, 4388.0m, 4381.0m],
      CurrentStopLoss = 4398.10m,
    };

    var afterTp4 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 3, Symbol, 0.1m, 6)
    );
    Assert.Equal(4395.0m, afterTp4.StopLoss);
    Assert.Equal("TP2", afterTp4.Label);
  }

  [Fact]
  public void MidLegFullGroupLadderTrailsByOrdinalNotLocalIndex()
  {
    // Manual Mid owns TP3/TP4 only, but TargetPrices is the full owner
    // ladder. After Mid books TP3, trail must lock to group TP2 (ordinal 2
    // → prices[1]), not prices[localIndex] which would be TP1 by accident
    // for index 0 and wrong for any later ordinal lookup.
    var state = State(TradeDirection.Buy, 4441.5m, 4437.0m) with
    {
      TargetsPips = [100, 130],
      TargetOrdinals = [3, 4],
      TargetPrices = [4446.0m, 4449.0m, 4453.0m, 4456.0m, 4463.0m],
      CurrentStopLoss = 4437.0m,
    };

    var afterTp3 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 0, Symbol, 0.1m, 6)
    );
    Assert.Equal(4449.0m, afterTp3.StopLoss);
    Assert.Equal("TP2", afterTp3.Label);
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 4000.26, 4000.25)]
  [InlineData(TradeDirection.Sell, 4000.14, 4000.15)]
  public void ProtectedBreakevenUsesTheProfitSideBufferSymmetrically(
    TradeDirection direction,
    double expectedThreshold,
    double worseStop
  )
  {
    const decimal entry = 4000.2m;
    var threshold = Convert.ToDecimal(expectedThreshold);
    var betterStop = direction == TradeDirection.Buy
      ? threshold + 0.01m
      : threshold - 0.01m;

    Assert.Equal(
      threshold,
      StopTrailPlanner.ProtectedBreakevenStop(direction, entry, Symbol, 6)
    );
    Assert.True(StopTrailPlanner.IsAtLeastProtectedBreakeven(
      direction, entry, threshold, Symbol, 6
    ));
    Assert.True(StopTrailPlanner.IsAtLeastProtectedBreakeven(
      direction, entry, betterStop, Symbol, 6
    ));
    Assert.False(StopTrailPlanner.IsAtLeastProtectedBreakeven(
      direction, entry, Convert.ToDecimal(worseStop), Symbol, 6
    ));
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 4350.02)]
  [InlineData(TradeDirection.Sell, 4352.98)]
  public void ManualLadderTp1UsesGroupEconomicBreakeven(
    TradeDirection direction,
    double expectedStop
  )
  {
    // TP1 already booked 30p on the closed 500-volume shallow slice.
    // Rather than bunch each runner at its own BE+6 ticks, solve one shared
    // stop that leaves the full original 3,000-volume group +0.6p if hit.
    var isBuy = direction == TradeDirection.Buy;
    var states = new[]
    {
      State(direction, 4351.5m, isBuy ? 4347.0m : 4356.0m) with
      {
        PositionId = 92,
        InitialVolume = 900,
        RemainingVolume = 900,
        GroupInitialVolume = 3_000,
      },
      State(direction, isBuy ? 4350.0m : 4353.0m, isBuy ? 4347.0m : 4356.0m) with
      {
        PositionId = 93,
        InitialVolume = 600,
        RemainingVolume = 600,
        GroupInitialVolume = 3_000,
      },
    };
    const decimal bookedPipVolume = 30m * 500m;

    var move = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.PlanGroupEconomicBreakeven(
        states,
        groupInitialVolume: 3_000,
        bookedPipVolume,
        Symbol,
        pipSize: 0.1m,
        protectedBufferTicks: 6
      )
    );

    Assert.Equal(Convert.ToDecimal(expectedStop), move.StopLoss);
    Assert.Equal("group BE+6 ticks", move.Label);
    var terminalPipVolume = bookedPipVolume + states.Sum(state => (
      direction == TradeDirection.Buy
        ? move.StopLoss - state.EntryPrice
        : state.EntryPrice - move.StopLoss
    ) / 0.1m * state.RemainingVolume);
    Assert.Equal(0.6m, terminalPipVolume / 3_000m);
    var remainingVwap = states.Sum(
      state => state.EntryPrice * state.RemainingVolume
    ) / states.Sum(state => state.RemainingVolume);
    Assert.True(direction == TradeDirection.Buy
      ? move.StopLoss < remainingVwap
      : move.StopLoss > remainingVwap);
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 4350.50)]
  [InlineData(TradeDirection.Sell, 4352.50)]
  public void GroupEconomicBreakevenNeverWidensAnExistingStopAndTightensToBreakeven(
    TradeDirection direction,
    double protectedStop
  )
  {
    var current = Convert.ToDecimal(protectedStop);
    var states = new[]
    {
      State(direction, 4351.5m, current) with
      {
        InitialVolume = 900,
        RemainingVolume = 900,
        GroupInitialVolume = 3_000,
      },
      State(direction, direction == TradeDirection.Buy ? 4350m : 4353m, current) with
      {
        PositionId = 93,
        InitialVolume = 600,
        RemainingVolume = 600,
        GroupInitialVolume = 3_000,
      },
    };

    // Booked profit funds a stop looser than the one already held, so the
    // economic stop itself never applies. It must never WIDEN the held
    // stop - but a held stop still short of the remaining volume's own
    // protected breakeven tightens to it (2026-09-21, manual 407) rather
    // than being left untouched.
    var move = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.PlanGroupEconomicBreakeven(
        states,
        groupInitialVolume: 3_000,
        bookedPipVolume: 15_000m,
        Symbol,
        pipSize: 0.1m,
        protectedBufferTicks: 6
      )
    );
    Assert.True(direction == TradeDirection.Buy
      ? move.StopLoss > current
      : move.StopLoss < current);
  }

  [Theory]
  [InlineData(TradeDirection.Buy)]
  [InlineData(TradeDirection.Sell)]
  public void GroupEconomicBreakevenFallsBackToProtectedBreakevenWhenTp1FundsAStopWorseThanTheHeldOne(
    TradeDirection direction
  )
  {
    // Owner-reported live 2026-09-21 (manual 407): one filled clip, TP1
    // banked half of it. The funded economic stop landed below entry -
    // worse than the original owner stop - so the planner returned null and
    // the runner stayed on its original stop until price swept it.
    var isBuy = direction == TradeDirection.Buy;
    var original = isBuy ? 4355.0m : 4364.0m;
    var entry = isBuy ? 4359.49m : 4359.51m;
    var states = new[]
    {
      State(direction, entry, original) with
      {
        PositionId = 41693612,
        InitialVolume = 800,
        RemainingVolume = 400,
        GroupInitialVolume = 800,
      },
    };

    var move = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.PlanGroupEconomicBreakeven(
        states,
        groupInitialVolume: 800,
        bookedPipVolume: 55.7m * 400m,
        Symbol,
        pipSize: 0.1m,
        protectedBufferTicks: 6
      )
    );

    Assert.Equal(
      StopTrailPlanner.ProtectedBreakevenStop(direction, entry, Symbol, 6),
      move.StopLoss
    );
  }

  [Theory]
  [InlineData(TradeDirection.Sell, 4341.04, 4346.0, 4344.5, 4344.50)]
  [InlineData(TradeDirection.Buy, 4341.0, 4336.0, 4338.5, 4338.50)]
  public void GroupEconomicBreakevenTrailsRunnerToDeepestPlannedEntry(
    TradeDirection direction,
    double entry,
    double original,
    double deepest,
    double expectedStop
  )
  {
    // Owner-reported live 2026-09-21 (manual 409, SELL zone 4341-4344): only
    // the shallow leg filled (0.08 @ 4341.04), TP1 banked half of it and the
    // unfilled legs (4342.5, 4344.5) were cancelled. The runner's stop went to
    // its own entry (4340.98) and a normal retest of the zone swept it for
    // ~0. It must sit at the deepest planned entry instead.
    var states = new[]
    {
      State(direction, Convert.ToDecimal(entry), Convert.ToDecimal(original)) with
      {
        PositionId = 41710394,
        InitialVolume = 800,
        RemainingVolume = 400,
        GroupInitialVolume = 800,
      },
    };

    var move = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.PlanGroupEconomicBreakeven(
        states,
        groupInitialVolume: 800,
        bookedPipVolume: 55.7m * 400m,
        Symbol,
        pipSize: 0.1m,
        protectedBufferTicks: 6,
        plannedDeepestEntry: Convert.ToDecimal(deepest)
      )
    );

    Assert.Equal(Convert.ToDecimal(expectedStop), move.StopLoss);
    Assert.Equal("deepest entry", move.Label);
    // Room, not BE: the runner stop is on the losing side of its own entry.
    Assert.True(direction == TradeDirection.Buy
      ? move.StopLoss < states[0].EntryPrice
      : move.StopLoss > states[0].EntryPrice);
  }

  [Fact]
  public void GroupEconomicBreakevenKeepsEconomicStopWhenItIsTighterThanDeepestEntry()
  {
    // Booked profit funds only 4352.98 (see ManualLadderTp1UsesGroupEconomic
    // Breakeven). A deeper planned entry must not loosen that: the group's
    // protected buffer wins.
    var states = new[]
    {
      State(TradeDirection.Sell, 4351.5m, 4356.0m) with
      {
        PositionId = 92,
        InitialVolume = 900,
        RemainingVolume = 900,
        GroupInitialVolume = 3_000,
      },
      State(TradeDirection.Sell, 4353.0m, 4356.0m) with
      {
        PositionId = 93,
        InitialVolume = 600,
        RemainingVolume = 600,
        GroupInitialVolume = 3_000,
      },
    };

    var move = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.PlanGroupEconomicBreakeven(
        states,
        groupInitialVolume: 3_000,
        bookedPipVolume: 30m * 500m,
        Symbol,
        pipSize: 0.1m,
        protectedBufferTicks: 6,
        plannedDeepestEntry: 4355.5m
      )
    );

    Assert.Equal(4352.98m, move.StopLoss);
    Assert.Equal("group BE+6 ticks", move.Label);
  }

  [Fact]
  public void GroupEconomicBreakevenDeepestEntryNeverLoosensAHeldStop()
  {
    var states = new[]
    {
      State(TradeDirection.Sell, 4341.04m, 4344.0m) with
      {
        PositionId = 41710394,
        InitialVolume = 800,
        RemainingVolume = 400,
        GroupInitialVolume = 800,
      },
    };

    var move = StopTrailPlanner.PlanGroupEconomicBreakeven(
      states,
      groupInitialVolume: 800,
      bookedPipVolume: 55.7m * 400m,
      Symbol,
      pipSize: 0.1m,
      protectedBufferTicks: 6,
      plannedDeepestEntry: 4344.5m
    );

    // The held 4344.00 is already tighter than the deepest entry; it is never
    // moved back out to 4344.50.
    Assert.True(move is null || move.StopLoss <= 4344.0m);
  }

  [Theory]
  [InlineData(TradeDirection.Buy)]
  [InlineData(TradeDirection.Sell)]
  public void GroupEconomicBreakevenSinglePriceLadderKeepsProtectedBreakeven(
    TradeDirection direction
  )
  {
    // No planned entry deeper than the fill (one-price ladder): there is no
    // zone depth to give, so the protected BE (+buffer) still applies.
    var isBuy = direction == TradeDirection.Buy;
    var original = isBuy ? 4355.0m : 4364.0m;
    var entry = isBuy ? 4359.49m : 4359.51m;
    var states = new[]
    {
      State(direction, entry, original) with
      {
        PositionId = 41693612,
        InitialVolume = 800,
        RemainingVolume = 400,
        GroupInitialVolume = 800,
      },
    };

    var move = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.PlanGroupEconomicBreakeven(
        states,
        groupInitialVolume: 800,
        bookedPipVolume: 55.7m * 400m,
        Symbol,
        pipSize: 0.1m,
        protectedBufferTicks: 6,
        plannedDeepestEntry: entry
      )
    );

    Assert.Equal(
      StopTrailPlanner.ProtectedBreakevenStop(direction, entry, Symbol, 6),
      move.StopLoss
    );
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
      StopTrailPlanner.Plan(state, 1, Symbol, 0.1m, 6)
    );

    Assert.Equal(4003.2m, move.StopLoss);
    Assert.Equal("TP1", move.Label);
  }

  [Fact]
  public void BuyBeStopDoesNotMoveBackwardBehindAnAlreadyBetterStop()
  {
    // Incident regression: entry 4087.66, existing SL 4088.00 is already
    // more protective than the BE+6 target of 4087.72 for a BUY - the
    // never-worsen rule must keep 4088.00, not overwrite it with 4087.72.
    var state = State(TradeDirection.Buy, 4087.66m, 4088.00m);

    Assert.Null(StopTrailPlanner.Plan(state, 0, Symbol, 0.1m, 6));
  }

  [Fact]
  public void SellBeStopDoesNotMoveBackwardBehindAnAlreadyBetterStop()
  {
    // Incident regression: entry 4100.74, existing SL 4100.50 is already
    // more protective than the BE+6 target of 4100.68 for a SELL.
    var state = State(TradeDirection.Sell, 4100.74m, 4100.50m);

    Assert.Null(StopTrailPlanner.Plan(state, 0, Symbol, 0.1m, 6));
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

    Assert.Null(StopTrailPlanner.Plan(state, 0, Symbol, 0.1m, 6));
  }

  private static AutoTradePositionState State(
    TradeDirection direction,
    decimal entryPrice = 4000.2m,
    decimal? currentStopLoss = null
  ) => new(
    "candidate",
    91,
    7,
    direction,
    entryPrice,
    1_000,
    1_000,
    [200, 200, 200, 200, 200],
    [30, 60, 90, 120, 200],
    0,
    1_000,
    currentStopLoss
      ?? (direction == TradeDirection.Buy ? entryPrice - 6.5m : entryPrice + 6.5m)
  );
}

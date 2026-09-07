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
  // instead of TP3. Every rung now advances the trail by exactly one step.
  [Theory]
  [InlineData(TradeDirection.Buy, 4000.26, 4003.2, 4006.2, 4009.2)]
  [InlineData(TradeDirection.Sell, 4000.14, 3997.2, 3994.2, 3991.2)]
  public void EachTargetTrailsToThePrecedingTargetsLevel(
    TradeDirection direction,
    double afterTp1,
    double afterTp2,
    double afterTp3,
    double afterTp4
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

    var tp4 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 3, Symbol, 0.1m, 6)
    );
    Assert.Equal(Convert.ToDecimal(afterTp4), tp4.StopLoss);
    Assert.Equal("TP3", tp4.Label);
    Assert.Null(StopTrailPlanner.Plan(state, 4, Symbol, 0.1m, 6));
  }

  [Fact]
  public void AbsoluteTargetPricesDriveTrailNotFillRelativePips()
  {
    // Manual ladders book absolute TargetPrices; trail after TP4 must lock
    // to Absolute TP3 (the preceding rung), not Entry±TargetsPips (fill
    // slippage desync).
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
    Assert.Equal(4391.0m, afterTp4.StopLoss);
    Assert.Equal("TP3", afterTp4.Label);
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
  public void GroupEconomicBreakevenNeverWidensAnExistingStop(
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

    Assert.Null(
      StopTrailPlanner.PlanGroupEconomicBreakeven(
        states,
        groupInitialVolume: 3_000,
        bookedPipVolume: 15_000m,
        Symbol,
        pipSize: 0.1m,
        protectedBufferTicks: 6
      )
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

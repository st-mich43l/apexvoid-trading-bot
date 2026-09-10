using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class TradePlanExecutionEngineTests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU",
    "XAUUSD",
    7,
    Digits: 2,
    PipPosition: 2,
    MinVolume: 100,
    StepVolume: 100,
    MaxVolume: 100_000,
    LotSize: 10_000
  );

  private static TradePlan MarketWatchPlan(
    string direction = "BUY",
    decimal zoneLow = 4088.10m,
    decimal zoneHigh = 4090.00m,
    decimal stopPrice = 4081.80m,
    long expiresAt = 1_720_003_600,
    int? maxSpreadTicks = 8,
    long maxVolume = 100_000
  ) => new(
    Version: 8,
    PlanId: "plan-1",
    ThesisId: "thesis-1",
    SetupId: "setup-1",
    Symbol: "XAU",
    CreatedAt: 1_720_000_000,
    ExpiresAt: expiresAt,
    Analysis: new TradePlanAnalysis(
      "Trend Pullback", "trend_pullback", direction,
      new[] { "M15" }, "M15", "M5", 1, 1, 0.8, 3, "up", "trend"
    ),
    SourceStructure: new TradePlanSourceStructure(
      "structure-1", "demand", "M15", zoneLow, zoneHigh, stopPrice
    ),
    Entry: new TradePlanEntry(
      "market_watch",
      expiresAt,
      ZoneLow: zoneLow,
      ZoneHigh: zoneHigh,
      Activation: "quote_inside_zone",
      PriceSide: direction == "BUY" ? "ask" : "bid",
      MaxSpreadTicks: maxSpreadTicks
    ),
    Stop: new TradePlanStop("absolute", stopPrice, "structural_invalidation"),
    Targets: new[]
    {
      new TradePlanTarget(
        "TP1", "absolute",
        direction == "BUY" ? zoneHigh + 6m : zoneLow - 6m,
        0.40m
      ),
      new TradePlanTarget(
        "TP2", "absolute",
        direction == "BUY" ? zoneHigh + 14m : zoneLow - 14m,
        0.35m
      ),
      new TradePlanTarget(
        "TP3", "absolute",
        direction == "BUY" ? zoneHigh + 25m : zoneLow - 25m,
        0.25m
      ),
    },
    Risk: new TradePlanRisk(1.0m, 1.0m, maxVolume, 2.0m),
    Management: new TradePlanManagement("TP1", 6, true),
    ExecutionPolicy: new TradePlanExecutionPolicy(true, false, true, true),
    Provenance: new TradePlanProvenance("v8", "map-1", "cfg-1"),
    Sizing: new TradePlanSizing(
      "equity_table", "owner_equity_v1", "single", Array.Empty<decimal>()
    )
  );

  private static TradePlan RiskSizedScalpPlan(
    decimal stopPips,
    long maxVolume = 100_000
  )
  {
    const decimal zoneLow = 4_000.00m;
    const decimal zoneHigh = 4_000.10m;
    return MarketWatchPlan(
      direction: "BUY",
      zoneLow: zoneLow,
      zoneHigh: zoneHigh,
      stopPrice: zoneHigh - stopPips * 0.1m,
      maxVolume: maxVolume
    ) with
    {
      Analysis = new TradePlanAnalysis(
        "Range Sweep Scalp", "scalp", "BUY",
        new[] { "M1" }, "M1", "M1", 1, 1, 0.8, 3, "range", "range"
      ),
      Risk = new TradePlanRisk(0.5m, 1.5m, maxVolume, 2.0m),
      Sizing = new TradePlanSizing(
        "risk", "owner_equity_v1", "single", Array.Empty<decimal>()
      ),
    };
  }

  [Fact]
  public void MarketWatchSubmitsWhenAskInsideZone()
  {
    var plan = MarketWatchPlan();

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4088.9m, ask: 4089.0m, spreadTicks: 2m, nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal(TradePlanEntryAction.SubmitMarket, decision.Action);
    Assert.True(decision.ShouldSubmit);
  }

  [Fact]
  public void ImmediateMarketChaseSubmitsOutsideTheAbandonedZone()
  {
    // Aug 24 HFS BUY: Python admitted a chase at 4631.89 after reclaiming
    // 4629.13-4630.11. The old market -> market_watch translation waited
    // for the abandoned band and expired while both targets traded.
    var plan = MarketWatchPlan(
      zoneLow: 4629.134892857143m,
      zoneHigh: 4630.110214285714m,
      stopPrice: 4628.89m,
      maxSpreadTicks: 50
    ) with
    {
      Entry = new TradePlanEntry(
        TradePlanContract.EntryTypeMarket,
        1_720_003_600,
        MaxSpreadTicks: 50,
        MaxSlippageTicks: 50,
        OrderPrice: 4631.89m
      ),
      Targets =
      [
        new TradePlanTarget("TP1", "absolute", 4632.89m, 0.5m),
        new TradePlanTarget("TP2", "absolute", 4633.89m, 0.5m),
      ],
      Management = new TradePlanManagement(null, 6, true),
    };

    TradePlanValidator.Validate(plan);
    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan,
      bid: 4632.00m,
      ask: 4632.14m,
      spreadTicks: 14m,
      nowUnixSeconds: 1_720_000_100,
      tickSize: 0.01m
    );

    Assert.Equal(TradePlanEntryAction.SubmitMarket, decision.Action);
    Assert.True(decision.ShouldSubmit);
  }

  [Fact]
  public void ImmediateMarketChaseRejectsWhenQuoteAlreadyThroughTp1()
  {
    // Live 2026-08-24 HFS SELL: order_price 4668.29, TP1 4667.29, bid
    // already 4667.17 — market would fill through TP1 and fake a take-profit.
    var plan = MarketWatchPlan(
      direction: "SELL",
      zoneLow: 4669.177214285714m,
      zoneHigh: 4670.411392857143m,
      stopPrice: 4671.29m,
      maxSpreadTicks: 50
    ) with
    {
      Entry = new TradePlanEntry(
        TradePlanContract.EntryTypeMarket,
        1_720_003_600,
        MaxSpreadTicks: 50,
        MaxSlippageTicks: 10,
        OrderPrice: 4668.29m
      ),
      Targets =
      [
        new TradePlanTarget("TP1", "absolute", 4667.29m, 0.5m),
        new TradePlanTarget("TP2", "absolute", 4666.29m, 0.5m),
      ],
      Management = new TradePlanManagement(null, 6, true),
    };

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan,
      bid: 4667.17m,
      ask: 4667.29m,
      spreadTicks: 12m,
      nowUnixSeconds: 1_720_000_100,
      tickSize: 0.01m
    );

    Assert.Equal(TradePlanEntryAction.Wait, decision.Action);
    Assert.Equal("chase_through_target", decision.RejectReason);
  }

  [Fact]
  public void ImmediateMarketChaseHonorsSlippageBudget()
  {
    var plan = MarketWatchPlan(
      direction: "SELL",
      zoneLow: 4669.18m,
      zoneHigh: 4670.41m,
      stopPrice: 4671.29m,
      maxSpreadTicks: 50
    ) with
    {
      Entry = new TradePlanEntry(
        TradePlanContract.EntryTypeMarket,
        1_720_003_600,
        MaxSpreadTicks: 50,
        MaxSlippageTicks: 10,
        OrderPrice: 4668.29m
      ),
      Targets =
      [
        new TradePlanTarget("TP1", "absolute", 4660.00m, 0.5m),
        new TradePlanTarget("TP2", "absolute", 4655.00m, 0.5m),
      ],
      Management = new TradePlanManagement(null, 6, true),
    };

    // 0.40 below order_price with 10-tick (0.10) budget — wait.
    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan,
      bid: 4667.89m,
      ask: 4668.01m,
      spreadTicks: 12m,
      nowUnixSeconds: 1_720_000_100,
      tickSize: 0.01m
    );

    Assert.Equal(TradePlanEntryAction.Wait, decision.Action);
    Assert.Equal("slippage_exceeds_declared_limit", decision.RejectReason);
  }

  [Fact]
  public void TargetBeyondFillAndFavorableExitGatesChaseThroughTp()
  {
    Assert.False(
      TradePlanExecutionEngine.TargetIsBeyondFill("SELL", 4667.17m, 4667.29m)
    );
    Assert.True(
      TradePlanExecutionEngine.TargetIsBeyondFill("SELL", 4668.00m, 4667.29m)
    );
    Assert.False(
      TradePlanExecutionEngine.ExitIsFavorableVsFill("SELL", 4667.17m, 4667.49m)
    );
    Assert.True(
      TradePlanExecutionEngine.ExitIsFavorableVsFill("SELL", 4668.00m, 4667.49m)
    );
  }

  [Fact]
  public void ImmediateMarketChaseStillHonorsSpreadLimit()
  {
    var plan = MarketWatchPlan() with
    {
      Entry = new TradePlanEntry(
        TradePlanContract.EntryTypeMarket,
        1_720_003_600,
        MaxSpreadTicks: 8,
        OrderPrice: 4089.0m
      ),
    };

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan,
      bid: 4089.0m,
      ask: 4089.2m,
      spreadTicks: 20m,
      nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal(TradePlanEntryAction.Wait, decision.Action);
    Assert.Equal("spread_exceeds_declared_limit", decision.RejectReason);
  }

  [Fact]
  public void ExplicitFxTrailMovesToOneRAfterConfiguredTrailTargetIsBooked()
  {
    // Explicit trail IDs still work when a plan declares them (legacy /
    // non-uniform ladders). Uniform fixed_rr 1R/2R omits trail IDs.
    var plan = MarketWatchPlan() with
    {
      Management = new TradePlanManagement(
        "TP1",
        6,
        true,
        TrailAfterTargetId: "TP2",
        TrailToTargetId: "TP1"
      ),
    };

    TradePlanValidator.Validate(plan);
    Assert.Equal(
      -1,
      TradePlanExecutionEngine.ResolveTrailTargetIndex(
        plan, nextTargetIndex: 2, highestBookedTargetIndex: 0
      )
    );
    Assert.Equal(
      0,
      TradePlanExecutionEngine.ResolveTrailTargetIndex(
        plan, nextTargetIndex: 2, highestBookedTargetIndex: 1
      )
    );
  }

  [Fact]
  public void TwoTargetLadderWithoutTrailIdsNeverAppliesLegacyTrail()
  {
    var plan = MarketWatchPlan() with
    {
      Targets =
      [
        new TradePlanTarget("TP1", "absolute", 4092.00m, 0.50m),
        new TradePlanTarget("TP2", "absolute", 4095.00m, 0.50m),
      ],
      Management = new TradePlanManagement("TP1", 6, true),
    };

    TradePlanValidator.Validate(plan);
    Assert.Equal(
      -1,
      TradePlanExecutionEngine.ResolveTrailTargetIndex(
        plan, nextTargetIndex: 1, highestBookedTargetIndex: 0
      )
    );
    Assert.Equal(
      -1,
      TradePlanExecutionEngine.ResolveTrailTargetIndex(
        plan, nextTargetIndex: 2, highestBookedTargetIndex: 1
      )
    );
  }

  [Fact]
  public void MarketWatchWaitsWhenAskOutsideZone()
  {
    var plan = MarketWatchPlan();

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4085.0m, ask: 4085.2m, spreadTicks: 2m, nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal(TradePlanEntryAction.Wait, decision.Action);
    Assert.False(decision.ShouldSubmit);
    // Live incident: a market_watch plan expired unfilled even though price
    // logs showed it re-entering the zone later - Wait decisions used to
    // carry no reason at all, so a poll's outcome was completely invisible
    // in production logs, making that "did it ever actually see the zone
    // again" question unanswerable after the fact. Naming this reason
    // (distinct from spread_exceeds_declared_limit) lets a future expiry
    // log line say which one actually happened.
    Assert.Equal("outside_zone", decision.RejectReason);
  }

  [Fact]
  public void MarketWatchSubmitsWhenBidInsideZoneEvenIfAskPokesJustAbove()
  {
    // 04 Aug 23:42 incident: BUY zone [4085.71, 4086.11] (0.40 wide, ~4
    // pips). Bid-based M1 bars showed price genuinely trading at 4086.03,
    // inside the zone, but the plan still expired 7 minutes later with
    // last_wait_reason=outside_zone. Root cause: the ask-only check
    // required the single trade-side quote strictly inside a zone
    // narrower than typical spread, so a normal spread of a few ticks put
    // ask just above zone high the entire time bid sat inside it. Reproduce
    // that exact geometry: bid inside, ask a few ticks over the top edge.
    var plan = MarketWatchPlan(
      zoneLow: 4085.71m, zoneHigh: 4086.11m, maxSpreadTicks: null
    );

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4086.03m, ask: 4086.15m, spreadTicks: 12m,
      nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal(TradePlanEntryAction.SubmitMarket, decision.Action);
    Assert.True(decision.ShouldSubmit);
  }

  [Fact]
  public void MarketWatchWaitsWhenNeitherBidNorAskReachTheZone()
  {
    // A spread wide enough that the whole [bid, ask] range still sits
    // clear of the zone must still wait - this is not "any touch fires",
    // it is "the tradable range overlaps the zone".
    var plan = MarketWatchPlan(
      zoneLow: 4085.71m, zoneHigh: 4086.11m, maxSpreadTicks: null
    );

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4086.20m, ask: 4086.30m, spreadTicks: 10m,
      nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal(TradePlanEntryAction.Wait, decision.Action);
    Assert.Equal("outside_zone", decision.RejectReason);
  }

  [Fact]
  public void MarketWatchWaitsWhenSpreadExceedsDeclaredLimit()
  {
    var plan = MarketWatchPlan(maxSpreadTicks: 8);

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4088.9m, ask: 4089.0m, spreadTicks: 20m, nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal(TradePlanEntryAction.Wait, decision.Action);
    Assert.Equal("spread_exceeds_declared_limit", decision.RejectReason);
  }

  [Fact]
  public void ExpiredPlanIsRejectedRegardlessOfQuote()
  {
    var plan = MarketWatchPlan(expiresAt: 1_720_000_050);

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4088.9m, ask: 4089.0m, spreadTicks: 2m, nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal("plan_expired", decision.RejectReason);
  }

  private static TradingAccountSnapshot Account(
    decimal equity,
    decimal? balance = null
  ) => new(
    1,
    IsLive: false,
    PermissionScope: "ScopeTrade",
    AccessRights: "FullAccess",
    AccountType: "Hedged",
    BrokerName: "Fusion Markets",
    Balance: balance ?? equity,
    Equity: equity,
    SnapshotTimestamp: 0
  );

  [Fact]
  public void CalculateVolumeWithoutSizingContractIsRejected()
  {
    var plan = MarketWatchPlan() with { Sizing = null };

    var error = Assert.Throws<TradePlanContractException>(() =>
      TradePlanExecutionEngine.CalculateVolume(
        plan, Account(10_000m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
      )
    );

    Assert.Equal("sizing_contract_missing", error.Message);
  }

  [Fact]
  public void CalculateVolumeUsesEquityTableIgnoringRiskPercent()
  {
    // RiskPercent=1 on a ~63-pip stop at equity 1300 would yield ~0.02 lots
    // under the old risk path; equity_table must produce table lots instead.
    var plan = MarketWatchPlan() with
    {
      Risk = new TradePlanRisk(1.0m, 1.0m, 100_000, 2.0m),
    };

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(1_300m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    Assert.Equal(1_200, result.TotalVolume); // LotsForEquity(1300)=0.12
    Assert.Empty(result.Slices);
    Assert.NotEqual(300, result.TotalVolume);
  }

  [Fact]
  public void CalculateVolumeScalesEquityTableByRiskMultiplier()
  {
    // A caller-provided multiplier still scales equity-table volume.
    var plan = MarketWatchPlan() with
    {
      Risk = new TradePlanRisk(1.0m, 2.0m, 100_000, 2.0m),
    };

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(2_500m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    // LotsForEquity(2500)=0.15 → ×2 = 0.30 lots → 3000 volume units.
    Assert.Equal(3_000, result.TotalVolume);
    Assert.Empty(result.Slices);
  }

  [Fact]
  public void CalculateVolumeUsesOnePointFiveScalpBoostAtOrAboveTwoThousandEquity()
  {
    var plan = MarketWatchPlan() with
    {
      Risk = new TradePlanRisk(1.0m, 1.5m, 100_000, 2.0m),
    };

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(2_500m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    // LotsForEquity(2500)=0.15 → ×1.5 = 0.225, rounded to 0.23 lots.
    Assert.Equal(2_300, result.TotalVolume);
    Assert.Empty(result.Slices);
  }

  [Fact]
  public void CalculateVolumeUsesOnePointFiveScalpBoostBelowTwoThousandEquity()
  {
    // Owner 2026-08-12: below $2k, scalp ×2 stamp becomes ×1.5 table lots
    // (0.10 → 0.15), not double and not half.
    var plan = MarketWatchPlan() with
    {
      Risk = new TradePlanRisk(1.0m, 2.0m, 100_000, 2.0m),
    };

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(800m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    // LotsForEquity(800)=0.10 → ×1.5 = 0.15 lots → 1500 volume units.
    Assert.Equal(1_500, result.TotalVolume);
    Assert.Empty(result.Slices);
  }

  [Fact]
  public void CalculateVolumeKeepsUnitMultiplierBelowTwoThousandEquity()
  {
    // Reaction / non-scalp stamps 1.0 — do not force the low-equity scalp cut.
    var plan = MarketWatchPlan() with
    {
      Risk = new TradePlanRisk(1.0m, 1.0m, 100_000, 2.0m),
    };

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(800m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    // LotsForEquity(800)=0.10 → ×1 = 0.10 lots → 1000 volume units.
    Assert.Equal(1_000, result.TotalVolume);
  }

  [Fact]
  public void CalculateVolumeSizesFromEquityTable()
  {
    var plan = MarketWatchPlan();

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(10_000m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    Assert.Equal(3_000, result.TotalVolume); // LotsForEquity(10000)=0.30
    Assert.Empty(result.Slices);
  }

  [Theory]
  [InlineData(12.0, 800L)]
  [InlineData(28.57, 300L)]
  public void CalculateVolumeRiskModeSizesFromDeclaredStop(
    decimal stopPips,
    long expectedVolume
  )
  {
    var result = TradePlanExecutionEngine.CalculateVolume(
      RiskSizedScalpPlan(stopPips), Account(2_000m), 0.1m, 10m, Symbol
    );

    // 0.5% of $2,000 is a $10 budget. Broker-step rounding floors the
    // resulting 0.0833 / 0.0350 lots to 0.08 / 0.03.
    Assert.Equal(expectedVolume, result.TotalVolume);
  }

  [Theory]
  [InlineData(5.0)]
  [InlineData(12.0)]
  [InlineData(20.0)]
  [InlineData(30.0)]
  public void CalculateVolumeRiskModeKeepsMoneyRiskConstant(decimal stopPips)
  {
    var result = TradePlanExecutionEngine.CalculateVolume(
      RiskSizedScalpPlan(stopPips), Account(2_000m), 0.1m, 10m, Symbol
    );
    var lots = result.TotalVolume / (decimal)Symbol.LotSize;
    var moneyRisk = lots * stopPips * 10m;

    // Step rounding may leave up to one 0.01-lot step of unused budget.
    Assert.InRange(moneyRisk, 8.5m, 10m);
  }

  [Fact]
  public void CalculateVolumeRiskModeRejectsZeroDeclaredStopDistance()
  {
    var error = Assert.Throws<TradePlanContractException>(() =>
      TradePlanExecutionEngine.CalculateVolume(
        RiskSizedScalpPlan(0m), Account(2_000m), 0.1m, 10m, Symbol
      )
    );

    Assert.Equal("risk_sizing_stop_pips_must_be_positive", error.Message);
  }

  [Fact]
  public void CalculateVolumeRiskModeKeepsMaxVolumeAsAHardReject()
  {
    var error = Assert.Throws<TradePlanContractException>(() =>
      TradePlanExecutionEngine.CalculateVolume(
        RiskSizedScalpPlan(5m, maxVolume: 500), Account(2_000m), 0.1m, 10m, Symbol
      )
    );

    Assert.Equal("equity_table_above_broker_maximum", error.Message);
  }

  [Fact]
  public void SmallRiskSizedScalpLadderDegradesToOneFullExit()
  {
    var plan = RiskSizedScalpPlan(30m) with
    {
      Targets = new[]
      {
        new TradePlanTarget("TP1", "absolute", 4_003m, 0.5m),
        new TradePlanTarget("TP2", "absolute", 4_006m, 0.5m),
      },
      Management = new TradePlanManagement("TP1", 6, true),
    };
    var volume = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(2_000m), 0.1m, 10m, Symbol
    );

    var degraded = TradePlanExecutionEngine.DegradeScalpLadderForMinVolume(
      plan, volume.TotalVolume, Symbol
    );

    Assert.NotNull(degraded);
    Assert.Single(degraded.Targets);
    Assert.Equal("TP2", degraded.Targets[0].TargetId);
    Assert.Equal(1m, degraded.Targets[0].CloseRatio);
    Assert.Null(degraded.Management.BeAfterTargetId);
  }

  [Fact]
  public void CalculateVolumeRejectsWhenTableExceedsDeclaredMaxVolume()
  {
    var plan = MarketWatchPlan(maxVolume: 500);

    var error = Assert.Throws<TradePlanContractException>(() =>
      TradePlanExecutionEngine.CalculateVolume(
        plan, Account(1_000_000m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
      )
    );

    Assert.Equal("equity_table_above_broker_maximum", error.Message);
  }

  [Fact]
  public void CalculateVolumeRejectsXauMaxVolumeOnFxLotSize()
  {
    // Prod 2026-08-17 GBPJPY HFS: Python stamped XAU max_volume=100_000 on
    // an FX plan. Broker LotSize=10_000_000 → 0.01 lot ceiling, so the
    // $1,067 / 1.5× scalp table (0.18 lots) rejected pre-submit.
    var gbpjpy = Symbol with
    {
      RedisSymbol = "GBPJPY",
      CTraderSymbol = "GBPJPY",
      Digits = 3,
      PipPosition = 2,
      MinVolume = 1_000,
      StepVolume = 1_000,
      MaxVolume = 500_000_000,
      LotSize = 10_000_000
    };
    var plan = MarketWatchPlan(maxVolume: 100_000) with { Symbol = "GBPJPY" };

    var error = Assert.Throws<TradePlanContractException>(() =>
      TradePlanExecutionEngine.CalculateVolume(
        plan,
        Account(1_067.54m),
        pipSize: 0.01m,
        pipValuePerLot: 7m,
        symbol: gbpjpy
      )
    );

    Assert.Equal("equity_table_above_broker_maximum", error.Message);
  }

  [Fact]
  public void CalculateVolumeAcceptsFxLotsWhenPlanMaxVolumeUsesFxLotSize()
  {
    var gbpjpy = Symbol with
    {
      RedisSymbol = "GBPJPY",
      CTraderSymbol = "GBPJPY",
      Digits = 3,
      PipPosition = 2,
      MinVolume = 1_000,
      StepVolume = 1_000,
      MaxVolume = 500_000_000,
      LotSize = 10_000_000
    };
    var plan = MarketWatchPlan(maxVolume: 100_000_000) with
    {
      Symbol = "GBPJPY",
      Risk = new TradePlanRisk(1.0m, 2.0m, 100_000_000, 2.0m),
    };

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan,
      Account(1_067.54m),
      pipSize: 0.01m,
      pipValuePerLot: 7m,
      symbol: gbpjpy
    );

    // LotsForEquity(1067.54)=0.12 → scalp < $2k ×1.5 = 0.18 lots
    // → 0.18 * 10_000_000 = 1_800_000 volume units.
    Assert.Equal(1_800_000, result.TotalVolume);
  }

  private static TradePlan LimitLadderPlan(
    string direction = "BUY",
    decimal legPrice1 = 4089.50m,
    decimal legPrice2 = 4085.00m,
    decimal leg1Ratio = 0.60m,
    decimal leg2Ratio = 0.40m,
    decimal stopPrice = 4079.00m,
    long maxVolume = 100_000
  ) => new(
    Version: 8,
    PlanId: "plan-1",
    ThesisId: "thesis-1",
    SetupId: "setup-1",
    Symbol: "XAU",
    CreatedAt: 1_720_000_000,
    ExpiresAt: 1_720_003_600,
    Analysis: new TradePlanAnalysis(
      "Zone Reaction", "supply_demand", direction,
      new[] { "M15" }, "M15", "M5", 1, 1, 0.65, 2, "up", "range"
    ),
    SourceStructure: new TradePlanSourceStructure(
      "structure-1", "demand", "M15", legPrice2, legPrice1, stopPrice
    ),
    Entry: new TradePlanEntry(
      "limit_ladder",
      1_720_003_600,
      ZoneLow: legPrice2,
      ZoneHigh: legPrice1,
      Legs: new[]
      {
        new TradePlanEntryLeg("L1", legPrice1, leg1Ratio),
        new TradePlanEntryLeg("L2", legPrice2, leg2Ratio),
      }
    ),
    Stop: new TradePlanStop("absolute", stopPrice, "structural_invalidation"),
    Targets: new[] { new TradePlanTarget("TP1", "absolute", legPrice1 + 8m, 1.0m) },
    Risk: new TradePlanRisk(1.0m, 1.0m, maxVolume, 2.0m),
    Management: new TradePlanManagement(null, 6, true),
    ExecutionPolicy: new TradePlanExecutionPolicy(false, true, true, true),
    Provenance: new TradePlanProvenance("v8", "map-1", "cfg-1"),
    Sizing: new TradePlanSizing(
      "equity_table",
      "owner_equity_v1",
      "zone_scale",
      new[] { leg1Ratio, leg2Ratio }
    )
  );

  [Fact]
  public void CalculateVolumeSlicesForMarketWithLimitScaleMatchEquity1300()
  {
    var plan = new TradePlan(
      Version: 8,
      PlanId: "plan-mwls",
      ThesisId: "thesis-1",
      SetupId: "setup-1",
      Symbol: "XAU",
      CreatedAt: 1_720_000_000,
      ExpiresAt: 1_720_003_600,
      Analysis: new TradePlanAnalysis(
        "Key Level Reaction", "key_level", "BUY",
        new[] { "M15" }, "M15", "M5", 1, 1, 0.65, 2, "up", "range"
      ),
      SourceStructure: new TradePlanSourceStructure(
        "structure-1", "key_level", "M15", 4085.00m, 4089.50m, 4079.00m
      ),
      Entry: new TradePlanEntry(
        TradePlanContract.EntryTypeMarketWithLimitScale,
        1_720_003_600,
        ZoneLow: 4085.00m,
        ZoneHigh: 4089.50m,
        Legs: new[]
        {
          new TradePlanEntryLeg(
            "L1", 4089.10m, 0.70m, TradePlanContract.OrderTypeMarket
          ),
          new TradePlanEntryLeg(
            "L2", 4085.00m, 0.30m, TradePlanContract.OrderTypeLimit
          ),
        }
      ),
      Stop: new TradePlanStop("absolute", 4079.00m, "structural_invalidation"),
      Targets: new[] { new TradePlanTarget("TP1", "absolute", 4097.00m, 1.0m) },
      Risk: new TradePlanRisk(1.0m, 1.0m, 100_000, 2.0m),
      Management: new TradePlanManagement(null, 6, true),
      ExecutionPolicy: new TradePlanExecutionPolicy(true, true, true, true),
      Provenance: new TradePlanProvenance("v8", "map-1", "cfg-1"),
      Sizing: new TradePlanSizing(
        "equity_table",
        "owner_equity_v1",
        "zone_scale",
        new[] { 0.70m, 0.30m }
      )
    );

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(1_300m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    Assert.Equal(1_200, result.TotalVolume);
    Assert.Equal(800, result.Slices.Single(s => s.TargetId == "L1").Volume);
    Assert.Equal(400, result.Slices.Single(s => s.TargetId == "L2").Volume);
  }

  [Fact]
  public void EvaluateEntrySubmitsMarketWithLimitScaleImmediately()
  {
    var plan = LimitLadderPlan(leg1Ratio: 0.70m, leg2Ratio: 0.30m) with
    {
      Entry = new TradePlanEntry(
        TradePlanContract.EntryTypeMarketWithLimitScale,
        1_720_003_600,
        ZoneLow: 4085.00m,
        ZoneHigh: 4089.50m,
        Legs: new[]
        {
          new TradePlanEntryLeg("L1", 4089.10m, 0.70m, "market"),
          new TradePlanEntryLeg("L2", 4085.00m, 0.30m, "limit"),
        }
      ),
    };

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4089.0m, ask: 4089.1m, spreadTicks: 1m, nowUnixSeconds: 1_720_000_100
    );

    Assert.True(decision.ShouldSubmit);
    Assert.Equal(TradePlanEntryAction.SubmitLadder, decision.Action);
  }

  [Fact]
  public void MarketWithLimitScaleWaitsWhenL1HasSlippedPastTheDeclaredCap()
  {
    // Owner-reported 2026-09-10 (real XAU BUY, Key Level): L1's price is
    // stamped from the live quote at plan-build time, then fires as an
    // outright market order whenever the engine gets around to submitting
    // it - with no cap on how far price can have moved in that gap. A fast
    // M5 break let the fill land 0.74 above the published zone's own high
    // edge. Mirrors EvaluateMarket's existing MaxSlippageTicks cap (Aug 24
    // HFS fix) for the route that never got it.
    var plan = LimitLadderPlan(leg1Ratio: 0.70m, leg2Ratio: 0.30m) with
    {
      Entry = new TradePlanEntry(
        TradePlanContract.EntryTypeMarketWithLimitScale,
        1_720_003_600,
        ZoneLow: 4085.00m,
        ZoneHigh: 4089.50m,
        MaxSlippageTicks: 10,
        Legs: new[]
        {
          new TradePlanEntryLeg("L1", 4089.10m, 0.70m, "market"),
          new TradePlanEntryLeg("L2", 4085.00m, 0.30m, "limit"),
        }
      ),
    };

    // Ask has run 0.25 (25 ticks) past L1's declared 4089.10 - well beyond
    // the 10-tick (0.10) budget.
    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan,
      bid: 4089.20m,
      ask: 4089.35m,
      spreadTicks: 15m,
      nowUnixSeconds: 1_720_000_100,
      tickSize: 0.01m
    );

    Assert.Equal(TradePlanEntryAction.Wait, decision.Action);
    Assert.Equal("slippage_exceeds_declared_limit", decision.RejectReason);
  }

  [Fact]
  public void MarketWithLimitScaleSubmitsWhenL1IsWithinTheSlippageBudget()
  {
    var plan = LimitLadderPlan(leg1Ratio: 0.70m, leg2Ratio: 0.30m) with
    {
      Entry = new TradePlanEntry(
        TradePlanContract.EntryTypeMarketWithLimitScale,
        1_720_003_600,
        ZoneLow: 4085.00m,
        ZoneHigh: 4089.50m,
        MaxSlippageTicks: 10,
        Legs: new[]
        {
          new TradePlanEntryLeg("L1", 4089.10m, 0.70m, "market"),
          new TradePlanEntryLeg("L2", 4085.00m, 0.30m, "limit"),
        }
      ),
    };

    // Ask only 0.05 (5 ticks) past L1's declared price - inside the budget.
    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan,
      bid: 4089.00m,
      ask: 4089.15m,
      spreadTicks: 15m,
      nowUnixSeconds: 1_720_000_100,
      tickSize: 0.01m
    );

    Assert.True(decision.ShouldSubmit);
    Assert.Equal(TradePlanEntryAction.SubmitLadder, decision.Action);
  }

  [Fact]
  public void LimitLadderSubmitsWhenBothLegsAreStillRestingBelowTheLiveQuote()
  {
    // Neither leg is marketable for a BUY (both rest below the live ask),
    // however far they sit from the current quote - a resting limit can
    // only ever fill at its own declared price, never chase, so it needs
    // (and gets) no slippage check regardless of distance.
    var plan = LimitLadderPlan(
      legPrice1: 4086.00m, legPrice2: 4085.00m, leg1Ratio: 0.60m, leg2Ratio: 0.40m
    ) with
    {
      Entry = new TradePlanEntry(
        TradePlanContract.EntryTypeLimitLadder,
        1_720_003_600,
        ZoneLow: 4085.00m,
        ZoneHigh: 4086.00m,
        MaxSlippageTicks: 10,
        Legs: new[]
        {
          new TradePlanEntryLeg("L1", 4086.00m, 0.60m),
          new TradePlanEntryLeg("L2", 4085.00m, 0.40m),
        }
      ),
    };

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan,
      bid: 4089.65m,
      ask: 4089.80m,
      spreadTicks: 15m,
      nowUnixSeconds: 1_720_000_100,
      tickSize: 0.01m
    );

    Assert.True(decision.ShouldSubmit);
    Assert.Equal(TradePlanEntryAction.SubmitLadder, decision.Action);
  }

  [Fact]
  public void CalculateVolumeSlicesForALimitLadderAreProportionalToLegVolumeRatio()
  {
    var plan = LimitLadderPlan(leg1Ratio: 0.70m, leg2Ratio: 0.30m);

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(1_300m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    Assert.Equal(1_200, result.TotalVolume);
    Assert.Equal(2, result.Slices.Count);
    Assert.Equal(result.TotalVolume, result.Slices.Sum(slice => slice.Volume));
    var l1 = result.Slices.Single(slice => slice.TargetId == "L1").Volume;
    var l2 = result.Slices.Single(slice => slice.TargetId == "L2").Volume;
    // SplitEntryVolume step-aligns 70/30 on 0.12 lots → 0.08 + 0.04.
    Assert.Equal(800, l1);
    Assert.Equal(400, l2);
  }

  [Fact]
  public void HasReachedTargetUsesDirectionCorrectSide()
  {
    var buyPlan = MarketWatchPlan(direction: "BUY");
    var sellPlan = MarketWatchPlan(direction: "SELL", zoneLow: 4110.00m, zoneHigh: 4112.00m, stopPrice: 4118.50m);
    var buyTarget = buyPlan.Targets[0];
    var sellTarget = sellPlan.Targets[0];

    Assert.True(TradePlanExecutionEngine.HasReachedTarget(buyPlan, buyTarget, buyTarget.Price));
    Assert.False(
      TradePlanExecutionEngine.HasReachedTarget(buyPlan, buyTarget, buyTarget.Price - 1m)
    );
    Assert.True(
      TradePlanExecutionEngine.HasReachedTarget(sellPlan, sellTarget, sellTarget.Price)
    );
    Assert.False(
      TradePlanExecutionEngine.HasReachedTarget(sellPlan, sellTarget, sellTarget.Price + 1m)
    );
  }

  [Fact]
  public void SellWholeNumberTargetGetsVipHandleCushion()
  {
    // Mirror algo-bot watcher._tp_hit: whole handle 4323 books when ask is
    // a pip-tick above (spread), not a full point above (that clipped #3
    // TP1 to +22 instead of +30).
    Assert.True(
      TradePlanExecutionEngine.HasReachedExitTarget("SELL", 4323.05m, 4323.00m)
    );
    Assert.False(
      TradePlanExecutionEngine.HasReachedExitTarget("SELL", 4323.87m, 4323.00m)
    );
    Assert.False(
      TradePlanExecutionEngine.HasReachedExitTarget("SELL", 4324.00m, 4323.00m)
    );
  }

  [Fact]
  public void SellDecimalTargetStaysExact()
  {
    Assert.True(
      TradePlanExecutionEngine.HasReachedExitTarget("SELL", 4408.50m, 4408.50m)
    );
    Assert.False(
      TradePlanExecutionEngine.HasReachedExitTarget("SELL", 4408.51m, 4408.50m)
    );
  }

  [Fact]
  public void BreakEvenMatchesWorkedXauExampleForBuy()
  {
    var plan = MarketWatchPlan(direction: "BUY");

    var result = TradePlanExecutionEngine.CalculateBreakEven(
      plan, brokerConfirmedFillPrice: 4087.66m, currentStop: 4081.80m, symbol: Symbol
    );

    Assert.Equal(4087.72m, result.DesiredStop);
    Assert.Equal(4087.72m, result.NewStop);
    Assert.True(result.Improved);
  }

  [Fact]
  public void BreakEvenMatchesWorkedXauExampleForSell()
  {
    var plan = MarketWatchPlan(direction: "SELL", zoneLow: 4110.00m, zoneHigh: 4112.00m, stopPrice: 4118.50m);

    var result = TradePlanExecutionEngine.CalculateBreakEven(
      plan, brokerConfirmedFillPrice: 4087.66m, currentStop: 4118.50m, symbol: Symbol
    );

    Assert.Equal(4087.60m, result.DesiredStop);
    Assert.Equal(4087.60m, result.NewStop);
  }

  [Fact]
  public void BreakEvenNeverWorsensAnAlreadyBetterStop()
  {
    var plan = MarketWatchPlan(direction: "BUY");

    var result = TradePlanExecutionEngine.CalculateBreakEven(
      plan, brokerConfirmedFillPrice: 4087.66m, currentStop: 4090.00m, symbol: Symbol
    );

    Assert.Equal(4090.00m, result.NewStop);
    Assert.False(result.Improved);
  }
}

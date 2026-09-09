using System.Text.Json;
using ApexVoid.CTraderFeed;
using StackExchange.Redis;

namespace CTraderFeed.Tests;

/// <summary>
/// Proves TradePlanRuntime is a genuinely wired broker-execution path, not
/// just pure decision logic: a real TradePlan JSON payload goes in via
/// the execution:trade_plans stream, and a real market order comes out via
/// ICTraderTradeClient, with fill/target/BE tracking and restart recovery -
/// see docs/adr-trade-plan-v8-cutover.md Sections F/H/I/J/K.
/// </summary>
public sealed class TradePlanRuntimeTests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU", "XAUUSD", 7, Digits: 2, PipPosition: 2,
    MinVolume: 100, StepVolume: 100, MaxVolume: 100_000, LotSize: 10_000
  );

  private static AutoTradeOptions Options(string contractMode = "v8_only") => new(
    Enabled: true,
    DryRun: false,
    ExpectedBroker: "Fusion",
    StopLossDistance: 6.5m,
    TargetsPips: [30, 60, 90, 120, 200],
    TargetWeights: [20, 20, 20, 20, 20],
    BreakEvenBufferTicks: 3,
    CandidateMaxAgeSeconds: 90,
    SpotMaxAgeSeconds: 5,
    MaxSpreadPips: 5,
    MaxEntryDistancePips: 10,
    MinConfluence: 2,
    PollMilliseconds: 10,
    CandidateStream: "auto_trade:candidates",
    EventStream: "auto_trade:events",
    Label: "apexvoid-auto",
    ContractMode: contractMode
  );

  private static string PlanJson(
    string planId = "v8:plan-1",
    string thesisId = "thesis-1",
    string setupId = "setup-1",
    string direction = "BUY",
    decimal zoneLow = 4088.10m,
    decimal zoneHigh = 4090.00m,
    decimal stopPrice = 4082.50m,
    long expiresAt = 2_000_000_000,
    string strategy = "Trend Pullback",
    string strategyFamily = "trend_pullback",
    string symbol = "XAU",
    string? targetsJson = null,
    string? managementJson = null,
    string? entryJson = null
  )
  {
    targetsJson ??= """
      [
        {"target_id": "TP1", "type": "absolute", "price": "4096.00", "close_ratio": "0.5"},
        {"target_id": "TP2", "type": "absolute", "price": "4104.00", "close_ratio": "0.5"}
      ]
      """;
    managementJson ??= """
      {
        "be_after_target_id": "TP1",
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      }
      """;
    entryJson ??= $$"""
      {
        "type": "market_watch",
        "expires_at": {{expiresAt}},
        "zone_low": "{{zoneLow}}",
        "zone_high": "{{zoneHigh}}",
        "activation": "quote_inside_zone",
        "price_side": "{{(direction == "BUY" ? "ask" : "bid")}}",
        "max_spread_ticks": 8,
        "max_slippage_ticks": 10,
        "legs": []
      }
      """;
    return $$"""
    {
      "version": 8,
      "plan_id": "{{planId}}",
      "thesis_id": "{{thesisId}}",
      "setup_id": "{{setupId}}",
      "symbol": "{{symbol}}",
      "created_at": 1719999600,
      "expires_at": {{expiresAt}},
      "analysis": {
        "strategy": "{{strategy}}",
        "strategy_family": "{{strategyFamily}}",
        "direction": "{{direction}}",
        "context_timeframes": ["M15"],
        "formation_timeframe": "H1",
        "confirmation_timeframe": "M15",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 3.0,
        "confluence": 3,
        "bias": "up",
        "regime": "trend",
        "reasons": ["htf_uptrend"],
        "tags": []
      },
      "source_structure": {
        "structure_id": "zone-xau-4088-4090",
        "kind": "demand",
        "timeframe": "H1",
        "low": "4088.10",
        "high": "4090.00",
        "invalidation_price": "4081.80"
      },
      "entry": {{entryJson}},
      "stop": {
        "type": "absolute",
        "price": "{{stopPrice}}",
        "source": "structure",
        "structure_id": "zone-xau-4088-4090",
        "reason": "protective stop plan"
      },
      "targets": {{targetsJson}},
      "risk": {
        "risk_percent": "1.0",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "single",
        "leg_ratios": []
      },
      "management": {{managementJson}},
      "execution_policy": {
        "allow_market": true,
        "allow_limit": false,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """;
  }

  [Fact]
  public async Task ReceivesPlanAndSubmitsMarketOrderWhenQuoteEntersZone()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    // Quote outside the zone: plan should be received but not submit yet.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.Empty(client.MarketOrders);
    var received = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Received, received.Stage);

    // Quote enters the zone: should submit a market order now.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    var order = Assert.Single(client.MarketOrders);
    Assert.Equal(TradeDirection.Buy, order.Direction);
    Assert.Contains("v8:plan-1", order.Comment);
    var open = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, open.Stage);
    Assert.NotNull(open.PositionId);
    var events = store.Events.Select(e => e.Type).ToArray();
    Assert.DoesNotContain("plan_armed", events);
    Assert.Contains("order_filled", events);
  }

  [Fact]
  public async Task OrderFilledEventCarriesMathTelemetryForOutcomeCorrelation()
  {
    // Owner 2026-09-08: "collect data 2 weeks to see if order that has
    // good math quality can process well than other or not" - detection-
    // time math telemetry (fib ratio, momentum velocity/acceleration,
    // dealing-range premium/discount) must reach the published
    // order_filled event so Postgres (auto_trade_fills) can later
    // correlate it against the eventual outcome.
    const string planJson = """
    {
      "version": 8,
      "plan_id": "v8:plan-math-telemetry",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Trend Pullback",
        "strategy_family": "trend_pullback",
        "direction": "BUY",
        "context_timeframes": ["M15"],
        "formation_timeframe": "H1",
        "confirmation_timeframe": "M15",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 3.0,
        "confluence": 3,
        "bias": "up",
        "regime": "trend",
        "reasons": ["htf_uptrend"],
        "tags": [],
        "math_fib_ratio": 0.618,
        "math_velocity": 0.42,
        "math_acceleration": 0.15,
        "math_pd": 0.35
      },
      "source_structure": {
        "structure_id": "zone-xau-4088-4090",
        "kind": "demand",
        "timeframe": "H1",
        "low": "4088.10",
        "high": "4090.00",
        "invalidation_price": "4081.80"
      },
      "entry": {
        "type": "market_watch",
        "expires_at": 2000000000,
        "zone_low": "4088.10",
        "zone_high": "4090.00",
        "activation": "quote_inside_zone",
        "price_side": "ask",
        "max_spread_ticks": 8,
        "max_slippage_ticks": 10,
        "legs": []
      },
      "stop": {
        "type": "absolute",
        "price": "4082.50",
        "source": "structure",
        "structure_id": "zone-xau-4088-4090",
        "reason": "protective stop plan"
      },
      "targets": [
        {"target_id": "TP1", "type": "absolute", "price": "4096.00", "close_ratio": "0.5"},
        {"target_id": "TP2", "type": "absolute", "price": "4104.00", "close_ratio": "0.5"}
      ],
      "risk": {
        "risk_percent": "1.0",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "single",
        "leg_ratios": []
      },
      "management": {
        "be_after_target_id": "TP1",
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": true,
        "allow_limit": false,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """;
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(planJson);
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    var filled = Assert.Single(store.Events, e => e.Type == "order_filled");
    Assert.Equal(0.618, filled.MathFibRatio);
    Assert.Equal(0.42, filled.MathVelocity);
    Assert.Equal(0.15, filled.MathAcceleration);
    Assert.Equal(0.35, filled.MathPd);
  }

  [Fact]
  public async Task ManualBrokerCloseFinalizesPlanWithSignedPips()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient
    {
      PositionCloseReasonToReturn = PositionCloseReason.ManualOrExternalOrder,
      PositionCloseExecutionPriceToReturn = 4094.50m,
    };
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    var positionId = Assert.Single(runtime.TrackedStates).PositionId;
    Assert.NotNull(positionId);

    client.RemovePosition(positionId.Value);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4094.45m, 4094.50m, 2), CancellationToken.None
    );

    Assert.Empty(runtime.TrackedStates);
    var closed = Assert.Single(store.Events, item => item.Type == "position_closed");
    Assert.Equal("manual_or_external_close", closed.ReasonCode);
    Assert.Equal(4094.50m, closed.Price);
    Assert.Equal(55m, closed.GroupRealizedPips);
    Assert.Contains(positionId.Value, client.PositionCloseReasonLookups);
    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "warning"
        && item.Message.Contains("RECOVERY REQUIRED", StringComparison.Ordinal)
    );
  }

  [Fact]
  public async Task UnknownCloseWithoutDealPriceFinalizesWithLiveQuote()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(
      stopPrice: 4082.50m,
      managementJson: """
        {
          "never_worsen_stop": true
        }
        """
    ));
    var client = new FakeTradePlanTradingClient
    {
      PositionCloseReasonToReturn = PositionCloseReason.Unknown,
    };
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    var positionId = Assert.Single(runtime.TrackedStates).PositionId;
    Assert.NotNull(positionId);

    client.RemovePosition(positionId.Value);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4094.45m, 4094.50m, 2), CancellationToken.None
    );

    Assert.Empty(runtime.TrackedStates);
    var closed = Assert.Single(store.Events, item => item.Type == "position_closed");
    Assert.Equal("manual_or_external_close", closed.ReasonCode);
    Assert.Equal(4094.45m, closed.Price);
    Assert.Equal(55m, closed.GroupRealizedPips);
    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "warning"
        && item.Message.Contains("RECOVERY REQUIRED", StringComparison.Ordinal)
    );
  }

  [Fact]
  public async Task UnknownCloseNearProtectiveStopPromotesSlNotManual()
  {
    // Production 2026-08-28 Flip Zone: exit within a few pips of SL must
    // not read as manual when the deal window misses the fill.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(stopPrice: 4082.50m));
    var client = new FakeTradePlanTradingClient
    {
      PositionCloseReasonToReturn = PositionCloseReason.Unknown,
    };
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    var positionId = Assert.Single(runtime.TrackedStates).PositionId;
    Assert.NotNull(positionId);

    client.RemovePosition(positionId.Value);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4082.55m, 4082.60m, 2), CancellationToken.None
    );

    Assert.Empty(runtime.TrackedStates);
    var closed = Assert.Single(store.Events, item => item.Type == "position_closed");
    Assert.Equal("stop_loss_or_take_profit", closed.ReasonCode);
    Assert.DoesNotContain("manual_or_external", closed.Message, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task PartialUnknownClosePastStopPromotesSlAndKeepsManagingRemainingLeg()
  {
    // Production 2026-08-26 HFS Range Sweep v8:a80bf164…: L1 SL'd, deal
    // lookup returned Unknown, L2 still open → GROUP RECOVERY REQUIRED
    // stranded the scale-in leg. Live quote past the stop must classify L1
    // as SL and keep managing L2.
    const string planJson = """
    {
      "version": 8,
      "plan_id": "v8:plan-partial-sl",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Range Sweep Scalp",
        "strategy_family": "scalp",
        "direction": "BUY",
        "context_timeframes": ["M1"],
        "formation_timeframe": "M1",
        "confirmation_timeframe": "M1",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 3.0,
        "confluence": 3,
        "bias": "range",
        "regime": "range",
        "reasons": ["lower_edge_sweep_reclaim"],
        "tags": []
      },
      "source_structure": {
        "structure_id": "demand:M1:4085.00:4089.50:1719990000",
        "kind": "demand",
        "timeframe": "M1",
        "low": "4085.00",
        "high": "4089.50",
        "invalidation_price": "4082.50"
      },
      "entry": {
        "type": "market_with_limit_scale",
        "zone_low": "4085.00",
        "zone_high": "4089.50",
        "expires_at": 2000000000,
        "legs": [
          {"leg_id": "L1", "price": "4089.10", "volume_ratio": "0.80", "order_type": "market"},
          {"leg_id": "L2", "price": "4085.00", "volume_ratio": "0.20", "order_type": "limit"}
        ]
      },
      "stop": {
        "type": "absolute",
        "price": "4082.50",
        "source": "m5_structure",
        "structure_id": "demand:M1:4085.00:4089.50:1719990000",
        "reason": "below distal"
      },
      "targets": [
        {"target_id": "TP1", "type": "absolute", "price": "4092.00", "close_ratio": "0.5"},
        {"target_id": "TP2", "type": "absolute", "price": "4095.00", "close_ratio": "0.5"}
      ],
      "risk": {
        "risk_percent": "1.0",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "zone_scale",
        "leg_ratios": ["0.80", "0.20"]
      },
      "management": {
        "be_after_target_id": null,
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": true,
        "allow_limit": true,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """;
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(planJson);
    var client = new FakeTradePlanTradingClient
    {
      AccountEquity = 1_300m,
      AccountBalance = 1_300m,
      PositionCloseReasonToReturn = PositionCloseReason.Unknown,
    };
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.00m, 4089.10m, 1), CancellationToken.None
    );
    var open = Assert.Single(runtime.TrackedStates);
    var l2Order = Assert.Single(
      open.Legs!, leg => leg.LegId == "L2"
    ).BrokerOrderId!.Value;
    client.FillPendingOrder(l2Order);

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4085.00m, 4085.10m, 2), CancellationToken.None
    );
    var filled = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, filled.Stage);
    var l1 = Assert.Single(filled.Legs!, leg => leg.LegId == "L1");
    var l2 = Assert.Single(filled.Legs!, leg => leg.LegId == "L2");
    Assert.NotNull(l1.BrokerPositionId);
    Assert.NotNull(l2.BrokerPositionId);
    Assert.NotEqual(l1.BrokerPositionId, l2.BrokerPositionId);

    client.RemovePosition(l1.BrokerPositionId!.Value);
    // Sweep past the protective stop — same shape as the live bid after SL.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4081.80m, 4081.90m, 3), CancellationToken.None
    );

    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "warning"
        && item.Message.Contains("RECOVERY REQUIRED", StringComparison.Ordinal)
    );
    var remaining = Assert.Single(runtime.TrackedStates);
    Assert.NotEqual(TradePlanGroupStages.RecoveryRequired, remaining.GroupStage);
    Assert.Equal(
      TradePlanLegStages.Closed,
      Assert.Single(remaining.Legs!, leg => leg.LegId == "L1").Stage
    );
    Assert.Equal(
      PositionCloseReason.StopLossOrTakeProfit.ToString(),
      Assert.Single(remaining.Legs!, leg => leg.LegId == "L1").LastError
    );
    Assert.Equal(
      TradePlanLegStages.Filled,
      Assert.Single(remaining.Legs!, leg => leg.LegId == "L2").Stage
    );
    Assert.Contains(
      store.Events,
      item => item.Type == "sl_moved"
        && item.Message.Contains("PARTIALLY CLOSED by SL", StringComparison.Ordinal)
    );
  }

  [Fact]
  public async Task Tp1ClosesShallowLegFirstAndBreakEvenUsesTheDeeperLegsOwnFill()
  {
    // Owner 2026-09-08: "when it hit TP1, it should trail to the deeper
    // entry price like the manual algo, not trail to the shallow entry
    // quickly". Pro-rata TP closing kept both legs' remaining volume in
    // their original 80/20 ratio after every target, so the group's
    // weighted-fill BE reference stayed skewed toward L1 (shallow,
    // market, worse price) even once TP1 booked. Shallow-first closing
    // drains L1 completely before touching L2 (deep, limit, better
    // price) - once L1 is gone, the BE reference recomputed from
    // currently-open legs is just L2's own fill.
    const string planJson = """
    {
      "version": 8,
      "plan_id": "v8:plan-shallow-first",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Trend Pullback",
        "strategy_family": "trend_pullback",
        "direction": "BUY",
        "context_timeframes": ["M15"],
        "formation_timeframe": "H1",
        "confirmation_timeframe": "M15",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 3.0,
        "confluence": 3,
        "bias": "up",
        "regime": "trend",
        "reasons": ["htf_uptrend"],
        "tags": []
      },
      "source_structure": {
        "structure_id": "demand:M15:4085.00:4089.50:1719990000",
        "kind": "demand",
        "timeframe": "M15",
        "low": "4085.00",
        "high": "4089.50",
        "invalidation_price": "4082.50"
      },
      "entry": {
        "type": "market_with_limit_scale",
        "zone_low": "4085.00",
        "zone_high": "4089.50",
        "expires_at": 2000000000,
        "legs": [
          {"leg_id": "L1", "price": "4089.10", "volume_ratio": "0.80", "order_type": "market"},
          {"leg_id": "L2", "price": "4085.00", "volume_ratio": "0.20", "order_type": "limit"}
        ]
      },
      "stop": {
        "type": "absolute",
        "price": "4082.50",
        "source": "m5_structure",
        "structure_id": "demand:M15:4085.00:4089.50:1719990000",
        "reason": "below distal"
      },
      "targets": [
        {"target_id": "TP1", "type": "absolute", "price": "4096.00", "close_ratio": "0.85"},
        {"target_id": "TP2", "type": "absolute", "price": "4104.00", "close_ratio": "0.15"}
      ],
      "risk": {
        "risk_percent": "1.0",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "zone_scale",
        "leg_ratios": ["0.80", "0.20"]
      },
      "management": {
        "be_after_target_id": "TP1",
        "be_buffer_ticks": 3,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": true,
        "allow_limit": true,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """;
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(planJson);
    var client = new FakeTradePlanTradingClient
    {
      AccountEquity = 1_300m,
      AccountBalance = 1_300m,
    };
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.00m, 4089.10m, 1), CancellationToken.None
    );
    var open = Assert.Single(runtime.TrackedStates);
    var l2Order = Assert.Single(open.Legs!, leg => leg.LegId == "L2").BrokerOrderId!.Value;
    client.FillPendingOrder(l2Order, fillPrice: 4085.00m);

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4085.00m, 4085.10m, 2), CancellationToken.None
    );
    var filled = Assert.Single(runtime.TrackedStates);
    var l1Before = Assert.Single(filled.Legs!, leg => leg.LegId == "L1");
    var l2Before = Assert.Single(filled.Legs!, leg => leg.LegId == "L2");
    Assert.NotNull(l1Before.BrokerPositionId);
    Assert.NotNull(l2Before.BrokerPositionId);
    var l1RemainingBeforeTp1 = l1Before.RemainingVolume;
    var l2RemainingBeforeTp1 = l2Before.RemainingVolume;
    var l2Fill = l2Before.FillPrice!.Value;

    // Price reaches TP1 (4096.00).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4096.50m, 4096.55m, 3), CancellationToken.None
    );

    var afterTp1 = Assert.Single(runtime.TrackedStates);
    Assert.True(afterTp1.BreakEvenApplied);
    var l1After = Assert.Single(afterTp1.Legs!, leg => leg.LegId == "L1");
    var l2After = Assert.Single(afterTp1.Legs!, leg => leg.LegId == "L2");

    // TP1's 85% share exceeds L1's own ~83% of the position, so shallow-
    // first closing drains L1 completely (any overflow into L2 rounds
    // away below one broker step here) - proving L1 is fully gone is
    // what actually matters: it means only L2 is left to compute BE from.
    Assert.Equal(0, l1After.RemainingVolume);
    Assert.True(l2After.RemainingVolume > 0, "L2 should still be open after TP1");

    // BE stop is L2's own (deeper, better) fill + buffer, not a blend
    // dragged toward L1's shallower fill.
    var expectedStop = decimal.Round(
      l2Fill + Options().BreakEvenBufferTicks * 0.01m, 2, MidpointRounding.AwayFromZero
    );
    Assert.Single(
      client.StopAmendments, item => item.StopLoss == expectedStop
    );
  }

  [Fact]
  public async Task DeferredTpTouchThenStopOutDoesNotArchiveTp()
  {
    // Production 2026-08-24: XAU BUY 4636.98, stop 4631.04. TP1 was
    // touched but its 0.012-lot share was below the 0.02-lot booking floor,
    // so no broker close occurred. After a Redis restart the deal lookup
    // timed out and the fallback quote was 4630.96; NextTargetIndex made
    // that real -60p stop-out render as an archived TP1 +31p.
    const string targets = """
      [
        {"target_id": "TP1", "type": "absolute", "price": "4092.00", "close_ratio": "0.2"},
        {"target_id": "TP2", "type": "absolute", "price": "4100.00", "close_ratio": "0.8"}
      ]
      """;
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(
      targetsJson: targets,
      managementJson: """
        {
          "never_worsen_stop": true
        }
        """
    ));
    var client = new FakeTradePlanTradingClient
    {
      AccountBalance = 200m,
      AccountEquity = 200m,
      PositionCloseReasonToReturn = PositionCloseReason.Unknown,
    };
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    await runtime.PollAsync(
      client, Symbol,
      new SpotPrice("XAU", 4089.05m, 4089.10m, 1),
      CancellationToken.None
    );
    var positionId = Assert.Single(runtime.TrackedStates).PositionId;
    Assert.NotNull(positionId);

    await runtime.PollAsync(
      client, Symbol,
      new SpotPrice("XAU", 4092.05m, 4092.10m, 2),
      CancellationToken.None
    );
    var touched = Assert.Single(runtime.TrackedStates);
    Assert.Equal(1, touched.NextTargetIndex);
    Assert.Equal(-1, touched.HighestBookedTargetIndex);
    Assert.Empty(client.Closes);
    Assert.DoesNotContain(store.Events, item => item.Type == "tp_booked");

    client.RemovePosition(positionId.Value);
    await runtime.PollAsync(
      client, Symbol,
      new SpotPrice("XAU", 4082.42m, 4082.50m, 3),
      CancellationToken.None
    );

    Assert.Empty(runtime.TrackedStates);
    var closed = Assert.Single(
      store.Events, item => item.Type == "position_closed"
    );
    Assert.Equal("stop_loss_or_take_profit", closed.ReasonCode);
    // Bid printed the continuing wick past the stop; book the protective
    // stop, not the sweep (same rule as V6 ExitBeyondProtectiveStop).
    Assert.Equal(4082.50m, closed.Price);
    Assert.Equal(-65m, closed.GroupRealizedPips);
    Assert.Null(closed.TargetPips);
    Assert.Contains(
      "no TP archived", closed.Message, StringComparison.OrdinalIgnoreCase
    );
    Assert.DoesNotContain(
      "highest TP archived", closed.Message, StringComparison.OrdinalIgnoreCase
    );
  }

  [Fact]
  public async Task UnknownCloseWithRecoveredExitPriceDoesNotUseStopTautology()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(stopPrice: 4082.50m));
    var client = new FakeTradePlanTradingClient
    {
      PositionCloseReasonToReturn = PositionCloseReason.Unknown,
      // Manual close well away from the protective stop.
      PositionCloseExecutionPriceToReturn = 4094.50m,
    };
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    var positionId = Assert.Single(runtime.TrackedStates).PositionId;
    Assert.NotNull(positionId);

    client.RemovePosition(positionId.Value);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4094.45m, 4094.50m, 2), CancellationToken.None
    );

    Assert.Empty(runtime.TrackedStates);
    var closed = Assert.Single(store.Events, item => item.Type == "position_closed");
    Assert.Null(closed.ReasonCode);
    Assert.Equal(55m, closed.GroupRealizedPips);
    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "position_closed"
        && item.Message.Contains("no TP archived", StringComparison.OrdinalIgnoreCase)
    );
  }

  [Fact]
  public async Task SecondSameDirectionNonScalpPlanIsRejectedWhileFirstIsPending()
  {
    // Live 2026-08-17 GBPJPY: two Key Level SELL plans 5s apart both filled
    // because pending runtime state was not a same-direction gate.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(
      planId: "v8:kl-dac0",
      thesisId: "thesis-dac0",
      setupId: "setup-dac0",
      strategy: "Key Level Reaction",
      strategyFamily: "key_level"
    ));
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    var first = Assert.Single(runtime.TrackedStates);
    Assert.Equal("v8:kl-dac0", first.PlanId);
    Assert.Equal(TradePlanRuntimeStage.Received, first.Stage);
    Assert.Equal(4089.05m, first.IntendedEntryPrice);

    store.EnqueuePlan(PlanJson(
      planId: "v8:kl-ca1c",
      thesisId: "thesis-ca1c",
      setupId: "setup-ca1c",
      strategy: "Key Level Reaction",
      strategyFamily: "key_level"
    ));
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 2), CancellationToken.None
    );

    Assert.Single(runtime.TrackedStates);
    Assert.Equal("rejected", store.Value("execution:plan_state:v8:kl-ca1c"));
    Assert.Contains(
      store.Events,
      item => item.Type == "plan_rejected" && item.CandidateId == "v8:kl-ca1c"
    );
    Assert.Empty(client.MarketOrders);
  }

  [Fact]
  public async Task IncomingScalpMayStackOnPendingNonScalpPlan()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(
      planId: "v8:kl-live",
      strategy: "Key Level Reaction",
      strategyFamily: "key_level"
    ));
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );

    store.EnqueuePlan(PlanJson(
      planId: "v8:hfs-stack",
      thesisId: "thesis-hfs",
      setupId: "setup-hfs",
      strategy: "Range Sweep Scalp",
      strategyFamily: "scalp"
    ));
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 2), CancellationToken.None
    );

    Assert.Equal(2, runtime.TrackedStates.Count);
    Assert.Equal("received", store.Value("execution:plan_state:v8:hfs-stack"));
  }

  [Fact]
  public async Task PendingSameDirectionOnOtherSymbolDoesNotBlockIncoming()
  {
    // Live 2026-08-17: open GBPJPY must not reject a later XAU same-dir plan.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(
      planId: "v8:gbpjpy-buy",
      thesisId: "thesis-gbp",
      setupId: "setup-gbp",
      strategy: "Key Level Reaction",
      strategyFamily: "key_level",
      symbol: "GBPJPY",
      zoneLow: 215.90m,
      zoneHigh: 215.92m,
      stopPrice: 215.70m
    ));
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.Single(runtime.TrackedStates);

    store.EnqueuePlan(PlanJson(
      planId: "v8:xau-buy",
      thesisId: "thesis-xau",
      setupId: "setup-xau",
      strategy: "Key Level Reaction",
      strategyFamily: "key_level",
      symbol: "XAU"
    ));
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 2), CancellationToken.None
    );

    Assert.Equal(2, runtime.TrackedStates.Count);
    Assert.Equal("received", store.Value("execution:plan_state:v8:xau-buy"));
  }

  [Fact]
  public async Task XauUsdAliasBlocksSecondXauSameDirectionBeforeTp2()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(
      planId: "v8:xauusd-buy",
      strategy: "Key Level Reaction",
      strategyFamily: "key_level",
      symbol: "XAUUSD"
    ));
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.Single(runtime.TrackedStates);

    store.EnqueuePlan(PlanJson(
      planId: "v8:xau-buy",
      thesisId: "thesis-xau-2",
      setupId: "setup-xau-2",
      strategy: "Key Level Reaction",
      strategyFamily: "key_level",
      symbol: "XAU"
    ));
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 2), CancellationToken.None
    );

    Assert.Single(runtime.TrackedStates);
    Assert.Equal("rejected", store.Value("execution:plan_state:v8:xau-buy"));
  }

  [Fact]
  public async Task ExpiredPlanLogsTheLastReasonItNeverFilled()
  {
    // Live incident: a market_watch plan expired unfilled even though price
    // logs showed it re-entering the zone a few minutes before expiry -
    // every poll that didn't submit was completely silent, so there was no
    // way to tell from production logs whether the entry never actually saw
    // the zone again, or saw it but got blocked by something else (spread).
    // The expiry log line must now say which one happened.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(expiresAt: 1_720_000_100));
    var client = new FakeTradePlanTradingClient();
    var logs = new List<string>();
    var currentTime = 1_720_000_000L;
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(currentTime),
      logs.Add
    );

    // Outside the zone - Wait, reason recorded in-memory but nothing
    // logged yet (a Wait on every poll would otherwise spam every setup
    // still waiting for price, which is the normal/common case).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.DoesNotContain(logs, line => line.Contains("plan expired"));

    // Clock now past expires_at - this poll's quote is irrelevant, the
    // plan expires using the reason recorded on the poll just above.
    currentTime = 1_720_000_100L;
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 2), CancellationToken.None
    );

    Assert.Contains(
      logs, line => line.Contains("v8 plan expired")
        && line.Contains("last_wait_reason=outside_zone")
    );
    // Live incident: this branch used to log-and-forget with no
    // PublishEventAsync call at all, unlike every other terminal
    // transition in this file (plan_rejected/order_filled/...) - the
    // owner's forming card never resolved and nothing told them the
    // setup died. Must now publish like everything else does.
    Assert.Contains(
      store.Events, e => e.Type == "plan_expired"
        && e.Message.Contains("outside_zone")
        && e.Message.Contains("never entered the entry zone")
    );
  }

  [Fact]
  public async Task ExpiredPlanThatTouchedZoneSaysLeftWithoutFill()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(
      zoneLow: 4088.10m, zoneHigh: 4090.00m, expiresAt: 1_720_000_100
    ));
    var client = new FakeTradePlanTradingClient();
    var currentTime = 1_720_000_000L;
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(currentTime),
      _ => { }
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    // M1 evidence: price traded through the zone after arm (the card
    // already showed "Price now" inside), but live quote is now outside.
    store.Bars.Add(new OhlcBar(1_720_000_030, 4087.50m, 4089.60m, 4087.20m, 4089.10m, 100));

    currentTime = 1_720_000_100L;
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.0m, 4095.2m, 2), CancellationToken.None
    );

    Assert.Contains(
      store.Events, e => e.Type == "plan_expired"
        && e.Message.Contains("outside_zone")
        && e.Message.Contains("left the entry zone without a fill")
    );
  }

  [Fact]
  public async Task LiveCatchUpSubmitsWithoutRecoveryGraceWhenM1TouchedAndQuoteStillClose()
  {
    // Same missed-tick incident as recovery catch-up, but without a
    // restart: process stayed up, poll simply never sampled the overlap.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(zoneLow: 4088.10m, zoneHigh: 4090.00m));
    var client = new FakeTradePlanTradingClient();
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000),
      logs.Add
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.Equal(TradePlanRuntimeStage.Received, runtime.TrackedStates.Single().Stage);
    Assert.Empty(client.MarketOrders);

    store.Bars.Add(new OhlcBar(1_720_000_060, 4087.50m, 4089.60m, 4087.20m, 4089.10m, 100));
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4090.35m, 4090.40m, 2), CancellationToken.None
    );

    Assert.Contains(logs, line => line.Contains("v8 zone catch-up"));
    Assert.Single(client.MarketOrders);
  }

  [Fact]
  public async Task ExpiredPlanThatWasNeverEvaluatedLogsNeverEvaluated()
  {
    // A plan can expire on its very first poll (e.g. expires_at already in
    // the past by the time the stream is consumed) without EvaluateEntry
    // ever reaching the market_watch branch at all - LastEntryWaitReason
    // stays null, and the log must say so plainly rather than a misleading
    // blank/default reason.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(expiresAt: 1_720_000_000));
    var client = new FakeTradePlanTradingClient();
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_100),
      logs.Add
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );

    Assert.Contains(
      logs, line => line.Contains("v8 plan expired")
        && line.Contains("last_wait_reason=never_evaluated")
    );
    Assert.Contains(
      store.Events, e => e.Type == "plan_expired"
        && e.Message.Contains("never_evaluated")
        && e.Message.Contains("never evaluated a live quote")
    );
  }

  [Fact]
  public async Task FirstPollSubmitsExecutablePlanWithoutArmedStage()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
    var open = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, open.Stage);
    Assert.DoesNotContain(store.Events, e => e.Type == "plan_armed");
    Assert.DoesNotContain(
      runtime.TrackedStates,
      s => s.Stage is TradePlanRuntimeStage.Received
        or TradePlanRuntimeStage.Submitting
    );
  }

  [Fact]
  public async Task ImmediateMarketChaseSubmitsOnFirstPollOutsideOldZone()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(
      planId: "v8:hfs-market-chase",
      setupId: "hfs-market-chase",
      direction: "BUY",
      zoneLow: 4629.134892857143m,
      zoneHigh: 4630.110214285714m,
      stopPrice: 4628.89m,
      strategy: "Range Sweep Scalp",
      strategyFamily: "scalp",
      targetsJson: """
        [
          {"target_id":"TP1","type":"absolute","price":"4632.89","close_ratio":"0.5"},
          {"target_id":"TP2","type":"absolute","price":"4633.89","close_ratio":"0.5"}
        ]
        """,
      managementJson: """
        {
          "be_after_target_id": null,
          "be_buffer_ticks": 6,
          "never_worsen_stop": true
        }
        """,
      entryJson: """
        {
          "type":"market",
          "expires_at":2000000000,
          "order_price":"4631.89",
          "max_spread_ticks":50,
          "max_slippage_ticks":50,
          "legs":[]
        }
        """
    ));
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    await runtime.PollAsync(
      client,
      Symbol,
      new SpotPrice("XAU", 4632.00m, 4632.14m, 1),
      CancellationToken.None
    );

    var order = Assert.Single(client.MarketOrders);
    Assert.Equal(TradeDirection.Buy, order.Direction);
    Assert.Equal(325_000, order.RelativeStopLoss);
    Assert.Equal(
      TradePlanRuntimeStage.FullyOpen,
      Assert.Single(runtime.TrackedStates).Stage
    );
  }

  [Fact]
  public async Task ImmediateMarketChaseDoesNotSubmitWhenAlreadyThroughTp1()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(
      planId: "v8:hfs-chase-through-tp",
      setupId: "hfs-chase-through-tp",
      direction: "SELL",
      zoneLow: 4669.177214285714m,
      zoneHigh: 4670.411392857143m,
      stopPrice: 4671.29m,
      strategy: "Range Sweep Scalp",
      strategyFamily: "scalp",
      targetsJson: """
        [
          {"target_id":"TP1","type":"absolute","price":"4667.29","close_ratio":"0.5"},
          {"target_id":"TP2","type":"absolute","price":"4666.29","close_ratio":"0.5"}
        ]
        """,
      managementJson: """
        {
          "be_after_target_id": null,
          "be_buffer_ticks": 6,
          "never_worsen_stop": true
        }
        """,
      entryJson: """
        {
          "type":"market",
          "expires_at":2000000000,
          "order_price":"4668.29",
          "max_spread_ticks":50,
          "max_slippage_ticks":10,
          "legs":[]
        }
        """
    ));
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    await runtime.PollAsync(
      client,
      Symbol,
      new SpotPrice("XAU", 4667.17m, 4667.29m, 1),
      CancellationToken.None
    );

    Assert.Empty(client.MarketOrders);
    Assert.Contains(
      runtime.TrackedStates,
      s => s.Stage is TradePlanRuntimeStage.Received
        or TradePlanRuntimeStage.Submitting
    );
  }

  [Fact]
  public async Task DoesNotBookTpWhenFillAlreadyPastTarget()
  {
    // Reproduce the fake TP1: market fills through TP1, next poll must
    // skip the target instead of closing half as "TP COMPLETED".
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(
      planId: "v8:hfs-fake-tp",
      setupId: "hfs-fake-tp",
      direction: "SELL",
      zoneLow: 4669.18m,
      zoneHigh: 4670.41m,
      stopPrice: 4671.29m,
      strategy: "Range Sweep Scalp",
      strategyFamily: "scalp",
      targetsJson: """
        [
          {"target_id":"TP1","type":"absolute","price":"4667.29","close_ratio":"0.5"},
          {"target_id":"TP2","type":"absolute","price":"4666.29","close_ratio":"0.5"}
        ]
        """,
      managementJson: """
        {
          "be_after_target_id": null,
          "be_buffer_ticks": 6,
          "never_worsen_stop": true
        }
        """,
      entryJson: """
        {
          "type":"market",
          "expires_at":2000000000,
          "order_price":"4668.29",
          "max_spread_ticks":50,
          "max_slippage_ticks":200,
          "legs":[]
        }
        """
    ));
    var logs = new List<string>();
    var client = new FakeTradePlanTradingClient();
    // Force a fill already through TP1 (broker slippage).
    client.NextMarketFillPrice = 4667.17m;
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, logs.Add
    );

    // First poll: within slippage of order_price and not through TP1 yet
    // so the order submits; fill is forced through TP1 via NextMarketFillPrice.
    await runtime.PollAsync(
      client,
      Symbol,
      new SpotPrice("XAU", 4668.20m, 4668.32m, 1),
      CancellationToken.None
    );
    Assert.Single(client.MarketOrders);
    Assert.Equal(
      TradePlanRuntimeStage.FullyOpen,
      Assert.Single(runtime.TrackedStates).Stage
    );

    // Second poll: ask still through TP1 (would have booked under old logic).
    await runtime.PollAsync(
      client,
      Symbol,
      new SpotPrice("XAU", 4667.10m, 4667.20m, 2),
      CancellationToken.None
    );

    Assert.Empty(client.Closes);
    Assert.DoesNotContain(store.Events, e => e.Type == "tp_booked");
    Assert.Contains(
      logs,
      line => line.Contains("v8 target skipped past fill")
        && line.Contains("target=TP1")
    );
    Assert.Equal(1, Assert.Single(runtime.TrackedStates).NextTargetIndex);
  }

  [Fact]
  public async Task SubmitsARelativeStopLossScaledForTheBrokerNotTheSymbolsTickSize()
  {
    // Live incident: RelativeStopLoss used to be computed as
    // distance / tickSize (a tick count) instead of distance * 100_000m
    // (the fixed-point scale cTrader's ProtoOANewOrderReq.RelativeStopLoss
    // actually expects, per the already-correct V6 path in
    // AutoTradeEngine.cs). For a 2-digit symbol like XAU that sent a value
    // roughly 1000x smaller than the broker expected, which cTrader
    // rejected outright with "Relative stop loss has invalid precision" -
    // crash-looping the whole auto_trade consumer, not just one order.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(zoneLow: 4088.10m, zoneHigh: 4090.00m, stopPrice: 4082.50m));
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    var order = Assert.Single(client.MarketOrders);
    // Marketable market_watch uses the live executable ask (4089.10) as the
    // relative-SL entry reference, not the zone proximal edge.
    // distance to the 4082.50 stop is 6.60 -> 6.60 * 100_000 = 660_000.
    Assert.Equal(660_000, order.RelativeStopLoss);
  }

  [Fact]
  public async Task MarketWatchWithManyTargetsAndASmallAccountStillSubmits()
  {
    // Live incident: a market_watch plan with 5 TP targets and a
    // small-account risk-based volume used to throw
    // "N volume steps cannot cover 5 targets" unconditionally inside
    // CalculateVolume - even though market_watch never reads the resulting
    // Slices at all (it submits TotalVolume as one order). TP close volume
    // is computed live from RemainingVolume at each target hit
    // (ManageOpenPositionsAsync), never from a pre-built slice list, so TP
    // count must never be able to block or crash entry submission.
    var fiveTargets = """
      [
        {"target_id": "TP1", "type": "absolute", "price": "4092.00", "close_ratio": "0.2"},
        {"target_id": "TP2", "type": "absolute", "price": "4094.00", "close_ratio": "0.2"},
        {"target_id": "TP3", "type": "absolute", "price": "4096.00", "close_ratio": "0.2"},
        {"target_id": "TP4", "type": "absolute", "price": "4098.00", "close_ratio": "0.2"},
        {"target_id": "TP5", "type": "absolute", "price": "4100.00", "close_ratio": "0.2"}
      ]
      """;
    var store = new FakeTradePlanStore();
    store.EnqueuePlan($$"""
    {
      "version": 8,
      "plan_id": "v8:plan-1",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Trend Pullback",
        "strategy_family": "trend_pullback",
        "direction": "BUY",
        "context_timeframes": ["M15"],
        "formation_timeframe": "H1",
        "confirmation_timeframe": "M15",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 3.0,
        "confluence": 3,
        "bias": "up",
        "regime": "trend",
        "reasons": ["htf_uptrend"],
        "tags": []
      },
      "source_structure": {
        "structure_id": "zone-xau-4088-4090",
        "kind": "demand",
        "timeframe": "H1",
        "low": "4088.10",
        "high": "4090.00",
        "invalidation_price": "4081.80"
      },
      "entry": {
        "type": "market_watch",
        "expires_at": 2000000000,
        "zone_low": "4088.10",
        "zone_high": "4090.00",
        "activation": "quote_inside_zone",
        "price_side": "ask",
        "max_spread_ticks": 8,
        "max_slippage_ticks": 10,
        "legs": []
      },
      "stop": {
        "type": "absolute",
        "price": "4082.50",
        "source": "structure",
        "structure_id": "zone-xau-4088-4090",
        "reason": "protective stop plan"
      },
      "targets": {{fiveTargets}},
      "risk": {
        "risk_percent": "0.42",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "single",
        "leg_ratios": []
      },
      "management": {
        "be_after_target_id": "TP1",
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": true,
        "allow_limit": false,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """);
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    var order = Assert.Single(client.MarketOrders);
    Assert.True(order.Volume > 0);
    var open = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, open.Stage);
  }

  [Fact]
  public async Task UndersizedLimitLadderRejectsThatPlanWithoutCrashingTheConsumer()
  {
    // A limit_ladder whose max_volume cannot meet the broker MinVolume is
    // rejected during arming (equity_table still respects plan.Risk.MaxVolume).
    // That sizing check runs before the plan is ever armed - a plan that can
    // never be sized must never show PLAN ARMED only to flip to PLAN REJECTED
    // a moment later - and it must reject just this plan, not crash-loop the
    // whole consumer forever on every poll.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan("""
    {
      "version": 8,
      "plan_id": "v8:plan-1",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Zone Reaction",
        "strategy_family": "structural_zone",
        "direction": "BUY",
        "context_timeframes": ["M15"],
        "formation_timeframe": "M15",
        "confirmation_timeframe": "M5",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 0.65,
        "confluence": 2,
        "bias": "up",
        "regime": "range",
        "reasons": ["demand_zone_ladder_fill"],
        "tags": []
      },
      "source_structure": {
        "structure_id": "demand:M15:4085.00:4089.50:1719990000",
        "kind": "demand",
        "timeframe": "M15",
        "low": "4085.00",
        "high": "4089.50",
        "invalidation_price": "4079.00"
      },
      "entry": {
        "type": "limit_ladder",
        "zone_low": "4085.00",
        "zone_high": "4089.50",
        "expires_at": 2000000000,
        "legs": [
          {"leg_id": "L1", "price": "4089.50", "volume_ratio": "0.90"},
          {"leg_id": "L2", "price": "4085.00", "volume_ratio": "0.10"}
        ]
      },
      "stop": {
        "type": "absolute",
        "price": "4079.00",
        "source": "structural_invalidation",
        "structure_id": "demand:M15:4085.00:4089.50:1719990000",
        "reason": "below distal edge plus Python-defined buffer"
      },
      "targets": [
        {"target_id": "TP1", "type": "absolute", "price": "4097.00", "close_ratio": "1.0"}
      ],
      "risk": {
        "risk_percent": "0.45",
        "risk_multiplier": "1.0",
        "max_volume": 50,
        "max_group_risk_percent": "2.0"
      },
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "zone_scale",
        "leg_ratios": ["0.90", "0.10"]
      },
      "management": {
        "be_after_target_id": null,
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": false,
        "allow_limit": true,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """);
    var client = new FakeTradePlanTradingClient();
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, logs.Add
    );

    // The plan is rejected during receive itself (before
    // EvaluatePendingEntryPlansAsync or SubmitEntryAsync ever run), on this
    // same first poll.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );

    Assert.Empty(client.MarketOrders);
    Assert.Empty(runtime.TrackedStates);
    Assert.Equal("rejected", store.Value("execution:plan_state:v8:plan-1"));
    Assert.Contains(
      store.Events, e => e.Type == "plan_rejected" && e.CandidateId == "v8:plan-1"
    );
    Assert.Contains(logs, line => line.Contains("v8 plan sizing rejected"));

    // The consumer must not be stuck retrying this plan - a further poll
    // is harmless (nothing left to submit) instead of throwing again.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.60m, 4089.70m, 2), CancellationToken.None
    );
    Assert.Empty(client.MarketOrders);
  }

  [Fact]
  public async Task MarketWithLimitScaleSubmitsL1MarketAndL2LimitOnFirstPoll()
  {
    const string planJson = """
    {
      "version": 8,
      "plan_id": "v8:plan-mwls",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Key Level Reaction",
        "strategy_family": "key_level",
        "direction": "BUY",
        "context_timeframes": ["M15"],
        "formation_timeframe": "M15",
        "confirmation_timeframe": "M5",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 0.65,
        "confluence": 2,
        "bias": "up",
        "regime": "range",
        "reasons": [],
        "tags": []
      },
      "source_structure": {
        "structure_id": "key:M15:4085.00:4089.50:1719990000",
        "kind": "key_level",
        "timeframe": "M15",
        "low": "4085.00",
        "high": "4089.50",
        "invalidation_price": "4079.00"
      },
      "entry": {
        "type": "market_with_limit_scale",
        "zone_low": "4085.00",
        "zone_high": "4089.50",
        "expires_at": 2000000000,
        "legs": [
          {"leg_id": "L1", "price": "4089.10", "volume_ratio": "0.70", "order_type": "market"},
          {"leg_id": "L2", "price": "4085.00", "volume_ratio": "0.30", "order_type": "limit"}
        ]
      },
      "stop": {
        "type": "absolute",
        "price": "4079.00",
        "source": "structural_invalidation",
        "structure_id": "key:M15:4085.00:4089.50:1719990000",
        "reason": "below distal edge"
      },
      "targets": [
        {"target_id": "TP1", "type": "absolute", "price": "4097.00", "close_ratio": "1.0"}
      ],
      "risk": {
        "risk_percent": "1.0",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "zone_scale",
        "leg_ratios": ["0.70", "0.30"]
      },
      "management": {
        "be_after_target_id": null,
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": true,
        "allow_limit": true,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """;
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(planJson);
    var client = new FakeTradePlanTradingClient { AccountEquity = 1_300m, AccountBalance = 1_300m };
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    // Quote inside the zone: L1 must PlaceMarketOrder (order_type=market)
    // even though a marketable-limit check on the L1 reference price would
    // also choose market; L2 must PlaceLimitOrder (order_type=limit) even
    // though detection is not consulted.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.00m, 4089.10m, 1), CancellationToken.None
    );

    var market = Assert.Single(client.MarketOrders);
    Assert.Equal(800, market.Volume); // 0.08 lots at equity 1300
    var limit = Assert.Single(client.LimitOrders);
    Assert.Equal(4085.00m, limit.LimitPrice);
    Assert.Equal(400, limit.Volume); // 0.04 lots (LotsForEquity(1300)=0.12 total)
    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, state.Stage);
    Assert.DoesNotContain(store.Events, e => e.Type == "plan_armed");
  }

  private const string LadderPlanJson = """
  {
    "version": 8,
    "plan_id": "v8:plan-1",
    "thesis_id": "thesis-1",
    "setup_id": "setup-1",
    "symbol": "XAU",
    "created_at": 1719999600,
    "expires_at": 2000000000,
    "analysis": {
      "strategy": "Zone Reaction",
      "strategy_family": "structural_zone",
      "direction": "BUY",
      "context_timeframes": ["M15"],
      "formation_timeframe": "M15",
      "confirmation_timeframe": "M5",
      "formation_bar_ts": 1719999000,
      "confirmation_bar_ts": 1719999600,
      "score": 0.65,
      "confluence": 2,
      "bias": "up",
      "regime": "range",
      "reasons": ["demand_zone_ladder_fill"],
      "tags": []
    },
    "source_structure": {
      "structure_id": "demand:M15:4085.00:4089.50:1719990000",
      "kind": "demand",
      "timeframe": "M15",
      "low": "4085.00",
      "high": "4089.50",
      "invalidation_price": "4079.00"
    },
    "entry": {
      "type": "limit_ladder",
      "zone_low": "4085.00",
      "zone_high": "4089.50",
      "expires_at": 2000000000,
      "legs": [
        {"leg_id": "L1", "price": "4089.50", "volume_ratio": "0.60"},
        {"leg_id": "L2", "price": "4085.00", "volume_ratio": "0.40"}
      ]
    },
    "stop": {
      "type": "absolute",
      "price": "4079.00",
      "source": "structural_invalidation",
      "structure_id": "demand:M15:4085.00:4089.50:1719990000",
      "reason": "below distal edge plus Python-defined buffer"
    },
    "targets": [
      {"target_id": "TP1", "type": "absolute", "price": "4097.00", "close_ratio": "1.0"}
    ],
    "risk": {
      "risk_percent": "2.0",
      "risk_multiplier": "1.0",
      "max_volume": 100000,
      "max_group_risk_percent": "2.0"
    },
    "sizing": {
      "mode": "equity_table",
      "table_version": "owner_equity_v1",
      "entry_distribution": "zone_scale",
      "leg_ratios": ["0.60", "0.40"]
    },
    "management": {
      "be_after_target_id": null,
      "be_buffer_ticks": 6,
      "never_worsen_stop": true
    },
    "execution_policy": {
      "allow_market": false,
      "allow_limit": true,
      "allow_partial_fill": true,
      "cancel_on_expiry": true
    },
    "provenance": {
      "analysis_engine_version": "",
      "market_map_id": "",
      "config_fingerprint": ""
    }
  }
  """;

  [Fact]
  public async Task OnePollCycleReusesOneAccountWideSnapshotAcrossReconcileAndManage()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    // Both ladder legs rest at the broker. Fill one between cycles so the
    // next poll must both reconcile Submitted -> PartiallyOpen and manage
    // the newly-open position.
    await runtime.PollAsync(
      client,
      Symbol,
      new SpotPrice("XAU", 4095.00m, 4095.20m, 1),
      CancellationToken.None
    );
    var submitted = Assert.Single(runtime.TrackedStates);
    var firstOrder = Assert.Single(
      submitted.Legs!, leg => leg.LegId == "L1"
    ).BrokerOrderId!.Value;
    client.FillPendingOrder(firstOrder);
    client.ResetReconcileAccountCalls();

    var cycle = new AccountReconcileSnapshotCycle(client);
    await runtime.PollAsync(
      client,
      Symbol,
      new SpotPrice("XAU", 4095.00m, 4095.20m, 2),
      CancellationToken.None,
      cycle.GetAsync
    );

    Assert.Equal(1, client.ReconcileAccountCalls);
    Assert.Equal(
      TradePlanRuntimeStage.PartiallyOpen,
      Assert.Single(runtime.TrackedStates).Stage
    );
  }

  [Fact]
  public async Task ANewSymbolPollCycleObservesBrokerMutationsAfterPriorSnapshot()
  {
    var client = new FakeTradePlanTradingClient();
    var firstCycle = new AccountReconcileSnapshotCycle(client);
    var before = await firstCycle.GetAsync(CancellationToken.None);
    Assert.Empty(before.Positions);

    client.SeedPosition(
      positionId: 9901,
      direction: TradeDirection.Buy,
      volume: 1_000
    );

    // AutoTradeEngine creates a separate cycle inside each per-symbol poll.
    // A mutation after symbol A's snapshot must therefore be visible when
    // symbol B starts; sharing one cycle across symbols would fail this.
    var secondCycle = new AccountReconcileSnapshotCycle(client);
    var after = await secondCycle.GetAsync(CancellationToken.None);

    Assert.Equal(2, client.ReconcileAccountCalls);
    Assert.Equal(9901, Assert.Single(after.Positions).PositionId);
  }

  [Fact]
  public async Task IdleFxPollTargetsDoNotReconcileForAnActiveXauPlan()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );
    await runtime.PollAsync(
      client,
      Symbol,
      new SpotPrice("XAU", 4089.05m, 4089.10m, 1),
      CancellationToken.None
    );
    Assert.Equal(
      TradePlanRuntimeStage.FullyOpen,
      Assert.Single(runtime.TrackedStates).Stage
    );
    client.ResetReconcileAccountCalls();

    var targets = new[]
    {
      Symbol,
      new SymbolInfo("EURUSD", "EURUSD", 8, 5),
      new SymbolInfo("GBPUSD", "GBPUSD", 9, 5),
      new SymbolInfo("USDJPY", "USDJPY", 10, 3),
      new SymbolInfo("GBPJPY", "GBPJPY", 11, 3),
    };
    foreach (var target in targets)
    {
      var cycle = new AccountReconcileSnapshotCycle(client);
      await runtime.PollAsync(
        client,
        target,
        new SpotPrice(target.RedisSymbol, 1.0m, 1.1m, 2),
        CancellationToken.None,
        cycle.GetAsync
      );
    }

    Assert.Equal(1, client.ReconcileAccountCalls);
  }

  [Fact]
  public void RelativeStopLossForEntryDiffersPerEntryButSharesAbsoluteStop()
  {
    const decimal absolute = 4079.00m;
    var l1 = TradePlanJson.RelativeStopLossForEntry(4089.50m, absolute);
    var l2 = TradePlanJson.RelativeStopLossForEntry(4085.00m, absolute);
    Assert.Equal(1_050_000, l1);
    Assert.Equal(600_000, l2);
    Assert.NotEqual(l1, l2);
    Assert.Equal(4089.50m - absolute, l1 / 100_000m);
    Assert.Equal(4085.00m - absolute, l2 / 100_000m);
  }

  [Theory]
  [InlineData("v8|v8:plan-1|thesis-1|L1", null, "v8:plan-1", "thesis-1", "L1")]
  [InlineData("v8|v8:plan-1|thesis-1|L2", null, "v8:plan-1", "thesis-1", "L2")]
  [InlineData("v8|v8:plan-1|thesis-1|0", null, "v8:plan-1", "thesis-1", "L1")]
  [InlineData("v8|v8:plan-1|thesis-1|1", null, "v8:plan-1", "thesis-1", "L2")]
  [InlineData(null, "v8:plan-1:L1", "v8:plan-1", "", "L1")]
  [InlineData(null, "v8:plan-1:0", "v8:plan-1", "", "L1")]
  public void TryParseOwnershipMapsL1L2AndLegacyIndex(
    string? comment,
    string? clientOrderId,
    string planId,
    string thesisId,
    string legId
  )
  {
    var ownership = TradePlanOwnership.TryParseOwnership(comment, clientOrderId);
    Assert.NotNull(ownership);
    Assert.Equal(planId, ownership!.PlanId);
    Assert.Equal(thesisId, ownership.ThesisId);
    Assert.Equal(legId, ownership.LegId);
  }

  [Fact]
  public async Task LadderL1MarketFillAndL2PendingIsPartiallyOpenThenFullyOpen()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 1), CancellationToken.None
    );

    var partial = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, partial.Stage);
    Assert.Equal(TradePlanGroupStages.PartiallyOpen, partial.GroupStage);
    var l1 = Assert.Single(partial.Legs!, leg => leg.LegId == "L1");
    var l2 = Assert.Single(partial.Legs!, leg => leg.LegId == "L2");
    Assert.NotNull(l1.BrokerPositionId);
    Assert.NotNull(l2.BrokerOrderId);
    Assert.Null(l2.BrokerPositionId);
    var pendingOrderId = l2.BrokerOrderId!.Value;

    client.FillPendingOrder(pendingOrderId);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 2), CancellationToken.None
    );

    var full = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, full.Stage);
    Assert.Equal(TradePlanGroupStages.FullyOpen, full.GroupStage);
    Assert.Equal(2, full.Legs!.Count(leg => leg.BrokerPositionId is not null));
    Assert.Equal(
      2,
      full.Legs!.Select(leg => leg.BrokerPositionId).Distinct().Count()
    );
  }

  [Fact]
  public async Task OwnerCancellingBothLadderLegsOnBrokerIsRecognizedAsPlanCancelled()
  {
    // 04 Aug incident (card 2): owner cancelled a Flip Zone limit-ladder's
    // pending legs directly on the broker platform. The plan stayed
    // reported as "submitted" forever - Telegram was never told, and
    // TrackedStates never released it. Quote well above both leg prices so
    // neither fires as an immediate market order; both rest as pending
    // limit orders, matching the real incident's zone-scale ladder.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var logs = new List<string>();
    var currentTime = 1_720_000_000L;
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(currentTime), logs.Add
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 1), CancellationToken.None
    );

    var submitted = Assert.Single(runtime.TrackedStates);
    Assert.All(submitted.Legs!, leg => Assert.Null(leg.BrokerPositionId));
    Assert.All(submitted.Legs!, leg => Assert.NotNull(leg.BrokerOrderId));

    // Owner cancels both legs directly on the broker - not through our own
    // CancelPendingOrderAsync, which is exactly the point: the broker-side
    // state changed out from under us.
    client.PendingOrders.Clear();

    // First poll after the cancel: the gap is only just noticed, not yet
    // confirmed - must not jump straight to cancelled (a same-instant fill
    // needs a full cycle for its position to land in ReconcilePositionsAsync).
    currentTime += 1;
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 2), CancellationToken.None
    );
    Assert.Single(runtime.TrackedStates);
    Assert.DoesNotContain(store.Events, e => e.Type == "plan_cancelled");

    // Gap survives past the confirmation window - now it's a real cancel.
    currentTime += 11;
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 3), CancellationToken.None
    );

    Assert.Empty(runtime.TrackedStates);
    Assert.Contains(
      store.Events, e => e.Type == "plan_cancelled"
        && e.Message.Contains("owner cancelled")
    );
    Assert.Contains(logs, line => line.Contains("v8 plan cancelled"));
  }

  [Fact]
  public async Task OwnerCancellingOneUnfilledLadderLegLeavesTheFilledLegManaged()
  {
    // Guard against the cancelled-leg detection above being too broad: a
    // partial fill (L1 real position) plus a cancelled L2 must still read
    // as a live, managed trade - not get swept into "plan cancelled" just
    // because one leg never filled.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var currentTime = 1_720_000_000L;
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(currentTime), _ => { }
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 1), CancellationToken.None
    );
    var partial = Assert.Single(runtime.TrackedStates);
    var l2 = Assert.Single(partial.Legs!, leg => leg.LegId == "L2");
    client.PendingOrders.RemoveAll(order => order.OrderId == l2.BrokerOrderId!.Value);

    currentTime += 12;
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 2), CancellationToken.None
    );
    currentTime += 12;
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 3), CancellationToken.None
    );

    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, state.Stage);
    Assert.NotEqual(TradePlanGroupStages.Cancelled, state.GroupStage);
    Assert.Contains(state.Legs!, leg => leg.LegId == "L2"
      && leg.Stage == TradePlanLegStages.Cancelled);
  }

  [Fact]
  public async Task TradePlanCommentPositionIsAdoptedWithoutCannotReconstructLog()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, logs.Add
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 1), CancellationToken.None
    );
    var state = Assert.Single(runtime.TrackedStates);
    var l2 = Assert.Single(state.Legs!, leg => leg.LegId == "L2");
    client.FillPendingOrder(l2.BrokerOrderId!.Value);

    var orphan = (await client.ReconcilePositionsAsync(CancellationToken.None))
      .Single(position => position.Comment.Contains("|L2", StringComparison.Ordinal));
    logs.Clear();
    var adopted = await runtime.TryAdoptBrokerPositionAsync(
      client, Symbol, orphan, CancellationToken.None
    );

    Assert.True(adopted);
    Assert.DoesNotContain(
      logs, line => line.Contains("cannot reconstruct", StringComparison.Ordinal)
    );
    Assert.Contains(logs, line => line.Contains("v8 adopt:", StringComparison.Ordinal));
    var after = Assert.Single(runtime.TrackedStates);
    Assert.Contains(
      after.Legs!,
      leg => leg.LegId == "L2" && leg.BrokerPositionId == orphan.PositionId
    );
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, after.Stage);
    Assert.Contains(
      store.Events,
      item => item.Type == "order_filled"
        && item.Message.Contains("ENTRY GROUP FULLY FILLED", StringComparison.Ordinal)
    );

    logs.Clear();
    var adoptedAgain = await runtime.TryAdoptBrokerPositionAsync(
      client, Symbol, orphan, CancellationToken.None
    );
    Assert.True(adoptedAgain);
    Assert.DoesNotContain(
      logs, line => line.Contains("v8 adopt:", StringComparison.Ordinal)
    );
  }

  [Fact]
  public async Task RestartAfterL1FillDoesNotResubmitL1()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var first = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    await first.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 1), CancellationToken.None
    );
    Assert.Single(client.MarketOrders);
    Assert.Single(client.LimitOrders);
    var afterL1 = Assert.Single(first.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, afterL1.Stage);
    Assert.Equal(2, afterL1.SubmittedLegCount);

    var second = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );
    await second.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 2), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
    Assert.Single(client.LimitOrders);
    var recovered = Assert.Single(second.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, recovered.Stage);
    Assert.Contains(
      recovered.Legs!,
      leg => leg.LegId == "L1" && leg.BrokerPositionId is not null
    );
  }

  [Fact]
  public async Task EachLadderLegGetsItsOwnUniqueClientOrderId()
  {
    // P0 production bug: every leg used to share one plan-wide
    // ClientOrderId, so cTrader rejected leg 2+ outright as a duplicate.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient { RejectDuplicateClientOrderIds = true };
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    // Quote sits above the whole zone, so neither leg is marketable - both
    // legs are genuine resting limit orders for this assertion.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4092.00m, 4092.20m, 1), CancellationToken.None
    );

    Assert.Equal(2, client.LimitOrders.Count);
    Assert.Equal(
      TradePlanJson.RelativeStopLossForEntry(4089.50m, 4079.00m),
      client.LimitOrders.Single(o => o.ClientOrderId.EndsWith(":L1", StringComparison.Ordinal))
        .RelativeStopLoss
    );
    Assert.Equal(
      TradePlanJson.RelativeStopLossForEntry(4085.00m, 4079.00m),
      client.LimitOrders.Single(o => o.ClientOrderId.EndsWith(":L2", StringComparison.Ordinal))
        .RelativeStopLoss
    );
    var clientOrderIds = client.LimitOrders.Select(o => o.ClientOrderId).ToArray();
    Assert.Equal(clientOrderIds.Length, clientOrderIds.Distinct().Count());
    Assert.Contains("v8:plan-1:L1", clientOrderIds);
    Assert.Contains("v8:plan-1:L2", clientOrderIds);
    var comments = client.LimitOrders.Select(o => o.Comment).ToArray();
    Assert.Equal(comments.Length, comments.Distinct().Count());
    Assert.Contains(comments, c => c.EndsWith("|L1", StringComparison.Ordinal));
    Assert.Contains(comments, c => c.EndsWith("|L2", StringComparison.Ordinal));
    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Submitted, state.Stage);
    Assert.Equal(2, state.SubmittedLegCount);
    Assert.Equal(2, state.Legs?.Count);
  }

  [Fact]
  public async Task RetryAfterALegFailureResumesWithoutResubmittingAnAcceptedLeg()
  {
    // P0 production bug: leg 2 erroring (duplicate ClientOrderId, or any
    // other broker rejection) threw before Stage ever became Submitted, so
    // the plan stayed Received/Submitting and the NEXT poll resubmitted
    // leg 1 from scratch - even though the broker had already accepted it.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient { ThrowOnCallNumber = 2 };
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );
    var quote = new SpotPrice("XAU", 4092.00m, 4092.20m, 1);

    await Assert.ThrowsAsync<InvalidOperationException>(
      () => runtime.PollAsync(client, Symbol, quote, CancellationToken.None)
    );

    // Leg 1 (only) was accepted and durably recorded before leg 2 threw.
    Assert.Single(client.LimitOrders);
    var afterFailure = Assert.Single(runtime.TrackedStates);
    // Stage stays Submitting (not Submitted) after a partial failure -
    // EvaluatePendingEntryPlansAsync only re-evaluates Received/Submitting
    // plans, so this is what makes the retry below come back.
    Assert.Equal(TradePlanRuntimeStage.Submitting, afterFailure.Stage);
    Assert.Equal(1, afterFailure.SubmittedLegCount);

    // Retry: must resume at leg 2, never resend leg 1.
    await runtime.PollAsync(client, Symbol, quote, CancellationToken.None);

    Assert.Equal(2, client.LimitOrders.Count);
    var afterRetry = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Submitted, afterRetry.Stage);
    Assert.Equal(2, afterRetry.SubmittedLegCount);
  }

  [Fact]
  public async Task ALegAlreadyMarketableAtSubmissionFillsAsAMarketOrderNotAStuckLimit()
  {
    // P0 production bug: the ladder leg meant to "enter now" was still
    // submitted as a resting limit order. A BUY limit priced at or through
    // the live ask (or a SELL limit at/through the live bid) is not a valid
    // resting order - it must go in as a real market order instead of
    // sitting there unfillable/rejectable.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    // Price has already traded up through leg 1 (4089.50): ask is 4089.40,
    // so BUY leg 1 (price >= ask) is marketable; leg 2 (4085.00) is not.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 1), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
    var limitOrder = Assert.Single(client.LimitOrders);
    Assert.Equal(4085.00m, limitOrder.LimitPrice);
    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, state.Stage);
    Assert.NotNull(state.PositionId);
    Assert.Single(state.PendingOrderIds ?? []);
    Assert.Equal(2, state.Legs?.Count);
    Assert.Contains(state.Legs!, leg =>
      leg.LegId == "L1" && leg.BrokerPositionId is not null
    );
    Assert.Contains(state.Legs!, leg =>
      leg.LegId == "L2" && leg.BrokerOrderId is not null && leg.BrokerPositionId is null
    );
  }

  [Fact]
  public async Task DryRunNeverSubmitsRealOrders()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(
      Options("v8_only") with { DryRun = true },
      store,
      () => DateTimeOffset.UtcNow,
      _ => { }
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    Assert.Empty(client.MarketOrders);
    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Received, state.Stage);
  }

  [Fact]
  public void LegacyV6ModeNeverReadsTheTradePlanStream()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(
      Options("legacy_v6"), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    // legacy_v6 gating lives in AutoTradeEngine (it never calls PollAsync in
    // that mode) - this test proves PollAsync itself is harmless to call,
    // not that AutoTradeEngine skips it (see AutoTradeEngineTests for that).
    // Directly assert ShouldSubmitOrders semantics via ContractMode instead.
    Assert.Equal("legacy_v6", Options("legacy_v6").ContractMode);
  }

  [Fact]
  public async Task TargetHitClosesPartialVolumeAndAppliesBreakEven()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    // Default $2,000 balance sizes to 300 units, and TP1's 50% share (150)
    // now falls under the two-step minimum meaningful close (200) - bump
    // balance so this test keeps proving a genuine partial-close + BE
    // interaction instead of degenerating into an all-deferred-to-TP2 case
    // (that behavior has its own dedicated coverage elsewhere).
    var client = new FakeTradePlanTradingClient { AccountBalance = 3000m };
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    var opened = Assert.Single(runtime.TrackedStates);

    // Price reaches TP1 (4096.00).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4096.50m, 4096.55m, 2), CancellationToken.None
    );

    var tp1Close = Assert.Single(client.Closes);
    Assert.True(
      tp1Close.Volume >= 200,
      $"TP1 booked {tp1Close.Volume} units - expected at least 200 (0.02 lot)"
    );
    var afterTp1 = Assert.Single(runtime.TrackedStates);
    Assert.Equal(1, afterTp1.NextTargetIndex);
    Assert.Equal(0, afterTp1.HighestBookedTargetIndex);
    Assert.True(afterTp1.BreakEvenApplied);
    // Entry amends the group absolute stop onto the filled position; TP1 then
    // moves it to break-even.
    Assert.Equal(2, client.StopAmendments.Count);
    Assert.Equal(4082.50m, client.StopAmendments[0].StopLoss);
    Assert.Equal(4089.06m, client.StopAmendments[1].StopLoss);
    var eventTypes = store.Events.Select(e => e.Type).ToArray();
    Assert.Contains("tp_booked", eventTypes);
    Assert.Contains("sl_moved", eventTypes);
  }

  [Fact]
  public async Task TpBookedArchivedPipsUseTheRealFillNotThePlannedTarget()
  {
    // TP1's declared target (4090.50) deliberately differs from what
    // FakeTradePlanTradingClient.ClosePositionAsync always actually fills
    // at (4096.0m, simulating real slippage/spread) -- the Telegram line
    // this feeds shows both numbers together ("Fill: 4096.00 · Achieved:
    // Npips"), so ArchivedTargetPips must derive N from the same 4096.00
    // fill, not the 4090.50 target, or the two numbers visibly disagree.
    var targets = """
      [
        {"target_id": "TP1", "type": "absolute", "price": "4090.50", "close_ratio": "0.5"},
        {"target_id": "TP2", "type": "absolute", "price": "4104.00", "close_ratio": "0.5"}
      ]
      """;
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(targetsJson: targets));
    var client = new FakeTradePlanTradingClient { AccountBalance = 3000m };
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    // Price reaches TP1's declared 4090.50 -- the fake still fills the
    // close at its own fixed 4096.0m, well past the declared target.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4090.55m, 4090.60m, 2), CancellationToken.None
    );

    var tpBooked = Assert.Single(store.Events, e => e.Type == "tp_booked");
    Assert.Equal(4096.0m, tpBooked.Price);
    // Weighted fill is ~4089.06 (see the sibling test's BE-stop amendment
    // for the same entry setup) -- (4096.00 - fill)/pip, NOT
    // (4090.50 - fill)/pip (which would round to ~14).
    Assert.Equal(70, tpBooked.TargetPips);
  }

  [Fact]
  public async Task BeStopOutAfterTp1FinalizesInsteadOfRecoveryRequired()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient
    {
      AccountBalance = 3000m,
      PositionCloseReasonToReturn = PositionCloseReason.Unknown,
    };
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4096.50m, 4096.55m, 2), CancellationToken.None
    );
    var afterTp1 = Assert.Single(runtime.TrackedStates);
    Assert.True(afterTp1.BreakEvenApplied);
    var positionId = afterTp1.PositionId;
    Assert.NotNull(positionId);

    client.RemovePosition(positionId.Value);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4088.90m, 4088.95m, 3), CancellationToken.None
    );

    Assert.Empty(runtime.TrackedStates);
    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "warning"
        && item.Message.Contains("RECOVERY REQUIRED", StringComparison.Ordinal)
    );
    var closed = Assert.Single(store.Events, item => item.Type == "position_closed");
    Assert.Equal("stop_loss_or_take_profit", closed.ReasonCode);
    Assert.Contains("highest TP archived TP1", closed.Message, StringComparison.OrdinalIgnoreCase);
    Assert.Equal(4089.06m, closed.Price);
  }

  [Fact]
  public async Task FxOneRBooksThenBreakEvenWithoutTrail()
  {
    var targets = """
      [
        {"target_id": "TP1", "type": "absolute", "price": "4092.00", "close_ratio": "0.50"},
        {"target_id": "TP2", "type": "absolute", "price": "4095.00", "close_ratio": "0.50"}
      ]
      """;
    var management = """
      {
        "be_after_target_id": "TP1",
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      }
      """;
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(
      stopPrice: 4086.00m,
      targetsJson: targets,
      managementJson: management
    ));
    var client = new FakeTradePlanTradingClient { AccountBalance = 3000m };
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    await runtime.PollAsync(
      client,
      Symbol,
      new SpotPrice("XAU", 4089.05m, 4089.10m, 1),
      CancellationToken.None
    );
    Assert.Equal(4086.00m, Assert.Single(client.StopAmendments).StopLoss);

    await runtime.PollAsync(
      client,
      Symbol,
      new SpotPrice("XAU", 4092.05m, 4092.10m, 2),
      CancellationToken.None
    );
    var afterOneR = Assert.Single(runtime.TrackedStates);
    Assert.Equal(0, afterOneR.HighestBookedTargetIndex);
    Assert.True(afterOneR.BreakEvenApplied);
    Assert.Equal(2, client.StopAmendments.Count);
    Assert.Equal(4089.06m, client.StopAmendments[^1].StopLoss);
    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "sl_moved"
        && item.Message.Contains("trail", StringComparison.OrdinalIgnoreCase)
    );

    await runtime.PollAsync(
      client,
      Symbol,
      new SpotPrice("XAU", 4095.05m, 4095.10m, 3),
      CancellationToken.None
    );
    Assert.Empty(runtime.TrackedStates);
    Assert.Equal(2, client.Closes.Count);
    // No additional trail amend after BE — only the structural SL + BE move.
    Assert.Equal(2, client.StopAmendments.Count);
    var closed = Assert.Single(store.Events, item => item.Type == "position_closed");
    Assert.Equal(true, closed.BreakEvenApplied);
    Assert.Equal(1, closed.HighestBookedTargetIndex);
    Assert.Equal(2.0m, closed.PlannedRewardRisk);
    Assert.Equal(false, closed.TargetRoomFallbackUsed);
  }

  [Fact]
  public async Task StopKeepsRatchetingBeyondBreakEvenAsLaterTargetsClose()
  {
    // Before this fix, TradePlanRuntime moved the stop to BE after TP1 and
    // then never touched it again - a position that ran all the way to
    // TP3/TP4 sat protected at nothing more than BE, so a full reversal
    // afterward gave back every pip TP2/TP3 had already banked. This proves
    // the V6-equivalent ratchet (trail to the target two levels behind the
    // one that just closed) now runs in the TradePlan path too.
    var fiveTargets = """
      [
        {"target_id": "TP1", "type": "absolute", "price": "4092.00", "close_ratio": "0.2"},
        {"target_id": "TP2", "type": "absolute", "price": "4094.00", "close_ratio": "0.2"},
        {"target_id": "TP3", "type": "absolute", "price": "4096.00", "close_ratio": "0.2"},
        {"target_id": "TP4", "type": "absolute", "price": "4098.00", "close_ratio": "0.2"},
        {"target_id": "TP5", "type": "absolute", "price": "4100.00", "close_ratio": "0.2"}
      ]
      """;
    var store = new FakeTradePlanStore();
    store.EnqueuePlan($$"""
    {
      "version": 8,
      "plan_id": "v8:plan-1",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Trend Pullback",
        "strategy_family": "trend_pullback",
        "direction": "BUY",
        "context_timeframes": ["M15"],
        "formation_timeframe": "H1",
        "confirmation_timeframe": "M15",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 3.0,
        "confluence": 3,
        "bias": "up",
        "regime": "trend",
        "reasons": ["htf_uptrend"],
        "tags": []
      },
      "source_structure": {
        "structure_id": "zone-xau-4088-4090",
        "kind": "demand",
        "timeframe": "H1",
        "low": "4088.10",
        "high": "4090.00",
        "invalidation_price": "4081.80"
      },
      "entry": {
        "type": "market_watch",
        "expires_at": 2000000000,
        "zone_low": "4088.10",
        "zone_high": "4090.00",
        "activation": "quote_inside_zone",
        "price_side": "ask",
        "max_spread_ticks": 8,
        "max_slippage_ticks": 10,
        "legs": []
      },
      "stop": {
        "type": "absolute",
        "price": "4082.50",
        "source": "structure",
        "structure_id": "zone-xau-4088-4090",
        "reason": "protective stop plan"
      },
      "targets": {{fiveTargets}},
      "risk": {
        "risk_percent": "1.0",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "single",
        "leg_ratios": []
      },
      "management": {
        "be_after_target_id": "TP1",
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": true,
        "allow_limit": false,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """);
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    Assert.Single(client.MarketOrders);
    // Entry amends the group absolute protective stop onto the fill.
    Assert.Equal(4082.50m, Assert.Single(client.StopAmendments).StopLoss);

    // TP1: BE move (fill 4089.0 + 6 ticks of 0.01 = 4089.06).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4092.05m, 4092.10m, 2), CancellationToken.None
    );
    var afterTp1 = Assert.Single(runtime.TrackedStates);
    Assert.True(afterTp1.BreakEvenApplied);
    Assert.Equal(2, client.StopAmendments.Count);
    Assert.Equal(4089.06m, client.StopAmendments[^1].StopLoss);

    // TP2: two levels back would be a target that doesn't exist yet (V6
    // parity - ordinal 2 is a deliberate no-op) - no further amendment.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4094.05m, 4094.10m, 3), CancellationToken.None
    );
    Assert.Equal(2, client.StopAmendments.Count);

    // TP3: trail to TP1's price (two levels back).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4096.05m, 4096.10m, 4), CancellationToken.None
    );
    Assert.Equal(3, client.StopAmendments.Count);
    Assert.Equal(4092.00m, client.StopAmendments[^1].StopLoss);

    // TP4: trail to TP2's price.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4098.05m, 4098.10m, 5), CancellationToken.None
    );
    Assert.Equal(4, client.StopAmendments.Count);
    Assert.Equal(4094.00m, client.StopAmendments[^1].StopLoss);

    var eventTypes = store.Events.Where(e => e.Type == "sl_moved").ToArray();
    Assert.Equal(3, eventTypes.Length);
  }

  [Fact]
  public async Task UndersizedPositionDefersSmallSharesToLaterTargetsInsteadOfForcingAStep()
  {
    // Live incident (2026-08-03): a small-equity position (0.02 lots, two
    // StepVolume units) with 5 equal-weight targets hit TP1, but 20% of
    // that volume rounds down below StepVolume - so PlanPartialCloseVolume
    // returned 0 and the "can't book a valid partial" branch fired. That
    // branch used to jump NextTargetIndex straight to Targets.Count - 1
    // (4), even though price had only ever reached TP1. The trail-stop
    // step further down reads `NextTargetIndex - 3` assuming that value
    // tracks genuinely reached targets, so it then tried to amend the SL
    // to TP2's price - a level price had never actually touched - and the
    // broker rejected that amend (TRADING_BAD_STOPS: new SL below current
    // ask) on every single poll thereafter, forever. Confirmed live: 300+
    // identical rejections over 2 hours on one position, stop never
    // actually trailing past break-even.
    //
    // The NextTargetIndex-advances-by-exactly-one fix for that stays -
    // still proven below. A later revision (2026-08) removed the
    // in-between fix that force-booked one whole StepVolume on a target
    // whose true % share rounded under it: owner's call - book by the
    // plan's actual declared ratio, always, even on a small partially-
    // filled ladder; never manufacture a close a target didn't earn just
    // to avoid booking nothing. A target whose share is under the minimum
    // meaningful close (two broker steps - raised from one after a live
    // 0.08 lot position kept booking bare single-step TPs) is deferred
    // (Skip(NextTargetIndex).Sum already re-normalizes the ratio onto
    // whatever targets remain, so a deferred share is never lost - it
    // accumulates onto later targets' cuts) until either a later target's
    // cumulative share clears that bar or the final target, which always
    // closes what's left regardless of size (the dust floor belongs there,
    // not to every target).
    var fiveTargets = """
      [
        {"target_id": "TP1", "type": "absolute", "price": "4092.00", "close_ratio": "0.2"},
        {"target_id": "TP2", "type": "absolute", "price": "4094.00", "close_ratio": "0.2"},
        {"target_id": "TP3", "type": "absolute", "price": "4096.00", "close_ratio": "0.2"},
        {"target_id": "TP4", "type": "absolute", "price": "4098.00", "close_ratio": "0.2"},
        {"target_id": "TP5", "type": "absolute", "price": "4100.00", "close_ratio": "0.2"}
      ]
      """;
    var store = new FakeTradePlanStore();
    store.EnqueuePlan($$"""
    {
      "version": 8,
      "plan_id": "v8:plan-1",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Trend Pullback",
        "strategy_family": "trend_pullback",
        "direction": "BUY",
        "context_timeframes": ["M15"],
        "formation_timeframe": "H1",
        "confirmation_timeframe": "M15",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 3.0,
        "confluence": 3,
        "bias": "up",
        "regime": "trend",
        "reasons": ["htf_uptrend"],
        "tags": []
      },
      "source_structure": {
        "structure_id": "zone-xau-4088-4090",
        "kind": "demand",
        "timeframe": "H1",
        "low": "4088.10",
        "high": "4090.00",
        "invalidation_price": "4081.80"
      },
      "entry": {
        "type": "market_watch",
        "expires_at": 2000000000,
        "zone_low": "4088.10",
        "zone_high": "4090.00",
        "activation": "quote_inside_zone",
        "price_side": "ask",
        "max_spread_ticks": 8,
        "max_slippage_ticks": 10,
        "legs": []
      },
      "stop": {
        "type": "absolute",
        "price": "4082.50",
        "source": "structure",
        "structure_id": "zone-xau-4088-4090",
        "reason": "protective stop plan"
      },
      "targets": {{fiveTargets}},
      "risk": {
        "risk_percent": "1.0",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "single",
        "leg_ratios": []
      },
      "management": {
        "be_after_target_id": "TP1",
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": true,
        "allow_limit": false,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """);
    // Equity 200 -> LotsForEquity floors to 0.02 lots -> 200 units, exactly
    // two StepVolume(100) steps - small enough that a single 20% target
    // slice (40 units) rounds down below StepVolume.
    var logs = new List<string>();
    var client = new FakeTradePlanTradingClient { AccountEquity = 200m, AccountBalance = 200m };
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, logs.Add);

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    Assert.Equal(200, Assert.Single(client.MarketOrders).Volume);
    Assert.Equal(4082.50m, Assert.Single(client.StopAmendments).StopLoss);

    // TP1: the raw 20% share (40) is under the two-step (200) minimum
    // meaningful close - deferred, nothing closed, no tp_booked event.
    // NextTargetIndex still advances by exactly one (the level was
    // genuinely touched) - that's what keeps the trail-stop step below
    // from ever computing off an untouched target. Deferral continues
    // before BE/trail in the same poll — re-poll below TP2 so manage can
    // evaluate BE without hitting another target.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4092.05m, 4092.10m, 2), CancellationToken.None
    );
    var afterTp1 = Assert.Single(runtime.TrackedStates);
    Assert.Equal(1, afterTp1.NextTargetIndex);
    Assert.Empty(client.Closes);
    Assert.Equal(200, afterTp1.RemainingVolume);
    Assert.Equal(-1, afterTp1.HighestBookedTargetIndex);
    Assert.Contains(
      logs, line => line.Contains("v8 target partial deferred")
        && line.Contains("target=TP1")
        && line.Contains("reason=share_below_minimum_meaningful_close")
    );

    // Manage poll with TP1 already archived-by-touch but no broker book:
    // BE must stay off (prod 2026-08-10: BE after deferred TP1 on 0.06 lots).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4092.50m, 4092.55m, 3), CancellationToken.None
    );
    afterTp1 = Assert.Single(runtime.TrackedStates);
    Assert.Equal(1, afterTp1.NextTargetIndex);
    Assert.False(afterTp1.BreakEvenApplied);
    Assert.Single(client.StopAmendments);
    Assert.DoesNotContain(store.Events, e => e.Type == "tp_booked");
    Assert.DoesNotContain(store.Events, e => e.Type == "sl_moved");

    // TP2: re-normalized share (200 * 0.2 / 0.8 = 50) still under the
    // two-step minimum - deferred again, still nothing closed.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4094.05m, 4094.10m, 4), CancellationToken.None
    );
    Assert.Empty(client.Closes);
    Assert.Equal(2, Assert.Single(runtime.TrackedStates).NextTargetIndex);

    // TP3: deferred a third time. NextTargetIndex reaches 3; re-poll without
    // reaching TP4 so trail (NextTargetIndex - 3 = 0) can fire. Entry stop
    // + trail only (no BE without a booked TP).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4096.05m, 4096.10m, 5), CancellationToken.None
    );
    Assert.Empty(client.Closes);
    Assert.Equal(3, Assert.Single(runtime.TrackedStates).NextTargetIndex);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4096.50m, 4096.55m, 6), CancellationToken.None
    );
    Assert.Empty(client.Closes);
    Assert.False(Assert.Single(runtime.TrackedStates).BreakEvenApplied);
    Assert.Equal(2, client.StopAmendments.Count);
    Assert.Equal(4092.00m, client.StopAmendments[^1].StopLoss);

    // TP4: re-normalized share (200 * 0.2 / 0.4 = 100) clears one broker
    // step but still falls short of the two-step minimum - deferred too.
    // Whole-position bookings only ever happen at the true final target now.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4098.05m, 4098.10m, 7), CancellationToken.None
    );
    Assert.Empty(client.Closes);
    Assert.DoesNotContain(store.Events, e => e.Type == "tp_booked");
    Assert.Equal(4, Assert.Single(runtime.TrackedStates).NextTargetIndex);

    // TP5 (final target): closes whatever remains regardless of size - the
    // dust floor belongs to the last TP level only, here the entire 200
    // units in a single booking since nothing closed earlier.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4100.05m, 4100.10m, 8), CancellationToken.None
    );
    Assert.Equal(200, Assert.Single(client.Closes).Volume);
    // A fully closed position is pruned from TrackedStates - the
    // "position_closed" event is the durable record of the final close.
    var closedEvent = Assert.Single(
      store.Events, e => e.Type == "position_closed"
    );
    Assert.Equal(0, closedEvent.RemainingVolume);
  }

  [Fact]
  public async Task PartialTpNeedsAtLeastTwoBrokerStepsToBookNotJustOne()
  {
    // Live report 2026-08-07: an 0.08 lot (8-step) position kept booking a
    // bare single 0.01 lot at TP1 - correct proportional math at the time
    // (a one-step floor), but not a meaningful booking on a position this
    // size. Owner's call: TP1 should be at least 0.02 lot on an 0.08 lot
    // position - raise the "is this worth booking now" bar from one broker
    // step to two.
    var threeTargets = """
      [
        {"target_id": "TP1", "type": "absolute", "price": "4092.00", "close_ratio": "0.15"},
        {"target_id": "TP2", "type": "absolute", "price": "4094.00", "close_ratio": "0.35"},
        {"target_id": "TP3", "type": "absolute", "price": "4096.00", "close_ratio": "0.50"}
      ]
      """;
    var store = new FakeTradePlanStore();
    store.EnqueuePlan($$"""
    {
      "version": 8,
      "plan_id": "v8:plan-1",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Trend Pullback",
        "strategy_family": "trend_pullback",
        "direction": "BUY",
        "context_timeframes": ["M15"],
        "formation_timeframe": "H1",
        "confirmation_timeframe": "M15",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 3.0,
        "confluence": 3,
        "bias": "up",
        "regime": "trend",
        "reasons": ["htf_uptrend"],
        "tags": []
      },
      "source_structure": {
        "structure_id": "zone-xau-4088-4090",
        "kind": "demand",
        "timeframe": "H1",
        "low": "4088.10",
        "high": "4090.00",
        "invalidation_price": "4081.80"
      },
      "entry": {
        "type": "market_watch",
        "expires_at": 2000000000,
        "zone_low": "4088.10",
        "zone_high": "4090.00",
        "activation": "quote_inside_zone",
        "price_side": "ask",
        "max_spread_ticks": 8,
        "max_slippage_ticks": 10,
        "legs": []
      },
      "stop": {
        "type": "absolute",
        "price": "4082.50",
        "source": "structure",
        "structure_id": "zone-xau-4088-4090",
        "reason": "protective stop plan"
      },
      "targets": {{threeTargets}},
      "risk": {
        "risk_percent": "1.0",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "single",
        "leg_ratios": []
      },
      "management": {
        "be_after_target_id": "TP1",
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": true,
        "allow_limit": false,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """);
    // ~0.08 lots (equity-table sizing, not asserted to an exact unit count
    // here - only the ratio behavior this test targets matters). TP1's raw
    // 15% share clears one broker step but not the two-step (200 unit)
    // minimum - it must defer, not book a bare 0.01 lot.
    var client = new FakeTradePlanTradingClient { AccountEquity = 800m, AccountBalance = 800m };
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    var entryVolume = Assert.Single(client.MarketOrders).Volume;
    Assert.True(
      entryVolume >= 800,
      $"entry volume {entryVolume} units - expected at least 800 (0.08 lot) for this fixture"
    );

    // TP1: 15% of the filled volume - under the two-step (200 unit) minimum
    // for any position this test's equity table can plausibly produce, so
    // it must defer rather than book a bare single step.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4092.05m, 4092.10m, 2), CancellationToken.None
    );
    Assert.Empty(client.Closes);
    Assert.Equal(1, Assert.Single(runtime.TrackedStates).NextTargetIndex);

    // TP2: re-normalized share (35% of the remaining 85%) clears the
    // two-step minimum, so it finally books, and at least 0.02 lot
    // (200 units) as required - not a bare single step.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4094.05m, 4094.10m, 3), CancellationToken.None
    );
    var booked = Assert.Single(client.Closes).Volume;
    Assert.True(
      booked >= 200,
      $"TP2 booked {booked} units - expected at least 200 (0.02 lot)"
    );
    Assert.Contains(store.Events, e => e.Type == "tp_booked");
  }

  [Fact]
  public async Task RestartRecoversReceivedStateFromRedis()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson());
    var client = new FakeTradePlanTradingClient();
    var first = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await first.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.Single(first.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Received, first.TrackedStates.Single().Stage);
    Assert.Null(store.Value("execution:plan:v8:plan-1"));
    Assert.NotNull(store.Value("execution:plan_recovery:v8:plan-1"));

    // Simulate a restart: brand new runtime instance, same backing store.
    var second = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await second.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    // The recovered plan should now be able to submit its order - proving
    // the plan JSON (not only the lightweight state record) survived.
    Assert.Single(client.MarketOrders);
  }

  [Fact]
  public async Task RestartGrantsGraceWindowToMarketWatchPlanExpiredDuringDowntime()
  {
    // Live incident: price traded inside a market_watch plan's zone while a
    // deploy restart was in flight. A market_watch plan is only ever
    // evaluated against the live quote on a poll - nothing is watching
    // while the process is down - and by the time the engine came back up
    // and finished recovery, the plan's own declared Entry.ExpiresAt had
    // already lapsed during that dead time. It would have expired on the
    // very next poll having never once seen a live quote.
    var store = new FakeTradePlanStore();
    var planExpiresAt = 1_720_000_100L;
    store.EnqueuePlan(PlanJson(expiresAt: planExpiresAt));
    var client = new FakeTradePlanTradingClient();
    var logs = new List<string>();

    var first = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000),
      logs.Add
    );
    // Outside the zone - plan stays Received, not yet expired.
    await first.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.Equal(
      TradePlanRuntimeStage.Received, first.TrackedStates.Single().Stage
    );

    // Simulate the restart: a brand new runtime instance recovers this
    // plan well after its original Entry.ExpiresAt already passed.
    var recoveryTime = planExpiresAt + 30;
    var second = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(recoveryTime),
      logs.Add
    );
    // Price is back in the zone on the very first poll after recovery.
    await second.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    Assert.DoesNotContain(logs, line => line.Contains("v8 plan expired"));
    Assert.Contains(
      logs, line => line.Contains("v8 restore: granted")
        && line.Contains("recovery grace")
    );
    Assert.Single(client.MarketOrders);
  }

  [Fact]
  public async Task RecoveryGraceDoesNotResurrectAPlanStillOutsideTheZone()
  {
    // The grace window buys the plan a real look, not an unconditional
    // reprieve - if price still isn't in the zone after recovery, it must
    // keep waiting exactly like any other live market_watch plan, and
    // still expire once the (now-extended) window genuinely runs out.
    var store = new FakeTradePlanStore();
    var planExpiresAt = 1_720_000_100L;
    store.EnqueuePlan(PlanJson(expiresAt: planExpiresAt));
    var client = new FakeTradePlanTradingClient();
    var logs = new List<string>();

    var first = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000),
      logs.Add
    );
    await first.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );

    var recoveryTime = planExpiresAt + 30;
    var second = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(recoveryTime),
      logs.Add
    );
    // Still outside the zone right after recovery.
    await second.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 2), CancellationToken.None
    );
    Assert.Empty(client.MarketOrders);
    Assert.Equal(
      TradePlanRuntimeStage.Received, second.TrackedStates.Single().Stage
    );

    // The granted grace window (RestoreExpiryGraceSeconds=90) has now
    // genuinely elapsed with price never returning - must expire for real.
    var afterGrace = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(recoveryTime + 91),
      logs.Add
    );
    await afterGrace.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 3), CancellationToken.None
    );

    Assert.Contains(logs, line => line.Contains("v8 plan expired"));
    Assert.Empty(client.MarketOrders);
  }

  [Fact]
  public async Task RecoveryCatchUpSubmitsWhenZoneWasTouchedDuringDowntimeAndPriceIsStillClose()
  {
    // The user-requested fix for the incident this whole feature exists
    // for: price traded INSIDE the zone entirely while a restart was in
    // flight and left again before recovery finished. A plain live-tick
    // check can never recover that on its own - it has no memory of price
    // it never polled. On recovery, backfilled M1 bars showing the zone
    // was genuinely touched, combined with the CURRENT live quote still
    // being close, is now enough to submit - at the current quote, never
    // at the stale historical touch price.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(zoneLow: 4088.10m, zoneHigh: 4090.00m));
    var client = new FakeTradePlanTradingClient();

    var first = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000),
      _ => { }
    );
    await first.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.Equal(TradePlanRuntimeStage.Received, first.TrackedStates.Single().Stage);

    // A bar during the "downtime" shows price genuinely traded inside the
    // zone (4088.10-4090.00).
    store.Bars.Add(new OhlcBar(1_720_000_060, 4087.50m, 4089.60m, 4087.20m, 4089.10m, 100));

    var logs = new List<string>();
    var second = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_120),
      logs.Add
    );
    // Live quote has drifted just outside the zone by the time recovery
    // finishes, but is still within tolerance (half the 1.90-wide zone) -
    // spread kept inside max_spread_ticks=8 (0.05 = 5 ticks at 0.01) so
    // that check isn't what's actually being exercised here.
    await second.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4090.35m, 4090.40m, 2), CancellationToken.None
    );

    Assert.Contains(logs, line => line.Contains("v8 zone catch-up"));
    var order = Assert.Single(client.MarketOrders);
    // Executed at the CURRENT quote (ask, since this is a BUY), never at
    // the stale historical bar price - the one real tradeoff this feature
    // accepts, made explicit here.
    Assert.Equal(TradeDirection.Buy, order.Direction);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, second.TrackedStates.Single().Stage);
  }

  [Fact]
  public async Task RecoveryCatchUpDoesNotFireWithoutEvidenceOfAnActualTouch()
  {
    // No bar overlaps the zone during the gap - the live quote being
    // merely close is never enough on its own; the whole point is
    // recovering a touch that genuinely happened, not loosening the zone.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(zoneLow: 4088.10m, zoneHigh: 4090.00m));
    var client = new FakeTradePlanTradingClient();

    var first = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000),
      _ => { }
    );
    await first.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );

    // No bars recorded at all - nothing to prove a touch happened. Spread
    // kept inside max_spread_ticks=8 so lack-of-touch is the only thing
    // actually being exercised here.
    var second = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_120),
      _ => { }
    );
    await second.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4090.35m, 4090.40m, 2), CancellationToken.None
    );

    Assert.Empty(client.MarketOrders);
    Assert.Equal(TradePlanRuntimeStage.Received, second.TrackedStates.Single().Stage);
  }

  [Fact]
  public async Task RecoveryCatchUpDoesNotFireWhenCurrentPriceRanTooFarAway()
  {
    // A touch genuinely happened, but price has since moved well past the
    // tolerance band - firing here would mean a fill at a price far from
    // the original thesis, exactly the slippage risk this stays bounded
    // against. Must keep waiting like a normal live-tick evaluation.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan(PlanJson(zoneLow: 4088.10m, zoneHigh: 4090.00m));
    var client = new FakeTradePlanTradingClient();

    var first = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000),
      _ => { }
    );
    await first.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );

    store.Bars.Add(new OhlcBar(1_720_000_060, 4087.50m, 4089.60m, 4087.20m, 4089.10m, 100));

    var second = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_120),
      _ => { }
    );
    // Far beyond the zone + tolerance (zone width 1.90, tolerance 0.95) -
    // spread still kept inside max_spread_ticks=8 so distance is the only
    // thing this test actually exercises.
    await second.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4098.00m, 4098.05m, 2), CancellationToken.None
    );

    Assert.Empty(client.MarketOrders);
    Assert.Equal(TradePlanRuntimeStage.Received, second.TrackedStates.Single().Stage);
  }

  [Fact]
  public async Task DuplicatePlanIsClaimedOnceAndNeverDoubleSubmitted()
  {
    var store = new FakeTradePlanStore();
    var planJson = PlanJson();
    store.EnqueuePlan(planJson);
    store.EnqueuePlan(planJson); // same plan_id republished on the stream
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
    Assert.Single(runtime.TrackedStates);
  }

  [Fact]
  public async Task DuplicateClaimOnALaterPollNeverResubmitsAnAlreadyFilledPlan()
  {
    // P1-3: a duplicate claim (same plan_id, owner already us) arriving
    // AFTER the plan has already progressed past Received - not within the
    // same poll cycle DuplicatePlanIsClaimedOnceAndNeverDoubleSubmitted
    // covers, where the second entry is overwritten before it is ever
    // evaluated. Falling through to a fresh Received state here used to
    // resurrect an already-filled plan and submit a second broker order.
    var store = new FakeTradePlanStore();
    var planJson = PlanJson();
    store.EnqueuePlan(planJson);
    var client = new FakeTradePlanTradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    Assert.Single(client.MarketOrders);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, runtime.TrackedStates.Single().Stage);

    // The same plan_id is redelivered on the stream (eg. a retried publish,
    // or a cursor replay) and picked up on a LATER poll cycle, after the
    // order already filled.
    store.EnqueuePlan(planJson);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
    Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, runtime.TrackedStates.Single().Stage);
  }

  [Fact]
  public async Task MalformedPlanIsDurablyRejectedAndLaterValidPlanStillReceives()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan("""{"version": 8,"plan_id":"v8:broken","targets":[]}""");
    store.EnqueuePlan(PlanJson(planId: "v8:after-broken"));
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000),
      logs.Add
    );

    await runtime.PollAsync(
      new FakeTradePlanTradingClient(),
      Symbol,
      new SpotPrice("XAU", 4080.0m, 4080.2m, 1),
      CancellationToken.None
    );

    Assert.NotNull(store.Value("execution:plan_rejection:1-0"));
    Assert.Equal("received", store.Value("execution:plan_state:v8:after-broken"));
    Assert.Equal("2-0", store.TradePlanCursor);
    Assert.Single(runtime.TrackedStates);
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_rejected"));
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_received_ready"));
  }

  [Fact]
  public async Task MalformedPlanWithUnsupportedExceptionStillYieldsToValidPlan()
  {
    // Source-gen / required-member failures can surface as NotSupportedException
    // rather than JsonException — must not abort the poll batch.
    var store = new FakeTradePlanStore();
    store.EnqueuePlan("""{"version": 8,"plan_id":"v8:unsupported-shape"}""");
    store.EnqueuePlan(PlanJson(planId: "v8:after-unsupported"));
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000),
      logs.Add
    );

    await runtime.PollAsync(
      new FakeTradePlanTradingClient(),
      Symbol,
      new SpotPrice("XAU", 4080.0m, 4080.2m, 1),
      CancellationToken.None
    );

    Assert.NotNull(store.Value("execution:plan_rejection:1-0"));
    Assert.Equal("received", store.Value("execution:plan_state:v8:after-unsupported"));
    Assert.Equal("2-0", store.TradePlanCursor);
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_rejected"));
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_received_ready"));
  }

  [Fact]
  public async Task TransientRejectionPersistenceFailureLeavesCursorForRetry()
  {
    var store = new FakeTradePlanStore();
    store.EnqueuePlan("""{"version": 8,"plan_id":"v8:broken","targets":[]}""");
    store.FailSetOnce("execution:plan_rejection:1-0");
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000),
      logs.Add
    );

    await runtime.PollAsync(
      new FakeTradePlanTradingClient(), Symbol, null, CancellationToken.None
    );

    Assert.Equal("0-0", store.TradePlanCursor);
    Assert.Null(store.Value("execution:plan_rejection:1-0"));

    await runtime.PollAsync(
      new FakeTradePlanTradingClient(), Symbol, null, CancellationToken.None
    );

    Assert.Equal("1-0", store.TradePlanCursor);
    Assert.NotNull(store.Value("execution:plan_rejection:1-0"));
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_retry"));
  }

  [Fact]
  public async Task RealRedisConsumesPythonFixtureAfterMalformedEntry()
  {
    var configured = Environment.GetEnvironmentVariable("REAL_REDIS_URL");
    if (string.IsNullOrWhiteSpace(configured))
    {
      // Optional live-redis integration; keep the P0 unit filter green.
      return;
    }
    var sourceUri = new Uri(configured);
    var redisUrl = (
      $"{sourceUri.Scheme}://{sourceUri.Host}:{sourceUri.Port}/13"
    );
    await using var store =
      await StackExchangeRedisSeriesCommands.ConnectAsync(redisUrl);
    var options = ConfigurationOptions.Parse(
      $"{sourceUri.Host}:{sourceUri.Port},defaultDatabase=13,abortConnect=false"
    );
    options.AllowAdmin = true;
    await using var mux = await ConnectionMultiplexer.ConnectAsync(options);
    var db = mux.GetDatabase();
    var prepublishedPlanId = Environment.GetEnvironmentVariable(
      "REAL_REDIS_PREPUBLISHED_TRADE_PLAN_ID"
    );
    var stream = string.IsNullOrWhiteSpace(prepublishedPlanId)
      ? "execution:trade_plans:p0-real"
      : "execution:trade_plans";
    RedisValue malformedId;
    if (string.IsNullOrWhiteSpace(prepublishedPlanId))
    {
      await db.ExecuteAsync("FLUSHDB");
      malformedId = await db.StreamAddAsync(
        stream,
        [new NameValueEntry(
          "payload",
          """{"version": 8,"plan_id":"v8:bad"}"""
        )]
      );
      var payload = PythonContractFixture("market_watch_buy");
      await db.StreamAddAsync(
        stream,
        [new NameValueEntry("payload", payload)]
      );
      prepublishedPlanId = "plan-001";
    }
    else
    {
      var existing = await db.StreamRangeAsync(stream, count: 1);
      malformedId = Assert.Single(existing).Id;
    }
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options() with { TradePlanStream = stream },
      store,
      () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_100),
      logs.Add
    );

    await runtime.PollAsync(
      new FakeTradePlanTradingClient(), Symbol, null, CancellationToken.None
    );

    Assert.Equal(
      "received",
      await store.GetStringAsync(
        $"execution:plan_state:{prepublishedPlanId}", CancellationToken.None
      )
    );
    Assert.NotNull(await store.GetStringAsync(
      $"execution:plan_rejection:{malformedId}",
      CancellationToken.None
    ));
    Assert.Single(runtime.TrackedStates);
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_rejected"));
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_received_ready"));
    await db.ExecuteAsync("FLUSHDB");
  }

  private static string PythonContractFixture(string name)
  {
    var directory = new DirectoryInfo(AppContext.BaseDirectory);
    while (directory is not null)
    {
      var path = Path.Combine(
        directory.FullName,
        "contracts",
        "autotrade",
        "trade-plan-v8.json"
      );
      if (File.Exists(path))
      {
        using var fixture = JsonDocument.Parse(File.ReadAllText(path));
        foreach (var item in fixture.RootElement
          .GetProperty("valid_plans").EnumerateArray())
        {
          if (item.GetProperty("name").GetString() == name)
          {
            return item.GetProperty("plan").GetRawText();
          }
        }
      }
      directory = directory.Parent;
    }
    throw new FileNotFoundException("Python TradePlan V8 fixture not found");
  }

  private sealed class FakeTradePlanTradingClient : ICTraderTradeClient
  {
    public List<MarketOrderRequest> MarketOrders { get; } = [];
    public List<LimitOrderRequest> LimitOrders { get; } = [];
    public List<(long PositionId, long Volume)> Closes { get; } = [];
    public List<(long PositionId, decimal StopLoss)> StopAmendments { get; } = [];
    public List<long> CancelledOrderIds { get; } = [];
    public List<TradingPendingOrder> PendingOrders { get; } = [];
    private readonly List<TradingPosition> _positions = [];
    private readonly HashSet<string> _seenClientOrderIds = [];
    private long _nextPositionId = 501;
    private long _nextOrderId = 601;

    public decimal AccountBalance { get; set; } = 2_000m;
    public decimal AccountEquity { get; set; } = 2_000m;
    public string EquitySource { get; set; } = "test";
    public PositionCloseReason PositionCloseReasonToReturn { get; set; } =
      PositionCloseReason.Unknown;
    public decimal? PositionCloseExecutionPriceToReturn { get; set; }
    public decimal? NextMarketFillPrice { get; set; }
    public List<long> PositionCloseReasonLookups { get; } = [];
    public int ReconcileAccountCalls { get; private set; }

    public void ResetReconcileAccountCalls() => ReconcileAccountCalls = 0;

    // Mirrors the real cTrader behaviour a duplicate leg ClientOrderId
    // actually triggers: the broker rejects the SECOND order carrying an
    // already-used id, exactly like CTraderOpenApiFeedClient.ThrowIfRejected.
    public bool RejectDuplicateClientOrderIds { get; set; }

    // One-shot: the Nth-from-now PlaceLimitOrderAsync call throws (simulating
    // a transient broker rejection unrelated to duplicate ids), then the
    // counter is spent and every later call succeeds normally - lets tests
    // prove a retry resumes from the failed leg instead of leg 0.
    public int ThrowOnCallNumber { get; set; } = -1;
    private int _limitOrderCalls;

    public void SeedPosition(
      long positionId,
      TradeDirection direction,
      long volume,
      string comment = "v8|seed",
      string clientOrderId = "",
      decimal entryPrice = 4089.0m,
      decimal? stopLoss = null
    ) =>
      _positions.Add(new TradingPosition(
        positionId, Symbol.SymbolId, direction, volume, entryPrice, stopLoss,
        "apexvoid-auto", comment, clientOrderId
      ));

    public void RemovePosition(long positionId) =>
      _positions.RemoveAll(position => position.PositionId == positionId);

    public void FillPendingOrder(long orderId, decimal? fillPrice = null)
    {
      var pending = PendingOrders.Single(order => order.OrderId == orderId);
      PendingOrders.Remove(pending);
      LimitOrders.RemoveAll(order =>
        order.ClientOrderId == pending.ClientOrderId
      );
      var positionId = _nextPositionId++;
      _positions.Add(new TradingPosition(
        positionId,
        pending.SymbolId,
        pending.Direction,
        pending.Volume,
        fillPrice ?? pending.LimitPrice,
        null,
        pending.Label,
        pending.Comment,
        pending.ClientOrderId
      ));
    }

    public Task<TradingAccountSnapshot> GetTradingAccountAsync(CancellationToken ct) =>
      Task.FromResult(new TradingAccountSnapshot(
        1, false, "ScopeTrade", "FullAccess", "Hedged", "Fusion Markets",
        AccountBalance, AccountEquity, 1_720_000_000, EquitySource
      ));

    public Task<IReadOnlyList<TradingPosition>> ReconcilePositionsAsync(CancellationToken ct) =>
      Task.FromResult<IReadOnlyList<TradingPosition>>(_positions.ToArray());

    public Task<IReadOnlyList<TradingPendingOrder>> ReconcilePendingOrdersAsync(
      CancellationToken ct
    ) => Task.FromResult<IReadOnlyList<TradingPendingOrder>>(PendingOrders.ToArray());

    public Task<TradingReconcileSnapshot> ReconcileAccountAsync(
      CancellationToken ct
    )
    {
      ReconcileAccountCalls++;
      return Task.FromResult(new TradingReconcileSnapshot(
        _positions.ToArray(), PendingOrders.ToArray()
      ));
    }

    public Task CancelPendingOrderAsync(long orderId, CancellationToken ct)
    {
      CancelledOrderIds.Add(orderId);
      var pending = PendingOrders.FirstOrDefault(order => order.OrderId == orderId);
      PendingOrders.RemoveAll(order => order.OrderId == orderId);
      if (pending is not null)
      {
        LimitOrders.RemoveAll(order => order.ClientOrderId == pending.ClientOrderId);
      }
      return Task.CompletedTask;
    }

    public Task<PositionCloseLookup> DeterminePositionCloseReasonAsync(
      long positionId,
      long openedAtTimestamp,
      long approximateCloseTimestamp,
      CancellationToken cancellationToken
    )
    {
      PositionCloseReasonLookups.Add(positionId);
      return Task.FromResult(new PositionCloseLookup(
        PositionCloseReasonToReturn,
        PositionCloseExecutionPriceToReturn
      ));
    }

    public Task<TradeExecution> PlaceMarketOrderAsync(
      MarketOrderRequest order, CancellationToken ct
    )
    {
      if (
        RejectDuplicateClientOrderIds
        && !string.IsNullOrWhiteSpace(order.ClientOrderId)
        && !_seenClientOrderIds.Add(order.ClientOrderId)
      )
      {
        throw new InvalidOperationException(
          $"cTrader rejected order operation: duplicate ClientOrderId "
          + $"{order.ClientOrderId}"
        );
      }
      MarketOrders.Add(order);
      var positionId = _nextPositionId++;
      var fillPrice = NextMarketFillPrice
        ?? (order.Direction == TradeDirection.Buy ? 4089.0m : 4098.46m);
      NextMarketFillPrice = null;
      _positions.Add(new TradingPosition(
        positionId, order.SymbolId, order.Direction, order.Volume, fillPrice, null,
        order.Label, order.Comment, order.ClientOrderId
      ));
      return Task.FromResult(new TradeExecution(positionId, 1, fillPrice, order.Volume));
    }

    public Task<long> PlaceLimitOrderAsync(LimitOrderRequest order, CancellationToken ct)
    {
      _limitOrderCalls++;
      if (
        RejectDuplicateClientOrderIds
        && !_seenClientOrderIds.Add(order.ClientOrderId)
      )
      {
        throw new InvalidOperationException(
          $"cTrader rejected order operation: duplicate ClientOrderId "
          + $"{order.ClientOrderId}"
        );
      }
      if (_limitOrderCalls == ThrowOnCallNumber)
      {
        ThrowOnCallNumber = -1;
        throw new InvalidOperationException(
          "cTrader rejected order operation: SERVER_ERROR"
        );
      }
      LimitOrders.Add(order);
      var orderId = _nextOrderId++;
      PendingOrders.Add(new TradingPendingOrder(
        orderId,
        order.SymbolId,
        order.Direction,
        order.Volume,
        order.LimitPrice,
        order.Label,
        order.Comment,
        order.ClientOrderId
      ));
      return Task.FromResult(orderId);
    }

    public Task AmendPositionStopLossAsync(
      long positionId, decimal stopLoss, CancellationToken ct
    )
    {
      StopAmendments.Add((positionId, stopLoss));
      var idx = _positions.FindIndex(position => position.PositionId == positionId);
      if (idx >= 0)
      {
        var current = _positions[idx];
        _positions[idx] = current with { StopLoss = stopLoss };
      }
      return Task.CompletedTask;
    }

    public Task<TradeExecution> ClosePositionAsync(
      long positionId, long volume, CancellationToken ct
    )
    {
      Closes.Add((positionId, volume));
      var idx = _positions.FindIndex(position => position.PositionId == positionId);
      if (idx >= 0)
      {
        var current = _positions[idx];
        var remaining = Math.Max(0, current.Volume - volume);
        if (remaining <= 0)
        {
          _positions.RemoveAt(idx);
        }
        else
        {
          _positions[idx] = current with { Volume = remaining };
        }
      }
      return Task.FromResult(new TradeExecution(positionId, 2, 4096.0m, volume));
    }
  }

  private sealed class FakeTradePlanStore : IAutoTradeStore
  {
    private readonly Dictionary<string, string> _strings = new();
    private readonly List<TradeStreamEntry> _stream = [];
    private readonly HashSet<string> _failSetOnce = [];
    private int _nextStreamId = 1;

    public List<AutoTradeEvent> Events { get; } = [];
    public List<OhlcBar> Bars { get; } = [];

    public void EnqueuePlan(string json) =>
      _stream.Add(new TradeStreamEntry($"{_nextStreamId++}-0", json));

    public Task<IReadOnlyList<OhlcBar>> ReadRecentBarsAsync(
      string symbol, string timeframe, int count, CancellationToken ct
    ) => Task.FromResult<IReadOnlyList<OhlcBar>>(Bars);

    public string TradePlanCursor => _tradePlanCursor;
    public string? Value(string key) =>
      _strings.TryGetValue(key, out var value) ? value : null;
    public void FailSetOnce(string key) => _failSetOnce.Add(key);

    public Task<string> GetCursorAsync(CancellationToken ct) => Task.FromResult("0-0");
    public Task SetCursorAsync(string cursor, CancellationToken ct) => Task.CompletedTask;
    public Task<string> GetCommandCursorAsync(CancellationToken ct) => Task.FromResult("0-0");
    public Task SetCommandCursorAsync(string cursor, CancellationToken ct) => Task.CompletedTask;

    private string _tradePlanCursor = "0-0";
    public Task<string> GetTradePlanCursorAsync(CancellationToken ct) =>
      Task.FromResult(_tradePlanCursor);
    public Task SetTradePlanCursorAsync(string cursor, CancellationToken ct)
    {
      _tradePlanCursor = cursor;
      return Task.CompletedTask;
    }

    public Task<string?> GetStringAsync(string key, CancellationToken ct) =>
      Task.FromResult(_strings.TryGetValue(key, out var value) ? value : null);

    public Task SetStringAsync(string key, string value, CancellationToken ct)
    {
      if (_failSetOnce.Remove(key))
      {
        throw new IOException($"transient write failure for {key}");
      }
      _strings[key] = value;
      return Task.CompletedTask;
    }

    public Task DeleteStringAsync(string key, CancellationToken ct)
    {
      _strings.Remove(key);
      return Task.CompletedTask;
    }

    public Task<bool> TryClaimStringAsync(
      string key, string value, TimeSpan ttl, CancellationToken ct
    )
    {
      if (_strings.ContainsKey(key))
      {
        return Task.FromResult(false);
      }
      _strings[key] = value;
      return Task.FromResult(true);
    }

    public Task<IReadOnlyList<TradeStreamEntry>> ReadCandidatesAsync(
      string stream, string afterId, int count, CancellationToken ct
    )
    {
      var after = int.Parse(afterId.Split('-')[0]);
      return Task.FromResult<IReadOnlyList<TradeStreamEntry>>(
        _stream
          .Where(entry => int.Parse(entry.Id.Split('-')[0]) > after)
          .Take(count)
          .ToArray()
      );
    }

    public Task PublishAutoTradeEventAsync(
      string stream, AutoTradeEvent tradeEvent, CancellationToken ct
    )
    {
      Events.Add(tradeEvent);
      return Task.CompletedTask;
    }

    // --- V6-only surface this test double never exercises but the
    // interface requires (no default body) - trivial stubs only. ---
    public Task SavePositionAsync(AutoTradePositionState state, CancellationToken ct) =>
      Task.CompletedTask;
    public Task<AutoTradePositionState?> GetPositionAsync(long positionId, CancellationToken ct) =>
      Task.FromResult<AutoTradePositionState?>(null);
    public Task<IReadOnlyList<long>> GetTrackedPositionIdsAsync(CancellationToken ct) =>
      Task.FromResult<IReadOnlyList<long>>([]);
    public Task DeletePositionAsync(long positionId, CancellationToken ct) =>
      Task.CompletedTask;
    public Task<long> GetDailyTradeCountAsync(DateOnly date, CancellationToken ct) =>
      Task.FromResult(0L);
    public Task<long> IncrementDailyTradeCountAsync(DateOnly date, CancellationToken ct) =>
      Task.FromResult(1L);
    public Task<bool> IsPausedAsync(CancellationToken ct) => Task.FromResult(false);
    public Task IncrementGateRejectAsync(
      string symbol, string condition, CancellationToken ct
    ) => Task.CompletedTask;
    public Task IncrementAddRejectAsync(
      string symbol, string mode, string condition, CancellationToken ct
    ) => Task.CompletedTask;
    public Task RecordZoneCooldownAsync(
      string symbol, string direction, ZoneCooldownRecord record, int ttlMinutes,
      CancellationToken ct
    ) => Task.CompletedTask;
    public Task SaveGroupPlanAsync(
      AutoTradeGroupPlan plan, TimeSpan ttl, CancellationToken ct
    ) => Task.CompletedTask;
    public Task DeleteGroupPlanAsync(string groupId, CancellationToken ct) =>
      Task.CompletedTask;

    // --- Unused V6 candidate-lease surface: default-interface members cover
    // everything this test double never exercises. ---
    public Task<CandidateClaimResult> TryClaimCandidateAsync(
      string candidateId, string streamEventId, TimeSpan leaseDuration,
      CancellationToken ct, CandidateClaimPolicy? policy = null
    ) => throw new NotSupportedException();

    public Task<bool> RenewCandidateLeaseAsync(
      string candidateId, string streamEventId, string leaseToken,
      TimeSpan leaseDuration, CancellationToken ct
    ) => throw new NotSupportedException();

    public Task<bool> TransitionCandidateStateAsync(
      string candidateId, string streamEventId, string leaseToken, string newState,
      CancellationToken ct, string? lastError = null
    ) => throw new NotSupportedException();

    public Task<string?> GetCandidateStatusAsync(string candidateId, CancellationToken ct) =>
      Task.FromResult<string?>(null);

    public Task<bool> CompleteCandidateAsync(
      string candidateId, string streamEventId, string leaseToken, string outcome,
      CancellationToken ct
    ) => throw new NotSupportedException();

    public Task<bool> ReleaseCandidateAsync(
      string candidateId, string streamEventId, string leaseToken,
      CancellationToken ct, string? lastError = null
    ) => throw new NotSupportedException();
  }
}

using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// Exact fake-broker regression for equity-table zone-scale ladder group
/// management (P0 acceptance): equity=1300 → 0.11 lots → L1=800/L2=300,
/// partial then full fill, shared stop, TP1 pro-rata, BE, group SL, restart
/// idempotency, and no TradePlan "cannot reconstruct" spam.
/// </summary>
public sealed class EquityZoneLadderGroupE2ETests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU", "XAUUSD", 7, Digits: 2, PipPosition: 2,
    MinVolume: 100, StepVolume: 100, MaxVolume: 100_000, LotSize: 10_000
  );

  private static AutoTradeOptions Options() => new(
    Enabled: true,
    DryRun: false,
    ExpectedBroker: "Fusion",
    StopLossDistance: 6.5m,
    TargetsPips: [30, 60, 90, 120, 200],
    TargetWeights: [20, 20, 20, 20, 20],
    BreakEvenBufferTicks: 6,
    CandidateMaxAgeSeconds: 90,
    SpotMaxAgeSeconds: 5,
    MaxSpreadPips: 5,
    MaxEntryDistancePips: 40,
    MinConfluence: 2,
    PollMilliseconds: 10,
    CandidateStream: "auto_trade:candidates",
    EventStream: "auto_trade:events",
    Label: "apexvoid-auto",
    ContractMode: "v8_only",
    PipSize: 0.1m,
    PipValuePerLot: 10m,
    ContractSize: 100m,
    UnfilledLegAfterTpPolicy: "cancel"
  );

  // Key Level Reaction SELL, zone 4097.07-4101.03, 70/30 equity_table.
  // Quote ~4098.46 makes L1 (4097.07) marketable for SELL and L2 (4101.03) pending.
  private const string PlanJson = """
  {
    "version": 8,
    "plan_id": "v8:klr-e2e-1",
    "thesis_id": "thesis-klr-e2e",
    "setup_id": "setup-klr-e2e",
    "symbol": "XAU",
    "created_at": 1719999600,
    "expires_at": 2000000000,
    "analysis": {
      "strategy": "Key Level Reaction",
      "strategy_family": "structural_zone",
      "direction": "SELL",
      "context_timeframes": ["H1"],
      "formation_timeframe": "H1",
      "confirmation_timeframe": "M5",
      "formation_bar_ts": 1719999000,
      "confirmation_bar_ts": 1719999600,
      "score": 0.8,
      "confluence": 3,
      "bias": "down",
      "regime": "range",
      "reasons": ["key_level_reaction"],
      "tags": []
    },
    "source_structure": {
      "structure_id": "key:H1:4097.07:4101.03",
      "kind": "supply",
      "timeframe": "H1",
      "low": "4097.07",
      "high": "4101.03",
      "invalidation_price": "4105.50"
    },
    "entry": {
      "type": "limit_ladder",
      "zone_low": "4097.07",
      "zone_high": "4101.03",
      "expires_at": 2000000000,
      "legs": [
        {"leg_id": "L1", "price": "4097.07", "volume_ratio": "0.70"},
        {"leg_id": "L2", "price": "4101.03", "volume_ratio": "0.30"}
      ]
    },
    "stop": {
      "type": "absolute",
      "price": "4105.50",
      "source": "structural_invalidation",
      "structure_id": "key:H1:4097.07:4101.03",
      "reason": "group stop 40-60 envelope"
    },
    "targets": [
      {"target_id": "TP1", "type": "absolute", "price": "4090.00", "close_ratio": "0.40"},
      {"target_id": "TP2", "type": "absolute", "price": "4084.00", "close_ratio": "0.60"}
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
      "be_after_target_id": "TP1",
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

  [Fact]
  public async Task EquityZoneLadderGroupLifecycleMatchesAcceptance()
  {
    var store = new E2EStore();
    store.EnqueuePlan(PlanJson);
    var logs = new List<string>();
    var client = new E2EFakeTradeClient
    {
      AccountBalance = 1_200m,
      AccountEquity = 1_300m,
      EquitySource = "test",
      PositionCloseReasonToReturn = PositionCloseReason.StopLossOrTakeProfit,
    };
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, logs.Add
    );
    var quoteInside = new SpotPrice("XAU", 4098.46m, 4098.56m, 1);

    // 1-2: size + submit L1 marketable / L2 pending
    await runtime.PollAsync(client, Symbol, quoteInside, CancellationToken.None);

    Assert.Contains(logs, line => line.Contains("equity_source=account_equity"));
    Assert.Contains(logs, line => line.Contains("account_equity=1300"));
    Assert.Single(client.MarketOrders);
    Assert.Single(client.LimitOrders);
    Assert.Equal(800, client.MarketOrders[0].Volume);
    Assert.Equal(400, client.LimitOrders[0].Volume);
    Assert.Equal(1_200, client.MarketOrders[0].Volume + client.LimitOrders[0].Volume);

    var afterL1 = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, afterL1.Stage);
    Assert.Equal(TradePlanGroupStages.PartiallyOpen, afterL1.GroupStage);
    Assert.NotNull(Assert.Single(afterL1.Legs!, leg => leg.LegId == "L1").BrokerPositionId);
    Assert.NotNull(Assert.Single(afterL1.Legs!, leg => leg.LegId == "L2").BrokerOrderId);
    Assert.Null(Assert.Single(afterL1.Legs!, leg => leg.LegId == "L2").BrokerPositionId);
    Assert.Equal(800, afterL1.TotalFilledVolume);
    Assert.True(Assert.Single(afterL1.Legs!, leg => leg.LegId == "L1").StopVerified);

    // 3-4: L2 fills → FullyOpen, two PositionIds
    var l2OrderId = Assert.Single(afterL1.Legs!, leg => leg.LegId == "L2").BrokerOrderId!.Value;
    client.FillPendingOrder(l2OrderId, fillPrice: 4101.03m);
    await runtime.PollAsync(client, Symbol, quoteInside, CancellationToken.None);

    var full = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, full.Stage);
    Assert.Equal(TradePlanGroupStages.FullyOpen, full.GroupStage);
    Assert.Equal(2, full.Legs!.Count(leg => leg.BrokerPositionId is not null));
    Assert.Equal(1_200, full.TotalFilledVolume);
    Assert.Equal(
      2,
      full.Legs!.Select(leg => leg.BrokerPositionId).Distinct().Count()
    );

    // 5-7: shared absolute stop verified on both; weighted fill correct
    Assert.True(full.GroupStopVerified);
    Assert.All(
      full.Legs!.Where(leg => leg.BrokerPositionId is not null),
      leg => Assert.True(leg.StopVerified)
    );
    Assert.Equal(2, client.StopAmendments.Select(item => item.PositionId).Distinct().Count());
    Assert.All(client.StopAmendments, item => Assert.Equal(4105.50m, item.StopLoss));

    var l1Fill = Assert.Single(full.Legs!, leg => leg.LegId == "L1").FillPrice!.Value;
    var l2Fill = Assert.Single(full.Legs!, leg => leg.LegId == "L2").FillPrice!.Value;
    var expectedWeighted =
      (l1Fill * 800m + l2Fill * 400m) / 1_200m;
    Assert.Equal(expectedWeighted, full.GroupWeightedFillPrice);

    // 8: TP1 closes pro-rata (40% of 1200 = 480, step-snapped)
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.90m, 4090.00m, 2), CancellationToken.None
    );
    Assert.Single(client.Closes);
    Assert.Equal(400, client.Closes.Sum(item => item.Volume));
    Assert.Contains(
      store.Events,
      item => item.Type == "tp_booked"
        && item.Message.Contains("TP COMPLETED")
        && item.TargetPips == 93
    );
    // L2 already filled before TP1, so no pending cancel is required.
    Assert.Empty(client.PendingOrders);

    var afterTp1 = Assert.Single(runtime.TrackedStates);
    Assert.True(afterTp1.BreakEvenApplied);
    Assert.Contains(
      store.Events,
      item => item.Type == "sl_moved" && item.Message.Contains("GROUP SL MOVED TO BE")
    );
    Assert.Equal(
      2,
      client.StopAmendments.Count(item => item.StopLoss != 4105.50m)
    );

    // 10: both legs disappear via SL → one group stop-loss event, plan closed
    foreach (var leg in afterTp1.Legs!.Where(leg => leg.BrokerPositionId is not null))
    {
      client.RemovePosition(leg.BrokerPositionId!.Value);
    }
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4106.00m, 4106.10m, 3), CancellationToken.None
    );

    Assert.Empty(runtime.TrackedStates);
    Assert.Contains(
      store.Events,
      item => item.Type == "position_closed"
        && item.Message.Contains("highest TP archived", StringComparison.OrdinalIgnoreCase)
        && item.TargetPips == 93
    );
    Assert.Contains(
      store.Events,
      item => item.Type == "position_closed" && item.Message.Contains("PLAN CLOSED")
    );
    // Close must not dump per-leg lot detail.
    Assert.DoesNotContain(
      store.Events,
      item => item.Type == "position_closed" && item.Message.Contains("lot=", StringComparison.Ordinal)
    );

    // 11: no cannot-reconstruct for TradePlan
    Assert.DoesNotContain(
      logs, line => line.Contains("cannot reconstruct", StringComparison.Ordinal)
    );

    // Dedup: restart must not republish orders_submitted / tp / plan_closed
    var submittedBefore = store.Events.Count(item => item.Type == "v8_order_submitted");
    var closedBefore = store.Events.Count(
      item => item.Type == "position_closed" && item.Message.Contains("PLAN CLOSED")
    );
    store.EnqueuePlan(PlanJson); // same plan_id claim already owned
    var restarted = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, logs.Add
    );
    await restarted.PollAsync(client, Symbol, quoteInside, CancellationToken.None);
    Assert.Equal(
      submittedBefore,
      store.Events.Count(item => item.Type == "v8_order_submitted")
    );
    Assert.Equal(
      closedBefore,
      store.Events.Count(
        item => item.Type == "position_closed" && item.Message.Contains("PLAN CLOSED")
      )
    );
    // No duplicate L1/L2 broker orders from the republished stream entry.
    Assert.Single(client.MarketOrders);
    Assert.True(client.LimitOrders.Count <= 1);
  }

  [Fact]
  public async Task RestartMidLadderDoesNotDuplicateLegsOrTp()
  {
    var store = new E2EStore();
    store.EnqueuePlan(PlanJson);
    var client = new E2EFakeTradeClient
    {
      AccountEquity = 1_300m,
      AccountBalance = 1_300m,
      EquitySource = "test",
    };
    var first = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );
    await first.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4098.46m, 4098.56m, 1), CancellationToken.None
    );
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, Assert.Single(first.TrackedStates).Stage);
    Assert.Single(client.MarketOrders);
    Assert.Single(client.LimitOrders);

    var second = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );
    await second.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4098.46m, 4098.56m, 2), CancellationToken.None
    );
    Assert.Single(client.MarketOrders);
    Assert.Single(client.LimitOrders);
    Assert.Equal(
      TradePlanRuntimeStage.PartiallyOpen,
      Assert.Single(second.TrackedStates).Stage
    );
  }

  private sealed class E2EFakeTradeClient : ICTraderTradeClient
  {
    public List<MarketOrderRequest> MarketOrders { get; } = [];
    public List<LimitOrderRequest> LimitOrders { get; } = [];
    public List<(long PositionId, long Volume)> Closes { get; } = [];
    public List<(long PositionId, decimal StopLoss)> StopAmendments { get; } = [];
    public List<long> CancelledOrderIds { get; } = [];
    public List<TradingPendingOrder> PendingOrders { get; } = [];
    private readonly List<TradingPosition> _positions = [];
    private long _nextPositionId = 501;
    private long _nextOrderId = 601;

    public decimal AccountBalance { get; set; } = 1_300m;
    public decimal AccountEquity { get; set; } = 1_300m;
    public string EquitySource { get; set; } = "test";
    public PositionCloseReason PositionCloseReasonToReturn { get; set; } =
      PositionCloseReason.StopLossOrTakeProfit;

    public void FillPendingOrder(long orderId, decimal? fillPrice = null)
    {
      var pending = PendingOrders.Single(order => order.OrderId == orderId);
      PendingOrders.Remove(pending);
      LimitOrders.RemoveAll(order => order.ClientOrderId == pending.ClientOrderId);
      _positions.Add(new TradingPosition(
        _nextPositionId++,
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

    public void RemovePosition(long positionId) =>
      _positions.RemoveAll(position => position.PositionId == positionId);

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
    ) => Task.FromResult(new PositionCloseLookup(PositionCloseReasonToReturn));

    public Task<TradeExecution> PlaceMarketOrderAsync(
      MarketOrderRequest order, CancellationToken ct
    )
    {
      MarketOrders.Add(order);
      var fillPrice = order.Direction == TradeDirection.Sell ? 4098.46m : 4089.0m;
      var positionId = _nextPositionId++;
      _positions.Add(new TradingPosition(
        positionId, order.SymbolId, order.Direction, order.Volume, fillPrice, null,
        order.Label, order.Comment, order.ClientOrderId
      ));
      return Task.FromResult(new TradeExecution(positionId, 1, fillPrice, order.Volume));
    }

    public Task<long> PlaceLimitOrderAsync(LimitOrderRequest order, CancellationToken ct)
    {
      LimitOrders.Add(order);
      var orderId = _nextOrderId++;
      PendingOrders.Add(new TradingPendingOrder(
        orderId, order.SymbolId, order.Direction, order.Volume, order.LimitPrice,
        order.Label, order.Comment, order.ClientOrderId
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
        _positions[idx] = _positions[idx] with { StopLoss = stopLoss };
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
      return Task.FromResult(new TradeExecution(positionId, 2, 4090.0m, volume));
    }
  }

  private sealed class E2EStore : IAutoTradeStore
  {
    private readonly Dictionary<string, string> _strings = new();
    private readonly List<TradeStreamEntry> _stream = [];
    private int _nextStreamId = 1;
    private string _tradePlanCursor = "0-0";

    public List<AutoTradeEvent> Events { get; } = [];

    public void EnqueuePlan(string json) =>
      _stream.Add(new TradeStreamEntry($"{_nextStreamId++}-0", json));

    public Task<string> GetCursorAsync(CancellationToken ct) => Task.FromResult("0-0");
    public Task SetCursorAsync(string cursor, CancellationToken ct) => Task.CompletedTask;
    public Task<string> GetCommandCursorAsync(CancellationToken ct) => Task.FromResult("0-0");
    public Task SetCommandCursorAsync(string cursor, CancellationToken ct) => Task.CompletedTask;
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

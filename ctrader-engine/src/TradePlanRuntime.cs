using System.Text.Json;
using System.Text.Json.Serialization;

namespace ApexVoid.CTraderFeed;

// V7 broker-execution runtime (Sections F-K of the TradePlan V7 cutover):
// consumes execution:trade_plans, arms the exact declared entry, submits
// exactly what Python declared, tracks the broker-confirmed fill/targets/
// stop, and persists enough state to restore after a restart. This is the
// piece TradePlanExecutionEngine.cs's own doc comment named as "not yet
// wired... a later phase" - that phase is this file.
//
// Deliberately a separate file/class from AutoTradeEngine.cs (which still
// legitimately calls ResolveExecutionRoute/StructureStopPlanner for the V6
// path elsewhere in the same class) so TradePlanExecutionEngineDependencyTests
// can scan every V7 runtime source file for those forbidden symbols without
// tripping over V6 code that must keep calling them. AutoTradeEngine composes
// this class into its own RunSessionAsync loop (see PollTradePlansAsync)
// rather than this class owning its own session/reconcile/heartbeat loop.

public enum TradePlanRuntimeStage
{
  Armed,
  Submitted,
  Open,
  Closed,
}

public sealed record TradePlanRuntimeState(
  string PlanId,
  string ThesisId,
  string SetupId,
  string Symbol,
  string Direction,
  string EntryType,
  TradePlanRuntimeStage Stage,
  long? PositionId = null,
  long? PendingOrderId = null,
  decimal? EntryFillPrice = null,
  long RemainingVolume = 0,
  decimal CurrentStop = 0,
  int NextTargetIndex = 0,
  bool BreakEvenApplied = false
);

public sealed class TradePlanRuntime(
  AutoTradeOptions options,
  IAutoTradeStore store,
  Func<DateTimeOffset> clock,
  Action<string> log
)
{
  private static readonly JsonSerializerOptions PlanJsonOptions = new()
  {
    PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
  };

  private static readonly JsonSerializerOptions StateJsonOptions = new()
  {
    Converters = { new JsonStringEnumConverter() },
  };

  private readonly Dictionary<string, TradePlan> _plansById = new();
  private readonly Dictionary<string, TradePlanRuntimeState> _statesById = new();
  private bool _restored;

  private static string PlanClaimKey(string planId) => $"execution:plan_claim:{planId}";
  private static string PlanStateKey(string planId) => $"execution:plan_runtime:{planId}";
  private static string TrackedPlansKey() => "execution:trade_plan_runtime_ids";

  public IReadOnlyCollection<TradePlanRuntimeState> TrackedStates => _statesById.Values;

  /// <summary>
  /// One poll cycle: recover state on first call, claim+arm any newly
  /// published plans, evaluate armed plans against the live quote, and
  /// manage every open position's targets/stop/BE. Call once per
  /// AutoTradeEngine.RunSessionAsync loop iteration - see
  /// AutoTradeEngine.PollTradePlansAsync.
  /// </summary>
  public async Task PollAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    SpotPrice? quote,
    CancellationToken cancellationToken
  )
  {
    if (!_restored)
    {
      await RestoreAsync(cancellationToken);
      _restored = true;
    }
    await ReadAndArmNewPlansAsync(cancellationToken);
    if (quote is null)
    {
      return;
    }
    await EvaluateArmedPlansAsync(client, symbol, quote, cancellationToken);
    await ManageOpenPositionsAsync(client, symbol, quote, cancellationToken);
  }

  private async Task RestoreAsync(CancellationToken cancellationToken)
  {
    var raw = await store.GetStringAsync(TrackedPlansKey(), cancellationToken);
    if (string.IsNullOrWhiteSpace(raw))
    {
      return;
    }
    foreach (var planId in raw.Split(',', StringSplitOptions.RemoveEmptyEntries))
    {
      var stateJson = await store.GetStringAsync(PlanStateKey(planId), cancellationToken);
      if (string.IsNullOrWhiteSpace(stateJson))
      {
        continue;
      }
      var state = JsonSerializer.Deserialize<TradePlanRuntimeState>(
        stateJson, StateJsonOptions
      );
      if (state is null || state.Stage == TradePlanRuntimeStage.Closed)
      {
        continue;
      }
      _statesById[planId] = state;
      var planJson = await store.GetStringAsync(
        TradePlanStreamKeys.PlanKey(planId), cancellationToken
      );
      if (!string.IsNullOrWhiteSpace(planJson))
      {
        try
        {
          var plan = JsonSerializer.Deserialize<TradePlan>(planJson, PlanJsonOptions);
          if (plan is not null)
          {
            _plansById[planId] = plan;
          }
        }
        catch (JsonException)
        {
          log($"v7 restore: could not re-parse plan {planId}");
        }
      }
      log($"v7 restore: recovered {planId} at stage {state.Stage}");
    }
  }

  private async Task PersistStateAsync(
    TradePlanRuntimeState state,
    CancellationToken cancellationToken
  )
  {
    _statesById[state.PlanId] = state;
    await store.SetStringAsync(
      PlanStateKey(state.PlanId),
      JsonSerializer.Serialize(state, StateJsonOptions),
      cancellationToken
    );
    var raw = await store.GetStringAsync(TrackedPlansKey(), cancellationToken);
    var ids = string.IsNullOrWhiteSpace(raw)
      ? new HashSet<string>()
      : raw.Split(',', StringSplitOptions.RemoveEmptyEntries).ToHashSet();
    if (ids.Add(state.PlanId))
    {
      await store.SetStringAsync(
        TrackedPlansKey(), string.Join(',', ids), cancellationToken
      );
    }
  }

  private async Task ForgetPlanAsync(string planId, CancellationToken cancellationToken)
  {
    _plansById.Remove(planId);
    _statesById.Remove(planId);
    await store.DeleteStringAsync(PlanStateKey(planId), cancellationToken);
    var raw = await store.GetStringAsync(TrackedPlansKey(), cancellationToken);
    if (string.IsNullOrWhiteSpace(raw))
    {
      return;
    }
    var ids = raw.Split(',', StringSplitOptions.RemoveEmptyEntries)
      .Where(id => id != planId)
      .ToArray();
    await store.SetStringAsync(TrackedPlansKey(), string.Join(',', ids), cancellationToken);
  }

  private async Task ReadAndArmNewPlansAsync(CancellationToken cancellationToken)
  {
    var cursor = await store.GetTradePlanCursorAsync(cancellationToken);
    var entries = await store.ReadCandidatesAsync(
      options.TradePlanStream, cursor, 20, cancellationToken
    );
    foreach (var entry in entries)
    {
      cursor = entry.Id;
      TradePlan plan;
      try
      {
        plan = JsonSerializer.Deserialize<TradePlan>(entry.Payload, PlanJsonOptions)
          ?? throw new TradePlanContractException("null plan payload");
        TradePlanValidator.Validate(plan);
      }
      catch (Exception exception) when (
        exception is TradePlanContractException or JsonException
      )
      {
        log($"v7 plan rejected: invalid contract - {exception.Message}");
        continue;
      }
      var claimed = await store.TryClaimStringAsync(
        PlanClaimKey(plan.PlanId),
        options.Label,
        TimeSpan.FromHours(24),
        cancellationToken
      );
      if (!claimed)
      {
        continue;
      }
      _plansById[plan.PlanId] = plan;
      // Persist the runtime's own copy of the plan JSON, independent of
      // Python's execution:plan:{plan_id} TTL - restart recovery must not
      // depend on that key still existing by the time this executor
      // restarts.
      await store.SetStringAsync(
        TradePlanStreamKeys.PlanKey(plan.PlanId), entry.Payload, cancellationToken
      );
      var state = new TradePlanRuntimeState(
        plan.PlanId,
        plan.ThesisId,
        plan.SetupId,
        plan.Symbol,
        plan.Analysis.Direction,
        plan.Entry.Type,
        TradePlanRuntimeStage.Armed,
        CurrentStop: plan.Stop.Price
      );
      await PersistStateAsync(state, cancellationToken);
      log($"v7 plan armed id={plan.PlanId} entry_type={plan.Entry.Type}");
      await PublishEventAsync(
        "plan_armed",
        $"PLAN ARMED {plan.Analysis.Strategy} {plan.Analysis.Direction} "
        + $"({plan.Entry.Type})",
        plan,
        cancellationToken
      );
    }
    await store.SetTradePlanCursorAsync(cursor, cancellationToken);
  }

  private Task PublishEventAsync(
    string type,
    string message,
    TradePlan plan,
    CancellationToken cancellationToken,
    long? positionId = null,
    decimal? price = null,
    int? targetPips = null,
    long? volume = null
  ) => store.PublishAutoTradeEventAsync(
    options.EventStream,
    new AutoTradeEvent(
      type,
      clock().ToUnixTimeSeconds(),
      message,
      plan.Symbol,
      CandidateId: plan.PlanId,
      MatchId: plan.SetupId,
      ThesisId: plan.ThesisId,
      Setup: plan.Analysis.Strategy,
      StrategyFamily: plan.Analysis.StrategyFamily,
      Direction: plan.Analysis.Direction,
      StructuralZoneId: plan.SourceStructure.StructureId,
      EntryType: plan.Entry.Type,
      PositionId: positionId,
      Price: price,
      TargetPips: targetPips,
      Volume: volume,
      StopLoss: plan.Stop.Price
    ),
    cancellationToken
  );

  private async Task EvaluateArmedPlansAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    SpotPrice quote,
    CancellationToken cancellationToken
  )
  {
    foreach (var state in _statesById.Values.Where(
      s => s.Stage == TradePlanRuntimeStage.Armed
    ).ToArray())
    {
      if (!_plansById.TryGetValue(state.PlanId, out var plan))
      {
        continue;
      }
      var now = clock().ToUnixTimeSeconds();
      var spreadTicks = StopTrailPlanner.RequireTickSize(symbol) > 0
        ? (quote.Ask - quote.Bid) / StopTrailPlanner.RequireTickSize(symbol)
        : 0m;
      var decision = TradePlanExecutionEngine.EvaluateEntry(
        plan, quote.Bid, quote.Ask, spreadTicks, now
      );
      if (decision.RejectReason == "plan_expired")
      {
        await ForgetPlanAsync(state.PlanId, cancellationToken);
        log($"v7 plan expired id={state.PlanId}");
        continue;
      }
      if (!decision.ShouldSubmit)
      {
        continue;
      }
      if (!ShouldSubmitOrders)
      {
        // shadow_v7 (or DryRun): the plan is valid and would have fired,
        // but per docs/adr-trade-plan-v7-boundary.md shadow mode "places no
        // orders from it". Left Armed so the next poll re-evaluates it -
        // this is intentionally not a terminal state, since shadow mode is
        // observing what V7 *would* do, not executing a one-shot decision.
        log(
          $"v7 shadow: would submit id={plan.PlanId} entry_type={plan.Entry.Type}"
        );
        continue;
      }
      await SubmitEntryAsync(client, symbol, plan, state, cancellationToken);
    }
  }

  private bool ShouldSubmitOrders =>
    options.ContractMode is "v7_primary" or "v7_only" && !options.DryRun;

  private async Task SubmitEntryAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    TradePlan plan,
    TradePlanRuntimeState state,
    CancellationToken cancellationToken
  )
  {
    var account = await client.GetTradingAccountAsync(cancellationToken);
    var volumePlan = TradePlanExecutionEngine.CalculateVolume(
      plan, account.Balance, options.PipSize, options.PipValuePerLot, symbol
    );
    var direction = plan.Analysis.Direction == "BUY"
      ? TradeDirection.Buy
      : TradeDirection.Sell;
    var comment = $"v7|{plan.PlanId}|{plan.ThesisId}";

    if (plan.Entry.Type == TradePlanContract.EntryTypeMarketWatch)
    {
      var execution = await client.PlaceMarketOrderAsync(
        new MarketOrderRequest(
          symbol.SymbolId,
          direction,
          volumePlan.TotalVolume,
          RelativeStopLoss(plan, symbol),
          options.Label,
          comment,
          plan.PlanId
        ),
        cancellationToken
      );
      var next = state with
      {
        Stage = TradePlanRuntimeStage.Open,
        PositionId = execution.PositionId,
        EntryFillPrice = execution.ExecutionPrice,
        RemainingVolume = execution.ExecutedVolume,
      };
      await PersistStateAsync(next, cancellationToken);
      log(
        $"v7 order submitted id={plan.PlanId} position={execution.PositionId} "
        + $"price={execution.ExecutionPrice}"
      );
      await PublishEventAsync(
        "order_filled",
        $"ORDER FILLED {plan.Analysis.Direction} {execution.ExecutedVolume} "
        + $"@ {execution.ExecutionPrice}",
        plan,
        cancellationToken,
        positionId: execution.PositionId,
        price: execution.ExecutionPrice,
        volume: execution.ExecutedVolume
      );
      return;
    }

    // single_limit and limit_ladder: submit every declared leg at its exact
    // price - never a different price the executor picks. Fill/position
    // ownership is reconciled from the pending-order comment (ParsePlanId).
    var legs = plan.Entry.Type == TradePlanContract.EntryTypeSingleLimit
      ? new[] { (plan.Entry.OrderPrice!.Value, volumePlan.TotalVolume) }
      : plan.Entry.Legs!
        .Zip(volumePlan.Slices, (leg, slice) => (leg.Price, slice.Volume))
        .ToArray();
    long? firstOrderId = null;
    foreach (var (price, volume) in legs)
    {
      var orderId = await client.PlaceLimitOrderAsync(
        new LimitOrderRequest(
          symbol.SymbolId,
          direction,
          volume,
          price,
          RelativeStopLoss(plan, symbol),
          options.Label,
          comment,
          plan.PlanId
        ),
        cancellationToken
      );
      firstOrderId ??= orderId;
    }
    var pendingState = state with
    {
      Stage = TradePlanRuntimeStage.Submitted,
      PendingOrderId = firstOrderId,
      RemainingVolume = volumePlan.TotalVolume,
    };
    await PersistStateAsync(pendingState, cancellationToken);
    log($"v7 limit order(s) submitted id={plan.PlanId} legs={legs.Length}");
    await PublishEventAsync(
      "order_submitted",
      $"ORDER SUBMITTED {plan.Analysis.Direction} {legs.Length} leg(s)",
      plan,
      cancellationToken,
      volume: volumePlan.TotalVolume
    );
  }

  private static long RelativeStopLoss(TradePlan plan, SymbolInfo symbol)
  {
    var tick = StopTrailPlanner.RequireTickSize(symbol);
    var entryReference = plan.Analysis.Direction == "BUY"
      ? plan.Entry.EntryPrices().Min()
      : plan.Entry.EntryPrices().Max();
    return decimal.ToInt64(Math.Abs(entryReference - plan.Stop.Price) / tick);
  }

  private async Task ManageOpenPositionsAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    SpotPrice quote,
    CancellationToken cancellationToken
  )
  {
    var openStates = _statesById.Values.Where(
      s => s.Stage == TradePlanRuntimeStage.Open && s.PositionId is not null
    ).ToArray();
    if (openStates.Length == 0)
    {
      return;
    }
    var positions = await client.ReconcilePositionsAsync(cancellationToken);
    var byId = positions.ToDictionary(p => p.PositionId);
    foreach (var initialState in openStates)
    {
      var state = initialState;
      if (!_plansById.TryGetValue(state.PlanId, out var plan))
      {
        continue;
      }
      if (!byId.ContainsKey(state.PositionId!.Value))
      {
        // Broker no longer reports this position - stopped out, manually
        // closed, or fully target-closed by a previous cycle. A single
        // missing snapshot is not proof of closure (see AutoTradeEngine's
        // own multi-snapshot confirmation pattern for the V6 path); this
        // runtime keeps it simple and requires an explicit close signal
        // (ClosePositionAsync response) before marking Closed, so a
        // transient reconcile gap can never silently drop tracked state.
        continue;
      }
      var currentPrice = plan.Analysis.Direction == "BUY" ? quote.Bid : quote.Ask;
      var target = plan.Targets.ElementAtOrDefault(state.NextTargetIndex);
      if (target is not null && TradePlanExecutionEngine.HasReachedTarget(
        plan, target, currentPrice
      ))
      {
        var closeVolume = decimal.ToInt64(
          state.RemainingVolume * target.CloseRatio
            / plan.Targets.Skip(state.NextTargetIndex).Sum(t => t.CloseRatio)
        );
        closeVolume = Math.Min(closeVolume, state.RemainingVolume);
        var execution = await client.ClosePositionAsync(
          state.PositionId!.Value, closeVolume, cancellationToken
        );
        var remaining = state.RemainingVolume - (execution.ExecutedVolume);
        log(
          $"v7 target hit id={plan.PlanId} target={target.TargetId} "
          + $"volume={execution.ExecutedVolume} remaining={remaining}"
        );
        await PublishEventAsync(
          remaining <= 0 ? "position_closed" : "tp_booked",
          $"TP BOOKED {target.TargetId} @ {execution.ExecutionPrice}",
          plan,
          cancellationToken,
          positionId: state.PositionId,
          price: execution.ExecutionPrice,
          volume: execution.ExecutedVolume
        );
        state = state with
        {
          RemainingVolume = Math.Max(0, remaining),
          NextTargetIndex = state.NextTargetIndex + 1,
        };
        await PersistStateAsync(state, cancellationToken);
        if (remaining <= 0)
        {
          await ForgetPlanAsync(state.PlanId, cancellationToken);
          continue;
        }
      }
      if (
        !state.BreakEvenApplied
        && plan.Management.BeAfterTargetId is not null
        && state.EntryFillPrice is decimal fillPrice
        && state.NextTargetIndex > IndexOfTarget(plan, plan.Management.BeAfterTargetId)
      )
      {
        var be = TradePlanExecutionEngine.CalculateBreakEven(
          plan, fillPrice, state.CurrentStop, symbol
        );
        if (be.Improved)
        {
          await client.AmendPositionStopLossAsync(
            state.PositionId!.Value, be.NewStop, cancellationToken
          );
          state = state with { CurrentStop = be.NewStop, BreakEvenApplied = true };
          await PersistStateAsync(state, cancellationToken);
          log($"v7 stop moved to BE id={plan.PlanId} stop={be.NewStop}");
          await PublishEventAsync(
            "sl_moved",
            $"SL MOVED to {be.NewStop} (BE)",
            plan,
            cancellationToken,
            positionId: state.PositionId,
            price: be.NewStop
          );
        }
      }
    }
  }

  private static int IndexOfTarget(TradePlan plan, string targetId)
  {
    for (var index = 0; index < plan.Targets.Count; index++)
    {
      if (plan.Targets[index].TargetId == targetId)
      {
        return index;
      }
    }
    return -1;
  }
}

internal static class TradePlanStreamKeys
{
  public static string PlanKey(string planId) => $"execution:plan:{planId}";
}

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
  IReadOnlyList<long>? PendingOrderIds = null,
  decimal? EntryFillPrice = null,
  long RemainingVolume = 0,
  decimal CurrentStop = 0,
  int NextTargetIndex = 0,
  bool BreakEvenApplied = false,
  // How many of Entry.Legs (or the single single_limit leg) have already
  // been submitted to the broker and durably recorded here. Resuming from
  // this index - instead of always restarting the ladder at leg 0 - is what
  // stops a mid-ladder broker error from resubmitting an already-accepted
  // leg on the next poll (see SubmitEntryAsync).
  int SubmittedLegCount = 0
);

public sealed record TradePlanRejectionRecord(
  string StreamId,
  string? PlanId,
  string ExceptionType,
  string ReasonCode,
  int? SchemaVersion,
  string Message,
  long RejectedAt
);

public sealed record TradePlanExecutorAcknowledgement(
  string PlanId,
  string State,
  long UpdatedAt,
  string Executor,
  string? StreamId = null,
  string? ReasonCode = null
);

[JsonSourceGenerationOptions(
  PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower,
  PropertyNameCaseInsensitive = true,
  GenerationMode = JsonSourceGenerationMode.Metadata
)]
[JsonSerializable(typeof(TradePlan))]
[JsonSerializable(typeof(TradePlanRejectionRecord))]
[JsonSerializable(typeof(TradePlanExecutorAcknowledgement))]
internal sealed partial class AutoTradeJsonContext : JsonSerializerContext;

[JsonSourceGenerationOptions(
  PropertyNameCaseInsensitive = true,
  UseStringEnumConverter = true,
  GenerationMode = JsonSourceGenerationMode.Metadata
)]
[JsonSerializable(typeof(TradePlanRuntimeState))]
internal sealed partial class TradePlanStateJsonContext : JsonSerializerContext;

public static class TradePlanJson
{
  private const string SelfTestPayload = """
  {
    "version":7,
    "plan_id":"v7:self-test",
    "thesis_id":"self-test-thesis",
    "setup_id":"self-test-setup",
    "symbol":"XAU",
    "created_at":1719999600,
    "expires_at":2000000000,
    "analysis":{
      "strategy":"Contract Self Test",
      "strategy_family":"contract",
      "direction":"BUY",
      "context_timeframes":["M15"],
      "formation_timeframe":"M5",
      "confirmation_timeframe":"M1",
      "formation_bar_ts":1719999300,
      "confirmation_bar_ts":1719999600,
      "score":3.0,
      "confluence":3,
      "bias":"up",
      "regime":"trend",
      "reasons":[],
      "tags":[]
    },
    "source_structure":{
      "structure_id":"self-test-zone",
      "kind":"demand",
      "timeframe":"M5",
      "low":"4088.10",
      "high":"4090.00",
      "invalidation_price":"4082.50"
    },
    "entry":{
      "type":"market_watch",
      "expires_at":2000000000,
      "zone_low":"4088.10",
      "zone_high":"4090.00",
      "activation":"quote_inside_zone",
      "price_side":"ask",
      "max_spread_ticks":8,
      "max_slippage_ticks":10,
      "legs":[]
    },
    "stop":{
      "type":"absolute",
      "price":"4082.50",
      "source":"structure",
      "structure_id":"self-test-zone",
      "reason":"contract self test"
    },
    "targets":[
      {
        "target_id":"TP1",
        "type":"absolute",
        "price":"4096.00",
        "close_ratio":"1.0"
      }
    ],
    "risk":{
      "risk_percent":"1.0",
      "risk_multiplier":"1.0",
      "max_volume":100000,
      "max_group_risk_percent":"2.0"
    },
    "management":{
      "be_after_target_id":null,
      "be_buffer_ticks":6,
      "never_worsen_stop":true
    },
    "execution_policy":{
      "allow_market":true,
      "allow_limit":false,
      "allow_partial_fill":true,
      "cancel_on_expiry":true
    },
    "provenance":{
      "analysis_engine_version":"self-test",
      "market_map_id":"",
      "config_fingerprint":""
    }
  }
  """;

  public static TradePlan DeserializePlan(string json) =>
    JsonSerializer.Deserialize(json, AutoTradeJsonContext.Default.TradePlan)
    ?? throw new TradePlanContractException("null plan payload");

  public static string SerializeState(TradePlanRuntimeState state) =>
    JsonSerializer.Serialize(
      state,
      TradePlanStateJsonContext.Default.TradePlanRuntimeState
    );

  public static TradePlanRuntimeState? DeserializeState(string json) =>
    JsonSerializer.Deserialize(
      json,
      TradePlanStateJsonContext.Default.TradePlanRuntimeState
    );

  public static string SerializeRejection(TradePlanRejectionRecord rejection) =>
    JsonSerializer.Serialize(
      rejection,
      AutoTradeJsonContext.Default.TradePlanRejectionRecord
    );

  public static string SerializeAcknowledgement(
    TradePlanExecutorAcknowledgement acknowledgement
  ) => JsonSerializer.Serialize(
    acknowledgement,
    AutoTradeJsonContext.Default.TradePlanExecutorAcknowledgement
  );

  public static void AssertContractAvailable()
  {
    var plan = DeserializePlan(SelfTestPayload);
    TradePlanValidator.Validate(plan);
  }
}

public sealed class TradePlanRuntime(
  AutoTradeOptions options,
  IAutoTradeStore store,
  Func<DateTimeOffset> clock,
  Action<string> log
)
{
  private readonly Dictionary<string, TradePlan> _plansById = new();
  private readonly Dictionary<string, TradePlanRuntimeState> _statesById = new();
  private bool _restored;

  private static string PlanClaimKey(string planId) => $"execution:plan_claim:{planId}";
  private static string PlanStateKey(string planId) => $"execution:plan_runtime:{planId}";
  private static string PlanRecoveryKey(string planId) =>
    $"execution:plan_recovery:{planId}";
  private static string PlanExecutorStateKey(string planId) =>
    $"execution:plan_state:{planId}";
  private static string PlanAcknowledgementKey(string planId) =>
    $"execution:plan_ack:{planId}";
  private static string PlanRejectionKey(string streamId) =>
    $"execution:plan_rejection:{streamId}";
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
    await ReadAndArmNewPlansAsync(client, symbol, cancellationToken);
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
      var state = TradePlanJson.DeserializeState(stateJson);
      if (state is null || state.Stage == TradePlanRuntimeStage.Closed)
      {
        continue;
      }
      _statesById[planId] = state;
      var planJson = await store.GetStringAsync(
        PlanRecoveryKey(planId), cancellationToken
      ) ?? await store.GetStringAsync(
        TradePlanStreamKeys.PlanKey(planId), cancellationToken
      );
      if (!string.IsNullOrWhiteSpace(planJson))
      {
        try
        {
          var plan = TradePlanJson.DeserializePlan(planJson);
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
      TradePlanJson.SerializeState(state),
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
    await store.DeleteStringAsync(PlanRecoveryKey(planId), cancellationToken);
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

  private async Task ReadAndArmNewPlansAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    CancellationToken cancellationToken
  )
  {
    var cursor = await store.GetTradePlanCursorAsync(cancellationToken);
    var entries = await store.ReadCandidatesAsync(
      options.TradePlanStream, cursor, 20, cancellationToken
    );
    foreach (var entry in entries)
    {
      try
      {
        await ProcessTradePlanEntryAsync(entry, client, symbol, cancellationToken);
        await store.SetTradePlanCursorAsync(entry.Id, cancellationToken);
      }
      catch (OperationCanceledException)
      {
        throw;
      }
      catch (Exception exception)
      {
        log(
          "auto_trade_plan_retry "
          + $"stream_id={entry.Id} exception={exception.GetType().Name} "
          + $"message={exception.Message}"
        );
        return;
      }
    }
  }

  private async Task ProcessTradePlanEntryAsync(
    TradeStreamEntry entry,
    ICTraderTradeClient client,
    SymbolInfo symbol,
    CancellationToken cancellationToken
  )
  {
    log($"auto_trade_plan_received stream_id={entry.Id}");
    TradePlan plan;
    try
    {
      plan = TradePlanJson.DeserializePlan(entry.Payload);
      TradePlanValidator.Validate(plan);
    }
    catch (Exception exception) when (
      exception is TradePlanContractException
        or JsonException
        or InvalidOperationException
        or NullReferenceException
        or ArgumentException
    )
    {
      var (planId, version) = ExtractPlanIdentity(entry.Payload);
      await PersistRejectionAsync(
        entry.Id,
        planId,
        version,
        exception,
        cancellationToken
      );
      log(
        "auto_trade_plan_rejected "
        + $"stream_id={entry.Id} plan_id={planId ?? "-"} "
        + $"reason_code=invalid_contract exception={exception.GetType().Name}"
      );
      return;
    }
    log(
      "auto_trade_plan_deserialized "
      + $"stream_id={entry.Id} plan_id={plan.PlanId} version={plan.Version}"
    );
    var claimed = await store.TryClaimStringAsync(
      PlanClaimKey(plan.PlanId),
      options.Label,
      TimeSpan.FromHours(24),
      cancellationToken
    );
    if (!claimed)
    {
      var owner = await store.GetStringAsync(
        PlanClaimKey(plan.PlanId), cancellationToken
      );
      if (owner != options.Label)
      {
        await PersistPlanExecutionStateAsync(
          plan.PlanId,
          "received",
          entry.Id,
          cancellationToken
        );
        log(
          "auto_trade_plan_duplicate "
          + $"stream_id={entry.Id} plan_id={plan.PlanId}"
        );
        return;
      }
      // P1-3: the claim already belongs to THIS executor - by construction
      // that only happens when we already began processing this exact
      // plan_id at least once before (a redelivered/retried stream entry,
      // eg. after a restart re-reads from an earlier cursor). Falling
      // through to the fresh-arm path below used to unconditionally
      // overwrite whatever real progress had already happened (a
      // submitted/filled order) with a brand-new Armed state and
      // re-publish "plan_armed" - risking a duplicate broker submission.
      // A duplicate claim must never look like a normal re-arm: reconcile
      // against whatever evidence exists (in-memory state first, durable
      // Redis state second) and never proceed past this point regardless
      // of what is found - there is no legitimate first-time-arm scenario
      // once we already own this claim.
      var evidenceStage = _statesById.TryGetValue(plan.PlanId, out var existingState)
        ? existingState.Stage.ToString()
        : await store.GetStringAsync(PlanStateKey(plan.PlanId), cancellationToken)
          is { } restoredJson
          ? TradePlanJson.DeserializeState(restoredJson)?.Stage.ToString() ?? "unknown"
          : "no_evidence_forgotten";
      log(
        "auto_trade_plan_duplicate_claim_reconciled "
        + $"stream_id={entry.Id} plan_id={plan.PlanId} stage={evidenceStage}"
      );
      return;
    }
    // Size against the live account before ever announcing PLAN ARMED. A
    // sizing failure here is not transient (identical guard exists in
    // EvaluateArmedPlansAsync/SubmitEntryAsync, for when balance/risk drift
    // between now and actual submission) - arming a plan that can never be
    // sized, only to reject it a moment later, produced a nonsensical
    // PLAN ARMED -> PLAN REJECTED flip within the same poll cycle.
    try
    {
      var account = await client.GetTradingAccountAsync(cancellationToken);
      TradePlanExecutionEngine.CalculateVolume(
        plan, account.Balance, options.PipSize, options.PipValuePerLot, symbol
      );
    }
    catch (Exception exception) when (
      exception is VolumePlanningException or TradePlanContractException
    )
    {
      await PersistPlanExecutionStateAsync(
        plan.PlanId, "rejected", entry.Id, cancellationToken,
        $"sizing_failed:{exception.GetType().Name}"
      );
      await PublishEventAsync(
        "plan_rejected",
        $"TradePlan V7 rejected: {exception.Message}",
        plan,
        cancellationToken
      );
      log(
        $"v7 plan sizing rejected pre-arm id={plan.PlanId} "
        + $"stream_id={entry.Id} "
        + $"exception={exception.GetType().Name} message={exception.Message}"
      );
      return;
    }
    _plansById[plan.PlanId] = plan;
    // Keep the executor recovery copy independent of Python's payload TTL.
    await store.SetStringAsync(
      PlanRecoveryKey(plan.PlanId), entry.Payload, cancellationToken
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
    await PersistPlanExecutionStateAsync(
      plan.PlanId,
      "armed",
      entry.Id,
      cancellationToken
    );
    log(
      "auto_trade_plan_armed "
      + $"stream_id={entry.Id} plan_id={plan.PlanId} "
      + $"entry_type={plan.Entry.Type}"
    );
    await PublishEventAsync(
      "plan_armed",
      $"PLAN ARMED {plan.Analysis.Strategy} {plan.Analysis.Direction} "
      + $"({plan.Entry.Type})",
      plan,
      cancellationToken
    );
  }

  private async Task PersistPlanExecutionStateAsync(
    string planId,
    string state,
    string? streamId,
    CancellationToken cancellationToken,
    string? reasonCode = null
  )
  {
    var current = await store.GetStringAsync(
      PlanExecutorStateKey(planId), cancellationToken
    );
    if (
      current is not null
      && PlanStatePriority(current) > PlanStatePriority(state)
    )
    {
      return;
    }
    await store.SetStringAsync(
      PlanExecutorStateKey(planId), state, cancellationToken
    );
    await store.SetStringAsync(
      PlanAcknowledgementKey(planId),
      TradePlanJson.SerializeAcknowledgement(
        new TradePlanExecutorAcknowledgement(
          planId,
          state,
          clock().ToUnixTimeSeconds(),
          options.Label,
          streamId,
          reasonCode
        )
      ),
      cancellationToken
    );
  }

  private static int PlanStatePriority(string state) => state switch
  {
    "published" => 10,
    "received" => 20,
    "armed" => 30,
    "submitted" => 40,
    "filled" => 50,
    "managing" => 60,
    "completed" => 100,
    "rejected" or "cancelled" or "expired" => 100,
    _ => 0,
  };

  private async Task PersistRejectionAsync(
    string streamId,
    string? planId,
    int? version,
    Exception exception,
    CancellationToken cancellationToken
  )
  {
    var record = new TradePlanRejectionRecord(
      streamId,
      planId,
      exception.GetType().Name,
      "invalid_contract",
      version,
      exception.Message,
      clock().ToUnixTimeSeconds()
    );
    await store.SetStringAsync(
      PlanRejectionKey(streamId),
      TradePlanJson.SerializeRejection(record),
      cancellationToken
    );
    if (!string.IsNullOrWhiteSpace(planId))
    {
      await PersistPlanExecutionStateAsync(
        planId,
        "rejected",
        streamId,
        cancellationToken,
        "invalid_contract"
      );
    }
    await store.PublishAutoTradeEventAsync(
      options.EventStream,
      new AutoTradeEvent(
        "plan_rejected",
        record.RejectedAt,
        $"TradePlan V7 rejected: {record.Message}",
        "UNKNOWN",
        CandidateId: planId,
        ReasonCode: record.ReasonCode,
        Stream: streamId
      ),
      cancellationToken
    );
  }

  private static (string? PlanId, int? Version) ExtractPlanIdentity(string payload)
  {
    try
    {
      using var document = JsonDocument.Parse(payload);
      var root = document.RootElement;
      var planId = root.TryGetProperty("plan_id", out var id)
        ? id.GetString()
        : null;
      int? version = root.TryGetProperty("version", out var schema)
        && schema.TryGetInt32(out var parsed)
          ? parsed
          : null;
      return (planId, version);
    }
    catch (JsonException)
    {
      return (null, null);
    }
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
        await PersistPlanExecutionStateAsync(
          plan.PlanId,
          "expired",
          null,
          cancellationToken,
          "plan_expired"
        );
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
      try
      {
        await SubmitEntryAsync(
          client, symbol, plan, state, quote, cancellationToken
        );
      }
      catch (Exception exception) when (
        exception is VolumePlanningException or TradePlanContractException
      )
      {
        // A sizing failure (e.g. risk-based volume too small to split
        // across a limit_ladder's declared legs) means this specific plan
        // can never execute as configured - it is not transient. Left
        // unhandled, this would escape EvaluateArmedPlansAsync/PollAsync and
        // get caught only by the top-level consumer retry loop, which
        // reprocesses the same Armed plan (still ShouldSubmit) every
        // attempt - an infinite crash loop that blocks every other plan and
        // symbol from ever polling. Reject just this plan and move on
        // instead.
        await PersistPlanExecutionStateAsync(
          plan.PlanId, "rejected", null, cancellationToken,
          $"sizing_failed:{exception.GetType().Name}"
        );
        await PublishEventAsync(
          "plan_rejected",
          $"TradePlan V7 rejected: {exception.Message}",
          plan,
          cancellationToken
        );
        await ForgetPlanAsync(state.PlanId, cancellationToken);
        log(
          $"v7 plan sizing rejected id={state.PlanId} "
          + $"exception={exception.GetType().Name} message={exception.Message}"
        );
      }
    }
  }

  private bool ShouldSubmitOrders =>
    options.ContractMode is "v7_primary" or "v7_only" && !options.DryRun;

  private async Task SubmitEntryAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    TradePlan plan,
    TradePlanRuntimeState state,
    SpotPrice quote,
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

    if (plan.Entry.Type == TradePlanContract.EntryTypeMarketWatch)
    {
      var execution = await client.PlaceMarketOrderAsync(
        new MarketOrderRequest(
          symbol.SymbolId,
          direction,
          volumePlan.TotalVolume,
          RelativeStopLoss(plan, symbol),
          options.Label,
          $"v7|{plan.PlanId}|{plan.ThesisId}",
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
        SubmittedLegCount = 1,
      };
      await PersistStateAsync(next, cancellationToken);
      await PersistPlanExecutionStateAsync(
        plan.PlanId, "filled", null, cancellationToken
      );
      log(
        $"auto_trade_plan_submitted plan_id={plan.PlanId} "
        + $"position={execution.PositionId} "
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
    // price - never a different price the executor picks.
    var legs = plan.Entry.Type == TradePlanContract.EntryTypeSingleLimit
      ? new[] { (plan.Entry.OrderPrice!.Value, volumePlan.TotalVolume) }
      : plan.Entry.Legs!
        .Zip(volumePlan.Slices, (leg, slice) => (leg.Price, slice.Volume))
        .ToArray();

    // Resume from the first leg not yet durably recorded as submitted -
    // never restart the ladder at leg 0. Without this, a broker rejection
    // on leg 2+ (see the ClientOrderId note below) throws before the old
    // code ever persisted Stage=Submitted, so the plan stayed Armed and the
    // next poll resubmitted leg 1 even though the broker had already
    // accepted it.
    var positionId = state.PositionId;
    var entryFillPrice = state.EntryFillPrice;
    var filledVolume = state.RemainingVolume;
    var pendingOrderIds = new List<long>(state.PendingOrderIds ?? []);
    for (var index = state.SubmittedLegCount; index < legs.Length; index++)
    {
      var (price, volume) = legs[index];
      // Every leg used to share one plan-wide comment/ClientOrderId. cTrader
      // rejects a second order submitted with a ClientOrderId it has
      // already seen, so leg 2+ of any ladder always failed here - one leg
      // per plan, never reused across legs, but still deterministic so a
      // genuine duplicate resubmission of the SAME leg is still caught by
      // the broker instead of double-ordering it.
      var legComment = $"v7|{plan.PlanId}|{plan.ThesisId}|{index}";
      var legClientOrderId = $"{plan.PlanId}:{index}";
      // A resting limit is only valid on the correct side of the live
      // market (SELL limit price >= current bid, BUY limit price <=
      // current ask). Detection latency and DCA-ladder anchoring can both
      // produce a leg priced at-or-through the live quote by the time this
      // actually reaches the broker - submitting that as a limit either
      // gets rejected outright or, if accepted, is really a marketable
      // order masquerading as a resting one. Submit it as an actual market
      // order instead so entry is guaranteed rather than left stuck.
      var marketable = direction == TradeDirection.Buy
        ? price >= quote.Ask
        : price <= quote.Bid;
      if (marketable)
      {
        var execution = await client.PlaceMarketOrderAsync(
          new MarketOrderRequest(
            symbol.SymbolId,
            direction,
            volume,
            RelativeStopLoss(plan, symbol),
            options.Label,
            legComment,
            legClientOrderId
          ),
          cancellationToken
        );
        positionId ??= execution.PositionId;
        entryFillPrice ??= execution.ExecutionPrice;
        filledVolume += execution.ExecutedVolume;
      }
      else
      {
        var orderId = await client.PlaceLimitOrderAsync(
          new LimitOrderRequest(
            symbol.SymbolId,
            direction,
            volume,
            price,
            RelativeStopLoss(plan, symbol),
            options.Label,
            legComment,
            legClientOrderId
          ),
          cancellationToken
        );
        pendingOrderIds.Add(orderId);
      }
      // Persist after EVERY leg, not once at the end - this is what makes a
      // mid-ladder broker error resumable instead of resubmission-prone.
      // Stage deliberately stays Armed until every leg has been attempted:
      // EvaluateArmedPlansAsync only re-evaluates Stage==Armed plans, so
      // flipping Stage away mid-ladder would strand any remaining legs -
      // the next poll would never come back to finish submitting them.
      state = state with
      {
        PositionId = positionId,
        EntryFillPrice = entryFillPrice,
        RemainingVolume = filledVolume,
        PendingOrderIds = pendingOrderIds,
        SubmittedLegCount = index + 1,
      };
      await PersistStateAsync(state, cancellationToken);
    }
    state = state with
    {
      Stage = positionId is not null
        ? TradePlanRuntimeStage.Open
        : TradePlanRuntimeStage.Submitted,
    };
    await PersistStateAsync(state, cancellationToken);
    await PersistPlanExecutionStateAsync(
      plan.PlanId,
      pendingOrderIds.Count == 0 ? "filled" : "submitted",
      null,
      cancellationToken
    );
    log(
      $"auto_trade_plan_submitted plan_id={plan.PlanId} legs={legs.Length} "
      + $"filled_volume={filledVolume} pending_legs={pendingOrderIds.Count}"
    );
    if (filledVolume > 0)
    {
      await PublishEventAsync(
        "order_filled",
        $"ORDER FILLED {plan.Analysis.Direction} {filledVolume} "
        + $"@ {entryFillPrice}",
        plan,
        cancellationToken,
        positionId: positionId,
        price: entryFillPrice,
        volume: filledVolume
      );
    }
    if (pendingOrderIds.Count > 0)
    {
      await PublishEventAsync(
        // "order_submitted" is already claimed by the V6 lifecycle as an
        // always-silent event type (never a Telegram card) - v7_ prefixed so
        // this V7 limit/ladder submission gets its own, visible ORDER
        // SUBMITTED card instead of silently colliding with that.
        "v7_order_submitted",
        $"ORDER SUBMITTED {plan.Analysis.Direction} {pendingOrderIds.Count} "
        + $"leg(s) pending",
        plan,
        cancellationToken,
        volume: volumePlan.TotalVolume - filledVolume
      );
    }
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
          await PersistPlanExecutionStateAsync(
            plan.PlanId, "completed", null, cancellationToken
          );
          await ForgetPlanAsync(state.PlanId, cancellationToken);
          continue;
        }
        await PersistPlanExecutionStateAsync(
          plan.PlanId, "managing", null, cancellationToken
        );
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
      // Beyond break-even, the stop must keep ratcheting as later targets
      // close - otherwise a position that ran all the way to TP3/TP4/TP5
      // sits protected at nothing more than BE forever, and a full reversal
      // afterward gives back every pip those later targets banked. Trail to
      // the target two levels behind the one that just closed (never the
      // one just passed - that leaves no room for a normal pullback between
      // levels): after TP3 closes, protect TP1's price; after TP4, TP2's;
      // and so on. Mirrors StopTrailPlanner.Plan's V6 ratchet (same "-2"
      // lag), reworked for V7's absolute per-target prices instead of V6's
      // entry-relative pip ladder - this runtime never had an equivalent
      // step of its own.
      var trailToIndex = state.NextTargetIndex - 3;
      if (trailToIndex >= 0 && trailToIndex < plan.Targets.Count)
      {
        var desired = decimal.Round(
          plan.Targets[trailToIndex].Price, symbol.Digits, MidpointRounding.AwayFromZero
        );
        var improves = plan.Analysis.Direction == "BUY"
          ? desired > state.CurrentStop
          : desired < state.CurrentStop;
        if (improves)
        {
          await client.AmendPositionStopLossAsync(
            state.PositionId!.Value, desired, cancellationToken
          );
          state = state with { CurrentStop = desired };
          await PersistStateAsync(state, cancellationToken);
          log(
            $"v7 stop trailed id={plan.PlanId} stop={desired} "
            + $"to_target={plan.Targets[trailToIndex].TargetId}"
          );
          await PublishEventAsync(
            "sl_moved",
            $"SL MOVED to {desired} (trail {plan.Targets[trailToIndex].TargetId})",
            plan,
            cancellationToken,
            positionId: state.PositionId,
            price: desired
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

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
  // Waiting for activation (e.g. market_watch quote not yet in zone) or
  // finishing a mid-ladder submit after a partial broker failure.
  Received,
  Submitting,
  // Legacy: older persisted JSON may still deserialize as Armed. Treated
  // like Received/Submitting for evaluate-and-submit; new writes never use it.
  Armed,
  Submitted,
  PartiallyOpen,
  FullyOpen,
  // Legacy synonym of FullyOpen — kept so older persisted JSON still
  // deserializes; new writes use FullyOpen / PartiallyOpen.
  Open,
  Closed,
}

public static class TradePlanRuntimeStateSchema
{
  public const int Legacy = 1;
  public const int Current = 2;
}

public static class TradePlanLegStages
{
  public const string Planned = "planned";
  public const string Submitting = "submitting";
  public const string Submitted = "submitted";
  public const string Pending = "pending";
  public const string PartiallyFilled = "partially_filled";
  public const string Filled = "filled";
  public const string Managing = "managing";
  public const string Cancelled = "cancelled";
  public const string Expired = "expired";
  public const string Rejected = "rejected";
  public const string Closed = "closed";
}

public static class TradePlanGroupStages
{
  public const string Received = "received";
  public const string Submitting = "submitting";
  public const string Submitted = "submitted";
  public const string PartiallyOpen = "partially_open";
  public const string FullyOpen = "fully_open";
  public const string Managing = "managing";
  public const string PartiallyClosed = "partially_closed";
  public const string Closed = "closed";
  public const string Rejected = "rejected";
  public const string Expired = "expired";
  public const string Cancelled = "cancelled";
  public const string RecoveryRequired = "recovery_required";
}

public sealed record TradePlanLegRuntimeState(
  string LegId,
  decimal IntendedPrice,
  decimal DeclaredRatio,
  long IntendedVolume,
  decimal IntendedLots,
  string ClientOrderId,
  long? BrokerOrderId = null,
  long? BrokerPositionId = null,
  long? SubmittedAt = null,
  long? FilledAt = null,
  decimal? FillPrice = null,
  long FilledVolume = 0,
  long RemainingVolume = 0,
  bool StopVerified = false,
  string Stage = TradePlanLegStages.Planned,
  string? LastError = null,
  int Revision = 0
);

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
  int SubmittedLegCount = 0,
  int SchemaVersion = TradePlanRuntimeStateSchema.Current,
  IReadOnlyList<TradePlanLegRuntimeState>? Legs = null,
  long TotalIntendedVolume = 0,
  long TotalFilledVolume = 0,
  decimal? GroupWeightedFillPrice = null,
  decimal? GroupAbsoluteStop = null,
  bool GroupStopVerified = false,
  string GroupStage = TradePlanGroupStages.Received,
  string? TerminalReason = null,
  int Revision = 0
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
[JsonSerializable(typeof(TradePlanLegRuntimeState))]
[JsonSerializable(typeof(IReadOnlyList<TradePlanLegRuntimeState>))]
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
    "sizing": {
      "mode": "equity_table",
      "table_version": "owner_equity_v1",
      "entry_distribution": "single",
      "leg_ratios": []
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

  public static TradePlanRuntimeState? DeserializeState(string json)
  {
    var state = JsonSerializer.Deserialize(
      json,
      TradePlanStateJsonContext.Default.TradePlanRuntimeState
    );
    return state is null ? null : MigrateRuntimeState(state);
  }

  /// <summary>
  /// Upgrades pre-Legs runtime snapshots so multi-leg reconcile/manage can
  /// run after a restart. Ambiguous pending-only legacy state is marked
  /// recovery_required rather than guessed.
  /// </summary>
  public static TradePlanRuntimeState MigrateRuntimeState(TradePlanRuntimeState state)
  {
    if (
      state.Legs is { Count: > 0 }
      && state.SchemaVersion >= TradePlanRuntimeStateSchema.Current
    )
    {
      return state.Stage == TradePlanRuntimeStage.Open
        ? state with { Stage = TradePlanRuntimeStage.FullyOpen }
        : state;
    }

    var legs = new List<TradePlanLegRuntimeState>(state.Legs ?? []);
    var groupStage = state.GroupStage;
    var terminalReason = state.TerminalReason;
    var stage = state.Stage == TradePlanRuntimeStage.Open
      ? TradePlanRuntimeStage.FullyOpen
      : state.Stage;

    if (legs.Count == 0 && state.PositionId is long positionId)
    {
      legs.Add(new TradePlanLegRuntimeState(
        LegId: "L1",
        IntendedPrice: state.EntryFillPrice ?? 0m,
        DeclaredRatio: 1m,
        IntendedVolume: state.RemainingVolume,
        IntendedLots: 0m,
        ClientOrderId: TradePlanV7Ownership.FormatClientOrderId(state.PlanId, "L1"),
        BrokerPositionId: positionId,
        FillPrice: state.EntryFillPrice,
        FilledVolume: state.RemainingVolume,
        RemainingVolume: state.RemainingVolume,
        StopVerified: state.GroupStopVerified,
        Stage: TradePlanLegStages.Filled
      ));
      if (
        stage is TradePlanRuntimeStage.Submitted
          or TradePlanRuntimeStage.Armed
          or TradePlanRuntimeStage.Received
          or TradePlanRuntimeStage.Submitting
      )
      {
        stage = TradePlanRuntimeStage.FullyOpen;
      }
      groupStage = string.IsNullOrWhiteSpace(groupStage)
        || groupStage == TradePlanGroupStages.Received
          ? TradePlanGroupStages.FullyOpen
          : groupStage;
    }

    var pending = state.PendingOrderIds ?? [];
    if (
      legs.Count == 0
      && pending.Count > 1
      && legs.All(leg => string.IsNullOrWhiteSpace(leg.ClientOrderId))
    )
    {
      // Multiple pending broker orders with no leg ClientOrderId mapping —
      // cannot safely assign which pending id belongs to which declared leg.
      groupStage = TradePlanGroupStages.RecoveryRequired;
      terminalReason ??= "ambiguous_pending_without_client_order_ids";
    }
    else if (legs.Count == 0 && pending.Count == 1)
    {
      legs.Add(new TradePlanLegRuntimeState(
        LegId: "L1",
        IntendedPrice: 0m,
        DeclaredRatio: 1m,
        IntendedVolume: 0,
        IntendedLots: 0m,
        ClientOrderId: TradePlanV7Ownership.FormatClientOrderId(state.PlanId, "L1"),
        BrokerOrderId: pending[0],
        Stage: TradePlanLegStages.Pending
      ));
      groupStage = TradePlanGroupStages.Submitted;
    }

    var totalFilled = legs.Sum(leg => leg.FilledVolume);
    var remaining = legs.Sum(leg => leg.RemainingVolume);
    var weighted = WeightedFillPrice(legs);

    return state with
    {
      SchemaVersion = TradePlanRuntimeStateSchema.Current,
      Stage = stage,
      Legs = legs,
      TotalFilledVolume = totalFilled > 0 ? totalFilled : state.TotalFilledVolume,
      RemainingVolume = remaining > 0 ? remaining : state.RemainingVolume,
      EntryFillPrice = weighted ?? state.EntryFillPrice,
      GroupWeightedFillPrice = weighted ?? state.GroupWeightedFillPrice,
      GroupStage = groupStage,
      TerminalReason = terminalReason,
      PositionId = state.PositionId
        ?? legs.Select(leg => leg.BrokerPositionId).FirstOrDefault(id => id is not null),
    };
  }

  public static decimal? WeightedFillPrice(
    IReadOnlyList<TradePlanLegRuntimeState> legs
  )
  {
    var filled = legs
      .Where(leg => leg.FillPrice is not null && leg.FilledVolume > 0)
      .ToArray();
    if (filled.Length == 0)
    {
      return null;
    }
    var volume = filled.Sum(leg => leg.FilledVolume);
    if (volume <= 0)
    {
      return null;
    }
    return filled.Sum(leg => leg.FillPrice!.Value * leg.FilledVolume) / volume;
  }

  public static long RelativeStopLossForEntry(
    decimal entryPrice,
    decimal groupAbsoluteStop
  ) => decimal.ToInt64(Math.Abs(entryPrice - groupAbsoluteStop) * 100_000m);

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
  private static string NotifyDedupKey(string planId, string eventKey) =>
    $"auto_trade:v7_notify:{planId}:{eventKey}";
  private static readonly TimeSpan NotifyDedupTtl = TimeSpan.FromDays(7);

  public IReadOnlyCollection<TradePlanRuntimeState> TrackedStates => _statesById.Values;

  /// <summary>
  /// One poll cycle: recover state on first call, claim any newly published
  /// plans and attempt L1+L2 submission in the same cycle when executable,
  /// finish any remaining Received/Submitting/Armed work, then reconcile and
  /// manage open positions. Call once per AutoTradeEngine.RunSessionAsync
  /// loop iteration - see AutoTradeEngine.PollTradePlansAsync.
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
    await EvaluatePendingEntryPlansAsync(client, symbol, quote, cancellationToken);
    await ReconcileSubmittedLegsAsync(client, symbol, cancellationToken);
    await ManageOpenPositionsAsync(client, symbol, quote, cancellationToken);
  }

  /// <summary>
  /// Adopts a broker position whose comment/ClientOrderId carries V7
  /// ownership into the matching tracked plan leg. Returns true when the
  /// position was recognised as V7 ownership (whether or not a live plan
  /// was found) so AutoTradeEngine can skip the av* reconstruct path and
  /// the "cannot reconstruct" spam for V7 comments.
  /// </summary>
  public Task<bool> TryAdoptV7BrokerPositionAsync(
    TradingPosition position,
    CancellationToken cancellationToken
  ) => TryAdoptV7BrokerPositionAsync(
    client: null, symbol: null, position, cancellationToken
  );

  public async Task<bool> TryAdoptV7BrokerPositionAsync(
    ICTraderTradeClient? client,
    SymbolInfo? symbol,
    TradingPosition position,
    CancellationToken cancellationToken
  )
  {
    var ownership = TradePlanV7Ownership.TryParseV7Ownership(
      position.Comment, position.ClientOrderId
    );
    if (ownership is null)
    {
      return false;
    }
    if (!_restored)
    {
      await RestoreAsync(cancellationToken);
      _restored = true;
    }
    if (!_statesById.TryGetValue(ownership.PlanId, out var state))
    {
      log(
        $"v7 adopt: no tracked plan for position={position.PositionId} "
        + $"plan_id={ownership.PlanId} leg={ownership.LegId}"
      );
      return true;
    }
    _plansById.TryGetValue(ownership.PlanId, out var plan);
    var absoluteStop = plan?.Stop.Price
      ?? state.GroupAbsoluteStop
      ?? state.CurrentStop;
    var next = AdoptFilledLeg(
      state,
      ownership.LegId,
      position,
      absoluteStop,
      clock().ToUnixTimeSeconds()
    );
    if (client is not null && symbol is not null)
    {
      next = await AmendAndVerifyLegStopAsync(
        client, symbol, next, ownership.LegId, absoluteStop, cancellationToken
      );
    }
    await PersistStateAsync(next, cancellationToken);
    log(
      $"v7 adopt: position={position.PositionId} plan_id={ownership.PlanId} "
      + $"leg={ownership.LegId} stage={next.Stage}"
    );
    return true;
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
        await ProcessTradePlanEntryAsync(
          entry, client, symbol, cancellationToken
        );
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
      // through to the fresh-receive path below used to unconditionally
      // overwrite whatever real progress had already happened (a
      // submitted/filled order) with a brand-new Received state and
      // risk a duplicate broker submission. A duplicate claim must never
      // look like a normal re-receive: reconcile against whatever evidence
      // exists (in-memory state first, durable Redis state second) and
      // never proceed past this point regardless of what is found.
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
    // Size against the live account before announcing receipt. A sizing
    // failure here is not transient (identical guard exists in
    // EvaluatePendingEntryPlansAsync/SubmitEntryAsync).
    try
    {
      var equity = await ResolveEquityForSizingAsync(client, cancellationToken);
      log(
        $"v7 sizing pre-submit id={plan.PlanId} {EquityResolver.FormatTelemetry(equity)}"
      );
      TradePlanExecutionEngine.CalculateVolume(
        plan, equity, options.PipSize, options.PipValuePerLot, symbol
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
        $"v7 plan sizing rejected pre-submit id={plan.PlanId} "
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
      TradePlanRuntimeStage.Received,
      CurrentStop: plan.Stop.Price,
      GroupStage: TradePlanGroupStages.Received
    );
    await PersistStateAsync(state, cancellationToken);
    await PersistPlanExecutionStateAsync(
      plan.PlanId,
      "received",
      entry.Id,
      cancellationToken
    );
    log(
      "auto_trade_plan_received_ready "
      + $"stream_id={entry.Id} plan_id={plan.PlanId} "
      + $"entry_type={plan.Entry.Type}"
    );
    // Submission happens in EvaluatePendingEntryPlansAsync on this same
    // PollAsync cycle (after the stream-read loop) so broker errors still
    // surface to the caller instead of being swallowed as stream retries.
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

  private async Task<EquityResolution> ResolveEquityForSizingAsync(
    ICTraderTradeClient client,
    CancellationToken cancellationToken
  )
  {
    var account = await client.GetTradingAccountAsync(cancellationToken);
    var positions = await client.ReconcilePositionsAsync(cancellationToken);
    var pending = await client.ReconcilePendingOrdersAsync(cancellationToken);
    return EquityResolver.Resolve(
      account,
      positions.Count,
      pending.Count,
      positions
    );
  }

  private Task PublishEventAsync(
    string type,
    string message,
    TradePlan plan,
    CancellationToken cancellationToken,
    long? positionId = null,
    decimal? price = null,
    int? targetPips = null,
    long? volume = null,
    string? eventKey = null,
    string? previousState = null,
    string? state = null,
    long? remainingVolume = null
  ) => PublishEventCoreAsync(
    type,
    message,
    plan,
    cancellationToken,
    positionId,
    price,
    targetPips,
    volume,
    eventKey,
    previousState,
    state,
    remainingVolume
  );

  private async Task PublishEventCoreAsync(
    string type,
    string message,
    TradePlan plan,
    CancellationToken cancellationToken,
    long? positionId,
    decimal? price,
    int? targetPips,
    long? volume,
    string? eventKey,
    string? previousState,
    string? state,
    long? remainingVolume
  )
  {
    if (!string.IsNullOrWhiteSpace(eventKey))
    {
      var claimed = await store.TryClaimStringAsync(
        NotifyDedupKey(plan.PlanId, eventKey),
        clock().ToUnixTimeSeconds().ToString(),
        NotifyDedupTtl,
        cancellationToken
      );
      if (!claimed)
      {
        log(
          $"v7 notify dedup skipped plan_id={plan.PlanId} event_key={eventKey} type={type}"
        );
        return;
      }
    }
    await store.PublishAutoTradeEventAsync(
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
        StopLoss: plan.Stop.Price,
        GroupId: plan.PlanId,
        PreviousState: previousState,
        State: state,
        RemainingVolume: remainingVolume
      ),
      cancellationToken
    );
  }

  private async Task EvaluatePendingEntryPlansAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    SpotPrice quote,
    CancellationToken cancellationToken
  )
  {
    foreach (var state in _statesById.Values.Where(IsPendingEntryStage).ToArray())
    {
      if (!_plansById.TryGetValue(state.PlanId, out var plan))
      {
        continue;
      }
      await TrySubmitReceivedPlanAsync(
        client, symbol, plan, state, quote, cancellationToken
      );
    }
  }

  private static bool IsPendingEntryStage(TradePlanRuntimeState state) =>
    state.Stage is TradePlanRuntimeStage.Received
      or TradePlanRuntimeStage.Submitting
      or TradePlanRuntimeStage.Armed;

  private async Task TrySubmitReceivedPlanAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    TradePlan plan,
    TradePlanRuntimeState state,
    SpotPrice quote,
    CancellationToken cancellationToken
  )
  {
    // Re-read from the map in case a same-poll submit already progressed.
    if (
      !_statesById.TryGetValue(state.PlanId, out var latest)
      || !IsPendingEntryStage(latest)
    )
    {
      return;
    }
    state = latest;
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
      return;
    }
    if (!decision.ShouldSubmit)
    {
      return;
    }
    if (!ShouldSubmitOrders)
    {
      // shadow_v7 (or DryRun): the plan is valid and would have fired,
      // but per docs/adr-trade-plan-v7-boundary.md shadow mode "places no
      // orders from it". Left Received so the next poll re-evaluates it.
      log(
        $"v7 shadow: would submit id={plan.PlanId} entry_type={plan.Entry.Type}"
      );
      return;
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

  // Kept for older call sites / migration docs; forwards to the pending-entry path.
  private Task EvaluateArmedPlansAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    SpotPrice quote,
    CancellationToken cancellationToken
  ) => EvaluatePendingEntryPlansAsync(client, symbol, quote, cancellationToken);

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
    var equity = await ResolveEquityForSizingAsync(client, cancellationToken);
    log(
      $"v7 sizing submit id={plan.PlanId} {EquityResolver.FormatTelemetry(equity)}"
    );
    var volumePlan = TradePlanExecutionEngine.CalculateVolume(
      plan, equity, options.PipSize, options.PipValuePerLot, symbol
    );
    var direction = plan.Analysis.Direction == "BUY"
      ? TradeDirection.Buy
      : TradeDirection.Sell;
    var now = clock().ToUnixTimeSeconds();
    var absoluteStop = plan.Stop.Price;

    if (plan.Entry.Type == TradePlanContract.EntryTypeMarketWatch)
    {
      const string legId = "L1";
      var entryPrice = direction == TradeDirection.Buy ? quote.Ask : quote.Bid;
      var comment = TradePlanV7Ownership.FormatComment(
        plan.PlanId, plan.ThesisId, legId
      );
      var clientOrderId = TradePlanV7Ownership.FormatClientOrderId(
        plan.PlanId, legId
      );
      var execution = await client.PlaceMarketOrderAsync(
        new MarketOrderRequest(
          symbol.SymbolId,
          direction,
          volumePlan.TotalVolume,
          TradePlanJson.RelativeStopLossForEntry(entryPrice, absoluteStop),
          options.Label,
          comment,
          clientOrderId
        ),
        cancellationToken
      );
      var lots = volumePlan.TotalVolume / (decimal)symbol.LotSize;
      var leg = new TradePlanLegRuntimeState(
        LegId: legId,
        IntendedPrice: entryPrice,
        DeclaredRatio: 1m,
        IntendedVolume: volumePlan.TotalVolume,
        IntendedLots: lots,
        ClientOrderId: clientOrderId,
        BrokerPositionId: execution.PositionId,
        SubmittedAt: now,
        FilledAt: now,
        FillPrice: execution.ExecutionPrice,
        FilledVolume: execution.ExecutedVolume,
        RemainingVolume: execution.ExecutedVolume,
        Stage: TradePlanLegStages.Filled
      );
      var next = AggregateState(
        state with
        {
          Stage = TradePlanRuntimeStage.FullyOpen,
          GroupStage = TradePlanGroupStages.FullyOpen,
          SubmittedLegCount = 1,
          TotalIntendedVolume = volumePlan.TotalVolume,
          GroupAbsoluteStop = absoluteStop,
          CurrentStop = absoluteStop,
          Legs = [leg],
        }
      );
      next = await AmendAndVerifyLegStopAsync(
        client, symbol, next, legId, absoluteStop, cancellationToken
      );
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

    // single_limit, limit_ladder, market_with_limit_scale: submit every
    // declared leg. Explicit order_type (market_with_limit_scale, or
    // limit_ladder legs that declare one) bypasses marketable-limit
    // detection — L1 market / L2 limit must place exactly those order types.
    var declaredLegs = BuildDeclaredLegs(plan, volumePlan, symbol);
    if (
      plan.Entry.Type == TradePlanContract.EntryTypeMarketWithLimitScale
      && declaredLegs.Count == 1
      && (plan.Entry.Legs?.Count ?? 0) > 1
    )
    {
      // Split collapsed (undersized volume). single_market policy → 100% L1 market.
      if (
        !string.Equals(
          options.ReactionScaleInvalidPolicy,
          "single_market",
          StringComparison.OrdinalIgnoreCase
        )
      )
      {
        throw new TradePlanContractException(
          "market_with_limit_scale volume cannot cover both legs"
        );
      }
      declaredLegs =
      [
        declaredLegs[0] with
        {
          OrderType = TradePlanContract.OrderTypeMarket,
          Ratio = 1m,
        },
      ];
    }

    var runtimeLegs = new List<TradePlanLegRuntimeState>(state.Legs ?? []);
    EnsurePlannedLegs(runtimeLegs, declaredLegs, plan);

    // Resume from the first leg not yet durably recorded as submitted -
    // never restart the ladder at leg 0.
    var pendingOrderIds = new List<long>(state.PendingOrderIds ?? []);
    for (var index = state.SubmittedLegCount; index < declaredLegs.Count; index++)
    {
      var declared = declaredLegs[index];
      var existing = runtimeLegs.FirstOrDefault(leg => leg.LegId == declared.LegId);
      if (
        existing is not null
        && (
          existing.BrokerPositionId is not null
          || existing.BrokerOrderId is not null
          || existing.Stage is TradePlanLegStages.Filled
            or TradePlanLegStages.Pending
            or TradePlanLegStages.Submitted
            or TradePlanLegStages.Managing
        )
      )
      {
        // Restart / resume: do not resubmit an already-owned leg.
        state = state with { SubmittedLegCount = index + 1 };
        await PersistStateAsync(
          AggregateState(state with { Legs = runtimeLegs }),
          cancellationToken
        );
        continue;
      }

      var legComment = TradePlanV7Ownership.FormatComment(
        plan.PlanId, plan.ThesisId, declared.LegId
      );
      var legClientOrderId = TradePlanV7Ownership.FormatClientOrderId(
        plan.PlanId, declared.LegId
      );
      var useMarket = ResolveLegUsesMarket(declared, direction, quote);
      // Market legs use the live executable quote for relative SL;
      // resting limits use their declared limit price.
      var relativeEntry = useMarket
        ? (direction == TradeDirection.Buy ? quote.Ask : quote.Bid)
        : declared.Price;
      var relativeStop = TradePlanJson.RelativeStopLossForEntry(
        relativeEntry, absoluteStop
      );

      TradePlanLegRuntimeState updatedLeg;
      if (useMarket)
      {
        var execution = await client.PlaceMarketOrderAsync(
          new MarketOrderRequest(
            symbol.SymbolId,
            direction,
            declared.Volume,
            relativeStop,
            options.Label,
            legComment,
            legClientOrderId
          ),
          cancellationToken
        );
        updatedLeg = new TradePlanLegRuntimeState(
          LegId: declared.LegId,
          IntendedPrice: declared.Price,
          DeclaredRatio: declared.Ratio,
          IntendedVolume: declared.Volume,
          IntendedLots: declared.Lots,
          ClientOrderId: legClientOrderId,
          BrokerPositionId: execution.PositionId,
          SubmittedAt: now,
          FilledAt: now,
          FillPrice: execution.ExecutionPrice,
          FilledVolume: execution.ExecutedVolume,
          RemainingVolume: execution.ExecutedVolume,
          Stage: TradePlanLegStages.Filled
        );
      }
      else
      {
        var orderId = await client.PlaceLimitOrderAsync(
          new LimitOrderRequest(
            symbol.SymbolId,
            direction,
            declared.Volume,
            declared.Price,
            relativeStop,
            options.Label,
            legComment,
            legClientOrderId
          ),
          cancellationToken
        );
        pendingOrderIds.Add(orderId);
        updatedLeg = new TradePlanLegRuntimeState(
          LegId: declared.LegId,
          IntendedPrice: declared.Price,
          DeclaredRatio: declared.Ratio,
          IntendedVolume: declared.Volume,
          IntendedLots: declared.Lots,
          ClientOrderId: legClientOrderId,
          BrokerOrderId: orderId,
          SubmittedAt: now,
          Stage: TradePlanLegStages.Pending
        );
      }

      UpsertLeg(runtimeLegs, updatedLeg);
      state = AggregateState(
        state with
        {
          Legs = runtimeLegs.ToArray(),
          PendingOrderIds = pendingOrderIds,
          SubmittedLegCount = index + 1,
          TotalIntendedVolume = declaredLegs.Sum(leg => leg.Volume),
          GroupAbsoluteStop = absoluteStop,
          CurrentStop = absoluteStop,
          // Stay Submitting until every declared leg has been attempted so
          // the next poll can finish a mid-ladder failure.
          Stage = index + 1 < declaredLegs.Count
            ? TradePlanRuntimeStage.Submitting
            : DeriveRuntimeStage(runtimeLegs),
          GroupStage = index + 1 < declaredLegs.Count
            ? TradePlanGroupStages.Submitting
            : DeriveGroupStage(runtimeLegs),
        }
      );
      if (updatedLeg.BrokerPositionId is not null)
      {
        state = await AmendAndVerifyLegStopAsync(
          client, symbol, state, updatedLeg.LegId, absoluteStop, cancellationToken
        );
        // AmendAndVerify rewrites Legs (StopVerified); keep the local list
        // in sync so the post-loop AggregateState does not clobber it.
        runtimeLegs = (state.Legs ?? []).ToList();
      }
      await PersistStateAsync(state, cancellationToken);
    }

    state = AggregateState(
      state with
      {
        Legs = runtimeLegs.ToArray(),
        PendingOrderIds = pendingOrderIds,
        Stage = DeriveRuntimeStage(runtimeLegs),
        GroupStage = DeriveGroupStage(runtimeLegs),
        TotalIntendedVolume = declaredLegs.Sum(leg => leg.Volume),
        GroupAbsoluteStop = absoluteStop,
      }
    );
    await PersistStateAsync(state, cancellationToken);
    var filledVolume = state.TotalFilledVolume;
    await PersistPlanExecutionStateAsync(
      plan.PlanId,
      pendingOrderIds.Count == 0 ? "filled" : "submitted",
      null,
      cancellationToken
    );
    log(
      $"auto_trade_plan_submitted plan_id={plan.PlanId} legs={declaredLegs.Count} "
      + $"filled_volume={filledVolume} pending_legs={pendingOrderIds.Count}"
    );
    if (filledVolume > 0 && pendingOrderIds.Count == 0)
    {
      await PublishEventAsync(
        "order_filled",
        $"ENTRY GROUP FULLY FILLED {plan.Analysis.Direction} "
        + $"volume={filledVolume} weighted={state.GroupWeightedFillPrice}",
        plan,
        cancellationToken,
        positionId: state.PositionId,
        price: state.GroupWeightedFillPrice,
        volume: filledVolume,
        eventKey: "entry_group_fully_filled",
        state: TradePlanGroupStages.FullyOpen
      );
    }
    else if (filledVolume > 0)
    {
      var filledLegs = (state.Legs ?? [])
        .Where(leg => leg.BrokerPositionId is not null)
        .Select(leg => leg.LegId)
        .ToArray();
      var pendingLegs = (state.Legs ?? [])
        .Where(leg => leg.BrokerOrderId is not null && leg.BrokerPositionId is null)
        .Select(leg => leg.LegId)
        .ToArray();
      await PublishEventAsync(
        "order_filled",
        $"ENTRY {string.Join("/", filledLegs)} FILLED volume={filledVolume} "
        + $"@ {state.GroupWeightedFillPrice}; "
        + $"{string.Join("/", pendingLegs)} still pending",
        plan,
        cancellationToken,
        positionId: state.PositionId,
        price: state.GroupWeightedFillPrice,
        volume: filledVolume,
        eventKey: $"entry_partial_filled_{string.Join("_", filledLegs)}",
        state: TradePlanGroupStages.PartiallyOpen
      );
    }
    if (pendingOrderIds.Count > 0 || filledVolume == 0)
    {
      var legVolumes = (state.Legs ?? [])
        .Select(leg => $"{leg.LegId}={leg.IntendedVolume}")
        .ToArray();
      await PublishEventAsync(
        "v7_order_submitted",
        $"ORDERS SUBMITTED {plan.Analysis.Direction} "
        + $"{string.Join(" ", legVolumes)} "
        + $"(pending={pendingOrderIds.Count})",
        plan,
        cancellationToken,
        volume: state.TotalIntendedVolume,
        eventKey: "orders_submitted",
        state: TradePlanGroupStages.Submitted
      );
    }
  }

  private async Task ReconcileSubmittedLegsAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    CancellationToken cancellationToken
  )
  {
    var candidates = _statesById.Values.Where(NeedsSubmittedReconcile).ToArray();
    if (candidates.Length == 0)
    {
      return;
    }
    var positions = await client.ReconcilePositionsAsync(cancellationToken);
    var pending = await client.ReconcilePendingOrdersAsync(cancellationToken);
    var pendingById = pending.ToDictionary(order => order.OrderId);
    var now = clock().ToUnixTimeSeconds();

    foreach (var initial in candidates)
    {
      if (!_plansById.TryGetValue(initial.PlanId, out var plan))
      {
        continue;
      }
      var state = initial;
      var legs = (state.Legs ?? []).ToList();
      if (legs.Count == 0)
      {
        continue;
      }
      var absoluteStop = plan.Stop.Price;
      var changed = false;

      // Match broker positions onto legs via ownership tokens.
      foreach (var position in positions)
      {
        var ownership = TradePlanV7Ownership.TryParseV7Ownership(
          position.Comment, position.ClientOrderId
        );
        if (ownership is null || ownership.PlanId != state.PlanId)
        {
          continue;
        }
        var before = legs.FirstOrDefault(leg => leg.LegId == ownership.LegId);
        if (before?.BrokerPositionId == position.PositionId
            && before.Stage is TradePlanLegStages.Filled or TradePlanLegStages.Managing)
        {
          continue;
        }
        state = AdoptFilledLeg(state, ownership.LegId, position, absoluteStop, now);
        legs = (state.Legs ?? []).ToList();
        state = await AmendAndVerifyLegStopAsync(
          client, symbol, state, ownership.LegId, absoluteStop, cancellationToken
        );
        legs = (state.Legs ?? []).ToList();
        changed = true;
      }

      // Pending order disappeared without a matched position yet: keep
      // looking; when the position appears above, adopt. If the pending
      // order is still live, refresh BrokerOrderId mapping.
      for (var i = 0; i < legs.Count; i++)
      {
        var leg = legs[i];
        if (leg.BrokerOrderId is not long orderId)
        {
          continue;
        }
        if (pendingById.ContainsKey(orderId))
        {
          continue;
        }
        if (leg.BrokerPositionId is not null)
        {
          // Already adopted via position match; drop the stale pending id.
          if (leg.Stage == TradePlanLegStages.Pending)
          {
            legs[i] = leg with { Stage = TradePlanLegStages.Filled };
            changed = true;
          }
          continue;
        }
        // Order gone and no position yet — leave as pending for another
        // poll; orphan detection is a later Telegram/classification slice.
      }

      var pendingIds = legs
        .Where(leg =>
          leg.BrokerOrderId is not null
          && leg.BrokerPositionId is null
          && pendingById.ContainsKey(leg.BrokerOrderId.Value)
        )
        .Select(leg => leg.BrokerOrderId!.Value)
        .ToArray();
      state = AggregateState(
        state with
        {
          Legs = legs,
          PendingOrderIds = pendingIds,
          GroupAbsoluteStop = absoluteStop,
          Stage = DeriveRuntimeStage(legs),
          GroupStage = DeriveGroupStage(legs),
        }
      );
      if (changed || state.Stage != initial.Stage
          || !Equals(state.PendingOrderIds, initial.PendingOrderIds))
      {
        await PersistStateAsync(state, cancellationToken);
        if (state.Stage is TradePlanRuntimeStage.FullyOpen
            or TradePlanRuntimeStage.PartiallyOpen)
        {
          await PersistPlanExecutionStateAsync(
            plan.PlanId,
            state.Stage == TradePlanRuntimeStage.FullyOpen ? "filled" : "submitted",
            null,
            cancellationToken
          );
        }
        if (
          changed
          && state.Stage == TradePlanRuntimeStage.FullyOpen
          && initial.Stage != TradePlanRuntimeStage.FullyOpen
        )
        {
          await PublishEventAsync(
            "order_filled",
            $"ENTRY GROUP FULLY FILLED {plan.Analysis.Direction} "
            + $"volume={state.TotalFilledVolume} "
            + $"weighted={state.GroupWeightedFillPrice}",
            plan,
            cancellationToken,
            positionId: state.PositionId,
            price: state.GroupWeightedFillPrice,
            volume: state.TotalFilledVolume,
            eventKey: "entry_group_fully_filled",
            state: TradePlanGroupStages.FullyOpen
          );
        }
        else if (
          changed
          && state.Stage == TradePlanRuntimeStage.PartiallyOpen
          && state.TotalFilledVolume > initial.TotalFilledVolume
        )
        {
          var filledLegs = (state.Legs ?? [])
            .Where(leg => leg.BrokerPositionId is not null)
            .Select(leg => leg.LegId)
            .ToArray();
          var pendingLegs = (state.Legs ?? [])
            .Where(leg => leg.BrokerOrderId is not null && leg.BrokerPositionId is null)
            .Select(leg => leg.LegId)
            .ToArray();
          await PublishEventAsync(
            "order_filled",
            $"ENTRY {string.Join("/", filledLegs)} FILLED "
            + $"volume={state.TotalFilledVolume}; "
            + $"{string.Join("/", pendingLegs)} still pending",
            plan,
            cancellationToken,
            positionId: state.PositionId,
            price: state.GroupWeightedFillPrice,
            volume: state.TotalFilledVolume,
            eventKey: $"entry_partial_filled_{string.Join("_", filledLegs)}",
            state: TradePlanGroupStages.PartiallyOpen
          );
        }
      }
    }
  }

  private async Task ManageOpenPositionsAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    SpotPrice quote,
    CancellationToken cancellationToken
  )
  {
    var openStates = _statesById.Values.Where(IsManagingStage).ToArray();
    if (openStates.Length == 0)
    {
      return;
    }
    var positions = await client.ReconcilePositionsAsync(cancellationToken);
    var byId = positions.ToDictionary(p => p.PositionId);
    foreach (var initialState in openStates)
    {
      var state = AggregateState(initialState);
      if (!_plansById.TryGetValue(state.PlanId, out var plan))
      {
        continue;
      }

      var missingHandled = await ClassifyMissingLegsAsync(
        client, plan, state, byId, cancellationToken
      );
      if (missingHandled is not null)
      {
        state = missingHandled;
        if (!_statesById.ContainsKey(state.PlanId))
        {
          // Plan closed / recovery — nothing left to manage this poll.
          continue;
        }
      }

      var openLegs = (state.Legs ?? [])
        .Where(leg =>
          leg.BrokerPositionId is long id
          && byId.ContainsKey(id)
          && leg.RemainingVolume > 0
          && leg.Stage is not TradePlanLegStages.Closed
        )
        .ToArray();
      if (openLegs.Length == 0)
      {
        continue;
      }

      var currentPrice = plan.Analysis.Direction == "BUY" ? quote.Bid : quote.Ask;
      var target = plan.Targets.ElementAtOrDefault(state.NextTargetIndex);
      if (target is not null && TradePlanExecutionEngine.HasReachedTarget(
        plan, target, currentPrice
      ))
      {
        state = await CancelUnfilledEntryLegsAsync(
          client, plan, state, "before_tp", cancellationToken
        );
        openLegs = (state.Legs ?? [])
          .Where(leg =>
            leg.BrokerPositionId is long id
            && byId.ContainsKey(id)
            && leg.RemainingVolume > 0
            && leg.Stage is not TradePlanLegStages.Closed
          )
          .ToArray();
        if (openLegs.Length == 0)
        {
          continue;
        }

        var remainingTargetsSum = plan.Targets
          .Skip(state.NextTargetIndex)
          .Sum(t => t.CloseRatio);
        if (remainingTargetsSum <= 0)
        {
          continue;
        }
        var groupRemaining = openLegs.Sum(leg => leg.RemainingVolume);
        var closeVolume = decimal.ToInt64(
          groupRemaining * target.CloseRatio / remainingTargetsSum
        );
        closeVolume = Math.Min(closeVolume, groupRemaining);
        var allocations = AllocateProRata(
          openLegs.Select(leg => leg.RemainingVolume).ToArray(),
          closeVolume
        );
        var closedTotal = 0L;
        var allSucceeded = true;
        TradeExecution? lastExecution = null;
        var legs = (state.Legs ?? []).ToList();
        var perLegCloses = new List<string>();
        for (var i = 0; i < openLegs.Length; i++)
        {
          var slice = allocations[i];
          if (slice <= 0)
          {
            continue;
          }
          try
          {
            var execution = await client.ClosePositionAsync(
              openLegs[i].BrokerPositionId!.Value, slice, cancellationToken
            );
            lastExecution = execution;
            closedTotal += execution.ExecutedVolume;
            perLegCloses.Add($"{openLegs[i].LegId}={execution.ExecutedVolume}");
            var idx = legs.FindIndex(leg => leg.LegId == openLegs[i].LegId);
            if (idx >= 0)
            {
              var remaining = Math.Max(
                0, legs[idx].RemainingVolume - execution.ExecutedVolume
              );
              legs[idx] = legs[idx] with
              {
                RemainingVolume = remaining,
                Stage = remaining <= 0
                  ? TradePlanLegStages.Closed
                  : TradePlanLegStages.Managing,
              };
            }
          }
          catch (Exception exception)
          {
            allSucceeded = false;
            log(
              $"v7 target close failed id={plan.PlanId} "
              + $"leg={openLegs[i].LegId} message={exception.Message}"
            );
          }
        }
        if (!allSucceeded)
        {
          state = AggregateState(state with { Legs = legs });
          await PersistStateAsync(state, cancellationToken);
          continue;
        }
        var remainingAfter = legs.Sum(leg => leg.RemainingVolume);
        var openCount = legs.Count(leg =>
          leg.BrokerPositionId is not null && leg.RemainingVolume > 0
        );
        var totalLegs = legs.Count(leg => leg.BrokerPositionId is not null);
        log(
          $"v7 target hit id={plan.PlanId} target={target.TargetId} "
          + $"volume={closedTotal} remaining={remainingAfter}"
        );
        var tpMessage = remainingAfter <= 0
          ? $"TP1 COMPLETED {target.TargetId} closed {string.Join(" ", perLegCloses)}; PLAN CLOSED"
          : $"TP1 COMPLETED {target.TargetId} closed {string.Join(" ", perLegCloses)} "
            + $"remaining={remainingAfter} ({openCount}/{Math.Max(totalLegs, 1)})";
        await PublishEventAsync(
          remainingAfter <= 0 ? "position_closed" : "tp_booked",
          tpMessage,
          plan,
          cancellationToken,
          positionId: state.PositionId,
          price: lastExecution?.ExecutionPrice,
          volume: closedTotal,
          eventKey: remainingAfter <= 0
            ? $"tp_completed_{target.TargetId}_closed"
            : $"tp_completed_{target.TargetId}",
          state: remainingAfter <= 0
            ? TradePlanGroupStages.Closed
            : TradePlanGroupStages.PartiallyClosed,
          remainingVolume: remainingAfter
        );
        if (remainingAfter <= 0)
        {
          await PublishEventAsync(
            "position_closed",
            "PLAN CLOSED",
            plan,
            cancellationToken,
            positionId: state.PositionId,
            eventKey: "plan_closed",
            state: TradePlanGroupStages.Closed
          );
        }
        state = AggregateState(
          state with
          {
            Legs = legs,
            NextTargetIndex = state.NextTargetIndex + 1,
            GroupStage = remainingAfter <= 0
              ? TradePlanGroupStages.Closed
              : TradePlanGroupStages.PartiallyClosed,
          }
        );
        await PersistStateAsync(state, cancellationToken);
        if (remainingAfter <= 0)
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
        // Refresh openLegs after TP closes.
        openLegs = legs
          .Where(leg =>
            leg.BrokerPositionId is long id
            && byId.ContainsKey(id)
            && leg.RemainingVolume > 0
          )
          .ToArray();
      }

      var fillPrice = state.GroupWeightedFillPrice ?? state.EntryFillPrice;
      if (
        !state.BreakEvenApplied
        && plan.Management.BeAfterTargetId is not null
        && fillPrice is decimal beFill
        && state.NextTargetIndex > IndexOfTarget(plan, plan.Management.BeAfterTargetId)
      )
      {
        state = await CancelUnfilledEntryLegsAsync(
          client, plan, state, "before_be", cancellationToken
        );
        openLegs = (state.Legs ?? [])
          .Where(leg =>
            leg.BrokerPositionId is long id
            && byId.ContainsKey(id)
            && leg.RemainingVolume > 0
          )
          .ToArray();
        var be = TradePlanExecutionEngine.CalculateBreakEven(
          plan, beFill, state.CurrentStop, symbol
        );
        if (be.Improved && openLegs.Length > 0)
        {
          var beOk = true;
          foreach (var leg in openLegs)
          {
            try
            {
              await client.AmendPositionStopLossAsync(
                leg.BrokerPositionId!.Value, be.NewStop, cancellationToken
              );
            }
            catch (Exception exception)
            {
              beOk = false;
              log(
                $"v7 BE amend failed id={plan.PlanId} leg={leg.LegId} "
                + $"message={exception.Message}"
              );
            }
          }
          if (beOk)
          {
            state = state with { CurrentStop = be.NewStop, BreakEvenApplied = true };
            await PersistStateAsync(state, cancellationToken);
            var n = openLegs.Length;
            log($"v7 stop moved to BE id={plan.PlanId} stop={be.NewStop}");
            await PublishEventAsync(
              "sl_moved",
              $"GROUP SL MOVED TO BE {be.NewStop} ({n}/{n})",
              plan,
              cancellationToken,
              positionId: state.PositionId,
              price: be.NewStop,
              eventKey: "group_sl_moved_to_be"
            );
          }
        }
      }

      var trailToIndex = state.NextTargetIndex - 3;
      if (trailToIndex >= 0 && trailToIndex < plan.Targets.Count)
      {
        state = await CancelUnfilledEntryLegsAsync(
          client, plan, state, "before_trail", cancellationToken
        );
        openLegs = (state.Legs ?? [])
          .Where(leg =>
            leg.BrokerPositionId is long id
            && byId.ContainsKey(id)
            && leg.RemainingVolume > 0
          )
          .ToArray();
        var desired = decimal.Round(
          plan.Targets[trailToIndex].Price, symbol.Digits, MidpointRounding.AwayFromZero
        );
        var improves = plan.Analysis.Direction == "BUY"
          ? desired > state.CurrentStop
          : desired < state.CurrentStop;
        if (improves && openLegs.Length > 0)
        {
          var trailOk = true;
          foreach (var leg in openLegs)
          {
            try
            {
              await client.AmendPositionStopLossAsync(
                leg.BrokerPositionId!.Value, desired, cancellationToken
              );
            }
            catch (Exception exception)
            {
              trailOk = false;
              log(
                $"v7 trail amend failed id={plan.PlanId} leg={leg.LegId} "
                + $"message={exception.Message}"
              );
            }
          }
          if (trailOk)
          {
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
              price: desired,
              eventKey: $"sl_trail_{plan.Targets[trailToIndex].TargetId}"
            );
          }
        }
      }
    }
  }

  private async Task<TradePlanRuntimeState?> ClassifyMissingLegsAsync(
    ICTraderTradeClient client,
    TradePlan plan,
    TradePlanRuntimeState state,
    Dictionary<long, TradingPosition> byId,
    CancellationToken cancellationToken
  )
  {
    var legs = (state.Legs ?? []).ToList();
    var missing = legs
      .Where(leg =>
        leg.BrokerPositionId is long id
        && !byId.ContainsKey(id)
        && leg.RemainingVolume > 0
        && leg.Stage is not TradePlanLegStages.Closed
      )
      .ToArray();
    if (missing.Length == 0)
    {
      return null;
    }

    var now = clock().ToUnixTimeSeconds();
    var reasons = new List<(TradePlanLegRuntimeState Leg, PositionCloseReason Reason)>();
    foreach (var leg in missing)
    {
      var openedAt = leg.FilledAt ?? leg.SubmittedAt ?? now - 3600;
      var lookup = await client.DeterminePositionCloseReasonAsync(
        leg.BrokerPositionId!.Value,
        openedAt,
        now,
        cancellationToken
      );
      reasons.Add((leg, lookup.Reason));
      var idx = legs.FindIndex(item => item.LegId == leg.LegId);
      if (idx >= 0)
      {
        legs[idx] = legs[idx] with
        {
          RemainingVolume = 0,
          Stage = TradePlanLegStages.Closed,
          LastError = lookup.Reason.ToString(),
        };
      }
    }

    var stillOpen = legs.Count(leg =>
      leg.BrokerPositionId is long id
      && byId.ContainsKey(id)
      && leg.RemainingVolume > 0
    );
    var allMissingAreSl = reasons.All(
      item => item.Reason == PositionCloseReason.StopLossOrTakeProfit
    );
    var anyUnknown = reasons.Any(item => item.Reason == PositionCloseReason.Unknown);
    var totalTracked = legs.Count(leg => leg.BrokerPositionId is not null);
    var closedCount = reasons.Count;

    if (anyUnknown)
    {
      var next = AggregateState(
        state with
        {
          Legs = legs,
          GroupStage = TradePlanGroupStages.RecoveryRequired,
          TerminalReason = "unknown_leg_close",
        }
      );
      await PersistStateAsync(next, cancellationToken);
      await PublishEventAsync(
        "warning",
        $"GROUP RECOVERY REQUIRED unknown close on "
        + $"{string.Join(",", reasons.Where(r => r.Reason == PositionCloseReason.Unknown).Select(r => r.Leg.LegId))}",
        plan,
        cancellationToken,
        positionId: state.PositionId,
        eventKey: "recovery_required_unknown_close",
        state: TradePlanGroupStages.RecoveryRequired
      );
      log($"v7 recovery_required id={plan.PlanId} reason=unknown_leg_close");
      return next;
    }

    if (allMissingAreSl && stillOpen > 0)
    {
      var next = AggregateState(
        state with
        {
          Legs = legs,
          GroupStage = TradePlanGroupStages.PartiallyClosed,
        }
      );
      await PersistStateAsync(next, cancellationToken);
      await PublishEventAsync(
        "sl_moved",
        $"GROUP PARTIALLY CLOSED by SL ({closedCount}/{Math.Max(totalTracked, 1)}); "
        + "continuing management",
        plan,
        cancellationToken,
        positionId: state.PositionId,
        eventKey: $"group_partial_sl_{closedCount}",
        state: TradePlanGroupStages.PartiallyClosed
      );
      return next;
    }

    if (allMissingAreSl && stillOpen == 0)
    {
      var next = AggregateState(
        state with
        {
          Legs = legs,
          GroupStage = TradePlanGroupStages.Closed,
          TerminalReason = "group_stop_loss",
          Stage = TradePlanRuntimeStage.Closed,
        }
      );
      await PersistStateAsync(next, cancellationToken);
      await PublishEventAsync(
        "position_closed",
        $"GROUP STOP LOSS ({closedCount}/{Math.Max(totalTracked, 1)})",
        plan,
        cancellationToken,
        positionId: state.PositionId,
        eventKey: "group_stop_loss",
        state: TradePlanGroupStages.Closed
      );
      await PublishEventAsync(
        "position_closed",
        "PLAN CLOSED",
        plan,
        cancellationToken,
        positionId: state.PositionId,
        eventKey: "plan_closed",
        state: TradePlanGroupStages.Closed
      );
      await PersistPlanExecutionStateAsync(
        plan.PlanId, "completed", null, cancellationToken, "group_stop_loss"
      );
      await ForgetPlanAsync(state.PlanId, cancellationToken);
      return next;
    }

    // Manual / external close on one or more legs without a clean SL story —
    // do not guess; require recovery.
    var nextManual = AggregateState(
      state with
      {
        Legs = legs,
        GroupStage = TradePlanGroupStages.RecoveryRequired,
        TerminalReason = "manual_or_external_close",
      }
    );
    await PersistStateAsync(nextManual, cancellationToken);
    await PublishEventAsync(
      "warning",
      "GROUP RECOVERY REQUIRED manual/external close",
      plan,
      cancellationToken,
      positionId: state.PositionId,
      eventKey: "recovery_required_manual_close",
      state: TradePlanGroupStages.RecoveryRequired
    );
    return nextManual;
  }

  private async Task<TradePlanRuntimeState> CancelUnfilledEntryLegsAsync(
    ICTraderTradeClient client,
    TradePlan plan,
    TradePlanRuntimeState state,
    string reason,
    CancellationToken cancellationToken
  )
  {
    if (
      !string.Equals(
        options.UnfilledLegAfterTpPolicy,
        "cancel",
        StringComparison.OrdinalIgnoreCase
      )
    )
    {
      return state;
    }
    var legs = (state.Legs ?? []).ToList();
    var pending = legs
      .Where(leg =>
        leg.BrokerOrderId is not null
        && leg.BrokerPositionId is null
        && leg.Stage is TradePlanLegStages.Pending
          or TradePlanLegStages.Submitted
          or TradePlanLegStages.Planned
      )
      .ToArray();
    if (pending.Length == 0)
    {
      return state;
    }
    foreach (var leg in pending)
    {
      try
      {
        await client.CancelPendingOrderAsync(
          leg.BrokerOrderId!.Value, cancellationToken
        );
        var idx = legs.FindIndex(item => item.LegId == leg.LegId);
        if (idx >= 0)
        {
          legs[idx] = legs[idx] with
          {
            Stage = TradePlanLegStages.Cancelled,
            LastError = reason,
          };
        }
        log(
          $"v7 cancel unfilled leg id={plan.PlanId} leg={leg.LegId} "
          + $"order={leg.BrokerOrderId} reason={reason}"
        );
      }
      catch (Exception exception)
      {
        log(
          $"v7 cancel unfilled leg failed id={plan.PlanId} leg={leg.LegId} "
          + $"message={exception.Message}"
        );
      }
    }
    // Reconcile pending so cancelled orders leave the broker snapshot.
    _ = await client.ReconcilePendingOrdersAsync(cancellationToken);
    var next = AggregateState(
      state with
      {
        Legs = legs,
        PendingOrderIds = legs
          .Where(leg =>
            leg.BrokerOrderId is not null
            && leg.BrokerPositionId is null
            && leg.Stage is not TradePlanLegStages.Cancelled
          )
          .Select(leg => leg.BrokerOrderId!.Value)
          .ToArray(),
      }
    );
    await PersistStateAsync(next, cancellationToken);
    return next;
  }

  private static bool NeedsSubmittedReconcile(TradePlanRuntimeState state) =>
    state.Stage is TradePlanRuntimeStage.Submitted
      or TradePlanRuntimeStage.PartiallyOpen
    || (state.Legs?.Any(leg =>
        leg.Stage is TradePlanLegStages.Pending or TradePlanLegStages.Submitted
        || (leg.BrokerOrderId is not null && leg.BrokerPositionId is null)
      ) ?? false);

  private static bool IsManagingStage(TradePlanRuntimeState state) =>
    (
      state.Stage is TradePlanRuntimeStage.Open
        or TradePlanRuntimeStage.PartiallyOpen
        or TradePlanRuntimeStage.FullyOpen
    )
    && (
      state.PositionId is not null
      || (state.Legs?.Any(leg => leg.BrokerPositionId is not null) ?? false)
    )
    && state.GroupStage != TradePlanGroupStages.RecoveryRequired;

  private static TradePlanRuntimeState AdoptFilledLeg(
    TradePlanRuntimeState state,
    string legId,
    TradingPosition position,
    decimal absoluteStop,
    long filledAt
  )
  {
    var legs = (state.Legs ?? []).ToList();
    var idx = legs.FindIndex(leg => leg.LegId == legId);
    if (idx < 0)
    {
      legs.Add(new TradePlanLegRuntimeState(
        LegId: legId,
        IntendedPrice: position.EntryPrice,
        DeclaredRatio: 0m,
        IntendedVolume: position.Volume,
        IntendedLots: 0m,
        ClientOrderId: position.ClientOrderId,
        BrokerPositionId: position.PositionId,
        FilledAt: filledAt,
        FillPrice: position.EntryPrice,
        FilledVolume: position.Volume,
        RemainingVolume: position.Volume,
        Stage: TradePlanLegStages.Filled
      ));
    }
    else
    {
      var prior = legs[idx];
      legs[idx] = prior with
      {
        BrokerPositionId = position.PositionId,
        BrokerOrderId = prior.BrokerOrderId,
        FilledAt = filledAt,
        FillPrice = position.EntryPrice,
        FilledVolume = position.Volume,
        RemainingVolume = position.Volume,
        Stage = TradePlanLegStages.Filled,
      };
    }
    return AggregateState(
      state with
      {
        Legs = legs,
        GroupAbsoluteStop = absoluteStop,
        CurrentStop = state.CurrentStop == 0 ? absoluteStop : state.CurrentStop,
        Stage = DeriveRuntimeStage(legs),
        GroupStage = DeriveGroupStage(legs),
      }
    );
  }

  private async Task<TradePlanRuntimeState> AmendAndVerifyLegStopAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    TradePlanRuntimeState state,
    string legId,
    decimal absoluteStop,
    CancellationToken cancellationToken
  )
  {
    var legs = (state.Legs ?? []).ToList();
    var idx = legs.FindIndex(leg => leg.LegId == legId);
    if (idx < 0 || legs[idx].BrokerPositionId is not long positionId)
    {
      return state;
    }
    await client.AmendPositionStopLossAsync(
      positionId, absoluteStop, cancellationToken
    );
    var positions = await client.ReconcilePositionsAsync(cancellationToken);
    var position = positions.FirstOrDefault(item => item.PositionId == positionId);
    var tick = StopTrailPlanner.RequireTickSize(symbol);
    var verified = position?.StopLoss is decimal stop
      && Math.Abs(stop - absoluteStop) <= tick;
    legs[idx] = legs[idx] with { StopVerified = verified };
    var allVerified = legs
      .Where(leg => leg.BrokerPositionId is not null && leg.RemainingVolume > 0)
      .All(leg => leg.StopVerified);
    return AggregateState(
      state with
      {
        Legs = legs,
        GroupAbsoluteStop = absoluteStop,
        CurrentStop = absoluteStop,
        GroupStopVerified = allVerified,
      }
    );
  }

  private static TradePlanRuntimeState AggregateState(TradePlanRuntimeState state)
  {
    var legs = state.Legs ?? [];
    var filled = legs.Sum(leg => leg.FilledVolume);
    var remaining = legs.Sum(leg => leg.RemainingVolume);
    var weighted = TradePlanJson.WeightedFillPrice(legs);
    var positionId = legs
      .Select(leg => leg.BrokerPositionId)
      .FirstOrDefault(id => id is not null)
      ?? state.PositionId;
    var pending = legs
      .Where(leg => leg.BrokerOrderId is not null && leg.BrokerPositionId is null)
      .Select(leg => leg.BrokerOrderId!.Value)
      .ToArray();
    return state with
    {
      SchemaVersion = TradePlanRuntimeStateSchema.Current,
      Legs = legs,
      TotalFilledVolume = filled,
      RemainingVolume = remaining,
      EntryFillPrice = weighted ?? state.EntryFillPrice,
      GroupWeightedFillPrice = weighted ?? state.GroupWeightedFillPrice,
      PositionId = positionId,
      PendingOrderIds = pending.Length > 0 ? pending : state.PendingOrderIds,
      // Preserve Submitting while a mid-ladder submit is still in progress so
      // EvaluatePendingEntryPlansAsync keeps finishing remaining legs.
      // Legacy Armed mid-ladder states migrate to Submitting on write.
      Stage = state.Stage is TradePlanRuntimeStage.Submitting
          or TradePlanRuntimeStage.Armed
        ? TradePlanRuntimeStage.Submitting
        : DeriveRuntimeStage(legs),
      GroupStage = state.GroupStage == TradePlanGroupStages.RecoveryRequired
        ? state.GroupStage
        : state.Stage is TradePlanRuntimeStage.Submitting
            or TradePlanRuntimeStage.Armed
          ? TradePlanGroupStages.Submitting
          : DeriveGroupStage(legs),
    };
  }

  private static TradePlanRuntimeStage DeriveRuntimeStage(
    IReadOnlyList<TradePlanLegRuntimeState> legs
  )
  {
    if (legs.Count == 0)
    {
      return TradePlanRuntimeStage.Submitted;
    }
    var open = legs.Count(leg =>
      leg.BrokerPositionId is not null
      && leg.RemainingVolume > 0
      && leg.Stage is not TradePlanLegStages.Closed
    );
    var pending = legs.Count(leg =>
      leg.BrokerPositionId is null
      && (
        leg.BrokerOrderId is not null
        || leg.Stage is TradePlanLegStages.Pending or TradePlanLegStages.Submitted
      )
    );
    if (open > 0 && pending > 0)
    {
      return TradePlanRuntimeStage.PartiallyOpen;
    }
    if (open > 0)
    {
      return TradePlanRuntimeStage.FullyOpen;
    }
    if (pending > 0)
    {
      return TradePlanRuntimeStage.Submitted;
    }
    if (legs.All(leg => leg.Stage == TradePlanLegStages.Closed))
    {
      return TradePlanRuntimeStage.Closed;
    }
    return TradePlanRuntimeStage.Submitted;
  }

  private static string DeriveGroupStage(IReadOnlyList<TradePlanLegRuntimeState> legs)
  {
    var stage = DeriveRuntimeStage(legs);
    return stage switch
    {
      TradePlanRuntimeStage.PartiallyOpen => TradePlanGroupStages.PartiallyOpen,
      TradePlanRuntimeStage.FullyOpen or TradePlanRuntimeStage.Open
        => TradePlanGroupStages.FullyOpen,
      TradePlanRuntimeStage.Submitted => TradePlanGroupStages.Submitted,
      TradePlanRuntimeStage.Closed => TradePlanGroupStages.Closed,
      _ => TradePlanGroupStages.Submitted,
    };
  }

  private sealed record DeclaredLeg(
    string LegId,
    decimal Price,
    decimal Ratio,
    long Volume,
    decimal Lots,
    string? OrderType = null
  );

  private static bool ResolveLegUsesMarket(
    DeclaredLeg declared,
    TradeDirection direction,
    SpotPrice quote
  )
  {
    if (
      string.Equals(
        declared.OrderType,
        TradePlanContract.OrderTypeMarket,
        StringComparison.OrdinalIgnoreCase
      )
    )
    {
      return true;
    }
    if (
      string.Equals(
        declared.OrderType,
        TradePlanContract.OrderTypeLimit,
        StringComparison.OrdinalIgnoreCase
      )
    )
    {
      return false;
    }
    // limit_ladder without explicit order_type: marketable-limit detection.
    return direction == TradeDirection.Buy
      ? declared.Price >= quote.Ask
      : declared.Price <= quote.Bid;
  }

  private static IReadOnlyList<DeclaredLeg> BuildDeclaredLegs(
    TradePlan plan,
    TradePlanVolumePlan volumePlan,
    SymbolInfo symbol
  )
  {
    if (plan.Entry.Type == TradePlanContract.EntryTypeSingleLimit)
    {
      return
      [
        new DeclaredLeg(
          "L1",
          plan.Entry.OrderPrice!.Value,
          1m,
          volumePlan.TotalVolume,
          volumePlan.TotalVolume / (decimal)symbol.LotSize,
          TradePlanContract.OrderTypeLimit
        ),
      ];
    }
    var planLegs = plan.Entry.Legs ?? [];
    // market_with_limit_scale defaults: L1 market, L2+ limit when omitted.
    return planLegs
      .Select((leg, index) => (leg, index))
      .Zip(
        volumePlan.Slices,
        (pair, slice) =>
        {
          var (leg, index) = pair;
          var orderType = leg.OrderType;
          if (
            string.IsNullOrWhiteSpace(orderType)
            && plan.Entry.Type == TradePlanContract.EntryTypeMarketWithLimitScale
          )
          {
            orderType = index == 0
              ? TradePlanContract.OrderTypeMarket
              : TradePlanContract.OrderTypeLimit;
          }
          return new DeclaredLeg(
            string.IsNullOrWhiteSpace(leg.LegId) ? slice.TargetId : leg.LegId,
            leg.Price,
            leg.VolumeRatio,
            slice.Volume,
            slice.Volume / (decimal)symbol.LotSize,
            orderType
          );
        }
      )
      .Select((leg, index) =>
        string.IsNullOrWhiteSpace(leg.LegId)
          ? leg with { LegId = $"L{index + 1}" }
          : leg
      )
      .ToArray();
  }

  private static void EnsurePlannedLegs(
    List<TradePlanLegRuntimeState> runtimeLegs,
    IReadOnlyList<DeclaredLeg> declared,
    TradePlan plan
  )
  {
    foreach (var item in declared)
    {
      if (runtimeLegs.Any(leg => leg.LegId == item.LegId))
      {
        continue;
      }
      runtimeLegs.Add(new TradePlanLegRuntimeState(
        LegId: item.LegId,
        IntendedPrice: item.Price,
        DeclaredRatio: item.Ratio,
        IntendedVolume: item.Volume,
        IntendedLots: item.Lots,
        ClientOrderId: TradePlanV7Ownership.FormatClientOrderId(
          plan.PlanId, item.LegId
        ),
        Stage: TradePlanLegStages.Planned
      ));
    }
  }

  private static void UpsertLeg(
    List<TradePlanLegRuntimeState> legs,
    TradePlanLegRuntimeState updated
  )
  {
    var idx = legs.FindIndex(leg => leg.LegId == updated.LegId);
    if (idx < 0)
    {
      legs.Add(updated);
    }
    else
    {
      legs[idx] = updated with { Revision = legs[idx].Revision + 1 };
    }
  }

  private static long[] AllocateProRata(long[] remaining, long closeVolume)
  {
    var total = remaining.Sum();
    if (total <= 0 || closeVolume <= 0)
    {
      return new long[remaining.Length];
    }
    var raw = remaining
      .Select(volume => (long)Math.Floor(closeVolume * (decimal)volume / total))
      .ToArray();
    var allocated = raw.Sum();
    var leftover = closeVolume - allocated;
    for (var i = 0; leftover > 0 && i < raw.Length; i++)
    {
      var room = remaining[i] - raw[i];
      if (room <= 0)
      {
        continue;
      }
      var add = Math.Min(room, leftover);
      raw[i] += add;
      leftover -= add;
    }
    return raw;
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

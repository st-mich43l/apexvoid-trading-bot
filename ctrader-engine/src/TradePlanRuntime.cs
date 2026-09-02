using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace ApexVoid.CTraderFeed;

/// <summary>
/// Lazily captures one account-wide broker reconcile snapshot for one bound
/// symbol poll. A new instance is created before every per-symbol PollAsync,
/// so failures and pre-mutation data are never retained across symbols or
/// cycles. Sharing that immutable response combines submitted-leg reconcile
/// and open-position management for the symbol while keeping all broker
/// mutations on AutoTradeEngine's existing serialized gate.
/// </summary>
internal sealed class AccountReconcileSnapshotCycle(
  ICTraderTradeClient client
)
{
  private readonly object _sync = new();
  private Task<TradingReconcileSnapshot>? _snapshot;

  public Task<TradingReconcileSnapshot> GetAsync(
    CancellationToken cancellationToken
  )
  {
    cancellationToken.ThrowIfCancellationRequested();
    lock (_sync)
    {
      return _snapshot ??= client.ReconcileAccountAsync(cancellationToken);
    }
  }
}

// TradePlan broker-execution runtime (Sections F-K of the TradePlan V8 cutover):
// consumes execution:trade_plans, arms the exact declared entry, submits
// exactly what Python declared, tracks the broker-confirmed fill/targets/
// stop, and persists enough state to restore after a restart. This is the
// piece TradePlanExecutionEngine.cs's own doc comment named as "not yet
// wired... a later phase" - that phase is this file.
//
// Deliberately a separate file/class from AutoTradeEngine.cs (which still
// legitimately calls ResolveExecutionRoute/StructureStopPlanner for the V6
// path elsewhere in the same class) so TradePlanExecutionEngineDependencyTests
// can scan every TradePlan runtime source file for those forbidden symbols without
// tripping over V6 code that must keep calling them. AutoTradeEngine composes
// this class into its own RunSessionAsync loop (see PollTradePlansAsync)
// rather than this class owning its own session/reconcile/heartbeat loop.

public enum TradePlanRuntimeStage
{
  // Waiting for activation (e.g. market_watch quote not yet in zone) or
  // finishing a mid-ladder submit after a partial broker failure.
  Received,
  Submitting,
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
  public const int Current = 3;
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
  int Revision = 0,
  // Set the first poll a submitted leg's BrokerOrderId is missing from the
  // broker's pending-order snapshot with no matching position yet - either
  // a same-instant fill whose position hasn't landed in this poll's
  // snapshot (self-resolves next poll), or the owner cancelled it directly
  // on the broker platform. Cleared the moment a position or the pending
  // order reappears. Only once this persists across a follow-up poll does
  // ReconcileSubmittedLegsAsync treat it as a real cancel - see the
  // 2026-08-04 incident (a manually-cancelled Flip Zone limit-ladder leg
  // left its plan stuck reporting "submitted" forever, with Telegram never
  // told anything happened).
  long? PendingGoneSinceUnixSeconds = null
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
  // Highest target index that actually closed broker volume (tp_booked / final
  // TP close). Touched-but-deferred targets advance NextTargetIndex without
  // booking; BE must key off this, not NextTargetIndex alone — otherwise a
  // 0.06-lot ladder moves SL to BE after TP1 while closing nothing
  // (prod 2026-08-10: fb13be6e… deferred TP1 desired=120 step=100 then BE).
  int HighestBookedTargetIndex = -1,
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
  int Revision = 0,
  // In-memory only (never persisted to Redis - just a cheap per-poll
  // cache): the reason the last pending-entry poll didn't submit
  // ("outside_zone" / "spread_exceeds_declared_limit"). Wait decisions
  // fire on every poll and are otherwise completely silent, so without
  // this the "v8 plan expired" log line can't say why a market_watch
  // entry never filled even when price genuinely returned to the zone
  // before expiry - see the live incident this fixed.
  string? LastEntryWaitReason = null,
  // In-memory only, same reasoning as LastEntryWaitReason above: set when
  // RestoreAsync grants this plan a recovery grace window (its
  // Entry.ExpiresAt had already lapsed during a restart). While true, a
  // plain "outside_zone" wait also tries the recovery catch-up check
  // (TryRecoveryCatchUpAsync) - live price traded inside the zone while
  // this process was down and left again before recovery finished is
  // otherwise unrecoverable, since a plain live-tick check has no memory
  // of price it never polled.
  bool RecoveryGraceActive = false,
  // Zone midpoint / declared limit used as exposure identity before fill.
  // Python same-direction gates must see Received/Submitted plans, not only
  // FullyOpen fills (live 2026-08-17: two GBPJPY Key Level sells published
  // 5s apart while open_position_count was still 0).
  decimal? IntendedEntryPrice = null
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
    "version":8,
    "plan_id":"v8:self-test",
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

  public static string SerializePlan(TradePlan plan) =>
    JsonSerializer.Serialize(plan, AutoTradeJsonContext.Default.TradePlan);

  public static string SerializeState(TradePlanRuntimeState state) =>
    JsonSerializer.Serialize(
      state,
      TradePlanStateJsonContext.Default.TradePlanRuntimeState
    );

  public static TradePlanRuntimeState? DeserializeState(string json)
  {
    // Completely remove Armed from the runtime: rewrite legacy persisted
    // snapshots before enum deserialization.
    var normalized = json
      .Replace("\"Stage\":\"Armed\"", "\"Stage\":\"Received\"", StringComparison.Ordinal)
      .Replace("\"stage\":\"Armed\"", "\"stage\":\"Received\"", StringComparison.Ordinal);
    var state = JsonSerializer.Deserialize(
      normalized,
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
        ClientOrderId: TradePlanOwnership.FormatClientOrderId(state.PlanId, "L1"),
        BrokerPositionId: positionId,
        FillPrice: state.EntryFillPrice,
        FilledVolume: state.RemainingVolume,
        RemainingVolume: state.RemainingVolume,
        StopVerified: state.GroupStopVerified,
        Stage: TradePlanLegStages.Filled
      ));
      if (
        stage is TradePlanRuntimeStage.Submitted
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
        ClientOrderId: TradePlanOwnership.FormatClientOrderId(state.PlanId, "L1"),
        BrokerOrderId: pending[0],
        Stage: TradePlanLegStages.Pending
      ));
      groupStage = TradePlanGroupStages.Submitted;
    }

    var totalFilled = legs.Sum(leg => leg.FilledVolume);
    var remaining = legs.Sum(leg => leg.RemainingVolume);
    var weighted = WeightedFillPrice(legs);
    // Schema 2→3: recover HighestBookedTargetIndex after restarts. Deferred
    // TP touches advance NextTargetIndex without reducing volume; a real
    // booked partial does. Prefer the pre-migration snapshot volumes so a
    // synthetic single-leg rewrite that copies Remaining into Filled does
    // not erase evidence of earlier closes.
    var highestBooked = InferHighestBookedTargetIndex(state, legs);

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
      HighestBookedTargetIndex = highestBooked,
      PositionId = state.PositionId
        ?? legs.Select(leg => leg.BrokerPositionId).FirstOrDefault(id => id is not null),
    };
  }

  /// <summary>
  /// Schema 2→3 recovery: infer the highest TP that actually closed volume.
  /// NextTargetIndex alone is not enough — deferred undersized shares advance
  /// it without booking. Closed volume is the durable signal.
  /// </summary>
  public static int InferHighestBookedTargetIndex(
    TradePlanRuntimeState state,
    IReadOnlyList<TradePlanLegRuntimeState> legs
  )
  {
    if (state.HighestBookedTargetIndex >= 0)
    {
      return state.HighestBookedTargetIndex;
    }
    var closedFromLegs = legs.Sum(leg =>
      Math.Max(0L, leg.FilledVolume - leg.RemainingVolume)
    );
    var filled = state.TotalFilledVolume > 0
      ? state.TotalFilledVolume
      : legs.Sum(leg => leg.FilledVolume);
    var remaining = state.TotalFilledVolume > 0 || state.RemainingVolume > 0
      ? state.RemainingVolume
      : legs.Sum(leg => leg.RemainingVolume);
    var closed = closedFromLegs > 0
      ? closedFromLegs
      : Math.Max(0L, filled - remaining);
    if (closed <= 0 || state.NextTargetIndex <= 0)
    {
      return -1;
    }
    return state.NextTargetIndex - 1;
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
  Action<string> log,
  Func<string, SymbolInfo?>? resolveBoundSymbol = null,
  Func<string, (decimal PipSize, decimal PipValuePerLot)>? resolveUnits = null
)
{
  private readonly Dictionary<string, TradePlan> _plansById = new();
  private readonly Dictionary<string, TradePlanRuntimeState> _statesById = new();
  private bool _restored;

  // How long a recovered market_watch plan gets to see a live quote after
  // a restart, when its own declared Entry.ExpiresAt already lapsed during
  // the downtime. Deliberately short - this is a fairness grace against
  // dead time the plan never got to use, not a way to keep a stale setup
  // alive; the plan's own thesis (structure/zone) is unchanged, only the
  // deadline the outage silently ate into is restored.
  private const int RestoreExpiryGraceSeconds = 90;

  private static string FormatEventPrice(decimal price, SymbolInfo? symbol)
  {
    var digits = symbol is { Digits: > 0 } ? symbol.Digits : 2;
    return decimal.Round(price, digits, MidpointRounding.AwayFromZero)
      .ToString($"F{digits}", CultureInfo.InvariantCulture);
  }

  private static string FormatEventPrice(decimal? price, SymbolInfo? symbol) =>
    price is decimal value ? FormatEventPrice(value, symbol) : "?";

  private static string FormatEventLot(long volumeUnits, SymbolInfo? symbol)
  {
    // Owner Telegram cards must show strategy lots (0.08), never raw cTrader
    // volume units (800 when LotSize=10000).
    var lotSize = symbol is { LotSize: > 0 } ? symbol.LotSize : 10_000m;
    var lots = (decimal)volumeUnits / lotSize;
    var text = lots.ToString("0.########", CultureInfo.InvariantCulture);
    return string.IsNullOrWhiteSpace(text) ? "0" : text;
  }

  private SymbolInfo BoundSymbol(string planSymbol, SymbolInfo sessionSymbol)
  {
    if (resolveBoundSymbol is null)
    {
      return sessionSymbol;
    }
    return resolveBoundSymbol(planSymbol)
      ?? throw new InvalidOperationException(
        $"no bound broker symbol for plan symbol {planSymbol}"
      );
  }

  private (decimal PipSize, decimal PipValuePerLot) UnitsFor(string planSymbol)
  {
    if (resolveUnits is null)
    {
      return (options.PipSize, options.PipValuePerLot);
    }
    var units = resolveUnits(planSymbol);
    if (units.PipSize <= 0m || units.PipValuePerLot <= 0m)
    {
      throw new InvalidOperationException(
        $"instrument units for {planSymbol} must be positive"
      );
    }
    return units;
  }

  private decimal PipSizeFor(string planSymbol)
  {
    if (resolveUnits is null)
    {
      return options.PipSize;
    }
    var units = resolveUnits(planSymbol);
    return units.PipSize > 0m ? units.PipSize : options.PipSize;
  }

  private static bool SameInstrument(string planSymbol, SymbolInfo session) =>
    CanonicalInstrument(planSymbol) == CanonicalInstrument(session.RedisSymbol)
    || string.Equals(
      planSymbol, session.CTraderSymbol, StringComparison.OrdinalIgnoreCase
    );

  private static bool SameInstrument(string planSymbol, SpotPrice quote) =>
    CanonicalInstrument(planSymbol) == CanonicalInstrument(quote.Symbol);

  private static decimal? ExecutableQuote(TradePlan plan, SpotPrice quote)
  {
    if (!SameInstrument(plan.Symbol, quote))
    {
      return null;
    }
    return plan.Analysis.Direction == "BUY" ? quote.Bid : quote.Ask;
  }

  private static string CanonicalInstrument(string symbol)
  {
    var upper = (symbol ?? "").Trim().ToUpperInvariant();
    return upper is "XAUUSD" or "GOLD" ? "XAU" : upper;
  }

  private static bool SameInstrumentKey(string left, string right) =>
    CanonicalInstrument(left) == CanonicalInstrument(right);

  private static readonly HashSet<string> ScalpFamilies = new(
    StringComparer.OrdinalIgnoreCase
  )
  {
    "scalp", "range", "range_reversion",
  };

  private static readonly HashSet<string> ScalpStrategies = new(
    StringComparer.OrdinalIgnoreCase
  )
  {
    "Range Sweep Scalp",
    "Impulse Pullback Scalp",
    "Breakout Retest Scalp",
    "Momentum Chase Scalp",
    "Range Box Scalp",
    "Range Edge Scalp",
    "One-Sided Range Reaction",
    "Fade Scalp",
    "Chop Zone Reaction",
  };

  private static bool IsScalpPlan(TradePlan plan) =>
    ScalpFamilies.Contains(plan.Analysis.StrategyFamily ?? "")
    || ScalpStrategies.Contains(plan.Analysis.Strategy ?? "");

  private static decimal? IntendedEntryPriceFrom(TradePlan plan)
  {
    try
    {
      var prices = plan.Entry.EntryPrices();
      if (prices.Count == 0)
      {
        return plan.Entry.OrderPrice;
      }
      return prices.Average();
    }
    catch (TradePlanContractException)
    {
      if (plan.Entry.OrderPrice is decimal orderPrice)
      {
        return orderPrice;
      }
      if (plan.Entry.ZoneLow is decimal low && plan.Entry.ZoneHigh is decimal high)
      {
        return (low + high) / 2m;
      }
      return null;
    }
  }

  // Non-scalp may not open a second same-direction plan while another is
  // still pending or open without TP2. Pending was invisible to Python
  // exposure (live 2026-08-17 GBPJPY two Key Level sells at 215.91).
  // Incoming scalps still stack — matching evaluate_entry_against_exposure.
  private bool HasBlockingSameDirectionLivePlan(TradePlan incoming)
  {
    if (IsScalpPlan(incoming))
    {
      return false;
    }
    foreach (var state in _statesById.Values)
    {
      if (string.Equals(state.PlanId, incoming.PlanId, StringComparison.Ordinal))
      {
        continue;
      }
      if (state.Stage is TradePlanRuntimeStage.Closed)
      {
        continue;
      }
      if (!SameInstrumentKey(state.Symbol, incoming.Symbol))
      {
        continue;
      }
      if (!string.Equals(
        state.Direction, incoming.Analysis.Direction,
        StringComparison.OrdinalIgnoreCase
      ))
      {
        continue;
      }
      if (state.HighestBookedTargetIndex >= 1)
      {
        continue;
      }
      return true;
    }
    return false;
  }

  private const decimal ProtectiveStopNearPips = 5m;

  private bool LooksLikeProtectiveStopHit(
    TradePlan plan,
    TradePlanRuntimeState state,
    decimal exitEstimate
  )
  {
    var stop =
      state.CurrentStop != 0 ? state.CurrentStop
      : state.GroupAbsoluteStop ?? plan.Stop.Price;
    if (stop <= 0)
    {
      return false;
    }
    var pipSize = PipSizeFor(plan.Symbol);
    // Live quote after an SL fill often pulls back a few pips (2026-08-28
    // XAU Flip Zone: exit 4604.16 vs stop 4604.47 misread as manual).
    var tolerance = Math.Max(pipSize * ProtectiveStopNearPips, 2m * pipSize);
    return Math.Abs(exitEstimate - stop) <= tolerance;
  }

  // Live dig 2026-08-26 HFS Range Sweep (v8:a80bf164…): L1 SL filled, deal
  // list timed out as Unknown, and the live bid kept printing the sweep past
  // SL 4640.67. Abs-near-stop alone missed it; abs-beyond with still-open L2
  // then raised GROUP RECOVERY REQUIRED and stranded L2. A quote strictly
  // past the protective stop on the loss side is the continuing wick.
  private bool ExitBeyondProtectiveStop(
    TradePlan plan,
    TradePlanRuntimeState state,
    decimal exitEstimate
  )
  {
    var stop =
      state.CurrentStop != 0 ? state.CurrentStop
      : state.GroupAbsoluteStop ?? plan.Stop.Price;
    if (stop <= 0)
    {
      return false;
    }
    return plan.Analysis.Direction == "BUY"
      ? exitEstimate < stop
      : exitEstimate > stop;
  }

  private bool LooksLikeStopOut(
    TradePlan plan,
    TradePlanRuntimeState state,
    decimal exitEstimate
  ) =>
    LooksLikeProtectiveStopHit(plan, state, exitEstimate)
    || ExitBeyondProtectiveStop(plan, state, exitEstimate);

  private PositionCloseReason ClassifyCloseReason(
    TradePlan plan,
    TradePlanRuntimeState state,
    PositionCloseLookup lookup
  )
  {
    if (lookup.Reason != PositionCloseReason.Unknown)
    {
      if (
        lookup.Reason == PositionCloseReason.ManualOrExternalOrder
        && lookup.ExecutionPrice is decimal manualExit
        && LooksLikeProtectiveStopHit(plan, state, manualExit)
      )
      {
        return PositionCloseReason.StopLossOrTakeProfit;
      }
      return lookup.Reason;
    }
    // Promote Unknown → SL/TP only when the deal lookup recovered a real
    // execution price sitting on the protective stop. Defaulting the exit
    // FROM CurrentStop and then comparing to that same stop is a tautology
    // — manual closes near BE/trail would read as stop-outs.
    if (
      lookup.ExecutionPrice is decimal exitPrice
      && LooksLikeStopOut(plan, state, exitPrice)
    )
    {
      return PositionCloseReason.StopLossOrTakeProfit;
    }
    // After TP1 we ourselves moved the stop to BE/trail. cTrader often
    // omits the auto-generated SLTP order from the short OrderList window
    // (see DeterminePositionCloseReasonAsync), so a genuine BE fill arrives
    // as Unknown with no execution price. That is not a tautology: the
    // runner was already protected, and a manual close usually still
    // returns a Market deal + fill.
    if (
      lookup.ExecutionPrice is null
      && (state.BreakEvenApplied || state.HighestBookedTargetIndex >= 0)
    )
    {
      var stop =
        state.CurrentStop != 0 ? state.CurrentStop
        : state.GroupAbsoluteStop ?? 0m;
      if (stop > 0)
      {
        return PositionCloseReason.StopLossOrTakeProfit;
      }
    }
    return PositionCloseReason.Unknown;
  }

  private static string PlanClaimKey(string planId) => $"execution:plan_claim:{planId}";
  private static string PlanStateKey(string planId) => $"execution:plan_runtime:{planId}";
  private static string PlanRecoveryKey(string planId) =>
    $"execution:plan_recovery:{planId}";
  private static string PlanRecoveryGraceKey(string planId) =>
    $"execution:plan_recovery_grace:{planId}";
  private static string PlanExecutorStateKey(string planId) =>
    $"execution:plan_state:{planId}";
  private static string PlanAcknowledgementKey(string planId) =>
    $"execution:plan_ack:{planId}";
  private static string PlanRejectionKey(string streamId) =>
    $"execution:plan_rejection:{streamId}";
  private static string TrackedPlansKey() => "execution:trade_plan_runtime_ids";
  private static string NotifyDedupKey(string planId, string eventKey) =>
    $"auto_trade:v8_notify:{planId}:{eventKey}";
  private static readonly TimeSpan NotifyDedupTtl = TimeSpan.FromDays(7);
  // How long a submitted leg's BrokerOrderId must stay missing from the
  // broker's pending-order snapshot (no matching position either) before
  // ReconcileSubmittedLegsAsync treats it as a real cancel rather than a
  // same-instant fill whose position hasn't landed in this poll yet.
  private const long OrphanPendingOrderConfirmSeconds = 10;

  public IReadOnlyCollection<TradePlanRuntimeState> TrackedStates => _statesById.Values;

  /// <summary>
  /// One poll cycle: recover state on first call, claim any newly published
  /// plans and attempt L1+L2 submission in the same cycle when executable,
  /// finish any remaining Received/Submitting work, then reconcile and
  /// manage open positions. Call once per AutoTradeEngine.RunSessionAsync
  /// loop iteration - see AutoTradeEngine.PollTradePlansAsync.
  /// </summary>
  public async Task PollAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    SpotPrice? quote,
    CancellationToken cancellationToken,
    Func<CancellationToken, Task<TradingReconcileSnapshot>>? reconcileSnapshot = null
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
    await ReconcileSubmittedLegsAsync(
      client, symbol, cancellationToken, reconcileSnapshot
    );
    await ManageOpenPositionsAsync(
      client, symbol, quote, cancellationToken, reconcileSnapshot
    );
  }

  /// <summary>
  /// Adopts a broker position whose comment/ClientOrderId carries TradePlan
  /// V8 ownership into the matching tracked plan leg.
  /// Idempotent: a leg already filled on the same broker position is a no-op
  /// so reconcile ticks do not re-log or re-amend every poll.
  /// </summary>
  public Task<bool> TryAdoptBrokerPositionAsync(
    TradingPosition position,
    CancellationToken cancellationToken
  ) => TryAdoptBrokerPositionAsync(
    client: null, symbol: null, position, cancellationToken
  );

  public async Task<bool> TryAdoptBrokerPositionAsync(
    ICTraderTradeClient? client,
    SymbolInfo? symbol,
    TradingPosition position,
    CancellationToken cancellationToken
  )
  {
    var ownership = TradePlanOwnership.TryParseOwnership(
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
        $"v8 adopt: no tracked plan for position={position.PositionId} "
        + $"plan_id={ownership.PlanId} leg={ownership.LegId}"
      );
      return true;
    }
    _plansById.TryGetValue(ownership.PlanId, out var plan);
    var existingLeg = (state.Legs ?? [])
      .FirstOrDefault(leg => leg.LegId == ownership.LegId);
    if (
      existingLeg is not null
      && existingLeg.BrokerPositionId == position.PositionId
      && existingLeg.Stage is TradePlanLegStages.Filled
        or TradePlanLegStages.Managing
    )
    {
      return true;
    }
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
    if (plan is not null)
    {
      await PublishEntryFillProgressAsync(
        plan, state, next, symbol, cancellationToken
      );
    }
    log(
      $"v8 adopt: position={position.PositionId} plan_id={ownership.PlanId} "
      + $"leg={ownership.LegId} stage={next.Stage}"
    );
    return true;
  }

  private async Task PublishEntryFillProgressAsync(
    TradePlan plan,
    TradePlanRuntimeState previous,
    TradePlanRuntimeState next,
    SymbolInfo? symbol,
    CancellationToken cancellationToken
  )
  {
    if (
      next.Stage == TradePlanRuntimeStage.FullyOpen
      && previous.Stage != TradePlanRuntimeStage.FullyOpen
    )
    {
      await PublishEventAsync(
        "order_filled",
        $"ENTRY GROUP FULLY FILLED {plan.Analysis.Direction} "
        + $"lot={FormatEventLot(next.TotalFilledVolume, symbol)} "
        + $"weighted={FormatEventPrice(next.GroupWeightedFillPrice, symbol)}",
        plan,
        cancellationToken,
        positionId: next.PositionId,
        price: next.GroupWeightedFillPrice,
        volume: next.TotalFilledVolume,
        eventKey: "entry_group_fully_filled",
        state: TradePlanGroupStages.FullyOpen
      );
      return;
    }
    if (next.TotalFilledVolume <= previous.TotalFilledVolume)
    {
      return;
    }
    var filledLegs = (next.Legs ?? [])
      .Where(leg => leg.BrokerPositionId is not null)
      .Select(leg => leg.LegId)
      .ToArray();
    var pendingLegs = (next.Legs ?? [])
      .Where(leg => leg.BrokerOrderId is not null && leg.BrokerPositionId is null)
      .Select(leg => leg.LegId)
      .ToArray();
    var pendingSuffix = pendingLegs.Length == 0
      ? ""
      : $"; {string.Join("/", pendingLegs)} still pending";
    await PublishEventAsync(
      "order_filled",
      $"ENTRY {string.Join("/", filledLegs)} FILLED "
      + $"lot={FormatEventLot(next.TotalFilledVolume, symbol)} "
      + $"@ {FormatEventPrice(next.GroupWeightedFillPrice, symbol)}"
      + pendingSuffix,
      plan,
      cancellationToken,
      positionId: next.PositionId,
      price: next.GroupWeightedFillPrice,
      volume: next.TotalFilledVolume,
      eventKey: $"entry_partial_filled_{string.Join("_", filledLegs)}",
      state: TradePlanGroupStages.PartiallyOpen
    );
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
            var isPendingMarketWatch =
              plan.Entry.Type == TradePlanContract.EntryTypeMarketWatch
              && IsPendingEntryStage(state);
            if (
              isPendingMarketWatch
              && plan.Entry.ExpiresAt <= clock().ToUnixTimeSeconds()
              // Claim-once: a repeatedly-restarting process (the exact
              // situation this exists for) must grant the grace a single
              // time, not reset a fresh window on every restart, or a plan
              // could outlive any real deadline for as long as restarts
              // keep happening. TryClaimStringAsync is atomic (SET NX) -
              // the same idempotency guard PlanClaimKey already uses.
              && await store.TryClaimStringAsync(
                PlanRecoveryGraceKey(planId),
                "1",
                TimeSpan.FromHours(1),
                cancellationToken
              )
            )
            {
              // A deploy/restart is dead time for a market_watch plan: it's
              // evaluated once per poll against the live quote, and nothing
              // is watching while this process is down. A plan recovered
              // here whose Entry.ExpiresAt already lapsed during that gap
              // (TradePlanExecutionEngine.EvaluateEntry's plan_expired
              // check reads exactly this field) would otherwise expire on
              // the very next poll having never once seen a live quote -
              // confirmed live: price traded inside the zone while a
              // restart was in flight, and the plan died without ever
              // getting a real look. Grant one genuine grace window
              // instead so it gets an actual chance post-recovery.
              var grantedUntil = clock().ToUnixTimeSeconds()
                + RestoreExpiryGraceSeconds;
              plan = plan with {
                Entry = plan.Entry with { ExpiresAt = grantedUntil },
              };
              // Persist the extension too, not just the in-memory copy, so
              // a later restart within the same grace window still sees
              // the extended deadline rather than the original stale one.
              await store.SetStringAsync(
                PlanRecoveryKey(planId),
                TradePlanJson.SerializePlan(plan),
                cancellationToken
              );
              log(
                $"v8 restore: granted {RestoreExpiryGraceSeconds}s recovery "
                + $"grace to {planId} (entry expired during downtime)"
              );
            }
            if (isPendingMarketWatch)
            {
              // Broader than the expiry-extension branch above: the
              // "price touched the zone and left again before recovery
              // finished" incident this exists for can happen even when
              // Entry.ExpiresAt hadn't lapsed yet - the plan still had
              // runway, it just had a blind spot while this process was
              // down. Every recovered pending market_watch plan gets the
              // more lenient recovery-aware check on its next poll(s),
              // not only the ones that also needed the deadline extended.
              _statesById[planId] = state with { RecoveryGraceActive = true };
            }
            _plansById[planId] = plan;
          }
        }
        catch (JsonException)
        {
          log($"v8 restore: could not re-parse plan {planId}");
        }
      }
      log($"v8 restore: recovered {planId} at stage {state.Stage}");
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
      exception is not OperationCanceledException
    )
    {
      // Any deserialize/validate failure is a durable contract reject.
      // Narrow filters previously missed source-gen NotSupportedException /
      // FormatException and let PollAsync's outer catch abort the batch,
      // poisoning the next valid stream item until restart.
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
        $"v8 sizing pre-submit id={plan.PlanId} {EquityResolver.FormatTelemetry(equity)}"
      );
      var bound = BoundSymbol(plan.Symbol, symbol);
      var units = UnitsFor(plan.Symbol);
      TradePlanExecutionEngine.CalculateVolume(
        plan, equity, units.PipSize, units.PipValuePerLot, bound
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
        $"TradePlan V8 rejected: {exception.Message}",
        plan,
        cancellationToken
      );
      log(
        $"v8 plan sizing rejected pre-submit id={plan.PlanId} "
        + $"stream_id={entry.Id} "
        + $"exception={exception.GetType().Name} message={exception.Message}"
      );
      return;
    }
    if (HasBlockingSameDirectionLivePlan(plan))
    {
      await PersistPlanExecutionStateAsync(
        plan.PlanId, "rejected", entry.Id, cancellationToken,
        "same_direction_live_plan"
      );
      await PublishEventAsync(
        "plan_rejected",
        "TradePlan V8 rejected: same-direction plan already live on "
          + $"{plan.Symbol} before TP2",
        plan,
        cancellationToken
      );
      log(
        "v8 plan rejected same_direction_live_plan "
          + $"id={plan.PlanId} symbol={plan.Symbol} "
          + $"direction={plan.Analysis.Direction} stream_id={entry.Id}"
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
      GroupStage: TradePlanGroupStages.Received,
      IntendedEntryPrice: IntendedEntryPriceFrom(plan)
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
        $"TradePlan V8 rejected: {record.Message}",
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
    var reconcile = await client.ReconcileAccountAsync(cancellationToken);
    var positions = reconcile.Positions;
    var pending = reconcile.PendingOrders;
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
    long? remainingVolume = null,
    decimal? groupRealizedPips = null,
    string? reasonCode = null,
    TradePlanRuntimeState? runtimeState = null,
    int? highestBookedTargetIndex = null
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
    remainingVolume,
    groupRealizedPips,
    reasonCode,
    runtimeState,
    highestBookedTargetIndex
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
    long? remainingVolume,
    decimal? groupRealizedPips,
    string? reasonCode = null,
    TradePlanRuntimeState? runtimeState = null,
    int? highestBookedTargetIndex = null
  )
  {
    if (!string.IsNullOrWhiteSpace(eventKey))
    {
      var stamp = clock().ToUnixTimeSeconds().ToString();
      var claimed = await store.TryClaimStringAsync(
        NotifyDedupKey(plan.PlanId, eventKey),
        stamp,
        NotifyDedupTtl,
        cancellationToken
      );
      if (!claimed)
      {
        log(
          $"v8 notify dedup skipped plan_id={plan.PlanId} event_key={eventKey} type={type}"
        );
        return;
      }
    }
    bool? breakEvenApplied = null;
    int? bookedIndex = null;
    decimal? plannedRewardRisk = null;
    bool? targetRoomFallbackUsed = null;
    var isTerminalClose = type is "position_closed" or "group_result";
    if (isTerminalClose)
    {
      breakEvenApplied = runtimeState?.BreakEvenApplied;
      bookedIndex = highestBookedTargetIndex
        ?? runtimeState?.HighestBookedTargetIndex;
      plannedRewardRisk = plan.Targets.Count >= 2 ? 2.0m : 1.0m;
      targetRoomFallbackUsed = plan.Targets.Count == 1;
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
        Stream: "algo_auto",
        GroupId: plan.PlanId,
        PreviousState: previousState,
        State: state,
        RemainingVolume: remainingVolume,
        GroupRealizedPips: groupRealizedPips,
        ReasonCode: reasonCode
          ?? (eventKey == "group_stop_loss"
            ? "stop_loss_or_take_profit"
            : null),
        BreakEvenApplied: breakEvenApplied,
        HighestBookedTargetIndex: bookedIndex,
        PlannedRewardRisk: plannedRewardRisk,
        TargetRoomFallbackUsed: targetRoomFallbackUsed
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
      if (!SameInstrument(plan.Symbol, quote))
      {
        continue;
      }
      var bound = BoundSymbol(plan.Symbol, symbol);
      await TrySubmitReceivedPlanAsync(
        client, bound, plan, state, quote, cancellationToken
      );
    }
  }

  private static bool IsPendingEntryStage(TradePlanRuntimeState state) =>
    state.Stage is TradePlanRuntimeStage.Received
      or TradePlanRuntimeStage.Submitting;

  // How far beyond the zone edge, as a FRACTION OF THE ZONE'S OWN WIDTH,
  // the current live quote may still sit and be treated as "close enough"
  // for a recovery catch-up. Scales with the zone's own thesis-derived
  // width rather than a fixed pip amount. Deliberately conservative (half
  // the zone width on each side, not the whole thing) - this exists
  // specifically because a live-tick check alone has no memory of price
  // it never polled while this process was down, and the owner explicitly
  // asked for it after price traded inside a zone entirely during a
  // restart and left again before recovery finished. The real tradeoff:
  // the eventual fill price may differ from wherever price actually was
  // during the missed touch, since only the CURRENT quote is ever used to
  // execute - this never fires an order off stale/historical data alone.
  private const decimal RecoveryCatchUpToleranceFraction = 0.5m;
  // Bars searched for a missed touch - generous relative to how long a
  // restart plausibly takes, without scanning arbitrarily far back.
  private const int RecoveryCatchUpLookbackBars = 120;

  private async Task<bool> TryRecoveryCatchUpAsync(
    TradePlan plan,
    SpotPrice quote,
    CancellationToken cancellationToken
  )
  {
    if (plan.Entry.ZoneLow is not decimal zoneLow || plan.Entry.ZoneHigh is not decimal zoneHigh)
    {
      return false;
    }
    IReadOnlyList<OhlcBar> bars;
    try
    {
      bars = await store.ReadRecentBarsAsync(
        plan.Symbol, "M1", RecoveryCatchUpLookbackBars, cancellationToken
      );
    }
    catch (Exception exception)
    {
      // A telemetry-adjacent read must never take down live entry
      // evaluation - fall back to "no catch-up", the plan keeps waiting
      // exactly as it would have without this feature.
      log(
        $"v8 recovery catch-up read failed id={plan.PlanId} "
        + $"exception={exception.GetType().Name}"
      );
      return false;
    }
    var touchedDuringGap = bars.Any(bar =>
      bar.Timestamp >= plan.CreatedAt
      && bar.Low <= zoneHigh
      && bar.High >= zoneLow
    );
    if (!touchedDuringGap)
    {
      return false;
    }
    var currentQuote = plan.Entry.PriceSide == "ask" ? quote.Ask : quote.Bid;
    var tolerance = (zoneHigh - zoneLow) * RecoveryCatchUpToleranceFraction;
    return currentQuote >= zoneLow - tolerance && currentQuote <= zoneHigh + tolerance;
  }

  private async Task<string> FormatPlanExpiredMessageAsync(
    TradePlan plan,
    string waitReasonForExpiry,
    CancellationToken cancellationToken
  )
  {
    var direction = plan.Analysis.Direction;
    if (waitReasonForExpiry == "never_evaluated")
    {
      return $"TradePlan V8 expired {direction} · "
        + $"executor never evaluated a live quote ({waitReasonForExpiry})";
    }
    if (waitReasonForExpiry == "spread_exceeds_declared_limit")
    {
      return $"TradePlan V8 expired {direction} · "
        + $"spread stayed above the plan limit while waiting ({waitReasonForExpiry})";
    }
    if (waitReasonForExpiry == "slippage_exceeds_declared_limit")
    {
      return $"TradePlan V8 expired {direction} · "
        + $"quote chased past the admitted slippage budget ({waitReasonForExpiry})";
    }
    if (waitReasonForExpiry == "chase_through_target")
    {
      return $"TradePlan V8 expired {direction} · "
        + $"quote already through the first take-profit before entry ({waitReasonForExpiry})";
    }
    if (waitReasonForExpiry == "outside_zone")
    {
      var touched = await ZoneTouchedSinceCreatedAsync(plan, cancellationToken);
      if (touched)
      {
        // Live incident: SETUP FORMING card showed price already inside the
        // entry zone, then expiry said "never returned". Prefer the truthful
        // "left without a fill" copy whenever M1 evidence shows a touch.
        return $"TradePlan V8 expired {direction} · "
          + $"price left the entry zone without a fill ({waitReasonForExpiry})";
      }
      return $"TradePlan V8 expired {direction} · "
        + $"price never entered the entry zone ({waitReasonForExpiry})";
    }
    return $"TradePlan V8 expired {direction} · "
      + $"entry wait timed out ({waitReasonForExpiry})";
  }

  private async Task<bool> ZoneTouchedSinceCreatedAsync(
    TradePlan plan,
    CancellationToken cancellationToken
  )
  {
    if (plan.Entry.ZoneLow is not decimal zoneLow || plan.Entry.ZoneHigh is not decimal zoneHigh)
    {
      return false;
    }
    try
    {
      var bars = await store.ReadRecentBarsAsync(
        plan.Symbol, "M1", RecoveryCatchUpLookbackBars, cancellationToken
      );
      return bars.Any(bar =>
        bar.Timestamp >= plan.CreatedAt
        && bar.Low <= zoneHigh
        && bar.High >= zoneLow
      );
    }
    catch (Exception exception)
    {
      log(
        $"v8 expiry touch check failed id={plan.PlanId} "
        + $"exception={exception.GetType().Name}"
      );
      return false;
    }
  }

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
      plan,
      quote.Bid,
      quote.Ask,
      spreadTicks,
      now,
      StopTrailPlanner.RequireTickSize(symbol)
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
      var waitReasonForExpiry = state.LastEntryWaitReason ?? "never_evaluated";
      // Every other terminal transition in this file (plan_rejected,
      // order_filled, position_closed, ...) publishes an event so Python
      // can resolve the forming card and the owner learns what happened.
      // This branch used to just log and forget the plan - the owner had
      // no way to tell a market_watch setup died from a stale "SETUP
      // FORMING" card short of noticing on their own and asking why.
      var expiryMessage = await FormatPlanExpiredMessageAsync(
        plan, waitReasonForExpiry, cancellationToken
      );
      await PublishEventAsync(
        "plan_expired",
        expiryMessage,
        plan,
        cancellationToken
      );
      await ForgetPlanAsync(state.PlanId, cancellationToken);
      log(
        $"v8 plan expired id={state.PlanId} "
        + $"last_wait_reason={waitReasonForExpiry}"
      );
      return;
    }
    if (!decision.ShouldSubmit)
    {
      var caughtUp = false;
      // Apply M1 missed-touch catch-up for ANY pending market_watch wait
      // on outside_zone, not only RecoveryGraceActive. Polling can miss a
      // brief overlap even without a restart (live 2026-08-05: zone
      // overlapped on M1 for ~2 minutes while last_wait_reason ended as
      // outside_zone and no order fired).
      if (
        decision.RejectReason == "outside_zone"
        && (
          plan.Entry.MaxSpreadTicks is not int maxSpread
          || spreadTicks <= maxSpread
        )
      )
      {
        caughtUp = await TryRecoveryCatchUpAsync(plan, quote, cancellationToken);
      }
      if (!caughtUp)
      {
        if (decision.RejectReason is string waitReason)
        {
          if (!string.Equals(state.LastEntryWaitReason, waitReason, StringComparison.Ordinal))
          {
            var zoneLow = plan.Entry.ZoneLow?.ToString(CultureInfo.InvariantCulture) ?? "-";
            var zoneHigh = plan.Entry.ZoneHigh?.ToString(CultureInfo.InvariantCulture) ?? "-";
            log(
              $"v8 entry wait id={plan.PlanId} reason={waitReason} "
              + $"bid={quote.Bid.ToString(CultureInfo.InvariantCulture)} "
              + $"ask={quote.Ask.ToString(CultureInfo.InvariantCulture)} "
              + $"spread_ticks={spreadTicks.ToString(CultureInfo.InvariantCulture)} "
              + $"zone={zoneLow}-{zoneHigh}"
            );
          }
          _statesById[state.PlanId] = state with { LastEntryWaitReason = waitReason };
        }
        return;
      }
      log(
        $"v8 zone catch-up: id={plan.PlanId} submitting - the zone "
        + "was touched on M1 and the live quote is still close"
      );
      state = state with { RecoveryGraceActive = false };
    }
    if (!ShouldSubmitOrders)
    {
      // DryRun / non-submitting contract mode: the plan is valid and would
      // have fired, but places no orders. Left Received so the next poll
      // re-evaluates it.
      log(
        $"v8 shadow: would submit id={plan.PlanId} entry_type={plan.Entry.Type}"
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
        $"TradePlan V8 rejected: {exception.Message}",
        plan,
        cancellationToken
      );
      await ForgetPlanAsync(state.PlanId, cancellationToken);
      log(
        $"v8 plan sizing rejected id={state.PlanId} "
        + $"exception={exception.GetType().Name} message={exception.Message}"
      );
    }
  }

  // EvaluateArmedPlansAsync removed — Armed is not part of the runtime.

  private bool ShouldSubmitOrders =>
    options.ContractMode is "v8_only"
    && !options.DryRun;

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
      $"v8 sizing submit id={plan.PlanId} {EquityResolver.FormatTelemetry(equity)}"
    );
    var units = UnitsFor(plan.Symbol);
    var volumePlan = TradePlanExecutionEngine.CalculateVolume(
      plan, equity, units.PipSize, units.PipValuePerLot, symbol
    );
    var direction = plan.Analysis.Direction == "BUY"
      ? TradeDirection.Buy
      : TradeDirection.Sell;
    var now = clock().ToUnixTimeSeconds();
    var absoluteStop = plan.Stop.Price;

    if (
      plan.Entry.Type is TradePlanContract.EntryTypeMarket
        or TradePlanContract.EntryTypeMarketWatch
    )
    {
      const string legId = "L1";
      var entryPrice = direction == TradeDirection.Buy ? quote.Ask : quote.Bid;
      var comment = TradePlanOwnership.FormatComment(
        plan.PlanId, plan.ThesisId, legId
      );
      var clientOrderId = TradePlanOwnership.FormatClientOrderId(
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
        $"ORDER FILLED {plan.Analysis.Direction} "
        + $"lot={FormatEventLot(execution.ExecutedVolume, symbol)} "
        + $"@ {FormatEventPrice(execution.ExecutionPrice, symbol)}",
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

      var legComment = TradePlanOwnership.FormatComment(
        plan.PlanId, plan.ThesisId, declared.LegId
      );
      var legClientOrderId = TradePlanOwnership.FormatClientOrderId(
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
        + $"lot={FormatEventLot(filledVolume, symbol)} "
        + $"weighted={FormatEventPrice(state.GroupWeightedFillPrice, symbol)}",
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
        $"ENTRY {string.Join("/", filledLegs)} FILLED lot={FormatEventLot(filledVolume, symbol)} "
        + $"@ {FormatEventPrice(state.GroupWeightedFillPrice, symbol)}; "
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
        "v8_order_submitted",
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
    CancellationToken cancellationToken,
    Func<CancellationToken, Task<TradingReconcileSnapshot>>? reconcileSnapshot
  )
  {
    // _statesById is account-wide. Filter to this bound runtime before the
    // lazy snapshot is touched, otherwise every idle FX target repeats a
    // full account reconcile merely because XAU has a submitted plan.
    var candidates = _statesById.Values
      .Where(state =>
        NeedsSubmittedReconcile(state)
        && _plansById.TryGetValue(state.PlanId, out var plan)
        && SameInstrument(plan.Symbol, symbol)
      )
      .ToArray();
    if (candidates.Length == 0)
    {
      return;
    }
    var reconcile = reconcileSnapshot is null
      ? await client.ReconcileAccountAsync(cancellationToken)
      : await reconcileSnapshot(cancellationToken);
    var positions = reconcile.Positions;
    var pending = reconcile.PendingOrders;
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
        var ownership = TradePlanOwnership.TryParseOwnership(
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
          if (leg.PendingGoneSinceUnixSeconds is not null)
          {
            legs[i] = leg with { PendingGoneSinceUnixSeconds = null };
            changed = true;
          }
          continue;
        }
        if (leg.BrokerPositionId is not null)
        {
          // Already adopted via position match; drop the stale pending id.
          if (leg.Stage == TradePlanLegStages.Pending)
          {
            legs[i] = leg with
            {
              Stage = TradePlanLegStages.Filled,
              PendingGoneSinceUnixSeconds = null,
            };
            changed = true;
          }
          continue;
        }
        // Order gone, no matching position: either a same-instant fill
        // whose position hasn't landed in this poll's snapshot yet (self-
        // resolves next poll via the position-match loop above), or the
        // owner cancelled it directly on the broker (23 Aug incident: a
        // manually-cancelled Flip Zone limit-ladder leg left the plan
        // stuck reporting "submitted" forever - Telegram was never told).
        // Require the gap to survive one more poll before treating it as a
        // real cancel, so a fill's position has a full cycle to appear.
        if (leg.PendingGoneSinceUnixSeconds is null)
        {
          legs[i] = leg with { PendingGoneSinceUnixSeconds = now };
          changed = true;
          continue;
        }
        if (now - leg.PendingGoneSinceUnixSeconds.Value < OrphanPendingOrderConfirmSeconds)
        {
          continue;
        }
        legs[i] = leg with
        {
          Stage = TradePlanLegStages.Cancelled,
          BrokerOrderId = null,
          PendingGoneSinceUnixSeconds = null,
        };
        changed = true;
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
        if (changed)
        {
          await PublishEntryFillProgressAsync(
            plan, initial, state, symbol, cancellationToken
          );
        }
        if (
          changed
          && state.GroupStage == TradePlanGroupStages.Cancelled
          && initial.GroupStage != TradePlanGroupStages.Cancelled
        )
        {
          // No leg of this plan ever reached a broker position - the owner
          // cancelled the pending order(s) directly on the broker platform
          // (see the OrphanPendingOrderConfirmSeconds comment above). Tell
          // Telegram so the forming card reflects reality instead of
          // sitting on "SETUP FORMING" / "submitted" forever, and stop
          // tracking a plan with nothing left to do.
          await PublishEventAsync(
            "plan_cancelled",
            $"TradePlan V8 cancelled {plan.Analysis.Direction} · "
              + "owner cancelled the pending order on the broker",
            plan,
            cancellationToken,
            eventKey: "plan_cancelled",
            state: TradePlanGroupStages.Cancelled
          );
          log($"v8 plan cancelled id={state.PlanId} reason=owner_cancelled_on_broker");
          await ForgetPlanAsync(state.PlanId, cancellationToken);
        }
      }
    }
  }

  private async Task ManageOpenPositionsAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    SpotPrice quote,
    CancellationToken cancellationToken,
    Func<CancellationToken, Task<TradingReconcileSnapshot>>? reconcileSnapshot
  )
  {
    // As above, do not trigger an account snapshot for an idle runtime just
    // because another symbol owns an open plan in this shared state table.
    var openStates = _statesById.Values
      .Where(state =>
        IsManagingStage(state)
        && _plansById.TryGetValue(state.PlanId, out var plan)
        && SameInstrument(plan.Symbol, symbol)
        && SameInstrument(plan.Symbol, quote)
      )
      .ToArray();
    if (openStates.Length == 0)
    {
      return;
    }
    var reconcile = reconcileSnapshot is null
      ? await client.ReconcileAccountAsync(cancellationToken)
      : await reconcileSnapshot(cancellationToken);
    var positions = reconcile.Positions;
    var byId = positions.ToDictionary(p => p.PositionId);
    foreach (var initialState in openStates)
    {
      var state = AggregateState(initialState);
      if (!_plansById.TryGetValue(state.PlanId, out var plan))
      {
        continue;
      }
      var missingHandled = await ClassifyMissingLegsAsync(
        client, symbol, plan, state, byId, quote, cancellationToken
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
      decimal? fillPrice;
      if (state.GroupWeightedFillPrice is decimal weightedFill)
      {
        fillPrice = weightedFill;
      }
      else
      {
        fillPrice = state.EntryFillPrice;
        if (
          fillPrice is not null
          && plan.Management.BeAfterTargetId is not null
          && !state.BreakEvenApplied
        )
        {
          log(
            $"v8 BE fill fallback to EntryFillPrice id={plan.PlanId} "
            + $"fill={fillPrice} (GroupWeightedFillPrice unavailable)"
          );
        }
      }
      var target = plan.Targets.ElementAtOrDefault(state.NextTargetIndex);
      if (target is not null && TradePlanExecutionEngine.HasReachedTarget(
        plan, target, currentPrice
      ))
      {
        // Live 2026-08-24 HFS SELL: fill @ 4667.17 already through TP1
        // 4667.29 → next poll booked "TP1 +1 pip" on a losing close. Skip
        // targets that are not beyond the broker fill, and never close
        // unless the live exit quote is actually favorable vs fill.
        if (
          fillPrice is not decimal confirmedFill
          || !TradePlanExecutionEngine.TargetIsBeyondFill(
            plan.Analysis.Direction,
            confirmedFill,
            target.Price
          )
        )
        {
          log(
            $"v8 target skipped past fill id={plan.PlanId} "
            + $"target={target.TargetId} fill={fillPrice} "
            + $"target_price={target.Price} quote={currentPrice}"
          );
          state = AggregateState(
            state with { NextTargetIndex = state.NextTargetIndex + 1 }
          );
          await PersistStateAsync(state, cancellationToken);
          continue;
        }
        if (
          !TradePlanExecutionEngine.ExitIsFavorableVsFill(
            plan.Analysis.Direction,
            confirmedFill,
            currentPrice
          )
        )
        {
          continue;
        }
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
        // Recalculate from the live filled remainder only — cancelled
        // unfilled L2/etc. must not keep the original ladder total in the
        // close math. Then snap to broker StepVolume.
        var groupRemaining = openLegs.Sum(leg => leg.RemainingVolume);
        var isFinalTarget = state.NextTargetIndex >= plan.Targets.Count - 1;
        var desiredClose = decimal.ToInt64(
          groupRemaining * target.CloseRatio / remainingTargetsSum
        );
        // Owner's call: book by the plan's actual declared % share, even on
        // a small partially-filled ladder - never force a non-final target
        // up to a size it didn't earn just to book *something*. But a share
        // under two broker steps (eg. a single 0.01 lot on an 0.08 lot
        // position) still isn't a meaningful booking on its own - defer it
        // the same way a sub-one-step share already deferred, just with a
        // higher bar (2026-08-07: raised from one step to two after a live
        // 0.08 lot position kept booking bare single-step TPs). The final
        // target still closes whatever is left regardless of size - that
        // floor belongs to the *last* target only.
        var minimumMeaningfulClose = checked(2 * symbol.StepVolume);
        var closeVolume = !isFinalTarget && desiredClose < minimumMeaningfulClose
          ? 0
          : VolumePlanner.PlanPartialCloseVolume(
              groupRemaining,
              desiredClose,
              symbol,
              isFinalTarget
            );
        if (closeVolume <= 0)
        {
          // Cannot book a broker-valid partial against the filled remainder
          // (e.g. one StepVolume position vs a 20% TP). Advance past just
          // this one target - jumping straight to Targets.Count - 1 used to
          // live here, but that corrupts NextTargetIndex for every target
          // still ahead of price: the trail-stop step below reads
          // `NextTargetIndex - 3` assuming NextTargetIndex tracks genuinely
          // reached targets, so a premature jump makes it compute a stop
          // price from a target the market hasn't reached yet, and the
          // broker then rejects that amend on every poll forever
          // (TRADING_BAD_STOPS) - trading one infinite-retry log spam for
          // another, and leaving the position's stop stuck at BE instead of
          // trailing. Advancing by one still avoids re-retrying this same
          // unclosable target (HasReachedTarget only fires again once price
          // reaches the next index), without skipping targets price hasn't
          // touched. Now the normal path for any non-final target whose
          // declared % share of a small partially-filled remainder rounds
          // under two broker steps - deferred honestly to whichever later
          // target's cumulative share clears that bar, or to the final
          // target, which always closes what's left regardless of size.
          log(
            $"v8 target partial deferred id={plan.PlanId} "
            + $"target={target.TargetId} remaining={groupRemaining} "
            + $"desired={desiredClose} step={symbol.StepVolume} "
            + "reason=share_below_minimum_meaningful_close"
          );
          state = AggregateState(
            state with { NextTargetIndex = state.NextTargetIndex + 1 }
          );
          await PersistStateAsync(state, cancellationToken);
          continue;
        }
        var allocations = VolumePlanner.AllocateProRataStepped(
          openLegs.Select(leg => leg.RemainingVolume).ToArray(),
          closeVolume,
          symbol
        );
        if (allocations.Sum() <= 0)
        {
          // Same NextTargetIndex-corruption hazard as the closeVolume <= 0
          // branch above - advance past only this target, not to the end.
          log(
            $"v8 target partial skipped id={plan.PlanId} "
            + $"target={target.TargetId} remaining={groupRemaining} "
            + "reason=stepped_allocation_empty"
          );
          state = AggregateState(
            state with { NextTargetIndex = state.NextTargetIndex + 1 }
          );
          await PersistStateAsync(state, cancellationToken);
          continue;
        }
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
            perLegCloses.Add(
              $"{openLegs[i].LegId} lot={FormatEventLot(execution.ExecutedVolume, symbol)}"
            );
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
              $"v8 target close failed id={plan.PlanId} "
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
        var bookedTargetIndex = Math.Max(
          state.HighestBookedTargetIndex,
          IndexOfTarget(plan, target.TargetId)
        );
        log(
          $"v8 target hit id={plan.PlanId} target={target.TargetId} "
          + $"lot={closedTotal} remaining={remainingAfter}"
        );
        string tpMessage;
        if (remainingAfter <= 0)
        {
          // Final close: report highest TP archived only — no per-leg dump.
          tpMessage =
            $"PLAN CLOSED · highest TP archived {target.TargetId}";
        }
        else
        {
          tpMessage =
            $"TP COMPLETED {target.TargetId} closed {string.Join(" ", perLegCloses)} "
            + $"remaining lot={FormatEventLot(remainingAfter, symbol)} "
            + $"({openCount}/{Math.Max(totalLegs, 1)})";
        }
        await PublishEventAsync(
          remainingAfter <= 0 ? "position_closed" : "tp_booked",
          tpMessage,
          plan,
          cancellationToken,
          positionId: state.PositionId,
          price: lastExecution?.ExecutionPrice,
          targetPips: ArchivedTargetPips(plan, state, target.TargetId),
          volume: closedTotal,
          eventKey: remainingAfter <= 0
            ? $"tp_completed_{target.TargetId}_closed"
            : $"tp_completed_{target.TargetId}",
          state: remainingAfter <= 0
            ? TradePlanGroupStages.Closed
            : TradePlanGroupStages.PartiallyClosed,
          remainingVolume: remainingAfter,
          runtimeState: state,
          highestBookedTargetIndex: bookedTargetIndex
        );
        state = AggregateState(
          state with
          {
            Legs = legs,
            NextTargetIndex = state.NextTargetIndex + 1,
            HighestBookedTargetIndex = bookedTargetIndex,
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

      var beAfterIndex = plan.Management.BeAfterTargetId is null
        ? -1
        : IndexOfTarget(plan, plan.Management.BeAfterTargetId);
      if (
        !state.BreakEvenApplied
        && beAfterIndex >= 0
        && fillPrice is decimal beFill
        // Require a real booked TP at/after BeAfterTargetId. Advancing
        // NextTargetIndex on deferred undersized shares must not move BE.
        && state.HighestBookedTargetIndex >= beAfterIndex
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
                $"v8 BE amend failed id={plan.PlanId} leg={leg.LegId} "
                + $"message={exception.Message}"
              );
            }
          }
          if (beOk)
          {
            state = state with { CurrentStop = be.NewStop, BreakEvenApplied = true };
            await PersistStateAsync(state, cancellationToken);
            var n = openLegs.Length;
            log($"v8 stop moved to BE id={plan.PlanId} stop={be.NewStop}");
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

      var trailToIndex = TradePlanExecutionEngine.ResolveTrailTargetIndex(
        plan,
        state.NextTargetIndex,
        state.HighestBookedTargetIndex
      );
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
                $"v8 trail amend failed id={plan.PlanId} leg={leg.LegId} "
                + $"message={exception.Message}"
              );
            }
          }
          if (trailOk)
          {
            state = state with { CurrentStop = desired };
            await PersistStateAsync(state, cancellationToken);
            log(
              $"v8 stop trailed id={plan.PlanId} stop={desired} "
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
    SymbolInfo symbol,
    TradePlan plan,
    TradePlanRuntimeState state,
    Dictionary<long, TradingPosition> byId,
    SpotPrice quote,
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
    var reasons = new List<(TradePlanLegRuntimeState Leg, PositionCloseReason Reason, decimal? ExitPrice)>();
    foreach (var leg in missing)
    {
      var openedAt = leg.FilledAt ?? leg.SubmittedAt ?? now - 3600;
      var lookup = await client.DeterminePositionCloseReasonAsync(
        leg.BrokerPositionId!.Value,
        openedAt,
        now,
        cancellationToken
      );
      var reason = ClassifyCloseReason(plan, state, lookup);
      decimal? exit = lookup.ExecutionPrice;
      if (
        exit is null
        && reason == PositionCloseReason.StopLossOrTakeProfit
      )
      {
        var stop =
          state.CurrentStop != 0 ? state.CurrentStop
          : state.GroupAbsoluteStop ?? plan.Stop.Price;
        if (stop > 0)
        {
          exit = stop;
        }
      }
      reasons.Add((leg, reason, exit));
      var idx = legs.FindIndex(item => item.LegId == leg.LegId);
      if (idx >= 0)
      {
        legs[idx] = legs[idx] with
        {
          RemainingVolume = 0,
          Stage = TradePlanLegStages.Closed,
          LastError = reason.ToString(),
        };
      }
    }

    var stillOpen = legs.Count(leg =>
      leg.BrokerPositionId is long id
      && byId.ContainsKey(id)
      && leg.RemainingVolume > 0
    );
    var previousGroupStage = state.GroupStage;

    // Promote Unknown legs with no deal fill using the live quote (near or
    // past the protective stop). Applies to full-group and partial (L1 gone,
    // L2 still open) so market_with_limit_scale SL hits do not stall in
    // recovery_required and strand the remaining legs.
    var liveExitHint = ExecutableQuote(plan, quote);
    if (liveExitHint is decimal liveExitForPromote)
    {
      var stopForExit =
        state.CurrentStop != 0 ? state.CurrentStop
        : state.GroupAbsoluteStop ?? plan.Stop.Price;
      for (var i = 0; i < reasons.Count; i++)
      {
        var item = reasons[i];
        if (
          item.Reason != PositionCloseReason.Unknown
          || item.ExitPrice is not null
        )
        {
          continue;
        }
        if (!LooksLikeStopOut(plan, state, liveExitForPromote))
        {
          continue;
        }
        var exit = stopForExit > 0
          && ExitBeyondProtectiveStop(plan, state, liveExitForPromote)
            ? stopForExit
            : liveExitForPromote;
        reasons[i] = (item.Leg, PositionCloseReason.StopLossOrTakeProfit, exit);
        var legIdx = legs.FindIndex(leg => leg.LegId == item.Leg.LegId);
        if (legIdx >= 0)
        {
          legs[legIdx] = legs[legIdx] with
          {
            LastError = PositionCloseReason.StopLossOrTakeProfit.ToString(),
          };
        }
      }
    }

    var anyUnknown = reasons.Any(item => item.Reason == PositionCloseReason.Unknown);
    var allHaveExitPrice = reasons.All(item => item.ExitPrice is not null);
    var totalTracked = legs.Count(leg => leg.BrokerPositionId is not null);
    var closedCount = reasons.Count;
    var allMissingAreSl = reasons.All(
      item => item.Reason == PositionCloseReason.StopLossOrTakeProfit
    );
    var allManual = reasons.All(
      item => item.Reason == PositionCloseReason.ManualOrExternalOrder
    );

    if (anyUnknown && !allHaveExitPrice)
    {
      // Full-group vanish with no deal fill (common after owner manual close
      // on cTrader when the short deal window misses the exit). Finalize with
      // the live executable quote instead of leaving recovery_required behind.
      if (stillOpen == 0)
      {
        var liveExit = liveExitHint ?? ExecutableQuote(plan, quote);
        if (liveExit is decimal exit)
        {
          var fallbackIsStop = LooksLikeStopOut(plan, state, exit)
            || (
              (state.BreakEvenApplied || state.HighestBookedTargetIndex >= 0)
              && (
                state.CurrentStop != 0
                || state.GroupAbsoluteStop is not null
              )
            );
          var fallbackReason = fallbackIsStop
            ? PositionCloseReason.StopLossOrTakeProfit
            : PositionCloseReason.ManualOrExternalOrder;
          var stopExit =
            state.CurrentStop != 0 ? state.CurrentStop
            : state.GroupAbsoluteStop ?? plan.Stop.Price;
          // Past-stop wick → book the protective stop (not the sweep). Near
          // stop → keep the live quote as the best available fill estimate.
          var bookedExit =
            fallbackIsStop
            && ExitBeyondProtectiveStop(plan, state, exit)
            && stopExit > 0
              ? stopExit
              : exit;
          var patched = reasons
            .Select(item => (
              item.Leg,
              fallbackReason,
              (decimal?)bookedExit
            ))
            .ToList();
          return await FinalizeBrokerAbsentCloseAsync(
            symbol,
            plan,
            state,
            legs,
            patched,
            terminalReason: fallbackIsStop
              ? "group_stop_loss"
              : "manual_or_external_close",
            eventKey: fallbackIsStop
              ? "group_stop_loss"
              : "manual_or_external_close",
            reasonCode: fallbackIsStop
              ? "stop_loss_or_take_profit"
              : "manual_or_external_close",
            closeKind: fallbackIsStop
              ? "stop_loss_or_take_profit"
              : "manual_or_external",
            previousGroupStage,
            cancellationToken
          );
        }
      }
      else
      {
        // Partial unknown with remaining open legs: do not raise recovery —
        // that freezes IsManagingStage and strands L2/L3. Treat the vanished
        // leg as manual/external and keep managing what is still open.
        var partialExit = liveExitHint;
        for (var i = 0; i < reasons.Count; i++)
        {
          var item = reasons[i];
          if (item.Reason != PositionCloseReason.Unknown)
          {
            continue;
          }
          reasons[i] = (
            item.Leg,
            PositionCloseReason.ManualOrExternalOrder,
            item.ExitPrice ?? partialExit
          );
          var legIdx = legs.FindIndex(leg => leg.LegId == item.Leg.LegId);
          if (legIdx >= 0)
          {
            legs[legIdx] = legs[legIdx] with
            {
              LastError = PositionCloseReason.ManualOrExternalOrder.ToString(),
            };
          }
        }
        anyUnknown = false;
        allHaveExitPrice = reasons.All(item => item.ExitPrice is not null);
        allManual = reasons.All(
          item => item.Reason == PositionCloseReason.ManualOrExternalOrder
        );
        allMissingAreSl = false;
      }

      if (anyUnknown && !allHaveExitPrice)
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
        var unknownBits = reasons
          .Where(r => r.Reason == PositionCloseReason.Unknown)
          .Select(r =>
            r.ExitPrice is decimal exit
              ? $"{r.Leg.LegId}@{FormatEventPrice(exit, symbol)}"
              : r.Leg.LegId
          );
        await PublishEventAsync(
          "warning",
          $"GROUP RECOVERY REQUIRED unknown close on "
          + $"{string.Join(",", unknownBits)}",
          plan,
          cancellationToken,
          positionId: state.PositionId,
          eventKey: "recovery_required_unknown_close",
          state: TradePlanGroupStages.RecoveryRequired
        );
        log($"v8 recovery_required id={plan.PlanId} reason=unknown_leg_close");
        return next;
      }
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
      return await FinalizeBrokerAbsentCloseAsync(
        symbol,
        plan,
        state,
        legs,
        reasons,
        terminalReason: "group_stop_loss",
        eventKey: "group_stop_loss",
        reasonCode: "stop_loss_or_take_profit",
        closeKind: "stop_loss_or_take_profit",
        previousGroupStage,
        cancellationToken
      );
    }

    if (stillOpen > 0 && !anyUnknown && !allMissingAreSl)
    {
      var partialExit = reasons
        .Select(item => item.ExitPrice)
        .FirstOrDefault(price => price is not null);
      var next = AggregateState(
        state with
        {
          Legs = legs,
          GroupStage = TradePlanGroupStages.PartiallyClosed,
        }
      );
      await PersistStateAsync(next, cancellationToken);
      await PublishEventAsync(
        "position_closed",
        $"GROUP PARTIALLY CLOSED manual/external "
        + $"({closedCount}/{Math.Max(totalTracked, 1)}); continuing management",
        plan,
        cancellationToken,
        positionId: state.PositionId,
        price: partialExit,
        eventKey: "group_partial_manual_close",
        previousState: previousGroupStage,
        state: TradePlanGroupStages.PartiallyClosed,
        groupRealizedPips: partialExit is decimal exit
          ? SignedExitPips(plan, state, exit)
          : null,
        reasonCode: "manual_or_external_close",
        runtimeState: state
      );
      return next;
    }

    if (stillOpen == 0 && allManual)
    {
      var manualExit = reasons
        .Select(item => item.ExitPrice)
        .FirstOrDefault(price => price is not null)
        ?? liveExitHint;
      if (
        manualExit is decimal exit
        && (
          LooksLikeStopOut(plan, state, exit)
          || state.BreakEvenApplied
          || state.HighestBookedTargetIndex >= 0
        )
      )
      {
        var stopExit =
          state.CurrentStop != 0 ? state.CurrentStop
          : state.GroupAbsoluteStop ?? plan.Stop.Price;
        var bookedExit =
          stopExit > 0
          && ExitBeyondProtectiveStop(plan, state, exit)
            ? stopExit
            : exit;
        var patched = reasons
          .Select(item => (
            item.Leg,
            PositionCloseReason.StopLossOrTakeProfit,
            (decimal?)(item.ExitPrice ?? bookedExit)
          ))
          .ToList();
        return await FinalizeBrokerAbsentCloseAsync(
          symbol,
          plan,
          state,
          legs,
          patched,
          terminalReason: "group_stop_loss",
          eventKey: "group_stop_loss",
          reasonCode: "stop_loss_or_take_profit",
          closeKind: "stop_loss_or_take_profit",
          previousGroupStage,
          cancellationToken
        );
      }
    }

    if (stillOpen == 0 && (allManual || (allHaveExitPrice && !allMissingAreSl)))
    {
      return await FinalizeBrokerAbsentCloseAsync(
        symbol,
        plan,
        state,
        legs,
        reasons,
        terminalReason: allManual
          ? "manual_or_external_close"
          : "broker_close_unconfirmed",
        eventKey: allManual
          ? "manual_or_external_close"
          : "broker_close_unconfirmed",
        reasonCode: allManual ? "manual_or_external_close" : null,
        closeKind: allManual ? "manual_or_external" : "unconfirmed",
        previousGroupStage,
        cancellationToken
      );
    }

    // Manual / external close on one or more legs without a recoverable exit
    // price — do not guess; require recovery.
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

  private async Task<TradePlanRuntimeState> FinalizeBrokerAbsentCloseAsync(
    SymbolInfo symbol,
    TradePlan plan,
    TradePlanRuntimeState state,
    List<TradePlanLegRuntimeState> legs,
    IReadOnlyList<(
      TradePlanLegRuntimeState Leg,
      PositionCloseReason Reason,
      decimal? ExitPrice
    )> reasons,
    string terminalReason,
    string eventKey,
    string? reasonCode,
    string closeKind,
    string previousGroupStage,
    CancellationToken cancellationToken
  )
  {
    var highestTp = HighestBookedTargetId(plan, state);
    var highestTpPips = highestTp is null
      ? null
      : ArchivedTargetPips(plan, state, highestTp);
    var exitHint = reasons
      .Select(item => item.ExitPrice)
      .FirstOrDefault(price => price is not null);
    if (
      exitHint is null
      && closeKind == "stop_loss_or_take_profit"
    )
    {
      var stop =
        state.CurrentStop != 0 ? state.CurrentStop
        : state.GroupAbsoluteStop ?? 0m;
      if (stop > 0)
      {
        exitHint = stop;
      }
      else if (highestTp is null && plan.Stop.Price > 0)
      {
        exitHint = plan.Stop.Price;
      }
    }
    var next = AggregateState(
      state with
      {
        Legs = legs,
        GroupStage = TradePlanGroupStages.Closed,
        TerminalReason = terminalReason,
        Stage = TradePlanRuntimeStage.Closed,
      }
    );
    await PersistStateAsync(next, cancellationToken);
    int? realizedPips = null;
    if (exitHint is decimal exitPrice)
    {
      if (closeKind != "stop_loss_or_take_profit" || highestTp is null)
      {
        realizedPips = SignedExitPips(plan, state, exitPrice);
      }
    }
    string closeMessage;
    if (highestTp is not null)
    {
      closeMessage = exitHint is decimal exit
        ? $"PLAN CLOSED · highest TP archived {highestTp} · @ {FormatEventPrice(exit, symbol)}"
        : $"PLAN CLOSED · highest TP archived {highestTp}";
    }
    else if (realizedPips is int lossPips && lossPips < 0)
    {
      closeMessage = exitHint is decimal exit
        ? closeKind == "stop_loss_or_take_profit"
          ? $"PLAN CLOSED · no TP archived · losing {lossPips} pips · @ {FormatEventPrice(exit, symbol)}"
          : $"PLAN CLOSED · {closeKind} · losing {lossPips} pips · @ {FormatEventPrice(exit, symbol)}"
        : closeKind == "stop_loss_or_take_profit"
          ? $"PLAN CLOSED · no TP archived · losing {lossPips} pips"
          : $"PLAN CLOSED · {closeKind} · losing {lossPips} pips";
    }
    else if (realizedPips is int winPips && winPips > 0)
    {
      closeMessage = exitHint is decimal exit
        ? $"PLAN CLOSED · {closeKind} · winning {winPips} pips · @ {FormatEventPrice(exit, symbol)}"
        : $"PLAN CLOSED · {closeKind} · winning {winPips} pips";
    }
    else if (realizedPips is 0)
    {
      closeMessage = exitHint is decimal exit
        ? $"PLAN CLOSED · {closeKind} · break-even · @ {FormatEventPrice(exit, symbol)}"
        : $"PLAN CLOSED · {closeKind} · break-even";
    }
    else
    {
      closeMessage = exitHint is decimal exit
        ? closeKind == "stop_loss_or_take_profit"
          ? $"PLAN CLOSED · no TP archived · @ {FormatEventPrice(exit, symbol)}"
          : $"PLAN CLOSED · {closeKind} · @ {FormatEventPrice(exit, symbol)}"
        : closeKind == "stop_loss_or_take_profit"
          ? "PLAN CLOSED · no TP archived"
          : $"PLAN CLOSED · {closeKind}";
    }
    await PublishEventAsync(
      "position_closed",
      closeMessage,
      plan,
      cancellationToken,
      positionId: state.PositionId,
      price: exitHint,
      targetPips: highestTpPips,
      eventKey: eventKey,
      previousState: previousGroupStage,
      state: TradePlanGroupStages.Closed,
      groupRealizedPips: realizedPips,
      reasonCode: reasonCode,
      runtimeState: next
    );
    await PersistPlanExecutionStateAsync(
      plan.PlanId, "completed", null, cancellationToken, terminalReason
    );
    await ForgetPlanAsync(state.PlanId, cancellationToken);
    return next;
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
          $"v8 cancel unfilled leg id={plan.PlanId} leg={leg.LegId} "
          + $"order={leg.BrokerOrderId} reason={reason}"
        );
      }
      catch (Exception exception)
      {
        log(
          $"v8 cancel unfilled leg failed id={plan.PlanId} leg={leg.LegId} "
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
      Stage = state.Stage is TradePlanRuntimeStage.Submitting
        ? TradePlanRuntimeStage.Submitting
        : DeriveRuntimeStage(legs),
      GroupStage = state.GroupStage == TradePlanGroupStages.RecoveryRequired
        ? state.GroupStage
        : state.Stage is TradePlanRuntimeStage.Submitting
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
    // No leg open or pending and every remaining leg is a terminal
    // non-fill (cancelled/expired/rejected, none ever reached a broker
    // position) — there is genuinely nothing left to submit, wait for, or
    // manage. Reuse Closed rather than adding a new enum value; the
    // GroupStage string below still distinguishes "never filled, fully
    // cancelled" from a real fill that later closed.
    if (legs.All(leg =>
      leg.Stage is TradePlanLegStages.Cancelled
        or TradePlanLegStages.Expired
        or TradePlanLegStages.Rejected
        or TradePlanLegStages.Closed
    ))
    {
      return TradePlanRuntimeStage.Closed;
    }
    return TradePlanRuntimeStage.Submitted;
  }

  private static string DeriveGroupStage(IReadOnlyList<TradePlanLegRuntimeState> legs)
  {
    var stage = DeriveRuntimeStage(legs);
    if (
      stage == TradePlanRuntimeStage.Closed
      && legs.Count > 0
      && legs.All(leg => leg.FilledVolume == 0)
      && legs.Any(leg => leg.Stage == TradePlanLegStages.Cancelled)
    )
    {
      // Never opened at all (no leg ever filled) and at least one leg was
      // cancelled - distinct from Closed, which implies a real fill that
      // was later managed/closed with realized pips.
      return TradePlanGroupStages.Cancelled;
    }
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
        ClientOrderId: TradePlanOwnership.FormatClientOrderId(
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

  /// <summary>
  /// Highest take-profit actually booked before this close. NextTargetIndex
  /// also advances for touched targets whose partial was too small to book,
  /// so it must never be used as the archive source.
  /// </summary>
  private static string? HighestBookedTargetId(
    TradePlan plan,
    TradePlanRuntimeState state
  )
  {
    if (state.HighestBookedTargetIndex < 0 || plan.Targets.Count == 0)
    {
      return null;
    }
    var index = Math.Min(
      state.HighestBookedTargetIndex,
      plan.Targets.Count - 1
    );
    return plan.Targets[index].TargetId;
  }

  private int? ArchivedTargetPips(
    TradePlan plan,
    TradePlanRuntimeState state,
    string targetId
  )
  {
    var weightedFill = state.GroupWeightedFillPrice ?? state.EntryFillPrice;
    var pipSize = PipSizeFor(plan.Symbol);
    if (
      pipSize <= 0
      || weightedFill is not decimal fillPrice
      || fillPrice <= 0
    )
    {
      return null;
    }
    var index = IndexOfTarget(plan, targetId);
    if (index < 0)
    {
      return null;
    }
    // Report realized direction vs fill — never abs() a losing chase-through
    // TP as "+1 pip achieved".
    var buy = string.Equals(
      plan.Analysis.Direction,
      "BUY",
      StringComparison.OrdinalIgnoreCase
    );
    var raw = buy
      ? (plan.Targets[index].Price - fillPrice) / pipSize
      : (fillPrice - plan.Targets[index].Price) / pipSize;
    if (raw <= 0)
    {
      return null;
    }
    return decimal.ToInt32(decimal.Round(
      raw,
      0,
      MidpointRounding.AwayFromZero
    ));
  }

  private int? SignedExitPips(
    TradePlan plan,
    TradePlanRuntimeState state,
    decimal exitPrice
  )
  {
    var weightedFill = state.GroupWeightedFillPrice ?? state.EntryFillPrice;
    var pipSize = PipSizeFor(plan.Symbol);
    if (
      pipSize <= 0
      || weightedFill is not decimal fillPrice
      || fillPrice <= 0
    )
    {
      return null;
    }
    var buy = string.Equals(
      plan.Analysis.Direction,
      "BUY",
      StringComparison.OrdinalIgnoreCase
    );
    var raw = buy
      ? (exitPrice - fillPrice) / pipSize
      : (fillPrice - exitPrice) / pipSize;
    return decimal.ToInt32(decimal.Round(
      raw,
      0,
      MidpointRounding.AwayFromZero
    ));
  }
}

internal static class TradePlanStreamKeys
{
  public static string PlanKey(string planId) => $"execution:plan:{planId}";
}

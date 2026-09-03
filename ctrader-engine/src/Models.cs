using System.Text.Json.Serialization;

namespace ApexVoid.CTraderFeed;

public sealed record RawTrendbar(
  string Timeframe,
  long Low,
  ulong DeltaOpen,
  ulong DeltaHigh,
  ulong DeltaClose,
  long Volume,
  uint UtcTimestampInMinutes,
  bool HasDeltaClose = true,
  long SymbolId = 0
);

public sealed record OhlcBar(
  long Timestamp,
  decimal Open,
  decimal High,
  decimal Low,
  decimal Close,
  long Volume
)
{
  public long CloseTimestamp(string timeframe) =>
    Timestamp + TimeframeCodec.ToSeconds(timeframe);
}

public sealed record SymbolInfo(
  string RedisSymbol,
  string CTraderSymbol,
  long SymbolId,
  int Digits,
  int PipPosition = 1,
  long MinVolume = 0,
  long StepVolume = 0,
  long MaxVolume = 0,
  long LotSize = 0
);

public sealed record SpotPrice(
  string Symbol,
  decimal Bid,
  decimal Ask,
  long Timestamp
);

public sealed record ClosedBarEmission(
  OhlcBar Bar,
  bool RequiresHistoricalClose
);

public sealed record RedisBarEntry(long Timestamp, string Json);

public enum TradeDirection
{
  Buy,
  Sell,
}

// What actually closed a position that disappeared from a broker reconcile
// snapshot. Determined (when possible) from the closing order's OrderType
// via ProtoOADealListByPositionIdReq + ProtoOAOrderListReq - see
// CTraderOpenApiFeedClient.DeterminePositionCloseReasonAsync. A position
// closed by our own ClosePositionAsync never reaches this classification;
// it already knows its own close reason from the direct broker response.
public enum PositionCloseReason
{
  // Deal/order history was unavailable, ambiguous, or the lookup failed -
  // the same "we cannot tell" state this code path has always reported.
  Unknown,
  // The closing order's type was StopLossTakeProfit - the broker-attached
  // SL/TP order triggered the close, not a manual action.
  StopLossOrTakeProfit,
  // The closing order was a plain Market/Limit/Stop/StopLimit order that
  // was not part of any order this executor placed - almost certainly the
  // owner (or another API client) closing the position directly on the
  // broker platform.
  ManualOrExternalOrder,
}

// Result of a best-effort close-reason lookup: the classification plus, when
// the closing deal was found, its real broker execution price - so a
// confirmed-missing position can report the true fill instead of falling
// back to the last known stop/entry price.
public sealed record PositionCloseLookup(
  PositionCloseReason Reason,
  decimal? ExecutionPrice = null
);

public sealed record TradingAccountSnapshot(
  long AccountId,
  bool IsLive,
  string PermissionScope,
  string AccessRights,
  string AccountType,
  string BrokerName,
  decimal Balance,
  decimal Equity,
  // Unix seconds when this snapshot was taken. 0 in fixtures that do not
  // model freshness.
  long SnapshotTimestamp = 0,
  // How Equity was obtained. Live OpenAPI ProtoOATrader has no Equity
  // field, so CTraderOpenApiFeedClient copies Balance into Equity and
  // marks this "balance_proxy". Tests set Equity independently (leave
  // blank or use "broker"/"test") so EquityResolver treats it as real.
  string EquitySource = ""
);

public sealed record TradingAccountGrant(long AccountId, bool IsLive);

public sealed record TradingPosition(
  long PositionId,
  long SymbolId,
  TradeDirection Direction,
  long Volume,
  decimal EntryPrice,
  decimal? StopLoss,
  string Label,
  string Comment,
  // Exact deterministic client order identity, when the broker exposes it on
  // the originating order. Empty when unavailable (legacy positions).
  string ClientOrderId = "",
  // Unrealized net profit in account currency when the broker/reconcile
  // path exposes it. ProtoOAPosition in OpenAPI.Net 1.4.4 does not carry
  // NetProfit/Unrealized; live mapping leaves this null. Fake/test clients
  // may set it so EquityResolver can use balance_plus_unrealized.
  decimal? NetProfit = null
);

public sealed record MarketOrderRequest(
  long SymbolId,
  TradeDirection Direction,
  long Volume,
  long RelativeStopLoss,
  string Label,
  string Comment,
  string ClientOrderId
);

public sealed record LimitOrderRequest(
  long SymbolId,
  TradeDirection Direction,
  long Volume,
  decimal LimitPrice,
  long RelativeStopLoss,
  string Label,
  string Comment,
  string ClientOrderId
);

public sealed record TradingPendingOrder(
  long OrderId,
  long SymbolId,
  TradeDirection Direction,
  long Volume,
  decimal LimitPrice,
  string Label,
  string Comment,
  // Exact deterministic client order identity as reported by the broker.
  // Empty when the broker did not echo one (legacy orders).
  string ClientOrderId = ""
);

public sealed record TradingReconcileSnapshot(
  IReadOnlyList<TradingPosition> Positions,
  IReadOnlyList<TradingPendingOrder> PendingOrders
);

public sealed record TradeExecution(
  long PositionId,
  long OrderId,
  decimal ExecutionPrice,
  long ExecutedVolume,
  long? RemainingVolume = null
);

public sealed record TradeCandidateZone(
  decimal Low,
  decimal High
);

public sealed record TradeCandidate(
  int Version,
  string CandidateId,
  string Symbol,
  string Timeframe,
  string Setup,
  string Mode,
  string Direction,
  string TriggerTs,
  long CreatedAt,
  long? SpotTs,
  decimal CurrentPrice,
  decimal KeyLevel,
  TradeCandidateZone EntryZone,
  int Confluence,
  IReadOnlyList<string> Reasons,
  long? BarTs = null,
  decimal? Atr = null,
  decimal? StructureSwing = null,
  string? DisplacementDirection = null,
  int? DisplacementAgeBars = null,
  string? BosDirection = null,
  long? BosTs = null,
  decimal? OpposingLevelDistanceAtr = null,
  string? RangeId = null,
  decimal? RangeLow = null,
  decimal? RangeHigh = null,
  int? FullTakeProfitPips = null,
  IReadOnlyList<int>? TargetsPips = null,
  string? Regime = null,
  decimal? OpposingZoneLow = null,
  decimal? OpposingZoneHigh = null,
  decimal? ManualStopLoss = null,
  long? ManualExpiresAt = null,
  bool BypassAnalysisGates = false,
  decimal? SweepLow = null,
  decimal? SweepHigh = null,
  // Pullback scale-in add (ScaleInTriggerPlanner P1-P4) - see scale_context.py.
  // CounterBosTs/ExtremeTs are raw timestamps gated against a group's own
  // GroupOpenedAt by ValidateAddTriggers (AutoTradeEngine.cs), the same
  // pattern BosTs already uses; AddZoneLow/High reuse OpposingZoneLow/High
  // (the nearest zone on the trade-direction side is the same lookup for
  // both purposes) and only the side label is new.
  long? CounterBosTs = null,
  decimal? ExtremePrice = null,
  long? ExtremeTs = null,
  string? AddZoneSide = null,
  bool RejectionConfirmed = false,
  string? MatchId = null,
  string? GroupId = null,
  string? StrategyFamily = null,
  IReadOnlyList<decimal>? ManualTakeProfits = null,
  string? ZoneId = null,
  string? TriggerId = null,
  string? ParentGroupId = null,
  string? StructuralSource = null,
  string? Bias = null,
  string? RelationshipToBias = null,
  string? ReactionId = null,
  string? ThesisId = null,
  string? StructuralZoneId = null,
  decimal? StructuralZoneLow = null,
  decimal? StructuralZoneHigh = null,
  string? OrderTypePreference = null,
  string? EntryDistribution = null,
  decimal? RiskMultiplier = null,
  string? TargetModel = null,
  decimal? AbsoluteTargetPrice = null,
  string? TargetReferencePrice = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? PlannedStopEntryPrice = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? PlannedStopPrice = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? PlannedStopDistance = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? PlannedStopPips = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? PlannedStopRawPrice = null,
  bool? PlannedStopClamped = null,
  string? StopSource = null,
  int? StopPlanVersion = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? PlannedBaseStopPrice = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? PlannedBaseStopPips = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? PlannedFinalStopPrice = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? PlannedFinalStopDistance = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? PlannedFinalStopPips = null,
  string? StopAdjustment = null,
  string? StopAdjustmentZoneId = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? StopAdjustmentZoneLow = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? StopAdjustmentZoneHigh = null,
  // Exact identity of the opposing zone Python evaluated. Required whenever
  // the stop was pushed beyond that zone.
  string? OpposingZoneId = null,
  // Route Python resolved and the entry it priced the stop against. The
  // executor rejects route drift and material entry drift before submitting.
  string? PlannedExecutionRoute = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? PlannedEntryPrice = null,
  IReadOnlyList<decimal>? PlannedLegEntryPrices = null,
  int? EntryPlanVersion = null,
  IReadOnlyList<int>? ManualTargetWeights = null,
  bool ManualSingleEntry = false,
  // Observed scoring telemetry. This is deliberately additive: the V6
  // executor still gates on Confluence, while outcome persistence records
  // both scorer variants for shadow-mode comparison.
  int? ConfluenceV1 = null,
  int? ConfluenceV2 = null,
  double? ConfluenceV2Raw = null,
  string? ConfluenceScoringVersion = null
);

public sealed record TradeStreamEntry(
  string Id,
  string Payload
);

public sealed record AutoTradePositionState(
  string CandidateId,
  long PositionId,
  long SymbolId,
  TradeDirection Direction,
  decimal EntryPrice,
  long InitialVolume,
  long RemainingVolume,
  IReadOnlyList<long> Slices,
  IReadOnlyList<int> TargetsPips,
  int NextTargetIndex,
  long OpenedAt,
  decimal? CurrentStopLoss = null,
  IReadOnlyList<int>? TargetOrdinals = null,
  string? GroupId = null,
  int TrancheIndex = 1,
  decimal GroupBookedPnl = 0m,
  decimal InitialTrancheBookedPnl = 0m,
  long GroupOpenedAt = 0,
  long LastTrancheBarTs = 0,
  int GroupTrancheCount = 1,
  bool HadAdds = false,
  decimal? InitialStopLoss = null,
  int ZoneLeg = 0,
  decimal GroupRealizedPipVolume = 0m,
  decimal InitialRealizedPipVolume = 0m,
  long GroupInitialVolume = 0,
  long InitialTrancheVolume = 0,
  string? Setup = null,
  string? Regime = null,
  int? Confluence = null,
  string? RangeId = null,
  decimal? RangeLow = null,
  decimal? RangeHigh = null,
  decimal? RangeExitPrice = null,
  string Stream = "algo_auto",
  string? MatchId = null,
  string? StrategyFamily = null,
  IReadOnlyList<decimal>? TargetPrices = null,
  string? ZoneId = null,
  string? TriggerId = null,
  string? ParentGroupId = null,
  string? StructuralSource = null,
  string? ReactionId = null,
  string? ThesisId = null,
  bool RangeBoxScaleOutBooked = false,
  long? RangeBoxScaleOutVolume = null,
  decimal? RangeBoxScaleOutPrice = null,
  decimal? RangeBoxScaleOutPips = null,
  long? RangeBoxScaleOutAt = null,
  string? StructuralZoneId = null,
  decimal? StructuralZoneLow = null,
  decimal? StructuralZoneHigh = null,
  decimal? RiskMultiplier = null,
  string? TargetModel = null,
  decimal? AbsoluteTargetPrice = null,
  long FillSourceQuoteTimestamp = 0,
  long FillSourceQuoteSequence = 0,
  // Canonical Redis instrument (XAU / EURUSD / GBPJPY). Python exposure
  // gates filter by this string — SymbolId alone cannot isolate books.
  string? Symbol = null,
  // Manual XAU ladders are sized as one owner intent from the shallow
  // (worst-case) entry. Mid/deep clips keep their own actual fill for PnL,
  // but lifecycle risk metadata must retain this group-level initial stop
  // distance instead of shrinking it as deeper clips fill.
  decimal? InitialRiskStopPips = null
);

public sealed record RedisClaimPayload(
  string? CandidateId = null,
  string? State = null,
  string? ReactionId = null,
  string? ThesisId = null,
  string? GroupId = null,
  bool RearmReady = false
);

// One owner-override command for an already-armed/filled manual-algo
// signal (`/trade_close`, `/trade_sl`, `/trade_cancel`) or a bulk flatten
// (`/auto_close_all`), published by the Python side onto
// `manual_trade:commands` and consumed by AutoTradeEngine's command poll.
// `Type` is one of "cancel_pending" | "close" | "move_sl" | "close_all".
public sealed record ManualTradeCommand(
  string Type,
  string? IntentId = null,
  long? PositionId = null,
  decimal? Price = null,
  decimal? Frac = null
);

// Close-reason-aware marker read by worker.py.  Only reason=stop_loss with
// confidence=confirmed is enforceable; reconciliation_unknown/manual/external
// closes are warning-only and must not silently become a 60-minute veto.
public sealed record ZoneCooldownRecord(
  string Reason,
  string Confidence,
  decimal EntryPrice,
  decimal StopPrice,
  long ClosedAt,
  string? GroupId = null,
  string? ZoneId = null,
  string? Strategy = null
);

public sealed record AutoTradeEvent(
  string Type,
  long Timestamp,
  string Message,
  string Symbol,
  string? CandidateId = null,
  long? PositionId = null,
  int? TargetPips = null,
  long? Volume = null,
  decimal? Price = null,
  string? GroupId = null,
  int? TrancheIndex = null,
  decimal? GroupWorstCase = null,
  decimal? RiskBudget = null,
  decimal? GroupRealizedPnl = null,
  decimal? CounterfactualPnl = null,
  bool? HadAdds = null,
  decimal? GroupRealizedPips = null,
  decimal? CounterfactualPips = null,
  string? Setup = null,
  string? Regime = null,
  int? Confluence = null,
  decimal? StopPips = null,
  IReadOnlyList<int>? TargetsPips = null,
  string? Stream = null,
  string? Direction = null,
  long? RemainingVolume = null,
  string? LifecycleId = null,
  string? State = null,
  string? ReasonCode = null,
  string? MatchId = null,
  string? RangeId = null,
  string? StrategyFamily = null,
  string? ConfigurationProfile = null,
  string? AccountType = null,
  string? Broker = null,
  string? CorrelationId = null,
  string? PreviousState = null,
  IReadOnlyList<long>? PendingOrderIds = null,
  long? OrderId = null,
  decimal? StopLoss = null,
  IReadOnlyList<decimal>? TargetPrices = null,
  decimal? EntryLow = null,
  decimal? EntryHigh = null,
  decimal? LegRealizedPips = null,
  long? GroupInitialVolume = null,
  long? LotSize = null,
  string? StructuralSource = null,
  string? ZoneId = null,
  string? StructuralZoneId = null,
  string? ReactionId = null,
  string? ThesisId = null,
  decimal? RiskMultiplier = null,
  string? TargetModel = null,
  string? EntryDistribution = null,
  bool MutatesLifecycle = false,
  // TradePlan V8 events only (docs/adr-trade-plan-v8-cutover.md):
  // CandidateId carries plan_id, MatchId carries setup_id, ThesisId carries
  // thesis_id (all already-existing fields, reused rather than duplicated).
  // EntryType is the one genuinely new label TradePlan needs (market_watch/
  // single_limit/limit_ladder has no V6 analogue).
  string? EntryType = null,
  // Terminal close analytics for fixed_rr journal (Python store.py).
  bool? BreakEvenApplied = null,
  int? HighestBookedTargetIndex = null,
  decimal? PlannedRewardRisk = null,
  bool? TargetRoomFallbackUsed = null,
  string? ExitPath = null,
  int? ConfluenceV1 = null,
  int? ConfluenceV2 = null,
  double? ConfluenceV2Raw = null,
  string? ConfluenceScoringVersion = null
);

public sealed record AutoTradeGroupPlan(
  string CandidateId,
  string GroupId,
  string? MatchId,
  string? StrategyFamily,
  string? RangeId,
  string Setup,
  string Direction,
  long CreatedAt,
  IReadOnlyList<decimal>? TargetPrices = null,
  decimal? ManualStopLoss = null,
  string? ZoneId = null,
  string? TriggerId = null,
  string? ParentGroupId = null,
  string? StructuralSource = null,
  string? ReactionId = null,
  string? ThesisId = null,
  string? StructuralZoneId = null,
  decimal? StructuralZoneLow = null,
  decimal? StructuralZoneHigh = null,
  decimal? RiskMultiplier = null,
  string? TargetModel = null,
  decimal? AbsoluteTargetPrice = null,
  // Deterministic recovery identities. Retained until adoption or confirmed
  // broker absence; never deleted after a single empty snapshot.
  string? StreamEventId = null,
  string? Route = null,
  IReadOnlyList<string>? ClientOrderIds = null,
  long? SubmittedAt = null,
  int RecoveryAttempt = 0,
  int AbsenceConfirmations = 0,
  long? LastAbsenceCheckAt = null,
  // One group-level risk distance derived from the shallow entry and the
  // owner's absolute SL. This survives broker reconciliation/restarts so
  // every ladder leg reports the same approved initial risk contract.
  decimal? ManualRiskStopPips = null
);

public sealed record CanonicalConfigOption(
  string Name,
  string NormalizedValue,
  string Source,
  IReadOnlyList<string> DeprecatedAliasesPresent,
  bool Conflict
);

public sealed record AutoTradeConfigManifest(
  int ConfigManifestVersion,
  string Service,
  string ServiceVersion,
  string GitSha,
  string Profile,
  bool AutoTradeEnabled,
  bool DryRun,
  string RedisFingerprint,
  int RedisDatabase,
  string CandidateStream,
  string EventStream,
  IReadOnlyList<string> Symbols,
  string CanonicalSymbol,
  decimal PipSize,
  decimal ContractSize,
  IReadOnlyList<int> TargetPlans,
  IReadOnlyList<int> RangeTargetPlans,
  decimal RangeTpBuffer,
  int CandidateStorageTtlSeconds,
  int CandidateExecutionMaxAgeSeconds,
  int SpotMaxAgeSeconds,
  bool RangeFlip,
  bool TwoSidedRange,
  bool ConcurrentStrategies,
  bool HedgingPolicy,
  bool ZoneFill,
  int MinConfluence,
  string AccountMode,
  bool RequireDemoAccount,
  string Broker,
  int CandidateContractVersion,
  long GeneratedAt,
  bool ManualAlgoEnabled = false,
  bool ManualAlgoDryRun = true,
  bool BrokerHedgingCapability = false,
  bool TrendEnabled = false,
  bool RangeEnabled = false,
  bool MappedZoneEnabled = false,
  bool MarketMapGuardEnabled = false,
  bool MapThesisLockEnabled = true,
  bool StrategyMatchEnabled = false,
  bool BreakoutEnabled = false,
  bool RetestEnabled = false,
  bool ReactionEnabled = false,
  bool LiquidityReversalEnabled = false,
  bool AllowCounterBias = false,
  string NonHedgedOppositePolicy = "reject",
  IReadOnlyList<string>? DeprecatedVariables = null,
  IReadOnlyDictionary<string, string>? ConfigSources = null,
  string BrokerReported = "",
  string StructuralGuardMode = "balanced",
  bool ZoneCooldownEnabled = true,
  string ZoneReconcileMode = "enforce",
  bool RangeBoxScaleOutEnabled = true,
  int RangeBoxScaleOutThresholdPips = 70,
  int RangeBoxScaleOutTriggerPips = 30,
  decimal RangeBoxScaleOutFraction = 0.50m,
  bool RangeBoxMoveSlToBeAfterScaleOut = false,
  decimal ExecutionZoneMaxWidthAtr = 2.0m,
  decimal ExecutionZoneMaxWidthPips = 100m,
  decimal StructureStopBufferAtr = 0.3m,
  int OrdinaryStopMinPips = 30,
  decimal OrdinaryStopMaxDistance = 6.5m,
  decimal WickStopBufferAtr = 0.15m,
  int TrendStopMinPips = 40,
  int TrendStopMaxPips = 60,
  IReadOnlyList<CanonicalConfigOption>? CanonicalOptions = null,
  int PriceDigits = 2,
  decimal MaxEntryDistancePips = 40m,
  decimal EntryContractTolerancePips = 3m,
  int BreakEvenBufferTicks = 6,
  decimal SymbolTickSize = 0.01m,
  int EntryPlanVersion = 1,
  int StopPlanVersion = 3,
  string PostFillTargetFallback = "fill_relative",
  string ContractMode = "v8_only",
  int TradePlanVersion = TradePlanContract.Version,
  string TradePlanStream = "execution:trade_plans",
  string SizingMode = "equity_table",
  string EquityTableVersion = "owner_equity_v1",
  string ZoneScaleUndersizedPolicy = "single_entry",
  string GroupCloseAllocation = "pro_rata",
  string UnfilledLegAfterTpPolicy = "cancel",
  string EntryLegRatios = "0.80,0.20"
);

public sealed record AutoTradeConfigHealthDocument(
  string State,
  IReadOnlyList<string> Fatal,
  IReadOnlyList<string> Warnings,
  string Profile,
  long CheckedAt
);

public sealed record AutoTradeExecutorReadiness(
  bool Ready,
  string State,
  IReadOnlyList<string> Fatal,
  IReadOnlyList<string> Warnings,
  string Profile,
  long CheckedAt
);

public sealed record AutoTradeExecutorSnapshot(
  string Symbol,
  string Profile,
  string ExposurePolicy,
  bool Demo,
  bool Hedged,
  bool Ready,
  IReadOnlyList<long> PositionIds,
  IReadOnlyList<long> PendingOrderIds,
  IReadOnlyList<string> GroupIds,
  long UpdatedAt,
  // Raw broker account figures at snapshot time (0 before the first
  // account snapshot arrives). Equity uses the same balance_proxy fallback
  // as sizing (EquityResolver) - see AccountEquitySource for which.
  decimal AccountBalance = 0m,
  decimal AccountEquity = 0m,
  string AccountEquitySource = ""
);

// Durable, restart-surviving confirmation progress for a tracked position
// that a broker snapshot did not report. A single missing snapshot is never
// enough to terminalise a position - it must be independently confirmed
// missing across at least AutoTradeOptions.PositionMissingConfirmations
// reconcile passes, each separated by at least
// AutoTradeOptions.PositionMissingRecheckSeconds.
public sealed record PositionMissingRecord(
  int Confirmations,
  long FirstMissingAt,
  long LastCheckedAt
);

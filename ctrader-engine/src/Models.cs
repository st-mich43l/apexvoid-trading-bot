namespace ApexVoid.CTraderFeed;

public sealed record RawTrendbar(
  string Timeframe,
  long Low,
  ulong DeltaOpen,
  ulong DeltaHigh,
  ulong DeltaClose,
  long Volume,
  uint UtcTimestampInMinutes,
  bool HasDeltaClose = true
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

public sealed record TradingAccountSnapshot(
  long AccountId,
  bool IsLive,
  string PermissionScope,
  string AccessRights,
  string AccountType,
  string BrokerName,
  decimal Balance
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
  string Comment
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
  string Comment
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
  decimal? StructuralZoneHigh = null
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
  decimal? StructuralZoneHigh = null
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
  long? LotSize = null
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
  decimal? StructuralZoneHigh = null
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
  IReadOnlyList<CanonicalConfigOption>? CanonicalOptions = null
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
  long UpdatedAt
);

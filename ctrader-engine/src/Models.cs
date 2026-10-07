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

// One historical order the executor can correlate against its own
// ClientOrderId convention, independent of whether that order is still
// resting, was filled, or was cancelled/expired/rejected - the broker-truth
// counterpart to a submitted-but-never-adopted AutoTradeGroupPlan leg (see
// AutoTradeEngine.ReconcileOrphanedGroupPlansAsync).
public sealed record HistoricalOrderMatch(
  string ClientOrderId,
  bool Filled,
  long? PositionId,
  long SymbolId,
  long ExecutedVolume
);

// One closing deal for a position, carrying the SAME entry/exit prices the
// broker itself used so realized pips can be computed with the existing
// direction-adjusted (exit - entry) / pipSize convention (see
// AutoTradeEngine.SignedPips) - never derived from GrossProfit/account
// currency, which would need a separate, untested money-digits conversion.
public sealed record ClosingDeal(
  decimal EntryPrice,
  decimal ExitPrice,
  long ClosedVolume,
  long ExecutionTimestamp
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

public sealed record TradeStreamEntry(
  string Id,
  string Payload
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
  // The group's DEEPEST fill price behind LegRealizedPips (see
  // AutoTradeEngine.GroupDeepestEntryPrice) - a multi-leg manual /algo
  // group's shallow/mid/deep clips each fill at their own price, but both
  // the channel pips card and the Python-side realized-R calc must measure
  // against the group's single best (deepest) fill, not whichever specific
  // tranche happens to be booking this event, and not the advertised entry
  // zone either.
  decimal? LegEntryPrice = null,
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
  // TradePlan V8 events only (docs/autotrade-execution-integrity.md):
  // CandidateId carries plan_id, MatchId carries setup_id, ThesisId carries
  // thesis_id (all already-existing fields, reused rather than duplicated).
  // EntryType is the one genuinely new label TradePlan needs (market_watch/
  // single_limit/limit_ladder is declared only by a TradePlan).
  string? EntryType = null,
  // Terminal close analytics for fixed_rr journal (Python store.py).
  bool? BreakEvenApplied = null,
  int? HighestBookedTargetIndex = null,
  // Plan's total declared target count, alongside HighestBookedTargetIndex,
  // so a mid-trade tp_booked card can say "TP1 of 2" instead of the
  // open-entry-legs fraction the card text used to carry there (owner
  // 2026-09-22: read as "this was the only target" when TP2+ were pending).
  int? TargetsTotal = null,
  decimal? PlannedRewardRisk = null,
  bool? TargetRoomFallbackUsed = null,
  string? ExitPath = null,
  int? ConfluenceV1 = null,
  int? ConfluenceV2 = null,
  double? ConfluenceV2Raw = null,
  string? ConfluenceScoringVersion = null,
  // 2026-09 (owner: "collect data 2 weeks to see if order that has good
  // math quality can process well than other or not") - republished
  // from plan.Analysis.MathFibRatio etc. (TradePlan.cs) so Postgres
  // (auto_trade_fills, store.py) can correlate detection-time math
  // telemetry with the eventual fill/outcome.
  double? MathFibRatio = null,
  double? MathVelocity = null,
  double? MathAcceleration = null,
  double? MathPd = null,
  int? MathFeatureVersion = null,
  // MAD v2 context telemetry - republished from plan.Analysis.MadPhase etc.
  // (TradePlan.cs), same pattern as the Math* fields above.
  int? MadVersion = null,
  string? MadPhase = null,
  double? MadConfidence = null,
  double? MadAffinity = null,
  string? MadDirection = null,
  string? MadSweepSide = null,
  bool? MadReclaim = null,
  double? MadRangeQualityAtr = null,
  double? MadBreakDistanceAtr = null,
  double? MadDisplacementAtr = null,
  int? MadAcceptanceCloses = null,
  double? MadSweepPenetrationAtr = null,
  double? MadReclaimDepthAtr = null,
  string? MadReasonCode = null,
  // Candle Confirmation V2 context telemetry - republished from
  // plan.Analysis.CandleVersion etc. (TradePlan.cs), same pattern as the
  // Math*/Mad* fields above.
  int? CandleVersion = null,
  string? CandlePrimaryPattern = null,
  string? CandlePatterns = null,
  double? CandleFinalScore = null,
  double? CandleBaseScore = null,
  double? CandleSynergyBonus = null,
  double? CandleRejectionScore = null,
  double? CandleDisplacementScore = null,
  double? CandleSequenceScore = null,
  double? CandleBodyFraction = null,
  double? CandleUpperWickFraction = null,
  double? CandleLowerWickFraction = null,
  double? CandleCloseLocation = null,
  double? CandleBodyAtr = null,
  double? CandleRangeAtr = null,
  bool? CandleSweep = null,
  double? CandleSweepPenetrationAtr = null,
  bool? CandleReclaim = null,
  double? CandleReclaimDepthAtr = null,
  bool? CandleEngulfing = null,
  bool? CandleDoji = null,
  double? CandleCompressionScore = null,
  string? CandleSequenceName = null,
  int? CandleSequenceBars = null,
  // Opposing Structure V2 context telemetry - republished from
  // plan.Analysis.KeyLevelOpposingZoneLow etc. (TradePlan.cs), same
  // pattern as the Math*/Mad*/Candle* fields above.
  double? KeyLevelOpposingZoneLow = null,
  double? KeyLevelOpposingZoneHigh = null,
  string? KeyLevelOpposingZoneSide = null,
  bool? OpposingZonePresent = null,
  string? OpposingZoneSide = null,
  double? OpposingZoneLow = null,
  double? OpposingZoneHigh = null,
  string? OpposingZoneTier = null,
  double? OpposingZoneScore = null,
  double? OpposingZoneStrength = null,
  double? OpposingRawRoomPrice = null,
  double? OpposingRoomPips = null,
  double? OpposingRoomAtr = null,
  double? OpposingRoomR = null,
  bool? OpposingBeforeTp1 = null,
  bool? OpposingDisplaced = null,
  bool? OpposingMitigated = null,
  double? OpposingRoomPressure = null,
  double? OpposingRiskScore = null,
  string? OpposingAction = null,
  string? OpposingReasonCode = null
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

public sealed record AutoTradeReadinessStatus(
  string State,
  IReadOnlyList<string> Fatal,
  IReadOnlyList<string> Warnings
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
  bool Demo,
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

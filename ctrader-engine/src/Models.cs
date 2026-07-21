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
  string? Regime = null
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
  long InitialTrancheVolume = 0
);

public sealed record AutoTradeEvent(
  string Type,
  long Timestamp,
  string Message,
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
  decimal? CounterfactualPips = null
);

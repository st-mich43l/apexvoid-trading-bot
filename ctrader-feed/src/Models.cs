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
  IReadOnlyList<string> Reasons
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
  long OpenedAt
);

public sealed record AutoTradeEvent(
  string Type,
  long Timestamp,
  string Message,
  string? CandidateId = null,
  long? PositionId = null,
  int? TargetPips = null,
  long? Volume = null,
  decimal? Price = null
);

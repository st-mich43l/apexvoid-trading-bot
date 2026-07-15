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
  int Digits
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

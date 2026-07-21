namespace ApexVoid.CTraderFeed;

public static class ClosedBarCloseResolver
{
  public static async Task<OhlcBar> ResolveAsync(
    ICTraderFeedClient client,
    SymbolInfo symbol,
    string timeframe,
    ClosedBarEmission emission,
    CancellationToken cancellationToken
  )
  {
    if (!emission.RequiresHistoricalClose)
    {
      return emission.Bar;
    }

    var from = DateTimeOffset.FromUnixTimeSeconds(emission.Bar.Timestamp);
    var to = DateTimeOffset.FromUnixTimeSeconds(
      emission.Bar.CloseTimestamp(timeframe) + 1
    );
    var bars = await client.GetTrendbarsAsync(
      symbol,
      timeframe,
      from,
      to,
      cancellationToken
    );
    var raw = bars.LastOrDefault(item =>
      checked((long)item.UtcTimestampInMinutes * 60) == emission.Bar.Timestamp
    );
    if (raw is null)
    {
      throw new InvalidOperationException(
        $"cTrader did not return historical {symbol.RedisSymbol} {timeframe} "
        + $"bar at {emission.Bar.Timestamp} for close fallback"
      );
    }
    if (!raw.HasDeltaClose)
    {
      throw new InvalidOperationException(
        $"historical {symbol.RedisSymbol} {timeframe} bar at "
        + $"{emission.Bar.Timestamp} has no deltaClose"
      );
    }
    return TrendbarDecoder.Decode(raw, symbol.Digits);
  }
}

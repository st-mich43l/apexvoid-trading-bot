namespace ApexVoid.CTraderFeed;

public static class TrendbarDecoder
{
  private const decimal PriceScale = 100_000m;

  public static OhlcBar Decode(RawTrendbar trendbar, int digits)
  {
    _ = digits;
    var low = trendbar.Low;
    return new OhlcBar(
      Timestamp: checked((long)trendbar.UtcTimestampInMinutes * 60),
      Open: (low + (decimal)trendbar.DeltaOpen) / PriceScale,
      High: (low + (decimal)trendbar.DeltaHigh) / PriceScale,
      Low: low / PriceScale,
      Close: (low + (decimal)trendbar.DeltaClose) / PriceScale,
      Volume: trendbar.Volume
    );
  }
}

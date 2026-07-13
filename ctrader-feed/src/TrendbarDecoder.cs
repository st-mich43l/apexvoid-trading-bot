namespace ApexVoid.CTraderFeed;

public static class TrendbarDecoder
{
  public static OhlcBar Decode(RawTrendbar trendbar, int digits)
  {
    _ = digits;
    var low = trendbar.Low;
    return new OhlcBar(
      Timestamp: checked((long)trendbar.UtcTimestampInMinutes * 60),
      Open: OpenApiPrice.Decode(low + (decimal)trendbar.DeltaOpen),
      High: OpenApiPrice.Decode(low + (decimal)trendbar.DeltaHigh),
      Low: OpenApiPrice.Decode(low),
      Close: OpenApiPrice.Decode(low + (decimal)trendbar.DeltaClose),
      Volume: trendbar.Volume
    );
  }
}

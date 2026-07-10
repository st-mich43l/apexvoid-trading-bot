namespace ApexVoid.CTraderFeed;

public static class TrendbarDecoder
{
  public static OhlcBar Decode(RawTrendbar trendbar, int digits)
  {
    var scale = Pow10(digits);
    var low = trendbar.Low;
    return new OhlcBar(
      Timestamp: checked((long)trendbar.UtcTimestampInMinutes * 60),
      Open: (low + (decimal)trendbar.DeltaOpen) / scale,
      High: (low + (decimal)trendbar.DeltaHigh) / scale,
      Low: low / scale,
      Close: (low + (decimal)trendbar.DeltaClose) / scale,
      Volume: trendbar.Volume
    );
  }

  private static decimal Pow10(int digits)
  {
    if (digits < 0 || digits > 28)
    {
      throw new ArgumentOutOfRangeException(nameof(digits), digits, null);
    }
    var result = 1m;
    for (var i = 0; i < digits; i++)
    {
      result *= 10m;
    }
    return result;
  }
}

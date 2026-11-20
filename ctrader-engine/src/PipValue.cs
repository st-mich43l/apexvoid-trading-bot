namespace ApexVoid.CTraderFeed;

/// <summary>
/// FX1: the pip value per lot, in USD, of a pair QUOTED IN JPY (USDJPY, GBPJPY) is the JPY value of a pip divided by the live USDJPY rate
/// (pip size x contract size / USDJPY). A hardcoded constant drifts with the rate: GBPJPY's 7.0 was set when USDJPY was near 143 and is 11% too high at 159.
/// Risk-sized lots divide the risk budget by this value, so a value that is too high sizes the trade too small. The configured value stays the fallback.
/// </summary>
public static class PipValue
{
  /// <summary>A live USDJPY outside this band is a bad tick, not a rate.</summary>
  private const decimal MinUsdJpy = 50m;
  private const decimal MaxUsdJpy = 400m;

  /// <summary>Longest age of the USDJPY quote that is still used.</summary>
  public const long MaxQuoteAgeSeconds = 300;

  public static bool IsJpyQuoted(string canonicalSymbol) =>
    canonicalSymbol is { Length: 6 } symbol
    && symbol.EndsWith("JPY", StringComparison.OrdinalIgnoreCase);

  /// <summary>USD pip value per lot of a JPY-quoted pair, or null when the inputs are not usable (the caller keeps the configured value).</summary>
  public static decimal? JpyQuotedUsdPerLot(decimal pipSize, decimal contractSize, decimal usdJpyBid, decimal usdJpyAsk)
  {
    if (pipSize <= 0m || contractSize <= 0m || usdJpyBid <= 0m || usdJpyAsk < usdJpyBid)
    {
      return null;
    }
    var mid = (usdJpyBid + usdJpyAsk) / 2m;
    if (mid < MinUsdJpy || mid > MaxUsdJpy)
    {
      return null;
    }
    return decimal.Round(pipSize * contractSize / mid, 4, MidpointRounding.AwayFromZero);
  }
}

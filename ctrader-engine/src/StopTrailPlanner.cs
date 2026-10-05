namespace ApexVoid.CTraderFeed;

public static class StopTrailPlanner
{
  public static decimal ProtectedBreakevenStop(
    TradeDirection direction,
    decimal entry,
    SymbolInfo symbol,
    int breakEvenBufferTicks
  )
  {
    if (breakEvenBufferTicks < 0)
    {
      throw new ArgumentOutOfRangeException(nameof(breakEvenBufferTicks));
    }
    var buffer = breakEvenBufferTicks * RequireTickSize(symbol);
    // Profit-side protection: the stop moves past entry by the buffer, in
    // the direction that locks in a small amount of profit rather than
    // merely covering the spread. BUY moves the stop above entry, SELL
    // moves it below entry.
    var stop = direction == TradeDirection.Buy
      ? entry + buffer
      : entry - buffer;
    return decimal.Round(stop, symbol.Digits, MidpointRounding.AwayFromZero);
  }

  public static decimal RequireTickSize(SymbolInfo symbol)
  {
    if (symbol.Digits < 0)
    {
      throw new InvalidOperationException(
        $"Symbol {symbol.CTraderSymbol} has invalid digits {symbol.Digits}"
      );
    }
    var tick = 1m;
    for (var index = 0; index < symbol.Digits; index++)
    {
      tick /= 10m;
    }
    if (tick <= 0)
    {
      throw new InvalidOperationException(
        $"Symbol {symbol.CTraderSymbol} tick size {tick} is not positive"
      );
    }
    return tick;
  }
}

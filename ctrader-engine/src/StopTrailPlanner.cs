namespace ApexVoid.CTraderFeed;

public sealed record StopTrailMove(
  decimal StopLoss,
  string Label,
  decimal? BufferPrice = null
);

public static class StopTrailPlanner
{
  public static StopTrailMove? Plan(
    AutoTradePositionState state,
    int completedTargetIndex,
    SymbolInfo symbol,
    decimal pipSize,
    int breakEvenBufferTicks
  )
  {
    if (
      completedTargetIndex < 0
      || completedTargetIndex >= state.TargetsPips.Count - 1
    )
    {
      return null;
    }
    var completedTargetOrdinal = TargetOrdinal(state, completedTargetIndex);
    if (completedTargetOrdinal == 2)
    {
      return null;
    }
    decimal desired;
    string label;
    decimal? bufferPrice = null;
    if (completedTargetOrdinal == 1)
    {
      var tickSize = RequireTickSize(symbol);
      bufferPrice = breakEvenBufferTicks * tickSize;
      desired = state.Direction == TradeDirection.Buy
        ? state.EntryPrice + bufferPrice.Value
        : state.EntryPrice - bufferPrice.Value;
      label = $"BE+{breakEvenBufferTicks} ticks";
    }
    else
    {
      var trailTargetOrdinal = completedTargetOrdinal - 2;
      var offsetPips = TargetPips(state, trailTargetOrdinal);
      if (offsetPips is null)
      {
        return null;
      }
      desired = state.Direction == TradeDirection.Buy
        ? state.EntryPrice + offsetPips.Value * pipSize
        : state.EntryPrice - offsetPips.Value * pipSize;
      label = $"TP{trailTargetOrdinal}";
    }
    desired = decimal.Round(desired, symbol.Digits, MidpointRounding.AwayFromZero);
    if (
      state.CurrentStopLoss is decimal current
      && !MovesTowardProfit(state.Direction, current, desired)
    )
    {
      return null;
    }
    return new StopTrailMove(desired, label, bufferPrice);
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

  private static int TargetOrdinal(AutoTradePositionState state, int index) =>
    state.TargetOrdinals is { } ordinals && index < ordinals.Count
      ? ordinals[index]
      : index + 1;

  private static int? TargetPips(
    AutoTradePositionState state,
    int targetOrdinal
  )
  {
    if (targetOrdinal < 1)
    {
      return null;
    }
    for (var index = 0; index < state.TargetsPips.Count; index++)
    {
      if (TargetOrdinal(state, index) == targetOrdinal)
      {
        return state.TargetsPips[index];
      }
    }
    return null;
  }

  private static bool MovesTowardProfit(
    TradeDirection direction,
    decimal current,
    decimal desired
  ) => direction == TradeDirection.Buy
    ? desired > current
    : desired < current;
}

namespace ApexVoid.CTraderFeed;

public sealed record StopTrailMove(decimal StopLoss, string Label);

public static class StopTrailPlanner
{
  public static StopTrailMove? Plan(
    AutoTradePositionState state,
    int completedTargetIndex,
    SymbolInfo symbol,
    int breakEvenBufferPips
  )
  {
    if (
      completedTargetIndex < 0
      || completedTargetIndex >= state.TargetsPips.Count - 1
    )
    {
      return null;
    }
    var pip = VolumePlanner.PipSize(symbol);
    var offsetPips = completedTargetIndex == 0
      ? breakEvenBufferPips
      : state.TargetsPips[completedTargetIndex - 1];
    var desired = state.Direction == TradeDirection.Buy
      ? state.EntryPrice + offsetPips * pip
      : state.EntryPrice - offsetPips * pip;
    desired = decimal.Round(desired, symbol.Digits, MidpointRounding.AwayFromZero);
    if (
      state.CurrentStopLoss is decimal current
      && !MovesTowardProfit(state.Direction, current, desired)
    )
    {
      return null;
    }
    var label = completedTargetIndex == 0
      ? $"BE+{breakEvenBufferPips}"
      : $"TP{completedTargetIndex}";
    return new StopTrailMove(desired, label);
  }

  private static bool MovesTowardProfit(
    TradeDirection direction,
    decimal current,
    decimal desired
  ) => direction == TradeDirection.Buy
    ? desired > current
    : desired < current;
}

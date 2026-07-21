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
    var completedTargetOrdinal = TargetOrdinal(state, completedTargetIndex);
    if (completedTargetOrdinal == 2)
    {
      return null;
    }
    var pip = VolumePlanner.PipSize(symbol);
    var trailTargetOrdinal = completedTargetOrdinal - 2;
    var offsetPips = completedTargetOrdinal == 1
      ? breakEvenBufferPips
      : TargetPips(state, trailTargetOrdinal);
    if (offsetPips is null)
    {
      return null;
    }
    var desired = state.Direction == TradeDirection.Buy
      ? state.EntryPrice + offsetPips.Value * pip
      : state.EntryPrice - offsetPips.Value * pip;
    desired = decimal.Round(desired, symbol.Digits, MidpointRounding.AwayFromZero);
    if (
      state.CurrentStopLoss is decimal current
      && !MovesTowardProfit(state.Direction, current, desired)
    )
    {
      return null;
    }
    var label = completedTargetOrdinal == 1
      ? $"BE+{breakEvenBufferPips}"
      : $"TP{trailTargetOrdinal}";
    return new StopTrailMove(desired, label);
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

namespace ApexVoid.CTraderFeed;

public enum ExposurePolicy
{
  FlatOnly,
  SameDirectionConcurrent,
  HedgedConcurrent,
}

public static class ExposurePolicyRules
{
  public static bool AllowsNewGroup(
    ExposurePolicy policy,
    TradeDirection direction,
    IReadOnlyList<TradingPosition> botPositions,
    IReadOnlyList<TradingPendingOrder> botOrders
  )
  {
    if (policy == ExposurePolicy.HedgedConcurrent)
    {
      return true;
    }
    if (policy == ExposurePolicy.FlatOnly)
    {
      return botPositions.Count == 0 && botOrders.Count == 0;
    }
    return botPositions.All(item => item.Direction == direction)
      && botOrders.All(item => item.Direction == direction);
  }
}

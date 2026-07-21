namespace ApexVoid.CTraderFeed;

public sealed record StructureStopPlan(
  decimal StopLoss,
  decimal Distance,
  decimal StopPips,
  decimal RawStopLoss,
  bool Clamped
);

public static class StructureStopPlanner
{
  public static StructureStopPlan Plan(
    TradeDirection direction,
    decimal entryPrice,
    decimal structureSwing,
    decimal atr,
    decimal bufferAtr,
    int minimumStopPips,
    int maximumStopPips,
    SymbolInfo symbol
  )
  {
    if (
      entryPrice <= 0
      || structureSwing <= 0
      || atr <= 0
      || bufferAtr < 0
      || minimumStopPips <= 0
      || maximumStopPips < minimumStopPips
    )
    {
      throw new VolumePlanningException("Structure-stop inputs are invalid");
    }
    var pip = VolumePlanner.PipSize(symbol);
    var rawStop = direction == TradeDirection.Buy
      ? structureSwing - bufferAtr * atr
      : structureSwing + bufferAtr * atr;
    var rawDistance = direction == TradeDirection.Buy
      ? entryPrice - rawStop
      : rawStop - entryPrice;
    if (rawDistance <= 0)
    {
      throw new VolumePlanningException(
        "Structure invalidation is not on the losing side of entry"
      );
    }
    var rawPips = rawDistance / pip;
    var stopPips = Math.Clamp(
      rawPips,
      Convert.ToDecimal(minimumStopPips),
      Convert.ToDecimal(maximumStopPips)
    );
    var distance = stopPips * pip;
    var stopLoss = direction == TradeDirection.Buy
      ? entryPrice - distance
      : entryPrice + distance;
    stopLoss = decimal.Round(
      stopLoss,
      symbol.Digits,
      MidpointRounding.AwayFromZero
    );
    distance = Math.Abs(entryPrice - stopLoss);
    stopPips = distance / pip;
    return new StructureStopPlan(
      stopLoss,
      distance,
      stopPips,
      rawStop,
      stopPips != rawPips
    );
  }
}

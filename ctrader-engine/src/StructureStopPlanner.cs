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
    decimal? sweepExtreme,
    decimal wickBufferAtr,
    int minimumStopPips,
    int maximumStopPips,
    decimal pipSize,
    SymbolInfo symbol
  )
  {
    if (
      entryPrice <= 0
      || structureSwing <= 0
      || atr <= 0
      || bufferAtr < 0
      || wickBufferAtr < 0
      || minimumStopPips <= 0
      || maximumStopPips < minimumStopPips
      || pipSize <= 0
    )
    {
      throw new VolumePlanningException("Structure-stop inputs are invalid");
    }
    var rawStop = direction == TradeDirection.Buy
      ? structureSwing - bufferAtr * atr
      : structureSwing + bufferAtr * atr;
    if (sweepExtreme is decimal sweep)
    {
      if (sweep <= 0)
      {
        throw new VolumePlanningException("Sweep extreme is invalid");
      }
      var wickStop = direction == TradeDirection.Buy
        ? sweep - wickBufferAtr * atr
        : sweep + wickBufferAtr * atr;
      var wickDistance = direction == TradeDirection.Buy
        ? entryPrice - wickStop
        : wickStop - entryPrice;
      if (wickDistance <= 0)
      {
        throw new VolumePlanningException(
          "Sweep invalidation is not on the losing side of entry"
        );
      }
      if (wickDistance / pipSize > maximumStopPips)
      {
        throw new VolumePlanningException("stop_exceeds_envelope_after_wick");
      }
      rawStop = direction == TradeDirection.Buy
        ? Math.Min(rawStop, wickStop)
        : Math.Max(rawStop, wickStop);
    }
    var rawDistance = direction == TradeDirection.Buy
      ? entryPrice - rawStop
      : rawStop - entryPrice;
    if (rawDistance <= 0)
    {
      throw new VolumePlanningException(
        "Structure invalidation is not on the losing side of entry"
      );
    }
    var rawPips = rawDistance / pipSize;
    var stopPips = Math.Clamp(
      rawPips,
      Convert.ToDecimal(minimumStopPips),
      Convert.ToDecimal(maximumStopPips)
    );
    var distance = stopPips * pipSize;
    var stopLoss = direction == TradeDirection.Buy
      ? entryPrice - distance
      : entryPrice + distance;
    stopLoss = decimal.Round(
      stopLoss,
      symbol.Digits,
      MidpointRounding.AwayFromZero
    );
    distance = Math.Abs(entryPrice - stopLoss);
    stopPips = distance / pipSize;
    return new StructureStopPlan(
      stopLoss,
      distance,
      stopPips,
      rawStop,
      stopPips != rawPips
    );
  }
}

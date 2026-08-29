namespace ApexVoid.CTraderFeed;

public sealed record OpposingZoneStopContext(
  string? ZoneId,
  decimal Low,
  decimal High,
  bool ExecutionGrade,
  bool PushBeyondZone,
  decimal BufferAtr
);

public sealed record StructureStopPlan(
  decimal StopLoss,
  decimal Distance,
  decimal StopPips,
  decimal RawStopLoss,
  bool Clamped,
  string Source = "structure",
  decimal? BaseStopLoss = null,
  decimal? BaseStopPips = null,
  string Adjustment = "none",
  string? AdjustmentZoneId = null,
  decimal? AdjustmentZoneLow = null,
  decimal? AdjustmentZoneHigh = null,
  int Version = 1
);

public static class StructureStopPlanner
{
  public const int StopPlanVersion = 2;

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
    SymbolInfo symbol,
    OpposingZoneStopContext? opposingZone = null
  )
  {
    var basePlan = PlanBase(
      direction,
      entryPrice,
      structureSwing,
      atr,
      bufferAtr,
      sweepExtreme,
      wickBufferAtr,
      minimumStopPips,
      maximumStopPips,
      pipSize,
      symbol
    );
    return ApplyOpposingZoneAdjustment(
      basePlan,
      direction,
      entryPrice,
      opposingZone,
      atr,
      maximumStopPips,
      pipSize,
      symbol
    );
  }

  public static StructureStopPlan PlanBase(
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
    var source = "structure";
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
      var selectedStop = direction == TradeDirection.Buy
        ? Math.Min(rawStop, wickStop)
        : Math.Max(rawStop, wickStop);
      if (selectedStop == wickStop)
      {
        source = selectedStop == rawStop ? "structure_and_wick" : "wick";
      }
      rawStop = selectedStop;
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
    // V6 StructureStopPlanner still clamps into the envelope (historical
    // contract used by AutoTradeEngine). The TradePlan/zone-scale group path rejects
    // over-max in Python plan_group_protective_stop instead of pulling the
    // stop inward before structural invalidation.
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
      stopPips != rawPips,
      source,
      stopLoss,
      stopPips
    );
  }

  public static StructureStopPlan ApplyOpposingZoneAdjustment(
    StructureStopPlan basePlan,
    TradeDirection direction,
    decimal entryPrice,
    OpposingZoneStopContext? opposingZone,
    decimal atr,
    int maximumStopPips,
    decimal pipSize,
    SymbolInfo symbol
  )
  {
    var baseStopLoss = basePlan.BaseStopLoss ?? basePlan.StopLoss;
    var baseStopPips = basePlan.BaseStopPips ?? basePlan.StopPips;
    if (opposingZone is null || !opposingZone.ExecutionGrade)
    {
      return basePlan with
      {
        BaseStopLoss = baseStopLoss,
        BaseStopPips = baseStopPips,
        Version = StopPlanVersion,
      };
    }
    if (baseStopLoss < opposingZone.Low || baseStopLoss > opposingZone.High)
    {
      return basePlan with
      {
        BaseStopLoss = baseStopLoss,
        BaseStopPips = baseStopPips,
        Version = StopPlanVersion,
      };
    }
    if (!opposingZone.PushBeyondZone)
    {
      throw new VolumePlanningException("stop_inside_opposing_zone");
    }
    var buffer = opposingZone.BufferAtr * atr;
    var pushedStop = direction == TradeDirection.Buy
      ? opposingZone.Low - buffer
      : opposingZone.High + buffer;
    pushedStop = decimal.Round(
      pushedStop,
      symbol.Digits,
      MidpointRounding.AwayFromZero
    );
    var pushedDistance = Math.Abs(entryPrice - pushedStop);
    if (
      direction == TradeDirection.Buy
        ? pushedStop >= entryPrice
        : pushedStop <= entryPrice
    )
    {
      throw new VolumePlanningException(
        "Opposing-zone push is not on the losing side of entry"
      );
    }
    var pushedPips = pushedDistance / pipSize;
    if (pushedPips > maximumStopPips)
    {
      throw new VolumePlanningException("stop_inside_opposing_zone");
    }
    return basePlan with
    {
      StopLoss = pushedStop,
      Distance = pushedDistance,
      StopPips = pushedPips,
      BaseStopLoss = baseStopLoss,
      BaseStopPips = baseStopPips,
      Clamped = true,
      Adjustment = "opposing_zone_push",
      AdjustmentZoneId = opposingZone.ZoneId,
      AdjustmentZoneLow = opposingZone.Low,
      AdjustmentZoneHigh = opposingZone.High,
      Version = StopPlanVersion,
    };
  }
}

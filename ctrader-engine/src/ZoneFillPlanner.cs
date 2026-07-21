namespace ApexVoid.CTraderFeed;

public sealed record ZoneFillLegPlan(
  int Leg,
  decimal LimitPrice,
  long Volume,
  TargetVolumePlan TargetPlan
);

public sealed record ZoneFillPlan(
  decimal StopLoss,
  IReadOnlyList<ZoneFillLegPlan> Legs
);

public static class ZoneFillPlanner
{
  public static bool Qualifies(
    TradeCandidateZone zone,
    decimal atr,
    decimal minimumWidthAtr
  ) => atr > 0
    && minimumWidthAtr > 0
    && zone.High > zone.Low
    && zone.High - zone.Low >= minimumWidthAtr * atr;

  public static ZoneFillPlan Build(
    TradeDirection direction,
    TradeCandidateZone zone,
    decimal stopLoss,
    long totalVolume,
    SymbolInfo symbol,
    IReadOnlyList<int> targetsPips,
    IReadOnlyList<int> targetWeights
  )
  {
    if (zone.High <= zone.Low)
    {
      throw new VolumePlanningException("zone fill requires a positive-width zone");
    }
    var totalSteps = totalVolume / symbol.StepVolume;
    if (totalSteps < 4 || totalVolume % symbol.StepVolume != 0)
    {
      throw new VolumePlanningException(
        "zone fill needs at least four broker volume steps for two ladders"
      );
    }
    var proximalSteps = (totalSteps + 1) / 2;
    var midpointSteps = totalSteps - proximalSteps;
    var volumes = new[] {
      proximalSteps * symbol.StepVolume,
      midpointSteps * symbol.StepVolume,
    };
    var midpoint = decimal.Round(
      (zone.Low + zone.High) / 2m,
      symbol.Digits,
      MidpointRounding.AwayFromZero
    );
    var proximal = direction == TradeDirection.Buy ? zone.High : zone.Low;
    var prices = new[] { proximal, midpoint };
    var legs = Enumerable.Range(0, 2).Select(index => new ZoneFillLegPlan(
      index + 1,
      prices[index],
      volumes[index],
      VolumePlanner.BuildTargetPlan(
        volumes[index],
        symbol,
        targetsPips,
        targetWeights
      )
    )).ToArray();
    if (legs.Sum(leg => leg.Volume) != totalVolume)
    {
      throw new InvalidOperationException("Zone-fill volume invariant violated");
    }
    return new ZoneFillPlan(stopLoss, legs);
  }
}

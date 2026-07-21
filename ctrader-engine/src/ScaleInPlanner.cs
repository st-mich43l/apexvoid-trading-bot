using System.Globalization;

namespace ApexVoid.CTraderFeed;

public sealed record TrancheExposure(
  TradeDirection Direction,
  decimal EntryPrice,
  decimal StopLoss,
  long RemainingVolume
);

public sealed record ScaleInSizingDecision(
  bool Allowed,
  string Reason,
  string? BindingTerm,
  decimal Lots,
  long Volume,
  decimal Budget,
  decimal HeadroomLots,
  decimal RiskHeadroomLots,
  decimal AddCapLots,
  decimal CurrentWorstCase,
  decimal PostAddWorstCase,
  TargetVolumePlan? TargetPlan,
  string SizingLog
);

public static class ScaleInPlanner
{
  public static ScaleInSizingDecision Plan(
    decimal balance,
    decimal riskPercent,
    decimal pipValuePerLot,
    decimal addRiskFraction,
    decimal addStopPips,
    decimal bookedPnl,
    IReadOnlyList<TrancheExposure> openTranches,
    bool requireRiskFree,
    SymbolInfo symbol,
    IReadOnlyList<int> targetsPips,
    IReadOnlyList<int> targetWeights
  )
  {
    if (
      balance <= 0
      || riskPercent <= 0
      || pipValuePerLot <= 0
      || addRiskFraction <= 0
      || addRiskFraction > 1
      || addStopPips <= 0
    )
    {
      return Reject("add sizing inputs are invalid");
    }
    var budget = balance * riskPercent / 100m;
    var tableLots = VolumePlanner.LotsForBalance(balance);
    var openLots = openTranches.Sum(item => Lots(item.RemainingVolume, symbol));
    var stopPnl = openTranches.Sum(item => StopPnl(
      item,
      pipValuePerLot,
      symbol
    ));
    var currentWorstCase = bookedPnl + stopPnl;
    var headroomRisk = budget + currentWorstCase;
    if (headroomRisk <= 0)
    {
      return Reject(
        $"group loss ceiling exhausted: headroom ${Money(headroomRisk)}",
        budget,
        currentWorstCase
      );
    }

    var headroomLots = Math.Max(0m, tableLots - openLots);
    if (headroomLots <= 0)
    {
      return Reject(
        "exposure ceiling reached; bank a partial first",
        budget,
        currentWorstCase,
        headroomLots
      );
    }
    var riskHeadroomLots = headroomRisk / (addStopPips * pipValuePerLot);
    var addCapLots = addRiskFraction * budget
      / (addStopPips * pipValuePerLot);
    var rawLots = Math.Min(
      headroomLots,
      Math.Min(riskHeadroomLots, addCapLots)
    );
    var bindingTerm = BindingTerm(
      rawLots,
      headroomLots,
      riskHeadroomLots,
      addCapLots
    );
    var volume = VolumePlanner.VolumeForLots(rawLots, symbol);
    if (volume <= 0)
    {
      return Reject(
        "add sizing is below broker minimum volume",
        budget,
        currentWorstCase,
        headroomLots,
        riskHeadroomLots,
        addCapLots,
        bindingTerm
      );
    }
    var lots = Lots(volume, symbol);
    TargetVolumePlan targetPlan;
    try
    {
      targetPlan = VolumePlanner.BuildTargetPlan(
        volume,
        symbol,
        targetsPips,
        targetWeights
      );
    }
    catch (VolumePlanningException exception)
    {
      return Reject(
        $"add ladder infeasible: {exception.Message}",
        budget,
        currentWorstCase,
        headroomLots,
        riskHeadroomLots,
        addCapLots,
        bindingTerm
      );
    }

    var addRisk = lots * addStopPips * pipValuePerLot;
    var postAddWorstCase = currentWorstCase - addRisk;
    if (postAddWorstCase < -budget)
    {
      return Reject(
        $"group loss ceiling would be breached: worst "
        + $"${Money(postAddWorstCase)} / budget ${Money(budget)}",
        budget,
        currentWorstCase,
        headroomLots,
        riskHeadroomLots,
        addCapLots,
        bindingTerm,
        postAddWorstCase
      );
    }
    if (requireRiskFree && postAddWorstCase < 0)
    {
      return Reject(
        $"risk-free mode: post-add group worst ${Money(postAddWorstCase)} < $0",
        budget,
        currentWorstCase,
        headroomLots,
        riskHeadroomLots,
        addCapLots,
        bindingTerm,
        postAddWorstCase
      );
    }

    AssertInvariants(
      tableLots,
      openLots + lots,
      budget,
      postAddWorstCase,
      addRiskFraction * budget,
      addRisk
    );
    var sizingLog = "add sizing: headroom_lots "
      + $"{Number(headroomLots)} / risk {Number(riskHeadroomLots)} / "
      + $"cap {Number(addCapLots)} → {Number(lots)} ({bindingTerm})";
    return new ScaleInSizingDecision(
      true,
      string.Empty,
      bindingTerm,
      lots,
      volume,
      budget,
      headroomLots,
      riskHeadroomLots,
      addCapLots,
      currentWorstCase,
      postAddWorstCase,
      targetPlan,
      sizingLog
    );
  }

  public static decimal StopPnl(
    TrancheExposure tranche,
    decimal pipValuePerLot,
    SymbolInfo symbol
  )
  {
    var move = tranche.Direction == TradeDirection.Buy
      ? tranche.StopLoss - tranche.EntryPrice
      : tranche.EntryPrice - tranche.StopLoss;
    var pips = move / VolumePlanner.PipSize(symbol);
    return pips * Lots(tranche.RemainingVolume, symbol) * pipValuePerLot;
  }

  public static void AssertInvariants(
    decimal tableLots,
    decimal postAddLots,
    decimal budget,
    decimal postAddWorstCase,
    decimal addRiskCap,
    decimal addRisk
  )
  {
    const decimal epsilon = 0.000001m;
    if (postAddLots > tableLots + epsilon)
    {
      throw new InvalidOperationException("Scale-in exposure invariant violated");
    }
    if (postAddWorstCase < -budget - epsilon)
    {
      throw new InvalidOperationException("Scale-in group loss invariant violated");
    }
    if (addRisk > addRiskCap + epsilon)
    {
      throw new InvalidOperationException("Scale-in add risk cap violated");
    }
  }

  private static ScaleInSizingDecision Reject(
    string reason,
    decimal budget = 0,
    decimal currentWorstCase = 0,
    decimal headroomLots = 0,
    decimal riskHeadroomLots = 0,
    decimal addCapLots = 0,
    string? bindingTerm = null,
    decimal postAddWorstCase = 0
  ) => new(
    false,
    reason,
    bindingTerm,
    0,
    0,
    budget,
    headroomLots,
    riskHeadroomLots,
    addCapLots,
    currentWorstCase,
    postAddWorstCase,
    null,
    string.Empty
  );

  private static string BindingTerm(
    decimal chosen,
    decimal exposure,
    decimal risk,
    decimal cap
  )
  {
    if (chosen == exposure)
    {
      return "exposure-bound";
    }
    return chosen == risk ? "risk-bound" : "add-cap-bound";
  }

  private static decimal Lots(long volume, SymbolInfo symbol) =>
    symbol.LotSize > 0 ? volume / (decimal)symbol.LotSize : 0m;

  private static string Number(decimal value) =>
    value.ToString("0.##", CultureInfo.InvariantCulture);

  private static string Money(decimal value) =>
    value.ToString("0.##", CultureInfo.InvariantCulture);
}

using System.Globalization;

namespace ApexVoid.CTraderFeed;

public sealed record RiskSizingResult(
  decimal RiskAmount,
  decimal ComputedLots,
  decimal Lots,
  long Volume,
  decimal StopPips
);

public sealed class VolumePlanningException(string message)
  : InvalidOperationException(message);

public static class VolumePlanner
{
  public static RiskSizingResult SizeForRisk(
    decimal balance,
    decimal riskPercent,
    decimal stopDistance,
    decimal pipValuePerLot,
    decimal maxLots,
    SymbolInfo symbol
  )
  {
    ValidateInputs(
      balance,
      riskPercent,
      stopDistance,
      pipValuePerLot,
      maxLots,
      symbol
    );
    var stopPips = stopDistance / PipSize(symbol);
    var riskAmount = balance * (riskPercent / 100m);
    var computedLots = riskAmount / (stopPips * pipValuePerLot);
    var brokerMaxLots = (decimal)symbol.MaxVolume / symbol.LotSize;
    var cappedLots = Math.Min(computedLots, Math.Min(maxLots, brokerMaxLots));
    var rawVolume = decimal.ToInt64(decimal.Floor(cappedLots * symbol.LotSize));
    var volume = rawVolume / symbol.StepVolume * symbol.StepVolume;
    if (volume < symbol.MinVolume)
    {
      var minimumLots = (decimal)symbol.MinVolume / symbol.LotSize;
      if (computedLots >= minimumLots)
      {
        throw new VolumePlanningException(
          $"maximum {Number(maxLots)} lots is below the broker minimum "
          + $"{Number(minimumLots)}"
        );
      }
      throw new VolumePlanningException(
        $"balance {Money(balance)} × {Number(riskPercent)}% = ${Money(riskAmount)} "
        + $"over a {Number(stopPips)}-pip stop → {TruncatedLots(computedLots)} lots, "
        + $"below the {Number(minimumLots)} minimum"
      );
    }
    return new RiskSizingResult(
      riskAmount,
      computedLots,
      (decimal)volume / symbol.LotSize,
      volume,
      stopPips
    );
  }

  public static void EnsureTargetFeasibility(
    RiskSizingResult sizing,
    decimal balance,
    decimal riskPercent,
    decimal pipValuePerLot,
    int targetCount,
    SymbolInfo symbol
  )
  {
    if (targetCount <= 0)
    {
      throw new VolumePlanningException("At least one target is required");
    }
    var availableSteps = sizing.Volume / symbol.StepVolume;
    var minimumStepsPerTarget = Math.Max(
      1,
      (symbol.MinVolume + symbol.StepVolume - 1) / symbol.StepVolume
    );
    var requiredSteps = checked(targetCount * minimumStepsPerTarget);
    if (availableSteps >= requiredSteps)
    {
      return;
    }
    var requiredLots = (decimal)(requiredSteps * symbol.StepVolume) / symbol.LotSize;
    var requiredBalance = requiredLots * sizing.StopPips * pipValuePerLot
      / (riskPercent / 100m);
    var maximumStopPips = balance * (riskPercent / 100m)
      / (requiredLots * pipValuePerLot);
    var remedyStop = maximumStopPips >= 10m
      ? decimal.Floor(maximumStopPips / 10m) * 10m
      : decimal.Floor(maximumStopPips);
    var stopRemedy = remedyStop > 0
      ? $" (or a stop ≤ {Number(remedyStop)} pips at the current balance)"
      : string.Empty;
    throw new VolumePlanningException(
      $"{Number(sizing.Lots)} lots = {availableSteps} steps; "
      + $"{targetCount} targets need {requiredSteps}. At {Number(riskPercent)}% risk "
      + $"with a {Number(sizing.StopPips)}-pip stop this needs balance ≥ "
      + $"${decimal.Ceiling(requiredBalance).ToString("N0", CultureInfo.InvariantCulture)}"
      + stopRemedy
    );
  }

  public static IReadOnlyList<long> SplitWeighted(
    long volume,
    SymbolInfo symbol,
    IReadOnlyList<int> weights
  )
  {
    if (
      volume <= 0
      || symbol.StepVolume <= 0
      || symbol.MinVolume <= 0
      || volume % symbol.StepVolume != 0
    )
    {
      throw new VolumePlanningException("Position volume is not broker-step aligned");
    }
    if (weights.Count == 0 || weights.Any(weight => weight <= 0))
    {
      throw new VolumePlanningException("Target weights must all be positive");
    }
    var totalWeight = weights.Sum();
    var totalSteps = volume / symbol.StepVolume;
    var minimumSteps = Math.Max(
      1,
      (symbol.MinVolume + symbol.StepVolume - 1) / symbol.StepVolume
    );
    var requiredSteps = checked(minimumSteps * weights.Count);
    if (totalSteps < requiredSteps)
    {
      throw new VolumePlanningException(
        $"{totalSteps} volume steps cannot cover {weights.Count} targets"
      );
    }

    var remaining = totalSteps - requiredSteps;
    var steps = Enumerable.Repeat(minimumSteps, weights.Count).ToArray();
    var remainders = new decimal[weights.Count];
    for (var index = 0; index < weights.Count; index++)
    {
      var ideal = (decimal)remaining * weights[index] / totalWeight;
      var whole = decimal.ToInt64(decimal.Floor(ideal));
      steps[index] += whole;
      remainders[index] = ideal - whole;
    }
    var leftover = totalSteps - steps.Sum();
    foreach (
      var index in Enumerable.Range(0, weights.Count)
        .OrderByDescending(index => remainders[index])
        .ThenBy(index => index)
        .Take(checked((int)leftover))
    )
    {
      steps[index]++;
    }
    return steps.Select(step => step * symbol.StepVolume).ToArray();
  }

  public static decimal PipSize(SymbolInfo symbol)
  {
    var divisor = 1m;
    for (var index = 0; index < symbol.PipPosition; index++)
    {
      divisor *= 10m;
    }
    return 1m / divisor;
  }

  private static void ValidateInputs(
    decimal balance,
    decimal riskPercent,
    decimal stopDistance,
    decimal pipValuePerLot,
    decimal maxLots,
    SymbolInfo symbol
  )
  {
    if (balance <= 0 || riskPercent <= 0 || stopDistance <= 0)
    {
      throw new VolumePlanningException("Risk sizing inputs must be positive");
    }
    if (pipValuePerLot <= 0 || maxLots <= 0)
    {
      throw new VolumePlanningException("Pip value and maximum lots must be positive");
    }
    if (
      symbol.LotSize <= 0
      || symbol.MinVolume <= 0
      || symbol.StepVolume <= 0
      || symbol.MaxVolume < symbol.MinVolume
    )
    {
      throw new VolumePlanningException("Broker symbol volume metadata is invalid");
    }
  }

  private static string Money(decimal value) =>
    value.ToString("N2", CultureInfo.InvariantCulture);

  private static string Number(decimal value) =>
    value.ToString("0.##", CultureInfo.InvariantCulture);

  private static string TruncatedLots(decimal value) =>
    (decimal.Floor(value * 1_000m) / 1_000m)
      .ToString("0.000", CultureInfo.InvariantCulture);
}

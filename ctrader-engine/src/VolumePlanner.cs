namespace ApexVoid.CTraderFeed;

public sealed record TargetVolumePlan(
  IReadOnlyList<long> Slices,
  IReadOnlyList<int> TargetsPips,
  IReadOnlyList<int> TargetOrdinals
);

public sealed record InitialSizingResult(
  decimal Budget,
  decimal RiskLots,
  decimal TableLots,
  decimal Lots,
  long Volume,
  decimal StopPips,
  string BindingTerm,
  TargetVolumePlan TargetPlan
);

public sealed class VolumePlanningException(string message)
  : InvalidOperationException(message);

public static class VolumePlanner
{
  public static InitialSizingResult SizeInitial(
    decimal balance,
    decimal riskPercent,
    decimal stopPips,
    decimal pipValuePerLot,
    SymbolInfo symbol,
    IReadOnlyList<int> targetsPips,
    IReadOnlyList<int> targetWeights
  )
  {
    if (
      balance <= 0
      || riskPercent <= 0
      || stopPips <= 0
      || pipValuePerLot <= 0
    )
    {
      throw new VolumePlanningException("Initial sizing inputs must be positive");
    }
    var budget = balance * riskPercent / 100m;
    var riskLots = budget / (stopPips * pipValuePerLot);
    var tableLots = LotsForBalance(balance);
    if (tableLots <= 0)
    {
      throw new VolumePlanningException(
        $"balance {balance:N2} is below the $200 sizing floor"
      );
    }
    var rawLots = Math.Min(riskLots, tableLots);
    var volume = VolumeForLots(rawLots, symbol);
    if (volume <= 0)
    {
      throw new VolumePlanningException(
        $"min(risk {riskLots:0.###}, table {tableLots:0.##}) lots is below "
        + "broker minimum volume"
      );
    }
    var lots = volume / (decimal)symbol.LotSize;
    var targetPlan = BuildTargetPlan(
      volume,
      symbol,
      targetsPips,
      targetWeights
    );
    return new InitialSizingResult(
      budget,
      riskLots,
      tableLots,
      lots,
      volume,
      stopPips,
      tableLots <= riskLots ? "equity-table-bound" : "risk-bound",
      targetPlan
    );
  }

  public static decimal LotsForBalance(decimal balance)
  {
    if (balance < 200m)
    {
      return 0m;
    }
    var rawLots = balance switch
    {
      >= 5_000m => 0.36m,
      >= 3_000m => 0.31m + (balance - 3_000m) * 0.05m / 2_000m,
      >= 1_000m => 0.11m + (balance - 1_000m) * 0.20m / 2_000m,
      >= 500m => 0.05m + (balance - 500m) * 0.06m / 500m,
      _ => 0.02m + (balance - 200m) * 0.03m / 300m,
    };
    return decimal.Floor(rawLots * 100m) / 100m;
  }

  public static long VolumeForLots(decimal lots, SymbolInfo symbol)
  {
    if (
      lots <= 0
      || symbol.LotSize <= 0
      || symbol.MinVolume <= 0
      || symbol.StepVolume <= 0
      || symbol.MaxVolume < symbol.MinVolume
    )
    {
      return 0;
    }
    var raw = decimal.Floor(lots * symbol.LotSize);
    if (raw > symbol.MaxVolume)
    {
      return 0;
    }
    var stepped = decimal.ToInt64(raw) / symbol.StepVolume * symbol.StepVolume;
    return stepped >= symbol.MinVolume ? stepped : 0;
  }

  public static TargetVolumePlan BuildTargetPlan(
    long volume,
    SymbolInfo symbol,
    IReadOnlyList<int> targetsPips,
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
    if (
      targetsPips.Count < 1
      || weights.Count != targetsPips.Count
      || targetsPips.Any(target => target <= 0)
      || weights.Any(weight => weight <= 0)
    )
    {
      throw new VolumePlanningException("Target plan configuration is invalid");
    }
    var minimumSteps = MinimumStepsPerClose(symbol);
    var totalSteps = volume / symbol.StepVolume;
    var availableExits = totalSteps / minimumSteps;
    var requiredExits = targetsPips.Count == 1 ? 1 : 2;
    if (availableExits < requiredExits)
    {
      throw new VolumePlanningException(
        requiredExits == 1
          ? "Configured volume cannot support a broker-valid exit"
          : "Configured volume cannot support the minimum two broker-valid exits"
      );
    }
    var selectedCount = (int)Math.Min(availableExits, targetsPips.Count);
    var indices = selectedCount == 2 && targetsPips.Count >= 3
      ? new[] { 0, 2 }
      : Enumerable.Range(0, selectedCount).ToArray();
    var selectedTargets = indices.Select(index => targetsPips[index]).ToArray();
    var selectedWeights = indices.Select(index => weights[index]).ToArray();
    return new TargetVolumePlan(
      SplitWeighted(volume, symbol, selectedWeights),
      selectedTargets,
      indices.Select(index => index + 1).ToArray()
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
    var minimumSteps = MinimumStepsPerClose(symbol);
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

  /// <summary>
  /// Derives the broker-reported pip size for diagnostics only. Price-to-pip
  /// conversions must use the configured AutoTradeOptions.PipSize value.
  /// </summary>
  public static decimal BrokerPipSize(SymbolInfo symbol)
  {
    var divisor = 1m;
    for (var index = 0; index < symbol.PipPosition; index++)
    {
      divisor *= 10m;
    }
    return 1m / divisor;
  }

  public static (string Message, bool Differs) PipUnitDiagnostic(
    SymbolInfo symbol,
    AutoTradeOptions options
  )
  {
    var brokerPipSize = BrokerPipSize(symbol);
    var message = $"auto-trade units: pipSize={options.PipSize} (configured) "
      + $"brokerPipPosition={symbol.PipPosition} (->{brokerPipSize}, ignored) "
      + $"contractSize={options.ContractSize} "
      + $"pipValuePerLot={options.PipValuePerLot:0.00} "
      + $"symbol={symbol.CTraderSymbol} digits={symbol.Digits} "
      + $"lotSize={symbol.LotSize}";
    return (message, brokerPipSize != options.PipSize);
  }

  private static long MinimumStepsPerClose(SymbolInfo symbol) => Math.Max(
    1,
    (symbol.MinVolume + symbol.StepVolume - 1) / symbol.StepVolume
  );
}

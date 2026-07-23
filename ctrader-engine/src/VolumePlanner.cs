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
    string sizingMode,
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
    var rawLots = sizingMode switch
    {
      "table" => tableLots,
      "risk" => riskLots,
      _ => Math.Min(riskLots, tableLots),
    };
    var volume = VolumeForLots(rawLots, symbol);
    if (volume <= 0)
    {
      throw new VolumePlanningException(
        $"sizing={sizingMode} lots={rawLots:0.###} (risk {riskLots:0.###}, "
        + $"table {tableLots:0.##}) is below broker minimum volume"
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
      $"sizing={sizingMode} lots={lots:0.00} (risk {riskLots:0.00}, "
        + $"table {tableLots:0.00})",
      targetPlan
    );
  }

  public static decimal LotsForBalance(decimal balance)
  {
    if (balance < 200m)
    {
      return 0m;
    }
    // The upward discontinuities at band boundaries are intentional.
    var rawLots = balance switch
    {
      >= 5_000m => 0.30m,
      >= 3_000m => 0.25m + (balance - 3_000m) * 0.05m / 2_000m,
      >= 2_000m => 0.15m,
      >= 1_000m => 0.09m + (balance - 1_000m) * 0.06m / 1_000m,
      >= 900m => 0.06m,
      _ => 0.02m + (balance - 200m) * 0.04m / 700m,
    };
    // Snap currency-cent band endpoints to their intended lot-cent anchor.
    rawLots = decimal.Round(rawLots, 5, MidpointRounding.AwayFromZero);
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

  /// <summary>
  /// Overrides a target plan's first leg to a fixed broker volume (e.g. a
  /// consistent ~0.05 lot first booking on larger manual /algo positions,
  /// rather than a proportional share that grows with account size),
  /// splitting the remainder evenly across the remaining legs. Fails open
  /// (returns <paramref name="plan"/> unchanged) if there's only one leg,
  /// the fixed amount doesn't strictly fit inside the total, or the
  /// remainder can't cover the remaining legs' broker minimums - callers
  /// should not have to special-case an edge configuration just to try
  /// this rebalance.
  /// </summary>
  public static TargetVolumePlan FixFirstLegVolume(
    TargetVolumePlan plan,
    long totalVolume,
    long firstLegVolume,
    SymbolInfo symbol
  )
  {
    if (plan.Slices.Count < 2 || firstLegVolume <= 0 || firstLegVolume >= totalVolume)
    {
      return plan;
    }
    var remainder = totalVolume - firstLegVolume;
    var remainingWeights = Enumerable.Repeat(1, plan.Slices.Count - 1).ToArray();
    IReadOnlyList<long> remainingSlices;
    try
    {
      remainingSlices = SplitWeighted(remainder, symbol, remainingWeights);
    }
    catch (VolumePlanningException)
    {
      return plan;
    }
    return plan with { Slices = [firstLegVolume, .. remainingSlices] };
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

  public static string SizingDiagnostic(
    decimal balance,
    AutoTradeOptions options
  )
  {
    var tableLots = LotsForBalance(balance);
    var riskLots = balance * options.RiskPercent / 100m
      / (options.TrendStopMaxPips * options.PipValuePerLot);
    return $"sizing: mode={options.SizingMode} balance={balance:0.00} "
      + $"→ table {tableLots:0.00} lots · risk {riskLots:0.00} lots";
  }

  private static long MinimumStepsPerClose(SymbolInfo symbol) => Math.Max(
    1,
    (symbol.MinVolume + symbol.StepVolume - 1) / symbol.StepVolume
  );
}

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
    var tableLots = LotsForEquity(balance);
    if (tableLots <= 0)
    {
      throw new VolumePlanningException(
        $"balance {balance:N2} is below the $200 sizing floor"
      );
    }
    var rawLots = sizingMode switch
    {
      "table" or "equity_table" => tableLots,
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

  /// <summary>
  /// Owner equity → lot table (owner_equity_v1). Bands are equity dollars;
  /// result is rounded AwayFromZero to two decimal places (lot cents), not
  /// floored — e.g. equity 1300 → 0.12 (flat above-$1k band).
  /// Owner 2026-08-06: $600–$1000 inclusive always 0.10; above $1000 and
  /// below $2000 always 0.12 (no progressive ramp in those bands).
  /// </summary>
  public static decimal LotsForEquity(decimal equity)
  {
    if (equity < 200m)
    {
      return 0m;
    }
    // The upward discontinuities at band boundaries are intentional.
    var rawLots = equity switch
    {
      >= 5_000m => 0.30m,
      >= 3_000m => 0.25m + (equity - 3_000m) * 0.05m / 2_000m,
      >= 2_000m => 0.15m,
      > 1_000m => 0.12m,
      >= 600m => 0.10m,
      _ => 0.02m + (equity - 200m) * 0.04m / 700m,
    };
    return decimal.Round(rawLots, 2, MidpointRounding.AwayFromZero);
  }

  [Obsolete("Use LotsForEquity — sizing is equity-based, not balance-based.")]
  public static decimal LotsForBalance(decimal balance) => LotsForEquity(balance);

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
  /// Splits an entry volume across legs by fractional ratios, preferring the
  /// step-aligned allocation closest to the declared ratios. Unlike
  /// <see cref="SplitWeighted"/> (largest-remainder on integer weights), this
  /// rounds the first-leg ideal in lot space AwayFromZero to 2dp then aligns
  /// to the broker step — e.g. 0.11 lots at 70/30 → 0.08 + 0.03, not 0.07 + 0.04.
  /// </summary>
  public static IReadOnlyList<long> SplitEntryVolume(
    long totalVolume,
    SymbolInfo symbol,
    IReadOnlyList<decimal> ratios
  )
  {
    if (
      totalVolume <= 0
      || symbol.StepVolume <= 0
      || symbol.MinVolume <= 0
      || symbol.LotSize <= 0
      || totalVolume % symbol.StepVolume != 0
    )
    {
      throw new VolumePlanningException("Position volume is not broker-step aligned");
    }
    if (ratios.Count == 0 || ratios.Any(ratio => ratio <= 0))
    {
      throw new VolumePlanningException("Entry ratios must all be positive");
    }
    var ratioSum = ratios.Sum();
    if (Math.Abs(ratioSum - 1m) > 0.0001m)
    {
      throw new VolumePlanningException(
        $"Entry ratios must sum to 1.0, got {ratioSum}"
      );
    }
    if (ratios.Count == 1)
    {
      return new[] { totalVolume };
    }

    var minimumSteps = MinimumStepsPerClose(symbol);
    var totalSteps = totalVolume / symbol.StepVolume;
    var requiredSteps = checked(minimumSteps * ratios.Count);
    if (totalSteps < requiredSteps)
    {
      // Not enough volume for every leg at broker minimum — collapse to a
      // single entry so callers can still submit the sized total.
      return new[] { totalVolume };
    }

    var totalLots = totalVolume / (decimal)symbol.LotSize;
    var slices = new long[ratios.Count];
    long allocated = 0;
    for (var index = 0; index < ratios.Count - 1; index++)
    {
      var idealLots = decimal.Round(
        totalLots * ratios[index],
        2,
        MidpointRounding.AwayFromZero
      );
      var raw = decimal.ToInt64(
        decimal.Round(idealLots * symbol.LotSize, 0, MidpointRounding.AwayFromZero)
      );
      var stepped = raw / symbol.StepVolume * symbol.StepVolume;
      slices[index] = stepped;
      allocated += stepped;
    }
    slices[^1] = totalVolume - allocated;

    // Feasibility: every leg must meet MinVolume. Walk one step at a time
    // toward a feasible split while staying as close as possible to the
    // rounded ratio allocation.
    for (var pass = 0; pass < ratios.Count * 4; pass++)
    {
      var shortIndex = -1;
      for (var index = 0; index < slices.Length; index++)
      {
        if (slices[index] < symbol.MinVolume)
        {
          shortIndex = index;
          break;
        }
      }
      if (shortIndex < 0)
      {
        break;
      }
      var donor = -1;
      var bestExtra = long.MinValue;
      for (var index = 0; index < slices.Length; index++)
      {
        if (index == shortIndex)
        {
          continue;
        }
        var extra = slices[index] - symbol.MinVolume;
        if (extra >= symbol.StepVolume && extra > bestExtra)
        {
          bestExtra = extra;
          donor = index;
        }
      }
      if (donor < 0)
      {
        return new[] { totalVolume };
      }
      slices[donor] -= symbol.StepVolume;
      slices[shortIndex] += symbol.StepVolume;
    }

    if (slices.Any(slice => slice < symbol.MinVolume) || slices.Sum() != totalVolume)
    {
      throw new VolumePlanningException(
        "Unable to split entry volume into broker-valid legs near the declared ratios"
      );
    }
    return slices;
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
    var tableLots = LotsForEquity(balance);
    var riskLots = balance * options.RiskPercent / 100m
      / (options.TrendStopMaxPips * options.PipValuePerLot);
    return $"sizing: mode={options.SizingMode} balance={balance:0.00} "
      + $"→ table {tableLots:0.00} lots · risk {riskLots:0.00} lots";
  }

  /// <summary>
  /// Choose a broker-valid close volume for a partial TP against the
  /// currently remaining filled volume (after cancelled unfilled legs are
  /// removed). Desired size is snapped down to StepVolume. When a valid
  /// partial would leave unsellable dust, the entire remaining is closed.
  /// Returns 0 when no broker-valid partial exists and this is not the
  /// final target — callers should skip ahead to the last target.
  /// </summary>
  public static long PlanPartialCloseVolume(
    long remainingVolume,
    long desiredClose,
    SymbolInfo symbol,
    bool isFinalTarget
  )
  {
    if (remainingVolume <= 0)
    {
      return 0;
    }
    if (isFinalTarget || desiredClose >= remainingVolume)
    {
      return remainingVolume;
    }
    if (desiredClose <= 0)
    {
      return 0;
    }
    if (symbol.StepVolume <= 0 || symbol.MinVolume <= 0)
    {
      return Math.Min(desiredClose, remainingVolume);
    }

    var close = Math.Min(desiredClose, remainingVolume);
    close = close / symbol.StepVolume * symbol.StepVolume;
    if (close < symbol.MinVolume)
    {
      // Not enough for a broker-valid partial. Only a single-step position
      // can exit here (full close); otherwise leave for a later full TP.
      return remainingVolume <= symbol.MinVolume ? remainingVolume : 0;
    }

    var leftover = remainingVolume - close;
    if (leftover == 0)
    {
      return close;
    }
    if (leftover < symbol.MinVolume || leftover % symbol.StepVolume != 0)
    {
      // Dust / misaligned remainder — book the whole remaining now.
      return remainingVolume;
    }
    return close;
  }

  /// <summary>
  /// Pro-rata allocate a step-aligned close volume across open legs. Each
  /// slice and the leftover redistribution stay on StepVolume boundaries.
  /// </summary>
  public static long[] AllocateProRataStepped(
    long[] remaining,
    long closeVolume,
    SymbolInfo symbol
  )
  {
    var total = remaining.Sum();
    if (total <= 0 || closeVolume <= 0)
    {
      return new long[remaining.Length];
    }
    closeVolume = Math.Min(closeVolume, total);
    var step = Math.Max(1L, symbol.StepVolume);
    if (closeVolume % step != 0)
    {
      closeVolume = closeVolume / step * step;
    }
    if (closeVolume <= 0)
    {
      return new long[remaining.Length];
    }

    var raw = remaining
      .Select(volume =>
      {
        var ideal = (long)Math.Floor(closeVolume * (decimal)volume / total);
        return ideal / step * step;
      })
      .ToArray();
    var allocated = raw.Sum();
    var leftover = closeVolume - allocated;
    for (var i = 0; leftover >= step && i < raw.Length; i++)
    {
      var room = remaining[i] - raw[i];
      var add = Math.Min(room / step * step, leftover / step * step);
      if (add <= 0)
      {
        continue;
      }
      raw[i] += add;
      leftover -= add;
    }
    // Any leftover that still cannot be placed step-wise on a single leg is
    // dropped — better to under-close than send TRADING_BAD_VOLUME.
    return raw;
  }

  private static long MinimumStepsPerClose(SymbolInfo symbol) => Math.Max(
    1,
    (symbol.MinVolume + symbol.StepVolume - 1) / symbol.StepVolume
  );
}

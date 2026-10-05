namespace ApexVoid.CTraderFeed;

public sealed class VolumePlanningException(string message)
  : InvalidOperationException(message);

public static class VolumePlanner
{

  /// <summary>
  /// Owner equity → lot table (owner_equity_v1). Bands are equity dollars;
  /// result is rounded AwayFromZero to two decimal places (lot cents), not
  /// floored — e.g. equity 1300 → 0.12 (flat above-$1k band).
  /// Owner 2026-08-06: $600–$1000 inclusive always 0.10; above $1000 and
  /// below $2000 always 0.12 (no progressive ramp in those bands).
  /// </summary>
  public static decimal LotsForEquity(
    decimal equity,
    bool useFxEquitySizing = false
  )
  {
    if (equity < 200m)
    {
      return 0m;
    }
    if (useFxEquitySizing && equity >= 2_000m)
    {
      // FX uses the same 1.5x fixed-RR pack multiplier as manual /algo.
      // The owner table's 0.15 -> 0.25 jump at $3k turned into 0.23 ->
      // 0.38 actual FX lots. Interpolate the FX base from 0.15 at $2k to
      // 0.20 at $3k, then 0.30 at $5k. It removes the boundary jump while
      // retaining the existing $5k-and-up base ceiling.
      var fxLots = equity switch
      {
        >= 5_000m => 0.30m,
        >= 3_000m => 0.20m + (equity - 3_000m) * 0.10m / 2_000m,
        _ => 0.15m + (equity - 2_000m) * 0.05m / 1_000m,
      };
      return decimal.Round(fxLots, 2, MidpointRounding.AwayFromZero);
    }
    // Non-FX bands retain their established intentional discontinuities.
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

  public static bool IsFxInstrument(SymbolInfo symbol)
  {
    var name = symbol.RedisSymbol.Trim().ToUpperInvariant();
    return name is "EURUSD" or "GBPUSD" or "GBPJPY" or "USDJPY";
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
    return $"sizing: mode={options.SizingMode} balance={balance:0.00} "
      + $"→ table {tableLots:0.00} lots";
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
  /// Sequentially allocate a step-aligned close volume, draining the
  /// shallowest leg (index 0) completely before any volume comes off a
  /// deeper sibling. Owner 2026-09-08: pro-rata closing (see
  /// AllocateProRataStepped) kept every leg's remaining volume in its
  /// original ratio after each TP, so the group's weighted fill price
  /// stayed skewed toward the shallow (larger, worse-price) leg even
  /// after several targets booked - mirrors manual algo's own ladder,
  /// where the shallow leg's TP1 fully consumes it and only the deeper,
  /// better-priced leg is left to compute BE/trail from. Callers must
  /// pass ``remaining`` in shallow-to-deep leg order (index 0 = the
  /// declared entry.legs[0]/L1 - the market/nearest-price leg by
  /// convention).
  /// </summary>
  public static long[] AllocateShallowFirstStepped(
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
    var allocations = new long[remaining.Length];
    var left = closeVolume;
    for (var i = 0; i < remaining.Length && left > 0; i++)
    {
      var take = Math.Min(remaining[i], left);
      take = take / step * step;
      allocations[i] = take;
      left -= take;
    }
    return allocations;
  }

  private static long MinimumStepsPerClose(SymbolInfo symbol) => Math.Max(
    1,
    (symbol.MinVolume + symbol.StepVolume - 1) / symbol.StepVolume
  );
}

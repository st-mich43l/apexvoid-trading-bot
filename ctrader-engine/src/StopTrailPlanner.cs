namespace ApexVoid.CTraderFeed;

public sealed record StopTrailMove(
  decimal StopLoss,
  string Label,
  decimal? BufferPrice = null
);

public static class StopTrailPlanner
{
  /// <summary>
  /// Plans one shared absolute stop for the remaining clips of a manual
  /// entry ladder after TP1. Realized TP pip-volume funds room below/above
  /// the remaining-entry VWAP, while the whole original group still locks
  /// the configured small positive buffer if every runner stops there.
  ///
  /// This deliberately differs from moving every clip to its own BE: the
  /// latter bunches stops around the individual entries and is vulnerable
  /// to an ordinary M1 retest even though TP1 already paid for wider group
  /// protection. Existing/current stops are a hard never-worsen boundary.
  /// </summary>
  public static StopTrailMove? PlanGroupEconomicBreakeven(
    IReadOnlyList<AutoTradePositionState> remainingStates,
    long groupInitialVolume,
    decimal bookedPipVolume,
    SymbolInfo symbol,
    decimal pipSize,
    int protectedBufferTicks
  )
  {
    if (remainingStates.Count == 0 || groupInitialVolume <= 0)
    {
      return null;
    }
    if (pipSize <= 0m)
    {
      throw new ArgumentOutOfRangeException(nameof(pipSize));
    }
    if (protectedBufferTicks < 0)
    {
      throw new ArgumentOutOfRangeException(nameof(protectedBufferTicks));
    }
    var direction = remainingStates[0].Direction;
    if (remainingStates.Any(state => state.Direction != direction))
    {
      throw new InvalidOperationException(
        "group economic breakeven requires one trade direction"
      );
    }
    var remainingVolume = remainingStates.Sum(state => state.RemainingVolume);
    if (remainingVolume <= 0)
    {
      return null;
    }
    var weightedEntry = remainingStates.Sum(
      state => state.EntryPrice * state.RemainingVolume
    ) / remainingVolume;
    var tickSize = RequireTickSize(symbol);
    var protectedBufferPrice = protectedBufferTicks * tickSize;
    var protectedBufferPips = protectedBufferPrice / pipSize;
    var targetPipVolume = protectedBufferPips * groupInitialVolume;
    var unfundedPipVolume = targetPipVolume - bookedPipVolume;
    var desired = direction == TradeDirection.Buy
      ? weightedEntry + unfundedPipVolume * pipSize / remainingVolume
      : weightedEntry - unfundedPipVolume * pipSize / remainingVolume;

    // Never loosen any live or original owner stop. All remaining ladder
    // clips receive one absolute price, so use the most protective boundary
    // already held by any sibling.
    var boundaries = remainingStates
      .Select(state => state.CurrentStopLoss ?? state.InitialStopLoss)
      .Where(stop => stop is not null)
      .Select(stop => stop!.Value)
      .ToArray();
    if (boundaries.Length > 0)
    {
      desired = direction == TradeDirection.Buy
        ? Math.Max(desired, boundaries.Max())
        : Math.Min(desired, boundaries.Min());
    }

    // Round toward greater protection so tick rounding cannot turn the
    // promised small group profit into a fractional loss.
    desired = direction == TradeDirection.Buy
      ? decimal.Ceiling(desired / tickSize) * tickSize
      : decimal.Floor(desired / tickSize) * tickSize;
    desired = decimal.Round(
      desired,
      symbol.Digits,
      MidpointRounding.AwayFromZero
    );
    if (remainingStates.All(state =>
      state.CurrentStopLoss is decimal current
      && !MovesTowardProfit(direction, current, desired)
    ))
    {
      return null;
    }
    return new StopTrailMove(
      desired,
      $"group BE+{protectedBufferTicks} ticks",
      protectedBufferPrice
    );
  }

  public static StopTrailMove? Plan(
    AutoTradePositionState state,
    int completedTargetIndex,
    SymbolInfo symbol,
    decimal pipSize,
    int breakEvenBufferTicks
  )
  {
    if (
      completedTargetIndex < 0
      || completedTargetIndex >= state.TargetsPips.Count - 1
    )
    {
      return null;
    }
    return PlanForTargetOrdinal(
      state,
      TargetOrdinal(state, completedTargetIndex),
      symbol,
      pipSize,
      breakEvenBufferTicks
    );
  }

  public static StopTrailMove? PlanForTargetOrdinal(
    AutoTradePositionState state,
    int completedTargetOrdinal,
    SymbolInfo symbol,
    decimal pipSize,
    int breakEvenBufferTicks
  )
  {
    if (completedTargetOrdinal <= 0)
    {
      return null;
    }
    decimal desired;
    string label;
    decimal? bufferPrice = null;
    if (completedTargetOrdinal == 1)
    {
      var tickSize = RequireTickSize(symbol);
      bufferPrice = breakEvenBufferTicks * tickSize;
      desired = ProtectedBreakevenStop(
        state.Direction,
        state.EntryPrice,
        symbol,
        breakEvenBufferTicks
      );
      label = $"BE+{breakEvenBufferTicks} ticks";
    }
    else
    {
      // TP2 has no target two behind it yet - trail to TP1 instead of the
      // usual "two behind" step (2026-08 R:R dig: TP2 previously moved the
      // stop nowhere at all, leaving the remaining position flat at
      // breakeven all the way through to TP3 - the single biggest driver of
      // wins scratching near zero instead of banking real progress).
      var trailTargetOrdinal = completedTargetOrdinal == 2
        ? 1
        : completedTargetOrdinal - 2;
      // Prefer absolute TargetPrices (manual / owner ladders) so trail
      // matches the booked TP levels rather than fill±pips from TargetsPips
      // (slippage made trail ≠ owner TP after TP4 on manual #8 2026-08-11).
      var absolute = AbsoluteTargetPrice(state, trailTargetOrdinal);
      if (absolute is decimal absolutePrice)
      {
        desired = absolutePrice;
        label = $"TP{trailTargetOrdinal}";
      }
      else
      {
        var offsetPips = TargetPips(state, trailTargetOrdinal);
        if (offsetPips is null)
        {
          return null;
        }
        desired = state.Direction == TradeDirection.Buy
          ? state.EntryPrice + offsetPips.Value * pipSize
          : state.EntryPrice - offsetPips.Value * pipSize;
        label = $"TP{trailTargetOrdinal}";
      }
    }
    desired = decimal.Round(desired, symbol.Digits, MidpointRounding.AwayFromZero);
    if (
      state.CurrentStopLoss is decimal current
      && !MovesTowardProfit(state.Direction, current, desired)
    )
    {
      return null;
    }
    return new StopTrailMove(desired, label, bufferPrice);
  }

  public static decimal ProtectedBreakevenStop(
    TradeDirection direction,
    decimal entry,
    SymbolInfo symbol,
    int breakEvenBufferTicks
  )
  {
    if (breakEvenBufferTicks < 0)
    {
      throw new ArgumentOutOfRangeException(nameof(breakEvenBufferTicks));
    }
    var buffer = breakEvenBufferTicks * RequireTickSize(symbol);
    // Profit-side protection: the stop moves past entry by the buffer, in
    // the direction that locks in a small amount of profit rather than
    // merely covering the spread. BUY moves the stop above entry, SELL
    // moves it below entry.
    var stop = direction == TradeDirection.Buy
      ? entry + buffer
      : entry - buffer;
    return decimal.Round(stop, symbol.Digits, MidpointRounding.AwayFromZero);
  }

  public static bool IsAtLeastProtectedBreakeven(
    TradeDirection direction,
    decimal entry,
    decimal stop,
    SymbolInfo symbol,
    int breakEvenBufferTicks
  )
  {
    var threshold = ProtectedBreakevenStop(
      direction,
      entry,
      symbol,
      breakEvenBufferTicks
    );
    return direction == TradeDirection.Buy
      ? stop >= threshold
      : stop <= threshold;
  }

  public static decimal RequireTickSize(SymbolInfo symbol)
  {
    if (symbol.Digits < 0)
    {
      throw new InvalidOperationException(
        $"Symbol {symbol.CTraderSymbol} has invalid digits {symbol.Digits}"
      );
    }
    var tick = 1m;
    for (var index = 0; index < symbol.Digits; index++)
    {
      tick /= 10m;
    }
    if (tick <= 0)
    {
      throw new InvalidOperationException(
        $"Symbol {symbol.CTraderSymbol} tick size {tick} is not positive"
      );
    }
    return tick;
  }

  private static int TargetOrdinal(AutoTradePositionState state, int index) =>
    state.TargetOrdinals is { } ordinals && index < ordinals.Count
      ? ordinals[index]
      : index + 1;

  private static int? TargetPips(
    AutoTradePositionState state,
    int targetOrdinal
  )
  {
    if (targetOrdinal < 1)
    {
      return null;
    }
    for (var index = 0; index < state.TargetsPips.Count; index++)
    {
      if (TargetOrdinal(state, index) == targetOrdinal)
      {
        return state.TargetsPips[index];
      }
    }
    return null;
  }

  private static decimal? AbsoluteTargetPrice(
    AutoTradePositionState state,
    int targetOrdinal
  )
  {
    if (targetOrdinal < 1 || state.TargetPrices is not { } prices)
    {
      return null;
    }
    // Compressed per-leg / adaptive ladders keep TargetPrices aligned 1:1
    // with TargetsPips rows. Full group ladders (manual Mid/Deep) keep the
    // owner TP list and must resolve by ordinal slot, not the local index
    // of that ordinal inside the leg's subset.
    if (prices.Count == state.TargetsPips.Count)
    {
      for (var index = 0; index < state.TargetsPips.Count; index++)
      {
        if (
          TargetOrdinal(state, index) == targetOrdinal
          && index < prices.Count
        )
        {
          return prices[index];
        }
      }
      return null;
    }
    return targetOrdinal <= prices.Count
      ? prices[targetOrdinal - 1]
      : null;
  }

  private static bool MovesTowardProfit(
    TradeDirection direction,
    decimal current,
    decimal desired
  ) => direction == TradeDirection.Buy
    ? desired > current
    : desired < current;
}

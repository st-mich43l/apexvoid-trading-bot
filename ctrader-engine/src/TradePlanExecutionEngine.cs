namespace ApexVoid.CTraderFeed;

public enum TradePlanEntryAction
{
  Wait,
  SubmitMarket,
  SubmitLimit,
  SubmitLadder,
}

public sealed record TradePlanEntryDecision(
  TradePlanEntryAction Action,
  string? RejectReason = null
)
{
  public bool ShouldSubmit =>
    RejectReason is null && Action != TradePlanEntryAction.Wait;
}

// TargetId holds a TP target_id for... actually never a TP target_id in
// practice (see CalculateVolume) - it holds an entry LegId for
// limit_ladder entries, the only case Slices is ever populated/consumed
// (TradePlanRuntime.SubmitEntryAsync zips it against plan.Entry.Legs).
// TP-target close volume is computed live from RemainingVolume at each
// target hit (see TradePlanRuntime.ManageOpenPositionsAsync) and never
// reads Slices - so Slices must never be sized off plan.Targets.
public sealed record TradePlanVolumeSlice(string TargetId, long Volume);

public sealed record TradePlanVolumePlan(
  long TotalVolume,
  IReadOnlyList<TradePlanVolumeSlice> Slices
);

public sealed record TradePlanBreakEvenResult(
  decimal DesiredStop,
  decimal NewStop,
  bool Improved
);

/// <summary>
/// Pure decision logic for the V7 execution path. Every method here is a
/// mechanical function of a TradePlan's own already-declared values plus
/// live broker-observed inputs (quote, spread, fill price, account
/// balance, tick size) - none of them classify regime, select a strategy,
/// resolve an execution route, or compute a structural stop. See
/// docs/adr-trade-plan-v7-boundary.md. The dependency boundary (this file
/// never calls StructureStopPlanner, ResolveExecutionRoute,
/// BuildOpposingZoneContext, StructuralStopIdentityMatches, or
/// PlansMatchWithinTolerance) is enforced by
/// TradePlanExecutionEngineDependencyTests.cs.
///
/// This is not yet wired into AutoTradeEngine.RunSessionAsync - broker
/// order submission, fill reconciliation, and restart recovery for V7
/// plans are a later phase. This class only decides *what* to do; a caller
/// still has to actually call ICTraderTradeClient.
/// </summary>
public static class TradePlanExecutionEngine
{
  public static TradePlanEntryDecision EvaluateEntry(
    TradePlan plan,
    decimal bid,
    decimal ask,
    decimal spreadTicks,
    long nowUnixSeconds
  )
  {
    if (nowUnixSeconds >= plan.Entry.ExpiresAt)
    {
      return new TradePlanEntryDecision(TradePlanEntryAction.Wait, "plan_expired");
    }
    return plan.Entry.Type switch
    {
      TradePlanContract.EntryTypeMarketWatch =>
        EvaluateMarketWatch(plan, bid, ask, spreadTicks),
      TradePlanContract.EntryTypeSingleLimit =>
        new TradePlanEntryDecision(TradePlanEntryAction.SubmitLimit),
      TradePlanContract.EntryTypeLimitLadder =>
        new TradePlanEntryDecision(TradePlanEntryAction.SubmitLadder),
      _ => new TradePlanEntryDecision(TradePlanEntryAction.Wait, "unknown_entry_type"),
    };
  }

  private static TradePlanEntryDecision EvaluateMarketWatch(
    TradePlan plan,
    decimal bid,
    decimal ask,
    decimal spreadTicks
  )
  {
    if (plan.Entry.ZoneLow is null || plan.Entry.ZoneHigh is null)
    {
      return new TradePlanEntryDecision(
        TradePlanEntryAction.Wait,
        "market_watch_missing_zone"
      );
    }
    var quote = plan.Entry.PriceSide == "ask" ? ask : bid;
    if (quote < plan.Entry.ZoneLow.Value || quote > plan.Entry.ZoneHigh.Value)
    {
      return new TradePlanEntryDecision(TradePlanEntryAction.Wait);
    }
    if (plan.Entry.MaxSpreadTicks is int maxSpread && spreadTicks > maxSpread)
    {
      return new TradePlanEntryDecision(
        TradePlanEntryAction.Wait,
        "spread_exceeds_declared_limit"
      );
    }
    return new TradePlanEntryDecision(TradePlanEntryAction.SubmitMarket);
  }

  /// <summary>
  /// Sizes volume purely from the plan's own risk contract and the live
  /// account balance - never from a structural stop distance C# derived
  /// itself, since the stop distance here is |entry - plan.Stop.Price| and
  /// plan.Stop.Price is Python's exact declared value. For a limit_ladder
  /// entry, Slices are proportional to each entry LEG's own declared
  /// VolumeRatio (e.g. the 70/30 DCA split from the zone-scale ladder) -
  /// never to plan.Targets.CloseRatio, which is a wholly separate TP-exit
  /// split that TradePlanRuntime.ManageOpenPositionsAsync already computes
  /// live from RemainingVolume at each target hit. Sizing entry legs off
  /// the TP count/weights instead used to both misapply the entry split
  /// (silently, whenever it happened not to throw) and, whenever a plan
  /// declared more TP targets than the sized volume had broker-steps for,
  /// throw unconditionally - even for a plain market_watch entry that never
  /// reads Slices at all - crashing the whole poll loop on a plan that
  /// could otherwise execute fine. Uses the existing broker-step-aware
  /// VolumePlanner.SplitWeighted (purely mechanical broker-unit math,
  /// shared with the V6 path - not analysis).
  /// </summary>
  public static TradePlanVolumePlan CalculateVolume(
    TradePlan plan,
    decimal accountBalance,
    decimal pipSize,
    decimal pipValuePerLot,
    SymbolInfo symbol
  )
  {
    if (accountBalance <= 0 || pipSize <= 0 || pipValuePerLot <= 0)
    {
      throw new TradePlanContractException(
        "account balance, pip size, and pip value must be positive"
      );
    }
    var entryPrices = plan.Entry.EntryPrices();
    var entryReference = plan.Analysis.Direction == "BUY"
      ? entryPrices.Min()
      : entryPrices.Max();
    var stopDistance = Math.Abs(entryReference - plan.Stop.Price);
    if (stopDistance <= 0)
    {
      throw new TradePlanContractException("stop distance must be positive");
    }
    var stopPips = stopDistance / pipSize;
    var riskAmount =
      accountBalance * plan.Risk.RiskPercent / 100m * plan.Risk.RiskMultiplier;
    var riskLots = riskAmount / (stopPips * pipValuePerLot);
    var maxVolumeLots = symbol.LotSize > 0 ? (decimal)plan.Risk.MaxVolume / symbol.LotSize : 0m;
    var cappedLots = Math.Min(riskLots, maxVolumeLots);
    var volume = VolumePlanner.VolumeForLots(cappedLots, symbol);
    if (volume <= 0)
    {
      throw new TradePlanContractException(
        $"risk-based sizing produced a non-tradeable volume "
        + $"(risk lots={riskLots:0.####}, plan max_volume lots={maxVolumeLots:0.####})"
      );
    }

    if (plan.Entry.Type != TradePlanContract.EntryTypeLimitLadder)
    {
      // market_watch and single_limit submit the full volume as one order
      // (TotalVolume) - no per-slice split exists to compute.
      return new TradePlanVolumePlan(volume, Array.Empty<TradePlanVolumeSlice>());
    }
    var legs = plan.Entry.Legs ?? Array.Empty<TradePlanEntryLeg>();
    if (legs.Count == 0)
    {
      throw new TradePlanContractException(
        "limit_ladder entry requires at least one leg"
      );
    }
    // SplitWeighted takes positive integer weights, not fractional ratios -
    // scale volume_ratio (e.g. 0.70) up by a fixed factor to preserve
    // relative proportions without floating-point drift.
    var weights = legs
      .Select(leg => Math.Max(1, decimal.ToInt32(leg.VolumeRatio * 10_000m)))
      .ToArray();
    var slices = VolumePlanner.SplitWeighted(volume, symbol, weights);
    return new TradePlanVolumePlan(
      volume,
      legs
        .Zip(slices, (leg, sliceVolume) => new TradePlanVolumeSlice(leg.LegId, sliceVolume))
        .ToArray()
    );
  }

  public static bool HasReachedTarget(
    TradePlan plan,
    TradePlanTarget target,
    decimal currentPrice
  ) =>
    plan.Analysis.Direction == "BUY"
      ? currentPrice >= target.Price
      : currentPrice <= target.Price;

  /// <summary>
  /// buffer_price = be_buffer_ticks * tick_size; BUY desired = fill +
  /// buffer, SELL desired = fill - buffer; never worsens an existing stop
  /// (BUY: max(current, desired), SELL: min(current, desired)). Uses only
  /// the broker-confirmed fill price - never the declared entry zone/order
  /// price - per docs/adr-trade-plan-v7-boundary.md.
  /// </summary>
  public static TradePlanBreakEvenResult CalculateBreakEven(
    TradePlan plan,
    decimal brokerConfirmedFillPrice,
    decimal currentStop,
    SymbolInfo symbol
  )
  {
    // Not StopTrailPlanner.ProtectedBreakevenStop - that method computes a
    // conservative *floor* used to verify an existing stop already counts
    // as protected (entry - buffer for BUY), the opposite sign convention
    // from "move the stop past the fill by a buffer" used here. Only
    // RequireTickSize (pure symbol.Digits math) is shared.
    var bufferPrice = plan.Management.BeBufferTicks * StopTrailPlanner.RequireTickSize(symbol);
    var desired = plan.Analysis.Direction == "BUY"
      ? brokerConfirmedFillPrice + bufferPrice
      : brokerConfirmedFillPrice - bufferPrice;
    desired = decimal.Round(desired, symbol.Digits, MidpointRounding.AwayFromZero);
    var newStop = plan.Analysis.Direction == "BUY"
      ? Math.Max(currentStop, desired)
      : Math.Min(currentStop, desired);
    return new TradePlanBreakEvenResult(desired, newStop, newStop != currentStop);
  }
}

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
      TradePlanContract.EntryTypeMarketWithLimitScale =>
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
    // A zone can be narrower than the live spread (23:42 incident: a
    // 0.40-wide BUY zone, price genuinely traded inside it on the bid side
    // for a full minute, but the ask-only check below never saw it -
    // "outside_zone" for the whole 7-minute window despite a real touch).
    // The zone describes where price reacted, not a single execution side;
    // fire whenever the current tradable range [bid, ask] overlaps the
    // zone at all, not only when the single trade-side quote is strictly
    // contained. A spread wide enough to matter is still caught below by
    // MaxSpreadTicks - this only stops a normal, tight spread from making
    // a real zone touch invisible to the ask/bid-only check.
    if (ask < plan.Entry.ZoneLow.Value || bid > plan.Entry.ZoneHigh.Value)
    {
      return new TradePlanEntryDecision(TradePlanEntryAction.Wait, "outside_zone");
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
  /// Sizes volume from the plan's sizing contract and the live account
  /// snapshot. When sizing.mode=equity_table, volume comes from
  /// VolumePlanner.LotsForEquity(resolvedEquity) and RiskPercent is ignored.
  /// Plans without a sizing contract are rejected
  /// (legacy_v7_sizing_contract_missing). For a limit_ladder entry, Slices
  /// are proportional to each entry LEG's own declared VolumeRatio (or
  /// sizing.leg_ratios when present) via SplitEntryVolume — never to
  /// plan.Targets.CloseRatio.
  /// </summary>
  public static TradePlanVolumePlan CalculateVolume(
    TradePlan plan,
    TradingAccountSnapshot account,
    decimal pipSize,
    decimal pipValuePerLot,
    SymbolInfo symbol
  ) => CalculateVolume(
    plan,
    EquityResolver.Resolve(account, openPositionCount: 0, pendingOrderCount: 0),
    pipSize,
    pipValuePerLot,
    symbol
  );

  /// <summary>
  /// Sizes from an already-resolved equity figure (arm/submit paths that
  /// know live open/pending exposure must Resolve first).
  /// </summary>
  public static TradePlanVolumePlan CalculateVolume(
    TradePlan plan,
    EquityResolution equity,
    decimal pipSize,
    decimal pipValuePerLot,
    SymbolInfo symbol
  )
  {
    if (equity.Equity <= 0)
    {
      throw new TradePlanContractException(
        "account equity/balance must be positive"
      );
    }
    if (pipSize <= 0 || pipValuePerLot <= 0)
    {
      throw new TradePlanContractException(
        "pip size and pip value must be positive"
      );
    }
    if (plan.Sizing is null)
    {
      throw new TradePlanContractException("legacy_v7_sizing_contract_missing");
    }
    if (plan.Sizing.Mode != "equity_table")
    {
      throw new TradePlanContractException(
        $"unsupported sizing mode '{plan.Sizing.Mode}'"
      );
    }

    var tableLots = VolumePlanner.LotsForEquity(equity.Equity);
    if (tableLots <= 0)
    {
      throw new TradePlanContractException(
        $"equity {equity.Equity:N2} is below the $200 equity sizing floor"
      );
    }
    // Python stamps RiskMultiplier (scalp = 2.0 for all quality tiers).
    // Stop geometry stays unchanged — this scales volume only.
    var riskMultiplier = plan.Risk.RiskMultiplier;
    if (riskMultiplier <= 0m)
    {
      riskMultiplier = 1m;
    }
    var sizedLots = decimal.Round(
      tableLots * riskMultiplier,
      2,
      MidpointRounding.AwayFromZero
    );
    var maxVolumeLots = symbol.LotSize > 0
      ? (decimal)plan.Risk.MaxVolume / symbol.LotSize
      : 0m;
    // MaxVolume is a hard ceiling: never silently Min() the owner table lots
    // down (e.g. 0.11 → smaller). Python publishes a large broker-style
    // max_volume that the table already fits; a tighter ceiling rejects.
    if (maxVolumeLots > 0m && sizedLots > maxVolumeLots)
    {
      throw new TradePlanContractException("equity_table_above_broker_maximum");
    }
    if (
      symbol.MaxVolume > 0
      && symbol.LotSize > 0
      && sizedLots * symbol.LotSize > symbol.MaxVolume
    )
    {
      throw new TradePlanContractException("equity_table_above_broker_maximum");
    }
    var volume = VolumePlanner.VolumeForLots(sizedLots, symbol);
    if (volume <= 0)
    {
      throw new TradePlanContractException(
        $"equity-table sizing produced a non-tradeable volume "
        + $"(table lots={tableLots:0.####}, risk_multiplier={riskMultiplier:0.####}, "
        + $"sized lots={sizedLots:0.####}, plan max_volume lots={maxVolumeLots:0.####})"
      );
    }

    if (
      plan.Entry.Type is not TradePlanContract.EntryTypeLimitLadder
        and not TradePlanContract.EntryTypeMarketWithLimitScale
    )
    {
      // market_watch and single_limit submit the full volume as one order
      // (TotalVolume) - no per-slice split exists to compute.
      return new TradePlanVolumePlan(volume, Array.Empty<TradePlanVolumeSlice>());
    }
    var legs = plan.Entry.Legs ?? Array.Empty<TradePlanEntryLeg>();
    if (legs.Count == 0)
    {
      throw new TradePlanContractException(
        $"{plan.Entry.Type} entry requires at least one leg"
      );
    }
    IReadOnlyList<decimal> ratios =
      plan.Sizing.LegRatios is { Count: > 0 } sizingRatios
        ? sizingRatios
        : legs.Select(leg => leg.VolumeRatio).ToArray();
    if (ratios.Count != legs.Count)
    {
      throw new TradePlanContractException(
        $"sizing.leg_ratios count {ratios.Count} does not match entry legs {legs.Count}"
      );
    }
    var slices = VolumePlanner.SplitEntryVolume(volume, symbol, ratios);
    if (slices.Count == 1 && legs.Count > 1)
    {
      // Split collapsed to a single entry — attach the full volume to the
      // first leg so SubmitEntryAsync still has one slice per submitted order
      // path decision; callers treating Count==1 as single_entry can detect it.
      return new TradePlanVolumePlan(
        volume,
        new[] { new TradePlanVolumeSlice(legs[0].LegId, volume) }
      );
    }
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

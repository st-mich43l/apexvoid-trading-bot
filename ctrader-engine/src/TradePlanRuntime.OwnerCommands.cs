using System.Globalization;

namespace ApexVoid.CTraderFeed;

/// <summary>
/// Owner broker controls (/trade_close, /trade_close_auto, /auto_close_all,
/// /trade_sl) expressed against TradePlan V8 state. Closes go straight to the
/// broker; the runtime's normal reconcile then books the group exactly as it
/// does for any manual/external close, so there is one accounting path.
/// </summary>
public sealed partial class TradePlanRuntime
{
  public const string ManualFamily = "manual";

  public static bool IsManualPlan(TradePlan plan) =>
    string.Equals(
      plan.Analysis.StrategyFamily, ManualFamily, StringComparison.OrdinalIgnoreCase
    );

  public sealed record OwnerCommandResult(int Affected, string? Error = null)
  {
    public static OwnerCommandResult Failed(string error) => new(0, error);
  }

  private async Task EnsureRestoredAsync(CancellationToken cancellationToken)
  {
    if (!_restored)
    {
      await RestoreAsync(cancellationToken);
      _restored = true;
    }
  }

  private IEnumerable<(TradePlan Plan, TradePlanRuntimeState State)> LivePlans()
  {
    foreach (var state in _statesById.Values.ToArray())
    {
      if (
        state.Stage == TradePlanRuntimeStage.Closed
        || !_plansById.TryGetValue(state.PlanId, out var plan)
      )
      {
        continue;
      }
      yield return (plan, state);
    }
  }

  private static IEnumerable<TradePlanLegRuntimeState> OpenLegs(
    TradePlanRuntimeState state
  ) => (state.Legs ?? []).Where(leg =>
    leg.BrokerPositionId is not null && leg.RemainingVolume > 0
  );

  // Align a requested close volume to the broker step; never below the
  // minimum, never above what is open.
  private static long CloseVolume(
    TradePlanLegRuntimeState leg, decimal? fraction, SymbolInfo? symbol
  )
  {
    var remaining = leg.RemainingVolume;
    if (fraction is not decimal frac || frac <= 0m || frac >= 1m)
    {
      return remaining;
    }
    var volume = decimal.ToInt64(decimal.Floor(remaining * frac));
    var step = symbol is { StepVolume: > 0 } ? symbol.StepVolume : 1;
    volume = volume / step * step;
    var minimum = symbol is { MinVolume: > 0 } ? symbol.MinVolume : 1;
    return Math.Clamp(Math.Max(volume, minimum), 1, remaining);
  }

  /// <summary>
  /// /trade_close: close every open leg of one plan (full, or the same
  /// fraction of each leg's own remaining volume).
  /// </summary>
  public async Task<OwnerCommandResult> OwnerClosePlanAsync(
    ICTraderTradeClient client,
    SymbolInfo? sessionSymbol,
    string planId,
    decimal? fraction,
    CancellationToken cancellationToken
  )
  {
    await EnsureRestoredAsync(cancellationToken);
    if (
      !_statesById.TryGetValue(planId, out var state)
      || !_plansById.TryGetValue(planId, out var plan)
    )
    {
      return OwnerCommandResult.Failed($"no open positions found for plan {planId}");
    }
    var symbol = sessionSymbol is null ? null : BoundSymbol(plan.Symbol, sessionSymbol);
    var closed = 0;
    foreach (var leg in OpenLegs(state))
    {
      await client.ClosePositionAsync(
        leg.BrokerPositionId!.Value, CloseVolume(leg, fraction, symbol), cancellationToken
      );
      closed++;
    }
    if (closed == 0)
    {
      return OwnerCommandResult.Failed($"no open positions found for plan {planId}");
    }
    log($"v8 owner close plan={planId} legs={closed}");
    return new OwnerCommandResult(closed);
  }

  /// <summary>
  /// /trade_close_auto (autonomous legs only) or a bare position id: close
  /// one broker position that a tracked plan owns.
  /// </summary>
  public async Task<OwnerCommandResult> OwnerClosePositionAsync(
    ICTraderTradeClient client,
    SymbolInfo? sessionSymbol,
    long positionId,
    decimal? fraction,
    bool autonomousOnly,
    CancellationToken cancellationToken
  )
  {
    await EnsureRestoredAsync(cancellationToken);
    foreach (var (plan, state) in LivePlans())
    {
      var leg = OpenLegs(state).FirstOrDefault(item => item.BrokerPositionId == positionId);
      if (leg is null)
      {
        continue;
      }
      if (autonomousOnly && IsManualPlan(plan))
      {
        return OwnerCommandResult.Failed(
          $"position {positionId} belongs to a manual /algo signal; use /trade_close"
        );
      }
      var symbol = sessionSymbol is null ? null : BoundSymbol(plan.Symbol, sessionSymbol);
      await client.ClosePositionAsync(
        positionId, CloseVolume(leg, fraction, symbol), cancellationToken
      );
      log($"v8 owner close position={positionId} plan={plan.PlanId}");
      return new OwnerCommandResult(1);
    }
    return OwnerCommandResult.Failed($"position {positionId} is not an open plan position");
  }

  /// <summary>
  /// /auto_close_all: market-close every open plan leg and withdraw every
  /// resting entry order the runtime owns.
  /// </summary>
  public async Task<(int Closed, int Cancelled)> OwnerFlattenAsync(
    ICTraderTradeClient client,
    SymbolInfo? sessionSymbol,
    CancellationToken cancellationToken
  )
  {
    await EnsureRestoredAsync(cancellationToken);
    var closed = 0;
    var cancelled = 0;
    foreach (var (plan, initial) in LivePlans())
    {
      var state = initial;
      var hadResting = (state.Legs ?? []).Any(leg =>
        leg.BrokerOrderId is not null && leg.BrokerPositionId is null
      );
      if (hadResting)
      {
        state = await CancelUnfilledEntryLegsAsync(
          client, plan, state, "owner_flatten", cancellationToken, force: true
        );
        cancelled += (initial.Legs ?? []).Count(leg =>
          leg.BrokerOrderId is not null && leg.BrokerPositionId is null
        );
      }
      foreach (var leg in OpenLegs(state))
      {
        await client.ClosePositionAsync(
          leg.BrokerPositionId!.Value, leg.RemainingVolume, cancellationToken
        );
        closed++;
      }
    }
    log($"v8 owner flatten closed={closed} cancelled={cancelled}");
    return (closed, cancelled);
  }

  /// <summary>
  /// /trade_sl: move the protective stop of the plan that owns this position
  /// (every open leg of that plan moves together, like the runtime's own
  /// break-even), through the same verified amend path.
  /// </summary>
  public async Task<OwnerCommandResult> OwnerMoveStopAsync(
    ICTraderTradeClient client,
    SymbolInfo? sessionSymbol,
    long positionId,
    decimal price,
    CancellationToken cancellationToken
  )
  {
    await EnsureRestoredAsync(cancellationToken);
    foreach (var (plan, initial) in LivePlans())
    {
      if (!OpenLegs(initial).Any(leg => leg.BrokerPositionId == positionId))
      {
        continue;
      }
      var symbol = sessionSymbol is null
        ? throw new InvalidOperationException("owner stop move needs a bound symbol")
        : BoundSymbol(plan.Symbol, sessionSymbol);
      var state = initial;
      foreach (var leg in OpenLegs(initial).ToArray())
      {
        state = await AmendAndVerifyLegStopAsync(
          client, symbol, state, leg.LegId, price, cancellationToken
        );
      }
      state = AggregateState(state with { CurrentStop = price, GroupAbsoluteStop = price });
      await PersistStateAsync(state, cancellationToken);
      await PublishEventAsync(
        "manual_sl_moved",
        $"STOP MOVED by owner to {price.ToString(CultureInfo.InvariantCulture)}",
        plan,
        cancellationToken,
        positionId: positionId,
        price: price,
        runtimeState: state
      );
      return new OwnerCommandResult(1);
    }
    return OwnerCommandResult.Failed($"position {positionId} is not an open plan position");
  }
}

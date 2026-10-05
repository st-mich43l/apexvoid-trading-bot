namespace ApexVoid.CTraderFeed;

/// <summary>
/// Instrument-owned autonomous opposite-direction exposure policy, read from
/// instruments.&lt;SYM&gt;.exposure.opposite_position. Allowed=false blocks any
/// opposite exposure at any distance; Allowed=true needs
/// <see cref="MinimumSeparationPips"/> (inclusive) from EVERY opposite group.
/// </summary>
public sealed record OppositePositionPolicy(
  bool Allowed,
  decimal? MinimumSeparationPips,
  decimal PipSize
)
{
  public static OppositePositionPolicy Parse(
    string symbol,
    IReadOnlyDictionary<string, object?> instrumentSection,
    decimal pipSize
  )
  {
    if (
      !instrumentSection.TryGetValue("exposure", out var exposure)
      || exposure is not IReadOnlyDictionary<string, object?> exposureMap
      || !exposureMap.TryGetValue("opposite_position", out var raw)
      || raw is not IReadOnlyDictionary<string, object?> policy
    )
    {
      throw new ConfigurationV3Error(
        $"instrument {symbol}: missing exposure.opposite_position policy"
      );
    }
    if (!policy.TryGetValue("allowed", out var allowedRaw) || allowedRaw is not bool allowed)
    {
      throw new ConfigurationV3Error(
        $"instrument {symbol}: exposure.opposite_position.allowed must be a boolean"
      );
    }
    policy.TryGetValue("minimum_separation_pips", out var minimumRaw);
    if (!allowed)
    {
      if (minimumRaw is not null)
      {
        throw new ConfigurationV3Error(
          $"instrument {symbol}: blocks opposite positions; "
          + "minimum_separation_pips must not be declared"
        );
      }
      return new OppositePositionPolicy(false, null, pipSize);
    }
    decimal minimum;
    try
    {
      minimum = minimumRaw is null
        ? 0m
        : Convert.ToDecimal(minimumRaw, System.Globalization.CultureInfo.InvariantCulture);
    }
    catch (Exception exception) when (
      exception is FormatException or InvalidCastException or OverflowException
    )
    {
      throw new ConfigurationV3Error(
        $"instrument {symbol}: invalid exposure.opposite_position.minimum_separation_pips",
        exception
      );
    }
    if (minimum <= 0m)
    {
      throw new ConfigurationV3Error(
        $"instrument {symbol}: allows opposite positions and needs a positive "
        + "exposure.opposite_position.minimum_separation_pips"
      );
    }
    return new OppositePositionPolicy(true, minimum, pipSize);
  }
}

/// <summary>One opposite-direction group (a plan, or a broker-only position/order).</summary>
public sealed record OppositeExposureGroup(
  string Source,
  string GroupId,
  string Direction,
  decimal EntryPrice,
  string? PlanId = null,
  long? PositionId = null,
  long? OrderId = null
);

public sealed record OppositeExposureVerdict(
  bool Allowed,
  string? ReasonCode,
  string Message,
  OppositeExposureGroup? Nearest = null,
  decimal? DistancePrice = null,
  decimal? DistancePips = null
)
{
  public static readonly OppositeExposureVerdict Clear = new(true, null, "");
}

/// <summary>
/// Engine-side second fence for the autonomous opposite-exposure rule. It
/// never trusts the Python admission result and never relies on broker
/// netting: it judges tracked TradePlan runtime state plus the actual broker
/// positions and pending orders on the symbol, before any broker mutation.
/// </summary>
public static class OppositeExposureFence
{
  public const string FxOppositeNotAllowed = "fx_opposite_position_not_allowed";
  public const string XauOppositeTooClose = "xau_opposite_position_too_close";

  /// <summary>
  /// Builds the opposite-direction groups: one per live tracked plan (a ladder's
  /// legs are one group at the recorded group entry), plus every broker position
  /// or pending order that no live tracked plan accounts for (broker-only, or
  /// belonging to a plan stuck in recovery - those stop blocking only once the
  /// broker no longer reports them).
  /// </summary>
  public static IReadOnlyList<OppositeExposureGroup> CollectOppositeGroups(
    string incomingDirection,
    string incomingSymbol,
    long brokerSymbolId,
    IEnumerable<TradePlanRuntimeState> trackedStates,
    IReadOnlyList<TradingPosition> brokerPositions,
    IReadOnlyList<TradingPendingOrder> brokerOrders
  )
  {
    var groups = new List<OppositeExposureGroup>();
    var owned = new TrackedOwnership();
    foreach (var state in trackedStates)
    {
      if (!IsLive(state) || !SameInstrument(state.Symbol, incomingSymbol))
      {
        continue;
      }
      owned.Add(state);
      if (!IsOpposite(state.Direction, incomingDirection))
      {
        continue;
      }
      if (EntryReference(state) is not decimal entry || entry <= 0m)
      {
        continue;
      }
      groups.Add(new OppositeExposureGroup(
        "plan_runtime", state.PlanId, state.Direction.ToUpperInvariant(), entry,
        PlanId: state.PlanId
      ));
    }
    foreach (var position in brokerPositions)
    {
      if (position.SymbolId != brokerSymbolId || position.Volume <= 0)
      {
        continue;
      }
      if (!IsOpposite(position.Direction, incomingDirection))
      {
        continue;
      }
      if (owned.OwnsPosition(position))
      {
        continue;
      }
      groups.Add(new OppositeExposureGroup(
        "broker_position", $"position:{position.PositionId}",
        position.Direction == TradeDirection.Buy ? "BUY" : "SELL",
        position.EntryPrice, PositionId: position.PositionId
      ));
    }
    foreach (var order in brokerOrders)
    {
      if (order.SymbolId != brokerSymbolId || order.Volume <= 0)
      {
        continue;
      }
      if (!IsOpposite(order.Direction, incomingDirection))
      {
        continue;
      }
      if (owned.OwnsOrder(order))
      {
        continue;
      }
      groups.Add(new OppositeExposureGroup(
        "broker_pending_order", $"order:{order.OrderId}",
        order.Direction == TradeDirection.Buy ? "BUY" : "SELL",
        order.LimitPrice, OrderId: order.OrderId
      ));
    }
    return groups;
  }

  public static OppositeExposureVerdict Evaluate(
    string incomingSymbol,
    string incomingDirection,
    decimal incomingEntryReference,
    IReadOnlyList<OppositeExposureGroup> oppositeGroups,
    OppositePositionPolicy policy
  )
  {
    if (oppositeGroups.Count == 0 || incomingEntryReference <= 0m)
    {
      return OppositeExposureVerdict.Clear;
    }
    if (policy.PipSize <= 0m)
    {
      throw new InvalidOperationException(
        $"opposite-exposure policy for {incomingSymbol} has no positive pip size"
      );
    }
    var nearest = oppositeGroups
      .OrderBy(group => Math.Abs(incomingEntryReference - group.EntryPrice))
      .First();
    var distancePrice = Math.Abs(incomingEntryReference - nearest.EntryPrice);
    var distancePips = distancePrice / policy.PipSize;
    var canonical = CanonicalInstrument(incomingSymbol);
    if (!policy.Allowed)
    {
      return new OppositeExposureVerdict(
        false,
        FxOppositeNotAllowed,
        $"{canonical} {incomingDirection} blocked: active {nearest.Direction} "
          + $"exposure ({nearest.Source} {nearest.GroupId}) exists and this "
          + "instrument never allows opposite positions",
        nearest, distancePrice, distancePips
      );
    }
    var minimum = policy.MinimumSeparationPips
      ?? throw new InvalidOperationException(
        $"opposite-exposure policy for {incomingSymbol} allows opposite positions "
        + "without minimum_separation_pips"
      );
    if (distancePips < minimum)
    {
      return new OppositeExposureVerdict(
        false,
        XauOppositeTooClose,
        $"{canonical} {incomingDirection} entry {incomingEntryReference} is "
          + $"{decimal.Round(distancePips, 1)} pips from active {nearest.Direction} "
          + $"@ {nearest.EntryPrice}; require >= {minimum} pips",
        nearest, distancePrice, distancePips
      );
    }
    return new OppositeExposureVerdict(
      true, null, "", nearest, distancePrice, distancePips
    );
  }

  private static bool IsLive(TradePlanRuntimeState state)
  {
    if (state.Stage == TradePlanRuntimeStage.Closed)
    {
      return false;
    }
    return state.GroupStage is not (
      TradePlanGroupStages.Closed
      or TradePlanGroupStages.Rejected
      or TradePlanGroupStages.Expired
      or TradePlanGroupStages.Cancelled
      or TradePlanGroupStages.RecoveryRequired
    );
  }

  // Filled exposure uses the recorded broker fill; a plan that has not filled
  // yet uses its planned entry reference.
  private static decimal? EntryReference(TradePlanRuntimeState state)
  {
    if (state.TotalFilledVolume > 0 && state.GroupWeightedFillPrice is decimal group)
    {
      return group;
    }
    if (state.TotalFilledVolume > 0 && state.EntryFillPrice is decimal fill)
    {
      return fill;
    }
    return state.IntendedEntryPrice ?? state.GroupWeightedFillPrice ?? state.EntryFillPrice;
  }

  // Which broker positions/orders a live tracked plan already accounts for:
  // by recorded leg ids, by ownership comment/ClientOrderId, or by the compact
  // plan token (legacy tokens are one-way hashes, so the id sets matter).
  private sealed class TrackedOwnership
  {
    private readonly HashSet<string> _planIds = new(StringComparer.Ordinal);
    private readonly HashSet<string> _planTokens = new(StringComparer.Ordinal);
    private readonly HashSet<long> _positionIds = [];
    private readonly HashSet<long> _orderIds = [];
    private readonly HashSet<string> _clientOrderIds = new(StringComparer.Ordinal);

    public void Add(TradePlanRuntimeState state)
    {
      _planIds.Add(state.PlanId);
      foreach (var token in TradePlanOwnership.PlanTokens(state.PlanId))
      {
        _planTokens.Add(token);
      }
      if (state.PositionId is long positionId)
      {
        _positionIds.Add(positionId);
      }
      foreach (var orderId in state.PendingOrderIds ?? [])
      {
        _orderIds.Add(orderId);
      }
      foreach (var leg in state.Legs ?? [])
      {
        if (leg.BrokerPositionId is long legPosition)
        {
          _positionIds.Add(legPosition);
        }
        if (leg.BrokerOrderId is long legOrder)
        {
          _orderIds.Add(legOrder);
        }
        if (!string.IsNullOrWhiteSpace(leg.ClientOrderId))
        {
          _clientOrderIds.Add(leg.ClientOrderId);
        }
      }
    }

    public bool OwnsPosition(TradingPosition position) =>
      _positionIds.Contains(position.PositionId)
      || OwnsIdentity(position.Comment, position.ClientOrderId);

    public bool OwnsOrder(TradingPendingOrder order) =>
      _orderIds.Contains(order.OrderId)
      || OwnsIdentity(order.Comment, order.ClientOrderId);

    private bool OwnsIdentity(string? comment, string? clientOrderId)
    {
      if (!string.IsNullOrWhiteSpace(clientOrderId) && _clientOrderIds.Contains(clientOrderId))
      {
        return true;
      }
      if (TradePlanOwnership.TryParseOwnership(comment, clientOrderId) is { } owner
        && _planIds.Contains(owner.PlanId))
      {
        return true;
      }
      return TradePlanOwnership.TryParseCompactComment(comment) is { } compact
        && _planTokens.Contains(compact.PlanToken);
    }
  }

  private static bool IsOpposite(string direction, string incoming) =>
    !string.Equals(direction, incoming, StringComparison.OrdinalIgnoreCase)
    && (direction.Equals("BUY", StringComparison.OrdinalIgnoreCase)
      || direction.Equals("SELL", StringComparison.OrdinalIgnoreCase));

  private static bool IsOpposite(TradeDirection direction, string incoming) =>
    IsOpposite(direction == TradeDirection.Buy ? "BUY" : "SELL", incoming);

  private static bool SameInstrument(string left, string right) =>
    CanonicalInstrument(left) == CanonicalInstrument(right);

  private static string CanonicalInstrument(string symbol)
  {
    var upper = (symbol ?? "").Trim().ToUpperInvariant();
    return upper is "XAUUSD" or "GOLD" ? "XAU" : upper;
  }
}

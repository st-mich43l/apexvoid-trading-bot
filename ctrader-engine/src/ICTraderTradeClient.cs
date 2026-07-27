namespace ApexVoid.CTraderFeed;

public interface ICTraderTradeClient
{
  Task<IReadOnlyList<TradingAccountGrant>> GetAccountGrantsAsync(
    CancellationToken cancellationToken
  ) => Task.FromResult<IReadOnlyList<TradingAccountGrant>>([]);

  Task<TradingAccountSnapshot> GetTradingAccountAsync(
    CancellationToken cancellationToken
  );

  Task<IReadOnlyList<TradingPosition>> ReconcilePositionsAsync(
    CancellationToken cancellationToken
  );

  Task<IReadOnlyList<TradingPendingOrder>> ReconcilePendingOrdersAsync(
    CancellationToken cancellationToken
  ) => Task.FromResult<IReadOnlyList<TradingPendingOrder>>([]);

  async Task<TradingReconcileSnapshot> ReconcileAccountAsync(
    CancellationToken cancellationToken
  ) => new(
    await ReconcilePositionsAsync(cancellationToken),
    await ReconcilePendingOrdersAsync(cancellationToken)
  );

  Task<TradeExecution> PlaceMarketOrderAsync(
    MarketOrderRequest order,
    CancellationToken cancellationToken
  );

  Task<long> PlaceLimitOrderAsync(
    LimitOrderRequest order,
    CancellationToken cancellationToken
  ) => throw new NotSupportedException("Limit orders are not supported");

  Task CancelPendingOrderAsync(
    long orderId,
    CancellationToken cancellationToken
  ) => throw new NotSupportedException("Pending-order cancellation is not supported");

  Task AmendPositionStopLossAsync(
    long positionId,
    decimal stopLoss,
    CancellationToken cancellationToken
  );

  Task<TradeExecution> ClosePositionAsync(
    long positionId,
    long volume,
    CancellationToken cancellationToken
  );

  // Best-effort classification of why a position that vanished from a
  // reconcile snapshot actually closed (broker-attached SL/TP vs a manual
  // or external order), plus the real execution price when the closing deal
  // was found - see PositionCloseLookup. Defaults to Unknown/no-price so
  // callers (and the FakeTradingClient test double) never have to opt in
  // just to keep compiling; a client that cannot look this up should
  // simply not override it.
  Task<PositionCloseLookup> DeterminePositionCloseReasonAsync(
    long positionId,
    long approximateCloseTimestamp,
    CancellationToken cancellationToken
  ) => Task.FromResult(new PositionCloseLookup(PositionCloseReason.Unknown));
}

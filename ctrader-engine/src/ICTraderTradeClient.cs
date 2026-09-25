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
  // was found - see PositionCloseLookup. openedAtTimestamp anchors how far
  // back the deal search looks - a missed reconcile window (eg. a redeploy
  // gap) can leave the true close far earlier than approximateCloseTimestamp,
  // so the search must reach back to when the position actually opened, not
  // just a fixed few minutes before confirmation. Defaults to Unknown/no-price
  // so callers (and the FakeTradingClient test double) never have to opt in
  // just to keep compiling; a client that cannot look this up should simply
  // not override it.
  Task<PositionCloseLookup> DeterminePositionCloseReasonAsync(
    long positionId,
    long openedAtTimestamp,
    long approximateCloseTimestamp,
    CancellationToken cancellationToken
  ) => Task.FromResult(new PositionCloseLookup(PositionCloseReason.Unknown));

  // Every historical order in the window, for the caller to match against
  // its own ClientOrderIds - used to discover an AutoTradeGroupPlan whose
  // legs filled (and, per GetClosingDealsAsync below, already closed too)
  // entirely outside the engine's own tracked-position lifetime (a restart
  // gap). Defaults to empty so a client that cannot look this up simply
  // never surfaces an orphan rather than failing to compile.
  Task<IReadOnlyList<HistoricalOrderMatch>> FindHistoricalOrdersAsync(
    long fromTimestampMs,
    long toTimestampMs,
    CancellationToken cancellationToken
  ) => Task.FromResult<IReadOnlyList<HistoricalOrderMatch>>([]);

  // Every closing deal for a position, each carrying its own entry/exit
  // price so the caller can compute realized pips the same way every other
  // pips-bearing event in this system already does.
  Task<IReadOnlyList<ClosingDeal>> GetClosingDealsAsync(
    long positionId,
    long fromTimestampMs,
    long toTimestampMs,
    CancellationToken cancellationToken
  ) => Task.FromResult<IReadOnlyList<ClosingDeal>>([]);
}

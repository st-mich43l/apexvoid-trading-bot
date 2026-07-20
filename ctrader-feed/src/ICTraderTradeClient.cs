namespace ApexVoid.CTraderFeed;

public interface ICTraderTradeClient
{
  Task<TradingAccountSnapshot> GetTradingAccountAsync(
    CancellationToken cancellationToken
  );

  Task<IReadOnlyList<TradingPosition>> ReconcilePositionsAsync(
    CancellationToken cancellationToken
  );

  Task<TradeExecution> PlaceMarketOrderAsync(
    MarketOrderRequest order,
    CancellationToken cancellationToken
  );

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
}

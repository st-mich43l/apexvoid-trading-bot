using System.Reactive.Linq;
using System.Threading.Channels;
using Google.Protobuf;
using OpenAPI.Net;

namespace ApexVoid.CTraderFeed;

public sealed class CTraderOpenApiFeedClient(
  FeedOptions options,
  IRefreshTokenStore refreshTokenStore
) : ICTraderFeedClient, ICTraderTradeClient
{
  private readonly Channel<IMessage> _responses = Channel.CreateUnbounded<IMessage>();
  private readonly Channel<RawTrendbar> _liveTrendbars = Channel.CreateUnbounded<RawTrendbar>();
  private readonly Channel<SpotPrice> _liveSpots = Channel.CreateUnbounded<SpotPrice>();
  private readonly SemaphoreSlim _requestLock = new(1, 1);
  private readonly List<IDisposable> _subscriptions = [];
  private readonly RefreshTokenState _tokens = new(options, refreshTokenStore);
  private readonly object _spotSubscriptionLock = new();
  private OpenClient? _client;
  private TaskCompletionSource<bool>? _spotSubscriptionReady;
  private long _spotSubscriptionAccountId;
  private long _spotSubscriptionSymbolId;
  private SymbolInfo? _spotSymbol;
  private IReadOnlyList<TradingAccountGrant> _accountGrants = [];

  public event Action? Heartbeat;

  public async Task ConnectAndAuthorizeAsync(CancellationToken cancellationToken)
  {
    await _tokens.SeedAsync(cancellationToken);
    _client = new OpenClient(
      options.Host,
      options.Port,
      TimeSpan.FromSeconds(10),
      useWebSocket: false
    );
    _subscriptions.Add(_client.Subscribe(OnMessage, OnError));

    await _client.Connect();
    await SendAndWaitAsync<ProtoOAApplicationAuthRes>(
      new ProtoOAApplicationAuthReq
      {
        ClientId = options.ClientId,
        ClientSecret = options.ClientSecret,
      },
      _ => true,
      cancellationToken
    );
    ProtoOAGetAccountListByAccessTokenRes accounts;
    try
    {
      accounts = await GetGrantedAccountsAsync(cancellationToken);
    }
    catch (InvalidOperationException)
    {
      Log("configured access token rejected; refreshing before one auth retry");
      await RefreshTokenAsync(cancellationToken);
      accounts = await GetGrantedAccountsAsync(cancellationToken);
    }
    _accountGrants = ToAccountGrants(accounts);
    RequireConfiguredAccount(accounts);
    await AuthorizeAccountAsync(cancellationToken);
  }

  public async Task RefreshTokenAsync(CancellationToken cancellationToken)
  {
    if (string.IsNullOrWhiteSpace(_tokens.RefreshToken))
    {
      return;
    }

    var response = await SendAndWaitAsync<ProtoOARefreshTokenRes>(
      new ProtoOARefreshTokenReq { RefreshToken = _tokens.RefreshToken },
      _ => true,
      cancellationToken
    );
    await _tokens.ApplyAsync(response, cancellationToken);
  }

  public async Task<SymbolInfo> ResolveSymbolAsync(CancellationToken cancellationToken)
  {
    var symbolList = await SendAndWaitAsync<ProtoOASymbolsListRes>(
      new ProtoOASymbolsListReq { CtidTraderAccountId = options.AccountId },
      response => response.CtidTraderAccountId == options.AccountId,
      cancellationToken
    );
    var expected = NormalizeSymbol(options.CTraderSymbol);
    var light = symbolList.Symbol.FirstOrDefault(
      symbol => NormalizeSymbol(symbol.SymbolName) == expected
    ) ?? throw new InvalidOperationException(
      $"Symbol {options.CTraderSymbol} was not found on account {options.AccountId}"
    );

    var byIdReq = new ProtoOASymbolByIdReq
    {
      CtidTraderAccountId = options.AccountId,
    };
    byIdReq.SymbolId.Add(light.SymbolId);
    var full = await SendAndWaitAsync<ProtoOASymbolByIdRes>(
      byIdReq,
      response => response.CtidTraderAccountId == options.AccountId,
      cancellationToken
    );
    var fullSymbol = full.Symbol.FirstOrDefault(symbol => symbol.SymbolId == light.SymbolId)
      ?? throw new InvalidOperationException($"Symbol {light.SymbolId} details missing");

    return new SymbolInfo(
      options.RedisSymbol,
      options.CTraderSymbol,
      light.SymbolId,
      fullSymbol.Digits,
      fullSymbol.PipPosition,
      fullSymbol.MinVolume,
      fullSymbol.StepVolume,
      fullSymbol.MaxVolume,
      fullSymbol.LotSize
    );
  }

  public async Task<TradingAccountSnapshot> GetTradingAccountAsync(
    CancellationToken cancellationToken
  )
  {
    var accounts = await GetGrantedAccountsAsync(cancellationToken);
    _accountGrants = ToAccountGrants(accounts);
    var account = RequireConfiguredAccount(accounts);
    var response = await SendAndWaitAsync<ProtoOATraderRes>(
      new ProtoOATraderReq { CtidTraderAccountId = options.AccountId },
      item => item.CtidTraderAccountId == options.AccountId,
      cancellationToken
    );
    var trader = response.Trader
      ?? throw new InvalidOperationException("cTrader account profile is missing");
    return ToAccountSnapshot(account, accounts.PermissionScope, trader);
  }

  public async Task<IReadOnlyList<TradingAccountGrant>> GetAccountGrantsAsync(
    CancellationToken cancellationToken
  )
  {
    if (_accountGrants.Count > 0)
    {
      return _accountGrants;
    }
    var accounts = await GetGrantedAccountsAsync(cancellationToken);
    _accountGrants = ToAccountGrants(accounts);
    return _accountGrants;
  }

  public async Task<IReadOnlyList<TradingAccountSnapshot>> GetGrantedDemoAccountsAsync(
    CancellationToken cancellationToken
  )
  {
    var accounts = await GetGrantedAccountsAsync(cancellationToken);
    var snapshots = new List<TradingAccountSnapshot>();
    foreach (var account in accounts.CtidTraderAccount.Where(item => !item.IsLive))
    {
      var accountId = checked((long)account.CtidTraderAccountId);
      if (accountId != options.AccountId)
      {
        await AuthorizeAccountAsync(accountId, cancellationToken);
      }
      var response = await SendAndWaitAsync<ProtoOATraderRes>(
        new ProtoOATraderReq { CtidTraderAccountId = accountId },
        item => item.CtidTraderAccountId == accountId,
        cancellationToken
      );
      var trader = response.Trader
        ?? throw new InvalidOperationException(
          $"cTrader account profile is missing for {accountId}"
        );
      snapshots.Add(ToAccountSnapshot(account, accounts.PermissionScope, trader));
    }
    return snapshots;
  }

  private static TradingAccountSnapshot ToAccountSnapshot(
    ProtoOACtidTraderAccount account,
    ProtoOAClientPermissionScope permissionScope,
    ProtoOATrader trader
  )
  {
    var divisor = 1m;
    for (var index = 0; index < trader.MoneyDigits; index++)
    {
      divisor *= 10m;
    }
    return new TradingAccountSnapshot(
      checked((long)account.CtidTraderAccountId),
      account.IsLive,
      permissionScope.ToString(),
      trader.AccessRights.ToString(),
      trader.AccountType.ToString(),
      trader.BrokerName,
      trader.Balance / divisor
    );
  }

  private static IReadOnlyList<TradingAccountGrant> ToAccountGrants(
    ProtoOAGetAccountListByAccessTokenRes accounts
  ) => accounts.CtidTraderAccount
    .Select(item => new TradingAccountGrant(
      checked((long)item.CtidTraderAccountId),
      item.IsLive
    ))
    .ToArray();

  public async Task<IReadOnlyList<TradingPosition>> ReconcilePositionsAsync(
    CancellationToken cancellationToken
  )
  {
    var response = await SendAndWaitAsync<ProtoOAReconcileRes>(
      new ProtoOAReconcileReq { CtidTraderAccountId = options.AccountId },
      item => item.CtidTraderAccountId == options.AccountId,
      cancellationToken
    );
    return response.Position
      .Where(position => position.TradeData is not null)
      .Select(ToTradingPosition)
      .ToArray();
  }

  public async Task<IReadOnlyList<TradingPendingOrder>> ReconcilePendingOrdersAsync(
    CancellationToken cancellationToken
  )
  {
    var response = await SendAndWaitAsync<ProtoOAReconcileRes>(
      new ProtoOAReconcileReq { CtidTraderAccountId = options.AccountId },
      item => item.CtidTraderAccountId == options.AccountId,
      cancellationToken
    );
    return response.Order
      .Where(order => (
        order.TradeData is not null
        && order.OrderType == ProtoOAOrderType.Limit
        && order.HasLimitPrice
      ))
      .Select(ToTradingPendingOrder)
      .ToArray();
  }

  public async Task<TradeExecution> PlaceMarketOrderAsync(
    MarketOrderRequest order,
    CancellationToken cancellationToken
  )
  {
    var request = new ProtoOANewOrderReq
    {
      CtidTraderAccountId = options.AccountId,
      SymbolId = order.SymbolId,
      OrderType = ProtoOAOrderType.Market,
      TradeSide = order.Direction == TradeDirection.Buy
        ? ProtoOATradeSide.Buy
        : ProtoOATradeSide.Sell,
      Volume = order.Volume,
      RelativeStopLoss = order.RelativeStopLoss,
      Label = order.Label,
      Comment = order.Comment,
      ClientOrderId = order.ClientOrderId,
    };
    var response = await SendAndWaitAsync<ProtoOAExecutionEvent>(
      request,
      item => (
        MarketOrderMatches(item, order)
        && IsTerminalExecution(item.ExecutionType)
      ),
      cancellationToken
    );
    return ToTradeExecution(response);
  }

  public async Task<long> PlaceLimitOrderAsync(
    LimitOrderRequest order,
    CancellationToken cancellationToken
  )
  {
    var request = new ProtoOANewOrderReq
    {
      CtidTraderAccountId = options.AccountId,
      SymbolId = order.SymbolId,
      OrderType = ProtoOAOrderType.Limit,
      TradeSide = order.Direction == TradeDirection.Buy
        ? ProtoOATradeSide.Buy
        : ProtoOATradeSide.Sell,
      Volume = order.Volume,
      LimitPrice = decimal.ToDouble(order.LimitPrice),
      RelativeStopLoss = order.RelativeStopLoss,
      Label = order.Label,
      Comment = order.Comment,
      ClientOrderId = order.ClientOrderId,
    };
    var response = await SendAndWaitAsync<ProtoOAExecutionEvent>(
      request,
      item => (
        LimitOrderMatches(item, order)
        && item.ExecutionType is ProtoOAExecutionType.OrderAccepted
          or ProtoOAExecutionType.OrderFilled
          or ProtoOAExecutionType.OrderRejected
      ),
      cancellationToken
    );
    ThrowIfRejected(response);
    var orderId = response.Order?.OrderId ?? response.Deal?.OrderId ?? 0;
    if (orderId <= 0)
    {
      throw new InvalidOperationException(
        "cTrader limit-order response did not return an order ID"
      );
    }
    return orderId;
  }

  public async Task CancelPendingOrderAsync(
    long orderId,
    CancellationToken cancellationToken
  )
  {
    var response = await SendAndWaitAsync<ProtoOAExecutionEvent>(
      new ProtoOACancelOrderReq
      {
        CtidTraderAccountId = options.AccountId,
        OrderId = orderId,
      },
      item => (
        item.Order?.OrderId == orderId
        && item.ExecutionType is ProtoOAExecutionType.OrderCancelled
          or ProtoOAExecutionType.OrderRejected
      ),
      cancellationToken
    );
    ThrowIfRejected(response);
  }

  public async Task AmendPositionStopLossAsync(
    long positionId,
    decimal stopLoss,
    CancellationToken cancellationToken
  )
  {
    var response = await SendAndWaitAsync<ProtoOAExecutionEvent>(
      new ProtoOAAmendPositionSLTPReq
      {
        CtidTraderAccountId = options.AccountId,
        PositionId = positionId,
        StopLoss = decimal.ToDouble(stopLoss),
      },
      item => (
        ExecutionPositionId(item) == positionId
        && item.ExecutionType is ProtoOAExecutionType.OrderReplaced
          or ProtoOAExecutionType.OrderRejected
      ),
      cancellationToken
    );
    ThrowIfRejected(response);
  }

  public async Task<TradeExecution> ClosePositionAsync(
    long positionId,
    long volume,
    CancellationToken cancellationToken
  )
  {
    var response = await SendAndWaitAsync<ProtoOAExecutionEvent>(
      new ProtoOAClosePositionReq
      {
        CtidTraderAccountId = options.AccountId,
        PositionId = positionId,
        Volume = volume,
      },
      item => (
        ExecutionPositionId(item) == positionId
        && IsTerminalExecution(item.ExecutionType)
      ),
      cancellationToken
    );
    return ToTradeExecution(response);
  }

  public async Task<IReadOnlyList<RawTrendbar>> GetTrendbarsAsync(
    SymbolInfo symbol,
    string timeframe,
    DateTimeOffset from,
    DateTimeOffset to,
    CancellationToken cancellationToken
  )
  {
    var response = await SendAndWaitAsync<ProtoOAGetTrendbarsRes>(
      new ProtoOAGetTrendbarsReq
      {
        CtidTraderAccountId = options.AccountId,
        SymbolId = symbol.SymbolId,
        Period = TimeframeCodec.ToProto(timeframe),
        FromTimestamp = from.ToUnixTimeMilliseconds(),
        ToTimestamp = to.ToUnixTimeMilliseconds(),
      },
      res => res.SymbolId == symbol.SymbolId && res.Period == TimeframeCodec.ToProto(timeframe),
      cancellationToken
    );
    return response.Trendbar.Select(ToRaw).OrderBy(bar => bar.UtcTimestampInMinutes).ToArray();
  }

  public async Task SubscribeAsync(
    SymbolInfo symbol,
    IReadOnlyCollection<string> timeframes,
    CancellationToken cancellationToken
  )
  {
    var spotReady = new TaskCompletionSource<bool>(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    lock (_spotSubscriptionLock)
    {
      _spotSubscriptionReady = spotReady;
      _spotSubscriptionAccountId = options.AccountId;
      _spotSubscriptionSymbolId = symbol.SymbolId;
      _spotSymbol = symbol;
    }

    var spotReq = new ProtoOASubscribeSpotsReq
    {
      CtidTraderAccountId = options.AccountId,
      SubscribeToSpotTimestamp = true,
    };
    spotReq.SymbolId.Add(symbol.SymbolId);
    try
    {
      Log($"subscribing spots {symbol.RedisSymbol} symbolId={symbol.SymbolId}");
      await SendAndWaitAsync<ProtoOASubscribeSpotsRes>(
        spotReq,
        response => response.CtidTraderAccountId == options.AccountId,
        cancellationToken
      );
      Log($"spot subscription queued {symbol.RedisSymbol}; waiting for technical spot event");
      await WaitForSpotSubscriptionAsync(spotReady.Task, cancellationToken);
      Log($"spot subscription active {symbol.RedisSymbol}");
    }
    finally
    {
      lock (_spotSubscriptionLock)
      {
        if (ReferenceEquals(_spotSubscriptionReady, spotReady))
        {
          _spotSubscriptionReady = null;
        }
      }
    }

    foreach (var timeframe in timeframes)
    {
      var period = TimeframeCodec.ToProto(timeframe);
      Log(
        $"subscribing live trendbar {symbol.RedisSymbol} {timeframe} symbolId={symbol.SymbolId} period={period}"
      );
      await SendAsync(
        new ProtoOASubscribeLiveTrendbarReq
        {
          CtidTraderAccountId = options.AccountId,
          SymbolId = symbol.SymbolId,
          Period = period,
        },
        cancellationToken
      );
      Log(
        $"live trendbar subscribe sent {symbol.RedisSymbol} {timeframe}; continuing without ack"
      );
    }
  }

  public async IAsyncEnumerable<RawTrendbar> LiveTrendbarsAsync(
    [System.Runtime.CompilerServices.EnumeratorCancellation]
    CancellationToken cancellationToken
  )
  {
    while (await _liveTrendbars.Reader.WaitToReadAsync(cancellationToken))
    {
      while (_liveTrendbars.Reader.TryRead(out var trendbar))
      {
        yield return trendbar;
      }
    }
  }

  public async IAsyncEnumerable<SpotPrice> LiveSpotsAsync(
    [System.Runtime.CompilerServices.EnumeratorCancellation]
    CancellationToken cancellationToken
  )
  {
    while (await _liveSpots.Reader.WaitToReadAsync(cancellationToken))
    {
      while (_liveSpots.Reader.TryRead(out var spot))
      {
        yield return spot;
      }
    }
  }

  public async ValueTask DisposeAsync()
  {
    foreach (var subscription in _subscriptions)
    {
      subscription.Dispose();
    }
    _subscriptions.Clear();
    if (_client is not null)
    {
      _client.Dispose();
    }
    _responses.Writer.TryComplete();
    _liveTrendbars.Writer.TryComplete();
    _liveSpots.Writer.TryComplete();
    await Task.CompletedTask;
  }

  private void OnMessage(IMessage message)
  {
    if (message is ProtoHeartbeatEvent)
    {
      Heartbeat?.Invoke();
      return;
    }
    if (message is ProtoOASpotEvent spot)
    {
      CompleteSpotSubscription(spot);
      var decoded = ToSpot(spot);
      if (decoded is not null)
      {
        _liveSpots.Writer.TryWrite(decoded);
      }
      foreach (var trendbar in spot.Trendbar)
      {
        _liveTrendbars.Writer.TryWrite(ToRaw(trendbar));
      }
      return;
    }
    _responses.Writer.TryWrite(message);
  }

  private void OnError(Exception exception)
  {
    var wrapped = new InvalidOperationException(
      $"cTrader transport error: {exception.GetType().Name}: {exception.Message}",
      exception
    );
    _responses.Writer.TryComplete(wrapped);
    _liveTrendbars.Writer.TryComplete(wrapped);
    _liveSpots.Writer.TryComplete(wrapped);
    TaskCompletionSource<bool>? spotSubscriptionReady;
    lock (_spotSubscriptionLock)
    {
      spotSubscriptionReady = _spotSubscriptionReady;
      _spotSubscriptionReady = null;
    }
    spotSubscriptionReady?.TrySetException(wrapped);
  }

  private async Task<T> SendAndWaitAsync<T>(
    IMessage request,
    Func<T, bool> predicate,
    CancellationToken cancellationToken
  )
    where T : class, IMessage
  {
    await _requestLock.WaitAsync(cancellationToken);
    try
    {
      var client = _client ?? throw new InvalidOperationException("Client is not connected");
      using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
      timeout.CancelAfter(options.RequestTimeout);

      await client.SendMessage(request);
      try
      {
        while (await _responses.Reader.WaitToReadAsync(timeout.Token))
        {
          while (_responses.Reader.TryRead(out var message))
          {
            if (message is ProtoOAErrorRes error)
            {
              throw new InvalidOperationException(
                $"cTrader Open API error while waiting for {typeof(T).Name} after {request.GetType().Name}: {FormatError(error)}"
              );
            }
            if (message is ProtoErrorRes genericError)
            {
              throw new InvalidOperationException(
                $"cTrader transport error while waiting for {typeof(T).Name} after {request.GetType().Name}: {FormatError(genericError)}"
              );
            }
            if (message is ProtoOAOrderErrorEvent orderError)
            {
              throw new InvalidOperationException(
                $"cTrader order error after {request.GetType().Name}: "
                + $"{orderError.ErrorCode}: {orderError.Description}"
              );
            }
            if (message is T typed && predicate(typed))
            {
              return typed;
            }
          }
        }
      }
      catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
      {
        throw new TimeoutException(
          $"Timed out after {options.RequestTimeout.TotalSeconds:N0}s waiting for {typeof(T).Name} after {request.GetType().Name}"
        );
      }
      throw new TimeoutException(
        $"Timed out after {options.RequestTimeout.TotalSeconds:N0}s waiting for {typeof(T).Name} after {request.GetType().Name}"
      );
    }
    finally
    {
      _requestLock.Release();
    }
  }

  private Task<ProtoOAAccountAuthRes> AuthorizeAccountAsync(
    CancellationToken cancellationToken
  ) => AuthorizeAccountAsync(options.AccountId, cancellationToken);

  private Task<ProtoOAAccountAuthRes> AuthorizeAccountAsync(
    long accountId,
    CancellationToken cancellationToken
  ) => SendAndWaitAsync<ProtoOAAccountAuthRes>(
    new ProtoOAAccountAuthReq
    {
      CtidTraderAccountId = accountId,
      AccessToken = _tokens.AccessToken,
    },
    response => response.CtidTraderAccountId == accountId,
    cancellationToken
  );

  private Task<ProtoOAGetAccountListByAccessTokenRes> GetGrantedAccountsAsync(
    CancellationToken cancellationToken
  ) => SendAndWaitAsync<ProtoOAGetAccountListByAccessTokenRes>(
    new ProtoOAGetAccountListByAccessTokenReq
    {
      AccessToken = _tokens.AccessToken,
    },
    _ => true,
    cancellationToken
  );

  private ProtoOACtidTraderAccount RequireConfiguredAccount(
    ProtoOAGetAccountListByAccessTokenRes accounts
  )
  {
    var account = accounts.CtidTraderAccount.FirstOrDefault(
      item => item.CtidTraderAccountId == (ulong)options.AccountId
    );
    if (account is not null)
    {
      return account;
    }
    throw AutoTradeConfigurationException.AccountNotGranted(
      options.AccountId,
      ToAccountGrants(accounts)
    );
  }

  private async Task SendAsync(
    IMessage request,
    CancellationToken cancellationToken
  )
  {
    var client = _client ?? throw new InvalidOperationException("Client is not connected");
    cancellationToken.ThrowIfCancellationRequested();
    await client.SendMessage(request);
  }

  private static RawTrendbar ToRaw(ProtoOATrendbar bar) =>
    new(
      TimeframeCodec.FromProto(bar.Period),
      bar.Low,
      bar.DeltaOpen,
      bar.DeltaHigh,
      bar.DeltaClose,
      bar.Volume,
      bar.UtcTimestampInMinutes,
      bar.HasDeltaClose
    );

  private SpotPrice? ToSpot(ProtoOASpotEvent spot)
  {
    SymbolInfo? symbol;
    lock (_spotSubscriptionLock)
    {
      symbol = _spotSymbol;
    }
    if (symbol is null || spot.SymbolId != symbol.SymbolId || spot.Bid <= 0 || spot.Ask <= 0)
    {
      return null;
    }
    return new SpotPrice(
      symbol.RedisSymbol,
      OpenApiPrice.Decode(spot.Bid),
      OpenApiPrice.Decode(spot.Ask),
      spot.Timestamp > 0
        ? spot.Timestamp / 1_000
        : DateTimeOffset.UtcNow.ToUnixTimeSeconds()
    );
  }

  private static string NormalizeSymbol(string symbol) =>
    symbol.Replace("/", "", StringComparison.Ordinal).ToUpperInvariant();

  private void CompleteSpotSubscription(ProtoOASpotEvent spot)
  {
    TaskCompletionSource<bool>? ready = null;
    lock (_spotSubscriptionLock)
    {
      if (
        _spotSubscriptionReady is not null
        && spot.CtidTraderAccountId == _spotSubscriptionAccountId
        && spot.SymbolId == _spotSubscriptionSymbolId
      )
      {
        ready = _spotSubscriptionReady;
      }
    }
    ready?.TrySetResult(true);
  }

  private async Task WaitForSpotSubscriptionAsync(
    Task spotReady,
    CancellationToken cancellationToken
  )
  {
    using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    timeout.CancelAfter(options.RequestTimeout);
    try
    {
      await spotReady.WaitAsync(timeout.Token);
    }
    catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
    {
      throw new TimeoutException(
        $"Timed out after {options.RequestTimeout.TotalSeconds:N0}s waiting for technical ProtoOASpotEvent after ProtoOASubscribeSpotsReq"
      );
    }
  }

  private static string FormatError(ProtoOAErrorRes error) =>
    string.IsNullOrWhiteSpace(error.Description)
      ? error.ErrorCode
      : $"{error.ErrorCode}: {error.Description}";

  private static string FormatError(ProtoErrorRes error) =>
    string.IsNullOrWhiteSpace(error.Description)
      ? error.ErrorCode
      : $"{error.ErrorCode}: {error.Description}";

  private static TradingPosition ToTradingPosition(ProtoOAPosition position)
  {
    var data = position.TradeData;
    return new TradingPosition(
      position.PositionId,
      data.SymbolId,
      data.TradeSide == ProtoOATradeSide.Buy
        ? TradeDirection.Buy
        : TradeDirection.Sell,
      data.Volume,
      Convert.ToDecimal(position.Price),
      position.HasStopLoss ? Convert.ToDecimal(position.StopLoss) : null,
      data.Label,
      data.Comment
    );
  }

  private static TradingPendingOrder ToTradingPendingOrder(ProtoOAOrder order)
  {
    var data = order.TradeData;
    return new TradingPendingOrder(
      order.OrderId,
      data.SymbolId,
      data.TradeSide == ProtoOATradeSide.Buy
        ? TradeDirection.Buy
        : TradeDirection.Sell,
      data.Volume,
      Convert.ToDecimal(order.LimitPrice),
      data.Label,
      data.Comment
    );
  }

  private static bool IsTerminalExecution(ProtoOAExecutionType type) =>
    type is ProtoOAExecutionType.OrderFilled
      or ProtoOAExecutionType.OrderRejected;

  private static bool MarketOrderMatches(
    ProtoOAExecutionEvent response,
    MarketOrderRequest order
  ) => response.Order?.ClientOrderId == order.ClientOrderId
    || (
      response.Position?.TradeData?.Label == order.Label
      && response.Position.TradeData.Comment == order.Comment
    );

  private static bool LimitOrderMatches(
    ProtoOAExecutionEvent response,
    LimitOrderRequest order
  ) => response.Order?.ClientOrderId == order.ClientOrderId
    || (
      response.Order?.TradeData?.Label == order.Label
      && response.Order.TradeData.Comment == order.Comment
    );

  private static long ExecutionPositionId(ProtoOAExecutionEvent response) =>
    response.Position?.PositionId
    ?? response.Deal?.PositionId
    ?? response.Order?.PositionId
    ?? 0;

  private static TradeExecution ToTradeExecution(ProtoOAExecutionEvent response)
  {
    ThrowIfRejected(response);
    var positionId = ExecutionPositionId(response);
    if (positionId <= 0)
    {
      throw new InvalidOperationException("cTrader execution did not return a position ID");
    }
    var price = response.Deal?.ExecutionPrice
      ?? response.Order?.ExecutionPrice
      ?? response.Position?.Price
      ?? 0;
    var dealVolume = response.Deal is { } deal
      ? deal.FilledVolume > 0 ? deal.FilledVolume : deal.Volume
      : 0;
    var volume = dealVolume > 0
      ? dealVolume
      : response.Order?.ExecutedVolume ?? 0;
    return new TradeExecution(
      positionId,
      response.Order?.OrderId ?? response.Deal?.OrderId ?? 0,
      Convert.ToDecimal(price),
      volume,
      response.Position?.TradeData?.Volume
    );
  }

  private static void ThrowIfRejected(ProtoOAExecutionEvent response)
  {
    if (response.ExecutionType == ProtoOAExecutionType.OrderRejected)
    {
      throw new InvalidOperationException(
        $"cTrader rejected order operation: {response.ErrorCode}"
      );
    }
  }

  private static void Log(string message) =>
    Console.Error.WriteLine($"ctrader-feed {message}");
}

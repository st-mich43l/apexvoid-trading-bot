using System.Reactive.Linq;
using System.Threading.Channels;
using Google.Protobuf;
using OpenAPI.Net;

namespace ApexVoid.CTraderFeed;

public sealed class CTraderOpenApiFeedClient(
  FeedOptions options,
  IRefreshTokenStore refreshTokenStore
) : ICTraderFeedClient
{
  private readonly Channel<IMessage> _responses = Channel.CreateUnbounded<IMessage>();
  private readonly Channel<RawTrendbar> _liveTrendbars = Channel.CreateUnbounded<RawTrendbar>();
  private readonly Channel<SpotPrice> _liveSpots = Channel.CreateUnbounded<SpotPrice>();
  private readonly List<IDisposable> _subscriptions = [];
  private readonly RefreshTokenState _tokens = new(options, refreshTokenStore);
  private readonly object _spotSubscriptionLock = new();
  private OpenClient? _client;
  private TaskCompletionSource<bool>? _spotSubscriptionReady;
  private long _spotSubscriptionAccountId;
  private long _spotSubscriptionSymbolId;
  private SymbolInfo? _spotSymbol;

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
    await RefreshTokenAsync(cancellationToken);
    await SendAndWaitAsync<ProtoOAAccountAuthRes>(
      new ProtoOAAccountAuthReq
      {
        CtidTraderAccountId = options.AccountId,
        AccessToken = _tokens.AccessToken,
      },
      _ => true,
      cancellationToken
    );
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
      fullSymbol.Digits
    );
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
      bar.UtcTimestampInMinutes
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
    var scale = DecimalScale(symbol.Digits);
    return new SpotPrice(
      symbol.RedisSymbol,
      spot.Bid / scale,
      spot.Ask / scale,
      DateTimeOffset.UtcNow.ToUnixTimeSeconds()
    );
  }

  private static decimal DecimalScale(int digits)
  {
    var scale = 1m;
    for (var i = 0; i < digits; i++)
    {
      scale *= 10m;
    }
    return scale;
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

  private static void Log(string message) =>
    Console.Error.WriteLine($"ctrader-feed {message}");
}

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
  private readonly List<IDisposable> _subscriptions = [];
  private readonly RefreshTokenState _tokens = new(options, refreshTokenStore);
  private OpenClient? _client;

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
    var spotReq = new ProtoOASubscribeSpotsReq
    {
      CtidTraderAccountId = options.AccountId,
    };
    spotReq.SymbolId.Add(symbol.SymbolId);
    await SendAndWaitAsync<ProtoOASubscribeSpotsRes>(
      spotReq,
      response => response.CtidTraderAccountId == options.AccountId,
      cancellationToken
    );

    foreach (var timeframe in timeframes)
    {
      await SendAndWaitAsync<ProtoOASubscribeLiveTrendbarRes>(
        new ProtoOASubscribeLiveTrendbarReq
        {
          CtidTraderAccountId = options.AccountId,
          SymbolId = symbol.SymbolId,
          Period = TimeframeCodec.ToProto(timeframe),
        },
        response => response.CtidTraderAccountId == options.AccountId,
        cancellationToken
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
    _responses.Writer.TryComplete(exception);
    _liveTrendbars.Writer.TryComplete(exception);
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
    while (await _responses.Reader.WaitToReadAsync(timeout.Token))
    {
      while (_responses.Reader.TryRead(out var message))
      {
        if (message is ProtoOAErrorRes error)
        {
          throw new InvalidOperationException($"cTrader Open API error: {error.ErrorCode}");
        }
        if (message is T typed && predicate(typed))
        {
          return typed;
        }
      }
    }
    throw new TimeoutException($"Timed out waiting for {typeof(T).Name}");
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

  private static string NormalizeSymbol(string symbol) =>
    symbol.Replace("/", "", StringComparison.Ordinal).ToUpperInvariant();
}

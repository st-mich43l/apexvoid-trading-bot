namespace ApexVoid.CTraderFeed;

public sealed class FeedRunner(
  FeedOptions options,
  Func<ICTraderFeedClient> clientFactory,
  IBarSink sink,
  HealthFile healthFile,
  Func<int, TimeSpan>? reconnectDelay = null
)
{
  public async Task RunForeverAsync(CancellationToken cancellationToken)
  {
    var attempt = 0;
    while (!cancellationToken.IsCancellationRequested)
    {
      try
      {
        await RunOneSessionAsync(cancellationToken);
        attempt = 0;
      }
      catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
      {
        throw;
      }
      catch (Exception ex)
      {
        attempt++;
        var delay = (reconnectDelay ?? Backoff)(attempt);
        Console.Error.WriteLine(
          $"ctrader-feed session failed: {ex.GetType().Name}: {ex.Message}; reconnecting in {delay.TotalSeconds:N0}s"
        );
        await Task.Delay(delay, cancellationToken);
      }
    }
  }

  public async Task RunOneSessionAsync(CancellationToken cancellationToken)
  {
    await using var client = clientFactory();
    void TouchOnHeartbeat() => healthFile.Touch();
    client.Heartbeat += TouchOnHeartbeat;
    using var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    Task? refreshTask = null;
    try
    {
      await client.ConnectAndAuthorizeAsync(cancellationToken);
      var symbol = await client.ResolveSymbolAsync(cancellationToken);
      await BackfillAsync(client, symbol, cancellationToken);
      await client.SubscribeAsync(symbol, options.Timeframes, cancellationToken);
      healthFile.Touch();

      refreshTask = RefreshLoopAsync(client, linked.Token);
      var emitter = new ClosedBarEmitter();
      await foreach (var raw in client.LiveTrendbarsAsync(cancellationToken))
      {
        var bar = TrendbarDecoder.Decode(raw, symbol.Digits);
        foreach (var closed in emitter.Observe(raw.Timeframe, bar))
        {
          await sink.WriteClosedBarAsync(
            symbol.RedisSymbol,
            raw.Timeframe,
            closed,
            cancellationToken
          );
          healthFile.Touch();
        }
      }
    }
    finally
    {
      client.Heartbeat -= TouchOnHeartbeat;
      linked.Cancel();
      if (refreshTask is not null)
      {
        await IgnoreCancellation(refreshTask);
      }
    }
  }

  private async Task BackfillAsync(
    ICTraderFeedClient client,
    SymbolInfo symbol,
    CancellationToken cancellationToken
  )
  {
    var now = DateTimeOffset.UtcNow;
    foreach (var timeframe in options.Timeframes)
    {
      var seconds = TimeframeCodec.ToSeconds(timeframe);
      var latest = await sink.GetLatestTimestampAsync(
        symbol.RedisSymbol,
        timeframe,
        cancellationToken
      );
      var from = latest is null
        ? now.AddSeconds(-seconds * options.BackfillBars)
        : DateTimeOffset.FromUnixTimeSeconds(latest.Value + seconds);
      var rawBars = await client.GetTrendbarsAsync(
        symbol,
        timeframe,
        from,
        now,
        cancellationToken
      );
      foreach (var raw in rawBars.OrderBy(bar => bar.UtcTimestampInMinutes))
      {
        var bar = TrendbarDecoder.Decode(raw, symbol.Digits);
        if (bar.CloseTimestamp(timeframe) > now.ToUnixTimeSeconds())
        {
          continue;
        }
        await sink.WriteClosedBarAsync(
          symbol.RedisSymbol,
          timeframe,
          bar,
          cancellationToken
        );
      }
    }
    healthFile.Touch();
  }

  private async Task RefreshLoopAsync(
    ICTraderFeedClient client,
    CancellationToken cancellationToken
  )
  {
    while (!cancellationToken.IsCancellationRequested)
    {
      await Task.Delay(options.TokenRefreshInterval, cancellationToken);
      await client.RefreshTokenAsync(cancellationToken);
    }
  }

  private static TimeSpan Backoff(int attempt)
  {
    var seconds = Math.Min(60, Math.Pow(2, Math.Min(attempt, 6)));
    return TimeSpan.FromSeconds(seconds);
  }

  private static async Task IgnoreCancellation(Task task)
  {
    try
    {
      await task;
    }
    catch (OperationCanceledException)
    {
    }
  }
}

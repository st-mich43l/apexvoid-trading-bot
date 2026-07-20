namespace ApexVoid.CTraderFeed;

public sealed class FeedRunner(
  FeedOptions options,
  Func<ICTraderFeedClient> clientFactory,
  IBarSink sink,
  HealthFile healthFile,
  Func<int, TimeSpan>? reconnectDelay = null,
  Action<string>? warningLog = null,
  AutoTradeEngine? autoTrade = null
)
{
  private bool _startupBackfillPending = true;

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
    Task? spotTask = null;
    Task? autoTradeTask = null;
    try
    {
      Log(
        $"connecting to {options.Host}:{options.Port} account={options.AccountId} symbol={options.CTraderSymbol} timeframes={string.Join(",", options.Timeframes)}"
      );
      await client.ConnectAndAuthorizeAsync(cancellationToken);
      Log("authorized cTrader session");
      var symbol = await client.ResolveSymbolAsync(cancellationToken);
      Log(
        $"resolved symbol {symbol.CTraderSymbol} -> id={symbol.SymbolId} redis={symbol.RedisSymbol} digits={symbol.Digits}"
      );
      var fullWindowBackfill = _startupBackfillPending;
      await BackfillAsync(client, symbol, fullWindowBackfill, cancellationToken);
      _startupBackfillPending = false;
      Log("backfill complete");
      await client.SubscribeAsync(symbol, options.Timeframes, cancellationToken);
      Log("subscribed live trendbars");
      healthFile.Touch();

      refreshTask = RefreshLoopAsync(client, linked.Token);
      var spots = new SpotHistory();
      spotTask = SpotLoopAsync(client, spots, autoTrade, linked.Token);
      if (autoTrade?.Enabled == true)
      {
        autoTradeTask = autoTrade.RunSessionAsync(client, symbol, linked.Token);
        _ = autoTradeTask.ContinueWith(
          _ => linked.Cancel(),
          CancellationToken.None,
          TaskContinuationOptions.OnlyOnFaulted,
          TaskScheduler.Default
        );
      }
      var emitter = new ClosedBarEmitter(spots, symbol.RedisSymbol);
      var quality = new LiveBarQualityMonitor(
        options.BarQualityLookback,
        warningLog ?? Warn
      );
      var rawDumped = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
      Log("live stream started");
      await foreach (var raw in client.LiveTrendbarsAsync(linked.Token))
      {
        if (rawDumped.Add(raw.Timeframe))
        {
          LogRawTrendbar("live", raw);
        }
        var bar = TrendbarDecoder.Decode(raw, symbol.Digits);
        foreach (var emission in emitter.Observe(raw.Timeframe, bar))
        {
          var closed = await ClosedBarCloseResolver.ResolveAsync(
            client,
            symbol,
            raw.Timeframe,
            emission,
            cancellationToken
          );
          if (emission.RequiresHistoricalClose)
          {
            Log(
              $"live close fallback {symbol.RedisSymbol} {raw.Timeframe} "
              + $"ts={closed.Timestamp} close={closed.Close}"
            );
          }
          quality.Observe(raw.Timeframe, closed);
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
      if (spotTask is not null)
      {
        await IgnoreCancellation(spotTask);
      }
      if (autoTradeTask is not null)
      {
        await IgnoreCancellation(autoTradeTask);
      }
    }
  }

  private async Task SpotLoopAsync(
    ICTraderFeedClient client,
    SpotHistory spots,
    AutoTradeEngine? autoTrade,
    CancellationToken cancellationToken
  )
  {
    await foreach (var spot in client.LiveSpotsAsync(cancellationToken))
    {
      spots.Observe(spot);
      await sink.WriteSpotAsync(spot, cancellationToken);
      if (autoTrade is not null)
      {
        await autoTrade.ObserveSpotAsync(spot, cancellationToken);
      }
      healthFile.Touch();
    }
  }

  private async Task BackfillAsync(
    ICTraderFeedClient client,
    SymbolInfo symbol,
    bool fullWindow,
    CancellationToken cancellationToken
  )
  {
    var now = DateTimeOffset.UtcNow;
    foreach (var timeframe in options.Timeframes)
    {
      var seconds = TimeframeCodec.ToSeconds(timeframe);
      var latest = fullWindow
        ? null
        : await sink.GetLatestTimestampAsync(
          symbol.RedisSymbol,
          timeframe,
          cancellationToken
        );
      var from = fullWindow || latest is null
        ? now.AddSeconds(-seconds * options.BackfillBars)
        : DateTimeOffset.FromUnixTimeSeconds(latest.Value + seconds);
      Log(
        $"backfill {symbol.RedisSymbol} {timeframe} "
        + $"mode={(fullWindow ? "full-window" : "incremental")} "
        + $"from={from:O} to={now:O}"
      );
      var rawBars = await client.GetTrendbarsAsync(
        symbol,
        timeframe,
        from,
        now,
        cancellationToken
      );
      var firstRaw = rawBars.FirstOrDefault();
      if (firstRaw is not null)
      {
        LogRawTrendbar("historical", firstRaw);
      }
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
          cancellationToken,
          publish: false
        );
      }
      Log($"backfill {symbol.RedisSymbol} {timeframe}: wrote {rawBars.Count} raw bars");
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

  private static void Log(string message) =>
    Console.Error.WriteLine($"ctrader-feed {message}");

  private static void Warn(string message) =>
    Console.Error.WriteLine($"ctrader-feed WARNING {message}");

  private static void LogRawTrendbar(string source, RawTrendbar raw) =>
    Log(
      $"raw {source} trendbar tf={raw.Timeframe} tsMin={raw.UtcTimestampInMinutes} "
      + $"low={raw.Low} deltaOpen={raw.DeltaOpen} deltaHigh={raw.DeltaHigh} "
      + $"deltaClose={raw.DeltaClose} hasDeltaClose={raw.HasDeltaClose}"
    );
}

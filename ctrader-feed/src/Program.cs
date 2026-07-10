namespace ApexVoid.CTraderFeed;

public static class Program
{
  public static async Task<int> Main(string[] args)
  {
    if (args.Contains("--healthcheck", StringComparer.OrdinalIgnoreCase))
    {
      var path = Environment.GetEnvironmentVariable("HEALTH_FILE")
        ?? "/tmp/ctrader-feed.heartbeat";
      return HealthFile.Check(path, TimeSpan.FromMinutes(10));
    }

    var options = FeedOptions.FromEnvironment();
    await using var redis = await StackExchangeRedisSeriesCommands.ConnectAsync(
      options.RedisUrl
    );
    var sink = new RedisBarSink(
      redis,
      options.BarsWindowMax,
      options.BarsChannel
    );
    var refreshTokenStore = new RedisRefreshTokenStore(
      redis,
      options.RefreshTokenKey
    );
    var runner = new FeedRunner(
      options,
      () => new CTraderOpenApiFeedClient(options, refreshTokenStore),
      sink,
      new HealthFile(options.HeartbeatFile)
    );

    using var cts = new CancellationTokenSource();
    Console.CancelKeyPress += (_, eventArgs) =>
    {
      eventArgs.Cancel = true;
      cts.Cancel();
    };
    AppDomain.CurrentDomain.ProcessExit += (_, _) => cts.Cancel();

    await runner.RunForeverAsync(cts.Token);
    return 0;
  }
}

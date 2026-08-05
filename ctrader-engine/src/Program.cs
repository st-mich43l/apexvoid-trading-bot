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

    DailyFileLog.Install();

    var resetTokenCache = args.Contains(
      "--reset-token-cache",
      StringComparer.OrdinalIgnoreCase
    );
    if (
      resetTokenCache
      && !args.Contains("--yes-i-know", StringComparer.OrdinalIgnoreCase)
    )
    {
      Console.Error.WriteLine(
        "Refusing to reset the cTrader token cache without --yes-i-know. "
        + "The environment refresh token was likely invalidated by rotation; "
        + "deleting both persisted tiers may require manual Playground re-authorisation."
      );
      return 2;
    }

    var configurationSource = RuntimeManifestBootstrap.ReadSource();
    var parityMode = RuntimeManifestBootstrap.ReadParityMode();
    var environmentFeedOptions = FeedOptions.FromEnvironment();
    var environmentTradeOptions = AutoTradeOptions.FromEnvironment();
    FeedOptions options = environmentFeedOptions;
    AutoTradeOptions autoTradeOptions = environmentTradeOptions;
    string? manifestFingerprint = null;
    string parityState = "skipped";

    if (
      parityMode != CtraderManifestParityMode.Off
      || configurationSource == CtraderConfigurationSource.Manifest
    )
    {
      var manifestPath = RuntimeManifestBootstrap.RequireManifestPath();
      var manifest = ResolvedRuntimeManifestLoader.Load(manifestPath);
      manifestFingerprint = manifest.EffectiveConfigurationFingerprint;
      var manifestFeedOptions = FeedOptions.FromRuntimeManifest(
        manifest,
        environmentFeedOptions
      );
      var manifestTradeOptions = AutoTradeOptions.FromRuntimeManifest(
        manifest,
        environmentTradeOptions
      );
      if (parityMode != CtraderManifestParityMode.Off)
      {
        var parity = RuntimeManifestParity.Compare(
          environmentFeedOptions,
          environmentTradeOptions,
          manifestFeedOptions,
          manifestTradeOptions
        );
        parityState = parity.HasFatal ? "mismatch" : "matched";
        RuntimeManifestParity.ApplyParityMode(
          parity,
          parityMode,
          message => Console.Error.WriteLine(message)
        );
      }
      if (configurationSource == CtraderConfigurationSource.Manifest)
      {
        options = manifestFeedOptions;
        autoTradeOptions = manifestTradeOptions;
      }
    }

    Console.WriteLine(
      "runtime_manifest_loaded "
      + $"manifest_version=1 "
      + $"fingerprint={(manifestFingerprint is null ? "-" : manifestFingerprint[..12])} "
      + $"source={configurationSource.ToString().ToLowerInvariant()} "
      + $"parity={parityMode.ToString().ToLowerInvariant()} "
      + $"instruments={string.Join(',', autoTradeOptions.EffectiveSymbols)} "
      + $"parity_state={parityState}"
    );

    await using var redis = await StackExchangeRedisSeriesCommands.ConnectAsync(
      options.RedisUrl
    );
    var redisRefreshTokenStore = new RedisRefreshTokenStore(
      redis,
      options.RefreshTokenKey
    );
    var fileRefreshTokenStore = new FileRefreshTokenStore(
      options.RefreshTokenFile
    );
    if (resetTokenCache)
    {
      var resetStore = new TieredRefreshTokenStore(
        redisRefreshTokenStore,
        fileRefreshTokenStore
      );
      await resetStore.DeleteAsync(CancellationToken.None);
      Console.WriteLine(
        $"Deleted refresh-token cache key {options.RefreshTokenKey} "
        + $"and mirror {options.RefreshTokenFile}"
      );
      return 0;
    }
    var sink = new RedisBarSink(
      redis,
      options.BarsWindowMax,
      options.BarsChannel
    );
    var autoTrade = new AutoTradeEngine(autoTradeOptions, redis);
    Func<string, string, CancellationToken, Task> notify =
      autoTrade.PublishOperationalEventAsync;
    var tokenEvents = new TokenEventNotifier(notify);
    var refreshTokenStore = new TieredRefreshTokenStore(
      redisRefreshTokenStore,
      fileRefreshTokenStore,
      notify,
      notifications: tokenEvents
    );
    if (
      args.Contains("--account-check", StringComparer.OrdinalIgnoreCase)
      || args.Contains("--account-list", StringComparer.OrdinalIgnoreCase)
    )
    {
      await using var client = new CTraderOpenApiFeedClient(
        options,
        refreshTokenStore,
        notify,
        tokenEvents: tokenEvents
      );
      await client.ConnectAndAuthorizeAsync(CancellationToken.None);
      if (args.Contains("--account-list", StringComparer.OrdinalIgnoreCase))
      {
        var accounts = await client.GetGrantedDemoAccountsAsync(
          CancellationToken.None
        );
        foreach (var grantedAccount in accounts)
        {
          Console.WriteLine(
            $"account={grantedAccount.AccountId} demo={!grantedAccount.IsLive} "
            + $"broker={grantedAccount.BrokerName} type={grantedAccount.AccountType} "
            + $"scope={grantedAccount.PermissionScope} access={grantedAccount.AccessRights} "
            + $"balance={grantedAccount.Balance:N2}"
          );
        }
        return 0;
      }
      var symbol = await client.ResolveSymbolAsync(CancellationToken.None);
      var account = await client.GetTradingAccountAsync(CancellationToken.None);
      Console.WriteLine(
        $"account={account.AccountId} demo={!account.IsLive} "
        + $"broker={account.BrokerName} type={account.AccountType} "
        + $"scope={account.PermissionScope} access={account.AccessRights} "
        + $"balance={account.Balance:N2}"
      );
      Console.WriteLine(
        $"symbol={symbol.CTraderSymbol} id={symbol.SymbolId} "
        + $"digits={symbol.Digits} pipPosition={symbol.PipPosition} "
        + $"minVolume={symbol.MinVolume} stepVolume={symbol.StepVolume} "
        + $"maxVolume={symbol.MaxVolume} lotSize={symbol.LotSize}"
      );
      return 0;
    }
    var runner = new FeedRunner(
      options,
      () => new CTraderOpenApiFeedClient(
        options,
        refreshTokenStore,
        notify,
        tokenEvents: tokenEvents
      ),
      sink,
      new HealthFile(options.HeartbeatFile),
      autoTrade: autoTrade
    );

    using var cts = new CancellationTokenSource();
    Console.CancelKeyPress += (_, eventArgs) =>
    {
      eventArgs.Cancel = true;
      cts.Cancel();
    };
    AppDomain.CurrentDomain.ProcessExit += (_, _) => cts.Cancel();

    try
    {
      await runner.RunForeverAsync(cts.Token);
    }
    catch (OperationCanceledException) when (cts.IsCancellationRequested)
    {
      // Normal SIGINT/SIGTERM shutdown.
    }
    return 0;
  }
}

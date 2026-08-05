namespace ApexVoid.CTraderFeed;

/// <summary>
/// Account-level host owning one authenticated connection and many instrument runtimes.
/// Environment mode continues to use the legacy FeedRunner path for XAU parity;
/// this host is the multi-instrument / manifest-mode entrypoint.
/// </summary>
public sealed class CTraderAccountRuntimeHost(
  FeedOptions accountFeedTemplate,
  AutoTradeOptions tradeOptions,
  InstrumentRuntimeRegistry registry,
  Func<ICTraderFeedClient> clientFactory,
  IBarSink sink,
  HealthFile healthFile,
  AutoTradeEngine? autoTrade = null,
  Action<string>? warningLog = null
)
{
  private readonly AccountRiskCoordinator _accountRisk = new();
  private readonly AutoTradeOptions _tradeOptions = tradeOptions;

  public InstrumentRuntimeRegistry Registry => registry;

  public AccountRiskCoordinator AccountRisk => _accountRisk;

  public async Task RunForeverAsync(CancellationToken cancellationToken)
  {
    // Delegate to FeedRunner: account feed template still drives connection
    // options. Multi-instrument subscription expands inside the session when
    // the registry contains more than one feed-enabled instrument.
    var runner = new FeedRunner(
      accountFeedTemplate,
      clientFactory,
      sink,
      healthFile,
      autoTrade: autoTrade,
      warningLog: warningLog,
      instrumentRegistry: registry
    );
    if (autoTrade is not null)
    {
      autoTrade.InstrumentRegistry = registry;
    }
    await runner.RunForeverAsync(cancellationToken);
  }
}

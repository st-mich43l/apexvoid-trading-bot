using System.Text.Json;

namespace ApexVoid.CTraderFeed;

/// <summary>
/// Session shell around the TradePlan V8 runtime: it owns the broker session,
/// polls the plan stream, adopts plan-owned broker positions after a restart,
/// and executes owner commands. Every order - autonomous or owner-armed
/// manual /algo - is a TradePlan; there is no other execution contract.
/// </summary>
public sealed class AutoTradeEngine(
  AutoTradeOptions options,
  IAutoTradeStore store,
  Func<DateTimeOffset>? clock = null,
  Action<string>? log = null,
  Action? sessionHeartbeat = null
)
{
  // Owner broker controls (/trade_close, /trade_sl, /trade_close_auto,
  // /auto_close_all). A fixed constant matching Python's
  // manual_trade_command_stream default, deliberately not an options knob.
  private const string ManualCommandStream = "manual_trade:commands";
  private readonly SemaphoreSlim _gate = new(1, 1);
  private readonly Dictionary<string, SymbolInfo> _symbolsByCanonical =
    new(StringComparer.OrdinalIgnoreCase);
  private readonly HashSet<string> _reportedSessionErrors = [];
  private readonly HashSet<string> _reportedWarnings = [];
  private readonly object _reportLock = new();
  private readonly Func<DateTimeOffset> _clock = clock ?? (() => DateTimeOffset.UtcNow);
  private readonly Action<string> _log = log ?? Log;
  // Touched once per RunSessionAsync main-loop iteration: a stale value means
  // this loop specifically stopped making progress, distinct from the feed
  // heartbeat. See Program.cs's --healthcheck.
  private readonly Action _sessionHeartbeat = sessionHeartbeat ?? (() => { });
  private TradePlanRuntime? _tradePlanRuntime;
  private SpotPrice? _lastSpot;
  private readonly Dictionary<string, SpotPrice> _lastSpotBySymbol =
    new(StringComparer.OrdinalIgnoreCase);
  private ICTraderTradeClient? _client;
  private SymbolInfo? _symbol;
  private TradingAccountSnapshot? _account;
  private int _tradePlanConsumerFailures;
  private DateTimeOffset _tradePlanConsumerRetryAt = DateTimeOffset.MinValue;
  private volatile bool _ready;
  private volatile bool _disabled;

  /// <summary>
  /// Optional multi-instrument registry. When null, single-instrument mode
  /// (the session symbol is the only instrument).
  /// </summary>
  internal InstrumentRuntimeRegistry? InstrumentRegistry { get; set; }

  public bool Enabled => options.Enabled && !_disabled;

  private TradePlanRuntime TradePlans =>
    _tradePlanRuntime ??= new TradePlanRuntime(
      options,
      store,
      _clock,
      _log,
      resolveBoundSymbol: ResolveBoundSymbol,
      resolveUnits: ResolveInstrumentUnits,
      resolveOppositePolicy: ResolveOppositePolicy
    );

  private OppositePositionPolicy? ResolveOppositePolicy(string canonical) =>
    InstrumentRegistry is not null
    && InstrumentRegistry.TryGet(canonical, out var runtime)
      ? runtime.Execution.OppositePosition
      : null;

  private SymbolInfo? ResolveBoundSymbol(string canonical)
  {
    if (_symbolsByCanonical.TryGetValue(canonical, out var bound))
    {
      return bound;
    }
    if (
      InstrumentRegistry is not null
      && InstrumentRegistry.TryGet(canonical, out var runtime)
    )
    {
      return runtime.Symbol;
    }
    // Single-instrument mode: if the plan's own symbol matches the one bound
    // session symbol this is not a real mismatch. Otherwise fail rather than
    // trade one instrument's plan under another instrument's geometry.
    if (
      _symbol is { } sessionSymbol
      && string.Equals(sessionSymbol.RedisSymbol, canonical, StringComparison.OrdinalIgnoreCase)
    )
    {
      return sessionSymbol;
    }
    return null;
  }

  private string? RedisSymbolFor(long symbolId)
  {
    foreach (var item in _symbolsByCanonical.Values)
    {
      if (item.SymbolId == symbolId)
      {
        return item.RedisSymbol;
      }
    }
    return null;
  }

  private (decimal PipSize, decimal PipValuePerLot) ResolveInstrumentUnits(
    string canonical
  )
  {
    if (
      InstrumentRegistry is not null
      && InstrumentRegistry.TryGet(canonical, out var runtime)
    )
    {
      return (
        runtime.Execution.PipSize,
        runtime.Execution.EffectivePipValuePerLot
      );
    }
    return (options.PipSize, options.PipValuePerLot);
  }

  private IEnumerable<(SymbolInfo Symbol, SpotPrice? Quote)> TradePlanPollTargets(
    SymbolInfo sessionSymbol,
    SpotPrice? sessionSpot
  )
  {
    if (_symbolsByCanonical.Count == 0 || InstrumentRegistry is null)
    {
      yield return (sessionSymbol, sessionSpot);
      yield break;
    }
    var live = InstrumentRegistry.LiveInstruments();
    if (live.Count == 0)
    {
      yield return (sessionSymbol, sessionSpot);
      yield break;
    }
    foreach (var runtime in live)
    {
      SymbolInfo? bound = runtime.Symbol;
      if (bound is null
        && !_symbolsByCanonical.TryGetValue(runtime.Feed.RedisSymbol, out bound))
      {
        continue;
      }
      _lastSpotBySymbol.TryGetValue(bound.RedisSymbol, out var quote);
      yield return (bound, quote);
    }
  }

  public void LogUnitConfiguration(
    SymbolInfo symbol,
    Action<string> info,
    Action<string> warning
  )
  {
    var slice = options;
    if (
      InstrumentRegistry is not null
      && InstrumentRegistry.TryGet(symbol.RedisSymbol, out var runtime)
    )
    {
      slice = options with
      {
        PipSize = runtime.Execution.PipSize,
        PipValuePerLot = runtime.Execution.EffectivePipValuePerLot,
        ContractSize = runtime.Execution.ContractSize,
      };
    }
    var diagnostic = VolumePlanner.PipUnitDiagnostic(symbol, slice);
    if (diagnostic.Differs)
    {
      warning(diagnostic.Message);
      return;
    }
    info(diagnostic.Message);
  }

  public void BindInstrumentSymbols(IEnumerable<SymbolInfo> symbols)
  {
    _symbolsByCanonical.Clear();
    foreach (var symbol in symbols)
    {
      _symbolsByCanonical[symbol.RedisSymbol] = symbol;
    }
  }

  public async Task RunSessionAsync(
    ICTraderFeedClient feedClient,
    SymbolInfo symbol,
    CancellationToken cancellationToken
  )
  {
    if (!Enabled)
    {
      return;
    }
    AutoTradeReadinessStatus? sessionHealth = null;
    try
    {
      options.Validate();
      _client = feedClient as ICTraderTradeClient
        ?? throw new AutoTradeConfigurationException(
          "Auto trade disabled: configured cTrader client does not support "
          + "trade operations"
        );
      _symbol = symbol;
      var grants = await _client.GetAccountGrantsAsync(cancellationToken);
      await ReportLiveGrantsAsync(grants, cancellationToken);
      if (options.RequireDemoOnlyToken && grants.Any(item => item.IsLive))
      {
        var live = grants.First(item => item.IsLive);
        throw new AutoTradeConfigurationException(
          $"Auto trade disabled: token grants live account {live.AccountId}; "
          + "AUTO_TRADE_REQUIRE_DEMO_ONLY_TOKEN requires a demo-only token"
        );
      }
      var account = await _client.GetTradingAccountAsync(cancellationToken);
      _account = account;
      if (options.Profile == "demo_eval" && account.IsLive)
      {
        await PublishAsync(
          "config_fatal",
          $"demo_eval refuses live account {account.AccountId}",
          cancellationToken
        );
      }
      ValidateAccount(account);
      var configHealth = new AutoTradeReadinessStatus(
        "healthy",
        Array.Empty<string>(),
        Array.Empty<string>()
      );
      sessionHealth = configHealth;
      _log(VolumePlanner.SizingDiagnostic(account.Balance, options));
      try
      {
        TradePlanJson.AssertContractAvailable();
        _log("TradePlan JSON contract self-test passed");
      }
      catch (Exception exception)
      {
        _log(
          "TradePlan JSON contract self-test failed: "
          + $"{exception.GetType().Name}: {exception.Message}"
        );
        throw new AutoTradeConfigurationException(
          "Auto trade disabled: TradePlan JSON contract metadata is unavailable"
        );
      }
      await AdoptTradePlanPositionsAsync(cancellationToken);
      _ready = true;
      await PublishReadinessAsync(true, "ready", configHealth, cancellationToken);
      await PublishAsync(
        "ready",
        $"demo executor ready: {account.BrokerName} balance {account.Balance:N2}",
        cancellationToken
      );
      _log(
        $"auto-trade ready account={account.AccountId} broker={account.BrokerName} "
        + $"balance={account.Balance:N2} dryRun={options.DryRun} "
        + $"profile={options.Profile} config={configHealth.State} "
        + $"warnings=[{string.Join(',', configHealth.Warnings)}]"
      );

      var commandCursor = await store.GetCommandCursorAsync(cancellationToken);
      var nextReconcile = _clock();
      while (Enabled)
      {
        // Cancellation must always surface as OperationCanceledException so a
        // cancelled session never reports a clean shutdown.
        cancellationToken.ThrowIfCancellationRequested();
        // Reaching here proves the previous pass (adoption, owner commands,
        // TradePlan poll) completed without hanging.
        _sessionHeartbeat();
        if (_clock() >= nextReconcile)
        {
          await WithGateAsync(
            () => AdoptTradePlanPositionsAsync(cancellationToken),
            cancellationToken
          );
          nextReconcile = _clock().AddSeconds(5);
        }
        // Owner commands share this loop/gate so they never race the
        // TradePlan runtime's state mutations.
        var commandEntries = await store.ReadCandidatesAsync(
          ManualCommandStream,
          commandCursor,
          10,
          cancellationToken
        );
        foreach (var commandEntry in commandEntries)
        {
          await WithGateAsync(
            () => ProcessCommandEntryAsync(commandEntry, cancellationToken),
            cancellationToken
          );
          commandCursor = commandEntry.Id;
          await store.SetCommandCursorAsync(commandCursor, cancellationToken);
        }
        await WithGateAsync(
          () => PollTradePlansSafelyAsync(
            _client!, symbol, _lastSpot, cancellationToken
          ),
          cancellationToken
        );
        await Task.Delay(
          TimeSpan.FromMilliseconds(Math.Max(100, options.PollMilliseconds)),
          cancellationToken
        );
      }
    }
    finally
    {
      await WithGateAsync(
        () =>
        {
          _ready = false;
          _client = null;
          _symbol = null;
          _account = null;
          return Task.CompletedTask;
        },
        CancellationToken.None
      );
      if (sessionHealth is not null)
      {
        await PublishReadinessAsync(
          false,
          _disabled ? "fatal" : "stopped",
          sessionHealth,
          CancellationToken.None
        );
      }
    }
  }

  public async Task HandleSessionFaultAsync(
    Exception exception,
    CancellationToken cancellationToken
  )
  {
    if (exception is AutoTradeConfigurationException)
    {
      _disabled = true;
    }
    lock (_reportLock)
    {
      if (!_reportedSessionErrors.Add(exception.Message))
      {
        return;
      }
    }
    if (exception is AutoTradeConfigurationException)
    {
      _log(exception.Message);
    }
    else
    {
      _log(
        $"auto-trade session failed: {exception.GetType().Name}: {exception.Message}"
      );
    }
    await PublishAsync(
      exception is AutoTradeConfigurationException && options.Profile == "demo_eval"
        ? "config_fatal"
        : "service_error",
      exception.Message,
      cancellationToken
    );
    // Only a genuine config/contract incompatibility is fatal: it sets
    // _disabled above, which stops the retry loop for good. Anything else
    // (Redis/network/broker) is being retried, so it reads as degraded.
    var isConfigurationFault = exception is AutoTradeConfigurationException;
    var state = isConfigurationFault ? "fatal" : "degraded_retrying";
    var fatal = isConfigurationFault
      ? new[] { "service_initialization" }
      : Array.Empty<string>();
    var warnings = isConfigurationFault
      ? Array.Empty<string>()
      : new[] { "broker_or_redis_connection" };
    await PublishReadinessAsync(
      false,
      state,
      new AutoTradeReadinessStatus(state, fatal, warnings),
      cancellationToken
    );
  }

  public Task PublishOperationalEventAsync(
    string kind,
    string message,
    CancellationToken cancellationToken
  ) => PublishAsync(kind, message, cancellationToken);

  public Task ObserveSpotAsync(SpotPrice spot, CancellationToken cancellationToken)
  {
    _lastSpot = spot;
    _lastSpotBySymbol[spot.Symbol] = spot;
    return Task.CompletedTask;
  }

  private async Task PollTradePlansSafelyAsync(
    ICTraderTradeClient client,
    SymbolInfo symbol,
    SpotPrice? spot,
    CancellationToken cancellationToken
  )
  {
    if (_clock() < _tradePlanConsumerRetryAt)
    {
      return;
    }
    try
    {
      foreach (var (bound, quote) in TradePlanPollTargets(symbol, spot))
      {
        // Evaluation may submit/cancel broker orders. Keep the lazy snapshot
        // cycle scoped to one symbol so a mutation made while evaluating a
        // later symbol can never reconcile against an earlier symbol's
        // snapshot.
        var reconcileCycle = new AccountReconcileSnapshotCycle(client);
        await TradePlans.PollAsync(
          client,
          bound,
          quote,
          cancellationToken,
          reconcileCycle.GetAsync
        );
      }
      if (_tradePlanConsumerFailures > 0)
      {
        _log(
          "auto_trade_consumer_recovered "
          + $"attempts={_tradePlanConsumerFailures}"
        );
      }
      _tradePlanConsumerFailures = 0;
      _tradePlanConsumerRetryAt = DateTimeOffset.MinValue;
    }
    catch (OperationCanceledException)
    {
      throw;
    }
    catch (Exception exception)
    {
      _tradePlanConsumerFailures++;
      var delayMs = Math.Min(
        5_000,
        100 * (1 << Math.Min(5, _tradePlanConsumerFailures - 1))
      );
      _tradePlanConsumerRetryAt = _clock().AddMilliseconds(delayMs);
      _log(
        "auto_trade_consumer_restarting "
        + $"attempt={_tradePlanConsumerFailures} delay_ms={delayMs} "
        + $"exception={exception.GetType().Name} "
        + $"message={exception.Message}"
      );
    }
  }

  // Hands every broker position that carries TradePlan ownership to the
  // runtime so a leg whose state was lost (restart, Redis wipe) is re-adopted
  // instead of becoming an unmanaged orphan. Idempotent per position.
  private async Task AdoptTradePlanPositionsAsync(CancellationToken cancellationToken)
  {
    var client = RequireClient();
    var sessionSymbol = RequireSymbol();
    var snapshot = await client.ReconcileAccountAsync(cancellationToken);
    await PublishExecutorSnapshotsAsync(snapshot, sessionSymbol, cancellationToken);
    foreach (var position in snapshot.Positions)
    {
      if (
        position.Label != options.Label
        || !(
          TradePlanOwnership.IsTradePlanOwnershipComment(position.Comment)
          || TradePlanOwnership.TryParseOwnership(
            position.Comment, position.ClientOrderId
          ) is not null
        )
      )
      {
        continue;
      }
      var bound = RedisSymbolFor(position.SymbolId) is { } redis
        ? ResolveBoundSymbol(redis) ?? sessionSymbol
        : sessionSymbol;
      await TradePlans.TryAdoptBrokerPositionAsync(
        client, bound, position, cancellationToken
      );
    }
  }

  // Per-symbol operator view (/algo_status): the executor's own broker
  // positions and resting orders, its live plans, and the account equity.
  private async Task PublishExecutorSnapshotsAsync(
    TradingReconcileSnapshot snapshot,
    SymbolInfo sessionSymbol,
    CancellationToken cancellationToken
  )
  {
    var targets = _symbolsByCanonical.Values
      .Append(sessionSymbol)
      .GroupBy(item => item.SymbolId)
      .Select(group => group.First());
    foreach (var target in targets)
    {
      var positionIds = snapshot.Positions
        .Where(item => item.SymbolId == target.SymbolId && item.Label == options.Label)
        .Select(item => item.PositionId)
        .ToArray();
      var pendingIds = snapshot.PendingOrders
        .Where(item => item.SymbolId == target.SymbolId && item.Label == options.Label)
        .Select(item => item.OrderId)
        .ToArray();
      var groupIds = TradePlans.TrackedStates
        .Where(state =>
          state.Stage != TradePlanRuntimeStage.Closed
          && string.Equals(
            state.Symbol, target.RedisSymbol, StringComparison.OrdinalIgnoreCase
          )
        )
        .Select(state => state.PlanId)
        .Order(StringComparer.Ordinal)
        .ToArray();
      var balance = 0m;
      var equity = 0m;
      var equitySource = "";
      if (_account is { } account)
      {
        var resolution = EquityResolver.Resolve(
          account, positionIds.Length, pendingIds.Length
        );
        balance = resolution.AccountBalance;
        equity = resolution.Equity;
        equitySource = resolution.EquitySource;
      }
      await store.SetValueAsync(
        $"auto_trade:executor_snapshot:{target.RedisSymbol.ToUpperInvariant()}",
        JsonSerializer.Serialize(
          new AutoTradeExecutorSnapshot(
            target.RedisSymbol,
            options.Profile,
            Demo: _account is { IsLive: false },
            Ready: _ready,
            PositionIds: positionIds,
            PendingOrderIds: pendingIds,
            GroupIds: groupIds,
            UpdatedAt: _clock().ToUnixTimeSeconds(),
            AccountBalance: balance,
            AccountEquity: equity,
            AccountEquitySource: equitySource
          ),
          RedisJsonContext.Default.AutoTradeExecutorSnapshot
        ),
        cancellationToken
      );
    }
  }

  private async Task ProcessCommandEntryAsync(
    TradeStreamEntry entry,
    CancellationToken cancellationToken
  )
  {
    ManualTradeCommand? command;
    try
    {
      command = JsonSerializer.Deserialize(
        entry.Payload,
        RedisJsonContext.Default.ManualTradeCommand
      );
    }
    catch (JsonException exception)
    {
      _log($"auto-trade ignored malformed owner command {entry.Id}: {exception.Message}");
      return;
    }
    if (command is null || string.IsNullOrWhiteSpace(command.Type))
    {
      return;
    }
    try
    {
      var client = RequireClient();
      var session = _symbol;
      TradePlanRuntime.OwnerCommandResult? result = null;
      switch (command.Type)
      {
        case "close" when command.PositionId is long positionId:
          result = await TradePlans.OwnerClosePositionAsync(
            client, session, positionId, command.Frac, autonomousOnly: false,
            cancellationToken
          );
          break;
        case "close" when !string.IsNullOrWhiteSpace(command.IntentId):
          result = await TradePlans.OwnerClosePlanAsync(
            client, session, command.IntentId, command.Frac, cancellationToken
          );
          break;
        case "close_position" when command.PositionId is long autoPositionId:
          result = await TradePlans.OwnerClosePositionAsync(
            client, session, autoPositionId, fraction: null, autonomousOnly: true,
            cancellationToken
          );
          break;
        case "close_all":
          var (closed, cancelled) = await TradePlans.OwnerFlattenAsync(
            client, session, cancellationToken
          );
          await PublishAsync(
            "owner_flatten",
            $"owner flatten: closed {closed} position(s), cancelled {cancelled} pending",
            cancellationToken
          );
          return;
        case "move_sl" when command.PositionId is long slPositionId
          && command.Price is decimal price:
          result = await TradePlans.OwnerMoveStopAsync(
            client, session, slPositionId, price, cancellationToken
          );
          break;
        default:
          _log(
            $"auto-trade ignored unsupported owner command type {command.Type}"
          );
          return;
      }
      if (result?.Error is { } error)
      {
        _log($"auto-trade owner command {command.Type} refused: {error}");
        await PublishAsync(
          "manual_command_error",
          error,
          cancellationToken,
          candidateId: command.IntentId,
          positionId: command.PositionId
        );
      }
    }
    catch (OperationCanceledException)
    {
      throw;
    }
    catch (Exception exception)
    {
      _log($"auto-trade owner command {command.Type} failed: {exception.Message}");
      await PublishAsync(
        "manual_command_error",
        $"owner command {command.Type} failed: {exception.Message}",
        cancellationToken,
        candidateId: command.IntentId,
        positionId: command.PositionId
      );
    }
  }

  private void ValidateAccount(TradingAccountSnapshot account)
  {
    if (account.IsLive)
    {
      throw new AutoTradeConfigurationException(
        $"Auto trade disabled: hard lock refuses live account {account.AccountId}"
      );
    }
    if (
      !account.PermissionScope.Equals("ScopeTrade", StringComparison.OrdinalIgnoreCase)
      && !account.PermissionScope.Equals("Trading", StringComparison.OrdinalIgnoreCase)
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: cTrader token does not have trading scope"
      );
    }
    if (!account.AccessRights.Equals("FullAccess", StringComparison.OrdinalIgnoreCase))
    {
      throw new AutoTradeConfigurationException(
        $"Auto trade disabled: cTrader account access is {account.AccessRights}, "
        + "expected FullAccess"
      );
    }
    if (
      options.Profile == "conservative"
      && !account.AccountType.Equals("Hedged", StringComparison.OrdinalIgnoreCase)
    )
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: auto-trade requires a Hedged demo account, "
        + $"got {account.AccountType}"
      );
    }
    if (
      !string.IsNullOrWhiteSpace(options.ExpectedBroker)
      && !BrokerIdentity.Matches(account.BrokerName, options.ExpectedBroker)
    )
    {
      throw new AutoTradeConfigurationException(
        $"Auto trade disabled: broker {account.BrokerName} does not match "
        + options.ExpectedBroker
      );
    }
  }

  private Task PublishReadinessAsync(
    bool ready,
    string state,
    AutoTradeReadinessStatus health,
    CancellationToken cancellationToken
  ) => store.SetValueAsync(
    "auto_trade:executor_readiness",
    JsonSerializer.Serialize(
      new AutoTradeExecutorReadiness(
        ready,
        state,
        health.Fatal,
        health.Warnings,
        options.Profile,
        _clock().ToUnixTimeSeconds()
      ),
      RedisJsonContext.Default.AutoTradeExecutorReadiness
    ),
    cancellationToken
  );

  private async Task ReportLiveGrantsAsync(
    IReadOnlyList<TradingAccountGrant> grants,
    CancellationToken cancellationToken
  )
  {
    foreach (var grant in grants.Where(item => item.IsLive))
    {
      var message = $"token grants live account {grant.AccountId} — "
        + "re-authorize with the demo account only";
      lock (_reportLock)
      {
        if (!_reportedWarnings.Add(message))
        {
          continue;
        }
      }
      _log(message);
      await PublishAsync("warning", message, cancellationToken);
    }
  }

  // Operational/owner-command event (not a plan lifecycle event: those are
  // published by TradePlanRuntime with full plan provenance).
  private Task PublishAsync(
    string type,
    string message,
    CancellationToken cancellationToken,
    string? candidateId = null,
    long? positionId = null
  ) => store.PublishAutoTradeEventAsync(
    options.EventStream,
    new AutoTradeEvent(
      type,
      _clock().ToUnixTimeSeconds(),
      message,
      _symbol?.RedisSymbol ?? options.CanonicalSymbol,
      CandidateId: candidateId,
      PositionId: positionId,
      ConfigurationProfile: options.Profile,
      AccountType: _account?.AccountType,
      Broker: _account?.BrokerName,
      CorrelationId: candidateId ?? Guid.NewGuid().ToString("N")
    ),
    cancellationToken
  );

  private async Task WithGateAsync(
    Func<Task> action,
    CancellationToken cancellationToken
  )
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      await action();
    }
    finally
    {
      _gate.Release();
    }
  }

  private ICTraderTradeClient RequireClient() => _client
    ?? throw new InvalidOperationException("auto-trade session is not connected");

  private SymbolInfo RequireSymbol() => _symbol
    ?? throw new InvalidOperationException("auto-trade symbol is not resolved");

  private static void Log(string message) =>
    Console.Error.WriteLine($"ctrader-feed {message}");
}

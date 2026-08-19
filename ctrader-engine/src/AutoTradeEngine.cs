using System.Globalization;
using System.Text.Json;

namespace ApexVoid.CTraderFeed;

public sealed class AutoTradeEngine(
  AutoTradeOptions options,
  IAutoTradeStore store,
  Func<DateTimeOffset>? clock = null,
  Action<string>? log = null
)
{
  // Owner-override commands for algo-armed/filled manual signals
  // (cancel_pending/close/move_sl). Not wired through AutoTradeOptions -
  // this stream name is a fixed constant matching Python's
  // settings.manual_trade_command_stream default, kept out of the
  // per-environment options surface deliberately (this feature is driven
  // by per-candidate/per-command data, not new global tuning knobs).
  private const string ManualCommandStream = "manual_trade:commands";
  private readonly SemaphoreSlim _gate = new(1, 1);
  private readonly Dictionary<long, AutoTradePositionState> _states = [];
  private readonly Dictionary<string, StructuralRouteIdentity> _routeIdentityByCandidate = [];
  private readonly Dictionary<string, SymbolInfo> _symbolsByCanonical =
    new(StringComparer.OrdinalIgnoreCase);
  private readonly HashSet<string> _reportedErrors = [];
  private readonly HashSet<string> _reportedSessionErrors = [];
  private readonly HashSet<string> _reportedWarnings = [];
  private readonly object _reportLock = new();
  private readonly Func<DateTimeOffset> _clock = clock ?? (() => DateTimeOffset.UtcNow);
  private readonly Action<string> _log = log ?? Log;
  // TradePlan V7 broker-execution runtime (docs/adr-trade-plan-v7-boundary.md) -
  // composed into this engine's own session loop (see PollTradePlansAsync)
  // rather than given a separate RunSessionAsync/reconcile/heartbeat of its
  // own, so it shares this engine's readiness/gate machinery instead of
  // duplicating it. Deliberately a distinct class in its own source file so
  // TradePlanExecutionEngineDependencyTests can scan every V7 file for
  // forbidden analysis/route/stop symbols without tripping over this file's
  // legitimate V6 use of them elsewhere.
  private TradePlanRuntime? _tradePlanRuntime;
  private long _spotSequence;
  private SpotPrice? _lastSpot;
  private readonly Dictionary<string, SpotPrice> _lastSpotBySymbol =
    new(StringComparer.OrdinalIgnoreCase);
  private ICTraderTradeClient? _client;
  private SymbolInfo? _symbol;
  private IReadOnlyList<TradingPosition> _allSymbolPositions = [];
  private IReadOnlyList<TradingPendingOrder> _allSymbolPendingOrders = [];
  private TradingAccountSnapshot? _account;
  private bool _accountSupportsHedging;
  private int _tradePlanConsumerFailures;
  private DateTimeOffset _tradePlanConsumerRetryAt = DateTimeOffset.MinValue;
  private volatile bool _ready;
  private volatile bool _disabled;
  // Set when the broker returns an already-tracked PositionId for what
  // should be a distinct independent group (see the PlaceTrancheAsync
  // conflict check). Unlike _disabled, this only blocks new autonomous
  // initial submissions - existing tracked positions (including both sides
  // of the conflict) keep being reconciled and managed, since neither can
  // be safely assumed closed.
  private volatile bool _positionIdentityConflict;
  private CandidateExecutionLease? _activeLease;
  private CandidateLeaseHeartbeat? _heartbeat;
  /// <summary>
  /// Optional multi-instrument registry. When null, XAU-only legacy behaviour.
  /// Shared stream is still consumed once; dispatch is by candidate.symbol.
  /// </summary>
  internal InstrumentRuntimeRegistry? InstrumentRegistry { get; set; }
  private static readonly TimeSpan CandidateLeaseDuration =
    CandidateLeaseDefaults.LeaseWindow;
  private static readonly TimeSpan CandidateHeartbeatInterval =
    CandidateLeaseDefaults.HeartbeatInterval;
  // Test seam so heartbeat behaviour is provable without wall-clock waits.
  internal Func<TimeSpan, CancellationToken, Task>? HeartbeatDelay { get; set; }
  // Test seam for the delay between absence-confirmation broker snapshots.
  internal Func<TimeSpan, CancellationToken, Task>? RecoveryDelay { get; set; }

  internal enum ExecutionRoute
  {
    Market,
    SingleLimit,
    ZoneSplit,
    ManualLimit,
  }

  // Exposed so the shared Python/C# route fixture can be asserted directly.
  internal static bool RouteInContract(
    string declaredRoute,
    ExecutionRoute resolved
  ) => RouteSatisfiesContract(declaredRoute, resolved);

  private sealed record ExecutionRouteResolution(
    ExecutionRoute Route,
    decimal PlannedEntryPrice,
    string? RoutingReason,
    string? RejectReason
  );

  private sealed record StructuralRouteIdentity(
    string? StructuralSource,
    string? ZoneId,
    string? StructuralZoneId,
    string? ReactionId,
    string? ThesisId
  );

  public bool Enabled => options.Enabled && !_disabled;

  private TradePlanRuntime TradePlans =>
    _tradePlanRuntime ??= new TradePlanRuntime(
      options,
      store,
      _clock,
      _log,
      resolveBoundSymbol: ResolveBoundSymbol,
      resolveUnits: ResolveInstrumentUnits
    );

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

  private decimal PipSizeForSymbol(string symbol)
  {
    return ResolveInstrumentUnits(symbol).PipSize;
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
    AutoTradeConfigHealthResult? sessionHealth = null;
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
      _accountSupportsHedging = account.AccountType.Equals(
        "Hedged",
        StringComparison.OrdinalIgnoreCase
      );
      var configHealth = await PublishConfigurationAsync(
        account,
        symbol,
        cancellationToken
      );
      sessionHealth = configHealth;
      if (configHealth.State == "fatal")
      {
        await PublishReadinessAsync(
          false,
          "fatal",
          configHealth,
          cancellationToken
        );
        throw new AutoTradeConfigurationException(
          "Auto trade disabled: Python/C# configuration mismatch: "
          + string.Join(", ", configHealth.Fatal)
        );
      }
      _log(VolumePlanner.SizingDiagnostic(account.Balance, options));
      try
      {
        TradePlanJson.AssertContractAvailable();
        _log("V7 JSON contract self-test passed");
      }
      catch (Exception exception)
      {
        _log(
          "V7 JSON contract self-test failed: "
          + $"{exception.GetType().Name}: {exception.Message}"
        );
        throw new AutoTradeConfigurationException(
          "Auto trade disabled: V7 JSON contract metadata is unavailable"
        );
      }
      await ReconcileAsync(cancellationToken);
      _ready = true;
      await PublishReadinessAsync(
        true,
        "ready",
        configHealth,
        cancellationToken
      );
      await PublishAsync(
        "ready",
        $"demo executor ready: {account.BrokerName} balance {account.Balance:N2}",
        cancellationToken
      );
      _log(
        $"auto-trade ready account={account.AccountId} broker={account.BrokerName} "
        + $"balance={account.Balance:N2} dryRun={options.DryRun} "
        + $"profile={options.Profile} exposure={EffectiveExposurePolicy()} "
        + $"twoSided={options.RangeTwoSidedEnabled} flip={options.RangeFlipEnabled} "
        + $"multiMatch={options.MultiMatchEnabled} config={configHealth.State} "
        + $"warnings=[{string.Join(',', configHealth.Warnings)}]"
      );
      await PublishAsync(
        "account_capability",
        _accountSupportsHedging
          ? "demo account supports hedged two-sided XAU execution"
          : "demo account is non-hedged; opposite routing policy "
            + options.NonHedgedOppositePolicy,
        cancellationToken
      );

      var cursor = await store.GetCursorAsync(cancellationToken);
      var commandCursor = await store.GetCommandCursorAsync(cancellationToken);
      var nextReconcile = _clock();
      while (Enabled)
      {
        // Cancellation must always surface as OperationCanceledException, even
        // when it lands while a candidate is mid-flight. Exiting the loop
        // silently would report a cancelled session as a clean shutdown.
        cancellationToken.ThrowIfCancellationRequested();
        if (_clock() >= nextReconcile)
        {
          await WithGateAsync(
            () => ReconcileAsync(cancellationToken),
            cancellationToken
          );
          nextReconcile = _clock().AddSeconds(15);
        }
        // Owner-override commands (/trade_close, /trade_sl, /trade_cancel on
        // an algo-armed/filled signal) share this loop/gate/thread rather
        // than a second poll loop, so they never race _states mutations
        // from ObserveSpotAsync's ProcessTargetsAsync.
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
        if (options.ContractMode != "legacy_v6")
        {
          await WithGateAsync(
            () => PollTradePlansSafelyAsync(
              _client!, symbol, _lastSpot, cancellationToken
            ),
            cancellationToken
          );
        }
        var entries = await store.ReadCandidatesAsync(
          options.CandidateStream,
          cursor,
          10,
          cancellationToken
        );
        if (entries.Count == 0)
        {
          await Task.Delay(
            TimeSpan.FromMilliseconds(Math.Max(100, options.PollMilliseconds)),
            cancellationToken
          );
          continue;
        }
        foreach (var entry in entries)
        {
          var advance = await ProcessEntryAsync(entry, cancellationToken);
          if (!advance)
          {
            await Task.Delay(
              TimeSpan.FromMilliseconds(Math.Max(100, options.PollMilliseconds)),
              cancellationToken
            );
            break;
          }
          cursor = entry.Id;
          await store.SetCursorAsync(cursor, cancellationToken);
        }
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
          _accountSupportsHedging = false;
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
    // Session-level failure with no candidate owner: telemetry only, so it can
    // never create or terminalise a candidate lifecycle state.
    await PublishAsync(
      exception is AutoTradeConfigurationException && options.Profile == "demo_eval"
        ? "config_fatal"
        : "service_error",
      exception.Message,
      cancellationToken
    );
    // P1-6: only a genuine config/contract incompatibility is fatal - it
    // sets _disabled above, which stops FeedRunner.RunAutoTradeSafelyAsync's
    // retry loop for good. Any other exception here (Redis/network/broker)
    // is actively being retried by that same loop - publishing "fatal" for
    // it would be a lie the moment the very next retry succeeds, and
    // /auto_status would keep reporting a dead executor that is, in fact,
    // about to recover. Everything else reads as degraded_retrying.
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
      new AutoTradeConfigHealthResult(state, fatal, warnings),
      cancellationToken
    );
  }

  public Task PublishOperationalEventAsync(
    string kind,
    string message,
    CancellationToken cancellationToken
  ) => PublishAsync(kind, message, cancellationToken);

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
        await TradePlans.PollAsync(
          client, bound, quote, cancellationToken
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

  public async Task ObserveSpotAsync(
    SpotPrice spot,
    CancellationToken cancellationToken
  )
  {
    _spotSequence++;
    _lastSpot = spot;
    _lastSpotBySymbol[spot.Symbol] = spot;
    if (!_ready || options.DryRun || !Enabled)
    {
      return;
    }
    await WithGateAsync(
      () => (
        !_ready || _client is null || _symbol is null
          ? Task.CompletedTask
          : ProcessTargetsAsync(spot, cancellationToken)
      ),
      cancellationToken
    );
  }

  private async Task<bool> ProcessEntryAsync(
    TradeStreamEntry entry,
    CancellationToken cancellationToken
  )
  {
    TradeCandidate? candidate;
    try
    {
      candidate = JsonSerializer.Deserialize(
        entry.Payload,
        RedisJsonContext.Default.TradeCandidate
      );
    }
    catch (JsonException exception)
    {
      _log($"auto-trade ignored malformed candidate {entry.Id}: {exception.Message}");
      return true;
    }
    if (candidate is null || string.IsNullOrWhiteSpace(candidate.CandidateId))
    {
      return true;
    }
    RememberRouteIdentity(candidate);
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "executor_received",
      cancellationToken
    );
    await PublishAsync(
      "executor_received",
      $"executor received candidate {Short(candidate.CandidateId)}",
      cancellationToken,
      candidate.CandidateId,
      groupId: CandidateGroupId(candidate),
      setup: candidate.Setup,
      direction: candidate.Direction,
      matchId: candidate.MatchId,
      rangeId: candidate.RangeId,
      strategyFamily: candidate.StrategyFamily,
      structuralSource: candidate.StructuralSource,
      zoneId: candidate.ZoneId,
      structuralZoneId: candidate.StructuralZoneId,
      reactionId: candidate.ReactionId,
      thesisId: candidate.ThesisId
    );
    var claim = await store.TryClaimCandidateAsync(
      candidate.CandidateId,
      entry.Id,
      CandidateLeaseDuration,
      cancellationToken
    );
    if (claim.Lease is not CandidateExecutionLease lease)
    {
      return await HandleUnclaimedCandidateAsync(
        candidate,
        entry,
        claim,
        cancellationToken
      );
    }
    if (claim.Record?.Attempt > 1)
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "candidate_retry_reclaimed",
        cancellationToken
      );
    }
    _activeLease = lease;
    await using var heartbeat = CandidateLeaseHeartbeat.Start(
      store,
      lease,
      CandidateLeaseDuration,
      cancellationToken,
      CandidateHeartbeatInterval,
      HeartbeatDelay,
      (metric, token) => store.IncrementMetricAsync(
        candidate.Symbol,
        metric,
        token
      ),
      _log
    );
    _heartbeat = heartbeat;
    try
    {
      var advance = await WithGateAsync(
        () => ProcessCandidateAsync(candidate, cancellationToken),
        cancellationToken
      );
      if (advance)
      {
        _reportedErrors.Remove(candidate.CandidateId);
      }
      return advance;
    }
    catch (CandidateLeaseLostException)
    {
      // The successor (or a recovery run) owns this candidate now. A stale
      // executor must not complete, reject or release it.
      await PublishOwnershipLossAsync(candidate, cancellationToken);
      return false;
    }
    catch (BrokerOutcomeUnknownException exception)
    {
      return await HandleBrokerOutcomeUnknownAsync(
        candidate,
        exception,
        cancellationToken
      );
    }
    catch (CandidateIntegrityException exception)
    {
      await PublishCandidateIntegrityErrorAsync(
        candidate,
        exception.Reason,
        cancellationToken
      );
      return false;
    }
    catch (AutoTradeConfigurationException)
    {
      // Configuration is fatal for the session. Only hand the candidate back
      // when it is still safely pre-submit and we still own the lease.
      await ReleaseRetryableAsync(
        candidate,
        "config_fatal",
        cancellationToken
      );
      throw;
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      // No broker side effect can reach this handler: every broker call
      // classifies its own failure and throws BrokerOutcomeUnknownException
      // when acceptance is not authoritative. This is therefore a safe
      // pre-submit error and the candidate stays retryable.
      await ReleaseRetryableAsync(
        candidate,
        exception.GetType().Name,
        cancellationToken
      );
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "lifecycle_error",
        cancellationToken
      );
      if (_reportedErrors.Add(candidate.CandidateId))
      {
        await PublishAsync(
          "candidate_retryable_error",
          $"candidate {Short(candidate.CandidateId)} failed: {exception.Message}",
          cancellationToken,
          candidate.CandidateId
        );
        _log(
          $"auto-trade candidate {Short(candidate.CandidateId)} failed: "
          + $"{exception.GetType().Name}: {exception.Message}"
        );
      }
      return false;
    }
    finally
    {
      _activeLease = null;
      _heartbeat = null;
    }
  }

  // Cursor advancement is decided by the typed claim disposition, never by a
  // status string. Only a proven-terminal candidate may advance.
  private async Task<bool> HandleUnclaimedCandidateAsync(
    TradeCandidate candidate,
    TradeStreamEntry entry,
    CandidateClaimResult claim,
    CancellationToken cancellationToken
  )
  {
    switch (claim.Disposition)
    {
      case CandidateClaimDisposition.Terminal:
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "candidate_terminal_cursor_advanced",
          cancellationToken
        );
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "duplicate_suppressed",
          cancellationToken
        );
        return true;
      case CandidateClaimDisposition.ActiveElsewhere:
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "duplicate_suppressed",
          cancellationToken
        );
        return false;
      case CandidateClaimDisposition.RecoveryRequired:
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "candidate_recovery_required",
          cancellationToken
        );
        return await ReconcileBrokerOutcomeAsync(
          candidate,
          entry,
          cancellationToken
        );
      default:
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "candidate_state_conflict",
          cancellationToken
        );
        await PublishCandidateIntegrityErrorAsync(
          candidate,
          "candidate_execution_state_conflict",
          cancellationToken
        );
        return false;
    }
  }

  // Dispatches one manual_trade:commands entry to the real broker. Unlike
  // ProcessEntryAsync there is no SETNX candidate-claim idempotency here -
  // each owner command is a one-shot fire-and-forget stream entry with no
  // republish-on-crash semantics on the Python side, so failures are
  // logged/published and the cursor still advances rather than retrying
  // forever.
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
      _log($"auto-trade ignored malformed manual command {entry.Id}: {exception.Message}");
      return;
    }
    if (command is null || string.IsNullOrWhiteSpace(command.Type))
    {
      return;
    }
    try
    {
      switch (command.Type)
      {
        case "cancel_pending":
          await HandleCancelPendingCommandAsync(command, cancellationToken);
          break;
        case "close":
          await HandleCloseCommandAsync(command, cancellationToken);
          break;
        case "close_all":
          await HandleCloseAllCommandAsync(cancellationToken);
          break;
        case "move_sl":
          await HandleMoveSlCommandAsync(command, cancellationToken);
          break;
        default:
          _log($"auto-trade ignored unknown manual command type {command.Type}");
          break;
      }
    }
    catch (OperationCanceledException)
    {
      throw;
    }
    catch (Exception exception)
    {
      _log($"auto-trade manual command {command.Type} failed: {exception.Message}");
      await PublishAsync(
        "manual_command_error",
        $"manual command {command.Type} failed: {exception.Message}",
        cancellationToken,
        candidateId: command.IntentId,
        positionId: command.PositionId
      );
    }
  }

  // /trade_cancel on an armed (not yet filled) manual algo signal: find the
  // still-resting limit order by its candidate token (the same
  // Contains(CandidateToken(...)) matching every other candidate type
  // already uses) and cancel it for real.
  private async Task HandleCancelPendingCommandAsync(
    ManualTradeCommand command,
    CancellationToken cancellationToken
  )
  {
    if (string.IsNullOrWhiteSpace(command.IntentId))
    {
      _log("auto-trade cancel_pending command missing intent_id");
      return;
    }
    var client = RequireClient();
    var pendingOrders = await client.ReconcilePendingOrdersAsync(cancellationToken);
    var token = CandidateToken(command.IntentId);
    var target = pendingOrders.FirstOrDefault(order =>
      order.Label == options.Label
      && order.Comment.Contains(token, StringComparison.Ordinal)
    );
    if (target is null)
    {
      _log($"auto-trade cancel_pending: no matching pending order for {token}");
      await PublishAsync(
        "manual_command_error",
        $"cancel requested but no pending order found for {token}",
        cancellationToken,
        candidateId: command.IntentId
      );
      return;
    }
    await client.CancelPendingOrderAsync(target.OrderId, cancellationToken);
    _allSymbolPendingOrders = _allSymbolPendingOrders
      .Where(item => item.OrderId != target.OrderId)
      .ToArray();
    var cancelledGroupId = ParseManualExpiry(target.Comment)?.GroupId
      ?? ParseZoneComment(target.Comment)?.GroupId;
    await MaybeDeleteGroupPlanAsync(cancelledGroupId, cancellationToken);
    await PublishAsync(
      "manual_cancelled",
      $"manual algo limit {target.OrderId} cancelled by owner",
      cancellationToken,
      candidateId: command.IntentId
    );
  }

  // /trade_close on a filled manual algo signal: close the real position
  // (full or partial by Frac) at the REAL broker fill. Full closes must
  // drop tracked state immediately so the next reconcile does not re-book
  // the same exit using a stop-loss estimate (wrong Total net / duplicate
  // POSITION CLOSED cards).
  private async Task HandleCloseCommandAsync(
    ManualTradeCommand command,
    CancellationToken cancellationToken
  )
  {
    if (command.PositionId is not long positionId)
    {
      _log("auto-trade close command missing position_id");
      return;
    }
    var client = RequireClient();
    var state = _states.GetValueOrDefault(positionId)
      ?? await store.GetPositionAsync(positionId, cancellationToken);
    var remaining = state?.RemainingVolume;
    if (remaining is null)
    {
      var positions = await client.ReconcilePositionsAsync(cancellationToken);
      remaining = positions.FirstOrDefault(item => item.PositionId == positionId)?.Volume;
    }
    if (remaining is not long remainingVolume || remainingVolume <= 0)
    {
      _log($"auto-trade close command: position {positionId} not found");
      await PublishAsync(
        "manual_command_error",
        $"close requested but position {positionId} is not open",
        cancellationToken,
        candidateId: command.IntentId,
        positionId: positionId
      );
      return;
    }
    var volume = command.Frac is decimal frac && frac > 0 && frac < 1
      ? Math.Clamp(
        decimal.ToInt64(decimal.Floor(remainingVolume * frac)),
        1,
        remainingVolume
      )
      : remainingVolume;
    var execution = await client.ClosePositionAsync(positionId, volume, cancellationToken);
    if (state is not null)
    {
      await ApplyOwnerCloseAsync(
        state,
        execution,
        eventType: "manual_closed",
        message: $"manual algo position {positionId} closed by owner",
        candidateId: command.IntentId,
        cancellationToken
      );
      return;
    }
    var remainingAfter = execution.RemainingVolume
      ?? Math.Max(0, remainingVolume - execution.ExecutedVolume);
    await PublishAsync(
      "manual_closed",
      $"manual algo position {positionId} closed by owner",
      cancellationToken,
      candidateId: command.IntentId,
      positionId: positionId,
      volume: execution.ExecutedVolume,
      price: execution.ExecutionPrice,
      remainingVolume: remainingAfter
    );
  }

  // /auto_close_all: market-close every tracked ApexVoid Algo position and
  // cancel resting labeled limits. Net pips use the broker close fill, not
  // a stop estimate.
  private async Task HandleCloseAllCommandAsync(CancellationToken cancellationToken)
  {
    var client = RequireClient();
    await ReconcileAsync(cancellationToken);
    var openStates = _states.Values.ToArray();
    var pending = _allSymbolPendingOrders
      .Where(order => order.Label == options.Label)
      .ToArray();
    await PublishAsync(
      "owner_flatten",
      $"owner flatten: closing {openStates.Length} position(s), "
        + $"cancelling {pending.Length} pending",
      cancellationToken
    );
    foreach (var state in openStates)
    {
      if (state.RemainingVolume <= 0)
      {
        continue;
      }
      var execution = await client.ClosePositionAsync(
        state.PositionId,
        state.RemainingVolume,
        cancellationToken
      );
      await ApplyOwnerCloseAsync(
        state,
        execution,
        eventType: "position_closed",
        message: "position closed by owner flatten",
        candidateId: state.CandidateId,
        cancellationToken
      );
    }
    foreach (var order in pending)
    {
      await client.CancelPendingOrderAsync(order.OrderId, cancellationToken);
      _allSymbolPendingOrders = _allSymbolPendingOrders
        .Where(item => item.OrderId != order.OrderId)
        .ToArray();
      var manual = ParseManualExpiry(order.Comment);
      var pendingGroupId = manual?.GroupId
        ?? ParseZoneComment(order.Comment)?.GroupId;
      await MaybeDeleteGroupPlanAsync(
        pendingGroupId,
        cancellationToken
      );
      await PublishAsync(
        "manual_cancelled",
        $"pending order {order.OrderId} cancelled by owner flatten",
        cancellationToken,
        candidateId: manual?.CandidateToken,
        groupId: manual?.GroupId
      );
    }
    await PublishAsync(
      "owner_flatten",
      "owner flatten complete",
      cancellationToken
    );
  }

  private async Task ApplyOwnerCloseAsync(
    AutoTradePositionState state,
    TradeExecution execution,
    string eventType,
    string message,
    string? candidateId,
    CancellationToken cancellationToken
  )
  {
    var symbol = RequireSymbol();
    var closeVolume = execution.ExecutedVolume > 0
      ? execution.ExecutedVolume
      : state.RemainingVolume;
    var remainingAfter = execution.RemainingVolume
      ?? Math.Max(0, state.RemainingVolume - closeVolume);
    var fill = execution.ExecutionPrice > 0
      ? execution.ExecutionPrice
      : state.EntryPrice;
    var realizedPips = SignedPips(state, fill);
    var currentGroup = _states.Values
      .Where(item => GroupId(item) == GroupId(state))
      .ToArray();
    if (currentGroup.Length == 0)
    {
      currentGroup = [state];
    }
    var groupPipVolume = GroupRealizedPipVolume(currentGroup)
      + realizedPips * closeVolume;
    var groupInitialVolume = GroupInitialVolume(currentGroup);
    if (groupInitialVolume <= 0)
    {
      groupInitialVolume = state.GroupInitialVolume > 0
        ? state.GroupInitialVolume
        : state.InitialVolume;
    }
    var weightedGroupPips = WeightedPips(groupPipVolume, groupInitialVolume);
    var groupId = GroupId(state);
    if (remainingAfter > 0)
    {
      state = state with
      {
        RemainingVolume = remainingAfter,
        GroupRealizedPipVolume = groupPipVolume,
        GroupInitialVolume = groupInitialVolume,
      };
      _states[state.PositionId] = state;
      await store.SavePositionAsync(state, cancellationToken);
      await PublishAsync(
        eventType,
        message,
        cancellationToken,
        candidateId: candidateId ?? state.CandidateId,
        positionId: state.PositionId,
        volume: closeVolume,
        price: fill,
        groupId: groupId,
        setup: state.Setup,
        regime: state.Regime,
        confluence: state.Confluence,
        groupRealizedPips: weightedGroupPips,
        stopPips: InitialStopPips(state),
        stream: ExecutionStream(state),
        direction: DirectionLabel(state.Direction),
        remainingVolume: remainingAfter,
        matchId: state.MatchId,
        rangeId: state.RangeId,
        strategyFamily: state.StrategyFamily,
        legRealizedPips: realizedPips,
        groupInitialVolume: groupInitialVolume,
        lotSize: symbol.LotSize
      );
      return;
    }
    _states.Remove(state.PositionId);
    await store.DeletePositionAsync(state.PositionId, cancellationToken);
    await PublishAsync(
      eventType,
      message,
      cancellationToken,
      candidateId: candidateId ?? state.CandidateId,
      positionId: state.PositionId,
      volume: closeVolume,
      price: fill,
      groupId: groupId,
      setup: state.Setup,
      regime: state.Regime,
      confluence: state.Confluence,
      groupRealizedPips: weightedGroupPips,
      stopPips: InitialStopPips(state),
      stream: ExecutionStream(state),
      direction: DirectionLabel(state.Direction),
      remainingVolume: 0,
      matchId: state.MatchId,
      rangeId: state.RangeId,
      strategyFamily: state.StrategyFamily,
      legRealizedPips: realizedPips,
      groupInitialVolume: groupInitialVolume,
      lotSize: symbol.LotSize
    );
    if (!_states.Values.Any(item => GroupId(item) == groupId))
    {
      await PublishAsync(
        "group_result",
        $"group {groupId} realised {weightedGroupPips.ToString("0.0", CultureInfo.InvariantCulture)} pips",
        cancellationToken,
        candidateId: candidateId ?? state.CandidateId,
        positionId: state.PositionId,
        groupId: groupId,
        groupRealizedPips: weightedGroupPips,
        setup: state.Setup,
        matchId: state.MatchId,
        rangeId: state.RangeId,
        strategyFamily: state.StrategyFamily,
        regime: state.Regime,
        confluence: state.Confluence,
        stopPips: InitialStopPips(state),
        stream: ExecutionStream(state),
        direction: DirectionLabel(state.Direction),
        groupInitialVolume: groupInitialVolume,
        lotSize: symbol.LotSize
      );
      await MaybeDeleteGroupPlanAsync(groupId, cancellationToken);
    }
  }

  // /trade_sl on a filled manual algo signal: amend the real position's
  // stop loss. The existing trailing-stop technique (StopTrailPlanner via
  // ProcessTargetsAsync) is untouched and keeps running afterwards.
  private async Task HandleMoveSlCommandAsync(
    ManualTradeCommand command,
    CancellationToken cancellationToken
  )
  {
    if (command.PositionId is not long positionId || command.Price is not decimal price)
    {
      _log("auto-trade move_sl command missing position_id or price");
      return;
    }
    await RequireClient().AmendPositionStopLossAsync(positionId, price, cancellationToken);
    if (_states.TryGetValue(positionId, out var state))
    {
      state = state with { CurrentStopLoss = price };
      _states[positionId] = state;
      await store.SavePositionAsync(state, cancellationToken);
    }
    await PublishAsync(
      "manual_sl_moved",
      $"manual algo position {positionId} stop moved to {price:N2} by owner",
      cancellationToken,
      candidateId: command.IntentId,
      positionId: positionId,
      price: price
    );
  }

  private async Task<bool> ProcessCandidateAsync(
    TradeCandidate candidate,
    CancellationToken cancellationToken
  )
  {
    var client = RequireClient();
    var symbol = RequireSymbol();
    if (InstrumentRegistry is not null)
    {
      var paperCandidate = string.Equals(
        candidate.Mode,
        "paper",
        StringComparison.OrdinalIgnoreCase
      );
      var instrumentRoute = CandidateInstrumentDispatcher.Route(
        InstrumentRegistry,
        candidate.Symbol,
        paperCandidate
      );
      if (
        !instrumentRoute.IsAccepted
        || instrumentRoute.Outcome == CandidateRouteOutcome.AcceptPaper
      )
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "candidate_route_rejected",
          cancellationToken
        );
        return await RejectAsync(
          candidate,
          instrumentRoute.Outcome == CandidateRouteOutcome.AcceptPaper
            ? "paper_instrument_no_broker_placement"
            : instrumentRoute.Reason,
          cancellationToken
        );
      }
      if (
        instrumentRoute.Runtime?.Symbol is not null
        && _symbolsByCanonical.Count > 0
      )
      {
        symbol = instrumentRoute.Runtime.Symbol;
      }
      else if (
        _symbolsByCanonical.TryGetValue(candidate.Symbol, out var routedSymbol)
      )
      {
        symbol = routedSymbol;
      }
    }
    var now = _clock().ToUnixTimeSeconds();
    var legacyRangeScalp = candidate.Version is 1 or 2 && string.Equals(
        candidate.Timeframe,
        "M1",
        StringComparison.OrdinalIgnoreCase
      )
      && candidate.Setup == "Auto Range Scalp"
      && candidate.Mode == "auto_range_scalp";
    var boxRangeScalp = IsBoxRangeScalp(candidate);
    var trendCandidate = IsTrendCandidate(candidate);
    var strategyMatchCandidate = IsStrategyMatchCandidate(candidate);
    var manualAlgoCandidate = IsManualAlgoCandidate(candidate);
    if (options.ContractMode is "v7_only" or "v8_only" && !manualAlgoCandidate)
    {
      // TradePlan is the sole autonomous order path in this mode - reject any
      // new V6 autonomous candidate before planning or broker calls, as
      // defense-in-depth alongside Python no longer publishing them (see
      // docs/adr-trade-plan-v8-cutover.md). Manual /algo candidates are
      // explicitly exempt - they are the owner's direct decision, not
      // autonomous analysis output.
      return await RejectAsync(
        candidate,
        options.ContractMode == "v8_only"
          ? "legacy_candidate_disabled_in_v8_only"
          : "legacy_candidate_disabled_in_v7_only",
        cancellationToken
      );
    }
    var orderTypePreference = (
      candidate.OrderTypePreference ?? "either"
    ).Trim().ToLowerInvariant();
    var orderTypePreferenceSupported = orderTypePreference is
      "either" or "market" or "limit";
    var entryDistribution = (
      candidate.EntryDistribution
      ?? (manualAlgoCandidate ? "single" : "either")
    ).Trim().ToLowerInvariant();
    var entryDistributionSupported = entryDistribution is
      "single" or "zone_split" or "either";
    var targetModel = (
      candidate.TargetModel
      ?? (candidate.AbsoluteTargetPrice is null ? "fill_relative" : "hybrid")
    ).Trim().ToLowerInvariant();
    var targetModelSupported = targetModel is
      "absolute" or "fill_relative" or "hybrid";
    // Tier risk belongs to the autonomous initial candidate. Pullback adds
    // inherit the already-sized parent group's contract and must not apply
    // (or require) the multiplier a second time.
    var autonomousRiskValid = manualAlgoCandidate
      || !string.IsNullOrWhiteSpace(candidate.ParentGroupId)
      || (
      candidate.RiskMultiplier is decimal autonomousMultiplier
      && autonomousMultiplier > 0m
      && autonomousMultiplier <= 1m
      );
    if (!orderTypePreferenceSupported)
    {
      return await RejectAsync(
        candidate,
        "unsupported order_type_preference",
        cancellationToken
      );
    }
    if (!entryDistributionSupported)
    {
      return await RejectAsync(
        candidate,
        "unsupported entry_distribution",
        cancellationToken
      );
    }
    if (!targetModelSupported)
    {
      return await RejectAsync(
        candidate,
        "unsupported target_model",
        cancellationToken
      );
    }
    if (!autonomousRiskValid)
    {
      return await RejectAsync(
        candidate,
        "invalid autonomous risk_multiplier",
        cancellationToken
      );
    }
    candidate = candidate with
    {
      OrderTypePreference = orderTypePreference,
      EntryDistribution = entryDistribution,
      TargetModel = targetModel,
    };
    if (boxRangeScalp)
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        candidate.Direction.Equals("BUY", StringComparison.OrdinalIgnoreCase)
          ? "range_buy_rail_triggered"
          : "range_sell_rail_triggered",
        cancellationToken
      );
    }
    if (
      (
        !legacyRangeScalp
        && !boxRangeScalp
        && !trendCandidate
        && !strategyMatchCandidate
        && !manualAlgoCandidate
      )
      // MinConfluence exists to filter the autonomous engines' own
      // confidence scoring - a manually-typed /algo signal is the owner's
      // explicit decision (Python defaults an untagged signal's confluence
      // to 1), so it is exempt rather than silently rejected under the
      // default MinConfluence=2.
      || (!manualAlgoCandidate && candidate.Confluence < options.MinConfluence)
      || !string.Equals(
        candidate.Symbol,
        symbol.RedisSymbol,
        StringComparison.OrdinalIgnoreCase
      )
      || candidate.EntryZone is null
      || candidate.EntryZone.Low > candidate.EntryZone.High
      || (
        !string.Equals(candidate.Direction, "BUY", StringComparison.OrdinalIgnoreCase)
        && !string.Equals(candidate.Direction, "SELL", StringComparison.OrdinalIgnoreCase)
      )
    )
    {
      return await RejectAsync(candidate, "unsupported candidate", cancellationToken);
    }
    await PublishAsync(
      "routing_selected",
      $"{candidate.Setup} {candidate.Direction} selected for routing",
      cancellationToken,
      candidate.CandidateId,
      groupId: CandidateGroupId(candidate),
      setup: candidate.Setup,
      regime: candidate.Regime,
      confluence: candidate.Confluence,
      direction: candidate.Direction,
      matchId: candidate.MatchId,
      rangeId: candidate.RangeId,
      strategyFamily: candidate.StrategyFamily,
      riskMultiplier: candidate.RiskMultiplier,
      targetModel: candidate.TargetModel,
      entryDistribution: candidate.EntryDistribution
    );
    if (
      boxRangeScalp
      && (
        string.IsNullOrWhiteSpace(candidate.RangeId)
        || candidate.RangeLow is not decimal rangeLow
        || candidate.RangeHigh is not decimal rangeHigh
        || rangeLow <= 0
        || rangeHigh <= rangeLow
        || candidate.FullTakeProfitPips is not int fullTakeProfitPips
        || !options.EffectiveRangeTargetsPips.Contains(fullTakeProfitPips)
        || (
          !options.RangeFlipEnabled
          && rangeHigh - rangeLow
            < candidate.FullTakeProfitPips.Value * options.PipSize
        )
        || candidate.KeyLevel < rangeLow
        || candidate.KeyLevel > rangeHigh
      )
    )
    {
      return await RejectAsync(
        candidate,
        "invalid range-box contract",
        cancellationToken
      );
    }
    if (
      (trendCandidate || strategyMatchCandidate)
      && (
        candidate.TargetsPips is not { Count: > 0 } targetsPips
        || targetsPips.Any(pips => pips <= 0)
        || candidate.Atr is not decimal trendAtr || trendAtr <= 0
        || candidate.StructureSwing is not decimal trendSwing || trendSwing <= 0
      )
    )
    {
      return await RejectAsync(
        candidate,
        "invalid strategy candidate contract",
        cancellationToken
      );
    }
    if (
      !manualAlgoCandidate
      && (targetModel is "absolute" or "hybrid")
      && (
        candidate.AbsoluteTargetPrice is not decimal absoluteTarget
        || (
          candidate.Direction.Equals("BUY", StringComparison.OrdinalIgnoreCase)
          && absoluteTarget <= candidate.EntryZone.Low
        )
        || (
          candidate.Direction.Equals("SELL", StringComparison.OrdinalIgnoreCase)
          && absoluteTarget >= candidate.EntryZone.High
        )
      )
    )
    {
      return await RejectAsync(
        candidate,
        "invalid absolute target geometry",
        cancellationToken
      );
    }
    if (manualAlgoCandidate && !options.ManualAlgoEnabled)
    {
      return await RejectAsync(
        candidate,
        "manual_algo_disabled",
        cancellationToken
      );
    }
    if (manualAlgoCandidate && !candidate.BypassAnalysisGates)
    {
      return await RejectAsync(
        candidate,
        "manual_algo_bypass_contract_missing",
        cancellationToken
      );
    }
    if (
      manualAlgoCandidate
      && (
        candidate.ManualTakeProfits is not { Count: > 0 } manualTargets
        || manualTargets.Any(price => price <= 0)
      )
    )
    {
      return await RejectAsync(
        candidate,
        "invalid manual algo target contract",
        cancellationToken
      );
    }
    if (
      manualAlgoCandidate
      && (
        candidate.ManualStopLoss is not decimal manualStopLoss
        || manualStopLoss <= 0
      )
    )
    {
      return await RejectAsync(
        candidate,
        "invalid manual algo stop contract",
        cancellationToken
      );
    }
    if (
      now - candidate.CreatedAt > Math.Max(10, options.CandidateMaxAgeSeconds)
      || candidate.CreatedAt > now + 30
    )
    {
      return await RejectAsync(candidate, "stale candidate", cancellationToken);
    }
    if (await store.IsPausedAsync(cancellationToken))
    {
      return await RejectAsync(candidate, "executor paused", cancellationToken);
    }
    var direction = ParseDirection(candidate.Direction);
    await ReconcileAsync(cancellationToken);
    if (
      boxRangeScalp
      && options.RangeFlipEnabled
      && !(options.RangeTwoSidedEnabled && _accountSupportsHedging)
      && await OppositeFlipClosePendingAsync(candidate, cancellationToken)
    )
    {
      await store.IncrementGateRejectAsync(
        candidate.Symbol,
        "flip_close_pending",
        cancellationToken
      );
      return await RejectAsync(candidate, "flip_close_pending", cancellationToken);
    }
    if (_allSymbolPositions.Count > 0)
    {
      var existing = _allSymbolPositions.FirstOrDefault(position =>
        position.Comment.Contains(
          CandidateToken(candidate.CandidateId),
          StringComparison.Ordinal
        )
      );
      if (existing is not null)
      {
        await AdoptPositionAsync(existing, cancellationToken);
        await CompleteActiveCandidateAsync(
          $"ordered:{existing.PositionId}",
          cancellationToken
        );
        return true;
      }
    }
    var existingPending = _allSymbolPendingOrders.FirstOrDefault(order =>
      order.Comment.Contains(
        CandidateToken(candidate.CandidateId),
        StringComparison.Ordinal
      )
    );
    if (existingPending is not null)
    {
      await CompleteActiveCandidateAsync(
        $"ordered:{existingPending.OrderId}",
        cancellationToken
      );
      return true;
    }
    var hasUnmanagedPosition = _allSymbolPositions.Any(
      position => position.Label != options.Label
    );
    var hasUnmanagedOrder = _allSymbolPendingOrders.Any(
      order => order.Label != options.Label
    );
    if (!manualAlgoCandidate && (hasUnmanagedPosition || hasUnmanagedOrder))
    {
      await store.IncrementGateRejectAsync(
        candidate.Symbol,
        "unmanaged_exposure",
        cancellationToken
      );
      return await RejectAsync(
        candidate,
        "unmanaged XAU position or pending order already open",
        cancellationToken
      );
    }
    var botPositions = _allSymbolPositions
      .Where(position => position.Label == options.Label)
      .ToArray();
    var botOrders = _allSymbolPendingOrders
      .Where(order => order.Label == options.Label)
      .ToArray();
    var hadExistingExposure = botPositions.Length > 0 || botOrders.Length > 0;
    if (
      boxRangeScalp
      && hadExistingExposure
    )
    {
      if (options.RequireFlatForRange)
      {
        await store.IncrementGateRejectAsync(
          candidate.Symbol,
          "range_box_awaiting_flat",
          cancellationToken
        );
        return await RejectAsync(
          candidate,
          "range-box scalp waits for flat XAU exposure",
          cancellationToken
        );
      }
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "range_box_would_have_awaited_flat",
        cancellationToken
      );
    }
    if (
      boxRangeScalp
      && options.RangeTwoSidedEnabled
      && !_accountSupportsHedging
      && options.NonHedgedOppositePolicy == "close_then_reverse"
    )
    {
      await CloseOppositeExposureForNonHedgedAsync(
        candidate,
        direction,
        cancellationToken
      );
      await ReconcileAsync(cancellationToken);
    }
    var date = DateOnly.FromDateTime(_clock().UtcDateTime);
    var account = await client.GetTradingAccountAsync(cancellationToken);
    ValidateAccount(account);
    SpotPrice quote;
    try
    {
      quote = ValidateQuote(candidate);
    }
    catch (CandidateRejectedException exception)
    {
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    var expectedEntry = direction == TradeDirection.Buy ? quote.Ask : quote.Bid;
    if (manualAlgoCandidate)
    {
      return await ProcessManualAlgoAsync(
        candidate,
        account,
        direction,
        expectedEntry,
        date,
        cancellationToken
      );
    }
    if (
      _positionIdentityConflict
      && string.IsNullOrWhiteSpace(candidate.ParentGroupId)
      && !IsManualAlgoCandidate(candidate)
    )
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "executor_position_identity_conflict_active",
        cancellationToken
      );
      return await RejectAsync(
        candidate,
        "broker_position_identity_group_conflict",
        cancellationToken
      );
    }
    if (await TryRejectIndependentGroupOnNonHedgedAccountAsync(
      candidate,
      symbol,
      cancellationToken
    ))
    {
      return true;
    }
    if (await TryRejectOppositeInitialGroupAsync(
      candidate,
      direction,
      symbol,
      cancellationToken
    ))
    {
      return true;
    }
    // Route first: the protective-stop contract is only meaningful against the
    // entry the executor will actually use, so the route and its planned entry
    // are resolved before any stop is computed or validated.
    var route = ResolveExecutionRoute(candidate, direction, expectedEntry);
    if (route.RejectReason is string routeRejection)
    {
      return await RejectAsync(candidate, routeRejection, cancellationToken);
    }
    if (route.RoutingReason is string routingReason)
    {
      _log($"auto-trade {routingReason}");
    }
    var plannedEntry = route.PlannedEntryPrice;
    if (
      await ValidateEntryContractAsync(
        candidate,
        route,
        expectedEntry,
        symbol,
        cancellationToken
      ) is string entryRejection
    )
    {
      return await RejectAsync(candidate, entryRejection, cancellationToken);
    }
    StructureStopPlan stopPlan;
    try
    {
      stopPlan = StructureStop(
        candidate,
        direction,
        plannedEntry,
        symbol,
        route.Route
      );
    }
    catch (VolumePlanningException exception)
    {
      if (exception.Message == "stop_exceeds_envelope_after_wick")
      {
        await store.IncrementGateRejectAsync(
          candidate.Symbol,
          "stop_exceeds_envelope_after_wick",
          cancellationToken
        );
      }
      else if (exception.Message == "stop_inside_opposing_zone")
      {
        await store.IncrementGateRejectAsync(
          candidate.Symbol,
          "stop_in_opposing_zone",
          cancellationToken
        );
      }
      else if (exception.Message == "final_stop_zone_identity_mismatch")
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "final_stop_zone_identity_mismatch",
          cancellationToken
        );
      }
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    await ObserveMarketStopContractRecomputeAsync(
      candidate,
      route.Route,
      plannedEntry,
      cancellationToken
    );
    // The approved absolute stop must still be on the losing side of the price
    // the broker can actually transact at, whatever the route.
    if (
      FinalStopSideRejection(
        direction,
        stopPlan.StopLoss,
        expectedEntry
      ) is { } stopSideRejection
    )
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        stopSideRejection.Metric,
        cancellationToken
      );
      return await RejectAsync(
        candidate,
        stopSideRejection.Reason,
        cancellationToken
      );
    }
    decimal? boxTargetPips = null;
    if (boxRangeScalp)
    {
      try
      {
        boxTargetPips = BoxTargetPips(candidate, direction, plannedEntry);
      }
      catch (VolumePlanningException exception)
      {
        return await RejectAsync(candidate, exception.Message, cancellationToken);
      }
    }
    if (
      !IsManualAlgoCandidate(candidate)
      && string.IsNullOrWhiteSpace(candidate.ParentGroupId)
      && !ValidateInitialRewardRisk(
        candidate,
        direction,
        plannedEntry,
        stopPlan,
        boxTargetPips
      )
    )
    {
      var minRewardRisk = ResolveMinRewardRisk(candidate);
      return await RejectAsync(
        candidate,
        boxRangeScalp
          ? $"range-box reward/risk below {options.BoxMinRiskReward:0.##}"
          : $"reward/risk below {minRewardRisk:0.##}",
        cancellationToken
      );
    }

    var candidateGroupId = CandidateGroupId(candidate);
    if (string.IsNullOrWhiteSpace(candidate.ParentGroupId))
    {
      if (await HasActiveDuplicateReactionAsync(candidate, cancellationToken))
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "executor_duplicate_reaction_rejected",
          cancellationToken
        );
        await CompleteActiveCandidateAsync(
          "already_processed:duplicate_reaction_active",
          cancellationToken
        );
        _log(
          $"auto-trade candidate {Short(candidate.CandidateId)} "
          + "already_processed:duplicate_reaction_active"
        );
        return true;
      }
      if (await HasActiveDuplicateMappedThesisAsync(candidate, cancellationToken))
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "executor_duplicate_thesis_rejected",
          cancellationToken
        );
        await CompleteActiveCandidateAsync(
          "already_processed:active_thesis_group",
          cancellationToken
        );
        _log(
          $"auto-trade candidate {Short(candidate.CandidateId)} "
          + "already_processed:active_thesis_group"
        );
        return true;
      }
      var activeForGroup = _states.Values.Any(state =>
        state.SymbolId == symbol.SymbolId
        && GroupId(state) == candidateGroupId
      );
      if (activeForGroup)
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "executor_duplicate_group_rejected",
          cancellationToken
        );
        await CompleteActiveCandidateAsync(
          "already_processed:active_initial_group",
          cancellationToken
        );
        _log(
          $"auto-trade candidate {Short(candidate.CandidateId)} "
          + "already_processed:active_initial_group"
        );
        return true;
      }
      var pendingForGroup = _allSymbolPendingOrders.Any(order =>
        order.Label == options.Label
        && order.Comment.Contains(
          GroupToken(candidateGroupId),
          StringComparison.Ordinal
        )
      );
      if (
        pendingForGroup
      )
      {
        return await RejectAsync(
          candidate,
          "planned zone fill is still pending",
          cancellationToken
        );
      }
      return await ProcessInitialAsync(
        candidate,
        account,
        direction,
        expectedEntry,
        route,
        stopPlan,
        date,
        cancellationToken
      );
    }
    if (!IsTrendCandidate(candidate))
    {
      return await RejectAsync(
        candidate,
        "only trend candidates may reference parent_group_id",
        cancellationToken
      );
    }
    var parentGroupId = GroupToken(candidate.ParentGroupId);
    var group = _states.Values
      .Where(state =>
        state.SymbolId == symbol.SymbolId
        && GroupId(state) == parentGroupId
      )
      .OrderBy(state => state.TrancheIndex)
      .ToArray();
    if (group.Length == 0)
    {
      return await RejectAsync(
        candidate,
        "explicit parent trend group is not active",
        cancellationToken
      );
    }
    if (
      group.Any(state => state.Direction != direction)
      || group.Any(state => !SameStrategyFamily(state, candidate))
      || group.Any(state =>
        !string.IsNullOrWhiteSpace(state.RangeId)
        && !string.Equals(
          state.RangeId,
          candidate.RangeId,
          StringComparison.Ordinal
        )
      )
      || group.Any(state =>
        !string.IsNullOrWhiteSpace(state.StructuralSource)
        && !string.IsNullOrWhiteSpace(candidate.StructuralSource)
        && !string.Equals(
          state.StructuralSource,
          candidate.StructuralSource,
          StringComparison.Ordinal
        )
      )
      || group.Any(state =>
        !string.IsNullOrWhiteSpace(state.ZoneId)
        && !string.IsNullOrWhiteSpace(candidate.ZoneId)
        && !string.Equals(
          state.ZoneId,
          candidate.ZoneId,
          StringComparison.Ordinal
        )
      )
    )
    {
      return await RejectAsync(
        candidate,
        "candidate group ownership conflicts with an existing strategy",
        cancellationToken
      );
    }
    return await ProcessAddAsync(
      candidate,
      account,
      direction,
      expectedEntry,
      stopPlan,
      quote,
      group,
      date,
      cancellationToken
    );
  }

  // Dispatch only - the route and its planned entry were already resolved and
  // the stop contract validated against that entry.
  private async Task<bool> ProcessInitialAsync(
    TradeCandidate candidate,
    TradingAccountSnapshot account,
    TradeDirection direction,
    decimal expectedEntry,
    ExecutionRouteResolution route,
    StructureStopPlan stopPlan,
    DateOnly date,
    CancellationToken cancellationToken
  ) => route.Route switch
  {
    ExecutionRoute.SingleLimit => await ProcessSingleLimitInitialAsync(
      candidate,
      account,
      direction,
      route.PlannedEntryPrice,
      stopPlan,
      date,
      cancellationToken
    ),
    ExecutionRoute.ZoneSplit => await ProcessZoneFillAsync(
      candidate,
      account,
      direction,
      expectedEntry,
      route.PlannedEntryPrice,
      stopPlan,
      date,
      cancellationToken
    ),
    _ => await ProcessSingleInitialAsync(
      candidate,
      account,
      direction,
      route.PlannedEntryPrice,
      stopPlan,
      date,
      routingReason: route.RoutingReason,
      cancellationToken: cancellationToken
    ),
  };

  // Phase 1 of execution planning: pick the route and the exact entry geometry
  // the broker request will use. No stop is computed or validated here.
  private ExecutionRouteResolution ResolveExecutionRoute(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal executableEntry
  )
  {
    if (IsManualAlgoCandidate(candidate))
    {
      return new ExecutionRouteResolution(
        ExecutionRoute.ManualLimit,
        executableEntry,
        null,
        null
      );
    }
    var preference = (
      candidate.OrderTypePreference ?? "either"
    ).Trim().ToLowerInvariant();
    var distribution = (
      candidate.EntryDistribution ?? "single"
    ).Trim().ToLowerInvariant();
    if (preference == "market")
    {
      return distribution == "zone_split"
        ? new ExecutionRouteResolution(
          ExecutionRoute.Market,
          executableEntry,
          null,
          "market order cannot use zone_split entry distribution"
        )
        : new ExecutionRouteResolution(
          ExecutionRoute.Market,
          executableEntry,
          "execution policy: market",
          null
        );
    }
    var geometry = ClassifyEntryGeometry(
      candidate.EntryZone,
      direction,
      executableEntry
    );
    var splitQualified = (
      options.ZoneFillEnabled
      && candidate.Atr is decimal limitAtr
      && ZoneFillPlanner.Qualifies(
        candidate.EntryZone,
        limitAtr,
        options.ZoneFillMinAtr
      )
    );
    if (
      preference == "limit"
      && (
        distribution == "single"
        || (distribution == "either" && !splitQualified)
      )
    )
    {
      var limitPrice = SelectValidSideProximal(
        candidate.EntryZone,
        direction,
        executableEntry,
        geometry,
        insideZoneMarketEntryEnabled: false
      );
      return limitPrice is decimal limit
        ? new ExecutionRouteResolution(
          ExecutionRoute.SingleLimit,
          limit,
          "execution policy: single limit",
          null
        )
        : new ExecutionRouteResolution(
          ExecutionRoute.SingleLimit,
          executableEntry,
          null,
          "required single limit is not on the valid broker side"
        );
    }
    if (distribution == "zone_split" && !splitQualified)
    {
      return new ExecutionRouteResolution(
        ExecutionRoute.ZoneSplit,
        executableEntry,
        null,
        "execution policy requires unavailable zone_split limit capability"
      );
    }
    if (
      !IsBoxRangeScalp(candidate)
      && distribution != "single"
      && (!IsStrategyMatchCandidate(candidate) || preference == "limit"
        || distribution == "zone_split")
      && splitQualified
    )
    {
      var proximal = SelectValidSideProximal(
        candidate.EntryZone,
        direction,
        executableEntry,
        geometry,
        options.InsideZoneMarketEntryEnabled
      );
      if (proximal is decimal reference)
      {
        return new ExecutionRouteResolution(
          ExecutionRoute.ZoneSplit,
          reference,
          "execution policy: zone split",
          null
        );
      }
      if (!options.ZoneFillFallbackEnabled)
      {
        return new ExecutionRouteResolution(
          ExecutionRoute.ZoneSplit,
          executableEntry,
          null,
          "zone-fill proximal edge is not on the valid limit-order side"
        );
      }
      // Deterministic fallback to a single market entry. The stop contract is
      // then validated at the executable quote, so a limit-priced contract
      // fails route validation instead of silently trading a different entry.
      return new ExecutionRouteResolution(
        ExecutionRoute.Market,
        executableEntry,
        $"zone-fill geometry invalid; single-entry fallback ({geometry})",
        null
      );
    }
    return new ExecutionRouteResolution(
      ExecutionRoute.Market,
      executableEntry,
      null,
      null
    );
  }

  // Phase 2 gate: resting entries must match the fixed price Python planned.
  // A market entry is the live quote and remains bounded by ValidateQuote's
  // entry-zone distance cap. Unknown route values fail closed; only an
  // explicit `either` leaves the executor free to choose.
  private async Task<string?> ValidateEntryContractAsync(
    TradeCandidate candidate,
    ExecutionRouteResolution route,
    decimal executableEntry,
    SymbolInfo symbol,
    CancellationToken cancellationToken
  )
  {
    if (IsManualAlgoCandidate(candidate))
    {
      return null;
    }
    var declaredRaw = candidate.PlannedExecutionRoute;
    if (string.IsNullOrWhiteSpace(declaredRaw))
    {
      // The entry-plan contract is opt-in via EntryPlanVersion. Older stop-plan
      // v5 candidates (and earlier) may omit the route during rolling deploy.
      if (candidate.EntryPlanVersion is null)
      {
        return null;
      }
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "final_stop_entry_route_invalid",
        cancellationToken
      );
      return "final_stop_entry_route_invalid";
    }
    if (!TryParseExecutionRouteContract(declaredRaw, out var declaredRoute))
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "final_stop_entry_route_invalid",
        cancellationToken
      );
      return "final_stop_entry_route_invalid";
    }
    if (declaredRoute == PlannedExecutionRoute.Either)
    {
      // Strict entry/stop-plan contracts must commit to an explicit route.
      // Legacy candidates without those versions may still leave `either` open.
      if (
        candidate.EntryPlanVersion is >= 1
        && candidate.StopPlanVersion is >= 2
      )
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "final_stop_entry_route_invalid",
          cancellationToken
        );
        return "final_stop_entry_route_invalid";
      }
      // Python intentionally did not commit to one route. Stop contract fields
      // remain the gate; an invalid route is never reinterpreted as either.
      return null;
    }
    if (!RouteSatisfiesContract(declaredRoute, route.Route))
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "final_stop_entry_route_mismatch",
        cancellationToken
      );
      return "final_stop_entry_route_mismatch";
    }
    if (candidate.PlannedEntryPrice is not decimal plannedEntry)
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "final_stop_planned_entry_missing",
        cancellationToken
      );
      return "final_stop_planned_entry_missing";
    }
    var tolerance = Math.Max(0m, options.EntryContractTolerancePips)
      * options.PipSize;
    var tick = SymbolTick(symbol);
    var driftBudget = Math.Max(tick, tolerance);
    if (
      EntryIsResting(route.Route)
      && Math.Abs(plannedEntry - route.PlannedEntryPrice) > driftBudget
    )
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "final_stop_entry_drift_rejected",
        cancellationToken
      );
      return "final_stop_entry_drift_rejected";
    }
    if (
      route.Route == ExecutionRoute.Market
      && Math.Abs(plannedEntry - executableEntry) > driftBudget
    )
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "entry_contract_market_drift_observed",
        cancellationToken
      );
    }
    if (declaredRoute == PlannedExecutionRoute.SingleLimit)
    {
      var legs = candidate.PlannedLegEntryPrices;
      if (legs is null || legs.Count == 0)
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "final_stop_leg_entries_missing",
          cancellationToken
        );
        return "final_stop_leg_entries_missing";
      }
      if (legs.Count != 1)
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "final_stop_leg_entry_count_mismatch",
          cancellationToken
        );
        return "final_stop_leg_entry_count_mismatch";
      }
      if (Math.Abs(legs[0] - plannedEntry) > tick)
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "final_stop_leg_entry_mismatch",
          cancellationToken
        );
        return "final_stop_leg_entry_mismatch";
      }
    }
    if (declaredRoute == PlannedExecutionRoute.ZoneSplit)
    {
      var legs = candidate.PlannedLegEntryPrices;
      if (legs is null || legs.Count == 0)
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "final_stop_leg_entries_missing",
          cancellationToken
        );
        return "final_stop_leg_entries_missing";
      }
      if (legs.Count != 2)
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "final_stop_leg_entry_count_mismatch",
          cancellationToken
        );
        return "final_stop_leg_entry_count_mismatch";
      }
      // Reference entry is the proximal leg; it must match the planned entry.
      if (Math.Abs(legs[0] - plannedEntry) > tick)
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "final_stop_leg_entry_mismatch",
          cancellationToken
        );
        return "final_stop_leg_entry_mismatch";
      }
    }
    return null;
  }

  internal enum PlannedExecutionRoute
  {
    Market,
    SingleLimit,
    ZoneSplit,
    Either,
  }

  private static bool TryParseExecutionRouteContract(
    string? value,
    out PlannedExecutionRoute route
  )
  {
    switch ((value ?? "").Trim().ToLowerInvariant())
    {
      case "market":
        route = PlannedExecutionRoute.Market;
        return true;
      case "single_limit":
        route = PlannedExecutionRoute.SingleLimit;
        return true;
      case "zone_split":
        route = PlannedExecutionRoute.ZoneSplit;
        return true;
      case "either":
        route = PlannedExecutionRoute.Either;
        return true;
      default:
        route = default;
        return false;
    }
  }

  private static bool RouteSatisfiesContract(
    PlannedExecutionRoute declaredRoute,
    ExecutionRoute resolved
  ) => declaredRoute switch
  {
    PlannedExecutionRoute.Market => resolved == ExecutionRoute.Market,
    PlannedExecutionRoute.SingleLimit => resolved == ExecutionRoute.SingleLimit,
    PlannedExecutionRoute.ZoneSplit => resolved == ExecutionRoute.ZoneSplit,
    PlannedExecutionRoute.Either => true,
    _ => false,
  };

  // Kept for the shared Python/C# route fixture which asserts string routes.
  private static bool RouteSatisfiesContract(
    string declaredRoute,
    ExecutionRoute resolved
  ) => TryParseExecutionRouteContract(declaredRoute, out var parsed)
    && RouteSatisfiesContract(parsed, resolved);

  private async Task<bool> ProcessSingleInitialAsync(
    TradeCandidate candidate,
    TradingAccountSnapshot account,
    TradeDirection direction,
    decimal expectedEntry,
    StructureStopPlan stopPlan,
    DateOnly date,
    string? routingReason,
    CancellationToken cancellationToken
  )
  {
    InitialSizingResult sizing;
    var boxTarget = IsBoxRangeScalp(candidate)
      ? BoxTarget(candidate, direction, expectedEntry)
      : ((int Pips, decimal? ExitPrice)?)null;
    var rangeBoxScaleOut = TryRangeBoxScaleOutPlan(candidate, out var scaleOutPips);
    IReadOnlyList<int> targetPips = UsesCandidateTargetPlan(candidate)
      ? candidate.TargetsPips!
      : rangeBoxScaleOut
        ? scaleOutPips!
        : IsBoxRangeScalp(candidate)
          ? [boxTarget!.Value.Pips]
          : options.TargetsPips;
    IReadOnlyList<int> targetWeights = UsesCandidateTargetPlan(candidate)
      ? EqualWeights(candidate.TargetsPips!.Count)
      : rangeBoxScaleOut
        ? RangeBoxScaleOutWeights()
        : IsBoxRangeScalp(candidate)
          ? [100]
          : options.TargetWeights;
    try
    {
      sizing = VolumePlanner.SizeInitial(
        account.Balance,
        EffectiveInitialRiskPercent(candidate),
        options.SizingMode,
        stopPlan.StopPips,
        options.PipValuePerLot,
        RequireSymbol(),
        targetPips,
        targetWeights
      );
    }
    catch (VolumePlanningException exception) when (rangeBoxScaleOut)
    {
      // Valid 50/50 split impossible (broker min/step) — keep full position
      // to the original Full TP only.
      _log(
        $"range-box scale-out skipped for {candidate.CandidateId}: "
        + exception.Message
      );
      rangeBoxScaleOut = false;
      targetPips = [boxTarget!.Value.Pips];
      targetWeights = [100];
      try
      {
        sizing = VolumePlanner.SizeInitial(
          account.Balance,
          EffectiveInitialRiskPercent(candidate),
          options.SizingMode,
          stopPlan.StopPips,
          options.PipValuePerLot,
          RequireSymbol(),
          targetPips,
          targetWeights
        );
      }
      catch (VolumePlanningException fallbackException)
      {
        return await RejectAsync(
          candidate, fallbackException.Message, cancellationToken
        );
      }
    }
    catch (VolumePlanningException exception)
    {
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    var groupId = CandidateGroupId(candidate);
    var barTs = candidate.BarTs ?? candidate.CreatedAt;
    var routingSuffix = routingReason is null ? "" : $" · {routingReason}";
    if (options.DryRun)
    {
      var targetSummary = IsBoxRangeScalp(candidate)
        ? (
          targetPips.Count >= 2
            ? $" · TP1 +{targetPips[0]}p book {options.RangeBoxScaleOutFraction:0%} · Full TP +{targetPips[^1]}p"
            : $" · full TP {targetPips[0]}p"
        )
        : "";
      return await CompleteDryRunAsync(
        candidate,
        $"{direction} {sizing.Lots:N2} lots · structure stop "
        + $"{stopPlan.StopPips:N0}p{targetSummary} · {sizing.BindingTerm}"
        + routingSuffix,
        sizing.Volume,
        expectedEntry,
        cancellationToken
      );
    }
    if (await store.IsPausedAsync(cancellationToken))
    {
      return await RejectAsync(candidate, "executor paused", cancellationToken);
    }
    await ReconcileAsync(cancellationToken);
    if (!CanOpenNewGroup(direction))
    {
      return await RejectAsync(
        candidate,
        "XAU exposure policy changed before initial order",
        cancellationToken
      );
    }
    return await PlaceTrancheAsync(
      candidate,
      direction,
      expectedEntry,
      stopPlan,
      sizing.Volume,
      sizing.TargetPlan,
      groupId,
      trancheIndex: 1,
      groupBookedPnl: 0m,
      initialBookedPnl: 0m,
      groupOpenedAt: barTs,
      lastTrancheBarTs: barTs,
      groupTrancheCount: 1,
      hadAdds: false,
      groupRealizedPipVolume: 0m,
      initialRealizedPipVolume: 0m,
      groupInitialVolume: sizing.Volume,
      initialTrancheVolume: sizing.Volume,
      date,
      eventType: "opened",
      message: $"{direction} {sizing.Lots:N2} lots filled {{fill}}, "
        + $"SL {{stop}} · {stopPlan.StopPips:N0}p structure · "
        + (IsBoxRangeScalp(candidate)
          ? (
            targetPips.Count >= 2
              ? $"TP1 +{targetPips[0]}p book {options.RangeBoxScaleOutFraction:0%} · Full TP +{targetPips[^1]}p · range "
                + $"{candidate.RangeLow:N2}-{candidate.RangeHigh:N2} · "
              : $"full TP {targetPips[0]}p · range "
                + $"{candidate.RangeLow:N2}-{candidate.RangeHigh:N2} · "
          )
          : "")
        + sizing.BindingTerm
        + routingSuffix,
      groupWorstCase: -sizing.Lots * stopPlan.StopPips
        * options.PipValuePerLot,
      riskBudget: sizing.Budget,
      cancellationToken
    );
  }

  // `limitPrice` is the route-resolved entry and `stopPlan` was already
  // validated against it, so nothing here re-derives either value.
  private async Task<bool> ProcessSingleLimitInitialAsync(
    TradeCandidate candidate,
    TradingAccountSnapshot account,
    TradeDirection direction,
    decimal limitPrice,
    StructureStopPlan stopPlan,
    DateOnly date,
    CancellationToken cancellationToken
  )
  {
    var symbol = RequireSymbol();
    InitialSizingResult sizing;
    var targets = UsesCandidateTargetPlan(candidate)
      ? candidate.TargetsPips!
      : options.TargetsPips;
    var weights = UsesCandidateTargetPlan(candidate)
      ? EqualWeights(targets.Count)
      : options.TargetWeights;
    try
    {
      sizing = VolumePlanner.SizeInitial(
        account.Balance,
        EffectiveInitialRiskPercent(candidate),
        options.SizingMode,
        stopPlan.StopPips,
        options.PipValuePerLot,
        symbol,
        targets,
        weights
      );
    }
    catch (VolumePlanningException exception)
    {
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    var groupId = CandidateGroupId(candidate);
    var barTs = candidate.BarTs ?? candidate.CreatedAt;
    if (options.DryRun)
    {
      return await CompleteDryRunAsync(
        candidate,
        $"single limit · {sizing.Lots:N2} lots at {limitPrice:N2} · "
          + $"SL {stopPlan.StopPips:N0}p · {sizing.BindingTerm}",
        sizing.Volume,
        limitPrice,
        cancellationToken
      );
    }
    if (await store.IsPausedAsync(cancellationToken))
    {
      return await RejectAsync(candidate, "executor paused", cancellationToken);
    }
    await ReconcileAsync(cancellationToken);
    if (!CanOpenNewGroup(direction))
    {
      return await RejectAsync(
        candidate,
        "XAU exposure policy changed before single limit order",
        cancellationToken
      );
    }
    var leg = new ZoneFillLegPlan(
      1,
      limitPrice,
      sizing.Volume,
      sizing.TargetPlan
    );
    // The approved absolute stop, not a distance re-derived from the entry.
    var stopLoss = decimal.Round(
      stopPlan.StopLoss,
      symbol.Digits,
      MidpointRounding.AwayFromZero
    );
    await PublishAsync(
      "order_submitted",
      $"{candidate.Setup} {candidate.Direction} single limit submitted",
      cancellationToken,
      candidate.CandidateId,
      volume: sizing.Volume,
      price: limitPrice,
      groupId: groupId,
      setup: candidate.Setup,
      direction: candidate.Direction,
      matchId: candidate.MatchId,
      strategyFamily: candidate.StrategyFamily,
      riskMultiplier: candidate.RiskMultiplier,
      targetModel: candidate.TargetModel,
      entryDistribution: "single"
    );
    // Persist the resolved route and the exact ID that is about to be
    // submitted before any broker side effect can occur.
    var clientOrderId = $"{ClientOrderId(candidate.CandidateId)}-l1";
    await SaveGroupPlanAsync(
      candidate,
      groupId,
      cancellationToken,
      streamEventId: _activeLease?.StreamEventId,
      route: "single_limit",
      clientOrderIds: [clientOrderId]
    );
    if (!await EnsureBrokerLeaseAsync(cancellationToken))
    {
      throw new CandidateLeaseLostException(candidate.CandidateId);
    }
    long orderId;
    using var brokerCts = CreateBrokerCancellation(cancellationToken);
    try
    {
      orderId = await RequireClient().PlaceLimitOrderAsync(
        new LimitOrderRequest(
          symbol.SymbolId,
          direction,
          sizing.Volume,
          limitPrice,
          decimal.ToInt64(Math.Abs(limitPrice - stopLoss) * 100_000m),
          options.Label,
          BuildZoneComment(candidate.CandidateId, groupId, leg, barTs),
          clientOrderId
        ),
        brokerCts.Token
      );
    }
    catch (Exception exception) when (
      exception is not OperationCanceledException
      || BrokerOwnershipCancelled()
    )
    {
      // The request may have been accepted. Keep the group plan so the
      // deterministic client order ID can be adopted during reconciliation.
      throw ClassifyBrokerUncertainty(candidate, clientOrderId, exception);
    }
    await CompleteActiveCandidateAsync(
      $"ordered:{orderId}",
      cancellationToken
    );
    await store.IncrementDailyTradeCountAsync(date, cancellationToken);
    await PublishAsync(
      "order_accepted",
      $"broker accepted single limit order {orderId}",
      cancellationToken,
      candidate.CandidateId,
      volume: sizing.Volume,
      price: limitPrice,
      groupId: groupId,
      setup: candidate.Setup,
      direction: candidate.Direction,
      matchId: candidate.MatchId,
      strategyFamily: candidate.StrategyFamily,
      pendingOrderIds: [orderId],
      riskMultiplier: candidate.RiskMultiplier,
      targetModel: candidate.TargetModel,
      entryDistribution: "single"
    );
    await ReconcileAsync(cancellationToken);
    return true;
  }

  // `referenceEntry` is the route-resolved zone-fill reference entry and
  // `zoneStopPlan` was already validated against it.
  private async Task<bool> ProcessZoneFillAsync(
    TradeCandidate candidate,
    TradingAccountSnapshot account,
    TradeDirection direction,
    decimal expectedEntry,
    decimal referenceEntry,
    StructureStopPlan zoneStopPlan,
    DateOnly date,
    CancellationToken cancellationToken
  )
  {
    var symbol = RequireSymbol();
    var geometry = ClassifyEntryGeometry(
      candidate.EntryZone,
      direction,
      expectedEntry
    );
    var proximal = (decimal?)referenceEntry;
    InitialSizingResult sizing;
    var zoneTargets = UsesCandidateTargetPlan(candidate)
      ? candidate.TargetsPips!
      : options.TargetsPips;
    var zoneWeights = UsesCandidateTargetPlan(candidate)
      ? EqualWeights(zoneTargets.Count)
      : options.TargetWeights;
    try
    {
      sizing = VolumePlanner.SizeInitial(
        account.Balance,
        EffectiveInitialRiskPercent(candidate),
        options.SizingMode,
        zoneStopPlan.StopPips,
        options.PipValuePerLot,
        symbol,
        zoneTargets,
        zoneWeights
      );
    }
    catch (VolumePlanningException exception)
    {
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    if (sizing.Lots < options.ZoneFillMinLots)
    {
      var reason = $"zone-fill skipped: {sizing.Lots:0.00} lots below "
        + $"{options.ZoneFillMinLots:0.00} minimum";
      _log($"auto-trade {reason}");
      return await FallBackToSingleEntryAsync(
        candidate,
        account,
        direction,
        expectedEntry,
        symbol,
        date,
        reason,
        cancellationToken
      );
    }
    var validLimitSide = direction == TradeDirection.Buy
      ? proximal.Value <= expectedEntry
      : proximal.Value >= expectedEntry;
    if (!validLimitSide)
    {
      if (!options.ZoneFillFallbackEnabled)
      {
        return await RejectAsync(
          candidate,
          "zone-fill proximal edge is not on the valid limit-order side",
          cancellationToken
        );
      }
      var fallbackReason =
        "zone-fill geometry invalid; single-entry fallback"
        + $" ({geometry})";
      _log($"auto-trade {fallbackReason}");
      return await FallBackToSingleEntryAsync(
        candidate,
        account,
        direction,
        expectedEntry,
        symbol,
        date,
        fallbackReason,
        cancellationToken
      );
    }
    var fillZone = SliceValidSideZone(
      candidate.EntryZone,
      direction,
      expectedEntry,
      proximal.Value
    );
    ZoneFillPlan plan;
    try
    {
      // Every leg shares the one approved absolute stop.
      var stopLoss = decimal.Round(
        zoneStopPlan.StopLoss,
        symbol.Digits,
        MidpointRounding.AwayFromZero
      );
      plan = ZoneFillPlanner.Build(
        direction,
        fillZone,
        stopLoss,
        sizing.Volume,
        symbol,
        zoneTargets,
        zoneWeights
      );
    }
    catch (VolumePlanningException exception)
    {
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    if (candidate.PlannedLegEntryPrices is { Count: > 0 } declaredLegs)
    {
      var tick = SymbolTick(symbol);
      if (declaredLegs.Count != plan.Legs.Count)
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "final_stop_leg_entry_count_mismatch",
          cancellationToken
        );
        return await RejectAsync(
          candidate,
          "final_stop_leg_entry_count_mismatch",
          cancellationToken
        );
      }
      for (var index = 0; index < plan.Legs.Count; index++)
      {
        if (Math.Abs(declaredLegs[index] - plan.Legs[index].LimitPrice) > tick)
        {
          await store.IncrementMetricAsync(
            candidate.Symbol,
            "final_stop_leg_entry_mismatch",
            cancellationToken
          );
          return await RejectAsync(
            candidate,
            "final_stop_leg_entry_mismatch",
            cancellationToken
          );
        }
      }
    }
    var groupId = CandidateGroupId(candidate);
    var barTs = candidate.BarTs ?? candidate.CreatedAt;
    if (options.DryRun)
    {
      return await CompleteDryRunAsync(
        candidate,
        $"zone fill · {sizing.Lots:N2} lots across {plan.Legs.Count} limits · "
          + $"SL {plan.StopLoss:N2} · {sizing.BindingTerm} · route={geometry}",
        sizing.Volume,
        proximal.Value,
        cancellationToken
      );
    }
    if (await store.IsPausedAsync(cancellationToken))
    {
      return await RejectAsync(candidate, "executor paused", cancellationToken);
    }
    await ReconcileAsync(cancellationToken);
    if (!CanOpenNewGroup(direction))
    {
      return await RejectAsync(
        candidate,
        "XAU exposure policy changed before zone-fill orders",
        cancellationToken
      );
    }
    var placed = new List<long>();
    await PublishAsync(
      "order_planned",
      $"zone fill {candidate.Direction} planned across {plan.Legs.Count} limits",
      cancellationToken,
      candidate.CandidateId,
      volume: sizing.Volume,
      groupId: groupId,
      setup: candidate.Setup,
      direction: candidate.Direction,
      matchId: candidate.MatchId,
      rangeId: candidate.RangeId,
      strategyFamily: candidate.StrategyFamily,
      riskMultiplier: candidate.RiskMultiplier,
      targetModel: candidate.TargetModel,
      entryDistribution: candidate.EntryDistribution
    );
    await PublishAsync(
      "order_submitted",
      $"zone fill {candidate.Direction} submitted to broker",
      cancellationToken,
      candidate.CandidateId,
      volume: sizing.Volume,
      groupId: groupId,
      setup: candidate.Setup,
      direction: candidate.Direction,
      matchId: candidate.MatchId,
      rangeId: candidate.RangeId,
      strategyFamily: candidate.StrategyFamily,
      riskMultiplier: candidate.RiskMultiplier,
      targetModel: candidate.TargetModel,
      entryDistribution: candidate.EntryDistribution
    );
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "order_submitted",
      cancellationToken
    );
    // The plan persists the route this executor actually resolved and the
    // exact per-leg client order IDs it is about to submit - never the
    // declared candidate route (which may be `either`).
    await SaveGroupPlanAsync(
      candidate,
      groupId,
      cancellationToken,
      streamEventId: _activeLease?.StreamEventId,
      route: "zone_split",
      clientOrderIds: plan.Legs
        .Select(leg => $"{ClientOrderId(candidate.CandidateId)}-z{leg.Leg}")
        .ToArray()
    );
    if (!await EnsureBrokerLeaseAsync(cancellationToken))
    {
      // The group plan stays: a successor or recovery run needs it to map
      // deterministic client order IDs back to this candidate.
      throw new CandidateLeaseLostException(candidate.CandidateId);
    }
    var legClientOrderId = ClientOrderId(candidate.CandidateId);
    using var brokerCts = CreateBrokerCancellation(cancellationToken);
    try
    {
      foreach (var leg in plan.Legs)
      {
        // Ownership is proven before every leg, not once before the loop: a
        // multi-leg placement can easily outlive a single lease window.
        if (!await StillOwnsCandidateAsync(cancellationToken))
        {
          if (placed.Count == 0)
          {
            throw new CandidateLeaseLostException(candidate.CandidateId);
          }
          // Legs are already live and belong to this candidate, but ownership
          // is gone: reconciliation must decide, not this executor.
          throw ClassifyBrokerUncertainty(
            candidate,
            $"{legClientOrderId}-z{leg.Leg}",
            new CandidateLeaseLostException(candidate.CandidateId)
          );
        }
        var distance = Math.Abs(leg.LimitPrice - plan.StopLoss);
        var comment = BuildZoneComment(
          candidate.CandidateId,
          groupId,
          leg,
          barTs
        );
        legClientOrderId = $"{ClientOrderId(candidate.CandidateId)}-z{leg.Leg}";
        var orderId = await RequireClient().PlaceLimitOrderAsync(
          new LimitOrderRequest(
            symbol.SymbolId,
            direction,
            leg.Volume,
            leg.LimitPrice,
            decimal.ToInt64(distance * 100_000m),
            options.Label,
            comment,
            legClientOrderId
          ),
          brokerCts.Token
        );
        placed.Add(orderId);
      }
    }
    catch (Exception exception)
      when (exception is not BrokerOutcomeUnknownException
        and not CandidateLeaseLostException
        && (exception is not OperationCanceledException || BrokerOwnershipCancelled()))
    {
      // A failed leg does not prove the request never arrived, so rollback is
      // restricted to order IDs the broker confirmed and the group plan is
      // retained for reconciliation either way.
      await RollbackZoneFillAsync(
        candidate.CandidateId,
        placed,
        cancellationToken
      );
      throw ClassifyBrokerUncertainty(candidate, legClientOrderId, exception);
    }
    await CompleteActiveCandidateAsync(
      $"ordered:{string.Join(',', placed)}",
      cancellationToken
    );
    await PublishAsync(
      "order_accepted",
      $"broker accepted {placed.Count} zone-fill limit order(s)",
      cancellationToken,
      candidate.CandidateId,
      volume: sizing.Volume,
      groupId: groupId,
      setup: candidate.Setup,
      direction: candidate.Direction,
      matchId: candidate.MatchId,
      rangeId: candidate.RangeId,
      strategyFamily: candidate.StrategyFamily,
      pendingOrderIds: placed
    );
    await store.IncrementDailyTradeCountAsync(date, cancellationToken);
    await PublishAsync(
      "zone_planned",
      $"zone fill · {sizing.Lots:N2} lots · limits "
        + string.Join(" / ", plan.Legs.Select(leg =>
          $"{leg.LimitPrice:N2} ({leg.Volume / (decimal)symbol.LotSize:N2})"
        ))
        + $" · SL {plan.StopLoss:N2} · midpoint TTL "
        + $"{options.ZoneFillTtlBars} bars · {sizing.BindingTerm} · route={geometry}",
      cancellationToken,
      candidate.CandidateId,
      volume: sizing.Volume,
      price: proximal.Value,
      groupId: groupId,
      trancheIndex: 1,
      groupWorstCase: -sizing.Lots * zoneStopPlan.StopPips
        * options.PipValuePerLot,
      riskBudget: sizing.Budget,
      hadAdds: false,
      setup: candidate.Setup,
      direction: candidate.Direction,
      matchId: candidate.MatchId,
      rangeId: candidate.RangeId,
      strategyFamily: candidate.StrategyFamily,
      pendingOrderIds: placed
    );
    await ReconcileAsync(cancellationToken);
    return true;
  }

  // A late zone-fill fallback changes the route to a single market entry, so
  // the stop contract is revalidated at the new planned entry instead of
  // reusing a plan priced against the abandoned limit geometry.
  private async Task<bool> FallBackToSingleEntryAsync(
    TradeCandidate candidate,
    TradingAccountSnapshot account,
    TradeDirection direction,
    decimal expectedEntry,
    SymbolInfo symbol,
    DateOnly date,
    string routingReason,
    CancellationToken cancellationToken
  )
  {
    StructureStopPlan fallbackStopPlan;
    try
    {
      fallbackStopPlan = StructureStop(
        candidate,
        direction,
        expectedEntry,
        symbol,
        ExecutionRoute.Market
      );
    }
    catch (VolumePlanningException exception)
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "final_stop_entry_route_mismatch",
        cancellationToken
      );
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    await ObserveMarketStopContractRecomputeAsync(
      candidate,
      ExecutionRoute.Market,
      expectedEntry,
      cancellationToken
    );
    return await ProcessSingleInitialAsync(
      candidate,
      account,
      direction,
      expectedEntry,
      fallbackStopPlan,
      date,
      routingReason,
      cancellationToken
    );
  }

  private static string ClassifyEntryGeometry(
    TradeCandidateZone zone,
    TradeDirection direction,
    decimal expectedEntry
  )
  {
    if (zone.High <= zone.Low)
    {
      return "stale_zone";
    }
    if (direction == TradeDirection.Buy)
    {
      // BUY LIMIT needs at least some zone mass at/below ask.
      if (zone.Low > expectedEntry)
      {
        return "price_beyond_zone";
      }
      if (zone.High <= expectedEntry)
      {
        return expectedEntry < zone.Low ? "price_before_zone" : "valid_limit_side";
      }
      return "price_inside_zone";
    }
    // SELL LIMIT needs at least some zone mass at/above bid.
    if (zone.High < expectedEntry)
    {
      return "price_beyond_zone";
    }
    if (zone.Low >= expectedEntry)
    {
      return "valid_limit_side";
    }
    return "price_inside_zone";
  }

  private static decimal? SelectValidSideProximal(
    TradeCandidateZone zone,
    TradeDirection direction,
    decimal expectedEntry,
    string geometry,
    bool insideZoneMarketEntryEnabled
  )
  {
    if (geometry is "stale_zone" or "price_beyond_zone")
    {
      return null;
    }
    // Prefer a single market/limit entry when price is already inside the
    // published zone — zone-fill's classic distal edge is the wrong side.
    if (geometry == "price_inside_zone" && insideZoneMarketEntryEnabled)
    {
      return null;
    }
    if (direction == TradeDirection.Buy)
    {
      if (zone.High <= expectedEntry)
      {
        return zone.High;
      }
      var remaining = expectedEntry - zone.Low;
      if (remaining >= (zone.High - zone.Low) * 0.35m)
      {
        return expectedEntry;
      }
      return null;
    }
    if (zone.Low >= expectedEntry)
    {
      return zone.Low;
    }
    var sellRemaining = zone.High - expectedEntry;
    if (sellRemaining >= (zone.High - zone.Low) * 0.35m)
    {
      return expectedEntry;
    }
    return null;
  }

  // Removed erroneous static options hook.

  private static TradeCandidateZone SliceValidSideZone(
    TradeCandidateZone zone,
    TradeDirection direction,
    decimal expectedEntry,
    decimal proximal
  )
  {
    if (direction == TradeDirection.Buy)
    {
      var high = Math.Min(zone.High, expectedEntry);
      var low = Math.Min(zone.Low, high);
      if (high <= low)
      {
        return new TradeCandidateZone(proximal, proximal);
      }
      return new TradeCandidateZone(low, high);
    }
    var sellLow = Math.Max(zone.Low, expectedEntry);
    var sellHigh = Math.Max(zone.High, sellLow);
    if (sellHigh <= sellLow)
    {
      return new TradeCandidateZone(proximal, proximal);
    }
    return new TradeCandidateZone(sellLow, sellHigh);
  }

  // Rollback is a broker mutation and therefore fenced: a stale executor must
  // never cancel orders a successor or adopter now owns. Only order IDs the
  // broker confirmed are cancelled - never a speculative ID whose acceptance
  // was never acknowledged.
  private async Task RollbackZoneFillAsync(
    string candidateId,
    IReadOnlyList<long> placedOrderIds,
    CancellationToken cancellationToken
  )
  {
    if (!await StillOwnsCandidateAsync(cancellationToken))
    {
      await store.IncrementMetricAsync(
        CandidateSymbolHint(),
        "executor_stale_release_blocked",
        cancellationToken
      );
      _log(
        $"auto-trade zone-fill rollback skipped for {Short(candidateId)}: "
        + "lease no longer owned"
      );
      return;
    }
    var client = RequireClient();
    foreach (var orderId in placedOrderIds)
    {
      try
      {
        await client.CancelPendingOrderAsync(orderId, cancellationToken);
      }
      catch (Exception exception) when (exception is not OperationCanceledException)
      {
        // Cancellation is unverified, so the leg may still be live. Leave the
        // candidate recovery-required rather than reporting a clean rollback.
        _log($"auto-trade zone-fill rollback cancel failed order={orderId}: "
          + exception.Message);
        throw new BrokerOutcomeUnknownException(
          candidateId,
          orderId.ToString(CultureInfo.InvariantCulture),
          exception
        );
      }
    }
    var positions = await client.ReconcilePositionsAsync(cancellationToken);
    foreach (var position in positions.Where(position => (
      position.SymbolId == RequireSymbol().SymbolId
      && position.Label == options.Label
      && position.Comment.Contains(
        CandidateToken(candidateId),
        StringComparison.Ordinal
      )
    )))
    {
      try
      {
        await client.ClosePositionAsync(
          position.PositionId,
          position.Volume,
          cancellationToken
        );
      }
      catch (Exception exception) when (exception is not OperationCanceledException)
      {
        _log($"auto-trade zone-fill rollback close failed position="
          + $"{position.PositionId}: {exception.Message}");
        throw;
      }
    }
  }

  /// <summary>
  /// Three entry-leg prices spanning the owner's zone: Shallow (near edge,
  /// most likely to fill), Mid (midpoint), Deep (far edge, best price,
  /// least likely to fill).
  ///
  /// A real typed range (zone.Low != zone.High) is used directly - Shallow
  /// is whichever edge is closer to a profitable fill (High for BUY, Low
  /// for SELL), Deep the opposite edge.
  ///
  /// A degenerate/single-price zone (zone.Low == zone.High, e.g. the owner
  /// typed one number twice) has no real span to split, so Deep is derived
  /// as the midpoint between the typed price and the stop loss - confirmed
  /// against a live example (BUY 4390, SL 4384 -> Deep 4387, Mid 4388.5).
  /// This never places a leg past the halfway point to the stop.
  /// </summary>
  private static (decimal Shallow, decimal Mid, decimal Deep) ManualEntryLegPrices(
    TradeCandidateZone zone,
    TradeDirection direction,
    decimal manualStopLoss,
    SymbolInfo symbol
  )
  {
    decimal shallow;
    decimal deep;
    if (zone.Low != zone.High)
    {
      shallow = direction == TradeDirection.Buy ? zone.High : zone.Low;
      deep = direction == TradeDirection.Buy ? zone.Low : zone.High;
    }
    else
    {
      shallow = zone.Low;
      deep = shallow + (manualStopLoss - shallow) / 2m;
    }
    var mid = shallow + (deep - shallow) / 2m;
    return (
      decimal.Round(shallow, symbol.Digits, MidpointRounding.AwayFromZero),
      decimal.Round(mid, symbol.Digits, MidpointRounding.AwayFromZero),
      decimal.Round(deep, symbol.Digits, MidpointRounding.AwayFromZero)
    );
  }

  // Owner /algo instructions have their own execution route. Autonomous
  // selection, zone, regime, bias and scale-in policy must never alter them.
  private async Task<bool> ProcessManualAlgoAsync(
    TradeCandidate candidate,
    TradingAccountSnapshot account,
    TradeDirection direction,
    decimal expectedEntry,
    DateOnly date,
    CancellationToken cancellationToken
  )
  {
    var symbol = RequireSymbol();
    var zone = candidate.EntryZone;
    var manualStopLoss = candidate.ManualStopLoss
      ?? throw new VolumePlanningException("manual algo candidate has no stop loss");
    var legPrices = ManualEntryLegPrices(zone, direction, manualStopLoss, symbol);
    var targetPrices = candidate.ManualTakeProfits!;
    // Validate against Shallow (the worst-case/most-likely-to-fill entry) -
    // if targets are profitable and correctly ordered relative to the worst
    // entry, they are automatically profitable relative to Mid/Deep too.
    var priceValidation = ValidateManualPrices(
      candidate,
      direction,
      legPrices.Shallow,
      symbol
    );
    if (priceValidation is not null)
    {
      return await RejectAsync(candidate, priceValidation, cancellationToken);
    }
    StructureStopPlan manualStopPlan;
    try
    {
      manualStopPlan = ManualStop(candidate, direction, legPrices.Shallow, symbol);
    }
    catch (VolumePlanningException exception)
    {
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    var targetsPips = targetPrices
      .Select(price => decimal.ToInt32(decimal.Round(
        decimal.Abs(price - legPrices.Shallow) / options.PipSize,
        0,
        MidpointRounding.AwayFromZero
      )))
      .ToArray();
    var targetWeights = EqualWeights(targetsPips.Length);
    InitialSizingResult sizing;
    try
    {
      sizing = VolumePlanner.SizeInitial(
        account.Balance,
        options.RiskPercent,
        options.SizingMode,
        manualStopPlan.StopPips,
        options.PipValuePerLot,
        symbol,
        targetsPips,
        targetWeights
      );
    }
    catch (VolumePlanningException exception)
    {
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    // Split the sized total across three entry legs (2026-08 R:R redesign):
    // Shallow 50% / Mid 30% / Deep 20%. SplitEntryVolume already collapses
    // to a single slice when the total can't support three broker-minimum
    // legs, but a slice that individually clears MinVolume can still be too
    // small for BuildTargetPlan's own "at least two broker-valid exits"
    // requirement (a small deep 20% leg, in particular) - fail closed to
    // the original single-leg-at-Shallow behavior rather than losing the
    // candidate to an unhandled exception.
    IReadOnlyList<long> legVolumes;
    IReadOnlyList<decimal> legEntryPrices;
    TargetVolumePlan[] legTargetPlans;
    try
    {
      var splitVolumes = VolumePlanner.SplitEntryVolume(
        sizing.Volume, symbol, ManualEntryLegRatios
      );
      var splitPrices = splitVolumes.Count == 1
        ? new[] { legPrices.Shallow }
        : new[] { legPrices.Shallow, legPrices.Mid, legPrices.Deep };
      var splitTargetPlans = new TargetVolumePlan[splitVolumes.Count];
      for (var index = 0; index < splitVolumes.Count; index++)
      {
        var legTargetPlan = VolumePlanner.BuildTargetPlan(
          splitVolumes[index], symbol, targetsPips, targetWeights
        );
        var legLots = splitVolumes[index] / (decimal)symbol.LotSize;
        if (legLots > ManualAlgoFirstLegThresholdLots)
        {
          var fixedFirstLeg = VolumePlanner.VolumeForLots(ManualAlgoFirstLegLots, symbol);
          legTargetPlan = VolumePlanner.FixFirstLegVolume(
            legTargetPlan, splitVolumes[index], fixedFirstLeg, symbol
          );
        }
        splitTargetPlans[index] = legTargetPlan;
      }
      legVolumes = splitVolumes;
      legEntryPrices = splitPrices;
      legTargetPlans = splitTargetPlans;
    }
    catch (VolumePlanningException)
    {
      var fallbackTargetPlan = sizing.TargetPlan;
      if (sizing.Lots > ManualAlgoFirstLegThresholdLots)
      {
        var fixedFirstLeg = VolumePlanner.VolumeForLots(ManualAlgoFirstLegLots, symbol);
        fallbackTargetPlan = VolumePlanner.FixFirstLegVolume(
          fallbackTargetPlan, sizing.Volume, fixedFirstLeg, symbol
        );
      }
      legVolumes = [sizing.Volume];
      legEntryPrices = [legPrices.Shallow];
      legTargetPlans = [fallbackTargetPlan];
    }
    var legCount = legVolumes.Count;
    var groupId = CandidateGroupId(candidate);
    var barTs = candidate.BarTs ?? candidate.CreatedAt;
    var expiresAt = candidate.ManualExpiresAt ?? 0;
    if (options.DryRun)
    {
      var legSummary = string.Join(
        " / ",
        legEntryPrices.Select((price, index) =>
          $"{legVolumes[index] / (decimal)symbol.LotSize:N2}@{price:N2}"
        )
      );
      return await CompleteDryRunAsync(
        candidate,
        $"manual algo {direction} {sizing.Lots:N2} lots [{legSummary}] · "
          + $"SL {manualStopPlan.StopLoss:N2} · {sizing.BindingTerm}",
        sizing.Volume,
        legPrices.Shallow,
        cancellationToken,
        setup: candidate.Setup,
        direction: candidate.Direction,
        stopLoss: manualStopPlan.StopLoss,
        targetPrices: targetPrices,
        stream: "algo_manual"
      );
    }
    if (await store.IsPausedAsync(cancellationToken))
    {
      return await RejectAsync(candidate, "executor paused", cancellationToken);
    }
    await ReconcileAsync(cancellationToken);
    if (
      !_accountSupportsHedging
      && (
        _allSymbolPositions.Any(position => position.Direction != direction)
        || _allSymbolPendingOrders.Any(order => order.Direction != direction)
      )
    )
    {
      return await RejectAsync(
        candidate,
        "broker_account_not_hedged_for_opposite_manual_order",
        cancellationToken
      );
    }
    var clientOrderIds = Enumerable.Range(1, legCount)
      .Select(legIndex => ClientOrderId(candidate.CandidateId, legIndex))
      .ToArray();
    await SaveGroupPlanAsync(
      candidate,
      groupId,
      cancellationToken,
      streamEventId: _activeLease?.StreamEventId,
      route: "manual_limit",
      clientOrderIds: clientOrderIds
    );
    if (!await EnsureBrokerLeaseAsync(cancellationToken))
    {
      throw new CandidateLeaseLostException(candidate.CandidateId);
    }
    var orderIds = new List<long>(legCount);
    for (var index = 0; index < legCount; index++)
    {
      var legIndex = index + 1;
      var comment = BuildManualComment(
        candidate.CandidateId,
        groupId,
        legVolumes[index],
        legTargetPlans[index].Slices,
        legTargetPlans[index].TargetsPips,
        legTargetPlans[index].TargetOrdinals,
        barTs,
        expiresAt,
        legIndex,
        legCount
      );
      long orderId;
      using var brokerCts = CreateBrokerCancellation(cancellationToken);
      try
      {
        orderId = await RequireClient().PlaceLimitOrderAsync(
          new LimitOrderRequest(
            symbol.SymbolId,
            direction,
            legVolumes[index],
            legEntryPrices[index],
            decimal.ToInt64(manualStopPlan.Distance * 100_000m),
            options.Label,
            comment,
            clientOrderIds[index]
          ),
          brokerCts.Token
        );
      }
      catch (Exception exception) when (
        exception is not OperationCanceledException
        || BrokerOwnershipCancelled()
      )
      {
        throw ClassifyBrokerUncertainty(candidate, clientOrderIds[index], exception);
      }
      orderIds.Add(orderId);
      await PublishAsync(
        "manual_limit_placed",
        $"manual algo {direction} limit leg {legIndex}/{legCount} "
          + $"{legVolumes[index] / (decimal)symbol.LotSize:N2} lots "
          + $"@ {legEntryPrices[index]:N2} · SL {manualStopPlan.StopLoss:N2} · "
          + $"{sizing.BindingTerm}",
        cancellationToken,
        candidate.CandidateId,
        volume: legVolumes[index],
        price: legEntryPrices[index],
        groupId: groupId,
        trancheIndex: legIndex,
        groupWorstCase: -sizing.Lots * manualStopPlan.StopPips
          * options.PipValuePerLot,
        riskBudget: sizing.Budget,
        hadAdds: false,
        setup: candidate.Setup,
        stopPips: manualStopPlan.StopPips,
        targetsPips: legTargetPlans[index].TargetsPips,
        stream: "algo_manual",
        direction: candidate.Direction,
        orderId: orderId,
        stopLoss: manualStopPlan.StopLoss,
        targetPrices: targetPrices,
        entryLow: candidate.EntryZone.Low,
        entryHigh: candidate.EntryZone.High
      );
    }
    await CompleteActiveCandidateAsync(
      $"ordered:{string.Join(',', orderIds)}",
      cancellationToken
    );
    await store.IncrementDailyTradeCountAsync(date, cancellationToken);
    return true;
  }

  private async Task<bool> ProcessAddAsync(
    TradeCandidate candidate,
    TradingAccountSnapshot account,
    TradeDirection direction,
    decimal expectedEntry,
    StructureStopPlan stopPlan,
    SpotPrice quote,
    IReadOnlyList<AutoTradePositionState> group,
    DateOnly date,
    CancellationToken cancellationToken
  )
  {
    if (!string.Equals(candidate.Regime, "trend", StringComparison.OrdinalIgnoreCase))
    {
      return await RejectAsync(
        candidate,
        "scale-in adds are restricted to the trend regime",
        cancellationToken
      );
    }
    var symbol = RequireSymbol();
    var triggerResult = ValidateAddTriggers(
      candidate,
      direction,
      expectedEntry,
      quote,
      group,
      symbol
    );
    if (!triggerResult.Accepted)
    {
      await store.IncrementAddRejectAsync(
        candidate.Symbol,
        triggerResult.Mode ?? "shared",
        triggerResult.Condition ?? "unknown",
        cancellationToken
      );
      return await RejectAsync(
        candidate,
        triggerResult.RejectReason ?? "add rejected",
        cancellationToken
      );
    }
    var mode = triggerResult.Mode!;
    // Momentum's stop guard was deferred here (see ProcessCandidateAsync) so
    // a pullback candidate never gets killed by a guard check against the
    // wrong (structure) stop; pullback computes an entirely different stop
    // (P5) instead of reusing the structure one at all.
    StructureStopPlan rawStopPlan;
    if (mode == "add_pullback")
    {
      try
      {
        rawStopPlan = PullbackAddStop(candidate, direction, expectedEntry, symbol);
      }
      catch (VolumePlanningException exception)
      {
        await store.IncrementAddRejectAsync(
          candidate.Symbol, mode, "stop_exceeds_envelope", cancellationToken
        );
        return await RejectAsync(candidate, exception.Message, cancellationToken);
      }
    }
    else
    {
      rawStopPlan = stopPlan;
    }
    stopPlan = rawStopPlan;
    var groupBooked = GroupBookedPnl(group);
    var initialTrancheLots = InitialTrancheVolume(group) / (decimal)symbol.LotSize;
    var decision = ScaleInPlanner.Plan(
      account.Balance,
      options.RiskPercent,
      options.PipValuePerLot,
      options.AddRiskFraction,
      stopPlan.StopPips,
      groupBooked,
      group.Select(state => new TrancheExposure(
        state.Direction,
        state.EntryPrice,
        state.CurrentStopLoss!.Value,
        state.RemainingVolume
      )).ToArray(),
      options.AddRequireRiskFree,
      options.PipSize,
      symbol,
      options.TargetsPips,
      options.TargetWeights,
      initialTrancheLots,
      options.AddSizeRatio
    );
    if (!decision.Allowed || decision.TargetPlan is null)
    {
      await store.IncrementAddRejectAsync(
        candidate.Symbol, mode, "sizing_infeasible", cancellationToken
      );
      return await RejectAsync(candidate, decision.Reason, cancellationToken);
    }
    // P6 (pullback only) - the guard that matters most: the initial
    // tranche's stop may sit in profit while the add's does not, and both
    // can stop out on the same move. Momentum keeps its existing
    // budget-based worst-case check inside ScaleInPlanner.Plan unchanged.
    if (mode == "add_pullback" && decision.PostAddWorstCase < 0)
    {
      var worstCaseLossPct = -decision.PostAddWorstCase / account.Balance * 100m;
      if (worstCaseLossPct > options.AddMaxGroupRiskPct)
      {
        await store.IncrementAddRejectAsync(
          candidate.Symbol, mode, "group_worst_case_exceeded", cancellationToken
        );
        return await RejectAsync(
          candidate,
          $"pullback add rejected: combined group worst case "
            + $"{worstCaseLossPct:0.##}% exceeds max "
            + $"{options.AddMaxGroupRiskPct:0.##}% of balance",
          cancellationToken
        );
      }
    }
    _log(decision.SizingLog);
    var groupId = GroupId(group[0]);
    var trancheIndex = group.Max(state => state.TrancheIndex) + 1;
    var barTs = candidate.BarTs ?? candidate.CreatedAt;
    if (options.DryRun)
    {
      return await CompleteDryRunAsync(
        candidate,
        $"Tranche {trancheIndex} · {mode} · {decision.Lots:N2} lots · "
        + $"{decision.BindingTerm} · group worst "
        + $"${decision.PostAddWorstCase:N1}",
        decision.Volume,
        expectedEntry,
        cancellationToken
      );
    }
    if (await store.IsPausedAsync(cancellationToken))
    {
      return await RejectAsync(candidate, "executor paused", cancellationToken);
    }
    await ReconcileAsync(cancellationToken);
    var refreshed = _states.Values
      .Where(state => GroupId(state) == groupId)
      .ToArray();
    if (refreshed.Length != group.Count)
    {
      return await RejectAsync(
        candidate,
        "tranche group changed before add order",
        cancellationToken
      );
    }
    return await PlaceTrancheAsync(
      candidate,
      direction,
      expectedEntry,
      stopPlan,
      decision.Volume,
      decision.TargetPlan,
      groupId,
      trancheIndex,
      groupBooked,
      InitialBookedPnl(group),
      GroupOpenedAt(group),
      barTs,
      Math.Max(group.Max(state => state.GroupTrancheCount), trancheIndex),
      hadAdds: true,
      groupRealizedPipVolume: GroupRealizedPipVolume(group),
      initialRealizedPipVolume: InitialRealizedPipVolume(group),
      groupInitialVolume: GroupInitialVolume(group) + decision.Volume,
      initialTrancheVolume: InitialTrancheVolume(group),
      date,
      eventType: "add",
      message: $"➕ Tranche {trancheIndex} · {mode} · {decision.Lots:N2} lots · "
        + $"stop {stopPlan.StopPips:N0}p "
        + (mode == "add_pullback" ? "(retrace)" : "(structure)") + " · "
        + $"{decision.BindingTerm} · group worst "
        + $"${decision.PostAddWorstCase:N1} / budget ${decision.Budget:N0}",
      groupWorstCase: decision.PostAddWorstCase,
      riskBudget: decision.Budget,
      cancellationToken,
      addMode: mode
    );
  }

  private async Task<bool> PlaceTrancheAsync(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal expectedEntry,
    StructureStopPlan stopPlan,
    long volume,
    TargetVolumePlan targetPlan,
    string groupId,
    int trancheIndex,
    decimal groupBookedPnl,
    decimal initialBookedPnl,
    long groupOpenedAt,
    long lastTrancheBarTs,
    int groupTrancheCount,
    bool hadAdds,
    decimal groupRealizedPipVolume,
    decimal initialRealizedPipVolume,
    long groupInitialVolume,
    long initialTrancheVolume,
    DateOnly date,
    string eventType,
    string message,
    decimal groupWorstCase,
    decimal riskBudget,
    CancellationToken cancellationToken,
    // Trigger mode for a scale-in tranche ("add_momentum"/"add_pullback") -
    // null for the initial tranche. Folded into Setup (not a new column)
    // so it rides the existing attribution pipeline (auto_trade_fills.
    // setup_type, delivery.py's attribution line, stats streams) the same
    // way box-scalp's "counter_bias" tag already does, and is independently
    // measurable per mode without a schema change.
    string? addMode = null
  )
  {
    var client = RequireClient();
    var now = _clock().ToUnixTimeSeconds();
    var symbol = RequireSymbol();
    var effectiveSetup = addMode is null
      ? candidate.Setup
      : $"{candidate.Setup} · {addMode}";
    var comment = BuildComment(
      candidate.CandidateId,
      groupId,
      trancheIndex,
      volume,
      targetPlan.Slices,
      targetPlan.TargetsPips,
      targetPlan.TargetOrdinals,
      lastTrancheBarTs
    );
    await PublishAsync(
      "order_planned",
      $"{effectiveSetup} {direction} tranche {trancheIndex} planned",
      cancellationToken,
      candidate.CandidateId,
      volume: volume,
      groupId: groupId,
      trancheIndex: trancheIndex,
      setup: effectiveSetup,
      regime: candidate.Regime,
      confluence: candidate.Confluence,
      stopPips: stopPlan.StopPips,
      targetsPips: targetPlan.TargetsPips,
      direction: DirectionLabel(direction),
      matchId: candidate.MatchId,
      rangeId: candidate.RangeId,
      strategyFamily: candidate.StrategyFamily,
      riskMultiplier: trancheIndex == 1 ? candidate.RiskMultiplier : null,
      targetModel: candidate.TargetModel,
      entryDistribution: candidate.EntryDistribution
    );
    await PublishAsync(
      "order_submitted",
      $"{effectiveSetup} {direction} tranche {trancheIndex} submitted",
      cancellationToken,
      candidate.CandidateId,
      volume: volume,
      groupId: groupId,
      trancheIndex: trancheIndex,
      setup: effectiveSetup,
      direction: DirectionLabel(direction),
      matchId: candidate.MatchId,
      rangeId: candidate.RangeId,
      strategyFamily: candidate.StrategyFamily,
      riskMultiplier: trancheIndex == 1 ? candidate.RiskMultiplier : null,
      targetModel: candidate.TargetModel,
      entryDistribution: candidate.EntryDistribution
    );
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "order_submitted",
      cancellationToken
    );
    var clientOrderId = ClientOrderId(candidate.CandidateId);
    if (trancheIndex <= 1)
    {
      // Market submissions persist their resolved route and exact client
      // order ID before the first broker side effect, exactly like the limit
      // routes. Scale-in tranches reuse the initial tranche's identity and
      // must not overwrite an existing plan.
      await SaveGroupPlanAsync(
        candidate,
        groupId,
        cancellationToken,
        streamEventId: _activeLease?.StreamEventId,
        route: "market",
        clientOrderIds: [clientOrderId]
      );
    }
    if (!await EnsureBrokerLeaseAsync(cancellationToken))
    {
      throw new CandidateLeaseLostException(candidate.CandidateId);
    }
    TradeExecution execution;
    using var brokerCts = CreateBrokerCancellation(cancellationToken);
    try
    {
      execution = await client.PlaceMarketOrderAsync(
        new MarketOrderRequest(
          RequireSymbol().SymbolId,
          direction,
          volume,
          // The broker request carries a distance because its API is
          // relative; it is derived from the planned entry to the exact
          // approved absolute stop and amended to that absolute price after
          // the fill.
          decimal.ToInt64(stopPlan.Distance * 100_000m),
          options.Label,
          comment,
          clientOrderId
        ),
        brokerCts.Token
      );
    }
    catch (Exception exception) when (
      exception is not OperationCanceledException
      || BrokerOwnershipCancelled()
    )
    {
      throw ClassifyBrokerUncertainty(candidate, clientOrderId, exception);
    }
    await PublishAsync(
      "order_accepted",
      $"broker accepted order {execution.OrderId}",
      cancellationToken,
      candidate.CandidateId,
      execution.PositionId,
      volume: execution.ExecutedVolume,
      price: execution.ExecutionPrice,
      groupId: groupId,
      trancheIndex: trancheIndex,
      setup: effectiveSetup,
      direction: DirectionLabel(direction),
      matchId: candidate.MatchId,
      rangeId: candidate.RangeId,
      strategyFamily: candidate.StrategyFamily,
      riskMultiplier: trancheIndex == 1 ? candidate.RiskMultiplier : null,
      targetModel: candidate.TargetModel,
      entryDistribution: candidate.EntryDistribution
    );
    var fill = execution.ExecutionPrice > 0
      ? execution.ExecutionPrice
      : expectedEntry;
    // Fill slippage never moves the approved stop. The absolute price validated
    // against the Python contract is the price the broker must hold, so the
    // amendment sends `stopPlan.StopLoss` rather than `fill ± distance`.
    var stopLoss = decimal.Round(
      stopPlan.StopLoss,
      symbol.Digits,
      MidpointRounding.AwayFromZero
    );
    try
    {
      await client.AmendPositionStopLossAsync(
        execution.PositionId,
        stopLoss,
        cancellationToken
      );
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "final_stop_absolute_applied",
        cancellationToken
      );
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      // The position exists but the protective stop is unconfirmed. Never
      // widen it silently and never retry the entry.
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "final_stop_amendment_unknown",
        cancellationToken
      );
      throw new BrokerOutcomeUnknownException(
        candidate.CandidateId,
        ClientOrderId(candidate.CandidateId),
        exception
      );
    }
    // Observed risk uses the actual fill for telemetry only; the approved stop
    // is unchanged, so slippage shows up as a measured deviation instead of a
    // silently moved stop.
    var observedStopPips = Math.Abs(fill - stopLoss) / options.PipSize;
    if (Math.Abs(observedStopPips - stopPlan.StopPips) > 0.0001m)
    {
      await PublishAsync(
        "execution_slippage",
        $"fill {fill:N2} vs planned entry {expectedEntry:N2}; approved stop "
        + $"{stopLoss:N2} held at {observedStopPips:N1}p observed risk "
        + $"({stopPlan.StopPips:N1}p planned)",
        cancellationToken,
        candidate.CandidateId,
        execution.PositionId,
        price: fill,
        groupId: groupId,
        stopPips: observedStopPips
      );
    }
    IReadOnlyList<decimal>? targetPrices = null;
    if (
      IsBoxRangeScalp(candidate)
      && targetPlan.TargetsPips.Count >= 2
      && !options.RangeFlipEnabled
    )
    {
      targetPrices = targetPlan.TargetsPips
        .Select(pips => decimal.Round(
          direction == TradeDirection.Buy
            ? fill + pips * options.PipSize
            : fill - pips * options.PipSize,
          symbol.Digits,
          MidpointRounding.AwayFromZero
        ))
        .ToArray();
    }
    else if (UsesCandidateTargetPlan(candidate))
    {
      targetPrices = BuildAutonomousTargetPrices(
        candidate,
        direction,
        fill,
        targetPlan.TargetsPips
      );
    }
    var state = new AutoTradePositionState(
      candidate.CandidateId,
      execution.PositionId,
      symbol.SymbolId,
      direction,
      fill,
      volume,
      volume,
      targetPlan.Slices,
      targetPlan.TargetsPips,
      0,
      now,
      stopLoss,
      targetPlan.TargetOrdinals,
      groupId,
      trancheIndex,
      groupBookedPnl,
      initialBookedPnl,
      groupOpenedAt,
      lastTrancheBarTs,
      groupTrancheCount,
      hadAdds,
      stopLoss,
      ZoneLeg: 0,
      groupRealizedPipVolume,
      initialRealizedPipVolume,
      groupInitialVolume,
      initialTrancheVolume,
      Setup: effectiveSetup,
      Regime: candidate.Regime,
      Confluence: candidate.Confluence,
      RangeId: candidate.RangeId,
      RangeLow: candidate.RangeLow,
      RangeHigh: candidate.RangeHigh,
      RangeExitPrice: IsBoxRangeScalp(candidate) && targetPlan.TargetsPips.Count < 2
        ? BoxExitPrice(candidate, direction)
        : null,
      Stream: "algo_auto",
      MatchId: candidate.MatchId,
      StrategyFamily: string.IsNullOrWhiteSpace(candidate.StrategyFamily)
        ? StrategyFamilyFromSetup(candidate.Setup)
        : candidate.StrategyFamily,
      TargetPrices: targetPrices,
      ZoneId: candidate.ZoneId,
      TriggerId: candidate.TriggerId,
      ParentGroupId: candidate.ParentGroupId,
      StructuralSource: candidate.StructuralSource,
      ReactionId: candidate.ReactionId,
      ThesisId: candidate.ThesisId,
      StructuralZoneId: candidate.StructuralZoneId,
      StructuralZoneLow: candidate.StructuralZoneLow,
      StructuralZoneHigh: candidate.StructuralZoneHigh,
      RiskMultiplier: candidate.RiskMultiplier,
      TargetModel: candidate.TargetModel,
      AbsoluteTargetPrice: candidate.AbsoluteTargetPrice,
      FillSourceQuoteTimestamp: _lastSpot?.Timestamp ?? now,
      FillSourceQuoteSequence: _spotSequence,
      Symbol: symbol.RedisSymbol
    );
    if (
      _states.TryGetValue(state.PositionId, out var existingState)
      && GroupId(existingState) != GroupId(state)
    )
    {
      // The broker returned an already-tracked PositionId for what should
      // be a distinct independent group - this must never silently become
      // a scale-in of the existing group, and the existing group's state
      // must not be overwritten. The broker fill already happened and
      // cannot be undone here; all that can be done is preserve what is
      // already tracked, refuse to claim the new fill is safely managed,
      // and stop admitting further autonomous initial groups until a human
      // reconciles the conflict.
      _positionIdentityConflict = true;
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "position_state_conflict",
        cancellationToken
      );
      _log(
        "auto-trade broker_position_identity_group_conflict "
          + $"position_id={state.PositionId} "
          + $"existing_group_id={GroupId(existingState)} "
          + $"incoming_group_id={GroupId(state)} "
          + $"incoming_candidate_id={Short(candidate.CandidateId)}"
      );
      await PublishAsync(
        "error",
        $"broker_position_identity_group_conflict: position {state.PositionId} "
          + $"is already tracked under group {GroupId(existingState)}; cannot "
          + $"adopt the new fill for group {GroupId(state)}",
        cancellationToken,
        candidate.CandidateId,
        state.PositionId,
        groupId: GroupId(state),
        reasonCode: "broker_position_identity_group_conflict"
      );
      return true;
    }
    _states[state.PositionId] = state;
    await PropagateGroupMetadataAsync(state, cancellationToken);
    await store.SavePositionAsync(state, cancellationToken);
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "order_filled",
      cancellationToken
    );
    await RecordRangeExecutionMetricsAsync(
      candidate,
      direction,
      cancellationToken
    );
    await CompleteActiveCandidateAsync(
      $"ordered:{state.PositionId}",
      cancellationToken
    );
    await store.IncrementDailyTradeCountAsync(date, cancellationToken);
    var rendered = message
      .Replace("{fill}", fill.ToString("N2", CultureInfo.InvariantCulture))
      .Replace("{stop}", stopLoss.ToString("N2", CultureInfo.InvariantCulture));
    await PublishAsync(
      eventType,
      rendered,
      cancellationToken,
      candidate.CandidateId,
      state.PositionId,
      volume: volume,
      price: fill,
      groupId: groupId,
      trancheIndex: trancheIndex,
      groupWorstCase: groupWorstCase,
      riskBudget: riskBudget,
      hadAdds: hadAdds,
      setup: effectiveSetup,
      regime: candidate.Regime,
      confluence: candidate.Confluence,
      stopPips: stopPlan.StopPips,
      targetsPips: targetPlan.TargetsPips,
      stream: state.Stream,
      direction: DirectionLabel(direction),
      matchId: candidate.MatchId,
      rangeId: candidate.RangeId,
      strategyFamily: state.StrategyFamily,
      riskMultiplier: trancheIndex == 1 ? candidate.RiskMultiplier : null,
      targetModel: candidate.TargetModel,
      entryDistribution: candidate.EntryDistribution
    );
    await PublishAsync(
      "managing",
      $"{effectiveSetup} {DirectionLabel(direction)} is under group management",
      cancellationToken,
      candidate.CandidateId,
      state.PositionId,
      groupId: groupId,
      trancheIndex: trancheIndex,
      setup: effectiveSetup,
      regime: candidate.Regime,
      confluence: candidate.Confluence,
      direction: DirectionLabel(direction),
      matchId: candidate.MatchId,
      rangeId: candidate.RangeId,
      strategyFamily: state.StrategyFamily,
      riskMultiplier: trancheIndex == 1 ? candidate.RiskMultiplier : null,
      targetModel: candidate.TargetModel,
      entryDistribution: candidate.EntryDistribution
    );
    return true;
  }

  private StructureStopPlan StructureStop(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal entryPrice,
    SymbolInfo symbol,
    ExecutionRoute route
  )
  {
    if (candidate.Atr is not decimal atr || candidate.StructureSwing is not decimal swing)
    {
      throw new VolumePlanningException(
        "structure context unavailable on candidate"
      );
    }
    var (minimumStopPips, maximumStopPips) = StopPipsBounds(candidate);
    var plan = StructureStopPlanner.Plan(
      direction,
      entryPrice,
      swing,
      atr,
      options.AddStopBufferAtr,
      direction == TradeDirection.Buy ? candidate.SweepLow : candidate.SweepHigh,
      options.WickStopBufferAtr,
      minimumStopPips,
      maximumStopPips,
      options.PipSize,
      symbol,
      BuildOpposingZoneContext(candidate)
    );
    ValidateProtectiveStopContract(
      candidate,
      plan,
      entryPrice,
      symbol,
      route
    );
    return plan;
  }

  private void ValidateProtectiveStopContract(
    TradeCandidate candidate,
    StructureStopPlan executorPlan,
    decimal executorEntryPrice,
    SymbolInfo symbol,
    ExecutionRoute route
  )
  {
    // Candidate v5 is the first schema that requires the Python stop plan.
    // Older in-flight v3/v4 events remain executable during a rolling deploy.
    if (candidate.Version != 5 || IsManualAlgoCandidate(candidate))
    {
      return;
    }
    if (candidate.StopPlanVersion == 2)
    {
      ValidateFinalProtectiveStopContract(
        candidate,
        executorPlan,
        executorEntryPrice,
        symbol,
        route
      );
      return;
    }
    if (
      candidate.StopPlanVersion != 1
      || candidate.PlannedStopEntryPrice is not decimal plannedEntry
      || candidate.PlannedStopPrice is not decimal plannedStop
      || candidate.PlannedStopDistance is not decimal plannedDistance
      || candidate.PlannedStopPips is not decimal plannedPips
      || candidate.PlannedStopRawPrice is not decimal plannedRaw
      || candidate.PlannedStopClamped is not bool plannedClamped
      || candidate.StopSource is not string plannedSource
    )
    {
      throw new VolumePlanningException("protective_stop_contract_mismatch");
    }
    if (
      EntryIsResting(route)
        ? !PlansMatchWithinTolerance(
          plannedEntry,
          plannedStop,
          plannedDistance,
          plannedPips,
          plannedRaw,
          plannedClamped,
          plannedSource,
          executorPlan,
          executorEntryPrice,
          symbol
        )
        : !LegacyStructuralStopIdentityMatches(
          plannedStop,
          plannedRaw,
          plannedClamped,
          plannedSource,
          executorPlan,
          symbol
        )
    )
    {
      throw new VolumePlanningException("protective_stop_contract_mismatch");
    }
  }

  private void ValidateFinalProtectiveStopContract(
    TradeCandidate candidate,
    StructureStopPlan executorPlan,
    decimal executorEntryPrice,
    SymbolInfo symbol,
    ExecutionRoute route
  )
  {
    if (
      candidate.PlannedStopEntryPrice is not decimal plannedEntry
      || candidate.PlannedBaseStopPrice is not decimal plannedBaseStop
      || candidate.PlannedBaseStopPips is not decimal plannedBasePips
      || candidate.PlannedFinalStopPrice is not decimal plannedFinalStop
      || candidate.PlannedFinalStopDistance is not decimal plannedFinalDistance
      || candidate.PlannedFinalStopPips is not decimal plannedFinalPips
      || candidate.PlannedStopRawPrice is not decimal plannedRaw
      || candidate.PlannedStopClamped is not bool plannedClamped
      || candidate.StopSource is not string plannedSource
      || candidate.StopAdjustment is not string plannedAdjustment
    )
    {
      throw new VolumePlanningException("final_protective_stop_contract_mismatch");
    }
    if (!EntryIsResting(route))
    {
      if (!StructuralStopIdentityMatches(
        plannedRaw,
        plannedBaseStop,
        plannedClamped,
        plannedSource,
        plannedAdjustment,
        executorPlan,
        symbol
      ))
      {
        throw new VolumePlanningException(
          "final_protective_stop_contract_mismatch"
        );
      }
      if (!AdjustmentZoneMatches(candidate, executorPlan, symbol))
      {
        throw new VolumePlanningException("final_stop_zone_identity_mismatch");
      }
      return;
    }
    var recomputed = RecomputeStructureStopPlan(candidate, executorEntryPrice, symbol);
    if (
      !PlansMatchWithinTolerance(
        plannedEntry,
        plannedFinalStop,
        plannedFinalDistance,
        plannedFinalPips,
        plannedRaw,
        plannedClamped || plannedAdjustment == "opposing_zone_push",
        plannedSource,
        recomputed,
        executorEntryPrice,
        symbol
      )
      || !StructuralStopIdentityMatches(
        plannedRaw,
        plannedBaseStop,
        plannedClamped,
        plannedSource,
        plannedAdjustment,
        recomputed,
        symbol
      )
      || Math.Abs(plannedBasePips - (recomputed.BaseStopPips ?? recomputed.StopPips))
        > PipTolerance(recomputed, symbol)
    )
    {
      throw new VolumePlanningException("final_protective_stop_contract_mismatch");
    }
    if (!AdjustmentZoneMatches(candidate, recomputed, symbol))
    {
      throw new VolumePlanningException("final_stop_zone_identity_mismatch");
    }
    if (
      !PlansMatchWithinTolerance(
        plannedEntry,
        plannedFinalStop,
        plannedFinalDistance,
        plannedFinalPips,
        plannedRaw,
        plannedClamped || plannedAdjustment == "opposing_zone_push",
        plannedSource,
        executorPlan,
        executorEntryPrice,
        symbol
      )
      || !StructuralStopIdentityMatches(
        plannedRaw,
        plannedBaseStop,
        plannedClamped,
        plannedSource,
        plannedAdjustment,
        executorPlan,
        symbol
      )
      || Math.Abs((executorPlan.BaseStopPips ?? executorPlan.StopPips) - plannedBasePips)
        > PipTolerance(executorPlan, symbol)
    )
    {
      throw new VolumePlanningException("final_protective_stop_contract_mismatch");
    }
    if (!AdjustmentZoneMatches(candidate, executorPlan, symbol))
    {
      throw new VolumePlanningException("final_stop_zone_identity_mismatch");
    }
  }

  private async Task ObserveMarketStopContractRecomputeAsync(
    TradeCandidate candidate,
    ExecutionRoute route,
    decimal executorEntryPrice,
    CancellationToken cancellationToken
  )
  {
    if (
      route != ExecutionRoute.Market
      || candidate.Version != 5
      || IsManualAlgoCandidate(candidate)
      || candidate.PlannedStopEntryPrice is not decimal plannedEntry
    )
    {
      return;
    }
    var tolerance = Math.Max(0m, options.EntryContractTolerancePips)
      * options.PipSize;
    if (Math.Abs(plannedEntry - executorEntryPrice) <= tolerance)
    {
      return;
    }
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "stop_contract_market_entry_recomputed",
      cancellationToken
    );
  }

  private StructureStopPlan RecomputeStructureStopPlan(
    TradeCandidate candidate,
    decimal entryPrice,
    SymbolInfo symbol
  )
  {
    if (candidate.Atr is not decimal atr || candidate.StructureSwing is not decimal swing)
    {
      throw new VolumePlanningException(
        "structure context unavailable on candidate"
      );
    }
    var direction = ParseDirection(candidate.Direction);
    var (minimumStopPips, maximumStopPips) = StopPipsBounds(candidate);
    return StructureStopPlanner.Plan(
      direction,
      entryPrice,
      swing,
      atr,
      options.AddStopBufferAtr,
      direction == TradeDirection.Buy ? candidate.SweepLow : candidate.SweepHigh,
      options.WickStopBufferAtr,
      minimumStopPips,
      maximumStopPips,
      options.PipSize,
      symbol,
      BuildOpposingZoneContext(candidate)
    );
  }

  // A pushed stop must name the exact zone it was pushed beyond. Missing
  // identity on either side is a mismatch, never an implicit pass, and a
  // "none" contract can never validate against a pushed executor plan.
  private static bool AdjustmentZoneMatches(
    TradeCandidate candidate,
    StructureStopPlan plan,
    SymbolInfo symbol
  )
  {
    if (plan.Adjustment == "none")
    {
      return candidate.StopAdjustment == "none";
    }
    if (candidate.StopAdjustment != plan.Adjustment)
    {
      return false;
    }
    if (
      string.IsNullOrWhiteSpace(candidate.StopAdjustmentZoneId)
      || string.IsNullOrWhiteSpace(plan.AdjustmentZoneId)
    )
    {
      return false;
    }
    if (!string.Equals(
      candidate.StopAdjustmentZoneId,
      plan.AdjustmentZoneId,
      StringComparison.Ordinal
    ))
    {
      return false;
    }
    if (
      candidate.StopAdjustmentZoneLow is not decimal candidateLow
      || candidate.StopAdjustmentZoneHigh is not decimal candidateHigh
      || plan.AdjustmentZoneLow is not decimal planLow
      || plan.AdjustmentZoneHigh is not decimal planHigh
    )
    {
      return false;
    }
    var tick = SymbolTick(symbol);
    return Math.Abs(candidateLow - planLow) <= tick
      && Math.Abs(candidateHigh - planHigh) <= tick;
  }

  private static bool IsBrokerStopDistanceRejection(Exception exception)
  {
    var message = exception.Message;
    if (string.IsNullOrWhiteSpace(message))
    {
      return false;
    }
    var lower = message.ToLowerInvariant();
    return lower.Contains("distance")
      || lower.Contains("freeze")
      || lower.Contains("too close")
      || lower.Contains("minimum")
      || lower.Contains("stop level");
  }

  private static decimal SymbolTick(SymbolInfo symbol) =>
    StopTrailPlanner.RequireTickSize(symbol);

  private static decimal PipTolerance(StructureStopPlan plan, SymbolInfo symbol)
  {
    var tick = SymbolTick(symbol);
    return tick / Math.Max(0.00000001m, plan.Distance == 0m
      ? tick
      : plan.Distance / plan.StopPips);
  }

  private static bool EntryIsResting(ExecutionRoute route) =>
    route is ExecutionRoute.SingleLimit or ExecutionRoute.ZoneSplit;

  internal static (string Metric, string Reason)? FinalStopSideRejection(
    TradeDirection direction,
    decimal stopLoss,
    decimal executableEntry
  )
  {
    var onLosingSide = direction == TradeDirection.Buy
      ? stopLoss < executableEntry
      : stopLoss > executableEntry;
    return onLosingSide
      ? null
      : (
        "final_stop_not_on_losing_side",
        "final_stop_not_on_losing_side_of_executable_entry"
      );
  }

  private static bool LegacyStructuralStopIdentityMatches(
    decimal plannedStop,
    decimal plannedRaw,
    bool plannedClamped,
    string plannedSource,
    StructureStopPlan executorPlan,
    SymbolInfo symbol
  )
  {
    var tick = SymbolTick(symbol);
    return Math.Abs(plannedStop - executorPlan.StopLoss) <= tick
      && Math.Abs(plannedRaw - executorPlan.RawStopLoss) <= tick
      && plannedClamped == executorPlan.Clamped
      && string.Equals(
        plannedSource,
        executorPlan.Source,
        StringComparison.Ordinal
      );
  }

  private static bool StructuralStopIdentityMatches(
    decimal plannedRaw,
    decimal plannedBaseStop,
    bool plannedClamped,
    string plannedSource,
    string plannedAdjustment,
    StructureStopPlan executorPlan,
    SymbolInfo symbol
  )
  {
    var tick = SymbolTick(symbol);
    return Math.Abs(plannedRaw - executorPlan.RawStopLoss) <= tick
      && Math.Abs(
        plannedBaseStop - (executorPlan.BaseStopLoss ?? executorPlan.StopLoss)
      ) <= tick
      && string.Equals(
        plannedSource,
        executorPlan.Source,
        StringComparison.Ordinal
      )
      && (plannedClamped || plannedAdjustment == "opposing_zone_push")
        == executorPlan.Clamped
      && string.Equals(
        plannedAdjustment,
        executorPlan.Adjustment,
        StringComparison.Ordinal
      );
  }

  private static bool PlansMatchWithinTolerance(
    decimal plannedEntry,
    decimal plannedStop,
    decimal plannedDistance,
    decimal plannedPips,
    decimal plannedRaw,
    bool plannedClamped,
    string plannedSource,
    StructureStopPlan executorPlan,
    decimal executorEntryPrice,
    SymbolInfo symbol
  )
  {
    var tick = SymbolTick(symbol);
    var pipTolerance = PipTolerance(executorPlan, symbol);
    return Math.Abs(plannedEntry - executorEntryPrice) <= tick
      && Math.Abs(plannedStop - executorPlan.StopLoss) <= tick
      && Math.Abs(plannedDistance - executorPlan.Distance) <= tick
      && Math.Abs(plannedPips - executorPlan.StopPips) <= pipTolerance
      && Math.Abs(plannedRaw - executorPlan.RawStopLoss) <= tick
      && plannedClamped == executorPlan.Clamped
      && string.Equals(
        plannedSource,
        executorPlan.Source,
        StringComparison.Ordinal
      );
  }

  // P5: a pullback add's stop must sit beyond the retrace extreme, not
  // merely beyond structure - averaging down disguised as a pullback would
  // otherwise slip through. retraceHigh/Low reuses StructureSwing (the
  // same latest-swing point StructureStop already uses) maxed/minned
  // against the mapped zone's far edge, so the stop clears whichever is
  // further. Throws (never clamps) when the result exceeds the trend
  // envelope - ProcessAddAsync rejects the add rather than place a stop
  // inside the very retrace it's supposed to sit beyond.
  private StructureStopPlan PullbackAddStop(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal entryPrice,
    SymbolInfo symbol
  )
  {
    if (
      candidate.Atr is not decimal atr
      || candidate.StructureSwing is not decimal retraceExtreme
      || candidate.OpposingZoneLow is not decimal zoneLow
      || candidate.OpposingZoneHigh is not decimal zoneHigh
    )
    {
      throw new VolumePlanningException(
        "pullback add stop requires atr, structure swing, and a mapped zone"
      );
    }
    var buffer = options.AddStopBufferAtr * atr;
    var rawStop = direction == TradeDirection.Buy
      ? Math.Min(retraceExtreme, zoneLow) - buffer
      : Math.Max(retraceExtreme, zoneHigh) + buffer;
    var rawDistance = direction == TradeDirection.Buy
      ? entryPrice - rawStop
      : rawStop - entryPrice;
    if (rawDistance <= 0)
    {
      throw new VolumePlanningException(
        "pullback stop is not on the losing side of entry"
      );
    }
    var stopLoss = decimal.Round(rawStop, symbol.Digits, MidpointRounding.AwayFromZero);
    var distance = Math.Abs(entryPrice - stopLoss);
    var stopPips = distance / options.PipSize;
    var (_, maximumStopPips) = StopPipsBounds(candidate);
    if (stopPips > maximumStopPips)
    {
      throw new VolumePlanningException(
        $"pullback stop {stopPips:0.#}p exceeds {maximumStopPips}p envelope"
      );
    }
    var basePlan = new StructureStopPlan(
      stopLoss,
      distance,
      stopPips,
      rawStop,
      false,
      "structure",
      stopLoss,
      stopPips
    );
    return StructureStopPlanner.ApplyOpposingZoneAdjustment(
      basePlan,
      direction,
      entryPrice,
      BuildOpposingZoneContext(candidate),
      atr,
      maximumStopPips,
      options.PipSize,
      symbol
    );
  }

  // The owner's exact entered stop, never a re-derived structure stop -
  // this is the entire reason the manual-algo path exists. No min/max stop
  // pips clamping either: options.AddMinStopPips/TrendStopMinPips/MaxPips
  // exist to bound the AUTONOMOUS engines' own structure-derived stops, not
  // an owner's explicit price.
  private string? ValidateManualPrices(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal executableEntry,
    SymbolInfo symbol
  )
  {
    var prices = new[]
    {
      candidate.EntryZone.Low,
      candidate.EntryZone.High,
      candidate.ManualStopLoss!.Value,
    }.Concat(candidate.ManualTakeProfits!);
    if (prices.Any(price =>
      decimal.Round(
        price,
        symbol.Digits,
        MidpointRounding.AwayFromZero
      ) != price
    ))
    {
      return "manual_price_precision_not_supported";
    }
    var targets = candidate.ManualTakeProfits!;
    if (
      direction == TradeDirection.Buy
        ? targets.Any(price => price <= executableEntry)
        : targets.Any(price => price >= executableEntry)
    )
    {
      return "manual_take_profit_not_on_profitable_side";
    }
    var distances = targets
      .Select(price => decimal.Abs(price - executableEntry))
      .ToArray();
    if (
      distances.Zip(distances.Skip(1), (left, right) => right > left)
        .Any(increasing => !increasing)
    )
    {
      return "manual_take_profits_not_ordered_near_to_far";
    }
    return null;
  }

  private StructureStopPlan ManualStop(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal entryPrice,
    SymbolInfo symbol
  )
  {
    var manualStopLoss = candidate.ManualStopLoss
      ?? throw new VolumePlanningException("manual algo candidate has no stop loss");
    var rawDistance = direction == TradeDirection.Buy
      ? entryPrice - manualStopLoss
      : manualStopLoss - entryPrice;
    if (rawDistance <= 0)
    {
      throw new VolumePlanningException(
        "manual stop loss is not on the losing side of entry"
      );
    }
    var stopLoss = decimal.Round(
      manualStopLoss,
      symbol.Digits,
      MidpointRounding.AwayFromZero
    );
    var distance = direction == TradeDirection.Buy
      ? entryPrice - stopLoss
      : stopLoss - entryPrice;
    if (distance <= 0)
    {
      throw new VolumePlanningException(
        "manual stop loss is not on the losing side of entry"
      );
    }
    var stopPips = distance / options.PipSize;
    return new StructureStopPlan(stopLoss, distance, stopPips, manualStopLoss, false);
  }

  private (int Minimum, int Maximum) StopPipsBounds(TradeCandidate candidate) =>
    UsesCandidateTargetPlan(candidate)
      ? (options.TrendStopMinPips, options.TrendStopMaxPips)
      : (
        options.AddMinStopPips,
        decimal.ToInt32(decimal.Floor(
          options.StopLossDistance / options.PipSize
        ))
      );

  private OpposingZoneStopContext? BuildOpposingZoneContext(TradeCandidate candidate)
  {
    if (
      candidate.OpposingZoneLow is not decimal zoneLow
      || candidate.OpposingZoneHigh is not decimal zoneHigh
    )
    {
      return null;
    }
    var zoneWidth = zoneHigh - zoneLow;
    var atr = candidate.Atr ?? 0m;
    var executionGrade = true;
    if (zoneWidth > 0m)
    {
      var widthPips = zoneWidth / options.PipSize;
      var widthAtr = atr > 0m ? zoneWidth / atr : decimal.MaxValue;
      if (
        widthPips > options.ExecutionZoneMaxWidthPips
        || widthAtr > options.ExecutionZoneMaxWidthAtr
      )
      {
        _log(
          "auto-trade ignoring context-only opposing zone "
          + $"{zoneLow:0.####}-{zoneHigh:0.####} "
          + $"({widthPips:0.#}p / {widthAtr:0.##} ATR)"
        );
        executionGrade = false;
      }
    }
    return new OpposingZoneStopContext(
      OpposingZoneIdentity(candidate, zoneLow, zoneHigh),
      zoneLow,
      zoneHigh,
      executionGrade,
      options.StopPushBeyondZone,
      options.AddStopBufferAtr
    );
  }

  // Exact opposing-zone identity. Python's `opposing_zone_id` is authoritative
  // when present; otherwise both sides derive the same deterministic
  // fingerprint from the zone's own geometry and provenance so two zones with
  // identical edges but different origins can never be confused.
  private string OpposingZoneIdentity(
    TradeCandidate candidate,
    decimal zoneLow,
    decimal zoneHigh
  )
  {
    if (!string.IsNullOrWhiteSpace(candidate.OpposingZoneId))
    {
      return candidate.OpposingZoneId!;
    }
    return OpposingZoneFingerprint(
      candidate.Symbol,
      candidate.Timeframe,
      candidate.Direction,
      zoneLow,
      zoneHigh,
      candidate.BarTs ?? candidate.CreatedAt,
      candidate.StructuralSource
    );
  }

  // Byte-for-byte identical to `opposing_zone_fingerprint` in
  // algo-bot/app/autotrade/protective_stop.py; the shared
  // contracts/autotrade/final-stop-parity.json fixture pins both sides.
  internal static string OpposingZoneFingerprint(
    string symbol,
    string timeframe,
    string direction,
    decimal zoneLow,
    decimal zoneHigh,
    long createdBarTs,
    string? source
  ) => string.Join(
    '|',
    FingerprintText(symbol, upper: true),
    FingerprintText(timeframe, upper: true),
    // The opposing zone of a BUY sits below price and is therefore demand;
    // Python names the zone's own side, not the trade direction.
    ParseDirection(direction) == TradeDirection.Buy ? "demand" : "supply",
    FingerprintNumber(zoneLow),
    FingerprintNumber(zoneHigh),
    createdBarTs.ToString(CultureInfo.InvariantCulture),
    FingerprintText(source, upper: false)
  );

  private static string FingerprintText(string? value, bool upper)
  {
    var trimmed = (value ?? "").Trim();
    if (trimmed.Length == 0)
    {
      return "unknown";
    }
    return upper ? trimmed.ToUpperInvariant() : trimmed.ToLowerInvariant();
  }

  private static string FingerprintNumber(decimal value) =>
    decimal.Round(value, 5, MidpointRounding.ToEven)
      .ToString("0.#####", CultureInfo.InvariantCulture);

  private decimal ResolveMinRewardRisk(TradeCandidate candidate) =>
    IsBoxRangeScalp(candidate)
      ? options.BoxMinRiskReward
      : candidate.Setup switch
      {
        "Range Edge Scalp" or "One-Sided Range Reaction" or "Fade Scalp"
          or "Zone Reaction" or "Chop Zone Reaction" => 1.10m,
        "Break & Retest" or "Box Breakout" => 1.20m,
        _ => 1.15m,
      };

  private bool ValidateInitialRewardRisk(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal entryPrice,
    StructureStopPlan stopPlan,
    decimal? boxTargetPips
  )
  {
    if (stopPlan.StopPips <= 0m)
    {
      return false;
    }
    var rewardPips = boxTargetPips ?? RemainingRewardPips(candidate, direction, entryPrice);
    if (rewardPips <= 0m)
    {
      // Legacy Auto Range Scalp uses engine-managed ladder targets that are
      // not present on the candidate payload. Measure RR against that ladder
      // rather than inventing a false reject.
      if (
        !UsesCandidateTargetPlan(candidate)
        && !IsBoxRangeScalp(candidate)
        && options.TargetsPips.Count > 0
      )
      {
        rewardPips = options.TargetsPips.Max();
      }
      else
      {
        return false;
      }
    }
    return rewardPips / stopPlan.StopPips >= ResolveMinRewardRisk(candidate);
  }

  private decimal RemainingRewardPips(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal entryPrice
  )
  {
    var targetModel = (candidate.TargetModel ?? "fill_relative").Trim().ToLowerInvariant();
    decimal ladderRoom = 0m;
    if (candidate.TargetsPips is { Count: > 0 } targets)
    {
      ladderRoom = targets.Max() * options.PipSize;
    }
    decimal absoluteRoom = 0m;
    if (candidate.AbsoluteTargetPrice is decimal absoluteTarget)
    {
      absoluteRoom = direction == TradeDirection.Buy
        ? absoluteTarget - entryPrice
        : entryPrice - absoluteTarget;
    }
    var remainingPrice = targetModel switch
    {
      "absolute" => absoluteRoom,
      "hybrid" => Math.Min(ladderRoom, absoluteRoom),
      _ => ladderRoom,
    };
    return Math.Max(0m, remainingPrice / options.PipSize);
  }

  private ScaleInTriggerResult ValidateAddTriggers(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal entryPrice,
    SpotPrice quote,
    IReadOnlyList<AutoTradePositionState> group,
    SymbolInfo symbol
  )
  {
    if (group.Count == 0)
    {
      return ScaleInTriggerResult.Reject(
        "shared", "empty_group", "scale-in group is empty"
      );
    }
    var groupId = GroupId(group[0]);
    var initialStates = group.Where(state => state.TrancheIndex == 1).ToArray();
    var initial = initialStates[0];
    var initialEntry = direction == TradeDirection.Buy
      ? initialStates.Max(state => state.EntryPrice)
      : initialStates.Min(state => state.EntryPrice);
    var exitQuote = direction == TradeDirection.Buy ? quote.Bid : quote.Ask;
    var floating = GroupBookedPnl(group) + group.Sum(state => OpenPnl(
      state,
      exitQuote,
      symbol
    ));
    var groupOpenedAt = GroupOpenedAt(group);
    // Both timestamps are position-agnostic market observations Python
    // publishes on every candidate (mirroring BosTs) - gating them against
    // this specific group's own open time only makes sense here, where
    // GroupOpenedAt is known.
    var counterBosSinceGroupOpen = candidate.CounterBosTs is long counterBosTs
      && counterBosTs >= groupOpenedAt;
    var extremeSinceGroupOpen = candidate.ExtremeTs is long extremeTs
      && extremeTs >= groupOpenedAt
      ? candidate.ExtremePrice
      : null;
    return ScaleInTriggerPlanner.Validate(new ScaleInTriggerInput(
      initial.Direction,
      direction,
      initialEntry,
      entryPrice,
      floating,
      initialStates.All(state => (
        state.NextTargetIndex >= 1
        && state.CurrentStopLoss is decimal initialStop
        && StopTrailPlanner.IsAtLeastProtectedBreakeven(
          state.Direction,
          state.EntryPrice,
          initialStop,
          symbol,
          options.BreakEvenBufferTicks
        )
      )),
      group.All(state => state.CurrentStopLoss is not null),
      group.All(state => GroupId(state) == groupId && state.Direction == direction),
      group.Max(state => state.GroupTrancheCount),
      options.MaxTranches,
      candidate.DisplacementDirection,
      candidate.DisplacementAgeBars,
      options.AddMaxAgeBars,
      candidate.BosDirection,
      candidate.BosTs,
      groupOpenedAt,
      candidate.OpposingLevelDistanceAtr,
      options.AddLevelBufferAtr,
      candidate.BarTs ?? 0,
      group.Max(state => state.LastTrancheBarTs),
      options.AddCooldownBars,
      PullbackEnabled: options.AddPullbackEnabled,
      CounterBosSinceGroupOpen: counterBosSinceGroupOpen,
      ExtremeSinceGroupOpen: extremeSinceGroupOpen,
      MinRetraceRatio: options.AddPullbackMinRetrace,
      MaxRetraceRatio: options.AddPullbackMaxRetrace,
      AddZoneLow: candidate.OpposingZoneLow,
      AddZoneHigh: candidate.OpposingZoneHigh,
      AddZoneSide: candidate.AddZoneSide,
      RejectionConfirmed: candidate.RejectionConfirmed
    ));
  }

  private async Task PropagateGroupMetadataAsync(
    AutoTradePositionState source,
    CancellationToken cancellationToken
  )
  {
    var groupId = GroupId(source);
    foreach (var current in _states.Values
      .Where(state => GroupId(state) == groupId)
      .ToArray())
    {
      var updated = current with
      {
        GroupBookedPnl = source.GroupBookedPnl,
        InitialTrancheBookedPnl = source.InitialTrancheBookedPnl,
        GroupOpenedAt = source.GroupOpenedAt,
        LastTrancheBarTs = source.LastTrancheBarTs,
        GroupTrancheCount = source.GroupTrancheCount,
        HadAdds = source.HadAdds,
        GroupRealizedPipVolume = source.GroupRealizedPipVolume,
        InitialRealizedPipVolume = source.InitialRealizedPipVolume,
        GroupInitialVolume = source.GroupInitialVolume,
        InitialTrancheVolume = source.InitialTrancheVolume,
      };
      _states[updated.PositionId] = updated;
      await store.SavePositionAsync(updated, cancellationToken);
    }
  }

  private decimal OpenPnl(
    AutoTradePositionState state,
    decimal price,
    SymbolInfo symbol
  )
  {
    var move = state.Direction == TradeDirection.Buy
      ? price - state.EntryPrice
      : state.EntryPrice - price;
    var pips = move / options.PipSize;
    var lots = state.RemainingVolume / (decimal)symbol.LotSize;
    return pips * lots * options.PipValuePerLot;
  }

  private decimal RealizedPnl(
    AutoTradePositionState state,
    decimal price,
    long closedVolume,
    SymbolInfo symbol
  )
  {
    var move = state.Direction == TradeDirection.Buy
      ? price - state.EntryPrice
      : state.EntryPrice - price;
    var pips = move / options.PipSize;
    var lots = closedVolume / (decimal)symbol.LotSize;
    return pips * lots * options.PipValuePerLot;
  }

  private decimal SignedPips(AutoTradePositionState state, decimal price)
  {
    var move = state.Direction == TradeDirection.Buy
      ? price - state.EntryPrice
      : state.EntryPrice - price;
    return move / options.PipSize;
  }

  private decimal? InitialStopPips(AutoTradePositionState state)
  {
    var stop = state.InitialStopLoss ?? state.CurrentStopLoss;
    return stop is decimal price
      ? Math.Abs(state.EntryPrice - price) / options.PipSize
      : null;
  }

  private static decimal WeightedPips(decimal pipVolume, long initialVolume) =>
    initialVolume > 0 ? pipVolume / initialVolume : 0m;

  // Highest plan target that already booked (TP1/TP2/...). Null when the
  // runner never reached a managed target - callers then keep weighted net.
  private static decimal? AchievedTargetPips(AutoTradePositionState state)
  {
    if (state.NextTargetIndex <= 0 || state.TargetsPips.Count == 0)
    {
      return null;
    }
    var index = Math.Min(state.NextTargetIndex, state.TargetsPips.Count) - 1;
    return state.TargetsPips[index];
  }

  // Close-card / group_result total: prefer the highest target reached over
  // a volume-weighted blend that mixes booked TPs with a later BE residual.
  // If the final exit itself printed higher than that target (manual/trail
  // beyond the last booked TP), keep the higher exit leg.
  private decimal TerminalAchievedPips(
    AutoTradePositionState state,
    decimal exitEstimate,
    long remainingVolume,
    decimal pipVolume,
    long initialVolume
  )
  {
    var weighted = WeightedPips(pipVolume, initialVolume);
    if (AchievedTargetPips(state) is not decimal achieved || achieved <= 0)
    {
      return weighted;
    }
    if (remainingVolume <= 0)
    {
      return achieved;
    }
    return Math.Max(achieved, SignedPips(state, exitEstimate));
  }

  // True when the exit sits on the protective stop (initial or trailed)
  // within a small pip tolerance. Covers both a clean SL out before any
  // TP and a BE/trail stop-out after booked targets — otherwise ordinary
  // broker SL hits read as "reason unconfirmed" when OrderType lookup fails.
  private bool LooksLikeProtectiveStopHit(
    AutoTradePositionState state,
    decimal exitEstimate
  )
  {
    var stop = state.CurrentStopLoss ?? state.InitialStopLoss;
    if (stop is not decimal stopPrice)
    {
      return false;
    }
    var tolerance = Math.Max(options.PipSize, 2m * options.PipSize);
    return Math.Abs(exitEstimate - stopPrice) <= tolerance;
  }

  private decimal ResolveMissingPositionExit(
    AutoTradePositionState state,
    PositionCloseLookup closeLookup,
    PositionCloseReason closeReason
  )
  {
    if (closeLookup.ExecutionPrice is decimal recovered)
    {
      return recovered;
    }
    if (closeReason == PositionCloseReason.StopLossOrTakeProfit)
    {
      return state.CurrentStopLoss ?? state.InitialStopLoss ?? state.EntryPrice;
    }
    return LiveExitQuote(state) ?? state.EntryPrice;
  }

  private decimal? LiveExitQuote(AutoTradePositionState state)
  {
    SpotPrice? spot = null;
    if (
      !string.IsNullOrWhiteSpace(state.Symbol)
      && _lastSpotBySymbol.TryGetValue(state.Symbol, out var bySymbol)
    )
    {
      spot = bySymbol;
    }
    else if (
      RedisSymbolFor(state.SymbolId) is string redisSymbol
      && _lastSpotBySymbol.TryGetValue(redisSymbol, out var byId)
    )
    {
      spot = byId;
    }
    else
    {
      spot = _lastSpot;
    }
    if (spot is null)
    {
      return null;
    }
    return state.Direction == TradeDirection.Buy ? spot.Bid : spot.Ask;
  }

  private static string GroupId(AutoTradePositionState state) =>
    string.IsNullOrWhiteSpace(state.GroupId)
      ? GroupToken(state.CandidateId)
      : state.GroupId;

  private static string ExecutionStream(AutoTradePositionState state) =>
    string.IsNullOrWhiteSpace(state.Stream)
      ? state.Setup == "Manual Algo" ? "algo_manual" : "algo_auto"
      : state.Stream;

  private static string DirectionLabel(TradeDirection direction) =>
    direction == TradeDirection.Buy ? "BUY" : "SELL";

  private static decimal GroupBookedPnl(
    IReadOnlyList<AutoTradePositionState> group
  ) => group.Count == 0 ? 0m : group.Max(state => state.GroupBookedPnl);

  private static decimal InitialBookedPnl(
    IReadOnlyList<AutoTradePositionState> group
  ) => group.Count == 0 ? 0m : group.Max(state => state.InitialTrancheBookedPnl);

  private static long GroupOpenedAt(
    IReadOnlyList<AutoTradePositionState> group
  )
  {
    var stored = group.Where(state => state.GroupOpenedAt > 0)
      .Select(state => state.GroupOpenedAt)
      .DefaultIfEmpty(0)
      .Min();
    return stored > 0
      ? stored
      : group.Select(state => state.OpenedAt).DefaultIfEmpty(0).Min();
  }

  private static decimal GroupRealizedPipVolume(
    IReadOnlyList<AutoTradePositionState> group
  ) => group.Count == 0 ? 0m : group.Max(state => state.GroupRealizedPipVolume);

  private static decimal InitialRealizedPipVolume(
    IReadOnlyList<AutoTradePositionState> group
  ) => group.Count == 0 ? 0m : group.Max(
    state => state.InitialRealizedPipVolume
  );

  private static long GroupInitialVolume(
    IReadOnlyList<AutoTradePositionState> group
  ) => group.Count == 0 ? 0 : Math.Max(
    group.Max(state => state.GroupInitialVolume),
    group.Sum(state => state.InitialVolume)
  );

  private static long InitialTrancheVolume(
    IReadOnlyList<AutoTradePositionState> group
  ) => group.Count == 0 ? 0 : Math.Max(
    group.Max(state => state.InitialTrancheVolume),
    group.Where(state => state.TrancheIndex == 1).Sum(state => state.InitialVolume)
  );

  private async Task<bool> CompleteDryRunAsync(
    TradeCandidate candidate,
    string message,
    long volume,
    decimal price,
    CancellationToken cancellationToken,
    string? setup = null,
    string? direction = null,
    decimal? stopLoss = null,
    IReadOnlyList<decimal>? targetPrices = null,
    string? stream = null
  )
  {
    await CompleteActiveCandidateAsync("dry_run", cancellationToken);
    await PublishAsync(
      "dry_run",
      message,
      cancellationToken,
      candidate.CandidateId,
      volume: volume,
      price: price,
      groupId: CandidateGroupId(candidate),
      setup: setup ?? candidate.Setup,
      direction: direction ?? candidate.Direction,
      stream: stream,
      stopLoss: stopLoss,
      targetPrices: targetPrices,
      entryLow: candidate.EntryZone.Low,
      entryHigh: candidate.EntryZone.High
    );
    return true;
  }

  private SpotPrice ValidateQuote(TradeCandidate candidate)
  {
    if (!_lastSpotBySymbol.TryGetValue(candidate.Symbol, out var quote))
    {
      quote = _lastSpot
        ?? throw new CandidateRejectedException("live cTrader quote unavailable");
      if (
        !string.Equals(
          quote.Symbol,
          candidate.Symbol,
          StringComparison.OrdinalIgnoreCase
        )
      )
      {
        throw new CandidateRejectedException(
          $"live quote is for {quote.Symbol}, not {candidate.Symbol}"
        );
      }
    }
    var pipSize = PipSizeForSymbol(candidate.Symbol);
    var age = _clock().ToUnixTimeSeconds() - quote.Timestamp;
    if (age < 0 || age > Math.Max(1, options.SpotMaxAgeSeconds))
    {
      throw new CandidateRejectedException("live cTrader quote is stale");
    }
    var spread = quote.Ask - quote.Bid;
    var spreadPips = spread / pipSize;
    if (spreadPips < 0 || spreadPips > options.MaxSpreadPips)
    {
      throw new CandidateRejectedException(
        $"spread rejected: bid={quote.Bid:0.00} ask={quote.Ask:0.00} "
          + $"raw={spread:0.00} pip={pipSize} -> "
          + $"{spreadPips:0.0} pips, cap {options.MaxSpreadPips:0.0}"
      );
    }
    // MaxEntryDistancePips exists to catch the AUTONOMOUS engines chasing a
    // setup whose zone price has already moved away from - it does not
    // apply to a manual /algo signal, whose entire design is a resting
    // limit order that is expected to sit and wait for price to arrive at
    // the owner's own zone, often well outside this (10-pip default) cap
    // at arm-time. See IsManualAlgoCandidate below.
    if (IsManualAlgoCandidate(candidate))
    {
      return quote;
    }
    var direction = ParseDirection(candidate.Direction);
    var entry = direction == TradeDirection.Buy ? quote.Ask : quote.Bid;
    var distance = entry < candidate.EntryZone.Low
      ? candidate.EntryZone.Low - entry
      : entry > candidate.EntryZone.High
        ? entry - candidate.EntryZone.High
        : 0m;
    var distancePips = distance / pipSize;
    if (distancePips > options.MaxEntryDistancePips)
    {
      var publicationDistance = candidate.CurrentPrice < candidate.EntryZone.Low
        ? candidate.EntryZone.Low - candidate.CurrentPrice
        : candidate.CurrentPrice > candidate.EntryZone.High
          ? candidate.CurrentPrice - candidate.EntryZone.High
          : 0m;
      var publicationDistancePips = publicationDistance / pipSize;
      throw new CandidateRejectedException(
        $"entry distance rejected: direction={candidate.Direction} "
          + $"entry={entry:0.00} "
          + $"zone={candidate.EntryZone.Low:0.00}-{candidate.EntryZone.High:0.00} "
          + $"raw={distance:0.00} pip={pipSize} -> "
          + $"{distancePips:0.0} pips, cap {options.MaxEntryDistancePips:0.0}"
          + (
            publicationDistance > 0m
              ? $"; publication={candidate.CurrentPrice:0.00} "
                + $"publication_pips={publicationDistancePips:0.0}"
              : ""
          )
      );
    }
    return quote;
  }

  private async Task ProcessTargetsAsync(
    SpotPrice spot,
    CancellationToken cancellationToken
  )
  {
    var client = RequireClient();
    var symbol = RequireSymbol();
    if (_symbolsByCanonical.TryGetValue(spot.Symbol, out var routed))
    {
      symbol = routed;
    }
    else if (!spot.Symbol.Equals(symbol.RedisSymbol, StringComparison.OrdinalIgnoreCase))
    {
      return;
    }
    foreach (var original in _states.Values.ToArray())
    {
      if (original.SymbolId != symbol.SymbolId)
      {
        continue;
      }
      var state = _states.GetValueOrDefault(original.PositionId, original);
      while (
        state.RemainingVolume > 0
        && state.NextTargetIndex < state.TargetsPips.Count
      )
      {
        var completedTargetIndex = state.NextTargetIndex;
        if (
          IsRangeBoxScaleOutState(state)
          && completedTargetIndex == 0
          && state.RangeBoxScaleOutBooked
        )
        {
          state = state with { NextTargetIndex = 1 };
          _states[state.PositionId] = state;
          await store.SavePositionAsync(state, cancellationToken);
          continue;
        }
        var targetOrdinal = TargetOrdinal(state, completedTargetIndex);
        var targetPips = state.TargetsPips[state.NextTargetIndex];
        var target = TargetPrice(state, targetPips, completedTargetIndex);
        // Event ordering: never evaluate targets on the fill-source quote.
        // Sequence advances on every ObserveSpot; equals fill sequence means
        // this is still the pre-fill (or same) quote generation. After restart
        // hydrated positions use FillSourceQuoteSequence=0 so the first live
        // quote is eligible.
        if (_spotSequence <= state.FillSourceQuoteSequence)
        {
          await store.IncrementMetricAsync(
            symbol.RedisSymbol,
            "stale_fill_quote_target_suppressed",
            cancellationToken
          );
          break;
        }
        if (
          state.FillSourceQuoteTimestamp > 0
          && spot.Timestamp < state.FillSourceQuoteTimestamp
        )
        {
          await store.IncrementMetricAsync(
            symbol.RedisSymbol,
            "stale_fill_quote_target_suppressed",
            cancellationToken
          );
          break;
        }
        var exitQuote = state.Direction == TradeDirection.Buy ? spot.Bid : spot.Ask;
        var hit = TradePlanExecutionEngine.HasReachedExitTarget(
          DirectionLabel(state.Direction),
          exitQuote,
          target
        );
        if (!hit)
        {
          break;
        }
        var closeVolume = state.NextTargetIndex == state.TargetsPips.Count - 1
          ? state.RemainingVolume
          : Math.Min(state.Slices[state.NextTargetIndex], state.RemainingVolume);
        if (
          state.NextTargetIndex < state.TargetsPips.Count - 1
          && closeVolume > 0
          && state.RemainingVolume - closeVolume > 0
          && state.RemainingVolume - closeVolume < RequireSymbol().MinVolume
        )
        {
          // Would leave an invalid dust remainder — skip partial, ride Full TP.
          _log(
            $"range-box scale-out skipped at runtime for position "
            + $"{state.PositionId}: dust remainder after TP1"
          );
          state = state with { NextTargetIndex = state.TargetsPips.Count - 1 };
          _states[state.PositionId] = state;
          await store.SavePositionAsync(state, cancellationToken);
          continue;
        }
        TradeExecution execution;
        var flipClose = options.RangeFlipEnabled
          && state.RangeExitPrice is not null
          && !string.IsNullOrWhiteSpace(state.RangeId)
          && state.NextTargetIndex == state.TargetsPips.Count - 1;
        if (flipClose)
        {
          if (!await BeginFlipCloseAsync(state, cancellationToken))
          {
            _log(
              $"range flip close already pending for range {state.RangeId}; "
              + "waiting for broker reconciliation"
            );
            break;
          }
          await store.IncrementMetricAsync(
            symbol.RedisSymbol,
            "range_flip_attempted",
            cancellationToken
          );
          await PublishAsync(
            "range_flip_attempted",
            $"range {state.RangeId} full target reached; close confirmed before reverse",
            cancellationToken,
            state.CandidateId,
            state.PositionId,
            groupId: GroupId(state),
            setup: state.Setup,
            direction: DirectionLabel(state.Direction),
            matchId: state.MatchId,
            rangeId: state.RangeId,
            strategyFamily: state.StrategyFamily
          );
          using var closeTimeout = CancellationTokenSource.CreateLinkedTokenSource(
            cancellationToken
          );
          closeTimeout.CancelAfter(TimeSpan.FromSeconds(
            options.FlipConfirmTimeoutSeconds
          ));
          try
          {
            execution = await client.ClosePositionAsync(
              state.PositionId,
              closeVolume,
              closeTimeout.Token
            );
          }
          catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
          {
            await ReleaseFlipCloseAsync(state, cancellationToken);
            var message = $"range flip close for {state.RangeId} was not confirmed "
              + $"within {options.FlipConfirmTimeoutSeconds}s; opposite side not armed";
            _log(message);
            await PublishAsync(
              "warning",
              message,
              cancellationToken,
              state.CandidateId,
              state.PositionId,
              groupId: GroupId(state),
              setup: state.Setup,
              regime: state.Regime,
              confluence: state.Confluence
            );
            break;
          }
          catch
          {
            await ReleaseFlipCloseAsync(state, cancellationToken);
            throw;
          }
          await ReleaseFlipCloseAsync(state, cancellationToken);
          await store.IncrementMetricAsync(
            symbol.RedisSymbol,
            "range_flip_filled",
            cancellationToken
          );
          await PublishAsync(
            "range_flip_filled",
            $"range {state.RangeId} target-side close filled; opposite rail remains armed",
            cancellationToken,
            state.CandidateId,
            state.PositionId,
            groupId: GroupId(state),
            setup: state.Setup,
            direction: DirectionLabel(state.Direction),
            matchId: state.MatchId,
            rangeId: state.RangeId,
            strategyFamily: state.StrategyFamily
          );
        }
        else
        {
          execution = await client.ClosePositionAsync(
            state.PositionId,
            closeVolume,
            cancellationToken
          );
        }
        var remaining = execution.RemainingVolume
          ?? Math.Max(0, state.RemainingVolume - closeVolume);
        var fill = execution.ExecutionPrice > 0
          ? execution.ExecutionPrice
          : exitQuote;
        var realized = RealizedPnl(state, fill, closeVolume, symbol);
        var currentGroup = _states.Values
          .Where(item => GroupId(item) == GroupId(state))
          .ToArray();
        var groupBooked = GroupBookedPnl(currentGroup) + realized;
        var initialBooked = InitialBookedPnl(currentGroup)
          + (state.TrancheIndex == 1 ? realized : 0m);
        var realizedPips = SignedPips(state, fill);
        var groupPipVolume = GroupRealizedPipVolume(currentGroup)
          + realizedPips * closeVolume;
        var initialPipVolume = InitialRealizedPipVolume(currentGroup)
          + (state.TrancheIndex == 1 ? realizedPips * closeVolume : 0m);
        var groupInitialVolume = GroupInitialVolume(currentGroup);
        var initialTrancheVolume = InitialTrancheVolume(currentGroup);
        state = state with
        {
          RemainingVolume = remaining,
          NextTargetIndex = state.NextTargetIndex + 1,
          GroupBookedPnl = groupBooked,
          InitialTrancheBookedPnl = initialBooked,
          GroupRealizedPipVolume = groupPipVolume,
          InitialRealizedPipVolume = initialPipVolume,
          GroupInitialVolume = groupInitialVolume,
          InitialTrancheVolume = initialTrancheVolume,
          RangeBoxScaleOutBooked = state.RangeBoxScaleOutBooked
            || (
              IsRangeBoxScaleOutState(state)
              && completedTargetIndex == 0
            ),
          RangeBoxScaleOutVolume = (
            IsRangeBoxScaleOutState(state) && completedTargetIndex == 0
          )
            ? closeVolume
            : state.RangeBoxScaleOutVolume,
          RangeBoxScaleOutPrice = (
            IsRangeBoxScaleOutState(state) && completedTargetIndex == 0
          )
            ? fill
            : state.RangeBoxScaleOutPrice,
          RangeBoxScaleOutPips = (
            IsRangeBoxScaleOutState(state) && completedTargetIndex == 0
          )
            ? realizedPips
            : state.RangeBoxScaleOutPips,
          RangeBoxScaleOutAt = (
            IsRangeBoxScaleOutState(state) && completedTargetIndex == 0
          )
            ? _clock().ToUnixTimeSeconds()
            : state.RangeBoxScaleOutAt,
        };
        _states[state.PositionId] = state;
        await PropagateGroupMetadataAsync(state, cancellationToken);
        var targetLabel = state.TargetsPips.Count == 1
          || (
            IsRangeBoxScaleOutState(state)
            && completedTargetIndex == state.TargetsPips.Count - 1
          )
          ? "FULL TP"
          : $"TP{targetOrdinal}";
        var legPipText = realizedPips.ToString(
          "+0.0;-0.0;+0.0",
          CultureInfo.InvariantCulture
        );
        var weightedGroupPips = WeightedPips(
          groupPipVolume,
          groupInitialVolume
        );
        await PublishAsync(
          "take_profit",
          $"{targetLabel} {legPipText} pips closed volume {closeVolume}",
          cancellationToken,
          state.CandidateId,
          state.PositionId,
          targetPips,
          closeVolume,
          fill,
          groupId: GroupId(state),
          trancheIndex: state.TrancheIndex,
          groupRealizedPnl: groupBooked,
          counterfactualPnl: initialBooked,
          hadAdds: state.HadAdds,
          groupRealizedPips: weightedGroupPips,
          counterfactualPips: WeightedPips(
            initialPipVolume,
            initialTrancheVolume
          ),
          stopPips: InitialStopPips(state),
          setup: state.Setup,
          regime: state.Regime,
          confluence: state.Confluence,
          stream: ExecutionStream(state),
          direction: DirectionLabel(state.Direction),
          remainingVolume: remaining,
          matchId: state.MatchId,
          rangeId: state.RangeId,
          strategyFamily: state.StrategyFamily,
          legRealizedPips: realizedPips,
          groupInitialVolume: groupInitialVolume,
          lotSize: symbol.LotSize
        );
        if (remaining <= 0)
        {
          var groupId = GroupId(state);
          _states.Remove(state.PositionId);
          await store.DeletePositionAsync(state.PositionId, cancellationToken);
          if (!_states.Values.Any(item => GroupId(item) == groupId))
          {
            var groupPips = WeightedPips(groupPipVolume, groupInitialVolume);
            var counterfactualPips = WeightedPips(
              initialPipVolume,
              initialTrancheVolume
            );
            var addDelta = groupBooked - initialBooked;
            var addLabel = addDelta > 0 ? "improved" : "degraded";
            await PublishAsync(
              "group_result",
              $"group {groupId} realised {groupPips.ToString("0.0", CultureInfo.InvariantCulture)} pips · "
              + $"no-add counterfactual {counterfactualPips.ToString("0.0", CultureInfo.InvariantCulture)} pips · adds "
              + addLabel,
              cancellationToken,
              state.CandidateId,
              state.PositionId,
              groupId: groupId,
              groupWorstCase: groupBooked,
              groupRealizedPnl: groupBooked,
              counterfactualPnl: initialBooked,
              hadAdds: state.HadAdds,
              groupRealizedPips: groupPips,
              counterfactualPips: counterfactualPips,
              setup: state.Setup,
              matchId: state.MatchId,
              rangeId: state.RangeId,
              strategyFamily: state.StrategyFamily,
              regime: state.Regime,
              confluence: state.Confluence,
              stopPips: InitialStopPips(state),
              stream: ExecutionStream(state),
              direction: DirectionLabel(state.Direction),
              groupInitialVolume: groupInitialVolume,
              lotSize: symbol.LotSize
            );
            await MaybeDeleteGroupPlanAsync(groupId, cancellationToken);
          }
          break;
        }
        // Final TP only partially filled — keep the last index live so the
        // residual can be re-closed on the next spot instead of becoming
        // unmanaged until SL / missing-snapshot (loop requires
        // NextTargetIndex < Count).
        if (state.NextTargetIndex >= state.TargetsPips.Count)
        {
          state = state with
          {
            NextTargetIndex = state.TargetsPips.Count - 1,
          };
          _states[state.PositionId] = state;
          await store.SavePositionAsync(state, cancellationToken);
          continue;
        }
        state = await MoveStopAfterTargetAsync(
          state,
          completedTargetIndex,
          targetOrdinal,
          symbol,
          cancellationToken
        );
        _states[state.PositionId] = state;
        await store.SavePositionAsync(state, cancellationToken);
      }
    }
  }

  private async Task<AutoTradePositionState> MoveStopAfterTargetAsync(
    AutoTradePositionState state,
    int completedTargetIndex,
    int targetOrdinal,
    SymbolInfo symbol,
    CancellationToken cancellationToken
  )
  {
    var move = StopTrailPlanner.Plan(
      state,
      completedTargetIndex,
      symbol,
      options.PipSize,
      options.BreakEvenBufferTicks
    );
    if (move is null)
    {
      return state;
    }
    try
    {
      await RequireClient().AmendPositionStopLossAsync(
        state.PositionId,
        move.StopLoss,
        cancellationToken
      );
    }
    catch (OperationCanceledException)
    {
      throw;
    }
    catch (Exception exception) when (IsBrokerStopDistanceRejection(exception))
    {
      var deferMessage = $"🛡 ApexVoid Algo stop deferred → {move.StopLoss:N2} "
        + $"({move.Label}) · position {state.PositionId}: broker distance/freeze";
      _log($"auto-trade {deferMessage}: {exception.Message}");
      try
      {
        await PublishAsync(
          "warning",
          deferMessage,
          cancellationToken,
          state.CandidateId,
          state.PositionId,
          price: move.StopLoss,
          groupId: GroupId(state),
          trancheIndex: state.TrancheIndex,
          hadAdds: state.HadAdds,
          matchId: state.MatchId,
          rangeId: state.RangeId,
          strategyFamily: state.StrategyFamily,
          direction: DirectionLabel(state.Direction),
          remainingVolume: state.RemainingVolume,
          stopLoss: state.CurrentStopLoss,
          entryLow: state.EntryPrice,
          entryHigh: state.EntryPrice,
          reasonCode: "be_buffer_stop_deferred_broker_distance"
        );
      }
      catch (Exception publishException) when (
        publishException is not OperationCanceledException
      )
      {
        _log(
          $"auto-trade stop-defer warning failed: {publishException.Message}"
        );
      }
      return state;
    }
    catch (Exception exception)
    {
      var errorMessage = $"position {state.PositionId} stop amend after "
        + $"TP{targetOrdinal} failed: {exception.Message}";
      _log($"auto-trade {errorMessage}");
      try
      {
        await PublishAsync(
          "error",
          errorMessage,
          cancellationToken,
          state.CandidateId,
          state.PositionId
        );
      }
      catch (Exception publishException) when (
        publishException is not OperationCanceledException
      )
      {
        _log(
          $"auto-trade stop-amend error event failed: {publishException.Message}"
        );
      }
      return state;
    }
    var moveMessage = $"🛡 ApexVoid Algo stop → {move.StopLoss:N2} ({move.Label})"
      + (
        move.BufferPrice is decimal buffer
          ? $" · buffer {buffer:0.00}"
          : ""
      )
      + $" · position {state.PositionId}";
    var previousStop = state.CurrentStopLoss;
    await PublishAsync(
      "stop_moved",
      moveMessage,
      cancellationToken,
      state.CandidateId,
      state.PositionId,
      price: move.StopLoss,
      groupId: GroupId(state),
      trancheIndex: state.TrancheIndex,
      hadAdds: state.HadAdds,
      matchId: state.MatchId,
      rangeId: state.RangeId,
      strategyFamily: state.StrategyFamily,
      direction: DirectionLabel(state.Direction),
      remainingVolume: state.RemainingVolume,
      stopLoss: previousStop,
      entryLow: state.EntryPrice,
      entryHigh: state.EntryPrice,
      stream: ExecutionStream(state)
    );
    return state with { CurrentStopLoss = move.StopLoss };
  }

  private async Task ReconcileAsync(CancellationToken cancellationToken)
  {
    var client = RequireClient();
    var symbol = RequireSymbol();
    var snapshot = await client.ReconcileAccountAsync(cancellationToken);
    _allSymbolPositions = snapshot.Positions
      .Where(position => position.SymbolId == symbol.SymbolId)
      .ToArray();
    _allSymbolPendingOrders = snapshot.PendingOrders
      .Where(order => order.SymbolId == symbol.SymbolId)
      .ToArray();
    foreach (var order in _allSymbolPendingOrders.ToArray())
    {
      var zone = ParseZoneComment(order.Comment);
      if (
        order.Label != options.Label
        || zone is null
        || zone.Value.Leg != 2
        || _clock().ToUnixTimeSeconds() - zone.Value.BarTs
          < options.ZoneFillTtlBars * 60L
      )
      {
        continue;
      }
      await client.CancelPendingOrderAsync(order.OrderId, cancellationToken);
      _allSymbolPendingOrders = _allSymbolPendingOrders
        .Where(item => item.OrderId != order.OrderId)
        .ToArray();
      var plan = await LoadGroupPlanAsync(zone.Value.GroupId, cancellationToken);
      await PublishAsync(
        "zone_expired",
        $"zone midpoint limit {order.OrderId} cancelled after "
          + $"{options.ZoneFillTtlBars} bars; filled volume keeps its "
          + "proportional ladder",
        cancellationToken,
        candidateId: plan?.CandidateId,
        groupId: zone.Value.GroupId,
        trancheIndex: 1,
        hadAdds: false,
        setup: plan?.Setup,
        direction: plan?.Direction,
        matchId: plan?.MatchId,
        rangeId: plan?.RangeId,
        strategyFamily: plan?.StrategyFamily,
        pendingOrderIds: PendingOrderIdsForGroup(zone.Value.GroupId)
      );
      await MaybeDeleteGroupPlanAsync(
        zone.Value.GroupId,
        cancellationToken
      );
    }
    foreach (var order in _allSymbolPendingOrders.ToArray())
    {
      var manual = ParseManualExpiry(order.Comment);
      if (
        order.Label != options.Label
        || manual is null
        || manual.Value.ExpiresAt <= 0
        || _clock().ToUnixTimeSeconds() < manual.Value.ExpiresAt
      )
      {
        continue;
      }
      await client.CancelPendingOrderAsync(order.OrderId, cancellationToken);
      _allSymbolPendingOrders = _allSymbolPendingOrders
        .Where(item => item.OrderId != order.OrderId)
        .ToArray();
      await PublishAsync(
        "manual_expired",
        $"manual algo limit {order.OrderId} cancelled after expiry",
        cancellationToken,
        candidateId: manual.Value.CandidateToken,
        groupId: manual.Value.GroupId,
        trancheIndex: 1,
        hadAdds: false
      );
      await MaybeDeleteGroupPlanAsync(
        manual.Value.GroupId,
        cancellationToken
      );
    }
    var trackedIds = await store.GetTrackedPositionIdsAsync(cancellationToken);
    // Presence is by PositionId on the full symbol snapshot. Filtering
    // openIds by Label orphaned runners whose broker Label drifted empty
    // or drifted off options.Label after SLTP/partials (manual #8 2026-08-11:
    // remaining 20% waiting TP5 was confirmed-missing while still open /
    // mismanaged). Label is only for new adopt; tracked IDs stay live as
    // long as the PositionId is still on the symbol.
    var openIds = _allSymbolPositions
      .Select(position => position.PositionId)
      .ToHashSet();
    foreach (var stale in trackedIds.Where(id => !openIds.Contains(id)))
    {
      // A single missing broker snapshot is only "suspected" missing, not
      // confirmed closed - a transient reconcile gap must never delete an
      // open position's tracking. Require at least
      // options.PositionMissingConfirmations independent snapshots, each
      // separated by at least options.PositionMissingRecheckSeconds on the
      // executor clock, before terminalising. This does not apply to a
      // close the engine itself submitted and got a confirmed broker
      // response for - those paths remove _states/store directly and never
      // reach this reconcile-driven stale-detection loop.
      var missingNow = _clock().ToUnixTimeSeconds();
      var missing = await store.GetPositionMissingAsync(stale, cancellationToken);
      if (
        missing is not null
        && missingNow - missing.LastCheckedAt < options.PositionMissingRecheckSeconds
      )
      {
        continue;
      }
      var confirmations = (missing?.Confirmations ?? 0) + 1;
      if (confirmations < options.PositionMissingConfirmations)
      {
        await store.SavePositionMissingAsync(
          stale,
          missing is null
            ? new PositionMissingRecord(confirmations, missingNow, missingNow)
            : missing with { Confirmations = confirmations, LastCheckedAt = missingNow },
          cancellationToken
        );
        await store.IncrementMetricAsync(
          RequireSymbol().RedisSymbol,
          "position_missing_snapshot_suspected",
          cancellationToken
        );
        _log(
          $"auto-trade position_missing_snapshot_suspected position_id={stale}"
            + $" confirmations={confirmations}"
        );
        continue;
      }
      await store.ClearPositionMissingAsync(stale, cancellationToken);
      await store.IncrementMetricAsync(
        RequireSymbol().RedisSymbol,
        "position_missing_snapshot_confirmed",
        cancellationToken
      );
      _log(
        $"auto-trade position_missing_snapshot_confirmed position_id={stale}"
          + $" confirmations={confirmations}"
      );
      var state = _states.GetValueOrDefault(stale)
        ?? await store.GetPositionAsync(stale, cancellationToken);
      _states.Remove(stale);
      await store.DeletePositionAsync(stale, cancellationToken);
      if (state is not null)
      {
        var initialVolume = state.GroupInitialVolume > 0
          ? state.GroupInitialVolume
          : state.InitialVolume;
        // Best-effort: was this the broker-attached SL/TP order, or a
        // manual/external order (almost certainly the owner closing it
        // directly on the platform)? Also recovers the closing deal's real
        // execution price when found. See
        // CTraderOpenApiFeedClient.DeterminePositionCloseReasonAsync.
        var closeLookup = new PositionCloseLookup(PositionCloseReason.Unknown);
        try
        {
          closeLookup = await client.DeterminePositionCloseReasonAsync(
            stale,
            state.OpenedAt,
            missingNow,
            cancellationToken
          );
        }
        catch (OperationCanceledException)
        {
          throw;
        }
        catch (Exception exception)
        {
          _log(
            $"auto-trade position_close_reason_lookup_failed position_id={stale}: "
              + exception.Message
          );
        }
        var closeReason = closeLookup.Reason;
        // Promote Unknown → SL/TP only when the deal lookup recovered a real
        // execution price sitting on the protective stop. Defaulting the
        // exit estimate FROM CurrentStopLoss and then comparing to that same
        // stop is a tautology (manual #8 2026-08-11: deal-list timeout →
        // fabricated "stop loss / take profit" with no TP5 book).
        if (
          closeReason == PositionCloseReason.Unknown
          && closeLookup.ExecutionPrice is decimal recoveredExit
          && LooksLikeProtectiveStopHit(state, recoveredExit)
        )
        {
          closeReason = PositionCloseReason.StopLossOrTakeProfit;
        }
        // Manual / unconfirmed closes must not book P&L against the stop.
        // Prefer the recovered deal fill, then the live quote, then entry.
        var exitEstimate = ResolveMissingPositionExit(
          state,
          closeLookup,
          closeReason
        );
        var remainingVolume = Math.Max(0, state.RemainingVolume);
        var pipVolume = state.GroupRealizedPipVolume
          + SignedPips(state, exitEstimate) * remainingVolume;
        // Total on the close card is the highest target reached (e.g. TP2
        // = 60), not the volume-weighted blend that dilutes booked TPs
        // with a later BE residual. Fall back to weighted only when no
        // target was booked yet (pure SL / full one-shot close).
        var terminalGroupPips = TerminalAchievedPips(
          state,
          exitEstimate,
          remainingVolume,
          pipVolume,
          initialVolume
        );
        var groupId = GroupId(state);
        var pipText = terminalGroupPips.ToString("0.0", CultureInfo.InvariantCulture);
        var resultPhrase = terminalGroupPips > 0m
          ? $"winning {pipText} pips"
          : terminalGroupPips < 0m
            ? $"losing {pipText} pips"
            : "break-even";
        var (closeMessage, closeReasonCode) = closeReason switch
        {
          PositionCloseReason.StopLossOrTakeProfit => (
            $"position closed at broker: stop loss / take profit · {resultPhrase}",
            "stop_loss_or_take_profit"
          ),
          PositionCloseReason.ManualOrExternalOrder => (
            $"position closed at broker: manual or external order · {resultPhrase}",
            "manual_or_external_close"
          ),
          _ => (
            $"position is no longer open at broker (reason unconfirmed) · {resultPhrase}",
            (string?)null
          ),
        };
        await PublishAsync(
          "position_closed",
          closeMessage,
          cancellationToken,
          state.CandidateId,
          stale,
          price: exitEstimate,
          volume: remainingVolume > 0 ? remainingVolume : null,
          groupId: groupId,
          setup: state.Setup,
          regime: state.Regime,
          confluence: state.Confluence,
          stopPips: InitialStopPips(state),
          stream: ExecutionStream(state),
          direction: DirectionLabel(state.Direction),
          reasonCode: closeReasonCode,
          matchId: state.MatchId,
          rangeId: state.RangeId,
          strategyFamily: state.StrategyFamily,
          groupRealizedPips: terminalGroupPips,
          groupInitialVolume: initialVolume,
          remainingVolume: 0,
          legRealizedPips: remainingVolume > 0
            ? SignedPips(state, exitEstimate)
            : null
        );
        if (!_states.Values.Any(item => GroupId(item) == groupId))
        {
          await PublishAsync(
            "group_result",
            $"group {groupId} realised {terminalGroupPips.ToString("0.0", CultureInfo.InvariantCulture)} pips",
            cancellationToken,
            state.CandidateId,
            stale,
            groupId: groupId,
            groupRealizedPips: terminalGroupPips,
            setup: state.Setup,
            matchId: state.MatchId,
            rangeId: state.RangeId,
            strategyFamily: state.StrategyFamily,
            regime: state.Regime,
            confluence: state.Confluence,
            stopPips: InitialStopPips(state),
            stream: ExecutionStream(state),
            direction: DirectionLabel(state.Direction),
            groupInitialVolume: initialVolume
          );
          await MaybeDeleteGroupPlanAsync(groupId, cancellationToken);
        }
        // A broker snapshot disappearance is ambiguous: it can be SL,
        // manual close, external close, or a reconciliation gap.  The Open
        // API adapter does not expose a confirmed close reason here, so do
        // not guess stop_loss.  Persist warning-only evidence; Python only
        // enforces reason=stop_loss + confidence=confirmed.
        if (state.CurrentStopLoss is decimal lastStopLoss)
        {
          var directionLabel = state.Direction == TradeDirection.Buy ? "BUY" : "SELL";
          await store.RecordZoneCooldownAsync(
            RequireSymbol().RedisSymbol,
            directionLabel,
            new ZoneCooldownRecord(
              Reason: "reconciliation_unknown",
              Confidence: "unconfirmed",
              EntryPrice: state.EntryPrice,
              StopPrice: lastStopLoss,
              ClosedAt: _clock().ToUnixTimeSeconds(),
              GroupId: GroupId(state),
              ZoneId: state.ZoneId,
              Strategy: state.Setup
            ),
            options.ZoneCooldownMinutes,
            cancellationToken
          );
        }
      }
    }
    // Adopt bot-labeled positions and any still-tracked IDs present on the
    // symbol (label-drift recovery for orphaned runners).
    foreach (var position in _allSymbolPositions.Where(
      item => item.Label == options.Label || trackedIds.Contains(item.PositionId)
    ))
    {
      if (
        position.Label != options.Label
        && trackedIds.Contains(position.PositionId)
      )
      {
        await store.IncrementMetricAsync(
          symbol.RedisSymbol,
          "tracked_position_label_mismatch_recovered",
          cancellationToken
        );
        _log(
          "auto-trade tracked_position_label_mismatch_recovered "
            + $"position_id={position.PositionId} label={position.Label}"
        );
      }
      await AdoptPositionAsync(position, cancellationToken);
    }
    foreach (var group in _states.Values.GroupBy(GroupId).ToArray())
    {
      var states = group.ToArray();
      var source = states.MinBy(state => state.TrancheIndex)! with
      {
        GroupBookedPnl = states.Max(state => state.GroupBookedPnl),
        InitialTrancheBookedPnl = states.Max(
          state => state.InitialTrancheBookedPnl
        ),
        GroupOpenedAt = GroupOpenedAt(states),
        LastTrancheBarTs = states.Max(state => state.LastTrancheBarTs),
        GroupTrancheCount = states.Max(state => Math.Max(
          state.GroupTrancheCount,
          state.TrancheIndex
        )),
        HadAdds = states.Any(state => state.HadAdds || state.TrancheIndex > 1),
        GroupRealizedPipVolume = GroupRealizedPipVolume(states),
        InitialRealizedPipVolume = InitialRealizedPipVolume(states),
        GroupInitialVolume = GroupInitialVolume(states),
        InitialTrancheVolume = InitialTrancheVolume(states),
      };
      await PropagateGroupMetadataAsync(source, cancellationToken);
    }
    var executorPositionIds = _allSymbolPositions
      .Where(item =>
        item.Label == options.Label || trackedIds.Contains(item.PositionId)
      )
      .Select(item => item.PositionId)
      .ToArray();
    var executorPendingOrderIds = _allSymbolPendingOrders
      .Where(item => item.Label == options.Label)
      .Select(item => item.OrderId)
      .ToArray();
    var accountBalance = 0m;
    var accountEquity = 0m;
    var accountEquitySource = "";
    if (_account is { } account)
    {
      var equityResolution = EquityResolver.Resolve(
        account, executorPositionIds.Length, executorPendingOrderIds.Length
      );
      accountBalance = equityResolution.AccountBalance;
      accountEquity = equityResolution.Equity;
      accountEquitySource = equityResolution.EquitySource;
    }
    var executorSnapshot = new AutoTradeExecutorSnapshot(
      symbol.RedisSymbol,
      options.Profile,
      EffectiveExposurePolicy().ToString(),
      Demo: _account is { IsLive: false },
      Hedged: _accountSupportsHedging,
      Ready: _ready,
      PositionIds: executorPositionIds,
      PendingOrderIds: executorPendingOrderIds,
      GroupIds: _states.Values
        .Select(GroupId)
        .Distinct(StringComparer.Ordinal)
        .Order()
        .ToArray(),
      UpdatedAt: _clock().ToUnixTimeSeconds(),
      AccountBalance: accountBalance,
      AccountEquity: accountEquity,
      AccountEquitySource: accountEquitySource
    );
    await store.SetValueAsync(
      $"auto_trade:executor_snapshot:{symbol.RedisSymbol.ToUpperInvariant()}",
      JsonSerializer.Serialize(
        executorSnapshot,
        RedisJsonContext.Default.AutoTradeExecutorSnapshot
      ),
      cancellationToken
    );
  }

  private async Task AdoptPositionAsync(
    TradingPosition position,
    CancellationToken cancellationToken
  )
  {
    // A position present in this snapshot clears any missing-confirmation
    // progress from an earlier reconcile pass that briefly did not see it.
    if (
      await store.GetPositionMissingAsync(position.PositionId, cancellationToken)
        is not null
    )
    {
      await store.ClearPositionMissingAsync(position.PositionId, cancellationToken);
      await store.IncrementMetricAsync(
        RequireSymbol().RedisSymbol,
        "position_missing_snapshot_recovered",
        cancellationToken
      );
      _log(
        "auto-trade position_missing_snapshot_recovered "
          + $"position_id={position.PositionId}"
      );
    }
    var stored = await store.GetPositionAsync(position.PositionId, cancellationToken);
    var parsed = stored is null ? ParseComment(position) : null;
    var state = stored ?? parsed;
    var isNewZoneFill = stored is null && parsed?.ZoneLeg is > 0;
    // A manual-algo limit order fill is never seen by PlaceTrancheAsync (no
    // market order is ever placed for it) - this adoption, the very first
    // time nothing in Redis/parseable-av* comments already knows this
    // position, IS the fill event for it, unlike av1/av2/av3/avz adoption
    // which is always recovering an already-published trade.
    var isNewManualFill = false;
    if (state is null)
    {
      var manual = ParseManualComment(position);
      if (manual is not null)
      {
        state = manual;
        isNewManualFill = true;
      }
    }
    // TradePlan ownership (v8|plan|thesis|L1, plus draining v7|) is never
    // an av1/av2/av3/avz comment — hand it to TradePlanRuntime before the
    // reconstruct failure log so multi-leg ladder fills are not treated as
    // unowned orphans.
    if (
      state is null
      && TradePlanV7Ownership.TryParseV7Ownership(
        position.Comment, position.ClientOrderId
      ) is not null
    )
    {
      await TradePlans.TryAdoptV7BrokerPositionAsync(
        _client, RequireSymbol(), position, cancellationToken
      );
      return;
    }
    if (state is null)
    {
      _log($"auto-trade cannot reconstruct position {position.PositionId}");
      return;
    }
    if (string.IsNullOrWhiteSpace(state.Symbol))
    {
      state = state with
      {
        Symbol = RedisSymbolFor(position.SymbolId)
          ?? RequireSymbol().RedisSymbol,
      };
    }
    AutoTradeGroupPlan? plan = null;
    if (stored is null && !string.IsNullOrWhiteSpace(state.GroupId))
    {
      plan = await LoadGroupPlanAsync(state.GroupId, cancellationToken);
      if (plan is not null)
      {
        state = state with
        {
          CandidateId = plan.CandidateId,
          GroupId = plan.GroupId,
          Setup = plan.Setup,
          RangeId = plan.RangeId,
          MatchId = plan.MatchId,
          StrategyFamily = plan.StrategyFamily,
          TargetPrices = plan.TargetPrices ?? (
            plan.TargetModel is null
              ? null
              : BuildAutonomousTargetPrices(
                plan.TargetModel,
                plan.AbsoluteTargetPrice,
                state.Direction,
                state.EntryPrice,
                state.TargetsPips
              )
          ),
          ZoneId = plan.ZoneId,
          TriggerId = plan.TriggerId,
          ParentGroupId = plan.ParentGroupId,
          StructuralSource = plan.StructuralSource,
          ReactionId = plan.ReactionId,
          ThesisId = plan.ThesisId,
          RiskMultiplier = plan.RiskMultiplier,
          TargetModel = plan.TargetModel,
          AbsoluteTargetPrice = plan.AbsoluteTargetPrice,
        };
      }
    }
    // Manual limits send a relative SL from the limit price. cTrader then
    // anchors that distance to the fill, so a better BUY fill silently
    // widens the VIP stop (live 2026-08-17: #64 posted SL 4382, fill
    // 4385.85, broker SL ~4378). Pin the approved absolute price the same
    // way PlaceTrancheAsync does for market fills.
    var currentStop = position.StopLoss ?? state.CurrentStopLoss;
    var initialStop = state.InitialStopLoss;
    if (isNewManualFill && plan?.ManualStopLoss is decimal approvedStop)
    {
      var symbol = RequireSymbol();
      var stopLoss = decimal.Round(
        approvedStop,
        symbol.Digits,
        MidpointRounding.AwayFromZero
      );
      try
      {
        await RequireClient().AmendPositionStopLossAsync(
          position.PositionId,
          stopLoss,
          cancellationToken
        );
        await store.IncrementMetricAsync(
          symbol.RedisSymbol,
          "final_stop_absolute_applied",
          cancellationToken
        );
        currentStop = stopLoss;
        initialStop = stopLoss;
      }
      catch (Exception exception) when (exception is not OperationCanceledException)
      {
        await store.IncrementMetricAsync(
          symbol.RedisSymbol,
          "final_stop_amendment_unknown",
          cancellationToken
        );
        _log(
          "auto-trade manual fill stop amend failed "
            + $"position_id={position.PositionId}: {exception.Message}"
        );
      }
    }
    state = state with
    {
      RemainingVolume = position.Volume,
      CurrentStopLoss = currentStop,
      InitialStopLoss = initialStop,
    };
    _states[position.PositionId] = state;
    await store.SavePositionAsync(state, cancellationToken);
    if (isNewZoneFill)
    {
      var directionLabel = DirectionLabel(state.Direction);
      var lots = state.InitialVolume / (decimal)RequireSymbol().LotSize;
      var pendingOrderIds = PendingOrderIdsForGroup(state.GroupId);
      await store.IncrementMetricAsync(
        RequireSymbol().RedisSymbol,
        "order_filled",
        cancellationToken
      );
      await PublishAsync(
        "opened",
        $"{directionLabel} {lots:N2} lots filled {state.EntryPrice:N2}, "
          + $"SL {state.CurrentStopLoss:N2} · "
          + $"{InitialStopPips(state):N0}p structure · zone fill",
        cancellationToken,
        state.CandidateId,
        state.PositionId,
        volume: state.InitialVolume,
        price: state.EntryPrice,
        groupId: state.GroupId,
        trancheIndex: state.TrancheIndex,
        setup: state.Setup,
        stopPips: InitialStopPips(state),
        targetsPips: state.TargetsPips,
        stream: state.Stream,
        direction: directionLabel,
        matchId: state.MatchId,
        rangeId: state.RangeId,
        strategyFamily: state.StrategyFamily,
        pendingOrderIds: pendingOrderIds
      );
      await PublishAsync(
        "managing",
        $"{state.Setup ?? "zone fill"} {directionLabel} is under group management",
        cancellationToken,
        state.CandidateId,
        state.PositionId,
        groupId: state.GroupId,
        trancheIndex: state.TrancheIndex,
        setup: state.Setup,
        direction: directionLabel,
        matchId: state.MatchId,
        rangeId: state.RangeId,
        strategyFamily: state.StrategyFamily,
        pendingOrderIds: pendingOrderIds
      );
    }
    if (isNewManualFill)
    {
      var directionLabel = state.Direction == TradeDirection.Buy ? "BUY" : "SELL";
      var lots = state.InitialVolume / (decimal)RequireSymbol().LotSize;
      await PublishAsync(
        "manual_opened",
        $"{directionLabel} {lots:N2} lots filled {state.EntryPrice:N2}, "
          + $"SL {state.CurrentStopLoss:N2} · manual algo",
        cancellationToken,
        state.CandidateId,
        state.PositionId,
        volume: state.InitialVolume,
        price: state.EntryPrice,
        groupId: state.GroupId,
        trancheIndex: 1,
        setup: state.Setup,
        stopPips: InitialStopPips(state),
        targetsPips: state.TargetsPips,
        stream: state.Stream,
        direction: directionLabel,
        stopLoss: state.CurrentStopLoss,
        targetPrices: state.TargetPrices
      );
    }
  }

  /// <summary>
  /// Independent strategies cannot safely retain independent SL/TP plans
  /// when the broker represents them as one net position. On a non-hedged
  /// (netting) account, a second autonomous initial group - same direction
  /// or opposite - collapses into the existing net position at the broker,
  /// silently merging two strategies' SL/TP/tracking into one. Fail closed
  /// before any broker submission rather than letting CanOpenNewGroup's
  /// broker_netting compatibility path (intended for controlled
  /// opposite-direction flip/net behavior) admit a second independent
  /// initial group. Legacy explicitly linked scale-ins (ParentGroupId
  /// present) are unaffected - they already share the parent's broker
  /// position by design and go through ValidateAddTriggers instead.
  /// </summary>
  private async Task<bool> TryRejectIndependentGroupOnNonHedgedAccountAsync(
    TradeCandidate candidate,
    SymbolInfo symbol,
    CancellationToken cancellationToken
  )
  {
    if (
      _accountSupportsHedging
      || !string.IsNullOrWhiteSpace(candidate.ParentGroupId)
      || IsManualAlgoCandidate(candidate)
    )
    {
      return false;
    }
    var activePositions = _allSymbolPositions
      .Where(position => position.SymbolId == symbol.SymbolId && position.Label == options.Label)
      .ToArray();
    var activeOrders = _allSymbolPendingOrders
      .Where(order => order.SymbolId == symbol.SymbolId && order.Label == options.Label)
      .ToArray();
    if (activePositions.Length == 0 && activeOrders.Length == 0)
    {
      return false;
    }
    var activePositionIds = activePositions
      .Select(position => position.PositionId)
      .ToArray();
    var activeGroupIds = _states.Values
      .Where(state => activePositionIds.Contains(state.PositionId))
      .Select(GroupId)
      .Distinct()
      .ToArray();
    _log(
      "auto-trade independent_group_rejected_non_hedged"
        + $" account_type={_account?.AccountType ?? "unknown"}"
        + $" broker_hedging_capability={_accountSupportsHedging}"
        + $" incoming_candidate_id={Short(candidate.CandidateId)}"
        + $" incoming_group_id={CandidateGroupId(candidate)}"
        + $" active_position_ids=[{string.Join(",", activePositionIds)}]"
        + $" active_group_ids=[{string.Join(",", activeGroupIds)}]"
        + " reason=independent_strategy_requires_hedged_account"
    );
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "independent_group_rejected_non_hedged",
      cancellationToken
    );
    return await RejectAsync(
      candidate,
      "independent_strategy_requires_hedged_account",
      cancellationToken
    );
  }

  private async Task<bool> TryRejectOppositeInitialGroupAsync(
    TradeCandidate candidate,
    TradeDirection direction,
    SymbolInfo symbol,
    CancellationToken cancellationToken
  )
  {
    if (
      !string.IsNullOrWhiteSpace(candidate.ParentGroupId)
      || IsManualAlgoCandidate(candidate)
    )
    {
      return false;
    }
    if (
      !_states.Values.Any(state =>
        state.SymbolId == symbol.SymbolId
        && state.Direction != direction
        && string.IsNullOrWhiteSpace(state.ParentGroupId)
      )
      && !_allSymbolPendingOrders.Any(order =>
        order.SymbolId == symbol.SymbolId
        && order.Label == options.Label
        && order.Direction != direction
      )
    )
    {
      return false;
    }
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "executor_opposite_initial_rejected",
      cancellationToken
    );
    return await RejectAsync(
      candidate,
      "opposite autonomous initial group is already active",
      cancellationToken
    );
  }

  private bool CanOpenNewGroup(TradeDirection direction)
  {
    if (
      _allSymbolPositions.Any(position => position.Label != options.Label)
      || _allSymbolPendingOrders.Any(order => order.Label != options.Label)
    )
    {
      return false;
    }
    var botPositions = _allSymbolPositions
      .Where(position => position.Label == options.Label)
      .ToArray();
    var botOrders = _allSymbolPendingOrders
      .Where(order => order.Label == options.Label)
      .ToArray();
    if (
      !_accountSupportsHedging
      && options.AllowConcurrentStrategies
      && options.NonHedgedOppositePolicy == "broker_netting"
    )
    {
      return true;
    }
    return ExposurePolicyRules.AllowsNewGroup(
      EffectiveExposurePolicy(),
      direction,
      botPositions,
      botOrders
    );
  }

  private async Task CloseOppositeExposureForNonHedgedAsync(
    TradeCandidate candidate,
    TradeDirection direction,
    CancellationToken cancellationToken
  )
  {
    var oppositeOrders = _allSymbolPendingOrders
      .Where(order =>
        order.Label == options.Label
        && order.Direction != direction
      )
      .ToArray();
    var oppositePositions = _allSymbolPositions
      .Where(position =>
        position.Label == options.Label
        && position.Direction != direction
      )
      .ToArray();
    if (oppositeOrders.Length == 0 && oppositePositions.Length == 0)
    {
      return;
    }
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "range_flip_attempted",
      cancellationToken
    );
    await PublishAsync(
      "range_flip_attempted",
      $"non-hedged demo close-and-reverse for {candidate.Direction} "
        + $"candidate {Short(candidate.CandidateId)}",
      cancellationToken,
      candidate.CandidateId,
      groupId: CandidateGroupId(candidate),
      setup: candidate.Setup,
      direction: candidate.Direction
    );
    foreach (var order in oppositeOrders)
    {
      await RequireClient().CancelPendingOrderAsync(
        order.OrderId,
        cancellationToken
      );
      _allSymbolPendingOrders = _allSymbolPendingOrders
        .Where(item => item.OrderId != order.OrderId)
        .ToArray();
      await MaybeDeleteGroupPlanAsync(
        ParseManualExpiry(order.Comment)?.GroupId
          ?? ParseZoneComment(order.Comment)?.GroupId,
        cancellationToken
      );
    }
    foreach (var position in oppositePositions)
    {
      var terminalGroupId = _states.TryGetValue(
        position.PositionId,
        out var ownedState
      )
        ? GroupId(ownedState)
        : null;
      await RequireClient().ClosePositionAsync(
        position.PositionId,
        position.Volume,
        cancellationToken
      );
      _states.Remove(position.PositionId);
      await store.DeletePositionAsync(position.PositionId, cancellationToken);
      await MaybeDeleteGroupPlanAsync(terminalGroupId, cancellationToken);
    }
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "range_flip_filled",
      cancellationToken
    );
    await PublishAsync(
      "range_flip_filled",
      $"closed {oppositePositions.Length} opposite position(s) and "
        + $"{oppositeOrders.Length} pending order(s) before "
        + $"{candidate.Direction} entry",
      cancellationToken,
      candidate.CandidateId,
      groupId: CandidateGroupId(candidate),
      setup: candidate.Setup,
      direction: candidate.Direction
    );
  }

  private async Task RecordRangeExecutionMetricsAsync(
    TradeCandidate candidate,
    TradeDirection direction,
    CancellationToken cancellationToken
  )
  {
    if (!IsBoxRangeScalp(candidate))
    {
      return;
    }
    var existingDirections = _allSymbolPositions
      .Where(position => position.Label == options.Label)
      .Select(position => position.Direction)
      .Concat(
        _allSymbolPendingOrders
          .Where(order => order.Label == options.Label)
          .Select(order => order.Direction)
      )
      .ToArray();
    if (existingDirections.Length == 0)
    {
      return;
    }
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "range_box_executed_with_existing_exposure",
      cancellationToken
    );
    if (existingDirections.Any(value => value == direction))
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "range_box_executed_with_same_direction_exposure",
        cancellationToken
      );
    }
    if (existingDirections.Any(value => value != direction))
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "range_box_executed_with_opposite_exposure",
        cancellationToken
      );
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "range_two_sided_simultaneous",
        cancellationToken
      );
    }
  }

  private static string CandidateGroupId(TradeCandidate candidate) =>
    GroupToken(
      string.IsNullOrWhiteSpace(candidate.GroupId)
        ? candidate.CandidateId
        : candidate.GroupId
    );

  private async Task<bool> HasActiveDuplicateReactionAsync(
    TradeCandidate candidate,
    CancellationToken cancellationToken
  )
  {
    var reactionId = candidate.ReactionId;
    if (string.IsNullOrWhiteSpace(reactionId))
    {
      return false;
    }
    if (
      _states.Values.Any(state =>
        string.Equals(state.ReactionId, reactionId, StringComparison.Ordinal)
        && string.IsNullOrWhiteSpace(state.ParentGroupId)
      )
    )
    {
      return true;
    }
    var candidateGroupId = CandidateGroupId(candidate);
    if (
      _allSymbolPendingOrders.Any(order =>
        order.Label == options.Label
        && order.Comment.Contains(
          GroupToken(candidateGroupId),
          StringComparison.Ordinal
        )
      )
    )
    {
      return true;
    }
    var claim = await store.GetValueAsync(
      $"auto_trade:reaction_claim:{reactionId}",
      cancellationToken
    );
    if (string.IsNullOrWhiteSpace(claim))
    {
      return false;
    }
    if (!TryParseClaim(claim, out var parsed))
    {
      // Legacy substring fallback when claim JSON is malformed.
      if (claim.Contains($"\"candidate_id\":\"{candidate.CandidateId}\"", StringComparison.Ordinal))
      {
        return false;
      }
      return !(
        claim.Contains("\"state\":\"closed\"", StringComparison.Ordinal)
        || claim.Contains("\"state\":\"cancelled\"", StringComparison.Ordinal)
        || claim.Contains("\"state\":\"rejected\"", StringComparison.Ordinal)
        || claim.Contains("\"state\":\"expired\"", StringComparison.Ordinal)
        || claim.Contains("\"state\":\"terminal\"", StringComparison.Ordinal)
      );
    }
    if (string.Equals(parsed.CandidateId, candidate.CandidateId, StringComparison.Ordinal))
    {
      return false;
    }
    return !IsTerminalClaimState(parsed.State);
  }

  private async Task<bool> HasActiveDuplicateMappedThesisAsync(
    TradeCandidate candidate,
    CancellationToken cancellationToken
  )
  {
    if (!options.MapThesisLockEnabled)
    {
      return false;
    }
    var thesisId = candidate.ThesisId;
    if (string.IsNullOrWhiteSpace(thesisId))
    {
      return false;
    }
    // Explicit linked scale-ins are not initial thesis occupancy.
    if (!string.IsNullOrWhiteSpace(candidate.ParentGroupId))
    {
      return false;
    }
    if (
      _states.Values.Any(state =>
        string.Equals(state.ThesisId, thesisId, StringComparison.Ordinal)
        && string.IsNullOrWhiteSpace(state.ParentGroupId)
      )
    )
    {
      return true;
    }
    var candidateGroupId = CandidateGroupId(candidate);
    if (
      _allSymbolPendingOrders.Any(order =>
        order.Label == options.Label
        && order.Comment.Contains(
          GroupToken(candidateGroupId),
          StringComparison.Ordinal
        )
      )
    )
    {
      // Pending for this exact group is handled elsewhere; different reaction
      // / group with same thesis is caught via claim below.
    }
    // Persisted group plans with the same thesis.
    // Scan is not available on the store interface in all fakes; rely on claim.
    var claim = await store.GetValueAsync(
      $"auto_trade:thesis_claim:{thesisId}",
      cancellationToken
    );
    if (string.IsNullOrWhiteSpace(claim))
    {
      return false;
    }
    if (!TryParseClaim(claim, out var parsed))
    {
      if (claim.Contains($"\"candidate_id\":\"{candidate.CandidateId}\"", StringComparison.Ordinal))
      {
        return false;
      }
      return !(
        claim.Contains("\"state\":\"rearm_ready\"", StringComparison.Ordinal)
        || claim.Contains("\"state\":\"closed\"", StringComparison.Ordinal)
        || claim.Contains("\"state\":\"cancelled\"", StringComparison.Ordinal)
        || claim.Contains("\"state\":\"rejected\"", StringComparison.Ordinal)
        || claim.Contains("\"state\":\"expired\"", StringComparison.Ordinal)
      );
    }
    if (string.Equals(parsed.CandidateId, candidate.CandidateId, StringComparison.Ordinal))
    {
      return false;
    }
    if (string.Equals(parsed.State, "rearm_ready", StringComparison.OrdinalIgnoreCase))
    {
      return false;
    }
    if (IsTerminalClaimState(parsed.State) && parsed.RearmReady)
    {
      return false;
    }
    // Active + post-terminal waiting-rearm states block another initial order.
    return true;
  }

  private static bool IsTerminalClaimState(string? state)
  {
    if (string.IsNullOrWhiteSpace(state))
    {
      return false;
    }
    return state.Equals("closed", StringComparison.OrdinalIgnoreCase)
      || state.Equals("cancelled", StringComparison.OrdinalIgnoreCase)
      || state.Equals("rejected", StringComparison.OrdinalIgnoreCase)
      || state.Equals("expired", StringComparison.OrdinalIgnoreCase)
      || state.Equals("terminal", StringComparison.OrdinalIgnoreCase);
  }

  private static bool TryParseClaim(string raw, out RedisClaimPayload parsed)
  {
    try
    {
      var value = System.Text.Json.JsonSerializer.Deserialize(
        raw,
        RedisJsonContext.Default.RedisClaimPayload
      );
      if (value is null)
      {
        parsed = default!;
        return false;
      }
      parsed = value;
      return true;
    }
    catch (System.Text.Json.JsonException)
    {
      parsed = default!;
      return false;
    }
  }

  private IReadOnlyList<long> PendingOrderIdsForGroup(string? groupId)
  {
    if (string.IsNullOrWhiteSpace(groupId))
    {
      return [];
    }
    var groupToken = $"|{GroupToken(groupId)}|";
    return _allSymbolPendingOrders
      .Where(order =>
        order.Label == options.Label
        && order.Comment.Contains(groupToken, StringComparison.Ordinal)
      )
      .Select(order => order.OrderId)
      .ToArray();
  }

  private async Task MaybeDeleteGroupPlanAsync(
    string? groupId,
    CancellationToken cancellationToken
  )
  {
    if (
      string.IsNullOrWhiteSpace(groupId)
      || _states.Values.Any(state => GroupId(state) == groupId)
      || PendingOrderIdsForGroup(groupId).Count > 0
    )
    {
      return;
    }
    await DeleteGroupPlanAsync(groupId, cancellationToken);
  }

  // The caller must pass the route it actually resolved and the exact client
  // order IDs it is about to submit. The plan never re-derives an execution-
  // critical identity from candidate declarations (which may be `either`).
  private async Task SaveGroupPlanAsync(
    TradeCandidate candidate,
    string groupId,
    CancellationToken cancellationToken,
    string? streamEventId,
    string route,
    IReadOnlyList<string> clientOrderIds
  )
  {
    var resolvedRoute = route;
    var plan = new AutoTradeGroupPlan(
      candidate.CandidateId,
      groupId,
      candidate.MatchId,
      candidate.StrategyFamily,
      candidate.RangeId,
      candidate.Setup,
      candidate.Direction,
      _clock().ToUnixTimeSeconds(),
      candidate.ManualTakeProfits,
      candidate.ManualStopLoss,
      candidate.ZoneId,
      candidate.TriggerId,
      candidate.ParentGroupId,
      candidate.StructuralSource,
      candidate.ReactionId,
      candidate.ThesisId,
      candidate.StructuralZoneId,
      candidate.StructuralZoneLow,
      candidate.StructuralZoneHigh,
      candidate.RiskMultiplier,
      candidate.TargetModel,
      candidate.AbsoluteTargetPrice,
      StreamEventId: streamEventId,
      Route: resolvedRoute,
      ClientOrderIds: clientOrderIds,
      SubmittedAt: _clock().ToUnixTimeSeconds()
    );
    await store.SaveGroupPlanAsync(
      plan,
      TimeSpan.FromSeconds(Math.Max(
        300,
        options.CandidateStorageTtlSeconds
      )),
      cancellationToken
    );
  }

  private Task DeleteGroupPlanAsync(
    string groupId,
    CancellationToken cancellationToken
  ) => store.DeleteGroupPlanAsync(groupId, cancellationToken);

  private async Task<AutoTradeGroupPlan?> LoadGroupPlanAsync(
    string groupId,
    CancellationToken cancellationToken
  )
  {
    var raw = await store.GetValueAsync(
      $"auto_trade:group_plan:{groupId}",
      cancellationToken
    );
    if (string.IsNullOrWhiteSpace(raw))
    {
      return null;
    }
    try
    {
      return JsonSerializer.Deserialize(
        raw,
        RedisJsonContext.Default.AutoTradeGroupPlan
      );
    }
    catch (JsonException)
    {
      return null;
    }
  }

  private static bool SameStrategyFamily(
    AutoTradePositionState state,
    TradeCandidate candidate
  )
  {
    var current = string.IsNullOrWhiteSpace(state.StrategyFamily)
      ? StrategyFamilyFromSetup(state.Setup)
      : state.StrategyFamily;
    var incoming = string.IsNullOrWhiteSpace(candidate.StrategyFamily)
      ? StrategyFamilyFromSetup(candidate.Setup)
      : candidate.StrategyFamily;
    return string.IsNullOrWhiteSpace(current)
      || string.IsNullOrWhiteSpace(incoming)
      || current.Equals(incoming, StringComparison.OrdinalIgnoreCase);
  }

  private static string StrategyFamilyFromSetup(string? setup)
  {
    if (string.IsNullOrWhiteSpace(setup))
    {
      return "";
    }
    var value = setup.Split('·', 2)[0].Trim().ToLowerInvariant();
    if (value.Contains("mapped"))
    {
      return "mapped_zone";
    }
    if (value.Contains("key level"))
    {
      return "key_level";
    }
    if (
      value.Contains("demand zone")
      || value.Contains("supply zone")
      || value == "zone reaction"
    )
    {
      return "supply_demand";
    }
    if (value.Contains("session level"))
    {
      return "session_level";
    }
    if (value.Contains("trendline"))
    {
      return "trendline";
    }
    if (value.Contains("range"))
    {
      return "range";
    }
    if (value.Contains("trend") || value.Contains("breakout"))
    {
      return "trend";
    }
    if (value.Contains("map"))
    {
      return "mapped_zone";
    }
    if (value.Contains("manual"))
    {
      return "manual";
    }
    return value.Replace(' ', '_');
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

  private ExposurePolicy EffectiveExposurePolicy()
  {
    if (
      options.ExposurePolicy == ExposurePolicy.HedgedConcurrent
      && !_accountSupportsHedging
    )
    {
      return ExposurePolicy.SameDirectionConcurrent;
    }
    return options.ExposurePolicy;
  }

  private async Task<AutoTradeConfigHealthResult> PublishConfigurationAsync(
    TradingAccountSnapshot account,
    SymbolInfo symbol,
    CancellationToken cancellationToken
  )
  {
    var generatedAt = _clock().ToUnixTimeSeconds();
    var manifest = AutoTradeConfigHealth.Build(
      options,
      account,
      symbol,
      generatedAt
    );
    var encoded = JsonSerializer.Serialize(
      manifest,
      RedisJsonContext.Default.AutoTradeConfigManifest
    );
    await store.SetValueAsync(
      AutoTradeConfigHealth.CTraderManifestKey,
      encoded,
      cancellationToken
    );
    var python = await store.GetValueAsync(
      AutoTradeConfigHealth.PythonManifestKey,
      cancellationToken
    );
    var health = AutoTradeConfigHealth.Compare(manifest, python);
    await store.SetValueAsync(
      AutoTradeConfigHealth.HealthKey,
      AutoTradeConfigHealth.SerializeHealth(
        health,
        options.Profile,
        generatedAt
      ),
      cancellationToken
    );
    _log(
      "AUTO-TRADE CONFIG service=ctrader-engine "
      + $"profile={manifest.Profile} enabled={manifest.AutoTradeEnabled} "
      + $"dry_run={manifest.DryRun} candidate_stream={manifest.CandidateStream} "
      + $"event_stream={manifest.EventStream} "
      + $"symbols=[{string.Join(',', manifest.Symbols)}] "
      + $"targets=[{string.Join(',', manifest.TargetPlans)}] "
      + $"range_targets=[{string.Join(',', manifest.RangeTargetPlans)}] "
      + $"candidate_max_age={manifest.CandidateExecutionMaxAgeSeconds} "
      + $"candidate_storage_ttl={manifest.CandidateStorageTtlSeconds} "
      + $"range_flip={manifest.RangeFlip} "
      + $"two_sided={manifest.TwoSidedRange} "
      + $"concurrent={manifest.ConcurrentStrategies} "
      + $"counter_bias={manifest.AllowCounterBias} "
      + $"broker={manifest.Broker} account_mode={manifest.AccountMode} "
      + $"broker_hedged={manifest.BrokerHedgingCapability} "
      + $"contract_version={manifest.CandidateContractVersion} "
      + $"deprecated=[{string.Join(',', manifest.DeprecatedVariables ?? [])}] "
      + "sources=["
      + string.Join(
        ',',
        (manifest.ConfigSources ?? new Dictionary<string, string>())
          .OrderBy(item => item.Key)
          .Select(item => $"{item.Key}={item.Value}")
      )
      + "]"
    );
    if (health.State != "healthy")
    {
      await store.IncrementMetricAsync(
        symbol.RedisSymbol,
        "config_mismatch",
        cancellationToken
      );
    }
    await PublishAsync(
      health.State == "fatal" ? "config_fatal" : "config_health",
      $"configuration health {health.State}"
        + (health.Fatal.Count > 0
          ? $" · fatal={string.Join(',', health.Fatal)}"
          : "")
        + (health.Warnings.Count > 0
          ? $" · warning={string.Join(',', health.Warnings)}"
          : ""),
      cancellationToken
    );
    return health;
  }

  private Task PublishReadinessAsync(
    bool ready,
    string state,
    AutoTradeConfigHealthResult health,
    CancellationToken cancellationToken
  ) => store.SetValueAsync(
    AutoTradeConfigHealth.ReadinessKey,
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

  // Linked session + ownership token for broker calls that are safe to cancel
  // before an authoritative response. Cancellation after submission may have
  // begun never proves absence — callers classify that as outcome-unknown.
  private CancellationTokenSource CreateBrokerCancellation(
    CancellationToken sessionCancellationToken
  )
  {
    if (_heartbeat is CandidateLeaseHeartbeat heartbeat)
    {
      return CancellationTokenSource.CreateLinkedTokenSource(
        sessionCancellationToken,
        heartbeat.OwnershipToken
      );
    }
    return CancellationTokenSource.CreateLinkedTokenSource(sessionCancellationToken);
  }

  private bool BrokerOwnershipCancelled() =>
    _heartbeat?.OwnershipLost == true
    || (_heartbeat is not null && _heartbeat.OwnershipToken.IsCancellationRequested);

  private BrokerOutcomeUnknownException ClassifyBrokerUncertainty(
    TradeCandidate candidate,
    string clientOrderId,
    Exception? inner = null
  ) => new(
    candidate.CandidateId,
    clientOrderId,
    inner,
    leaseLostAfterBroker: BrokerOwnershipCancelled()
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

  private async Task<bool> RenewActiveLeaseAsync(CancellationToken cancellationToken)
  {
    if (_activeLease is null)
    {
      return false;
    }
    if (_heartbeat is CandidateLeaseHeartbeat heartbeat)
    {
      return await heartbeat.EnsureOwnershipAsync(cancellationToken);
    }
    return await store.RenewCandidateLeaseAsync(
      _activeLease.CandidateId,
      _activeLease.StreamEventId,
      _activeLease.Token,
      CandidateLeaseDuration,
      cancellationToken
    );
  }

  // Ownership gate for a broker side effect. Verifies the lease immediately
  // before the call and moves the record into `broker_submitting` so a crash
  // between here and the broker response can never be mistaken for a safe
  // pre-submit failure.
  private async Task<bool> EnsureBrokerLeaseAsync(CancellationToken cancellationToken)
  {
    if (_activeLease is null || !await RenewActiveLeaseAsync(cancellationToken))
    {
      return false;
    }
    var record = await store.GetCandidateRecordAsync(
      _activeLease.CandidateId,
      cancellationToken
    );
    if (record?.State == CandidateExecutionStates.BrokerSubmitting)
    {
      // Already submitting (multi-leg zone fill): ownership is proven and the
      // state does not need to be re-entered.
      return true;
    }
    return await store.TransitionCandidateStateAsync(
      _activeLease.CandidateId,
      _activeLease.StreamEventId,
      _activeLease.Token,
      CandidateExecutionStates.BrokerSubmitting,
      cancellationToken
    );
  }

  // Verifies ownership without changing state. Used before every zone-fill leg
  // and before any rollback cancellation so a stale executor cannot mutate
  // orders that now belong to a successor.
  private async Task<bool> StillOwnsCandidateAsync(CancellationToken cancellationToken)
  {
    if (_activeLease is null)
    {
      return false;
    }
    return await RenewActiveLeaseAsync(cancellationToken);
  }

  private async Task<bool> CompleteActiveCandidateAsync(
    string outcome,
    CancellationToken cancellationToken
  )
  {
    if (_activeLease is null)
    {
      return false;
    }
    var completed = await store.CompleteCandidateAsync(
      _activeLease.CandidateId,
      _activeLease.StreamEventId,
      _activeLease.Token,
      outcome,
      cancellationToken
    );
    if (!completed)
    {
      await store.IncrementMetricAsync(
        CandidateSymbolHint(),
        "executor_stale_complete_blocked",
        cancellationToken
      );
      _log(
        "auto-trade fenced completion rejected for "
        + $"{Short(_activeLease.CandidateId)} lease {_activeLease.Correlation}"
      );
    }
    return completed;
  }

  private async Task<bool> ReleaseActiveCandidateAsync(
    CancellationToken cancellationToken,
    string? lastError = null
  )
  {
    if (_activeLease is null)
    {
      return false;
    }
    var released = await store.ReleaseCandidateAsync(
      _activeLease.CandidateId,
      _activeLease.StreamEventId,
      _activeLease.Token,
      cancellationToken,
      lastError
    );
    if (!released)
    {
      await store.IncrementMetricAsync(
        CandidateSymbolHint(),
        "executor_stale_release_blocked",
        cancellationToken
      );
    }
    return released;
  }

  private async Task<bool> MarkBrokerOutcomeUnknownAsync(
    CancellationToken cancellationToken,
    string? reason = null
  )
  {
    if (_activeLease is null)
    {
      return false;
    }
    var marked = await store.TransitionCandidateStateAsync(
      _activeLease.CandidateId,
      _activeLease.StreamEventId,
      _activeLease.Token,
      CandidateExecutionStates.BrokerOutcomeUnknown,
      cancellationToken,
      reason
    );
    if (marked)
    {
      await store.IncrementMetricAsync(
        CandidateSymbolHint(),
        "broker_outcome_unknown_preserved",
        cancellationToken
      );
    }
    return marked;
  }

  private string CandidateSymbolHint() =>
    _symbol?.RedisSymbol ?? options.CanonicalSymbol;

  // Safe pre-submit failure: hand the candidate back so it can be retried.
  // Never called after a broker request whose outcome is unknown.
  private async Task ReleaseRetryableAsync(
    TradeCandidate candidate,
    string reason,
    CancellationToken cancellationToken
  )
  {
    if (_heartbeat?.OwnershipLost == true)
    {
      await PublishOwnershipLossAsync(candidate, cancellationToken);
      return;
    }
    if (await ReleaseActiveCandidateAsync(cancellationToken, reason))
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "lifecycle_retryable_error",
        cancellationToken
      );
    }
  }

  private async Task PublishOwnershipLossAsync(
    TradeCandidate candidate,
    CancellationToken cancellationToken
  )
  {
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "executor_lease_lost_before_broker",
      cancellationToken
    );
    _log(
      $"auto-trade candidate {Short(candidate.CandidateId)} lease lost; "
      + "leaving state to the current owner"
    );
    await PublishAsync(
      "candidate_lease_lost",
      $"candidate {Short(candidate.CandidateId)} lease lost during execution",
      cancellationToken,
      candidate.CandidateId,
      groupId: CandidateGroupId(candidate)
    );
  }

  private async Task PublishCandidateIntegrityErrorAsync(
    TradeCandidate candidate,
    string reason,
    CancellationToken cancellationToken
  )
  {
    _log(
      $"auto-trade candidate {Short(candidate.CandidateId)} integrity error: {reason}"
    );
    await PublishAsync(
      "candidate_integrity_error",
      $"candidate {Short(candidate.CandidateId)} integrity error: {reason}",
      cancellationToken,
      candidate.CandidateId,
      groupId: CandidateGroupId(candidate),
      reasonCode: reason
    );
  }

  // A broker request may have been accepted. Preserve the recovery state, keep
  // the group plan and deterministic identities, and never release to a
  // normal retry.
  private async Task<bool> HandleBrokerOutcomeUnknownAsync(
    TradeCandidate candidate,
    BrokerOutcomeUnknownException exception,
    CancellationToken cancellationToken
  )
  {
    await MarkBrokerOutcomeUnknownAsync(cancellationToken, "broker_response_lost");
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "broker_response_unknown",
      cancellationToken
    );
    if (exception.LeaseLostAfterBroker)
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "executor_lease_lost_after_broker",
        cancellationToken
      );
    }
    _log(
      $"auto-trade candidate {Short(candidate.CandidateId)} broker outcome unknown "
      + $"for client order {exception.ClientOrderId}: {exception.InnerException?.Message}"
    );
    await PublishAsync(
      "broker_outcome_unknown",
      $"candidate {Short(candidate.CandidateId)} broker outcome unknown",
      cancellationToken,
      candidate.CandidateId,
      groupId: CandidateGroupId(candidate),
      reasonCode: "broker_outcome_unknown"
    );
    return false;
  }

  // Deterministic broker reconciliation for a recovery-required candidate.
  // Adoption or confirmed absence (via direct identity or absence quorum) are
  // the only ways out; a single empty snapshot leaves the record unknown.
  private async Task<bool> ReconcileBrokerOutcomeAsync(
    TradeCandidate candidate,
    TradeStreamEntry entry,
    CancellationToken cancellationToken
  )
  {
    if (!_ready || _client is null || _symbol is null)
    {
      return false;
    }
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "broker_recovery_started",
      cancellationToken
    );
    var claim = await store.TryClaimCandidateAsync(
      candidate.CandidateId,
      entry.Id,
      CandidateLeaseDuration,
      cancellationToken,
      CandidateClaimPolicy.Recovery
    );
    if (claim.Lease is not CandidateExecutionLease lease)
    {
      // The previous owner is still inside its lease, or the record moved on.
      return claim.AdvancesCursor;
    }
    _activeLease = lease;
    // The recovery heartbeat covers the entire operation: group-plan load,
    // every broker snapshot, the delays between snapshots, and the final
    // fenced mutation. While it renews, no second recovery worker can claim
    // this candidate; when it loses ownership, all broker queries and delays
    // are cancelled through the linked token.
    await using var heartbeat = CandidateLeaseHeartbeat.Start(
      store,
      lease,
      CandidateLeaseDuration,
      cancellationToken,
      CandidateHeartbeatInterval,
      HeartbeatDelay,
      (metric, token) => store.IncrementMetricAsync(
        candidate.Symbol,
        metric,
        token
      ),
      _log
    );
    _heartbeat = heartbeat;
    using var recoveryCts = CancellationTokenSource.CreateLinkedTokenSource(
      cancellationToken,
      heartbeat.OwnershipToken
    );
    try
    {
      var disposition = await WithGateAsync(
        () => RecoverBrokerOutcomeAsync(candidate, entry, recoveryCts.Token),
        recoveryCts.Token
      );
      // Adoption completes the candidate (cursor may advance on the next
      // terminal claim). Confirmed absence releases to retryable and holds
      // the cursor for the retry. StillUnknown / Conflict hold the cursor.
      return disposition is BrokerRecoveryDisposition.AdoptedPosition
        or BrokerRecoveryDisposition.AdoptedPendingOrder;
    }
    catch (CandidateLeaseLostException)
    {
      // A successor owns recovery now. This worker must not release the
      // record, delete the group plan, or count confirmation progress: it
      // leaves everything for the current or next recovery owner.
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "broker_recovery_ownership_lost",
        cancellationToken
      );
      _log(
        $"auto-trade recovery ownership lost for {Short(candidate.CandidateId)}"
      );
      return false;
    }
    catch (OperationCanceledException) when (heartbeat.OwnershipLost)
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "broker_recovery_ownership_lost",
        cancellationToken
      );
      _log(
        $"auto-trade recovery ownership lost for {Short(candidate.CandidateId)}"
      );
      return false;
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      // Broker view is unusable: restore the recovery state rather than
      // guessing that nothing was placed. Fenced, so a stale worker cannot
      // overwrite a successor's record.
      if (!heartbeat.OwnershipLost)
      {
        await MarkBrokerOutcomeUnknownAsync(
          cancellationToken,
          "reconciliation_failed"
        );
      }
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "broker_recovery_still_unknown",
        cancellationToken
      );
      _log(
        $"auto-trade broker reconciliation failed for {Short(candidate.CandidateId)}: "
        + exception.Message
      );
      return false;
    }
    finally
    {
      _activeLease = null;
      _heartbeat = null;
    }
  }

  private async Task<BrokerRecoveryDisposition> RecoverBrokerOutcomeAsync(
    TradeCandidate candidate,
    TradeStreamEntry entry,
    CancellationToken cancellationToken
  )
  {
    var groupId = CandidateGroupId(candidate);
    var plan = await LoadGroupPlanAsync(groupId, cancellationToken);
    var requiredConfirmations = options.BrokerAbsenceConfirmations;
    var recheck = TimeSpan.FromSeconds(options.BrokerAbsenceRecheckSeconds);
    var deadline = _clock().AddSeconds(options.BrokerRecoveryTimeoutSeconds);
    var recoveryAttempt = (plan?.RecoveryAttempt ?? 0) + 1;
    // Ensure deterministic identities survive even when the group plan was
    // never written (legacy records) before the first broker side effect.
    plan = await PersistRecoveryProgressAsync(
      candidate,
      entry,
      plan,
      recoveryAttempt,
      cancellationToken
    );
    var delay = RecoveryDelay ?? ((wait, token) => Task.Delay(wait, token));

    while (_clock() <= deadline)
    {
      await ReconcileAsync(cancellationToken);
      var adopted = await TryAdoptBrokerOutcomeAsync(
        candidate,
        plan,
        cancellationToken
      );
      if (adopted is BrokerRecoveryDisposition.AdoptedPosition
        or BrokerRecoveryDisposition.AdoptedPendingOrder)
      {
        await store.ClearBrokerAbsenceProgressAsync(
          candidate.CandidateId,
          cancellationToken
        );
        return adopted.Value;
      }
      if (adopted == BrokerRecoveryDisposition.Conflict)
      {
        // Conflicting candidate-related broker objects block confirmation:
        // absence progress is never advanced while they exist.
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "broker_recovery_conflict",
          cancellationToken
        );
        await MarkBrokerOutcomeUnknownAsync(cancellationToken, "recovery_conflict");
        return BrokerRecoveryDisposition.Conflict;
      }

      // Empty snapshot: never confirm absence from one look, and never count
      // a snapshot before the durable Redis-time interval has elapsed since
      // the previously persisted confirmation.
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "broker_recovery_empty_snapshot",
        cancellationToken
      );
      var progress = await store.TryRecordBrokerAbsenceCheckAsync(
        candidate.CandidateId,
        entry.Id,
        _activeLease?.Token ?? "",
        options.BrokerAbsenceRecheckSeconds,
        TimeSpan.FromSeconds(Math.Max(300, options.CandidateStorageTtlSeconds)),
        cancellationToken
      );
      if (progress is null)
      {
        // Fenced out: a successor recovery owner holds the record now. This
        // worker must stop without counting, releasing or deleting anything.
        throw new CandidateLeaseLostException(candidate.CandidateId);
      }
      if (!progress.Recorded)
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "broker_recovery_confirmation_deferred",
          cancellationToken
        );
        var remaining = TimeSpan.FromSeconds(Math.Max(
          1,
          options.BrokerAbsenceRecheckSeconds - progress.SecondsSincePrevious
        ));
        if (_clock().Add(remaining) > deadline)
        {
          break;
        }
        await delay(remaining, cancellationToken);
        continue;
      }
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "broker_recovery_confirmation_recorded",
        cancellationToken
      );
      if (progress.Confirmations >= requiredConfirmations)
      {
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "broker_recovery_absence_confirmed",
          cancellationToken
        );
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "broker_outcome_confirmed_absent",
          cancellationToken
        );
        // Fenced release first: only the current recovery owner may hand the
        // candidate back. The group plan survives until the release succeeds.
        if (!await ReleaseActiveCandidateAsync(
          cancellationToken,
          "broker_outcome_confirmed_absent"
        ))
        {
          throw new CandidateLeaseLostException(candidate.CandidateId);
        }
        await store.ClearBrokerAbsenceProgressAsync(
          candidate.CandidateId,
          cancellationToken
        );
        // Absence is durably proven: the group plan is no longer needed for
        // adoption.
        await DeleteGroupPlanAsync(groupId, cancellationToken);
        await store.IncrementMetricAsync(
          candidate.Symbol,
          "candidate_retry_waiting",
          cancellationToken
        );
        return BrokerRecoveryDisposition.ConfirmedAbsent;
      }
      if (_clock().Add(recheck) > deadline)
      {
        break;
      }
      await delay(recheck, cancellationToken);
    }

    await store.IncrementMetricAsync(
      candidate.Symbol,
      "broker_recovery_still_unknown",
      cancellationToken
    );
    // Timeout before quorum: the record stays recovery-required and the
    // persisted confirmation progress remains for the next recovery attempt.
    await MarkBrokerOutcomeUnknownAsync(cancellationToken, "absence_quorum_pending");
    return BrokerRecoveryDisposition.StillUnknown;
  }

  // Deterministic broker evidence classification for one reconciled snapshot.
  // Matching priority:
  //   1. exact broker ClientOrderId equality (direct lookup);
  //   2. exact persisted client-order identity inside broker metadata
  //      (comment / label);
  //   3. legacy candidate-token fallback, only for objects that carry no
  //      client-order identity at all.
  // An object that references this candidate but carries a different exact
  // client-order identity is a conflict, never an adoption and never absence.
  private async Task<BrokerRecoveryDisposition?> TryAdoptBrokerOutcomeAsync(
    TradeCandidate candidate,
    AutoTradeGroupPlan? plan,
    CancellationToken cancellationToken
  )
  {
    var candidateToken = CandidateToken(candidate.CandidateId);
    var expectedIds = plan?.ClientOrderIds is { Count: > 0 } persisted
      ? persisted
      : BuildClientOrderIds(candidate, plan?.Route);

    var matchedPositions = new List<TradingPosition>();
    var matchedPending = new List<TradingPendingOrder>();
    var directMatches = 0;
    var legacyMatches = 0;
    var conflict = false;
    var seenDirectIds = new HashSet<string>(StringComparer.Ordinal);

    void Classify(
      string clientOrderId,
      string comment,
      string label,
      Action adopt
    )
    {
      if (!string.IsNullOrEmpty(clientOrderId))
      {
        // The broker reports the exact identity: trust it exclusively.
        if (expectedIds.Contains(clientOrderId, StringComparer.Ordinal))
        {
          if (!seenDirectIds.Add(clientOrderId))
          {
            // Duplicated leg identity: two broker objects claim the same
            // deterministic client order ID.
            conflict = true;
            return;
          }
          directMatches++;
          adopt();
          return;
        }
        if (
          comment.Contains(candidateToken, StringComparison.Ordinal)
          || label.Contains(candidateToken, StringComparison.Ordinal)
          || expectedIds.Any(id =>
            comment.Contains(id, StringComparison.Ordinal)
            || label.Contains(id, StringComparison.Ordinal))
        )
        {
          // Candidate-related object with a different exact identity.
          conflict = true;
        }
        return;
      }
      if (expectedIds.Any(id =>
        comment.Contains(id, StringComparison.Ordinal)
        || label.Contains(id, StringComparison.Ordinal)))
      {
        adopt();
        return;
      }
      if (comment.Contains(candidateToken, StringComparison.Ordinal))
      {
        legacyMatches++;
        adopt();
      }
    }

    foreach (var position in _allSymbolPositions)
    {
      var current = position;
      Classify(
        current.ClientOrderId,
        current.Comment,
        current.Label,
        () => matchedPositions.Add(current)
      );
    }
    foreach (var order in _allSymbolPendingOrders)
    {
      var current = order;
      Classify(
        current.ClientOrderId,
        current.Comment,
        current.Label,
        () => matchedPending.Add(current)
      );
    }

    if (conflict)
    {
      return BrokerRecoveryDisposition.Conflict;
    }
    if (matchedPositions.Count == 0 && matchedPending.Count == 0)
    {
      return null;
    }
    if (directMatches > 0)
    {
      // Only an actual exact client-order-identity match counts as a direct
      // lookup; metadata scans and legacy token fallbacks never do.
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "broker_recovery_direct_lookup",
        cancellationToken
      );
    }
    if (legacyMatches > 0)
    {
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "broker_recovery_legacy_token_lookup",
        cancellationToken
      );
    }

    // Adopt every valid known object: a partial zone outcome (one leg, or a
    // filled leg plus a pending leg) is adopted as-is, never treated as
    // absence and never re-submitted.
    foreach (var position in matchedPositions)
    {
      await AdoptPositionAsync(position, cancellationToken);
    }
    var outcome = matchedPositions.Count > 0
      ? $"ordered:{matchedPositions[0].PositionId}"
      : $"ordered:{matchedPending[0].OrderId}";
    if (!await CompleteActiveCandidateAsync(outcome, cancellationToken))
    {
      // A successor owns the candidate: the stale worker must not publish
      // adoption events or advance anything.
      throw new CandidateLeaseLostException(candidate.CandidateId);
    }
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "broker_outcome_adopted",
      cancellationToken
    );
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "broker_duplicate_prevented",
      cancellationToken
    );
    if (matchedPositions.Count > 0)
    {
      await PublishAsync(
        "order_filled",
        $"adopted existing position {matchedPositions[0].PositionId} for candidate "
        + Short(candidate.CandidateId),
        cancellationToken,
        candidate.CandidateId,
        matchedPositions[0].PositionId,
        groupId: CandidateGroupId(candidate),
        pendingOrderIds: matchedPending.Count > 0
          ? matchedPending.Select(order => order.OrderId).ToArray()
          : null
      );
      return BrokerRecoveryDisposition.AdoptedPosition;
    }
    await PublishAsync(
      "order_accepted",
      $"adopted existing pending order {matchedPending[0].OrderId} for candidate "
      + Short(candidate.CandidateId),
      cancellationToken,
      candidate.CandidateId,
      groupId: CandidateGroupId(candidate),
      pendingOrderIds: matchedPending.Select(order => order.OrderId).ToArray()
    );
    return BrokerRecoveryDisposition.AdoptedPendingOrder;
  }

  private async Task<AutoTradeGroupPlan> PersistRecoveryProgressAsync(
    TradeCandidate candidate,
    TradeStreamEntry entry,
    AutoTradeGroupPlan? plan,
    int recoveryAttempt,
    CancellationToken cancellationToken
  )
  {
    var groupId = CandidateGroupId(candidate);
    var updated = (plan ?? new AutoTradeGroupPlan(
      candidate.CandidateId,
      groupId,
      candidate.MatchId,
      candidate.StrategyFamily,
      candidate.RangeId,
      candidate.Setup,
      candidate.Direction,
      _clock().ToUnixTimeSeconds()
    )) with
    {
      StreamEventId = entry.Id,
      RecoveryAttempt = recoveryAttempt,
      ClientOrderIds = plan?.ClientOrderIds
        ?? BuildClientOrderIds(candidate, plan?.Route),
    };
    await store.SaveGroupPlanAsync(
      updated,
      TimeSpan.FromSeconds(Math.Max(300, options.CandidateStorageTtlSeconds)),
      cancellationToken
    );
    return updated;
  }

  private static IReadOnlyList<string> BuildClientOrderIds(
    TradeCandidate candidate,
    string? route
  )
  {
    var root = ClientOrderId(candidate.CandidateId);
    var normalized = (route ?? candidate.PlannedExecutionRoute ?? "")
      .Trim()
      .ToLowerInvariant();
    if (normalized == "zone_split")
    {
      return [$"{root}-z1", $"{root}-z2"];
    }
    if (normalized is "single_limit" or "limit")
    {
      return [$"{root}-l1"];
    }
    return [root];
  }

  private async Task<bool> RejectAsync(
    TradeCandidate candidate,
    string reason,
    CancellationToken cancellationToken
  )
  {
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "executor_rejected",
      cancellationToken
    );
    await CompleteActiveCandidateAsync(
      $"rejected:{reason}",
      cancellationToken
    );
    await PublishAsync(
      "rejected",
      $"candidate {Short(candidate.CandidateId)} rejected: {reason}",
      cancellationToken,
      candidate.CandidateId,
      groupId: CandidateGroupId(candidate),
      setup: candidate.Setup,
      regime: candidate.Regime,
      confluence: candidate.Confluence,
      direction: candidate.Direction,
      reasonCode: reason,
      matchId: candidate.MatchId,
      rangeId: candidate.RangeId,
      strategyFamily: candidate.StrategyFamily,
      stream: IsManualAlgoCandidate(candidate) ? "algo_manual" : "algo_auto",
      stopLoss: candidate.ManualStopLoss,
      targetPrices: candidate.ManualTakeProfits,
      entryLow: candidate.EntryZone.Low,
      entryHigh: candidate.EntryZone.High,
      structuralSource: candidate.StructuralSource,
      zoneId: candidate.ZoneId,
      structuralZoneId: candidate.StructuralZoneId,
      reactionId: candidate.ReactionId,
      thesisId: candidate.ThesisId
    );
    _log($"auto-trade candidate {Short(candidate.CandidateId)} rejected: {reason}");
    return true;
  }

  private async Task PublishAsync(
    string type,
    string message,
    CancellationToken cancellationToken,
    string? candidateId = null,
    long? positionId = null,
    int? targetPips = null,
    long? volume = null,
    decimal? price = null,
    string? groupId = null,
    int? trancheIndex = null,
    decimal? groupWorstCase = null,
    decimal? riskBudget = null,
    decimal? groupRealizedPnl = null,
    decimal? counterfactualPnl = null,
    bool? hadAdds = null,
    decimal? groupRealizedPips = null,
    decimal? counterfactualPips = null,
    string? setup = null,
    string? regime = null,
    int? confluence = null,
    decimal? stopPips = null,
    IReadOnlyList<int>? targetsPips = null,
    string? stream = null,
    string? direction = null,
    long? remainingVolume = null,
    string? reasonCode = null,
    string? matchId = null,
    string? rangeId = null,
    string? strategyFamily = null,
    IReadOnlyList<long>? pendingOrderIds = null,
    long? orderId = null,
    decimal? stopLoss = null,
    IReadOnlyList<decimal>? targetPrices = null,
    decimal? entryLow = null,
    decimal? entryHigh = null,
    decimal? legRealizedPips = null,
    long? groupInitialVolume = null,
    long? lotSize = null,
    string? structuralSource = null,
    string? zoneId = null,
    string? structuralZoneId = null,
    string? reactionId = null,
    string? thesisId = null,
    decimal? riskMultiplier = null,
    string? targetModel = null,
    string? entryDistribution = null
  )
  {
    var transition = LifecycleTransitionForEvent(type, remainingVolume);
    var owner = candidateId ?? groupId;
    string? currentState = null;
    if (
      !string.IsNullOrWhiteSpace(owner)
      && transition?.MutatesCurrentState == true
    )
    {
      try
      {
        var raw = await store.GetValueAsync(
          $"auto_trade:lifecycle_state:{owner}",
          cancellationToken
        );
        currentState = AutoTradeLifecycle.ParseState(raw);
      }
      catch (Exception exception) when (
        exception is not OperationCanceledException
      )
      {
        _log($"auto-trade lifecycle state read failed: {exception.Message}");
      }
    }

    string? appliedState = null;
    var mutatesLifecycle = false;
    var telemetryNoTransition = transition is null;
    var invalidTransition = false;
    var recoveryTransition = false;
    if (
      transition?.MutatesCurrentState == true
      && !string.IsNullOrWhiteSpace(owner)
    )
    {
      var evaluation = AutoTradeLifecycle.EvaluateTransition(
        currentState,
        transition,
        type
      );
      switch (evaluation.Outcome)
      {
        case LifecycleTransitionOutcome.Applied:
          appliedState = evaluation.AppliedState;
          mutatesLifecycle = true;
          break;
        case LifecycleTransitionOutcome.Recovery:
          appliedState = evaluation.AppliedState;
          mutatesLifecycle = true;
          recoveryTransition = true;
          break;
        case LifecycleTransitionOutcome.Duplicate:
          appliedState = transition.State;
          break;
        case LifecycleTransitionOutcome.Invalid:
          invalidTransition = true;
          break;
      }
    }

    if (
      !string.IsNullOrWhiteSpace(candidateId)
      && _routeIdentityByCandidate.TryGetValue(candidateId, out var remembered)
    )
    {
      structuralSource ??= remembered.StructuralSource;
      zoneId ??= remembered.ZoneId;
      structuralZoneId ??= remembered.StructuralZoneId;
      reactionId ??= remembered.ReactionId;
      thesisId ??= remembered.ThesisId;
    }
    var lifecycleId = Guid.NewGuid().ToString("N");
    var tradeEvent = new AutoTradeEvent(
      type,
      _clock().ToUnixTimeSeconds(),
      message,
      RequireSymbolOrDefault(),
      candidateId,
      positionId,
      targetPips,
      volume,
      price,
      groupId,
      trancheIndex,
      groupWorstCase,
      riskBudget,
      groupRealizedPnl,
      counterfactualPnl,
      hadAdds,
      groupRealizedPips,
      counterfactualPips,
      setup,
      regime,
      confluence,
      stopPips,
      targetsPips,
      stream,
      direction,
      remainingVolume,
      lifecycleId,
      appliedState,
      reasonCode,
      matchId,
      rangeId,
      strategyFamily,
      options.Profile,
      _account?.AccountType,
      _account?.BrokerName,
      candidateId ?? groupId ?? lifecycleId,
      currentState,
      pendingOrderIds,
      orderId,
      stopLoss,
      targetPrices,
      entryLow,
      entryHigh,
      legRealizedPips,
      groupInitialVolume,
      lotSize,
      structuralSource,
      zoneId,
      structuralZoneId,
      reactionId,
      thesisId,
      riskMultiplier,
      targetModel,
      entryDistribution,
      mutatesLifecycle
    );
    await store.PublishAutoTradeEventAsync(
      options.EventStream,
      tradeEvent,
      cancellationToken
    );
    try
    {
      await store.RecordLifecycleEventAsync(tradeEvent, cancellationToken);
      if (telemetryNoTransition)
      {
        await store.IncrementMetricAsync(
          RequireSymbolOrDefault(),
          "lifecycle_telemetry_no_transition",
          cancellationToken
        );
      }
      if (invalidTransition)
      {
        await store.IncrementMetricAsync(
          RequireSymbolOrDefault(),
          "lifecycle_invalid_transition",
          cancellationToken
        );
      }
      if (recoveryTransition)
      {
        await store.IncrementMetricAsync(
          RequireSymbolOrDefault(),
          "lifecycle_recovery_transition",
          cancellationToken
        );
      }
      if (AutoTradeLifecycle.IsNonTerminalRecoveryState(appliedState))
      {
        await store.IncrementMetricAsync(
          RequireSymbolOrDefault(),
          appliedState == "broker_outcome_unknown"
            ? "lifecycle_broker_recovery"
            : "lifecycle_retryable_error",
          cancellationToken
        );
      }
      if (
        mutatesLifecycle
        && !string.IsNullOrWhiteSpace(appliedState)
        && !string.IsNullOrWhiteSpace(rangeId)
        && !string.IsNullOrWhiteSpace(direction)
      )
      {
        var railState = AutoTradeLifecycle.RangeSideStateFor(appliedState);
        if (railState is not null)
        {
          await store.UpdateRangeSideStateAsync(
            RequireSymbolOrDefault(),
            rangeId,
            direction,
            railState,
            candidateId,
            positionId,
            pendingOrderIds,
            cancellationToken
          );
        }
      }
    }
    catch (Exception exception) when (
      exception is not OperationCanceledException
    )
    {
      _log($"auto-trade lifecycle persistence failed: {exception.Message}");
      try
      {
        await store.IncrementMetricAsync(
          RequireSymbolOrDefault(),
          "lifecycle_error",
          cancellationToken
        );
      }
      catch (Exception metricException) when (
        metricException is not OperationCanceledException
      )
      {
        _log($"auto-trade lifecycle_error metric failed: {metricException.Message}");
      }
    }
  }

  private static LifecycleTransition? LifecycleTransitionForEvent(
    string type,
    long? remainingVolume
  ) => AutoTradeLifecycle.TransitionForEvent(type, remainingVolume);

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

  private async Task<T> WithGateAsync<T>(
    Func<Task<T>> action,
    CancellationToken cancellationToken
  )
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      return await action();
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

  private string RequireSymbolOrDefault() =>
    _symbol?.RedisSymbol ?? options.CanonicalSymbol;

  private static TradeDirection ParseDirection(string value) =>
    value.Equals("BUY", StringComparison.OrdinalIgnoreCase)
      ? TradeDirection.Buy
      : value.Equals("SELL", StringComparison.OrdinalIgnoreCase)
        ? TradeDirection.Sell
        : throw new InvalidOperationException($"Unsupported direction {value}");

  private static bool IsBoxRangeScalp(TradeCandidate candidate) =>
    candidate.Version is 3 or 5
    && candidate.Timeframe is not null
    && (
      candidate.Timeframe.Equals("M1", StringComparison.OrdinalIgnoreCase)
      || candidate.Timeframe.Equals("M5", StringComparison.OrdinalIgnoreCase)
    )
    && candidate.Setup == "Range Box Scalp"
    && candidate.Mode == "auto_box_scalp";

  private bool TryRangeBoxScaleOutPlan(
    TradeCandidate candidate,
    out IReadOnlyList<int>? targetsPips
  )
  {
    targetsPips = null;
    if (
      !IsBoxRangeScalp(candidate)
      || !options.RangeBoxScaleOutEnabled
      || options.RangeFlipEnabled
      || candidate.FullTakeProfitPips is not int fullTp
      || fullTp <= options.RangeBoxScaleOutThresholdPips
      || options.RangeBoxScaleOutTriggerPips <= 0
      || options.RangeBoxScaleOutTriggerPips >= fullTp
    )
    {
      return false;
    }
    targetsPips = [options.RangeBoxScaleOutTriggerPips, fullTp];
    return true;
  }

  private int[] RangeBoxScaleOutWeights()
  {
    var first = decimal.ToInt32(decimal.Round(
      options.RangeBoxScaleOutFraction * 100m,
      MidpointRounding.AwayFromZero
    ));
    first = Math.Clamp(first, 1, 99);
    return [first, 100 - first];
  }

  private static bool IsRangeBoxScaleOutState(AutoTradePositionState state) =>
    state.Setup is not null
    && state.Setup.Contains("Range Box Scalp", StringComparison.OrdinalIgnoreCase)
    && state.TargetsPips.Count >= 2
    && state.TrancheIndex == 1
    && string.IsNullOrWhiteSpace(state.ParentGroupId);

  private static bool IsTrendCandidate(TradeCandidate candidate) =>
    candidate.Version is 3 or 5
    && candidate.Timeframe.Equals("M1", StringComparison.OrdinalIgnoreCase)
    && candidate.Mode is "auto_trend_pullback" or "auto_trend_breakout"
      or "auto_box_breakout";

  private static bool IsStrategyMatchCandidate(TradeCandidate candidate) =>
    candidate.Version is 4 or 5
    && candidate.Timeframe is not null
    && (
      candidate.Timeframe.Equals("M1", StringComparison.OrdinalIgnoreCase)
      || candidate.Timeframe.Equals("M5", StringComparison.OrdinalIgnoreCase)
    )
    && !string.IsNullOrWhiteSpace(candidate.Setup)
    && candidate.Mode == "auto_strategy_match";

  private static bool IsManualAlgoCandidate(TradeCandidate candidate) =>
    candidate.Mode == "manual_algo";

  private decimal EffectiveInitialRiskPercent(TradeCandidate candidate)
  {
    var multiplier = candidate.RiskMultiplier ?? 1m;
    return options.RiskPercent * multiplier;
  }

  // On larger manual /algo positions the first booking should stay a
  // consistent ~0.05 lots rather than a proportional share that keeps
  // growing with account size - see VolumePlanner.FixFirstLegVolume.
  private const decimal ManualAlgoFirstLegThresholdLots = 0.13m;
  private const decimal ManualAlgoFirstLegLots = 0.05m;

  // 2026-08 R:R dig: manual /algo positions were a single entry, so a real
  // win typically only banked TP1 on 20% before the remaining 80% gave back
  // to breakeven on a pullback (58 closed XAU trades: median win 36 pips vs
  // median loss the full -60 stop). Splitting into 3 legs across the
  // owner's zone improves the realized average entry instead of touching
  // exits: shallow (near edge, most likely to actually fill) carries the
  // most size, deep (far edge, best price, least likely to fill) the least.
  private static readonly IReadOnlyList<decimal> ManualEntryLegRatios =
    [0.5m, 0.3m, 0.2m];

  private static bool UsesCandidateTargetPlan(TradeCandidate candidate) =>
    IsTrendCandidate(candidate) || IsStrategyMatchCandidate(candidate);

  private (int Pips, decimal? ExitPrice) BoxTarget(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal entryPrice
  )
  {
    if (!options.RangeFlipEnabled)
    {
      return (candidate.FullTakeProfitPips!.Value, null);
    }
    var exitPrice = BoxExitPrice(candidate, direction)!.Value;
    var distance = direction == TradeDirection.Buy
      ? exitPrice - entryPrice
      : entryPrice - exitPrice;
    var targetPips = decimal.ToInt32(decimal.Floor(distance / options.PipSize));
    if (targetPips <= 0)
    {
      throw new VolumePlanningException(
        "range flip exit is not on the profitable side of entry"
      );
    }
    return (targetPips, exitPrice);
  }

  private decimal? BoxExitPrice(
    TradeCandidate candidate,
    TradeDirection direction
  )
  {
    if (!options.RangeFlipEnabled)
    {
      return null;
    }
    var rawExit = direction == TradeDirection.Buy
      ? candidate.RangeHigh!.Value - options.FlipExitBufferPips * options.PipSize
      : candidate.RangeLow!.Value + options.FlipExitBufferPips * options.PipSize;
    return decimal.Round(
      rawExit,
      RequireSymbol().Digits,
      MidpointRounding.AwayFromZero
    );
  }

  private decimal BoxTargetPips(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal entryPrice
  ) => BoxTarget(candidate, direction, entryPrice).Pips;

  private static string FlipClaimId(string symbol, string rangeId) =>
    $"flip:{symbol.ToUpperInvariant()}:{rangeId}";

  private string? FlipClaimId(AutoTradePositionState state) =>
    string.IsNullOrWhiteSpace(state.RangeId)
      ? null
      : FlipClaimId(RequireSymbol().RedisSymbol, state.RangeId);

  private async Task<bool> OppositeFlipClosePendingAsync(
    TradeCandidate candidate,
    CancellationToken cancellationToken
  )
  {
    if (string.IsNullOrWhiteSpace(candidate.RangeId))
    {
      return false;
    }
    var claimId = FlipClaimId(candidate.Symbol, candidate.RangeId);
    var status = await store.GetCandidateStatusAsync(claimId, cancellationToken);
    if (string.IsNullOrWhiteSpace(status) || !status.StartsWith(
      "flip_pending:",
      StringComparison.Ordinal
    ))
    {
      return false;
    }
    var fields = status.Split(':');
    if (
      fields.Length < 3
      || !long.TryParse(fields[2], CultureInfo.InvariantCulture, out var expiresAt)
      || _clock().ToUnixTimeSeconds() >= expiresAt
    )
    {
      // Flip rendezvous records have no owning lease once they become
      // `flip_pending:`; expiry re-arms them through the restricted
      // administrative path rather than a fenced completion.
      await store.OverrideFlipClaimAsync(
        claimId,
        "rejected:flip_expired",
        cancellationToken
      );
      return false;
    }
    return !fields[1].Equals(candidate.Direction, StringComparison.OrdinalIgnoreCase);
  }

  private async Task<bool> BeginFlipCloseAsync(
    AutoTradePositionState state,
    CancellationToken cancellationToken
  )
  {
    var claimId = FlipClaimId(state);
    if (claimId is null)
    {
      return false;
    }
    var claim = await store.TryClaimCandidateAsync(
      claimId,
      "",
      CandidateLeaseDuration,
      cancellationToken,
      CandidateClaimPolicy.FlipClaim
    );
    if (claim.Lease is not CandidateExecutionLease lease)
    {
      return false;
    }
    var direction = state.Direction == TradeDirection.Buy ? "BUY" : "SELL";
    var expiresAt = _clock().ToUnixTimeSeconds()
      + options.FlipConfirmTimeoutSeconds;
    return await store.CompleteCandidateAsync(
      claimId,
      "",
      lease.Token,
      $"flip_pending:{direction}:{expiresAt}",
      cancellationToken
    );
  }

  private async Task ReleaseFlipCloseAsync(
    AutoTradePositionState state,
    CancellationToken cancellationToken
  )
  {
    var claimId = FlipClaimId(state);
    if (claimId is not null)
    {
      await store.OverrideFlipClaimAsync(
        claimId,
        "rejected:flip_released",
        cancellationToken
      );
    }
  }

  private static IReadOnlyList<int> EqualWeights(int count)
  {
    if (count <= 0)
    {
      throw new VolumePlanningException(
        "Cannot build target weights for zero targets"
      );
    }
    var baseWeight = 100 / count;
    var remainder = 100 - baseWeight * count;
    var weights = new int[count];
    for (var index = 0; index < count; index++)
    {
      weights[index] = baseWeight + (index == count - 1 ? remainder : 0);
    }
    return weights;
  }

  private decimal TargetPrice(
    AutoTradePositionState state,
    int targetPips,
    int targetIndex
  ) => (
    state.TargetPrices is { } targetPrices
    && targetIndex < targetPrices.Count
  )
    ? targetPrices[targetIndex]
    : state.RangeExitPrice ?? (
    state.Direction == TradeDirection.Buy
      ? state.EntryPrice + targetPips * options.PipSize
      : state.EntryPrice - targetPips * options.PipSize
  );

  private IReadOnlyList<decimal> BuildAutonomousTargetPrices(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal brokerFill,
    IReadOnlyList<int> targetsPips
  ) => BuildAutonomousTargetPrices(
    candidate.TargetModel,
    candidate.AbsoluteTargetPrice,
    direction,
    brokerFill,
    targetsPips
  );

  private IReadOnlyList<decimal> BuildAutonomousTargetPrices(
    string? targetModel,
    decimal? absoluteTargetPrice,
    TradeDirection direction,
    decimal brokerFill,
    IReadOnlyList<int> targetsPips
  )
  {
    var symbol = RequireSymbol();
    var model = (
      targetModel
      ?? (absoluteTargetPrice is null ? "fill_relative" : "hybrid")
    ).Trim().ToLowerInvariant();
    var cap = absoluteTargetPrice;
    var fallback = (options.PostFillTargetFallback ?? "fill_relative")
      .Trim()
      .ToLowerInvariant();
    return targetsPips.Select(pips =>
    {
      var fillRelative = direction == TradeDirection.Buy
        ? brokerFill + pips * options.PipSize
        : brokerFill - pips * options.PipSize;
      var target = model switch
      {
        "absolute" when cap is decimal absolute => absolute,
        "hybrid" when cap is decimal absolute => direction == TradeDirection.Buy
          ? Math.Min(fillRelative, absolute)
          : Math.Max(fillRelative, absolute),
        _ => fillRelative,
      };
      var profitable = direction == TradeDirection.Buy
        ? target > brokerFill
        : target < brokerFill;
      if (!profitable)
      {
        target = fallback == "management_hold"
          ? fillRelative
          : fillRelative;
      }
      return decimal.Round(
        target,
        symbol.Digits,
        MidpointRounding.AwayFromZero
      );
    }).ToArray();
  }

  private static int TargetOrdinal(AutoTradePositionState state, int index) =>
    state.TargetOrdinals is { } ordinals && index < ordinals.Count
      ? ordinals[index]
      : index + 1;

  private static string BuildComment(
    string candidateId,
    string groupId,
    int trancheIndex,
    long volume,
    IReadOnlyList<long> slices,
    IReadOnlyList<int> targets,
    IReadOnlyList<int> ordinals,
    long barTs
  )
  {
    var comment = string.Join(
      '|',
      "av3",
      CandidateToken(candidateId),
      GroupToken(groupId),
      trancheIndex.ToString(CultureInfo.InvariantCulture),
      volume.ToString(CultureInfo.InvariantCulture),
      string.Join(',', slices),
      string.Join(',', targets),
      string.Join(',', ordinals),
      barTs.ToString(CultureInfo.InvariantCulture)
    );
    if (comment.Length > 100)
    {
      throw new VolumePlanningException(
        $"tranche comment is {comment.Length} chars; cTrader maximum is 100"
      );
    }
    return comment;
  }

  private static string BuildZoneComment(
    string candidateId,
    string groupId,
    ZoneFillLegPlan leg,
    long barTs
  )
  {
    var comment = string.Join(
      '|',
      "avz",
      CandidateToken(candidateId),
      GroupToken(groupId),
      leg.Leg.ToString(CultureInfo.InvariantCulture),
      leg.Volume.ToString(CultureInfo.InvariantCulture),
      string.Join(',', leg.TargetPlan.Slices),
      string.Join(',', leg.TargetPlan.TargetsPips),
      string.Join(',', leg.TargetPlan.TargetOrdinals),
      barTs.ToString(CultureInfo.InvariantCulture)
    );
    if (comment.Length > 100)
    {
      throw new VolumePlanningException(
        $"zone-fill comment is {comment.Length} chars; cTrader maximum is 100"
      );
    }
    return comment;
  }

  // avm|{candidateToken}|{groupId}|{volume}|{slices}|{targets}|{ordinals}|
  // {barTs}|{expiresAt} - single-leg manual-algo equivalent of av3/avz.
  // expiresAt is an absolute unix timestamp (0 = never expires), unlike
  // zone-fill's bars*60s TTL formula.
  private static string BuildManualComment(
    string candidateId,
    string groupId,
    long volume,
    IReadOnlyList<long> slices,
    IReadOnlyList<int> targets,
    IReadOnlyList<int> ordinals,
    long barTs,
    long expiresAt,
    int legIndex,
    int legCount
  )
  {
    var comment = string.Join(
      '|',
      "avm",
      CandidateToken(candidateId),
      GroupToken(groupId),
      volume.ToString(CultureInfo.InvariantCulture),
      string.Join(',', slices),
      string.Join(',', targets),
      string.Join(',', ordinals),
      barTs.ToString(CultureInfo.InvariantCulture),
      expiresAt.ToString(CultureInfo.InvariantCulture),
      legIndex.ToString(CultureInfo.InvariantCulture),
      legCount.ToString(CultureInfo.InvariantCulture)
    );
    if (comment.Length > 100)
    {
      throw new VolumePlanningException(
        $"manual algo comment is {comment.Length} chars; cTrader maximum is 100"
      );
    }
    return comment;
  }

  // Three entry legs (2026-08 R:R redesign) share one GroupId/TargetPrices
  // but are otherwise independent broker positions - each trails its own
  // stop from its own real fill price (see StopTrailPlanner), so no
  // group-level entry-price blending is needed. legIndex/legCount are
  // parsed but only used for observability (TrancheIndex/GroupTrancheCount);
  // a pre-redesign 9-part "avm" comment (no leg fields) still parses as a
  // single leg 1-of-1 for backward compatibility with orders already live
  // when this shipped.
  private static AutoTradePositionState? ParseManualComment(TradingPosition position)
  {
    var parts = position.Comment.Split('|');
    if ((parts.Length != 9 && parts.Length != 11) || parts[0] != "avm")
    {
      return null;
    }
    try
    {
      var initial = long.Parse(parts[3], CultureInfo.InvariantCulture);
      var slices = parts[4].Split(',')
        .Select(value => long.Parse(value, CultureInfo.InvariantCulture))
        .ToArray();
      var targets = parts[5].Split(',')
        .Select(value => int.Parse(value, CultureInfo.InvariantCulture))
        .ToArray();
      var ordinals = parts[6].Split(',')
        .Select(value => int.Parse(value, CultureInfo.InvariantCulture))
        .ToArray();
      var barTs = long.Parse(parts[7], CultureInfo.InvariantCulture);
      var legIndex = parts.Length == 11
        ? int.Parse(parts[9], CultureInfo.InvariantCulture)
        : 1;
      var legCount = parts.Length == 11
        ? int.Parse(parts[10], CultureInfo.InvariantCulture)
        : 1;
      if (
        slices.Length == 0
        || slices.Length != targets.Length
        || ordinals.Length != targets.Length
        || slices.Any(value => value <= 0)
        || targets.Any(value => value <= 0)
        || ordinals.Any(value => value <= 0)
        || !ordinals.SequenceEqual(ordinals.Order())
        || legIndex < 1
        || legCount < 1
        || legIndex > legCount
      )
      {
        return null;
      }
      return new AutoTradePositionState(
        parts[1],
        position.PositionId,
        position.SymbolId,
        position.Direction,
        position.EntryPrice,
        initial,
        position.Volume,
        slices,
        targets,
        NextTargetIndex: 0,
        OpenedAt: 0,
        position.StopLoss,
        ordinals,
        parts[2],
        TrancheIndex: legIndex,
        GroupOpenedAt: barTs,
        LastTrancheBarTs: barTs,
        GroupTrancheCount: legCount,
        HadAdds: false,
        InitialStopLoss: position.StopLoss,
        ZoneLeg: 0,
        GroupInitialVolume: initial,
        InitialTrancheVolume: initial,
        Setup: "Manual Algo",
        Stream: "algo_manual",
        StrategyFamily: "manual"
      );
    }
    catch (FormatException)
    {
      return null;
    }
  }

  private static (long ExpiresAt, string GroupId, string CandidateToken)? ParseManualExpiry(
    string comment
  )
  {
    var parts = comment.Split('|');
    if (
      (parts.Length != 9 && parts.Length != 11)
      || parts[0] != "avm"
      || !long.TryParse(
        parts[8],
        NumberStyles.Integer,
        CultureInfo.InvariantCulture,
        out var expiresAt
      )
    )
    {
      return null;
    }
    return (expiresAt, parts[2], parts[1]);
  }

  private static AutoTradePositionState? ParseComment(TradingPosition position)
  {
    var parts = position.Comment.Split('|');
    var version3 = parts.Length > 0 && parts[0] == "av3";
    var zoneVersion = parts.Length > 0 && parts[0] == "avz";
    if (
      !(
        (parts[0] == "av1" && parts.Length == 5)
        || (parts[0] == "av2" && parts.Length == 6)
        || ((version3 || zoneVersion) && parts.Length == 9)
      )
    )
    {
      return null;
    }
    try
    {
      var currentVersion = version3 || zoneVersion;
      var initialIndex = currentVersion ? 4 : 2;
      var slicesIndex = currentVersion ? 5 : 3;
      var targetsIndex = currentVersion ? 6 : 4;
      var ordinalsIndex = currentVersion ? 7 : 5;
      var initial = long.Parse(parts[initialIndex], CultureInfo.InvariantCulture);
      var slices = parts[slicesIndex].Split(',')
        .Select(value => long.Parse(value, CultureInfo.InvariantCulture))
        .ToArray();
      var targets = parts[targetsIndex].Split(',')
        .Select(value => int.Parse(value, CultureInfo.InvariantCulture))
        .ToArray();
      var ordinals = parts[0] != "av1"
        ? parts[ordinalsIndex].Split(',')
          .Select(value => int.Parse(value, CultureInfo.InvariantCulture))
          .ToArray()
        : Enumerable.Range(1, targets.Length).ToArray();
      var groupId = currentVersion ? parts[2] : GroupToken(parts[1]);
      var trancheIndex = version3
        ? int.Parse(parts[3], CultureInfo.InvariantCulture)
        : 1;
      var zoneLeg = zoneVersion
        ? int.Parse(parts[3], CultureInfo.InvariantCulture)
        : 0;
      var barTs = currentVersion
        ? long.Parse(parts[8], CultureInfo.InvariantCulture)
        : 0;
      if (
        slices.Length == 0
        || slices.Length != targets.Length
        || ordinals.Length != targets.Length
        || slices.Any(value => value <= 0)
        || targets.Any(value => value <= 0)
        || ordinals.Any(value => value <= 0)
        || !ordinals.SequenceEqual(ordinals.Order())
      )
      {
        return null;
      }
      var closed = Math.Max(0, initial - position.Volume);
      var cumulative = 0L;
      var next = 0;
      foreach (var slice in slices)
      {
        cumulative += slice;
        if (closed < cumulative)
        {
          break;
        }
        next++;
      }
      return new AutoTradePositionState(
        parts[1],
        position.PositionId,
        position.SymbolId,
        position.Direction,
        position.EntryPrice,
        initial,
        position.Volume,
        slices,
        targets,
        Math.Min(next, targets.Length),
        0,
        position.StopLoss,
        ordinals,
        groupId,
        trancheIndex,
        GroupOpenedAt: trancheIndex == 1 ? barTs : 0,
        LastTrancheBarTs: barTs,
        GroupTrancheCount: trancheIndex,
        HadAdds: trancheIndex > 1,
        InitialStopLoss: position.StopLoss,
        ZoneLeg: zoneLeg,
        GroupInitialVolume: initial,
        InitialTrancheVolume: trancheIndex == 1 ? initial : 0
      );
    }
    catch (FormatException)
    {
      return null;
    }
  }

  private static (int Leg, long BarTs, string GroupId)? ParseZoneComment(
    string comment
  )
  {
    var parts = comment.Split('|');
    if (
      parts.Length != 9
      || parts[0] != "avz"
      || !int.TryParse(parts[3], NumberStyles.Integer, CultureInfo.InvariantCulture, out var leg)
      || !long.TryParse(parts[8], NumberStyles.Integer, CultureInfo.InvariantCulture, out var barTs)
      || leg is not (1 or 2)
      || barTs <= 0
    )
    {
      return null;
    }
    return (leg, barTs, parts[2]);
  }

  private static string ClientOrderId(string candidateId, int legIndex = 1) =>
    legIndex <= 1
      ? $"av-{candidateId[..Math.Min(40, candidateId.Length)]}"
      : $"av-{candidateId[..Math.Min(38, candidateId.Length)]}-L{legIndex}";

  private static string CandidateToken(string candidateId) =>
    candidateId[..Math.Min(10, candidateId.Length)];

  private static string GroupToken(string groupId) =>
    groupId[..Math.Min(10, groupId.Length)];

  private void RememberRouteIdentity(TradeCandidate candidate)
  {
    if (string.IsNullOrWhiteSpace(candidate.CandidateId))
    {
      return;
    }
    _routeIdentityByCandidate[candidate.CandidateId] = new StructuralRouteIdentity(
      candidate.StructuralSource,
      candidate.ZoneId,
      candidate.StructuralZoneId,
      candidate.ReactionId,
      candidate.ThesisId
    );
  }

  private static string Short(string candidateId) =>
    candidateId[..Math.Min(12, candidateId.Length)];

  private static void Log(string message) =>
    Console.Error.WriteLine($"ctrader-feed {message}");

  private sealed class CandidateRejectedException(string message)
    : Exception(message);
}

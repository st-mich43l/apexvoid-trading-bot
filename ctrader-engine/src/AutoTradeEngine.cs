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
  private readonly HashSet<string> _reportedErrors = [];
  private readonly HashSet<string> _reportedSessionErrors = [];
  private readonly HashSet<string> _reportedWarnings = [];
  private readonly object _reportLock = new();
  private readonly Func<DateTimeOffset> _clock = clock ?? (() => DateTimeOffset.UtcNow);
  private readonly Action<string> _log = log ?? Log;
  private ICTraderTradeClient? _client;
  private SymbolInfo? _symbol;
  private SpotPrice? _lastSpot;
  private IReadOnlyList<TradingPosition> _allSymbolPositions = [];
  private IReadOnlyList<TradingPendingOrder> _allSymbolPendingOrders = [];
  private TradingAccountSnapshot? _account;
  private bool _accountSupportsHedging;
  private volatile bool _ready;
  private volatile bool _disabled;

  public bool Enabled => options.Enabled && !_disabled;

  public void LogUnitConfiguration(
    SymbolInfo symbol,
    Action<string> info,
    Action<string> warning
  )
  {
    var diagnostic = VolumePlanner.PipUnitDiagnostic(symbol, options);
    if (diagnostic.Differs)
    {
      warning(diagnostic.Message);
      return;
    }
    info(diagnostic.Message);
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
      while (Enabled && !cancellationToken.IsCancellationRequested)
      {
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
    await PublishAsync(
      exception is AutoTradeConfigurationException && options.Profile == "demo_eval"
        ? "config_fatal"
        : "error",
      exception.Message,
      cancellationToken
    );
    var fatal = exception is AutoTradeConfigurationException
      ? new[] { "service_initialization" }
      : new[] { "broker_or_redis_connection" };
    await PublishReadinessAsync(
      false,
      "fatal",
      new AutoTradeConfigHealthResult("fatal", fatal, []),
      cancellationToken
    );
  }

  public Task PublishOperationalEventAsync(
    string kind,
    string message,
    CancellationToken cancellationToken
  ) => PublishAsync(kind, message, cancellationToken);

  public async Task ObserveSpotAsync(
    SpotPrice spot,
    CancellationToken cancellationToken
  )
  {
    _lastSpot = spot;
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
      strategyFamily: candidate.StrategyFamily
    );
    if (!await store.TryClaimCandidateAsync(candidate.CandidateId, cancellationToken))
    {
      var status = await store.GetCandidateStatusAsync(
        candidate.CandidateId,
        cancellationToken
      );
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "duplicate_suppressed",
        cancellationToken
      );
      return !string.Equals(status, "processing", StringComparison.Ordinal);
    }
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
    catch (AutoTradeConfigurationException)
    {
      await store.ReleaseCandidateAsync(candidate.CandidateId, cancellationToken);
      throw;
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      await store.ReleaseCandidateAsync(candidate.CandidateId, cancellationToken);
      await store.IncrementMetricAsync(
        candidate.Symbol,
        "lifecycle_error",
        cancellationToken
      );
      if (_reportedErrors.Add(candidate.CandidateId))
      {
        await PublishAsync(
          "error",
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
    await PublishAsync(
      "manual_cancelled",
      $"manual algo limit {target.OrderId} cancelled by owner",
      cancellationToken,
      candidateId: command.IntentId
    );
  }

  // /trade_close on a filled manual algo signal: close the real position
  // (full or partial by Frac) and publish the REAL execution price so
  // Python can compute the pip result itself - no pip math belongs here.
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
    var remainingAfter = execution.RemainingVolume
      ?? Math.Max(0, remainingVolume - execution.ExecutedVolume);
    decimal? terminalGroupPips = null;
    if (state is not null && remainingAfter <= 0)
    {
      var initialVolume = state.GroupInitialVolume > 0
        ? state.GroupInitialVolume
        : state.InitialVolume;
      var pipVolume = state.GroupRealizedPipVolume
        + SignedPips(state, execution.ExecutionPrice) * execution.ExecutedVolume;
      terminalGroupPips = WeightedPips(pipVolume, initialVolume);
    }
    await PublishAsync(
      "manual_closed",
      $"manual algo position {positionId} closed by owner",
      cancellationToken,
      candidateId: command.IntentId,
      positionId: positionId,
      volume: execution.ExecutedVolume,
      price: execution.ExecutionPrice,
      groupId: state is null ? null : GroupId(state),
      setup: state?.Setup,
      regime: state?.Regime,
      confluence: state?.Confluence,
      groupRealizedPips: terminalGroupPips,
      stopPips: state is null ? null : InitialStopPips(state),
      stream: state is null ? null : ExecutionStream(state),
      direction: state is null ? null : DirectionLabel(state.Direction),
      remainingVolume: remainingAfter
    );
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
      strategyFamily: candidate.StrategyFamily
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
    if (manualAlgoCandidate && !options.ManualAlgoEnabled)
    {
      return await RejectAsync(
        candidate,
        "manual_algo_disabled",
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
        await store.CompleteCandidateAsync(
          candidate.CandidateId,
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
      await store.CompleteCandidateAsync(
        candidate.CandidateId,
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
    StructureStopPlan stopPlan;
    try
    {
      stopPlan = StructureStop(candidate, direction, expectedEntry, symbol);
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
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    // An explicitly linked trend candidate may use a pullback stop rather
    // than this initial structure stop, so its opposing-zone guard belongs
    // to ProcessAddAsync after the add mode is selected.
    var deferStopGuardToAddPath = !string.IsNullOrWhiteSpace(
      candidate.ParentGroupId
    );
    if (!deferStopGuardToAddPath)
    {
      var (guardedStopPlan, stopRejectReason, stopNotice) = ApplyOpposingZoneGuard(
        candidate,
        direction,
        expectedEntry,
        stopPlan,
        symbol
      );
      if (stopRejectReason is not null)
      {
        await store.IncrementGateRejectAsync(
          candidate.Symbol,
          "stop_in_opposing_zone",
          cancellationToken
        );
        return await RejectAsync(candidate, stopRejectReason, cancellationToken);
      }
      stopPlan = guardedStopPlan;
      if (stopNotice is not null)
      {
        await PublishAsync(
          "warning",
          stopNotice,
          cancellationToken,
          candidate.CandidateId,
          setup: candidate.Setup,
          regime: candidate.Regime,
          confluence: candidate.Confluence,
          stopPips: stopPlan.StopPips
        );
      }
    }
    decimal? boxTargetPips = null;
    if (boxRangeScalp)
    {
      try
      {
        boxTargetPips = BoxTargetPips(candidate, direction, expectedEntry);
      }
      catch (VolumePlanningException exception)
      {
        return await RejectAsync(candidate, exception.Message, cancellationToken);
      }
    }
    if (
      boxTargetPips is decimal rewardPips
      && rewardPips / stopPlan.StopPips < options.BoxMinRiskReward
    )
    {
      return await RejectAsync(
        candidate,
        $"range-box reward/risk below {options.BoxMinRiskReward:0.##}",
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
        await store.CompleteCandidateAsync(
          candidate.CandidateId,
          "already_processed:duplicate_reaction_active",
          cancellationToken
        );
        _log(
          $"auto-trade candidate {Short(candidate.CandidateId)} "
          + "already_processed:duplicate_reaction_active"
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

  private async Task<bool> ProcessInitialAsync(
    TradeCandidate candidate,
    TradingAccountSnapshot account,
    TradeDirection direction,
    decimal expectedEntry,
    StructureStopPlan stopPlan,
    DateOnly date,
    CancellationToken cancellationToken
  )
  {
    if (
      !IsBoxRangeScalp(candidate)
      && !IsStrategyMatchCandidate(candidate)
      && options.ZoneFillEnabled
      && candidate.Atr is decimal atr
      && ZoneFillPlanner.Qualifies(
        candidate.EntryZone,
        atr,
        options.ZoneFillMinAtr
      )
    )
    {
      return await ProcessZoneFillAsync(
        candidate,
        account,
        direction,
        expectedEntry,
        stopPlan,
        date,
        cancellationToken
      );
    }
    return await ProcessSingleInitialAsync(
      candidate,
      account,
      direction,
      expectedEntry,
      stopPlan,
      date,
      routingReason: null,
      cancellationToken: cancellationToken
    );
  }

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
    IReadOnlyList<int> targetPips = UsesCandidateTargetPlan(candidate)
      ? candidate.TargetsPips!
      : IsBoxRangeScalp(candidate)
        ? [boxTarget!.Value.Pips]
        : options.TargetsPips;
    IReadOnlyList<int> targetWeights = UsesCandidateTargetPlan(candidate)
      ? EqualWeights(candidate.TargetsPips!.Count)
      : IsBoxRangeScalp(candidate)
        ? [100]
        : options.TargetWeights;
    try
    {
      sizing = VolumePlanner.SizeInitial(
        account.Balance,
        options.RiskPercent,
        options.SizingMode,
        stopPlan.StopPips,
        options.PipValuePerLot,
        RequireSymbol(),
        targetPips,
        targetWeights
      );
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
        ? $" · full TP {targetPips[0]}p"
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
          ? $"full TP {targetPips[0]}p · range "
            + $"{candidate.RangeLow:N2}-{candidate.RangeHigh:N2} · "
          : "")
        + sizing.BindingTerm
        + routingSuffix,
      groupWorstCase: -sizing.Lots * stopPlan.StopPips
        * options.PipValuePerLot,
      riskBudget: sizing.Budget,
      cancellationToken
    );
  }

  private async Task<bool> ProcessZoneFillAsync(
    TradeCandidate candidate,
    TradingAccountSnapshot account,
    TradeDirection direction,
    decimal expectedEntry,
    StructureStopPlan singleEntryStopPlan,
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
    // Prefer the portion of the zone that remains on the valid limit-order
    // side. When price is already inside the zone the classic distal edge
    // (BUY→zone.High / SELL→zone.Low) can sit on the wrong side of price and
    // must not hard-reject a fresh candidate.
    var proximal = SelectValidSideProximal(
      candidate.EntryZone,
      direction,
      expectedEntry,
      geometry,
      options.InsideZoneMarketEntryEnabled
    );
    if (proximal is null)
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
      return await ProcessSingleInitialAsync(
        candidate,
        account,
        direction,
        expectedEntry,
        singleEntryStopPlan,
        date,
        fallbackReason,
        cancellationToken
      );
    }
    StructureStopPlan zoneStopPlan;
    InitialSizingResult sizing;
    try
    {
      zoneStopPlan = StructureStop(candidate, direction, proximal.Value, symbol);
      sizing = VolumePlanner.SizeInitial(
        account.Balance,
        options.RiskPercent,
        options.SizingMode,
        zoneStopPlan.StopPips,
        options.PipValuePerLot,
        symbol,
        options.TargetsPips,
        options.TargetWeights
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
      return await ProcessSingleInitialAsync(
        candidate,
        account,
        direction,
        expectedEntry,
        singleEntryStopPlan,
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
      return await ProcessSingleInitialAsync(
        candidate,
        account,
        direction,
        expectedEntry,
        singleEntryStopPlan,
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
      var stopLoss = direction == TradeDirection.Buy
        ? proximal.Value - zoneStopPlan.Distance
        : proximal.Value + zoneStopPlan.Distance;
      stopLoss = decimal.Round(
        stopLoss,
        symbol.Digits,
        MidpointRounding.AwayFromZero
      );
      plan = ZoneFillPlanner.Build(
        direction,
        fillZone,
        stopLoss,
        sizing.Volume,
        symbol,
        options.TargetsPips,
        options.TargetWeights
      );
    }
    catch (VolumePlanningException exception)
    {
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    var groupId = CandidateGroupId(candidate);
    var barTs = candidate.BarTs ?? candidate.CreatedAt;
    await SaveGroupPlanAsync(candidate, groupId, cancellationToken);
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
      strategyFamily: candidate.StrategyFamily
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
      strategyFamily: candidate.StrategyFamily
    );
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "order_submitted",
      cancellationToken
    );
    try
    {
      foreach (var leg in plan.Legs)
      {
        var distance = Math.Abs(leg.LimitPrice - plan.StopLoss);
        var comment = BuildZoneComment(
          candidate.CandidateId,
          groupId,
          leg,
          barTs
        );
        var orderId = await RequireClient().PlaceLimitOrderAsync(
          new LimitOrderRequest(
            symbol.SymbolId,
            direction,
            leg.Volume,
            leg.LimitPrice,
            decimal.ToInt64(distance * 100_000m),
            options.Label,
            comment,
            $"{ClientOrderId(candidate.CandidateId)}-z{leg.Leg}"
          ),
          cancellationToken
        );
        placed.Add(orderId);
      }
    }
    catch
    {
      await RollbackZoneFillAsync(
        candidate.CandidateId,
        placed,
        cancellationToken
      );
      throw;
    }
    await store.CompleteCandidateAsync(
      candidate.CandidateId,
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

  private async Task RollbackZoneFillAsync(
    string candidateId,
    IReadOnlyList<long> placedOrderIds,
    CancellationToken cancellationToken
  )
  {
    var client = RequireClient();
    foreach (var orderId in placedOrderIds)
    {
      try
      {
        await client.CancelPendingOrderAsync(orderId, cancellationToken);
      }
      catch (Exception exception) when (exception is not OperationCanceledException)
      {
        _log($"auto-trade zone-fill rollback cancel failed order={orderId}: "
          + exception.Message);
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
    var insideZone = expectedEntry >= zone.Low && expectedEntry <= zone.High;
    var limitPrice = insideZone
      ? expectedEntry
      : (direction == TradeDirection.Buy ? zone.High : zone.Low);
    limitPrice = decimal.Round(
      limitPrice,
      symbol.Digits,
      MidpointRounding.AwayFromZero
    );
    var targetPrices = candidate.ManualTakeProfits!;
    var priceValidation = ValidateManualPrices(
      candidate,
      direction,
      limitPrice,
      symbol
    );
    if (priceValidation is not null)
    {
      return await RejectAsync(candidate, priceValidation, cancellationToken);
    }
    StructureStopPlan manualStopPlan;
    try
    {
      manualStopPlan = ManualStop(candidate, direction, limitPrice, symbol);
    }
    catch (VolumePlanningException exception)
    {
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    var targetsPips = targetPrices
      .Select(price => decimal.ToInt32(decimal.Round(
        decimal.Abs(price - limitPrice) / options.PipSize,
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
    if (sizing.Lots > ManualAlgoFirstLegThresholdLots)
    {
      var fixedFirstLeg = VolumePlanner.VolumeForLots(ManualAlgoFirstLegLots, symbol);
      sizing = sizing with
      {
        TargetPlan = VolumePlanner.FixFirstLegVolume(
          sizing.TargetPlan,
          sizing.Volume,
          fixedFirstLeg,
          symbol
        ),
      };
    }
    var groupId = CandidateGroupId(candidate);
    var barTs = candidate.BarTs ?? candidate.CreatedAt;
    var expiresAt = candidate.ManualExpiresAt ?? 0;
    if (options.DryRun)
    {
      return await CompleteDryRunAsync(
        candidate,
        $"manual algo {direction} {sizing.Lots:N2} lots @ {limitPrice:N2} · "
          + $"SL {manualStopPlan.StopLoss:N2} · {sizing.BindingTerm}",
        sizing.Volume,
        limitPrice,
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
    await SaveGroupPlanAsync(candidate, groupId, cancellationToken);
    var comment = BuildManualComment(
      candidate.CandidateId,
      groupId,
      sizing.Volume,
      sizing.TargetPlan.Slices,
      sizing.TargetPlan.TargetsPips,
      sizing.TargetPlan.TargetOrdinals,
      barTs,
      expiresAt
    );
    var orderId = await RequireClient().PlaceLimitOrderAsync(
      new LimitOrderRequest(
        symbol.SymbolId,
        direction,
        sizing.Volume,
        limitPrice,
        decimal.ToInt64(manualStopPlan.Distance * 100_000m),
        options.Label,
        comment,
        ClientOrderId(candidate.CandidateId)
      ),
      cancellationToken
    );
    await store.CompleteCandidateAsync(
      candidate.CandidateId,
      $"ordered:{orderId}",
      cancellationToken
    );
    await store.IncrementDailyTradeCountAsync(date, cancellationToken);
    await PublishAsync(
      "manual_limit_placed",
      $"manual algo {direction} limit {sizing.Lots:N2} lots @ {limitPrice:N2} · "
        + $"SL {manualStopPlan.StopLoss:N2} · {sizing.BindingTerm}",
      cancellationToken,
      candidate.CandidateId,
      volume: sizing.Volume,
      price: limitPrice,
      groupId: groupId,
      trancheIndex: 1,
      groupWorstCase: -sizing.Lots * manualStopPlan.StopPips
        * options.PipValuePerLot,
      riskBudget: sizing.Budget,
      hadAdds: false,
      setup: candidate.Setup,
      stopPips: manualStopPlan.StopPips,
      targetsPips: sizing.TargetPlan.TargetsPips,
      stream: "algo_manual",
      direction: candidate.Direction,
      orderId: orderId,
      stopLoss: manualStopPlan.StopLoss,
      targetPrices: targetPrices,
      entryLow: candidate.EntryZone.Low,
      entryHigh: candidate.EntryZone.High
    );
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
    var (guardedStopPlan, stopRejectReason, stopNotice) = ApplyOpposingZoneGuard(
      candidate, direction, expectedEntry, rawStopPlan, symbol
    );
    if (stopRejectReason is not null)
    {
      await store.IncrementAddRejectAsync(
        candidate.Symbol, mode, "stop_in_opposing_zone", cancellationToken
      );
      return await RejectAsync(candidate, stopRejectReason, cancellationToken);
    }
    stopPlan = guardedStopPlan;
    if (stopNotice is not null)
    {
      await PublishAsync(
        "warning", stopNotice, cancellationToken, candidate.CandidateId,
        setup: candidate.Setup, regime: candidate.Regime,
        confluence: candidate.Confluence, stopPips: stopPlan.StopPips
      );
    }
    var groupBooked = GroupBookedPnl(group);
    // AUTO_TRADE_ADD_SIZE_RATIO only constrains pullback tranches - momentum
    // keeps ScaleInPlanner's existing exposure/risk/add-cap sizing exactly
    // as before (byte-identical, proven by
    // MomentumContinuationOpensIndependentSecondTranche's fixed 600-volume
    // add-cap-bound expectation).
    var initialTrancheLots = mode == "add_pullback"
      ? InitialTrancheVolume(group) / (decimal)symbol.LotSize
      : (decimal?)null;
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
      strategyFamily: candidate.StrategyFamily
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
      strategyFamily: candidate.StrategyFamily
    );
    await store.IncrementMetricAsync(
      candidate.Symbol,
      "order_submitted",
      cancellationToken
    );
    var execution = await client.PlaceMarketOrderAsync(
      new MarketOrderRequest(
        RequireSymbol().SymbolId,
        direction,
        volume,
        decimal.ToInt64(stopPlan.Distance * 100_000m),
        options.Label,
        comment,
        ClientOrderId(candidate.CandidateId)
      ),
      cancellationToken
    );
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
      strategyFamily: candidate.StrategyFamily
    );
    var fill = execution.ExecutionPrice > 0
      ? execution.ExecutionPrice
      : expectedEntry;
    var stopLoss = direction == TradeDirection.Buy
      ? fill - stopPlan.Distance
      : fill + stopPlan.Distance;
    stopLoss = decimal.Round(stopLoss, symbol.Digits, MidpointRounding.AwayFromZero);
    await client.AmendPositionStopLossAsync(
      execution.PositionId,
      stopLoss,
      cancellationToken
    );
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
      RangeExitPrice: IsBoxRangeScalp(candidate)
        ? BoxExitPrice(candidate, direction)
        : null,
      Stream: "algo_auto",
      MatchId: candidate.MatchId,
      StrategyFamily: string.IsNullOrWhiteSpace(candidate.StrategyFamily)
        ? StrategyFamilyFromSetup(candidate.Setup)
        : candidate.StrategyFamily,
      ZoneId: candidate.ZoneId,
      TriggerId: candidate.TriggerId,
      ParentGroupId: candidate.ParentGroupId,
      StructuralSource: candidate.StructuralSource,
      ReactionId: candidate.ReactionId,
      ThesisId: candidate.ThesisId
    );
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
    await store.CompleteCandidateAsync(
      candidate.CandidateId,
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
      strategyFamily: state.StrategyFamily
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
      strategyFamily: state.StrategyFamily
    );
    return true;
  }

  private StructureStopPlan StructureStop(
    TradeCandidate candidate,
    TradeDirection direction,
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
      symbol
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
    return new StructureStopPlan(stopLoss, distance, stopPips, rawStop, false);
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

  // A stop must sit beyond the nearest opposing HTF supply/demand zone, never
  // inside it - the 22 Jul 2026 incident's SL sat inside the very supply zone
  // the SELL was meant to fade, killing the position before its own thesis
  // could be tested. `candidate.OpposingZoneLow/High` are attached by
  // worker.py from the same HTF veto lookup as the A3 supply/demand check.
  private (
    StructureStopPlan Plan,
    string? RejectReason,
    string? Notice
  ) ApplyOpposingZoneGuard(
    TradeCandidate candidate,
    TradeDirection direction,
    decimal entryPrice,
    StructureStopPlan stopPlan,
    SymbolInfo symbol
  )
  {
    if (
      candidate.OpposingZoneLow is not decimal zoneLow
      || candidate.OpposingZoneHigh is not decimal zoneHigh
    )
    {
      return (stopPlan, null, null);
    }
    if (stopPlan.StopLoss < zoneLow || stopPlan.StopLoss > zoneHigh)
    {
      return (stopPlan, null, null);
    }
    var zoneDescription =
      $"stop {stopPlan.StopLoss:0.####} inside opposing zone "
      + $"{zoneLow:0.####}-{zoneHigh:0.####}";
    if (!options.StopPushBeyondZone)
    {
      _log($"auto-trade stop rejected: {zoneDescription}");
      return (stopPlan, zoneDescription, null);
    }
    var atr = candidate.Atr ?? 0m;
    var buffer = options.AddStopBufferAtr * atr;
    var pushedStop = direction == TradeDirection.Buy
      ? zoneLow - buffer
      : zoneHigh + buffer;
    pushedStop = decimal.Round(
      pushedStop,
      symbol.Digits,
      MidpointRounding.AwayFromZero
    );
    var pushedDistance = Math.Abs(entryPrice - pushedStop);
    var pushedPips = pushedDistance / options.PipSize;
    var (_, maximumStopPips) = StopPipsBounds(candidate);
    if (pushedPips > maximumStopPips)
    {
      var rejectReason =
        $"{zoneDescription} - pushing beyond it would need {pushedPips:0.#}p, "
        + $"over the {maximumStopPips}p max";
      _log($"auto-trade stop rejected: {rejectReason}");
      return (stopPlan, rejectReason, null);
    }
    _log(
      $"auto-trade stop pushed beyond opposing zone: {zoneDescription} -> "
      + $"{pushedStop:0.####} ({pushedPips:0.#}p)"
    );
    var pushedPlan = new StructureStopPlan(
      pushedStop,
      pushedDistance,
      pushedPips,
      stopPlan.RawStopLoss,
      true
    );
    return (pushedPlan, null, null);
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
        && AtLeastBreakeven(state.Direction, state.EntryPrice, initialStop)
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

  private static bool AtLeastBreakeven(
    TradeDirection direction,
    decimal entry,
    decimal stop
  ) => direction == TradeDirection.Buy ? stop >= entry : stop <= entry;

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
    await store.CompleteCandidateAsync(
      candidate.CandidateId,
      "dry_run",
      cancellationToken
    );
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
    var quote = _lastSpot
      ?? throw new CandidateRejectedException("live cTrader quote unavailable");
    var age = _clock().ToUnixTimeSeconds() - quote.Timestamp;
    if (age < 0 || age > Math.Max(1, options.SpotMaxAgeSeconds))
    {
      throw new CandidateRejectedException("live cTrader quote is stale");
    }
    var spread = quote.Ask - quote.Bid;
    var spreadPips = spread / options.PipSize;
    if (spreadPips < 0 || spreadPips > options.MaxSpreadPips)
    {
      throw new CandidateRejectedException(
        $"spread rejected: bid={quote.Bid:0.00} ask={quote.Ask:0.00} "
          + $"raw={spread:0.00} pip={options.PipSize} -> "
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
    var distancePips = distance / options.PipSize;
    if (distancePips > options.MaxEntryDistancePips)
    {
      throw new CandidateRejectedException(
        $"entry distance rejected: entry={entry:0.00} "
          + $"zone={candidate.EntryZone.Low:0.00}-{candidate.EntryZone.High:0.00} "
          + $"raw={distance:0.00} pip={options.PipSize} -> "
          + $"{distancePips:0.0} pips, cap {options.MaxEntryDistancePips:0.0}"
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
    if (!spot.Symbol.Equals(symbol.RedisSymbol, StringComparison.OrdinalIgnoreCase))
    {
      return;
    }
    foreach (var original in _states.Values.ToArray())
    {
      var state = _states.GetValueOrDefault(original.PositionId, original);
      while (
        state.RemainingVolume > 0
        && state.NextTargetIndex < state.TargetsPips.Count
      )
      {
        var completedTargetIndex = state.NextTargetIndex;
        var targetOrdinal = TargetOrdinal(state, completedTargetIndex);
        var targetPips = state.TargetsPips[state.NextTargetIndex];
        var target = TargetPrice(state, targetPips, completedTargetIndex);
        var exitQuote = state.Direction == TradeDirection.Buy ? spot.Bid : spot.Ask;
        var hit = state.Direction == TradeDirection.Buy
          ? exitQuote >= target
          : exitQuote <= target;
        if (!hit)
        {
          break;
        }
        var closeVolume = state.NextTargetIndex == state.TargetsPips.Count - 1
          ? state.RemainingVolume
          : Math.Min(state.Slices[state.NextTargetIndex], state.RemainingVolume);
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
        };
        _states[state.PositionId] = state;
        await PropagateGroupMetadataAsync(state, cancellationToken);
        var targetLabel = state.TargetsPips.Count == 1
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
          }
          break;
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
      options.BreakEvenBufferPips
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
    var moveMessage = $"🛡 ApexVoid Algo stop → {move.StopLoss:N2} ({move.Label}) "
      + $"· position {state.PositionId}";
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
      direction: DirectionLabel(state.Direction)
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
    }
    var botPositions = _allSymbolPositions
      .Where(position => position.Label == options.Label)
      .ToArray();
    var openIds = botPositions.Select(position => position.PositionId).ToHashSet();
    var trackedIds = await store.GetTrackedPositionIdsAsync(cancellationToken);
    foreach (var stale in trackedIds.Where(id => !openIds.Contains(id)))
    {
      var state = _states.GetValueOrDefault(stale)
        ?? await store.GetPositionAsync(stale, cancellationToken);
      _states.Remove(stale);
      await store.DeletePositionAsync(stale, cancellationToken);
      if (state is not null)
      {
        var initialVolume = state.GroupInitialVolume > 0
          ? state.GroupInitialVolume
          : state.InitialVolume;
        // Broker snapshot disappearance does not expose the true fill. Use the
        // last known protective stop (or entry) to book the remaining volume
        // into the volume-weighted net so Telegram can show Total net pips.
        var exitEstimate = state.CurrentStopLoss ?? state.EntryPrice;
        var remainingVolume = Math.Max(0, state.RemainingVolume);
        var pipVolume = state.GroupRealizedPipVolume
          + SignedPips(state, exitEstimate) * remainingVolume;
        var terminalGroupPips = WeightedPips(pipVolume, initialVolume);
        var groupId = GroupId(state);
        await PublishAsync(
          "position_closed",
          "position is no longer open at broker (SL or manual close)",
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
    foreach (var position in botPositions)
    {
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
    var executorSnapshot = new AutoTradeExecutorSnapshot(
      symbol.RedisSymbol,
      options.Profile,
      EffectiveExposurePolicy().ToString(),
      Demo: _account is { IsLive: false },
      Hedged: _accountSupportsHedging,
      Ready: _ready,
      PositionIds: _allSymbolPositions
        .Where(item => item.Label == options.Label)
        .Select(item => item.PositionId)
        .ToArray(),
      PendingOrderIds: _allSymbolPendingOrders
        .Where(item => item.Label == options.Label)
        .Select(item => item.OrderId)
        .ToArray(),
      GroupIds: _states.Values
        .Select(GroupId)
        .Distinct(StringComparer.Ordinal)
        .Order()
        .ToArray(),
      UpdatedAt: _clock().ToUnixTimeSeconds()
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
    if (state is null)
    {
      _log($"auto-trade cannot reconstruct position {position.PositionId}");
      return;
    }
    if (stored is null)
    {
      var plan = string.IsNullOrWhiteSpace(state.GroupId)
        ? null
        : await LoadGroupPlanAsync(state.GroupId, cancellationToken);
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
          TargetPrices = plan.TargetPrices,
          ZoneId = plan.ZoneId,
          TriggerId = plan.TriggerId,
          ParentGroupId = plan.ParentGroupId,
          StructuralSource = plan.StructuralSource,
          ReactionId = plan.ReactionId,
          ThesisId = plan.ThesisId,
        };
      }
    }
    state = state with
    {
      RemainingVolume = position.Volume,
      CurrentStopLoss = position.StopLoss ?? state.CurrentStopLoss,
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
    }
    foreach (var position in oppositePositions)
    {
      await RequireClient().ClosePositionAsync(
        position.PositionId,
        position.Volume,
        cancellationToken
      );
      _states.Remove(position.PositionId);
      await store.DeletePositionAsync(position.PositionId, cancellationToken);
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
    // The publisher of this candidate owns the claim; only a different
    // candidate_id on a live claim is a duplicate.
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

  private async Task SaveGroupPlanAsync(
    TradeCandidate candidate,
    string groupId,
    CancellationToken cancellationToken
  )
  {
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
      candidate.ThesisId
    );
    await store.SetValueAsync(
      $"auto_trade:group_plan:{groupId}",
      JsonSerializer.Serialize(
        plan,
        RedisJsonContext.Default.AutoTradeGroupPlan
      ),
      cancellationToken
    );
  }

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
    if (value.Contains("range"))
    {
      return "range";
    }
    if (value.Contains("trend") || value.Contains("breakout"))
    {
      return "trend";
    }
    if (value.Contains("map") || value.Contains("zone"))
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
      && !NormalizeBrokerIdentity(account.BrokerName).Contains(
        NormalizeBrokerIdentity(options.ExpectedBroker),
        StringComparison.Ordinal
      )
    )
    {
      throw new AutoTradeConfigurationException(
        $"Auto trade disabled: broker {account.BrokerName} does not match "
        + options.ExpectedBroker
      );
    }
  }

  private static string NormalizeBrokerIdentity(string value) => string.Concat(
    value.Where(char.IsLetterOrDigit).Select(char.ToLowerInvariant)
  );

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
    await store.CompleteCandidateAsync(
      candidate.CandidateId,
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
      entryHigh: candidate.EntryZone.High
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
    long? lotSize = null
  )
  {
    var state = LifecycleState(type, remainingVolume);
    var owner = candidateId ?? groupId ?? "service";
    string? previousState = null;
    try
    {
      previousState = await store.GetValueAsync(
        $"auto_trade:lifecycle_state:{owner}",
        cancellationToken
      );
    }
    catch (Exception exception) when (
      exception is not OperationCanceledException
    )
    {
      _log($"auto-trade lifecycle state read failed: {exception.Message}");
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
      state,
      reasonCode,
      matchId,
      rangeId,
      strategyFamily,
      options.Profile,
      _account?.AccountType,
      _account?.BrokerName,
      candidateId ?? groupId ?? lifecycleId,
      previousState,
      pendingOrderIds,
      orderId,
      stopLoss,
      targetPrices,
      entryLow,
      entryHigh,
      legRealizedPips,
      groupInitialVolume,
      lotSize
    );
    await store.PublishAutoTradeEventAsync(
      options.EventStream,
      tradeEvent,
      cancellationToken
    );
    try
    {
      await store.RecordLifecycleEventAsync(tradeEvent, cancellationToken);
      if (
        !string.IsNullOrWhiteSpace(rangeId)
        && !string.IsNullOrWhiteSpace(direction)
      )
      {
        var railState = state switch
        {
          "order_submitted" or "order_accepted" => "ORDER_SUBMITTED",
          "order_filled" => "ORDER_FILLED",
          "managing" or "partially_closed" => "MANAGING",
          "closed" => "CLOSED",
          "rejected" or "expired" or "cancelled" => "REARMED",
          _ => state.ToUpperInvariant(),
        };
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

  private static string LifecycleState(string type, long? remainingVolume) =>
    type switch
    {
      "ready" => "auto_ready",
      "dry_run" => "order_planned",
      "executor_received" => "executor_received",
      "routing_selected" => "routing_selected",
      "order_planned" => "order_planned",
      "order_submitted" => "order_submitted",
      "order_accepted" => "order_accepted",
      "opened" or "manual_opened" or "add" => "order_filled",
      "managing" => "managing",
      "zone_planned" or "manual_planned" => "waiting_for_price",
      "take_profit" when remainingVolume is > 0 => "partially_closed",
      "take_profit" => "closed",
      "position_closed" or "manual_closed" or "group_result" => "closed",
      "rejected" => "rejected",
      "zone_expired" or "manual_expired" => "expired",
      "manual_cancelled" or "cancelled" => "cancelled",
      "invalidated" => "invalidated",
      "error" or "manual_command_error" or "config_fatal" => "error",
      _ => "managing",
    };

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
    candidate.Version == 3
    && candidate.Timeframe is not null
    && (
      candidate.Timeframe.Equals("M1", StringComparison.OrdinalIgnoreCase)
      || candidate.Timeframe.Equals("M5", StringComparison.OrdinalIgnoreCase)
    )
    && candidate.Setup == "Range Box Scalp"
    && candidate.Mode == "auto_box_scalp";

  private static bool IsTrendCandidate(TradeCandidate candidate) =>
    candidate.Version == 3
    && candidate.Timeframe.Equals("M1", StringComparison.OrdinalIgnoreCase)
    && candidate.Mode is "auto_trend_pullback" or "auto_trend_breakout"
      or "auto_box_breakout";

  private static bool IsStrategyMatchCandidate(TradeCandidate candidate) =>
    candidate.Version == 4
    && candidate.Timeframe is not null
    && (
      candidate.Timeframe.Equals("M1", StringComparison.OrdinalIgnoreCase)
      || candidate.Timeframe.Equals("M5", StringComparison.OrdinalIgnoreCase)
    )
    && !string.IsNullOrWhiteSpace(candidate.Setup)
    && candidate.Mode == "auto_strategy_match";

  private static bool IsManualAlgoCandidate(TradeCandidate candidate) =>
    candidate.Mode == "manual_algo";

  // On larger manual /algo positions the first booking should stay a
  // consistent ~0.05 lots rather than a proportional share that keeps
  // growing with account size - see VolumePlanner.FixFirstLegVolume.
  private const decimal ManualAlgoFirstLegThresholdLots = 0.13m;
  private const decimal ManualAlgoFirstLegLots = 0.05m;

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
      await store.ReleaseCandidateAsync(claimId, cancellationToken);
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
    if (!await store.TryClaimCandidateAsync(claimId, cancellationToken))
    {
      return false;
    }
    var direction = state.Direction == TradeDirection.Buy ? "BUY" : "SELL";
    var expiresAt = _clock().ToUnixTimeSeconds()
      + options.FlipConfirmTimeoutSeconds;
    await store.CompleteCandidateAsync(
      claimId,
      $"flip_pending:{direction}:{expiresAt}",
      cancellationToken
    );
    return true;
  }

  private async Task ReleaseFlipCloseAsync(
    AutoTradePositionState state,
    CancellationToken cancellationToken
  )
  {
    var claimId = FlipClaimId(state);
    if (claimId is not null)
    {
      await store.ReleaseCandidateAsync(claimId, cancellationToken);
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
    long expiresAt
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
      expiresAt.ToString(CultureInfo.InvariantCulture)
    );
    if (comment.Length > 100)
    {
      throw new VolumePlanningException(
        $"manual algo comment is {comment.Length} chars; cTrader maximum is 100"
      );
    }
    return comment;
  }

  private static AutoTradePositionState? ParseManualComment(TradingPosition position)
  {
    var parts = position.Comment.Split('|');
    if (parts.Length != 9 || parts[0] != "avm")
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
        TrancheIndex: 1,
        GroupOpenedAt: barTs,
        LastTrancheBarTs: barTs,
        GroupTrancheCount: 1,
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
      parts.Length != 9
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

  private static string ClientOrderId(string candidateId) =>
    $"av-{candidateId[..Math.Min(40, candidateId.Length)]}";

  private static string CandidateToken(string candidateId) =>
    candidateId[..Math.Min(10, candidateId.Length)];

  private static string GroupToken(string groupId) =>
    groupId[..Math.Min(10, groupId.Length)];

  private static string Short(string candidateId) =>
    candidateId[..Math.Min(12, candidateId.Length)];

  private static void Log(string message) =>
    Console.Error.WriteLine($"ctrader-feed {message}");

  private sealed class CandidateRejectedException(string message)
    : Exception(message);
}

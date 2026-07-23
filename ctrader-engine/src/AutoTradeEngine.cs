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
      ValidateAccount(account);
      _log(VolumePlanner.SizingDiagnostic(account.Balance, options));
      await ReconcileAsync(cancellationToken);
      _ready = true;
      await PublishAsync(
        "ready",
        $"demo executor ready: {account.BrokerName} balance {account.Balance:N2}",
        cancellationToken
      );
      _log(
        $"auto-trade ready account={account.AccountId} broker={account.BrokerName} "
        + $"balance={account.Balance:N2} dryRun={options.DryRun}"
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
          return Task.CompletedTask;
        },
        CancellationToken.None
      );
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
    await PublishAsync("error", exception.Message, cancellationToken);
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
    if (!await store.TryClaimCandidateAsync(candidate.CandidateId, cancellationToken))
    {
      var status = await store.GetCandidateStatusAsync(
        candidate.CandidateId,
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
    if (
      boxRangeScalp
      && (
        string.IsNullOrWhiteSpace(candidate.RangeId)
        || candidate.RangeLow is not decimal rangeLow
        || candidate.RangeHigh is not decimal rangeHigh
        || rangeLow <= 0
        || rangeHigh <= rangeLow
        || candidate.FullTakeProfitPips is not (50 or 70)
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
      manualAlgoCandidate
      && (
        candidate.TargetsPips is not { Count: > 0 } manualTargetsPips
        || manualTargetsPips.Any(pips => pips <= 0)
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
    await ReconcileAsync(cancellationToken);
    if (
      boxRangeScalp
      && options.RangeFlipEnabled
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
    if (hasUnmanagedPosition || hasUnmanagedOrder)
    {
      return await RejectAsync(
        candidate,
        "unmanaged XAU position or pending order already open",
        cancellationToken
      );
    }
    if (
      boxRangeScalp
      && (_allSymbolPositions.Count > 0 || _allSymbolPendingOrders.Count > 0)
    )
    {
      return await RejectAsync(
        candidate,
        "range-box scalp waits for flat XAU exposure",
        cancellationToken
      );
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
    var direction = ParseDirection(candidate.Direction);
    var expectedEntry = direction == TradeDirection.Buy ? quote.Ask : quote.Bid;
    StructureStopPlan stopPlan;
    try
    {
      stopPlan = manualAlgoCandidate
        ? ManualStop(candidate, direction, expectedEntry, symbol)
        : StructureStop(candidate, direction, expectedEntry, symbol);
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
    if (!manualAlgoCandidate)
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

    var group = _states.Values
      .Where(state => state.SymbolId == symbol.SymbolId)
      .OrderBy(state => state.TrancheIndex)
      .ToArray();
    if (group.Length == 0)
    {
      if (_allSymbolPendingOrders.Count > 0)
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
    if (IsManualAlgoCandidate(candidate))
    {
      return await ProcessManualAlgoAsync(
        candidate,
        account,
        direction,
        expectedEntry,
        stopPlan,
        date,
        cancellationToken
      );
    }
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
    var groupId = GroupToken(candidate.CandidateId);
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
    if (_allSymbolPositions.Count > 0)
    {
      return await RejectAsync(
        candidate,
        "XAU position appeared before initial order",
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
    var proximal = direction == TradeDirection.Buy
      ? candidate.EntryZone.High
      : candidate.EntryZone.Low;
    StructureStopPlan zoneStopPlan;
    InitialSizingResult sizing;
    try
    {
      zoneStopPlan = StructureStop(candidate, direction, proximal, symbol);
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
      ? proximal <= expectedEntry
      : proximal >= expectedEntry;
    if (!validLimitSide)
    {
      return await RejectAsync(
        candidate,
        "zone-fill proximal edge is not on the valid limit-order side",
        cancellationToken
      );
    }
    ZoneFillPlan plan;
    try
    {
      var stopLoss = direction == TradeDirection.Buy
        ? proximal - zoneStopPlan.Distance
        : proximal + zoneStopPlan.Distance;
      stopLoss = decimal.Round(
        stopLoss,
        symbol.Digits,
        MidpointRounding.AwayFromZero
      );
      plan = ZoneFillPlanner.Build(
        direction,
        candidate.EntryZone,
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
    var groupId = GroupToken(candidate.CandidateId);
    var barTs = candidate.BarTs ?? candidate.CreatedAt;
    if (options.DryRun)
    {
      return await CompleteDryRunAsync(
        candidate,
        $"zone fill · {sizing.Lots:N2} lots across {plan.Legs.Count} limits · "
          + $"SL {plan.StopLoss:N2} · {sizing.BindingTerm}",
        sizing.Volume,
        proximal,
        cancellationToken
      );
    }
    if (await store.IsPausedAsync(cancellationToken))
    {
      return await RejectAsync(candidate, "executor paused", cancellationToken);
    }
    await ReconcileAsync(cancellationToken);
    if (_allSymbolPositions.Count > 0 || _allSymbolPendingOrders.Count > 0)
    {
      return await RejectAsync(
        candidate,
        "XAU exposure appeared before zone-fill orders",
        cancellationToken
      );
    }
    var placed = new List<long>();
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
    await store.IncrementDailyTradeCountAsync(date, cancellationToken);
    await PublishAsync(
      "zone_planned",
      $"zone fill · {sizing.Lots:N2} lots · limits "
        + string.Join(" / ", plan.Legs.Select(leg =>
          $"{leg.LimitPrice:N2} ({leg.Volume / (decimal)symbol.LotSize:N2})"
        ))
        + $" · SL {plan.StopLoss:N2} · midpoint TTL "
        + $"{options.ZoneFillTtlBars} bars · {sizing.BindingTerm}",
      cancellationToken,
      candidate.CandidateId,
      volume: sizing.Volume,
      price: proximal,
      groupId: groupId,
      trancheIndex: 1,
      groupWorstCase: -sizing.Lots * zoneStopPlan.StopPips
        * options.PipValuePerLot,
      riskBudget: sizing.Budget,
      hadAdds: false
    );
    await ReconcileAsync(cancellationToken);
    return true;
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

  // Owner's manual /algo signal: a single pending LIMIT order at the
  // proximal-or-current-price edge (mirrors ZoneFillPlanner's proximal-edge
  // concept, one leg instead of two), an absolute stop from ManualStop, and
  // TargetsPips taken directly from the candidate (already pip-converted by
  // the Python bridge). No AutoTradePositionState/store.SavePositionAsync
  // happens here - like zone-fill, that only happens once ReconcileAsync
  // notices the limit order filled and reconstructs state from its comment.
  private async Task<bool> ProcessManualAlgoAsync(
    TradeCandidate candidate,
    TradingAccountSnapshot account,
    TradeDirection direction,
    decimal expectedEntry,
    StructureStopPlan stopPlan,
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
    StructureStopPlan manualStopPlan;
    try
    {
      manualStopPlan = ManualStop(candidate, direction, limitPrice, symbol);
    }
    catch (VolumePlanningException exception)
    {
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    var (guardedStopPlan, stopRejectReason, stopNotice) = ApplyOpposingZoneGuard(
      candidate,
      direction,
      limitPrice,
      manualStopPlan,
      symbol
    );
    if (stopRejectReason is not null)
    {
      return await RejectAsync(candidate, stopRejectReason, cancellationToken);
    }
    manualStopPlan = guardedStopPlan;
    if (stopNotice is not null)
    {
      await PublishAsync(
        "warning",
        stopNotice,
        cancellationToken,
        candidate.CandidateId,
        setup: candidate.Setup,
        stopPips: manualStopPlan.StopPips
      );
    }
    var targetsPips = candidate.TargetsPips!;
    var targetWeights = EqualWeights(targetsPips.Count);
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
    var groupId = GroupToken(candidate.CandidateId);
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
        cancellationToken
      );
    }
    if (await store.IsPausedAsync(cancellationToken))
    {
      return await RejectAsync(candidate, "executor paused", cancellationToken);
    }
    await ReconcileAsync(cancellationToken);
    if (_allSymbolPositions.Count > 0 || _allSymbolPendingOrders.Count > 0)
    {
      return await RejectAsync(
        candidate,
        "XAU exposure appeared before manual algo order",
        cancellationToken
      );
    }
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
      "manual_planned",
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
      targetsPips: sizing.TargetPlan.TargetsPips
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
    var triggerFailure = ValidateAddTriggers(
      candidate,
      direction,
      expectedEntry,
      quote,
      group,
      RequireSymbol()
    );
    if (triggerFailure is not null)
    {
      return await RejectAsync(
        candidate,
        triggerFailure,
        cancellationToken
      );
    }
    var groupBooked = GroupBookedPnl(group);
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
      RequireSymbol(),
      options.TargetsPips,
      options.TargetWeights
    );
    if (!decision.Allowed || decision.TargetPlan is null)
    {
      return await RejectAsync(candidate, decision.Reason, cancellationToken);
    }
    _log(decision.SizingLog);
    var groupId = GroupId(group[0]);
    var trancheIndex = group.Max(state => state.TrancheIndex) + 1;
    var barTs = candidate.BarTs ?? candidate.CreatedAt;
    if (options.DryRun)
    {
      return await CompleteDryRunAsync(
        candidate,
        $"Tranche {trancheIndex} · {decision.Lots:N2} lots · "
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
      message: $"➕ Tranche {trancheIndex} · {decision.Lots:N2} lots · "
        + $"stop {stopPlan.StopPips:N0}p (structure) · "
        + $"{decision.BindingTerm} · group worst "
        + $"${decision.PostAddWorstCase:N1} / budget ${decision.Budget:N0}",
      groupWorstCase: decision.PostAddWorstCase,
      riskBudget: decision.Budget,
      cancellationToken
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
    CancellationToken cancellationToken
  )
  {
    var client = RequireClient();
    var now = _clock().ToUnixTimeSeconds();
    var symbol = RequireSymbol();
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
      Setup: candidate.Setup,
      Regime: candidate.Regime,
      Confluence: candidate.Confluence,
      RangeId: candidate.RangeId,
      RangeLow: candidate.RangeLow,
      RangeHigh: candidate.RangeHigh,
      RangeExitPrice: IsBoxRangeScalp(candidate)
        ? BoxExitPrice(candidate, direction)
        : null,
      Stream: "algo_auto"
    );
    _states[state.PositionId] = state;
    await PropagateGroupMetadataAsync(state, cancellationToken);
    await store.SavePositionAsync(state, cancellationToken);
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
      setup: candidate.Setup,
      regime: candidate.Regime,
      confluence: candidate.Confluence,
      stopPips: stopPlan.StopPips,
      targetsPips: targetPlan.TargetsPips,
      stream: state.Stream,
      direction: DirectionLabel(direction)
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

  // The owner's exact entered stop, never a re-derived structure stop -
  // this is the entire reason the manual-algo path exists. No min/max stop
  // pips clamping either: options.AddMinStopPips/TrendStopMinPips/MaxPips
  // exist to bound the AUTONOMOUS engines' own structure-derived stops, not
  // an owner's explicit price.
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
      if (IsManualAlgoCandidate(candidate))
      {
        _log(
          $"manual algo kept owner SL {stopPlan.StopLoss:0.####}; "
          + $"opposing-zone push disabled ({zoneLow:0.####}-{zoneHigh:0.####})"
        );
        return (stopPlan, null, null);
      }
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
      if (IsManualAlgoCandidate(candidate))
      {
        _log(
          $"manual algo kept owner SL {stopPlan.StopLoss:0.####}; widening to "
          + $"{pushedStop:0.####} beyond opposing zone would exceed the "
          + $"{maximumStopPips}p autonomous envelope"
        );
        return (stopPlan, null, null);
      }
      var rejectReason =
        $"{zoneDescription} - pushing beyond it would need {pushedPips:0.#}p, "
        + $"over the {maximumStopPips}p max";
      _log($"auto-trade stop rejected: {rejectReason}");
      return (stopPlan, rejectReason, null);
    }
    if (IsManualAlgoCandidate(candidate))
    {
      var widens = direction == TradeDirection.Buy
        ? pushedStop <= stopPlan.StopLoss
        : pushedStop >= stopPlan.StopLoss;
      if (!widens)
      {
        _log(
          $"manual algo discarded opposing-zone guard that would tighten owner "
          + $"SL {stopPlan.StopLoss:0.####} -> {pushedStop:0.####}"
        );
        return (stopPlan, null, null);
      }
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
    var notice = IsManualAlgoCandidate(candidate)
      ? $"SL widened {stopPlan.StopLoss:0.####} -> {pushedStop:0.####} · "
        + $"cleared opposing zone {zoneLow:0.####}-{zoneHigh:0.####}"
      : null;
    return (pushedPlan, null, notice);
  }

  private string? ValidateAddTriggers(
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
      return "scale-in group is empty";
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
      GroupOpenedAt(group),
      candidate.OpposingLevelDistanceAtr,
      options.AddLevelBufferAtr,
      candidate.BarTs ?? 0,
      group.Max(state => state.LastTrancheBarTs),
      options.AddCooldownBars
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
    CancellationToken cancellationToken
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
      price: price
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
        var target = TargetPrice(state, targetPips);
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
        await PublishAsync(
          "take_profit",
          $"{targetLabel} +{targetPips} pips closed volume {closeVolume}",
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
          groupRealizedPips: WeightedPips(
            groupPipVolume,
            groupInitialVolume
          ),
          counterfactualPips: WeightedPips(
            initialPipVolume,
            initialTrancheVolume
          ),
          stopPips: InitialStopPips(state),
          setup: state.Setup,
          regime: state.Regime,
          confluence: state.Confluence,
          stream: ExecutionStream(state),
          direction: DirectionLabel(state.Direction)
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
            await PublishAsync(
              "group_result",
              $"group {groupId} realised ${groupBooked:N2} · "
              + $"{groupPips:N1} pips · no-add counterfactual "
              + $"${initialBooked:N2} / {counterfactualPips:N1} pips · adds "
              + (addDelta > 0 ? "improved" : "degraded")
              + $" ${Math.Abs(addDelta):N2}",
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
              regime: state.Regime,
              confluence: state.Confluence,
              stopPips: InitialStopPips(state),
              stream: ExecutionStream(state),
              direction: DirectionLabel(state.Direction)
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
      hadAdds: state.HadAdds
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
      await PublishAsync(
        "zone_expired",
        $"zone midpoint limit {order.OrderId} cancelled after "
          + $"{options.ZoneFillTtlBars} bars; filled volume keeps its "
          + "proportional ladder",
        cancellationToken,
        groupId: zone.Value.GroupId,
        trancheIndex: 1,
        hadAdds: false
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
        await PublishAsync(
          "position_closed",
          "position is no longer open at broker (SL or manual close)",
          cancellationToken,
          state.CandidateId,
          stale,
          price: state.CurrentStopLoss,
          groupId: GroupId(state),
          setup: state.Setup,
          regime: state.Regime,
          confluence: state.Confluence,
          stopPips: InitialStopPips(state),
          stream: ExecutionStream(state),
          direction: DirectionLabel(state.Direction)
        );
        // Reached only when the engine did NOT close this position itself
        // (a clean take-profit exit untracks in ProcessTargetsAsync before
        // this branch ever runs) - so this is always an SL hit or a manual
        // close, indistinguishable from the data available here. Per the
        // 23 Jul 2026 incident (a stopped-out zone re-entered 15 minutes
        // later), default to starting the cooldown every time rather than
        // trying to guess which one it was.
        if (state.CurrentStopLoss is decimal lastStopLoss)
        {
          var directionLabel = state.Direction == TradeDirection.Buy ? "BUY" : "SELL";
          await store.RecordZoneCooldownAsync(
            RequireSymbol().RedisSymbol,
            directionLabel,
            state.EntryPrice,
            lastStopLoss,
            _clock().ToUnixTimeSeconds(),
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
  }

  private async Task AdoptPositionAsync(
    TradingPosition position,
    CancellationToken cancellationToken
  )
  {
    var stored = await store.GetPositionAsync(position.PositionId, cancellationToken);
    var state = stored ?? ParseComment(position);
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
    state = state with
    {
      RemainingVolume = position.Volume,
      CurrentStopLoss = position.StopLoss ?? state.CurrentStopLoss,
    };
    _states[position.PositionId] = state;
    await store.SavePositionAsync(state, cancellationToken);
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
        setup: "Manual Algo",
        stopPips: InitialStopPips(state),
        targetsPips: state.TargetsPips,
        stream: state.Stream,
        direction: directionLabel
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
    if (!account.AccountType.Equals("Hedged", StringComparison.OrdinalIgnoreCase))
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: auto-trade requires a Hedged demo account, "
        + $"got {account.AccountType}"
      );
    }
    if (
      !string.IsNullOrWhiteSpace(options.ExpectedBroker)
      && !account.BrokerName.Contains(
        options.ExpectedBroker,
        StringComparison.OrdinalIgnoreCase
      )
    )
    {
      throw new AutoTradeConfigurationException(
        $"Auto trade disabled: broker {account.BrokerName} does not match "
        + options.ExpectedBroker
      );
    }
  }

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
    await store.CompleteCandidateAsync(
      candidate.CandidateId,
      $"rejected:{reason}",
      cancellationToken
    );
    await PublishAsync(
      "rejected",
      $"candidate {Short(candidate.CandidateId)} rejected: {reason}",
      cancellationToken,
      candidate.CandidateId
    );
    _log($"auto-trade candidate {Short(candidate.CandidateId)} rejected: {reason}");
    return true;
  }

  private Task PublishAsync(
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
    long? remainingVolume = null
  ) => store.PublishAutoTradeEventAsync(
    options.EventStream,
    new AutoTradeEvent(
      type,
      _clock().ToUnixTimeSeconds(),
      message,
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
      remainingVolume
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
    candidate.Version == 3
    && candidate.Mode == "manual_algo"
    && candidate.ManualStopLoss is not null;

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
    int targetPips
  ) => state.RangeExitPrice ?? (
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
        Stream: "algo_manual"
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

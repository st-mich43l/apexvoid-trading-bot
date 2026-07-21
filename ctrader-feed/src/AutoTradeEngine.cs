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
  private bool _ready;
  private volatile bool _disabled;

  public bool Enabled => options.Enabled && !_disabled;

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
      await ReportDepositAssetWarningAsync(account, cancellationToken);
      ValidateAccount(account);
      await ReconcileAsync(cancellationToken);
      _ready = true;
      await PublishAsync(
        "ready",
        $"demo executor ready: {account.BrokerName} balance {account.Balance:N2}",
        cancellationToken
      );
      _log(
        $"auto-trade ready account={account.AccountId} broker={account.BrokerName} "
        + $"balance={account.Balance:N2} asset={account.DepositAsset} "
        + $"risk={options.RiskPercent:N1}% "
        + $"pipValuePerLot=${options.PipValuePerLot:N2} "
        + $"maxLots={options.MaxLots:N2} dryRun={options.DryRun}"
      );

      var cursor = await store.GetCursorAsync(cancellationToken);
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
      _ready = false;
      _client = null;
      _symbol = null;
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
      () => ProcessTargetsAsync(spot, cancellationToken),
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

  private async Task<bool> ProcessCandidateAsync(
    TradeCandidate candidate,
    CancellationToken cancellationToken
  )
  {
    var client = RequireClient();
    var symbol = RequireSymbol();
    var now = _clock().ToUnixTimeSeconds();
    var autoRangeScalp = string.Equals(
        candidate.Timeframe,
        "M1",
        StringComparison.OrdinalIgnoreCase
      )
      && candidate.Setup == "Auto Range Scalp"
      && candidate.Mode == "auto_range_scalp";
    if (
      candidate.Version != 1
      || !autoRangeScalp
      || candidate.Confluence < options.MinConfluence
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
      return await RejectAsync(candidate, "XAU position already open", cancellationToken);
    }
    var date = DateOnly.FromDateTime(_clock().UtcDateTime);
    var tradeCount = await store.GetDailyTradeCountAsync(date, cancellationToken);
    if (tradeCount >= options.MaxDailyTrades)
    {
      return await RejectAsync(candidate, "daily trade cap reached", cancellationToken);
    }

    var account = await client.GetTradingAccountAsync(cancellationToken);
    ValidateAccount(account);
    RiskSizingResult sizing;
    IReadOnlyList<long> slices;
    try
    {
      sizing = VolumePlanner.SizeForRisk(
        account.Balance,
        options.RiskPercent,
        options.StopLossDistance,
        options.PipValuePerLot,
        options.MaxLots,
        symbol
      );
      VolumePlanner.EnsureTargetFeasibility(
        sizing,
        account.Balance,
        options.RiskPercent,
        options.PipValuePerLot,
        options.TargetWeights.Count,
        symbol
      );
      slices = VolumePlanner.SplitWeighted(
        sizing.Volume,
        symbol,
        options.TargetWeights
      );
    }
    catch (VolumePlanningException exception)
    {
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    var lots = sizing.Lots;
    var volume = sizing.Volume;
    SpotPrice quote;
    try
    {
      quote = ValidateQuote(candidate, symbol);
    }
    catch (CandidateRejectedException exception)
    {
      return await RejectAsync(candidate, exception.Message, cancellationToken);
    }
    var direction = ParseDirection(candidate.Direction);
    var expectedEntry = direction == TradeDirection.Buy ? quote.Ask : quote.Bid;
    if (options.DryRun)
    {
      await store.CompleteCandidateAsync(
        candidate.CandidateId,
        "dry_run",
        cancellationToken
      );
      await PublishAsync(
        "dry_run",
        $"{direction} {lots:N2} lots planned at {expectedEntry:N2}",
        cancellationToken,
        candidate.CandidateId,
        volume: volume,
        price: expectedEntry
      );
      return true;
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
        "XAU position appeared before order",
        cancellationToken
      );
    }

    var comment = BuildComment(candidate.CandidateId, volume, slices, options.TargetsPips);
    var execution = await client.PlaceMarketOrderAsync(
      new MarketOrderRequest(
        symbol.SymbolId,
        direction,
        volume,
        decimal.ToInt64(options.StopLossDistance * 100_000m),
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
      ? fill - options.StopLossDistance
      : fill + options.StopLossDistance;
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
      slices,
      options.TargetsPips,
      0,
      now,
      stopLoss
    );
    _states[state.PositionId] = state;
    await store.SavePositionAsync(state, cancellationToken);
    await store.CompleteCandidateAsync(
      candidate.CandidateId,
      $"ordered:{state.PositionId}",
      cancellationToken
    );
    await store.IncrementDailyTradeCountAsync(date, cancellationToken);
    await PublishAsync(
      "opened",
      $"{direction} {lots:N2} lots filled {fill:N2}, SL {stopLoss:N2}",
      cancellationToken,
      candidate.CandidateId,
      state.PositionId,
      volume: volume,
      price: fill
    );
    return true;
  }

  private SpotPrice ValidateQuote(TradeCandidate candidate, SymbolInfo symbol)
  {
    var quote = _lastSpot
      ?? throw new CandidateRejectedException("live cTrader quote unavailable");
    var age = _clock().ToUnixTimeSeconds() - quote.Timestamp;
    if (age < 0 || age > Math.Max(1, options.SpotMaxAgeSeconds))
    {
      throw new CandidateRejectedException("live cTrader quote is stale");
    }
    var pip = VolumePlanner.PipSize(symbol);
    var spreadPips = (quote.Ask - quote.Bid) / pip;
    if (spreadPips < 0 || spreadPips > options.MaxSpreadPips)
    {
      throw new CandidateRejectedException(
        $"spread {spreadPips:N1} pips exceeds cap {options.MaxSpreadPips}"
      );
    }
    var direction = ParseDirection(candidate.Direction);
    var entry = direction == TradeDirection.Buy ? quote.Ask : quote.Bid;
    var distance = entry < candidate.EntryZone.Low
      ? candidate.EntryZone.Low - entry
      : entry > candidate.EntryZone.High
        ? entry - candidate.EntryZone.High
        : 0m;
    var distancePips = distance / pip;
    if (distancePips > options.MaxEntryDistancePips)
    {
      throw new CandidateRejectedException(
        $"entry moved {distancePips:N1} pips beyond candidate zone"
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
      var state = original;
      while (
        state.RemainingVolume > 0
        && state.NextTargetIndex < state.TargetsPips.Count
      )
      {
        var completedTargetIndex = state.NextTargetIndex;
        var targetPips = state.TargetsPips[state.NextTargetIndex];
        var target = TargetPrice(state, targetPips, symbol);
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
        var execution = await client.ClosePositionAsync(
          state.PositionId,
          closeVolume,
          cancellationToken
        );
        var remaining = execution.RemainingVolume
          ?? Math.Max(0, state.RemainingVolume - closeVolume);
        state = state with
        {
          RemainingVolume = remaining,
          NextTargetIndex = state.NextTargetIndex + 1,
        };
        await PublishAsync(
          "take_profit",
          $"TP{state.NextTargetIndex} +{targetPips} pips closed volume {closeVolume}",
          cancellationToken,
          state.CandidateId,
          state.PositionId,
          targetPips,
          closeVolume,
          execution.ExecutionPrice > 0 ? execution.ExecutionPrice : exitQuote
        );
        if (remaining <= 0)
        {
          _states.Remove(state.PositionId);
          await store.DeletePositionAsync(state.PositionId, cancellationToken);
          break;
        }
        state = await MoveStopAfterTargetAsync(
          state,
          completedTargetIndex,
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
    SymbolInfo symbol,
    CancellationToken cancellationToken
  )
  {
    var move = StopTrailPlanner.Plan(
      state,
      completedTargetIndex,
      symbol,
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
        + $"TP{completedTargetIndex + 1} failed: {exception.Message}";
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
    var moveMessage = $"🛡 Auto trade stop → {move.StopLoss:N2} ({move.Label}) "
      + $"· position {state.PositionId}";
    await PublishAsync(
      "stop_moved",
      moveMessage,
      cancellationToken,
      state.CandidateId,
      state.PositionId,
      price: move.StopLoss
    );
    return state with { CurrentStopLoss = move.StopLoss };
  }

  private async Task ReconcileAsync(CancellationToken cancellationToken)
  {
    var client = RequireClient();
    var symbol = RequireSymbol();
    var positions = await client.ReconcilePositionsAsync(cancellationToken);
    _allSymbolPositions = positions
      .Where(position => position.SymbolId == symbol.SymbolId)
      .ToArray();
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
          stale
        );
      }
    }
    foreach (var position in botPositions)
    {
      await AdoptPositionAsync(position, cancellationToken);
    }
  }

  private async Task AdoptPositionAsync(
    TradingPosition position,
    CancellationToken cancellationToken
  )
  {
    var state = await store.GetPositionAsync(position.PositionId, cancellationToken)
      ?? ParseComment(position);
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
    if (
      options.RequireUsdAccount
      && !account.DepositAsset.Equals("USD", StringComparison.OrdinalIgnoreCase)
    )
    {
      throw new AutoTradeConfigurationException(
        $"Auto trade disabled: account deposit asset is {account.DepositAsset}; "
        + "AUTO_TRADE_REQUIRE_USD_ACCOUNT requires USD"
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

  private async Task ReportDepositAssetWarningAsync(
    TradingAccountSnapshot account,
    CancellationToken cancellationToken
  )
  {
    if (account.DepositAsset.Equals("USD", StringComparison.OrdinalIgnoreCase))
    {
      return;
    }
    var message = $"account deposit asset {account.DepositAsset} — "
      + $"pip value ${options.PipValuePerLot:N2} per lot assumes USD";
    lock (_reportLock)
    {
      if (!_reportedWarnings.Add(message))
      {
        return;
      }
    }
    _log(message);
    await PublishAsync("warning", message, cancellationToken);
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
    decimal? price = null
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
      price
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

  private static decimal TargetPrice(
    AutoTradePositionState state,
    int targetPips,
    SymbolInfo symbol
  ) => state.Direction == TradeDirection.Buy
    ? state.EntryPrice + targetPips * VolumePlanner.PipSize(symbol)
    : state.EntryPrice - targetPips * VolumePlanner.PipSize(symbol);

  private static string BuildComment(
    string candidateId,
    long volume,
    IReadOnlyList<long> slices,
    IReadOnlyList<int> targets
  ) => string.Join(
    '|',
    "av1",
    CandidateToken(candidateId),
    volume.ToString(CultureInfo.InvariantCulture),
    string.Join(',', slices),
    string.Join(',', targets)
  );

  private static AutoTradePositionState? ParseComment(TradingPosition position)
  {
    var parts = position.Comment.Split('|');
    if (
      parts.Length != 5
      || parts[0] != "av1"
      || !long.TryParse(parts[2], CultureInfo.InvariantCulture, out var initial)
    )
    {
      return null;
    }
    try
    {
      var slices = parts[3].Split(',')
        .Select(value => long.Parse(value, CultureInfo.InvariantCulture))
        .ToArray();
      var targets = parts[4].Split(',')
        .Select(value => int.Parse(value, CultureInfo.InvariantCulture))
        .ToArray();
      if (
        slices.Length == 0
        || slices.Length != targets.Length
        || slices.Any(value => value <= 0)
        || targets.Any(value => value <= 0)
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
        position.StopLoss
      );
    }
    catch (FormatException)
    {
      return null;
    }
  }

  private static string ClientOrderId(string candidateId) =>
    $"av-{candidateId[..Math.Min(40, candidateId.Length)]}";

  private static string CandidateToken(string candidateId) =>
    candidateId[..Math.Min(24, candidateId.Length)];

  private static string Short(string candidateId) =>
    candidateId[..Math.Min(12, candidateId.Length)];

  private static void Log(string message) =>
    Console.Error.WriteLine($"ctrader-feed {message}");

  private sealed class CandidateRejectedException(string message)
    : Exception(message);
}

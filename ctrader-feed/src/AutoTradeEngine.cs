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
  private readonly Func<DateTimeOffset> _clock = clock ?? (() => DateTimeOffset.UtcNow);
  private readonly Action<string> _log = log ?? Log;
  private ICTraderTradeClient? _client;
  private SymbolInfo? _symbol;
  private SpotPrice? _lastSpot;
  private IReadOnlyList<TradingPosition> _allSymbolPositions = [];
  private bool _ready;

  public bool Enabled => options.Enabled;

  public async Task RunSessionAsync(
    ICTraderFeedClient feedClient,
    SymbolInfo symbol,
    CancellationToken cancellationToken
  )
  {
    if (!options.Enabled)
    {
      return;
    }
    options.Validate();
    _client = feedClient as ICTraderTradeClient
      ?? throw new InvalidOperationException(
        "Configured cTrader client does not support trade operations"
      );
    _symbol = symbol;
    var account = await _client.GetTradingAccountAsync(cancellationToken);
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
      + $"balance={account.Balance:N2} dryRun={options.DryRun}"
    );

    var cursor = await store.GetCursorAsync(cancellationToken);
    var nextReconcile = _clock();
    try
    {
      while (!cancellationToken.IsCancellationRequested)
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

  public async Task ObserveSpotAsync(
    SpotPrice spot,
    CancellationToken cancellationToken
  )
  {
    _lastSpot = spot;
    if (!_ready || options.DryRun || !options.Enabled)
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
      return await WithGateAsync(
        () => ProcessCandidateAsync(candidate, cancellationToken),
        cancellationToken
      );
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      await store.ReleaseCandidateAsync(candidate.CandidateId, cancellationToken);
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
    if (
      candidate.Version != 1
      || candidate.Setup != "Range Edge Scalp"
      || candidate.Mode != "range_scalp"
      || candidate.Confluence < options.MinConfluence
      || !candidate.Symbol.Equals(symbol.RedisSymbol, StringComparison.OrdinalIgnoreCase)
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
    var lots = VolumePlanner.LotsForBalance(account.Balance);
    var volume = VolumePlanner.VolumeForLots(lots, symbol);
    if (volume <= 0)
    {
      return await RejectAsync(
        candidate,
        "balance below 1K or broker volume invalid",
        cancellationToken
      );
    }
    var slices = VolumePlanner.SplitFive(volume, symbol);
    var quote = ValidateQuote(candidate, symbol);
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
      now
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
      ?? throw new InvalidOperationException("live cTrader quote unavailable");
    var age = _clock().ToUnixTimeSeconds() - quote.Timestamp;
    if (age < 0 || age > Math.Max(1, options.SpotMaxAgeSeconds))
    {
      throw new InvalidOperationException("live cTrader quote is stale");
    }
    var pip = PipSize(symbol);
    var spreadPips = (quote.Ask - quote.Bid) / pip;
    if (spreadPips < 0 || spreadPips > options.MaxSpreadPips)
    {
      throw new InvalidOperationException(
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
      throw new InvalidOperationException(
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
        _states[state.PositionId] = state;
        await store.SavePositionAsync(state, cancellationToken);
      }
    }
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
    state = state with { RemainingVolume = position.Volume };
    _states[position.PositionId] = state;
    await store.SavePositionAsync(state, cancellationToken);
  }

  private void ValidateAccount(TradingAccountSnapshot account)
  {
    if (account.IsLive)
    {
      throw new InvalidOperationException("auto-trade hard lock refuses live accounts");
    }
    if (
      !account.PermissionScope.Equals("ScopeTrade", StringComparison.OrdinalIgnoreCase)
      && !account.PermissionScope.Equals("Trading", StringComparison.OrdinalIgnoreCase)
    )
    {
      throw new InvalidOperationException("cTrader token does not have trading scope");
    }
    if (!account.AccessRights.Equals("FullAccess", StringComparison.OrdinalIgnoreCase))
    {
      throw new InvalidOperationException(
        $"cTrader account access is {account.AccessRights}, expected FullAccess"
      );
    }
    if (!account.AccountType.Equals("Hedged", StringComparison.OrdinalIgnoreCase))
    {
      throw new InvalidOperationException(
        $"auto-trade requires a Hedged demo account, got {account.AccountType}"
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
      throw new InvalidOperationException(
        $"broker {account.BrokerName} does not match {options.ExpectedBroker}"
      );
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

  private static decimal PipSize(SymbolInfo symbol)
  {
    var divisor = 1m;
    for (var index = 0; index < symbol.PipPosition; index++)
    {
      divisor *= 10m;
    }
    return 1m / divisor;
  }

  private static decimal TargetPrice(
    AutoTradePositionState state,
    int targetPips,
    SymbolInfo symbol
  ) => state.Direction == TradeDirection.Buy
    ? state.EntryPrice + targetPips * PipSize(symbol)
    : state.EntryPrice - targetPips * PipSize(symbol);

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
      if (slices.Length != targets.Length || slices.Length != 5)
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
        0
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
}

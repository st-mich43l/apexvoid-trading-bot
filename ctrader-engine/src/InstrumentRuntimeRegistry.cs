namespace ApexVoid.CTraderFeed;

/// <summary>
/// One instrument runtime under a shared account connection.
/// Credentials stay on the account host — never on this object.
/// </summary>
public sealed class InstrumentRuntime
{
  public required string InstrumentId { get; init; }
  public required FeedInstrumentOptions Feed { get; init; }
  public required ExecutionInstrumentOptions Execution { get; init; }
  public SymbolInfo? Symbol { get; set; }
  public bool FeedReady { get; set; }
  public bool AnalysisReady { get; set; }
  public bool ExecutionReady { get; set; }
  public long LastBarAtUnixSeconds { get; set; }

  public InstrumentRollout Rollout => Feed.Rollout;
}

/// <summary>
/// Account-scoped registry of instrument runtimes. One connection owns many.
/// </summary>
public sealed class InstrumentRuntimeRegistry
{
  private readonly Dictionary<string, InstrumentRuntime> _byId;
  private readonly Dictionary<string, string> _aliasToId;
  private readonly Dictionary<long, string> _symbolIdToId = new();
  private readonly object _gate = new();

  public InstrumentRuntimeRegistry(IEnumerable<InstrumentRuntime> runtimes)
  {
    _byId = new Dictionary<string, InstrumentRuntime>(StringComparer.Ordinal);
    _aliasToId = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    foreach (var runtime in runtimes.OrderBy(item => item.InstrumentId, StringComparer.Ordinal))
    {
      if (!_byId.TryAdd(runtime.InstrumentId, runtime))
      {
        throw new InvalidOperationException(
          $"duplicate instrument id {runtime.InstrumentId}"
        );
      }
      RegisterAlias(runtime.InstrumentId, runtime.InstrumentId);
      RegisterAlias(runtime.Feed.CanonicalSymbol, runtime.InstrumentId);
      RegisterAlias(runtime.Feed.CTraderSymbol, runtime.InstrumentId);
      RegisterAlias(runtime.Feed.RedisSymbol, runtime.InstrumentId);
      if (
        runtime.Feed.CanonicalSymbol.Equals("XAU", StringComparison.OrdinalIgnoreCase)
      )
      {
        RegisterAlias("XAUUSD", runtime.InstrumentId);
      }
    }
    if (_byId.Count == 0)
    {
      throw new InvalidOperationException("instrument runtime registry is empty");
    }
  }

  public IReadOnlyCollection<InstrumentRuntime> All =>
    _byId.Values.OrderBy(item => item.InstrumentId, StringComparer.Ordinal).ToArray();

  public IReadOnlyList<InstrumentRuntime> FeedInstruments() =>
    All.Where(item => InstrumentRolloutGates.PermitsFeed(item.Rollout)).ToArray();

  public IReadOnlyList<InstrumentRuntime> ExecutableInstruments() =>
    All.Where(item => InstrumentRolloutGates.PermitsCandidatePublication(item.Rollout))
      .ToArray();

  public IReadOnlyList<InstrumentRuntime> LiveInstruments() =>
    All.Where(item => InstrumentRolloutGates.PermitsBrokerExecution(item.Rollout))
      .ToArray();

  public InstrumentRuntime Get(string symbolOrId)
  {
    if (!_aliasToId.TryGetValue(symbolOrId.Trim(), out var id))
    {
      throw new InvalidOperationException($"unknown instrument symbol {symbolOrId}");
    }
    return _byId[id];
  }

  public bool TryGet(string symbolOrId, out InstrumentRuntime runtime)
  {
    if (_aliasToId.TryGetValue(symbolOrId.Trim(), out var id))
    {
      runtime = _byId[id];
      return true;
    }
    runtime = null!;
    return false;
  }

  public void BindResolvedSymbol(InstrumentRuntime runtime, SymbolInfo symbol)
  {
    lock (_gate)
    {
      runtime.Symbol = symbol;
      foreach (var existing in _symbolIdToId)
      {
        if (
          existing.Key == symbol.SymbolId
          && !existing.Value.Equals(runtime.InstrumentId, StringComparison.Ordinal)
        )
        {
          throw new InvalidOperationException(
            $"cTrader symbol id {symbol.SymbolId} conflicts between "
            + $"{existing.Value} and {runtime.InstrumentId}"
          );
        }
      }
      _symbolIdToId[symbol.SymbolId] = runtime.InstrumentId;
    }
  }

  public bool TryGetBySymbolId(long symbolId, out InstrumentRuntime runtime)
  {
    lock (_gate)
    {
      if (_symbolIdToId.TryGetValue(symbolId, out var id))
      {
        runtime = _byId[id];
        return true;
      }
    }
    runtime = null!;
    return false;
  }

  public static InstrumentRuntimeRegistry FromXauCompatibility(
    FeedOptions feed,
    AutoTradeOptions trade
  )
  {
    var feedInstrument = FeedInstrumentOptions.FromFeedOptions(feed);
    var execution = ExecutionInstrumentOptions.FromAutoTradeOptions(trade);
    return new InstrumentRuntimeRegistry([
      new InstrumentRuntime
      {
        InstrumentId = "XAU",
        Feed = feedInstrument,
        Execution = execution,
        FeedReady = false,
        AnalysisReady = false,
        ExecutionReady = false,
      },
    ]);
  }

  public static InstrumentRuntimeRegistry FromRuntimeManifest(
    ResolvedRuntimeManifest manifest,
    FeedOptions feedBootstrap,
    AutoTradeOptions tradeBootstrap
  )
  {
    if (
      manifest.InstrumentRuntimes is null
      || manifest.InstrumentRuntimes.Count == 0
    )
    {
      return FromXauCompatibility(feedBootstrap, tradeBootstrap);
    }
    var runtimes = new List<InstrumentRuntime>();
    foreach (var (instrumentId, element) in manifest.InstrumentRuntimes.OrderBy(
      item => item.Key,
      StringComparer.Ordinal
    ))
    {
      if (!element.TryGetProperty("rollout", out var rolloutEl))
      {
        throw new InvalidOperationException(
          $"instrument_runtimes.{instrumentId}.rollout is required"
        );
      }
      var rollout = InstrumentRolloutGates.Parse(rolloutEl.GetString());
      if (rollout == InstrumentRollout.Disabled)
      {
        continue;
      }
      if (!element.TryGetProperty("feed", out var feedEl))
      {
        throw new InvalidOperationException(
          $"instrument_runtimes.{instrumentId}.feed is required"
        );
      }
      var cTraderSymbol = feedEl.GetProperty("ctrader_symbol").GetString()
        ?? throw new InvalidOperationException(
          $"instrument_runtimes.{instrumentId}.feed.ctrader_symbol missing"
        );
      var redisSymbol = feedEl.GetProperty("redis_symbol").GetString()
        ?? throw new InvalidOperationException(
          $"instrument_runtimes.{instrumentId}.feed.redis_symbol missing"
        );
      var timeframes = feedEl.GetProperty("timeframes")
        .EnumerateArray()
        .Select(item => item.GetString() ?? "")
        .Where(item => item.Length > 0)
        .ToArray();
      var pip = 0.1m;
      var contract = 100m;
      if (element.TryGetProperty("units", out var units))
      {
        if (units.TryGetProperty("pip_size", out var pipEl))
        {
          pip = ManifestDecimal.Parse(
            pipEl.GetString() ?? "0.1",
            $"instrument_runtimes.{instrumentId}.units.pip_size"
          );
        }
        if (units.TryGetProperty("contract_units_per_lot", out var contractEl))
        {
          contract = ManifestDecimal.Parse(
            contractEl.GetString() ?? "100",
            $"instrument_runtimes.{instrumentId}.units.contract_units_per_lot"
          );
        }
      }
      runtimes.Add(new InstrumentRuntime
      {
        InstrumentId = instrumentId,
        Feed = new FeedInstrumentOptions(
          InstrumentId: instrumentId,
          CanonicalSymbol: redisSymbol,
          CTraderSymbol: cTraderSymbol,
          RedisSymbol: redisSymbol,
          Timeframes: timeframes.Length > 0 ? timeframes : feedBootstrap.Timeframes,
          BackfillBars: feedBootstrap.BackfillBars,
          BarsWindowMax: feedBootstrap.BarsWindowMax,
          BarsChannel: feedBootstrap.BarsChannel,
          BarQualityLookback: feedBootstrap.BarQualityLookback,
          Rollout: rollout
        ),
        Execution = new ExecutionInstrumentOptions(
          InstrumentId: instrumentId,
          CanonicalSymbol: redisSymbol,
          Rollout: rollout,
          PipSize: pip,
          ContractSize: contract,
          EffectiveSymbols: [redisSymbol]
        ),
      });
    }
    if (runtimes.Count == 0)
    {
      return FromXauCompatibility(feedBootstrap, tradeBootstrap);
    }
    return new InstrumentRuntimeRegistry(runtimes);
  }

  private void RegisterAlias(string alias, string instrumentId)
  {
    if (string.IsNullOrWhiteSpace(alias))
    {
      return;
    }
    var key = alias.Trim();
    if (
      _aliasToId.TryGetValue(key, out var existing)
      && !existing.Equals(instrumentId, StringComparison.Ordinal)
    )
    {
      throw new InvalidOperationException(
        $"duplicate alias {key} maps to both {existing} and {instrumentId}"
      );
    }
    _aliasToId[key] = instrumentId;
  }
}

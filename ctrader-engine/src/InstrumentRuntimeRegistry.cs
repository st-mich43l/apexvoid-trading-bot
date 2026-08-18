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

  /// <summary>
  /// Explicit V1 compatibility: one XAU runtime from top-level projections.
  /// </summary>
  public static InstrumentRuntimeRegistry FromManifestV1Projections(
    FeedOptions feed,
    AutoTradeOptions trade
  ) => FromXauCompatibility(feed, trade);

  /// <summary>
  /// Manifest V2 authority: every runtime from instrument_runtimes.
  /// Never falls back to XAU compatibility defaults.
  /// </summary>
  public static InstrumentRuntimeRegistry FromRuntimeManifestV2(
    ResolvedRuntimeManifest manifest,
    FeedOptions sharedFeed
  )
  {
    if (
      manifest.InstrumentRuntimes is null
      || manifest.InstrumentRuntimes.Count == 0
    )
    {
      throw new InvalidOperationException(
        "runtime manifest instrument_runtimes is required for manifest_version=2"
      );
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
      if (!element.TryGetProperty("units", out var units))
      {
        throw new InvalidOperationException(
          $"instrument_runtimes.{instrumentId}.units is required"
        );
      }
      if (!units.TryGetProperty("pip_size", out var pipEl)
        || string.IsNullOrWhiteSpace(pipEl.GetString()))
      {
        throw new InvalidOperationException(
          $"instrument_runtimes.{instrumentId}.units.pip_size is required"
        );
      }
      if (!units.TryGetProperty("contract_units_per_lot", out var contractEl)
        || string.IsNullOrWhiteSpace(contractEl.GetString()))
      {
        throw new InvalidOperationException(
          $"instrument_runtimes.{instrumentId}.units.contract_units_per_lot is required"
        );
      }
      var pip = ManifestDecimal.Parse(
        pipEl.GetString()!,
        $"instrument_runtimes.{instrumentId}.units.pip_size"
      );
      var contract = ManifestDecimal.Parse(
        contractEl.GetString()!,
        $"instrument_runtimes.{instrumentId}.units.contract_units_per_lot"
      );
      if (pip <= 0m || contract <= 0m)
      {
        throw new InvalidOperationException(
          $"instrument_runtimes.{instrumentId}.units must be positive"
        );
      }
      decimal pipValue = 0m;
      if (
        units.TryGetProperty("pip_value_per_lot", out var pipValueEl)
        && !string.IsNullOrWhiteSpace(pipValueEl.GetString())
      )
      {
        pipValue = ManifestDecimal.Parse(
          pipValueEl.GetString()!,
          $"instrument_runtimes.{instrumentId}.units.pip_value_per_lot"
        );
        if (pipValue <= 0m)
        {
          throw new InvalidOperationException(
            $"instrument_runtimes.{instrumentId}.units.pip_value_per_lot must be positive"
          );
        }
      }
      var cTraderSymbol = feedEl.GetProperty("ctrader_symbol").GetString();
      if (string.IsNullOrWhiteSpace(cTraderSymbol))
      {
        throw new InvalidOperationException(
          $"instrument_runtimes.{instrumentId}.feed.ctrader_symbol is required"
        );
      }
      var redisSymbol = feedEl.GetProperty("redis_symbol").GetString();
      if (string.IsNullOrWhiteSpace(redisSymbol))
      {
        throw new InvalidOperationException(
          $"instrument_runtimes.{instrumentId}.feed.redis_symbol is required"
        );
      }
      if (!feedEl.TryGetProperty("timeframes", out var tfEl) || tfEl.GetArrayLength() == 0)
      {
        throw new InvalidOperationException(
          $"instrument_runtimes.{instrumentId}.feed.timeframes is required"
        );
      }
      var timeframes = tfEl
        .EnumerateArray()
        .Select(item => item.GetString() ?? "")
        .Where(item => item.Length > 0)
        .ToArray();
      if (timeframes.Length == 0)
      {
        throw new InvalidOperationException(
          $"instrument_runtimes.{instrumentId}.feed.timeframes is empty"
        );
      }
      var backfill = sharedFeed.BackfillBars;
      var barsWindow = sharedFeed.BarsWindowMax;
      var barsChannel = sharedFeed.BarsChannel;
      var lookback = sharedFeed.BarQualityLookback;
      if (feedEl.TryGetProperty("backfill_bars", out var bf))
      {
        backfill = bf.GetInt32();
      }
      if (feedEl.TryGetProperty("bars_window_max", out var bw))
      {
        barsWindow = bw.GetInt32();
      }
      if (feedEl.TryGetProperty("bars_channel", out var bc)
        && !string.IsNullOrWhiteSpace(bc.GetString()))
      {
        barsChannel = bc.GetString()!;
      }
      if (feedEl.TryGetProperty("bar_quality_lookback", out var bl))
      {
        lookback = bl.GetInt32();
      }
      runtimes.Add(new InstrumentRuntime
      {
        InstrumentId = instrumentId,
        Feed = new FeedInstrumentOptions(
          InstrumentId: instrumentId,
          CanonicalSymbol: redisSymbol,
          CTraderSymbol: cTraderSymbol,
          RedisSymbol: redisSymbol,
          Timeframes: timeframes,
          BackfillBars: backfill,
          BarsWindowMax: barsWindow,
          BarsChannel: barsChannel,
          BarQualityLookback: lookback,
          Rollout: rollout
        ),
        Execution = new ExecutionInstrumentOptions(
          InstrumentId: instrumentId,
          CanonicalSymbol: redisSymbol,
          Rollout: rollout,
          PipSize: pip,
          ContractSize: contract,
          EffectiveSymbols: [redisSymbol],
          PipValuePerLot: pipValue
        ),
      });
    }
    if (runtimes.Count == 0)
    {
      throw new InvalidOperationException(
        "runtime manifest instrument_runtimes produced an empty registry"
      );
    }
    return new InstrumentRuntimeRegistry(runtimes);
  }

  [Obsolete("Use FromRuntimeManifestV2 or FromManifestV1Projections")]
  public static InstrumentRuntimeRegistry FromRuntimeManifest(
    ResolvedRuntimeManifest manifest,
    FeedOptions feedBootstrap,
    AutoTradeOptions tradeBootstrap
  )
  {
    if (manifest.ManifestVersion == 1)
    {
      return FromManifestV1Projections(feedBootstrap, tradeBootstrap);
    }
    return FromRuntimeManifestV2(manifest, feedBootstrap);
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

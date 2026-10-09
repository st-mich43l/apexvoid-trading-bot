using System.Globalization;

namespace ApexVoid.CTraderFeed;

/// <summary>
/// Builds the cTrader runtime directly from the resolved categorized YAML
/// document. Secrets and broker session bootstrap remain environment inputs;
/// all trading and instrument policy comes from the document.
/// </summary>
public sealed record NativeRuntimeConfiguration(
  FeedOptions Feed,
  AutoTradeOptions AutoTrade,
  InstrumentRuntimeRegistry Instruments,
  ConfigDocument Document
);

public static class NativeRuntimeFactory
{
  public static NativeRuntimeConfiguration Load(string rootPath)
  {
    var document = ConfigDocument.Resolve(rootPath);
    var account = CTraderAccountOptions.FromEnvironment(document);
    var trade = BuildAutoTrade(document, account.RedisUrl);
    var feed = BuildFeed(document, account);
    var instruments = BuildInstruments(document, feed);
    trade.Validate();
    return new NativeRuntimeConfiguration(feed, trade, instruments, document);
  }

  private static FeedOptions BuildFeed(
    ConfigDocument document,
    CTraderAccountOptions account
  )
  {
    var live = document.LiveInstruments();
    var first = live.Contains("XAU", StringComparer.OrdinalIgnoreCase)
      ? "XAU"
      : live.FirstOrDefault()
      ?? throw new ConfigurationV3Error("instruments has no live instrument");
    var instrument = document.InstrumentSection(first);
    var broker = RequiredString(instrument, "broker_symbol", first);
    var canonical = RequiredString(instrument, "canonical_symbol", first);
    var timeframes = RequiredStrings(instrument, "timeframes", first);
    return new FeedOptions(
      ClientId: account.ClientId,
      ClientSecret: account.ClientSecret,
      AccessToken: account.AccessToken,
      RefreshToken: account.RefreshToken,
      AccountId: account.AccountId,
      Host: account.Host,
      Port: account.Port,
      CTraderSymbol: broker,
      RedisSymbol: canonical,
      Timeframes: timeframes,
      BackfillBars: document.RequiredInt("runtime.feed.backfill_bars"),
      RedisUrl: account.RedisUrl,
      BarsWindowMax: document.RequiredInt("runtime.feed.bars_window_max"),
      BarsChannel: document.RequiredString("runtime.feed.bars_channel"),
      BarQualityLookback: document.RequiredInt("runtime.feed.bar_quality_lookback"),
      HeartbeatFile: account.HeartbeatFile,
      AutoTradeHeartbeatFile: account.AutoTradeHeartbeatFile,
      RefreshTokenKey: account.RefreshTokenKey,
      RefreshTokenFile: account.RefreshTokenFile,
      RequestTimeout: account.RequestTimeout,
      TokenRefreshLead: account.TokenRefreshLead,
      TokenCheckInterval: account.TokenCheckInterval,
      ExpectedBroker: document.RequiredString("runtime.broker.expected_broker")
    );
  }

  private static AutoTradeOptions BuildAutoTrade(
    ConfigDocument c,
    string redisUrl
  )
  {
    var xau = c.InstrumentSection("XAU");
    var geometry = c.GeometryFor("XAU");
    var profile = c.RequiredString("runtime.environment").Equals(
      "demo_eval", StringComparison.OrdinalIgnoreCase
    ) ? "demo_eval" : "conservative";
    return new AutoTradeOptions(
      Enabled: c.RequiredBool("auto_algo.enabled"),
      DryRun: c.RequiredBool("auto_algo.dry_run"),
      ExpectedBroker: c.RequiredString("runtime.broker.expected_broker"),
      PollMilliseconds: c.RequiredInt("execution.entry.poll_ms"),
      EventStream: c.RequiredString("runtime.redis_streams.events"),
      Label: c.RequiredString("execution.policy.label"),
      RequireDemoOnlyToken: OptionalBool(c, "execution.policy.require_demo_only_token", false),
      RiskPercent: OptionalDecimal(c, "auto_algo.risk.sizing.risk_pct", 2m),
      SizingMode: OptionalString(c, "auto_algo.risk.sizing.mode", "equity_table"),
      PipValuePerLot: OptionalDecimal(
        xau,
        "contract.pip_value_per_lot",
        (decimal)geometry.PipSize * RequiredDecimal(xau, "contract.contract_units_per_lot", "XAU")
      ),
      PipSize: RequiredDecimal(xau, "contract.pip_size", "XAU"),
      ContractSize: RequiredDecimal(xau, "contract.contract_units_per_lot", "XAU"),
      Profile: profile,
      RequireDemoAccount: c.RequiredBool("runtime.broker.require_demo"),
      RedisUrl: redisUrl,
      CanonicalSymbol: geometry.CanonicalSymbol,
      TradePlanStream: c.RequiredString("runtime.redis_streams.trade_plans"),
      Symbols: c.LiveInstruments(),
      EquityTableVersion: c.RequiredString("auto_algo.risk.sizing.equity_table_version"),
      UnfilledLegAfterTpPolicy: c.RequiredString("execution.targeting.unfilled_leg_after_tp_policy"),
      ReactionScaleInvalidPolicy: c.RequiredString("execution.reaction.scale_invalid_policy"),
      // Python refuses a spot older than analysis.spot.maximum_age_seconds when it
      // builds a plan. The executor allows three times that (and at least 15s) for
      // its own poll and sizing delay before it stops acting on the last tick.
      MaximumQuoteAgeSeconds: Math.Max(
        15,
        (int)OptionalDecimal(c, "analysis.spot.maximum_age_seconds", 5m) * 3
      )
    );
  }

  private static InstrumentRuntimeRegistry BuildInstruments(
    ConfigDocument c,
    FeedOptions sharedFeed
  )
  {
    var declared = c.Section("instruments");
    var runtimes = new List<InstrumentRuntime>();
    foreach (var symbol in declared.Keys.Order(StringComparer.Ordinal))
    {
      var section = c.InstrumentSection(symbol);
      var rollout = InstrumentRolloutGates.Parse(RequiredString(section, "rollout", symbol));
      if (rollout == InstrumentRollout.Disabled) continue;
      var geometry = c.GeometryFor(symbol);
      var aliases = OptionalStrings(section, "aliases");
      var timeframes = RequiredStrings(section, "timeframes", symbol);
      var pipValue = OptionalDecimal(
        section,
        "contract.pip_value_per_lot",
        (decimal)geometry.PipSize
          * RequiredDecimal(section, "contract.contract_units_per_lot", symbol)
      );
      runtimes.Add(new InstrumentRuntime
      {
        InstrumentId = symbol,
        Aliases = aliases,
        Feed = new FeedInstrumentOptions(
          symbol,
          geometry.CanonicalSymbol,
          geometry.BrokerSymbol,
          geometry.CanonicalSymbol,
          timeframes,
          sharedFeed.BackfillBars,
          sharedFeed.BarsWindowMax,
          sharedFeed.BarsChannel,
          sharedFeed.BarQualityLookback,
          rollout
        ),
        Execution = new ExecutionInstrumentOptions(
          symbol,
          geometry.CanonicalSymbol,
          rollout,
          (decimal)geometry.PipSize,
          RequiredDecimal(section, "contract.contract_units_per_lot", symbol),
          [geometry.CanonicalSymbol],
          pipValue,
          OppositePositionPolicy.Parse(symbol, section, (decimal)geometry.PipSize)
        )
      });
    }
    return new InstrumentRuntimeRegistry(runtimes);
  }

  private static string RequiredString(IReadOnlyDictionary<string, object?> map, string path, string symbol) =>
    Value(map, path, symbol) is string value && !string.IsNullOrWhiteSpace(value)
      ? value
      : throw new ConfigurationV3Error($"instrument {symbol}: missing or invalid {path}");

  private static IReadOnlyList<string> RequiredStrings(IReadOnlyDictionary<string, object?> map, string path, string symbol)
  {
    if (Value(map, path, symbol) is not IEnumerable<object?> items)
      throw new ConfigurationV3Error($"instrument {symbol}: missing or invalid {path}");
    var result = items.Select(item => item as string ?? "").Where(item => item.Length > 0).ToArray();
    if (result.Length == 0) throw new ConfigurationV3Error($"instrument {symbol}: {path} is empty");
    return result;
  }

  private static IReadOnlyList<string> OptionalStrings(IReadOnlyDictionary<string, object?> map, string path) =>
    TryValue(map, path, out var raw) && raw is IEnumerable<object?> items
      ? items.Select(item => item as string ?? "").Where(item => item.Length > 0).ToArray()
      : [];

  private static decimal RequiredDecimal(IReadOnlyDictionary<string, object?> map, string path, string symbol) =>
    ToDecimal(Value(map, path, symbol), $"instrument {symbol}: {path}");

  private static object? Value(IReadOnlyDictionary<string, object?> map, string path, string symbol)
  {
    object? cursor = map;
    foreach (var part in path.Split('.'))
    {
      if (cursor is not IReadOnlyDictionary<string, object?> section || !section.TryGetValue(part, out cursor))
        throw new ConfigurationV3Error($"instrument {symbol}: missing {path}");
    }
    return cursor;
  }

  private static bool OptionalBool(ConfigDocument c, string path, bool fallback) =>
    c.Get(path) is null ? fallback : c.RequiredBool(path);

  private static decimal OptionalDecimal(ConfigDocument c, string path, decimal fallback) =>
    c.Get(path) is null ? fallback : c.RequiredDecimal(path);

  private static string OptionalString(ConfigDocument c, string path, string fallback) =>
    c.Get(path) is null ? fallback : c.RequiredString(path);

  private static decimal OptionalDecimal(
    IReadOnlyDictionary<string, object?> map,
    string path,
    decimal fallback
  ) => map is null ? fallback : TryValue(map, path, out var value) ? ToDecimal(value, path) : fallback;

  private static bool TryValue(IReadOnlyDictionary<string, object?> map, string path, out object? value)
  {
    value = map;
    foreach (var part in path.Split('.'))
    {
      if (value is not IReadOnlyDictionary<string, object?> section || !section.TryGetValue(part, out value))
      {
        value = null;
        return false;
      }
    }
    return true;
  }

  private static decimal ToDecimal(object? value, string path)
  {
    if (value is null) throw new ConfigurationV3Error($"missing required value {path}");
    try { return Convert.ToDecimal(value, CultureInfo.InvariantCulture); }
    catch (Exception ex) when (ex is FormatException or InvalidCastException or OverflowException)
    { throw new ConfigurationV3Error($"invalid decimal value {path}: {value}", ex); }
  }
}

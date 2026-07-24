using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace ApexVoid.CTraderFeed;

public sealed record AutoTradeConfigHealthResult(
  string State,
  IReadOnlyList<string> Fatal,
  IReadOnlyList<string> Warnings
);

public static class AutoTradeConfigHealth
{
  public const string PythonManifestKey = "auto_trade:config_manifest:python";
  public const string CTraderManifestKey = "auto_trade:config_manifest:ctrader";
  public const string HealthKey = "auto_trade:config_health";

  public static AutoTradeConfigManifest Build(
    AutoTradeOptions options,
    TradingAccountSnapshot account,
    SymbolInfo symbol,
    long generatedAt
  )
  {
    var (fingerprint, database) = RedisIdentity(options.RedisUrl);
    var version = Assembly.GetExecutingAssembly().GetName().Version?.ToString()
      ?? "dev";
    return new AutoTradeConfigManifest(
      Service: "ctrader-engine",
      ServiceVersion: version,
      GitSha: Environment.GetEnvironmentVariable("GIT_SHA") ?? "unknown",
      Profile: options.Profile,
      AutoTradeEnabled: options.Enabled,
      DryRun: options.DryRun,
      RedisFingerprint: fingerprint,
      RedisDatabase: database,
      CandidateStream: options.CandidateStream,
      EventStream: options.EventStream,
      Symbols: [symbol.RedisSymbol],
      CanonicalSymbol: options.CanonicalSymbol,
      PipSize: options.PipSize,
      ContractSize: options.ContractSize,
      TargetPlans: options.TargetsPips,
      RangeTargetPlans: options.EffectiveRangeTargetsPips,
      RangeTpBuffer: options.RangeTpBufferPips,
      CandidateTtl: 7 * 86400,
      CandidateMaxAge: options.CandidateMaxAgeSeconds,
      SpotMaxAge: options.SpotMaxAgeSeconds,
      RangeFlip: options.RangeFlipEnabled,
      TwoSidedRange: options.RangeTwoSidedEnabled,
      ConcurrentStrategies: options.AllowConcurrentStrategies,
      HedgingPolicy: options.AllowHedgedXau,
      ZoneFill: options.ZoneFillEnabled,
      MinConfluence: options.MinConfluence,
      AccountMode: account.IsLive ? "live" : "demo",
      Broker: account.BrokerName,
      CandidateContractVersion: options.CandidateContractVersion,
      GeneratedAt: generatedAt,
      ManualAlgoEnabled: options.ManualAlgoEnabled,
      ManualAlgoDryRun: options.DryRun,
      HedgingCapability: account.AccountType.Equals(
        "Hedged",
        StringComparison.OrdinalIgnoreCase
      ),
      TrendEnabled: options.TrendEnabled,
      RangeEnabled: options.RangeEnabled,
      MappedZoneEnabled: options.MappedZoneEnabled,
      StrategyMatchEnabled: options.StrategyMatchEnabled,
      BreakoutEnabled: options.BreakoutEnabled,
      RetestEnabled: options.RetestEnabled,
      ReactionEnabled: options.ReactionEnabled,
      LiquidityReversalEnabled: options.LiquidityReversalEnabled,
      AllowCounterBias: options.AllowCounterBias
    );
  }

  public static AutoTradeConfigHealthResult Compare(
    AutoTradeConfigManifest current,
    string? pythonJson
  )
  {
    if (string.IsNullOrWhiteSpace(pythonJson))
    {
      return new("warning", [], ["python_manifest_missing"]);
    }
    JsonDocument document;
    try
    {
      document = JsonDocument.Parse(pythonJson);
    }
    catch (JsonException)
    {
      return new("warning", [], ["python_manifest_invalid"]);
    }
    using (document)
    {
      var root = document.RootElement;
      var fatal = new List<string>();
      CompareBool(root, "auto_trade_enabled", current.AutoTradeEnabled, fatal);
      CompareBool(root, "dry_run", current.DryRun, fatal);
      CompareBool(
        root, "manual_algo_enabled", current.ManualAlgoEnabled, fatal
      );
      CompareBool(
        root, "manual_algo_dry_run", current.ManualAlgoDryRun, fatal
      );
      CompareString(root, "candidate_stream", current.CandidateStream, fatal);
      CompareString(root, "event_stream", current.EventStream, fatal);
      CompareInt(root, "redis_database", current.RedisDatabase, fatal);
      CompareString(
        root, "redis_fingerprint", current.RedisFingerprint, fatal
      );
      CompareStringList(root, "symbols", current.Symbols, fatal);
      CompareString(root, "canonical_symbol", current.CanonicalSymbol, fatal);
      CompareDecimal(root, "pip_size", current.PipSize, fatal);
      CompareInt(
        root,
        "candidate_contract_version",
        current.CandidateContractVersion,
        fatal
      );
      CompareIntList(root, "target_plans", current.TargetPlans, fatal);
      CompareIntList(
        root,
        "range_target_plans",
        current.RangeTargetPlans,
        fatal
      );
      var warnings = new List<string>();
      CompareString(root, "profile", current.Profile, warnings);
      CompareBool(root, "range_flip", current.RangeFlip, warnings);
      CompareBool(
        root, "two_sided_range", current.TwoSidedRange, warnings
      );
      CompareBool(
        root,
        "concurrent_strategies",
        current.ConcurrentStrategies,
        warnings
      );
      CompareBool(root, "hedging_policy", current.HedgingPolicy, warnings);
      CompareBool(root, "zone_fill", current.ZoneFill, warnings);
      CompareBool(
        root, "hedging_capability", current.HedgingCapability, warnings
      );
      CompareBool(root, "trend_enabled", current.TrendEnabled, warnings);
      CompareBool(root, "range_enabled", current.RangeEnabled, warnings);
      CompareBool(
        root, "mapped_zone_enabled", current.MappedZoneEnabled, warnings
      );
      CompareBool(
        root, "strategy_match_enabled", current.StrategyMatchEnabled, warnings
      );
      CompareBool(
        root, "breakout_enabled", current.BreakoutEnabled, warnings
      );
      CompareBool(root, "retest_enabled", current.RetestEnabled, warnings);
      CompareBool(root, "reaction_enabled", current.ReactionEnabled, warnings);
      CompareBool(
        root,
        "liquidity_reversal_enabled",
        current.LiquidityReversalEnabled,
        warnings
      );
      CompareBool(
        root, "allow_counter_bias", current.AllowCounterBias, warnings
      );
      CompareInt(root, "min_confluence", current.MinConfluence, warnings);
      var state = fatal.Count > 0
        ? "fatal"
        : warnings.Count > 0 ? "warning" : "healthy";
      return new(state, fatal, warnings);
    }
  }

  public static string SerializeHealth(
    AutoTradeConfigHealthResult health,
    string profile,
    long checkedAt
  ) => JsonSerializer.Serialize(
    new AutoTradeConfigHealthDocument(
      health.State,
      health.Fatal,
      health.Warnings,
      profile,
      checkedAt
    ),
    RedisJsonContext.Default.AutoTradeConfigHealthDocument
  );

  private static (string Fingerprint, int Database) RedisIdentity(string url)
  {
    var uri = new Uri(url);
    var path = uri.AbsolutePath.Trim('/');
    var database = int.TryParse(path, out var parsed) ? parsed : 0;
    var endpoint = $"{uri.Scheme}://{uri.Host}:{uri.Port}/{database}";
    var hash = SHA256.HashData(Encoding.UTF8.GetBytes(endpoint));
    return (Convert.ToHexString(hash).ToLowerInvariant()[..16], database);
  }

  private static void CompareString(
    JsonElement root,
    string name,
    string expected,
    ICollection<string> differences
  )
  {
    if (
      !root.TryGetProperty(name, out var value)
      || value.GetString() != expected
    )
    {
      differences.Add(name);
    }
  }

  private static void CompareBool(
    JsonElement root,
    string name,
    bool expected,
    ICollection<string> differences
  )
  {
    if (
      !root.TryGetProperty(name, out var value)
      || value.ValueKind is not (JsonValueKind.True or JsonValueKind.False)
      || value.GetBoolean() != expected
    )
    {
      differences.Add(name);
    }
  }

  private static void CompareInt(
    JsonElement root,
    string name,
    int expected,
    ICollection<string> differences
  )
  {
    if (
      !root.TryGetProperty(name, out var value)
      || !value.TryGetInt32(out var actual)
      || actual != expected
    )
    {
      differences.Add(name);
    }
  }

  private static void CompareDecimal(
    JsonElement root,
    string name,
    decimal expected,
    ICollection<string> differences
  )
  {
    if (
      !root.TryGetProperty(name, out var value)
      || !value.TryGetDecimal(out var actual)
      || actual != expected
    )
    {
      differences.Add(name);
    }
  }

  private static void CompareIntList(
    JsonElement root,
    string name,
    IReadOnlyList<int> expected,
    ICollection<string> differences
  )
  {
    if (!root.TryGetProperty(name, out var value) || value.ValueKind != JsonValueKind.Array)
    {
      differences.Add(name);
      return;
    }
    var actual = value.EnumerateArray()
      .Select(item => item.TryGetInt32(out var parsed) ? parsed : int.MinValue)
      .ToArray();
    if (!actual.SequenceEqual(expected))
    {
      differences.Add(name);
    }
  }

  private static void CompareStringList(
    JsonElement root,
    string name,
    IReadOnlyList<string> expected,
    ICollection<string> differences
  )
  {
    if (!root.TryGetProperty(name, out var value) || value.ValueKind != JsonValueKind.Array)
    {
      differences.Add(name);
      return;
    }
    var actual = value.EnumerateArray()
      .Select(item => item.GetString() ?? "")
      .ToArray();
    if (!actual.SequenceEqual(expected, StringComparer.OrdinalIgnoreCase))
    {
      differences.Add(name);
    }
  }
}

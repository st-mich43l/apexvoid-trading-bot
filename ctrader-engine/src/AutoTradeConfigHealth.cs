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
  public const string ReadinessKey = "auto_trade:executor_readiness";

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
      ConfigManifestVersion: options.ConfigManifestVersion,
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
      Symbols: CanonicalStrings(options.EffectiveSymbols),
      CanonicalSymbol: options.CanonicalSymbol.Trim().ToUpperInvariant(),
      PipSize: options.PipSize,
      ContractSize: options.ContractSize,
      TargetPlans: CanonicalInts(options.TargetsPips),
      RangeTargetPlans: CanonicalInts(options.EffectiveRangeTargetsPips),
      RangeTpBuffer: options.RangeTpBufferPips,
      CandidateStorageTtlSeconds: options.CandidateStorageTtlSeconds,
      CandidateExecutionMaxAgeSeconds: options.CandidateMaxAgeSeconds,
      SpotMaxAgeSeconds: options.SpotMaxAgeSeconds,
      RangeFlip: options.RangeFlipEnabled,
      TwoSidedRange: options.RangeTwoSidedEnabled,
      ConcurrentStrategies: options.AllowConcurrentStrategies,
      HedgingPolicy: options.AllowHedgedXau,
      ZoneFill: options.ZoneFillEnabled,
      MinConfluence: options.MinConfluence,
      AccountMode: account.IsLive ? "live" : "demo",
      RequireDemoAccount: options.RequireDemoAccount,
      Broker: CanonicalBroker(account.BrokerName),
      CandidateContractVersion: options.CandidateContractVersion,
      GeneratedAt: generatedAt,
      ManualAlgoEnabled: options.ManualAlgoEnabled,
      ManualAlgoDryRun: options.DryRun,
      BrokerHedgingCapability: account.AccountType.Equals(
        "Hedged",
        StringComparison.OrdinalIgnoreCase
      ),
      TrendEnabled: options.TrendEnabled,
      RangeEnabled: options.RangeEnabled,
      MappedZoneEnabled: options.MappedZoneEnabled,
      MapThesisLockEnabled: options.MapThesisLockEnabled,
      StrategyMatchEnabled: options.StrategyMatchEnabled,
      BreakoutEnabled: options.BreakoutEnabled,
      RetestEnabled: options.RetestEnabled,
      ReactionEnabled: options.ReactionEnabled,
      LiquidityReversalEnabled: options.LiquidityReversalEnabled,
      AllowCounterBias: options.AllowCounterBias,
      NonHedgedOppositePolicy: options.NonHedgedOppositePolicy,
      DeprecatedVariables: options.DeprecatedVariables ?? [],
      ConfigSources: options.ConfigSources,
      BrokerReported: account.BrokerName,
      StructuralGuardMode: options.StructuralGuardMode,
      ZoneCooldownEnabled: options.ZoneCooldownEnabled,
      ZoneReconcileMode: options.ZoneReconcileMode
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
      CompareInt(
        root,
        "config_manifest_version",
        current.ConfigManifestVersion,
        fatal
      );
      CompareBool(root, "auto_trade_enabled", current.AutoTradeEnabled, fatal);
      CompareBool(root, "dry_run", current.DryRun, fatal);
      CompareString(root, "candidate_stream", current.CandidateStream, fatal);
      CompareString(root, "event_stream", current.EventStream, fatal);
      CompareInt(root, "redis_database", current.RedisDatabase, fatal);
      CompareString(
        root, "redis_fingerprint", current.RedisFingerprint, fatal
      );
      CompareStringList(root, "symbols", current.Symbols, fatal);
      CompareCanonicalSymbol(
        root, "canonical_symbol", current.CanonicalSymbol, fatal
      );
      CompareDecimal(root, "pip_size", current.PipSize, fatal);
      CompareDecimal(root, "contract_size", current.ContractSize, fatal);
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
      CompareDecimal(
        root, "range_tp_buffer", current.RangeTpBuffer, fatal
      );
      CompareInt(
        root,
        "candidate_execution_max_age_seconds",
        current.CandidateExecutionMaxAgeSeconds,
        fatal
      );
      CompareInt(
        root,
        "spot_max_age_seconds",
        current.SpotMaxAgeSeconds,
        fatal
      );
      CompareBool(
        root,
        "require_demo_account",
        current.RequireDemoAccount,
        fatal
      );
      if (
        current.Profile == "demo_eval"
        && CanonicalAccountMode(current.AccountMode) == "live"
      )
      {
        fatal.Add("demo_eval_live_account");
      }

      var warnings = new List<string>();
      CompareInt(
        root,
        "candidate_storage_ttl_seconds",
        current.CandidateStorageTtlSeconds,
        warnings
      );
      CompareString(root, "profile", current.Profile, warnings);
      CompareBool(
        root, "manual_algo_enabled", current.ManualAlgoEnabled, warnings
      );
      CompareBool(
        root, "manual_algo_dry_run", current.ManualAlgoDryRun, warnings
      );
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
      CompareBool(root, "trend_enabled", current.TrendEnabled, warnings);
      CompareBool(root, "range_enabled", current.RangeEnabled, warnings);
      CompareBool(
        root, "mapped_zone_enabled", current.MappedZoneEnabled, warnings
      );
      CompareBool(
        root,
        "map_thesis_lock_enabled",
        current.MapThesisLockEnabled,
        warnings
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
      CompareString(
        root,
        "non_hedged_opposite_policy",
        current.NonHedgedOppositePolicy,
        warnings
      );
      CompareString(
        root,
        "structural_guard_mode",
        current.StructuralGuardMode,
        warnings
      );
      CompareBool(
        root,
        "zone_cooldown_enabled",
        current.ZoneCooldownEnabled,
        warnings
      );
      CompareString(
        root,
        "zone_reconcile_mode",
        current.ZoneReconcileMode,
        warnings
      );
      if (!current.BrokerHedgingCapability)
      {
        warnings.Add("broker_non_hedged");
      }
      if (
        !string.IsNullOrWhiteSpace(current.BrokerReported)
        && NormalizeIdentity(current.BrokerReported)
          != CanonicalBroker(current.BrokerReported)
      )
      {
        warnings.Add("broker_alias_normalized");
      }
      if (
        TryString(root, "broker", out var pythonBroker)
        && CanonicalBroker(pythonBroker) != CanonicalBroker(current.Broker)
      )
      {
        warnings.Add("broker");
      }
      AddDeprecatedWarnings(
        root,
        current.DeprecatedVariables ?? [],
        warnings
      );
      if (current.GitSha is "" or "unknown")
      {
        warnings.Add("ctrader_git_sha_unknown");
      }
      if (
        !TryString(root, "git_sha", out var pythonGitSha)
        || pythonGitSha is "" or "unknown"
      )
      {
        warnings.Add("python_git_sha_unknown");
      }
      return new(
        fatal.Count > 0 ? "fatal" : "healthy",
        fatal.Distinct(StringComparer.Ordinal).Order().ToArray(),
        warnings.Distinct(StringComparer.Ordinal).Order().ToArray()
      );
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

  private static IReadOnlyList<int> CanonicalInts(
    IEnumerable<int> values
  ) => values.Distinct().Order().ToArray();

  private static IReadOnlyList<string> CanonicalStrings(
    IEnumerable<string> values
  ) => values
    .Select(value => value.Trim().ToUpperInvariant())
    .Where(value => value.Length > 0)
    .Distinct(StringComparer.Ordinal)
    .Order(StringComparer.Ordinal)
    .ToArray();

  private static string CanonicalBroker(string value)
  {
    var compact = NormalizeIdentity(value);
    return compact is "fpmarkets" or "fpmarketssc"
      ? "fpmarkets"
      : compact;
  }

  private static string NormalizeIdentity(string value) => string.Concat(
    value.Trim().ToLowerInvariant().Where(char.IsLetterOrDigit)
  );

  private static string CanonicalAccountMode(string value)
  {
    var normalized = value.Trim().ToLowerInvariant().Replace('_', '-');
    return normalized switch
    {
      "demo" or "demo-only" or "demo-required" => "demo",
      "live" or "live-only" or "live-required" => "live",
      _ => normalized,
    };
  }

  private static (string Fingerprint, int Database) RedisIdentity(string url)
  {
    var uri = new Uri(url);
    var path = uri.AbsolutePath.Trim('/');
    var database = int.TryParse(path, out var parsed) ? parsed : 0;
    var endpoint = $"{uri.Scheme}://{uri.Host}:{uri.Port}/{database}";
    var hash = SHA256.HashData(Encoding.UTF8.GetBytes(endpoint));
    return (Convert.ToHexString(hash).ToLowerInvariant()[..16], database);
  }

  private static bool TryString(
    JsonElement root,
    string name,
    out string value
  )
  {
    value = "";
    if (
      !root.TryGetProperty(name, out var property)
      || property.ValueKind != JsonValueKind.String
    )
    {
      return false;
    }
    value = property.GetString() ?? "";
    return true;
  }

  private static void CompareString(
    JsonElement root,
    string name,
    string expected,
    ICollection<string> differences
  )
  {
    if (!TryString(root, name, out var actual) || actual != expected)
    {
      differences.Add(name);
    }
  }

  private static void CompareCanonicalSymbol(
    JsonElement root,
    string name,
    string expected,
    ICollection<string> differences
  )
  {
    if (
      !TryString(root, name, out var actual)
      || actual.Trim().ToUpperInvariant()
        != expected.Trim().ToUpperInvariant()
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
      || !value.TryGetDecimal(out var actual)
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
    if (
      !root.TryGetProperty(name, out var value)
      || value.ValueKind != JsonValueKind.Array
    )
    {
      differences.Add(name);
      return;
    }
    var actual = new List<int>();
    foreach (var item in value.EnumerateArray())
    {
      if (
        !item.TryGetDecimal(out var parsed)
        || parsed != decimal.Truncate(parsed)
        || parsed < int.MinValue
        || parsed > int.MaxValue
      )
      {
        differences.Add(name);
        return;
      }
      actual.Add(decimal.ToInt32(parsed));
    }
    if (!CanonicalInts(actual).SequenceEqual(CanonicalInts(expected)))
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
    if (
      !root.TryGetProperty(name, out var value)
      || value.ValueKind != JsonValueKind.Array
    )
    {
      differences.Add(name);
      return;
    }
    var actual = value.EnumerateArray()
      .Select(item => item.GetString() ?? "")
      .ToArray();
    if (!CanonicalStrings(actual).SequenceEqual(CanonicalStrings(expected)))
    {
      differences.Add(name);
    }
  }

  private static void AddDeprecatedWarnings(
    JsonElement root,
    IReadOnlyList<string> current,
    ICollection<string> warnings
  )
  {
    foreach (var variable in current)
    {
      warnings.Add($"deprecated_variable:{variable}");
    }
    if (
      !root.TryGetProperty("deprecated_variables", out var value)
      || value.ValueKind != JsonValueKind.Array
    )
    {
      return;
    }
    foreach (var item in value.EnumerateArray())
    {
      var variable = item.GetString();
      if (!string.IsNullOrWhiteSpace(variable))
      {
        warnings.Add($"deprecated_variable:{variable}");
      }
    }
  }
}

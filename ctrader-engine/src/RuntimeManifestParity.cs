using System.Collections;
using System.Globalization;
using System.Reflection;
using System.Text;

namespace ApexVoid.CTraderFeed;

public enum RuntimeManifestMismatchSeverity
{
  Fatal,
  Warning,
  Informational,
}

public sealed record RuntimeManifestMismatch(
  string PropertyPath,
  string? EnvironmentName,
  string ManifestPath,
  string EnvironmentValue,
  string ManifestValue,
  RuntimeManifestMismatchSeverity Severity
);

public sealed record RuntimeManifestParityReport(
  IReadOnlyList<RuntimeManifestMismatch> Mismatches
)
{
  public bool HasFatal => Mismatches.Any(
    item => item.Severity == RuntimeManifestMismatchSeverity.Fatal
  );

  public IReadOnlyList<RuntimeManifestMismatch> Fatals => Mismatches
    .Where(item => item.Severity == RuntimeManifestMismatchSeverity.Fatal)
    .ToArray();
}

public static class RuntimeManifestParity
{
  private static readonly HashSet<string> FeedBootstrapOrSecret = new(
    StringComparer.Ordinal
  )
  {
    "ClientId",
    "ClientSecret",
    "AccessToken",
    "RefreshToken",
    "AccountId",
    "Host",
    "Port",
    "RedisUrl",
    "HeartbeatFile",
    "RefreshTokenKey",
    "RefreshTokenFile",
    "RequestTimeout",
    "TokenRefreshLead",
    "TokenCheckInterval",
  };

  private static readonly HashSet<string> AutoTradeSkip = new(StringComparer.Ordinal)
  {
    "RedisUrl",
    "ConfigSources",
    "DeprecatedVariables",
  };

  public static RuntimeManifestParityReport Compare(
    FeedOptions environmentFeed,
    AutoTradeOptions environmentTrade,
    FeedOptions manifestFeed,
    AutoTradeOptions manifestTrade
  )
  {
    var mismatches = new List<RuntimeManifestMismatch>();
    CompareRecords(
      environmentFeed,
      manifestFeed,
      "FeedOptions",
      "feed",
      FeedBootstrapOrSecret,
      mismatches
    );
    CompareRecords(
      environmentTrade,
      manifestTrade,
      "AutoTradeOptions",
      "auto_trade",
      AutoTradeSkip,
      mismatches
    );
    return new RuntimeManifestParityReport(mismatches);
  }

  public static void ApplyParityMode(
    RuntimeManifestParityReport report,
    CtraderManifestParityMode mode,
    Action<string> log
  )
  {
    if (mode == CtraderManifestParityMode.Off)
    {
      return;
    }
    foreach (var mismatch in report.Mismatches)
    {
      log(
        $"runtime_manifest_parity_mismatch path={mismatch.PropertyPath} "
        + $"env={mismatch.EnvironmentName ?? "-"} "
        + $"manifest_path={mismatch.ManifestPath} "
        + $"env_value={mismatch.EnvironmentValue} "
        + $"manifest_value={mismatch.ManifestValue} "
        + $"severity={mismatch.Severity.ToString().ToLowerInvariant()}"
      );
    }
    if (mode == CtraderManifestParityMode.Enforce && report.HasFatal)
    {
      throw new InvalidOperationException(
        $"runtime manifest parity enforce failed with {report.Fatals.Count} fatal mismatch(es)"
      );
    }
  }

  private static void CompareRecords(
    object environment,
    object manifest,
    string typeName,
    string manifestRoot,
    HashSet<string> skip,
    List<RuntimeManifestMismatch> mismatches
  )
  {
    foreach (var property in environment.GetType().GetProperties(
      BindingFlags.Instance | BindingFlags.Public
    ))
    {
      if (skip.Contains(property.Name))
      {
        continue;
      }
      if (property.GetIndexParameters().Length > 0)
      {
        continue;
      }
      // Skip computed helpers on AutoTradeOptions.
      if (property.Name is "EffectiveRangeTargetsPips" or "EffectiveSymbols" or "ExposurePolicy")
      {
        continue;
      }
      var left = property.GetValue(environment);
      var right = property.GetValue(manifest);
      var leftText = Normalize(left);
      var rightText = Normalize(right);
      if (leftText == rightText)
      {
        continue;
      }
      mismatches.Add(new RuntimeManifestMismatch(
        PropertyPath: $"{typeName}.{property.Name}",
        EnvironmentName: null,
        ManifestPath: $"{manifestRoot}.{ToSnake(property.Name)}",
        EnvironmentValue: leftText,
        ManifestValue: rightText,
        Severity: RuntimeManifestMismatchSeverity.Fatal
      ));
    }
  }

  private static string Normalize(object? value)
  {
    if (value is null)
    {
      return "<null>";
    }
    if (value is string text)
    {
      return text;
    }
    if (value is bool flag)
    {
      return flag ? "true" : "false";
    }
    if (value is decimal dec)
    {
      return dec.ToString(CultureInfo.InvariantCulture);
    }
    if (value is IEnumerable enumerable and not string)
    {
      var parts = new List<string>();
      foreach (var item in enumerable)
      {
        parts.Add(Normalize(item));
      }
      return "[" + string.Join(",", parts) + "]";
    }
    if (value is IFormattable formattable)
    {
      return formattable.ToString(null, CultureInfo.InvariantCulture) ?? "";
    }
    return value.ToString() ?? "";
  }

  private static string ToSnake(string propertyName)
  {
    if (propertyName == "CTraderSymbol")
    {
      return "ctrader_symbol";
    }
    var chars = new StringBuilder();
    for (var index = 0; index < propertyName.Length; index++)
    {
      var ch = propertyName[index];
      if (char.IsUpper(ch) && index > 0)
      {
        chars.Append('_');
      }
      chars.Append(char.ToLowerInvariant(ch));
    }
    return chars.ToString();
  }
}

using System.Text.Json;

namespace ApexVoid.CTraderFeed;

public static class ResolvedRuntimeManifestLoader
{
  public static ResolvedRuntimeManifest Load(string path)
  {
    if (string.IsNullOrWhiteSpace(path))
    {
      throw new InvalidOperationException("runtime manifest path is empty");
    }
    if (!File.Exists(path))
    {
      throw new InvalidOperationException($"runtime manifest file missing: {path}");
    }
    string json;
    try
    {
      json = File.ReadAllText(path);
    }
    catch (Exception ex)
    {
      throw new InvalidOperationException(
        $"runtime manifest unreadable: {path}",
        ex
      );
    }
    ResolvedRuntimeManifest? manifest;
    try
    {
      // The reflection-based Deserialize<T>(json, options) overload throws
      // at runtime in this AOT-published app ("Reflection-based
      // serialization has been disabled") - confirmed live, crashed on
      // every single startup attempt. Must go through the source-generated
      // context like every other JSON type in this codebase already does.
      // RedisJsonContext's PropertyNamingPolicy/DefaultIgnoreCondition
      // don't affect this: every property here already declares its own
      // explicit JsonPropertyName, which always wins: over any policy, and
      // ReadCommentHandling/AllowTrailingCommas/PropertyNameCaseInsensitive
      // are already JsonSerializerOptions' own defaults, so parsing
      // behavior is unchanged - only the AOT-safety mechanism is.
      manifest = JsonSerializer.Deserialize(
        json, RedisJsonContext.Default.ResolvedRuntimeManifest
      );
    }
    catch (JsonException ex)
    {
      throw new InvalidOperationException(
        $"runtime manifest JSON malformed: {path}",
        ex
      );
    }
    if (manifest is null)
    {
      throw new InvalidOperationException("runtime manifest deserialized to null");
    }
    if (manifest.ManifestVersion is not (1 or 2))
    {
      throw new InvalidOperationException(
        $"unsupported runtime manifest_version {manifest.ManifestVersion}; expected 1 or 2"
      );
    }
    if (!manifest.Instruments.ContainsKey("XAU"))
    {
      throw new InvalidOperationException("runtime manifest missing instruments.XAU");
    }
    if (!manifest.LiveInstruments.Contains("XAU", StringComparer.Ordinal))
    {
      throw new InvalidOperationException(
        "runtime manifest live_instruments does not include XAU"
      );
    }
    var xau = manifest.Instruments["XAU"];
    if (
      !xau.TryGetProperty("identity", out var identity)
      || !identity.TryGetProperty("rollout", out var rollout)
      || rollout.GetString() != "live"
    )
    {
      throw new InvalidOperationException(
        "runtime manifest requires instruments.XAU.identity.rollout=live"
      );
    }
    if (
      !xau.TryGetProperty("units", out var units)
      || !units.TryGetProperty("pip_size", out var pip)
      || ManifestDecimal.Parse(pip.GetString() ?? "", "instruments.XAU.units.pip_size") <= 0m
    )
    {
      throw new InvalidOperationException("runtime manifest XAU pip_size must be positive");
    }
    if (
      !units.TryGetProperty("contract_units_per_lot", out var contract)
      || ManifestDecimal.Parse(
        contract.GetString() ?? "",
        "instruments.XAU.units.contract_units_per_lot"
      ) <= 0m
    )
    {
      throw new InvalidOperationException(
        "runtime manifest XAU contract_units_per_lot must be positive"
      );
    }
    if (
      !units.TryGetProperty("price_digits", out var digits)
      || digits.GetInt32() < 0
    )
    {
      throw new InvalidOperationException("runtime manifest XAU price_digits invalid");
    }
    return manifest;
  }
}

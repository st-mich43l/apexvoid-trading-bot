using System.Text.Json;
using System.Text.Json.Serialization;

namespace ApexVoid.CTraderFeed;

public static class ResolvedRuntimeManifestLoader
{
  private static readonly JsonSerializerOptions JsonOptions = new()
  {
    PropertyNameCaseInsensitive = false,
    ReadCommentHandling = JsonCommentHandling.Disallow,
    AllowTrailingCommas = false,
  };

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
      manifest = JsonSerializer.Deserialize<ResolvedRuntimeManifest>(json, JsonOptions);
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
    if (manifest.ManifestVersion != 1)
    {
      throw new InvalidOperationException(
        $"unsupported runtime manifest_version {manifest.ManifestVersion}; expected 1"
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

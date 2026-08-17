namespace ApexVoid.CTraderFeed;

/// <summary>
/// Typed runtime configuration constructed exclusively from a resolved
/// runtime manifest plus account/bootstrap options. No trading ENV reads.
/// </summary>
public sealed record ManifestRuntimeConfiguration(
  CTraderAccountOptions Account,
  FeedOptions Feed,
  AutoTradeOptions AutoTrade,
  InstrumentRuntimeRegistry Instruments,
  int ManifestVersion,
  string EffectiveFingerprint,
  string CompatibilityMode,
  bool ManifestValidationEnforced
);

public static class ManifestRuntimeFactory
{
  public static ManifestRuntimeConfiguration Create(
    ResolvedRuntimeManifest manifest,
    CTraderAccountOptions account
  )
  {
    ArgumentNullException.ThrowIfNull(manifest);
    ArgumentNullException.ThrowIfNull(account);
    if (manifest.ManifestVersion is not (1 or 2))
    {
      throw new InvalidOperationException(
        $"unsupported runtime manifest_version {manifest.ManifestVersion}; expected 1 or 2"
      );
    }

    ValidateProductionXau(manifest);

    var feed = FeedOptions.FromRuntimeManifest(manifest, account);
    var trade = AutoTradeOptions.FromRuntimeManifest(manifest, account);
    trade.Validate();

    string compatibility;
    InstrumentRuntimeRegistry registry;
    if (manifest.ManifestVersion == 1)
    {
      compatibility = "v1_xau";
      registry = InstrumentRuntimeRegistry.FromManifestV1Projections(feed, trade);
      Console.WriteLine("manifest_compatibility=v1_xau");
    }
    else
    {
      compatibility = "v2";
      registry = InstrumentRuntimeRegistry.FromRuntimeManifestV2(manifest, feed);
    }

    if (registry.LiveInstruments().All(item => item.InstrumentId != "XAU")
      || registry.LiveInstruments().Count == 0)
    {
      throw new InvalidOperationException(
        "manifest live instrument set must include XAU"
      );
    }

    return new ManifestRuntimeConfiguration(
      Account: account,
      Feed: feed,
      AutoTrade: trade,
      Instruments: registry,
      ManifestVersion: manifest.ManifestVersion,
      EffectiveFingerprint: manifest.EffectiveConfigurationFingerprint,
      CompatibilityMode: compatibility,
      ManifestValidationEnforced: true
    );
  }

  private static void ValidateProductionXau(ResolvedRuntimeManifest manifest)
  {
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
      throw new InvalidOperationException(
        "runtime manifest instruments.XAU.units.pip_size must be positive"
      );
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
        "runtime manifest instruments.XAU.units.contract_units_per_lot must be positive"
      );
    }
  }
}

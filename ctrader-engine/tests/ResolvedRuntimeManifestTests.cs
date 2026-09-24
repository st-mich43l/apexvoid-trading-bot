using System.Text.Json;
using Xunit;

namespace CTraderFeed.Tests;

[Collection(nameof(EnvMutationCollection))]
public sealed class ResolvedRuntimeManifestTests : IDisposable
{
  private readonly Dictionary<string, string?> _environment = new(StringComparer.Ordinal);

  public ResolvedRuntimeManifestTests()
  {
    foreach (System.Collections.DictionaryEntry entry in Environment.GetEnvironmentVariables())
    {
      var key = entry.Key?.ToString();
      if (key is not null && (key.StartsWith("AUTO_TRADE_", StringComparison.Ordinal)
        || key.StartsWith("CTRADER_", StringComparison.Ordinal)))
      {
        _environment[key] = Environment.GetEnvironmentVariable(key);
      }
    }
  }

  public void Dispose()
  {
    foreach (System.Collections.DictionaryEntry entry in Environment.GetEnvironmentVariables())
    {
      var key = entry.Key?.ToString();
      if (key is not null && (key.StartsWith("AUTO_TRADE_", StringComparison.Ordinal)
        || key.StartsWith("CTRADER_", StringComparison.Ordinal)) && !_environment.ContainsKey(key))
      {
        Environment.SetEnvironmentVariable(key, null);
      }
    }
    foreach (var (key, value) in _environment)
    {
      Environment.SetEnvironmentVariable(key, value);
    }
  }

  private static string FixturePath()
  {
    var root = Path.GetFullPath(
      Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..")
    );
    return Path.Combine(
      root,
      "contracts",
      "configuration",
      "runtime-manifest-example.generated.json"
    );
  }

  [Fact]
  public void LoadsPythonGeneratedFixture()
  {
    var path = FixturePath();
    Assert.True(File.Exists(path), $"missing fixture {path}");
    var manifest = ResolvedRuntimeManifestLoader.Load(path);
    Assert.Equal(2, manifest.ManifestVersion);
    Assert.Contains("XAU", manifest.LiveInstruments);
    Assert.Equal("XAUUSD", manifest.Feed.CTraderSymbol);
    Assert.Equal("XAU", manifest.Feed.RedisSymbol);
    Assert.Equal(5, manifest.AutoTrade.TargetsPips.Count);
    Assert.Equal(0.1m, ManifestDecimal.Parse(manifest.AutoTrade.PipSize, "pip"));
    Assert.NotNull(manifest.InstrumentRuntimes);
    Assert.True(manifest.InstrumentRuntimes!.ContainsKey("XAU"));
    var registry = InstrumentRuntimeRegistry.FromRuntimeManifestV2(
      manifest,
      SharedFeed(manifest)
    );
    Assert.Contains("XAUUSD", registry.Get("XAU").Aliases);
  }

  private static FeedOptions SharedFeed(ResolvedRuntimeManifest manifest) => new(
    ClientId: "id",
    ClientSecret: "secret",
    AccessToken: "access",
    RefreshToken: "refresh",
    AccountId: 1,
    Host: "demo.ctraderapi.com",
    Port: 5035,
    CTraderSymbol: manifest.Feed.CTraderSymbol,
    RedisSymbol: manifest.Feed.RedisSymbol,
    Timeframes: manifest.Feed.Timeframes,
    BackfillBars: manifest.Feed.BackfillBars,
    RedisUrl: "redis://localhost:6379/0",
    BarsWindowMax: manifest.Feed.BarsWindowMax,
    BarsChannel: manifest.Feed.BarsChannel,
    BarQualityLookback: manifest.Feed.BarQualityLookback,
    HeartbeatFile: "/tmp/ctrader-feed-heartbeat",
    RefreshTokenKey: "ctrader:refresh_token",
    RefreshTokenFile: "/tmp/ctrader-token.json",
    RequestTimeout: TimeSpan.FromSeconds(30),
    TokenRefreshLead: TimeSpan.FromDays(5),
    TokenCheckInterval: TimeSpan.FromHours(6),
    ExpectedBroker: manifest.Feed.ExpectedBroker
  );

  [Fact]
  public void MissingFileFailsClosed()
  {
    Assert.Throws<InvalidOperationException>(() =>
      ResolvedRuntimeManifestLoader.Load("/tmp/does-not-exist-apexvoid-manifest.json")
    );
  }

  [Fact]
  public void UnsupportedVersionFailsClosed()
  {
    var path = Path.GetTempFileName();
    try
    {
      var json = File.ReadAllText(FixturePath());
      using var doc = JsonDocument.Parse(json);
      using var stream = new MemoryStream();
      using (var writer = new Utf8JsonWriter(stream))
      {
        writer.WriteStartObject();
        writer.WriteNumber("manifest_version", 99);
        writer.WriteString(
          "contract_fingerprint",
          doc.RootElement.GetProperty("contract_fingerprint").GetString()
        );
        writer.WriteString(
          "effective_configuration_fingerprint",
          doc.RootElement.GetProperty("effective_configuration_fingerprint").GetString()
        );
        writer.WriteString("profile", "conservative");
        writer.WritePropertyName("global");
        doc.RootElement.GetProperty("global").WriteTo(writer);
        writer.WritePropertyName("instruments");
        doc.RootElement.GetProperty("instruments").WriteTo(writer);
        writer.WritePropertyName("feed");
        doc.RootElement.GetProperty("feed").WriteTo(writer);
        writer.WritePropertyName("auto_trade");
        doc.RootElement.GetProperty("auto_trade").WriteTo(writer);
        writer.WritePropertyName("live_instruments");
        doc.RootElement.GetProperty("live_instruments").WriteTo(writer);
        writer.WriteEndObject();
      }
      File.WriteAllBytes(path, stream.ToArray());
      Assert.Throws<InvalidOperationException>(() =>
        ResolvedRuntimeManifestLoader.Load(path)
      );
    }
    finally
    {
      File.Delete(path);
    }
  }

  [Fact]
  public void ManifestDerivedOptionsMatchEnvironmentWhenEnvAligned()
  {
    var path = FixturePath();
    Assert.True(File.Exists(path), $"missing fixture {path}");
    var manifest = ResolvedRuntimeManifestLoader.Load(path);
    // These legacy aliases are deliberately conflict-checked in production.
    // Clear them only inside this isolated aligned-fixture test.
    Environment.SetEnvironmentVariable("AUTO_TRADE_STRATEGY_BRIDGE_ENABLED", null);
    Environment.SetEnvironmentVariable("AUTO_TRADE_FORMING_GATE_ENABLED", null);
    Environment.SetEnvironmentVariable("CTRADER_CLIENT_ID", "id");
    Environment.SetEnvironmentVariable("CTRADER_CLIENT_SECRET", "secret");
    Environment.SetEnvironmentVariable("CTRADER_ACCESS_TOKEN", "access");
    Environment.SetEnvironmentVariable("CTRADER_REFRESH_TOKEN", "refresh");
    Environment.SetEnvironmentVariable("CTRADER_ACCOUNT_ID", "1");
    Environment.SetEnvironmentVariable("CTRADER_SYMBOL", manifest.Feed.CTraderSymbol);
    Environment.SetEnvironmentVariable(
      "CTRADER_TIMEFRAMES",
      string.Join(",", manifest.Feed.Timeframes)
    );
    Environment.SetEnvironmentVariable(
      "AUTO_TRADE_MAX_SPREAD_PIPS",
      manifest.AutoTrade.MaxSpreadPips.ToString()
    );
    Environment.SetEnvironmentVariable(
      "AUTO_TRADE_XAU_PIP_SIZE",
      manifest.AutoTrade.PipSize
    );
    Environment.SetEnvironmentVariable(
      "AUTO_TRADE_XAU_CONTRACT_SIZE",
      manifest.AutoTrade.ContractSize
    );
    Environment.SetEnvironmentVariable(
      "AUTO_TRADE_TARGET_PLANS_PIPS",
      string.Join(",", manifest.AutoTrade.TargetsPips)
    );
    Environment.SetEnvironmentVariable(
      "AUTO_TRADE_TP_WEIGHTS",
      string.Join(",", manifest.AutoTrade.TargetWeights)
    );
    Environment.SetEnvironmentVariable("AUTO_TRADE_PROFILE", manifest.Profile);
    var envFeed = FeedOptions.FromEnvironment();
    var envTrade = AutoTradeOptions.FromEnvironment();
    var manFeed = FeedOptions.FromRuntimeManifest(manifest, envFeed);
    var manTrade = AutoTradeOptions.FromRuntimeManifest(manifest, envTrade);
    Assert.Equal(envFeed.CTraderSymbol, manFeed.CTraderSymbol);
    Assert.Equal(envFeed.RedisSymbol, manFeed.RedisSymbol);
    Assert.Equal(envTrade.PipSize, manTrade.PipSize);
    Assert.Equal(envTrade.ContractSize, manTrade.ContractSize);
    Assert.Equal(envTrade.TargetsPips, manTrade.TargetsPips);
    Assert.Equal(envTrade.TargetWeights, manTrade.TargetWeights);
    Assert.Equal(envTrade.MaxSpreadPips, manTrade.MaxSpreadPips);
  }
}

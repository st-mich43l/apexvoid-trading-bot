using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using ApexVoid.CTraderFeed;
using OpenAPI.Net;

namespace CTraderFeed.Tests;

public sealed class RefreshTokenStateTests
{
  [Fact]
  public async Task MatchingSeedUsesCachedCurrent()
  {
    var store = new FakeRefreshTokenStore
    {
      Token = Document("env-refresh", "cached-current"),
    };
    var state = new RefreshTokenState(Options(), store);

    await state.SeedAsync(CancellationToken.None);

    Assert.Equal("cached-current", state.RefreshToken);
    Assert.Equal(0, store.Writes);
  }

  [Fact]
  public async Task ChangedEnvironmentTokenDiscardsCacheAndPersistsNewSeed()
  {
    var logs = new List<string>();
    var store = new FakeRefreshTokenStore
    {
      Token = Document("old-env-refresh", "old-current-refresh"),
    };
    var state = new RefreshTokenState(Options(), store, logs.Add);

    await state.SeedAsync(CancellationToken.None);

    Assert.Equal("env-refresh", state.RefreshToken);
    Assert.Equal(1, store.Writes);
    AssertDocument(store.Token, "env-refresh", "env-refresh");
    var message = Assert.Single(logs);
    Assert.Contains(Fingerprint("old-env-refresh")[..8], message);
    Assert.Contains(Fingerprint("env-refresh")[..8], message);
    AssertNoTokenValues(logs);
  }

  [Fact]
  public async Task LegacyBareTokenIsDiscardedAndHealed()
  {
    var logs = new List<string>();
    var store = new FakeRefreshTokenStore { Token = "legacy-refresh" };
    var state = new RefreshTokenState(Options(), store, logs.Add);

    await state.SeedAsync(CancellationToken.None);

    Assert.Equal("env-refresh", state.RefreshToken);
    AssertDocument(store.Token, "env-refresh", "env-refresh");
    Assert.Single(logs);
    AssertNoTokenValues(logs);
  }

  [Fact]
  public async Task MissingCacheUsesEnvironmentAndPersistsDocument()
  {
    var logs = new List<string>();
    var store = new FakeRefreshTokenStore();
    var state = new RefreshTokenState(Options(), store, logs.Add);

    await state.SeedAsync(CancellationToken.None);

    Assert.Equal("env-refresh", state.RefreshToken);
    AssertDocument(store.Token, "env-refresh", "env-refresh");
    Assert.Empty(logs);
  }

  [Fact]
  public async Task MalformedDocumentNeverLogsStoredValues()
  {
    var logs = new List<string>();
    var store = new FakeRefreshTokenStore
    {
      Token = "{\"seed\":\"plain-secret-seed\",\"current\":\"plain-secret-current\"}",
    };
    var state = new RefreshTokenState(Options(), store, logs.Add);

    await state.SeedAsync(CancellationToken.None);

    var output = Assert.Single(logs);
    Assert.DoesNotContain("plain-secret-seed", output);
    Assert.DoesNotContain("plain-secret-current", output);
    AssertDocument(store.Token, "env-refresh", "env-refresh");
  }

  [Fact]
  public async Task RotationKeepsSeedAndLaterSeedUsesRotatedCurrent()
  {
    var store = new FakeRefreshTokenStore();
    var state = new RefreshTokenState(Options(), store);
    await state.SeedAsync(CancellationToken.None);
    var firstExpiry = new TokenExpiryResolution(
      DateTimeOffset.Parse("2026-08-20T00:00:00Z"),
      "seconds-remaining",
      false
    );

    await state.ApplyAsync(
      new ProtoOARefreshTokenRes
      {
        AccessToken = "rotated-access",
        RefreshToken = "rotated-refresh",
      },
      firstExpiry,
      CancellationToken.None
    );

    Assert.Equal("rotated-access", state.AccessToken);
    Assert.Equal("rotated-refresh", state.RefreshToken);
    AssertDocument(store.Token, "env-refresh", "rotated-refresh");
    Assert.Equal(2, store.Writes);

    var secondExpiry = new TokenExpiryResolution(
      DateTimeOffset.Parse("2026-09-20T00:00:00Z"),
      "absolute-ms",
      false
    );
    await state.ApplyAsync(
      new ProtoOARefreshTokenRes
      {
        AccessToken = "second-access",
        RefreshToken = "rotated-refresh",
      },
      secondExpiry,
      CancellationToken.None
    );
    Assert.Equal(3, store.Writes);

    var restarted = new RefreshTokenState(Options(), store);
    await restarted.SeedAsync(CancellationToken.None);

    Assert.Equal("rotated-refresh", restarted.RefreshToken);
    Assert.Equal("second-access", restarted.AccessToken);
    Assert.Equal(secondExpiry.ExpiresAt, restarted.ExpiresAt);
    Assert.Equal(3, store.Writes);
  }

  private static void AssertNoTokenValues(IEnumerable<string> logs)
  {
    var output = string.Join('\n', logs);
    Assert.DoesNotContain("env-refresh", output);
    Assert.DoesNotContain("old-env-refresh", output);
    Assert.DoesNotContain("old-current-refresh", output);
    Assert.DoesNotContain("legacy-refresh", output);
  }

  private static void AssertDocument(
    string? json,
    string seedToken,
    string current
  )
  {
    using var document = JsonDocument.Parse(Assert.IsType<string>(json));
    Assert.Equal(
      Fingerprint(seedToken),
      document.RootElement.GetProperty("seed").GetString()
    );
    Assert.Equal(current, document.RootElement.GetProperty("current").GetString());
  }

  private static string Document(string seedToken, string current) =>
    JsonSerializer.Serialize(new
    {
      seed = Fingerprint(seedToken),
      current,
    });

  private static string Fingerprint(string value) =>
    Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)))
      .ToLowerInvariant();

  private static FeedOptions Options() =>
    new(
      ClientId: "client",
      ClientSecret: "secret",
      AccessToken: "env-access",
      RefreshToken: "env-refresh",
      AccountId: 123,
      Host: "demo.ctraderapi.com",
      Port: 5035,
      CTraderSymbol: "XAUUSD",
      RedisSymbol: "XAU",
      Timeframes: ["M5"],
      BackfillBars: 1500,
      RedisUrl: "redis://redis:6379/0",
      BarsWindowMax: 1500,
      BarsChannel: "bars:new",
      BarQualityLookback: 6,
      HeartbeatFile: "/tmp/ctrader-feed.heartbeat",
      RefreshTokenKey: "ctrader:refresh_token",
      RefreshTokenFile: "/tmp/ctrader-token.json",
      RequestTimeout: TimeSpan.FromSeconds(1),
      TokenRefreshLead: TimeSpan.FromDays(5),
      TokenCheckInterval: TimeSpan.FromHours(6)
    );
}

internal sealed class FakeRefreshTokenStore : IRefreshTokenStore
{
  public string? Token { get; set; }
  public int Writes { get; private set; }

  public Task<string?> GetAsync(CancellationToken cancellationToken) =>
    Task.FromResult(Token);

  public Task SetAsync(string token, CancellationToken cancellationToken)
  {
    Token = token;
    Writes++;
    return Task.CompletedTask;
  }

  public Task DeleteAsync(CancellationToken cancellationToken)
  {
    Token = null;
    return Task.CompletedTask;
  }
}

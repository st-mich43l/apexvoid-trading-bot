using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using ApexVoid.CTraderFeed;
using OpenAPI.Net;

namespace CTraderFeed.Tests;

public sealed class TokenLifecycleTests
{
  private static readonly DateTimeOffset Now = DateTimeOffset.Parse(
    "2026-07-22T12:00:00Z"
  );

  [Fact]
  public void ResolveExpiryAcceptsSecondsRemaining()
  {
    var result = TokenExpiry.ResolveExpiry(2_628_000, Now);

    Assert.Equal("seconds-remaining", result.Interpretation);
    Assert.False(result.Warning);
    Assert.Equal(Now.AddSeconds(2_628_000), result.ExpiresAt);
  }

  [Fact]
  public void ResolveExpiryAcceptsAbsoluteMilliseconds()
  {
    var expected = Now.AddDays(30);

    var result = TokenExpiry.ResolveExpiry(expected.ToUnixTimeMilliseconds(), Now);

    Assert.Equal("absolute-ms", result.Interpretation);
    Assert.False(result.Warning);
    Assert.Equal(expected, result.ExpiresAt);
  }

  [Theory]
  [InlineData(5)]
  [InlineData(long.MaxValue)]
  public void ResolveExpiryFallsBackForAbsurdInput(long raw)
  {
    var result = TokenExpiry.ResolveExpiry(raw, Now);

    Assert.Equal("fallback-30d", result.Interpretation);
    Assert.True(result.Warning);
    Assert.Equal(Now.AddDays(30), result.ExpiresAt);
  }

  [Fact]
  public void ProactiveRefreshHonorsLeadBoundary()
  {
    var lead = TimeSpan.FromDays(5);

    Assert.False(TokenRefreshPolicy.ShouldRefresh(Now.AddDays(6), Now, lead));
    Assert.True(TokenRefreshPolicy.ShouldRefresh(Now.AddDays(4), Now, lead));
    Assert.True(TokenRefreshPolicy.ShouldRefresh(null, Now, lead));
  }

  [Fact]
  public async Task ConcurrentRefreshCallsIssueOneProtoRefreshRequest()
  {
    var singleFlight = new SingleFlightOperation();
    var entered = new TaskCompletionSource<bool>(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    var release = new TaskCompletionSource<bool>(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    var protoRefreshRequests = 0;

    async Task SendProtoRefreshRequest(CancellationToken cancellationToken)
    {
      var request = new ProtoOARefreshTokenReq { RefreshToken = "test-refresh" };
      Assert.IsType<ProtoOARefreshTokenReq>(request);
      Interlocked.Increment(ref protoRefreshRequests);
      entered.TrySetResult(true);
      await release.Task.WaitAsync(cancellationToken);
    }

    var first = singleFlight.RunAsync(
      SendProtoRefreshRequest,
      CancellationToken.None
    );
    await entered.Task;
    var second = singleFlight.RunAsync(
      SendProtoRefreshRequest,
      CancellationToken.None
    );

    Assert.Equal(1, protoRefreshRequests);
    release.TrySetResult(true);
    await Task.WhenAll(first, second);
    Assert.Equal(1, protoRefreshRequests);
  }

  [Fact]
  public async Task EmptyPrimaryRecoversFromMirrorAndHealsPrimary()
  {
    var warnings = new List<string>();
    var events = new List<(string Kind, string Message)>();
    var primary = new RecordingTokenStore("redis");
    var mirror = new RecordingTokenStore("file")
    {
      Token = Document("env-refresh", "rotated-refresh"),
    };
    var store = new TieredRefreshTokenStore(
      primary,
      mirror,
      (kind, message, _) =>
      {
        events.Add((kind, message));
        return Task.CompletedTask;
      },
      warnings.Add
    );

    var value = await store.GetAsync(CancellationToken.None);

    Assert.Equal(mirror.Token, value);
    Assert.Equal(mirror.Token, primary.Token);
    Assert.Equal("file", store.LastReadTier);
    Assert.Contains("Redis volume may have been lost", Assert.Single(warnings));
    var tokenEvent = Assert.Single(events);
    Assert.Equal("error", tokenEvent.Kind);
    Assert.Contains("Redis volume may have been lost", tokenEvent.Message);
  }

  [Fact]
  public async Task TierWritesMirrorBeforePrimaryAndToleratesMirrorFailure()
  {
    var order = new List<string>();
    var primary = new RecordingTokenStore("redis", order);
    var mirror = new RecordingTokenStore("file", order) { ThrowOnSet = true };
    var store = new TieredRefreshTokenStore(
      primary,
      mirror,
      warningLog: _ => { }
    );

    await store.SetAsync("document", CancellationToken.None);

    Assert.Equal(["file", "redis"], order);
    Assert.Equal("document", primary.Token);
  }

  [Fact]
  public async Task PrimaryWriteFailureStillLeavesMirrorWritten()
  {
    var order = new List<string>();
    var primary = new RecordingTokenStore("redis", order) { ThrowOnSet = true };
    var mirror = new RecordingTokenStore("file", order);
    var store = new TieredRefreshTokenStore(primary, mirror);

    await Assert.ThrowsAsync<IOException>(
      () => store.SetAsync("document", CancellationToken.None)
    );

    Assert.Equal(["file", "redis"], order);
    Assert.Equal("document", mirror.Token);
  }

  [Fact]
  public void LegacyDocumentDeserializesWithUnknownExpiry()
  {
    var json = Document("env-refresh", "rotated-refresh");

    var document = JsonSerializer.Deserialize(
      json,
      RedisJsonContext.Default.RefreshTokenDocument
    );

    Assert.NotNull(document);
    Assert.Null(document.AccessToken);
    Assert.Equal(0, document.ExpiresAtUnixSeconds);
  }

  [Fact]
  public async Task ResetWithoutExplicitConfirmationIsANoop()
  {
    var result = await Program.Main(["--reset-token-cache"]);

    Assert.NotEqual(0, result);
  }

  [Fact]
  public async Task SeedAndRefreshDiagnosticsNeverExposeTokenMaterial()
  {
    const string environmentToken = "known-environment-refresh-secret";
    const string oldCurrentToken = "known-old-current-refresh-secret";
    const string accessToken = "known-access-secret";
    var logs = new List<string>();
    var events = new List<string>();
    var store = new RecordingTokenStore("redis")
    {
      Token = Document("different-seed", oldCurrentToken),
    };
    var options = TestOptions(environmentToken, accessToken);
    var state = new RefreshTokenState(
      options,
      store,
      logs.Add,
      (_, message, _) =>
      {
        events.Add(message);
        return Task.CompletedTask;
      }
    );

    await state.SeedAsync(CancellationToken.None);
    var refreshError = TokenRedaction.Redact(
      $"request rejected for {environmentToken} and {accessToken}",
      environmentToken,
      accessToken
    );
    var output = string.Join('\n', logs.Concat(events).Append(refreshError));

    Assert.DoesNotContain(environmentToken, output);
    Assert.DoesNotContain(oldCurrentToken, output);
    Assert.DoesNotContain(accessToken, output);
    Assert.Contains("[redacted]", refreshError);
  }

  [Fact]
  public async Task FileMirrorWritesAtomicallyWithOwnerOnlyMode()
  {
    var directory = Path.Combine(
      Path.GetTempPath(),
      $"ctrader-token-{Guid.NewGuid():N}"
    );
    var path = Path.Combine(directory, "token.json");
    try
    {
      var store = new FileRefreshTokenStore(path);

      await store.SetAsync("document", CancellationToken.None);

      Assert.Equal("document", await store.GetAsync(CancellationToken.None));
      if (!OperatingSystem.IsWindows())
      {
        var mode = File.GetUnixFileMode(path);
        Assert.Equal(
          UnixFileMode.UserRead | UnixFileMode.UserWrite,
          mode
        );
      }
    }
    finally
    {
      if (Directory.Exists(directory))
      {
        Directory.Delete(directory, recursive: true);
      }
    }
  }

  [Fact]
  public async Task TokenNotificationsAreRateLimitedPerEventType()
  {
    var now = Now;
    var events = new List<string>();
    var notifier = new TokenEventNotifier(
      (_, message, _) =>
      {
        events.Add(message);
        return Task.CompletedTask;
      },
      () => now
    );

    await notifier.NotifyAsync("refresh-failed", "first", CancellationToken.None);
    await notifier.NotifyAsync("refresh-failed", "duplicate", CancellationToken.None);
    await notifier.NotifyAsync("seed-changed", "other type", CancellationToken.None);
    now = now.AddHours(1);
    await notifier.NotifyAsync("refresh-failed", "after window", CancellationToken.None);

    Assert.Equal(["first", "other type", "after window"], events);
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

  private static FeedOptions TestOptions(string refreshToken, string accessToken) =>
    new(
      ClientId: "client",
      ClientSecret: "secret",
      AccessToken: accessToken,
      RefreshToken: refreshToken,
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

  private sealed class RecordingTokenStore(
    string tier,
    List<string>? order = null
  ) : IRefreshTokenStore
  {
    public string LastReadTier => tier;
    public string? Token { get; set; }
    public bool ThrowOnSet { get; init; }

    public Task<string?> GetAsync(CancellationToken cancellationToken) =>
      Task.FromResult(Token);

    public Task SetAsync(string token, CancellationToken cancellationToken)
    {
      order?.Add(tier);
      if (ThrowOnSet)
      {
        throw new IOException($"{tier} write failed");
      }
      Token = token;
      return Task.CompletedTask;
    }

    public Task DeleteAsync(CancellationToken cancellationToken)
    {
      Token = null;
      return Task.CompletedTask;
    }
  }
}

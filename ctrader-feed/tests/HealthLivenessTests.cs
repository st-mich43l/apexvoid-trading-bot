using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class HealthLivenessTests
{
  [Fact]
  public async Task HeartbeatKeepsHealthFreshWhenNoBarsClose()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var stale = DateTime.UtcNow.AddMinutes(-20);
    var client = new FakeCTraderClient
    {
      OnLiveStart = () => TouchStale(temp.Path, stale),
      HeartbeatsOnLiveStart = 1,
      CancelOnLiveStart = () => cts.Cancel(),
    };
    var runner = Runner(temp.Path, client);

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunOneSessionAsync(cts.Token)
    );

    Assert.Equal(0, HealthFile.Check(temp.Path, TimeSpan.FromMinutes(10)));
  }

  [Fact]
  public async Task HealthStalesWhenNoHeartbeatArrives()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var stale = DateTime.UtcNow.AddMinutes(-20);
    var client = new FakeCTraderClient
    {
      OnLiveStart = () => TouchStale(temp.Path, stale),
      CancelOnLiveStart = () => cts.Cancel(),
    };
    var runner = Runner(temp.Path, client);

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunOneSessionAsync(cts.Token)
    );

    Assert.Equal(1, HealthFile.Check(temp.Path, TimeSpan.FromMinutes(10)));
  }

  private static FeedRunner Runner(string heartbeatPath, FakeCTraderClient client) =>
    new(
      Options(heartbeatPath),
      () => client,
      new RecordingSink(),
      new HealthFile(heartbeatPath),
      _ => TimeSpan.Zero
    );

  private static FeedOptions Options(string heartbeatPath) =>
    new(
      ClientId: "client",
      ClientSecret: "secret",
      AccessToken: "access",
      RefreshToken: "refresh",
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
      HeartbeatFile: heartbeatPath,
      RefreshTokenKey: "ctrader:refresh_token",
      RequestTimeout: TimeSpan.FromSeconds(1),
      TokenRefreshInterval: TimeSpan.FromHours(1)
    );

  private static void TouchStale(string path, DateTime stale)
  {
    File.WriteAllText(path, "old");
    File.SetLastWriteTimeUtc(path, stale);
  }
}

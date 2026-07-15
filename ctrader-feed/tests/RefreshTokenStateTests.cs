using ApexVoid.CTraderFeed;
using OpenAPI.Net;

namespace CTraderFeed.Tests;

public sealed class RefreshTokenStateTests
{
  [Fact]
  public async Task SeedsRefreshTokenFromStoreAndPersistsRotatedValue()
  {
    var store = new FakeRefreshTokenStore { Token = "persisted-refresh" };
    var state = new RefreshTokenState(Options(), store);

    await state.SeedAsync(CancellationToken.None);
    await state.ApplyAsync(
      new ProtoOARefreshTokenRes
      {
        AccessToken = "rotated-access",
        RefreshToken = "rotated-refresh",
      },
      CancellationToken.None
    );

    Assert.Equal("rotated-access", state.AccessToken);
    Assert.Equal("rotated-refresh", state.RefreshToken);
    Assert.Equal("rotated-refresh", store.Token);
  }

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
      RequestTimeout: TimeSpan.FromSeconds(1),
      TokenRefreshInterval: TimeSpan.FromHours(1)
    );
}

internal sealed class FakeRefreshTokenStore : IRefreshTokenStore
{
  public string? Token { get; set; }

  public Task<string?> GetAsync(CancellationToken cancellationToken) =>
    Task.FromResult(Token);

  public Task SetAsync(string token, CancellationToken cancellationToken)
  {
    Token = token;
    return Task.CompletedTask;
  }
}

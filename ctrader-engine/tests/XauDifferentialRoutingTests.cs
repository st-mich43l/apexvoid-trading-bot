using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class XauDifferentialRoutingTests
{
  [Fact]
  public void Legacy_xau_feed_options_match_registry_projection()
  {
    var feed = new FeedOptions(
      ClientId: "id",
      ClientSecret: "secret",
      AccessToken: "access",
      RefreshToken: "refresh",
      AccountId: 42,
      Host: "demo.ctraderapi.com",
      Port: 5035,
      CTraderSymbol: "XAUUSD",
      RedisSymbol: "XAU",
      Timeframes: ["M1", "M5", "M15", "H1"],
      BackfillBars: 1500,
      RedisUrl: "redis://localhost:6379/0",
      BarsWindowMax: 1500,
      BarsChannel: "bars:new",
      BarQualityLookback: 6,
      HeartbeatFile: "/tmp/h",
      AutoTradeHeartbeatFile: "/tmp/h.autotrade",
      RefreshTokenKey: "ctrader:refresh_token",
      RefreshTokenFile: "/tmp/t.json",
      RequestTimeout: TimeSpan.FromSeconds(30),
      TokenRefreshLead: TimeSpan.FromDays(5),
      TokenCheckInterval: TimeSpan.FromHours(6),
      ExpectedBroker: "fpmarkets"
    );
    var trade = new AutoTradeOptions(
      Enabled: true,
      DryRun: false,
      ExpectedBroker: "fpmarkets",
      StopLossDistance: 6.5m,
      TargetsPips: [30, 60, 90, 120, 200],
      TargetWeights: [20, 20, 20, 20, 20],
      BreakEvenBufferTicks: 6,
      CandidateMaxAgeSeconds: 90,
      SpotMaxAgeSeconds: 5,
      MaxSpreadPips: 5,
      MaxEntryDistancePips: 10,
      MinConfluence: 2,
      PollMilliseconds: 10,
      CandidateStream: "auto_trade:candidates",
      EventStream: "auto_trade:events",
      Label: "apexvoid-auto",
      CanonicalSymbol: "XAU",
      PipSize: 0.1m,
      ContractSize: 100m
    );

    var legacyInstrument = FeedInstrumentOptions.FromFeedOptions(feed);
    var routed = InstrumentRuntimeRegistry.FromXauCompatibility(feed, trade).Get("XAU");

    Assert.Equal(legacyInstrument.CTraderSymbol, routed.Feed.CTraderSymbol);
    Assert.Equal(legacyInstrument.RedisSymbol, routed.Feed.RedisSymbol);
    Assert.Equal(legacyInstrument.Timeframes, routed.Feed.Timeframes);
    Assert.Equal(legacyInstrument.BackfillBars, routed.Feed.BackfillBars);
    Assert.Equal(legacyInstrument.BarsChannel, routed.Feed.BarsChannel);
    Assert.Equal(InstrumentRollout.Live, routed.Rollout);
    Assert.Equal(0.1m, routed.Execution.PipSize);
    Assert.Equal(100m, routed.Execution.ContractSize);

    var account = CTraderAccountOptions.FromFeedOptions(feed);
    Assert.Equal(feed.AccountId, account.AccountId);
    Assert.Equal(feed.Host, account.Host);
    Assert.Equal(feed.RedisUrl, account.RedisUrl);
  }

  [Fact]
  public void Xau_live_candidate_route_is_accept_live()
  {
    var feed = new FeedOptions(
      ClientId: "id",
      ClientSecret: "secret",
      AccessToken: "access",
      RefreshToken: "refresh",
      AccountId: 42,
      Host: "demo.ctraderapi.com",
      Port: 5035,
      CTraderSymbol: "XAUUSD",
      RedisSymbol: "XAU",
      Timeframes: ["M1"],
      BackfillBars: 10,
      RedisUrl: "redis://localhost:6379/0",
      BarsWindowMax: 10,
      BarsChannel: "bars:new",
      BarQualityLookback: 6,
      HeartbeatFile: "/tmp/h",
      AutoTradeHeartbeatFile: "/tmp/h.autotrade",
      RefreshTokenKey: "k",
      RefreshTokenFile: "/tmp/t.json",
      RequestTimeout: TimeSpan.FromSeconds(30),
      TokenRefreshLead: TimeSpan.FromDays(5),
      TokenCheckInterval: TimeSpan.FromHours(6)
    );
    var trade = new AutoTradeOptions(
      Enabled: true,
      DryRun: false,
      ExpectedBroker: "fpmarkets",
      StopLossDistance: 6.5m,
      TargetsPips: [30],
      TargetWeights: [100],
      BreakEvenBufferTicks: 6,
      CandidateMaxAgeSeconds: 90,
      SpotMaxAgeSeconds: 5,
      MaxSpreadPips: 5,
      MaxEntryDistancePips: 10,
      MinConfluence: 2,
      PollMilliseconds: 10,
      CandidateStream: "auto_trade:candidates",
      EventStream: "auto_trade:events",
      Label: "apexvoid-auto",
      CanonicalSymbol: "XAU"
    );
    var registry = InstrumentRuntimeRegistry.FromXauCompatibility(feed, trade);
    var decision = CandidateInstrumentDispatcher.Route(registry, "XAU");
    Assert.Equal(CandidateRouteOutcome.AcceptLive, decision.Outcome);
    Assert.True(decision.IsAccepted);
  }
}

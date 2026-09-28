using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class MultiInstrumentRoutingTests
{
  private static InstrumentRuntime Make(
    string id,
    InstrumentRollout rollout,
    string broker,
    string canonical,
    IReadOnlyList<string>? aliases = null
  ) =>
    new()
    {
      InstrumentId = id,
      Aliases = aliases ?? [],
      Feed = new FeedInstrumentOptions(
        InstrumentId: id,
        CanonicalSymbol: canonical,
        CTraderSymbol: broker,
        RedisSymbol: canonical,
        Timeframes: ["M1", "M5"],
        BackfillBars: 100,
        BarsWindowMax: 100,
        BarsChannel: "bars:new",
        BarQualityLookback: 6,
        Rollout: rollout
      ),
      Execution = new ExecutionInstrumentOptions(
        InstrumentId: id,
        CanonicalSymbol: canonical,
        Rollout: rollout,
        PipSize: id == "XAU" ? 0.1m : 0.01m,
        ContractSize: id == "XAU" ? 100m : 5000m,
        EffectiveSymbols: [canonical]
      ),
    };

  [Fact]
  public void Xau_live_xag_disabled_only_subscribes_xau()
  {
    var registry = new InstrumentRuntimeRegistry([
      Make("XAU", InstrumentRollout.Live, "XAUUSD", "XAU"),
      Make("XAG", InstrumentRollout.Disabled, "XAGUSD", "XAG"),
    ]);
    Assert.Equal(["XAU"], registry.FeedInstruments().Select(r => r.InstrumentId));
    Assert.Equal(["XAU"], registry.LiveInstruments().Select(r => r.InstrumentId));
    Assert.DoesNotContain(
      registry.All,
      runtime => runtime.InstrumentId == "XAG"
        && InstrumentRolloutGates.PermitsFeed(runtime.Rollout)
    );
  }

  [Fact]
  public void Xau_live_xag_feed_only_subscribes_both_but_only_xau_executable()
  {
    var registry = new InstrumentRuntimeRegistry([
      Make("XAU", InstrumentRollout.Live, "XAUUSD", "XAU"),
      Make("XAG", InstrumentRollout.FeedOnly, "XAGUSD", "XAG"),
    ]);
    Assert.Equal(
      ["XAG", "XAU"],
      registry.FeedInstruments().Select(r => r.InstrumentId).OrderBy(x => x)
    );
    Assert.Equal(["XAU"], registry.ExecutableInstruments().Select(r => r.InstrumentId));
    var xag = CandidateInstrumentDispatcher.Route(registry, "XAG");
    Assert.Equal(CandidateRouteOutcome.RejectFeedOnly, xag.Outcome);
    Assert.False(xag.IsAccepted);
  }

  [Fact]
  public void Xau_live_xag_analysis_only_cannot_trade()
  {
    var registry = new InstrumentRuntimeRegistry([
      Make("XAU", InstrumentRollout.Live, "XAUUSD", "XAU"),
      Make("XAG", InstrumentRollout.AnalysisOnly, "XAGUSD", "XAG"),
    ]);
    var decision = CandidateInstrumentDispatcher.Route(registry, "XAG");
    Assert.Equal(CandidateRouteOutcome.RejectAnalysisOnly, decision.Outcome);
  }

  [Fact]
  public void Paper_symbol_cannot_place_broker_orders()
  {
    var registry = new InstrumentRuntimeRegistry([
      Make("XAU", InstrumentRollout.Live, "XAUUSD", "XAU"),
      Make("XAG", InstrumentRollout.Paper, "XAGUSD", "XAG"),
    ]);
    var liveShape = CandidateInstrumentDispatcher.Route(registry, "XAG", paperCandidate: false);
    Assert.Equal(CandidateRouteOutcome.RejectPaperToLiveBroker, liveShape.Outcome);
    var paper = CandidateInstrumentDispatcher.Route(registry, "XAG", paperCandidate: true);
    Assert.Equal(CandidateRouteOutcome.AcceptPaper, paper.Outcome);
    Assert.False(InstrumentRolloutGates.PermitsBrokerExecution(paper.Runtime!.Rollout));
  }

  [Fact]
  public void Candidate_isolation_rejects_cross_symbol_units_and_alias()
  {
    var registry = new InstrumentRuntimeRegistry([
      Make("XAU", InstrumentRollout.Live, "XAUUSD", "XAU"),
      Make("XAG", InstrumentRollout.Live, "XAGUSD", "XAG"),
    ]);
    var xau = CandidateInstrumentDispatcher.Route(registry, "XAU");
    var xag = CandidateInstrumentDispatcher.Route(registry, "XAG");
    Assert.True(xau.IsAccepted);
    Assert.True(xag.IsAccepted);
    Assert.NotEqual(xau.Runtime!.Execution.PipSize, xag.Runtime!.Execution.PipSize);
    var alias = CandidateInstrumentDispatcher.Route(registry, "XAUUSD");
    Assert.Equal(CandidateRouteOutcome.RejectAliasNotNormalized, alias.Outcome);
  }

  [Fact]
  public void Duplicate_alias_fails_closed()
  {
    Assert.Throws<InvalidOperationException>(() =>
      new InstrumentRuntimeRegistry([
        Make("XAU", InstrumentRollout.Live, "XAUUSD", "XAU"),
        Make("XAG", InstrumentRollout.FeedOnly, "XAUUSD", "XAG"),
      ])
    );
  }

  [Fact]
  public void Runtime_identity_alias_is_resolved_case_insensitively()
  {
    var registry = new InstrumentRuntimeRegistry([
      Make(
        "XAU",
        InstrumentRollout.Live,
        "XAUUSD",
        "XAU",
        ["GOLD", " XAUUSDm "]
      ),
    ]);

    Assert.Equal("XAU", registry.Get("gold").InstrumentId);
    Assert.Equal("XAU", registry.Get("xauusdm").InstrumentId);
  }

  [Fact]
  public void Runtime_identity_alias_collision_fails_closed()
  {
    var exception = Assert.Throws<InvalidOperationException>(() =>
      new InstrumentRuntimeRegistry([
        Make("XAU", InstrumentRollout.Live, "XAUUSD", "XAU", ["METAL"]),
        Make("XAG", InstrumentRollout.Live, "XAGUSD", "XAG", ["metal"]),
      ])
    );

    Assert.Contains("duplicate alias", exception.Message);
  }

  [Fact]
  public void Reconciliation_partitions_and_leaves_unknown_unmanaged()
  {
    var registry = new InstrumentRuntimeRegistry([
      Make("XAU", InstrumentRollout.Live, "XAUUSD", "XAU"),
      Make("XAG", InstrumentRollout.FeedOnly, "XAGUSD", "XAG"),
    ]);
    registry.BindResolvedSymbol(
      registry.Get("XAU"),
      new SymbolInfo("XAU", "XAUUSD", 11, 2)
    );
    registry.BindResolvedSymbol(
      registry.Get("XAG"),
      new SymbolInfo("XAG", "XAGUSD", 22, 3)
    );
    var positions = new[]
    {
      new TradingPosition(1, 11, TradeDirection.Buy, 100, 2000m, null, "xau", ""),
      new TradingPosition(2, 22, TradeDirection.Sell, 200, 30m, null, "xag", ""),
      new TradingPosition(3, 99, TradeDirection.Buy, 50, 1m, null, "unk", ""),
    };
    var coordinator = new AccountRiskCoordinator();
    coordinator.ObserveAccountPositions(positions);
    Assert.Equal(3, coordinator.OpenPositionCount);
    var xauPositions = coordinator.PartitionByInstrument(positions, registry.Get("XAU"));
    Assert.Single(xauPositions);
    Assert.Equal(11, xauPositions[0].SymbolId);
    var unmanaged = coordinator.UnmanagedPositions(positions, registry);
    Assert.Single(unmanaged);
    Assert.Equal(99, unmanaged[0].SymbolId);
  }

  [Fact]
  public void Env_xau_compatibility_registry_matches_feed_options()
  {
    var feed = new FeedOptions(
      ClientId: "id",
      ClientSecret: "secret",
      AccessToken: "access",
      RefreshToken: "refresh",
      AccountId: 1,
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
    var registry = InstrumentRuntimeRegistry.FromXauCompatibility(feed, trade);
    Assert.Single(registry.All);
    Assert.Equal("XAU", registry.LiveInstruments().Single().InstrumentId);
    Assert.Equal("XAUUSD", registry.Get("XAU").Feed.CTraderSymbol);
    Assert.Equal("XAU", registry.Get("XAU").Feed.RedisSymbol);
  }
}

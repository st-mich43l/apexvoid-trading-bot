using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class AutoTradeOptionsTests
{
  [Fact]
  public void ValidatesTargetsWeightsAndBreakEvenAsOneSet()
  {
    Options().Validate();

    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { TargetWeights = [20, 20, 20, 20, 19] }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { TargetWeights = [25, 25, 25, 25] }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { BreakEvenBufferPips = 30 }).Validate()
    );
  }

  [Fact]
  public void ValidatesScaleInAndZoneFillSettingsAsOneSet()
  {
    Options().Validate();

    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { MaxTranches = 0 }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { AddRiskFraction = 1.1m }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { AddMinStopPips = 0 }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { AddMinStopPips = 66 }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { ZoneFillTtlBars = 0 }).Validate()
    );
  }

  private static AutoTradeOptions Options() => new(
    Enabled: true,
    DryRun: false,
    ExpectedBroker: "Fusion",
    StopLossDistance: 6.5m,
    TargetsPips: [30, 60, 90, 120, 200],
    TargetWeights: [20, 20, 20, 20, 20],
    BreakEvenBufferPips: 3,
    CandidateMaxAgeSeconds: 90,
    SpotMaxAgeSeconds: 5,
    MaxSpreadPips: 5,
    MaxEntryDistancePips: 10,
    MaxDailyTrades: 6,
    MinConfluence: 2,
    PollMilliseconds: 10,
    CandidateStream: "auto_trade:candidates",
    EventStream: "auto_trade:events",
    Label: "apexvoid-auto"
  );
}

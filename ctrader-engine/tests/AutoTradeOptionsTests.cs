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
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { ZoneFillMinLots = 0 }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { BoxMinRiskReward = 0.9m }).Validate()
    );
  }

  [Fact]
  public void SizingModeDefaultsToMinAndRejectsUnknownValues()
  {
    Assert.Equal("min", Options().SizingMode);

    var error = Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { SizingMode = "maximum" }).Validate()
    );

    Assert.Contains("AUTO_TRADE_SIZING_MODE", error.Message);
    Assert.Contains("min, table, risk", error.Message);
  }

  [Fact]
  public void ReadsExplicitSizingModeFromEnvironment()
  {
    Environment.SetEnvironmentVariable("AUTO_TRADE_SIZING_MODE", "table");
    try
    {
      Assert.Equal("table", AutoTradeOptions.FromEnvironment().SizingMode);
    }
    finally
    {
      Environment.SetEnvironmentVariable("AUTO_TRADE_SIZING_MODE", null);
    }
  }

  [Fact]
  public void ValidatesTrendStopBandBounds()
  {
    Options().Validate();

    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { TrendStopMinPips = 0 }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { TrendStopMaxPips = 0 }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { TrendStopMinPips = 150, TrendStopMaxPips = 120 }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { TrendStopMaxPips = 120 }).Validate()
    );
  }

  [Fact]
  public void RejectsInconsistentPipContractAtStartup()
  {
    var error = Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { PipSize = 0.01m }).Validate()
    );

    Assert.Contains("pip value inconsistent", error.Message);
    Assert.Contains("PipValuePerLot=10", error.Message);
    Assert.Contains("ContractSize 100 x PipSize 0.01 = 1.00", error.Message);
  }

  [Fact]
  public void StopPushBeyondZoneDefaultsTrueAndReadsFromEnvironment()
  {
    Environment.SetEnvironmentVariable("AUTO_TRADE_STOP_PUSH_BEYOND_ZONE", null);
    Assert.True(AutoTradeOptions.FromEnvironment().StopPushBeyondZone);

    Environment.SetEnvironmentVariable("AUTO_TRADE_STOP_PUSH_BEYOND_ZONE", "false");
    try
    {
      Assert.False(AutoTradeOptions.FromEnvironment().StopPushBeyondZone);
    }
    finally
    {
      Environment.SetEnvironmentVariable("AUTO_TRADE_STOP_PUSH_BEYOND_ZONE", null);
    }
  }

  [Fact]
  public void StopFloorAndWickBufferUseSafeDefaults()
  {
    Environment.SetEnvironmentVariable("AUTO_TRADE_ADD_MIN_STOP_PIPS", null);
    Environment.SetEnvironmentVariable("AUTO_TRADE_WICK_STOP_BUFFER_ATR", null);

    var options = AutoTradeOptions.FromEnvironment();

    Assert.Equal(30, options.AddMinStopPips);
    Assert.Equal(0.15m, options.WickStopBufferAtr);
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { WickStopBufferAtr = -0.01m }).Validate()
    );
  }

  [Fact]
  public void RangeFlipDefaultsOffAndValidatesItsControls()
  {
    var options = AutoTradeOptions.FromEnvironment();

    Assert.False(options.RangeFlipEnabled);
    Assert.Equal(10, options.FlipExitBufferPips);
    Assert.Equal(30, options.FlipConfirmTimeoutSeconds);
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { FlipExitBufferPips = -1 }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { FlipConfirmTimeoutSeconds = 0 }).Validate()
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
    MinConfluence: 2,
    PollMilliseconds: 10,
    CandidateStream: "auto_trade:candidates",
    EventStream: "auto_trade:events",
    Label: "apexvoid-auto"
  );
}

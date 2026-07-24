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
  public void ZoneCooldownMinutesDefaultsToSixtyAndValidatesPositive()
  {
    Assert.Equal(60, AutoTradeOptions.FromEnvironment().ZoneCooldownMinutes);
    Options().Validate();

    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { ZoneCooldownMinutes = 0 }).Validate()
    );
  }

  [Fact]
  public void ReadsZoneCooldownMinutesFromEnvironment()
  {
    Environment.SetEnvironmentVariable("AUTO_TRADE_ZONE_COOLDOWN_MINUTES", "90");
    try
    {
      Assert.Equal(90, AutoTradeOptions.FromEnvironment().ZoneCooldownMinutes);
    }
    finally
    {
      Environment.SetEnvironmentVariable("AUTO_TRADE_ZONE_COOLDOWN_MINUTES", null);
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

  [Fact]
  public void DemoEvalProfileResolvesPermissiveExecutionDefaults()
  {
    Environment.SetEnvironmentVariable("AUTO_TRADE_PROFILE", "demo_eval");
    try
    {
      var options = AutoTradeOptions.FromEnvironment();

      Assert.Equal("demo_eval", options.Profile);
      Assert.True(options.RequireDemoAccount);
      Assert.True(options.AllowConcurrentStrategies);
      Assert.True(options.AllowHedgedXau);
      Assert.False(options.RequireFlatForRange);
      Assert.True(options.RangeTwoSidedEnabled);
      Assert.True(options.RangeFlipEnabled);
      Assert.True(options.MultiMatchEnabled);
      Assert.True(options.TrackAllStructuralMatches);
      Assert.True(options.Enabled);
      Assert.False(options.DryRun);
      Assert.Equal(420, options.CandidateMaxAgeSeconds);
      Assert.Equal(604800, options.CandidateStorageTtlSeconds);
      Assert.Equal(5, options.CandidateContractVersion);
      Assert.True(options.ZoneFillEnabled);
      Assert.Equal("broker_netting", options.NonHedgedOppositePolicy);
      Assert.Equal("observe", options.StructuralGuardMode);
      Assert.False(options.ZoneCooldownEnabled);
      Assert.Equal("shadow", options.ZoneReconcileMode);
      Assert.Equal(ExposurePolicy.HedgedConcurrent, options.ExposurePolicy);
      options.Validate();
    }
    finally
    {
      Environment.SetEnvironmentVariable("AUTO_TRADE_PROFILE", null);
    }
  }

  [Fact]
  public void DemoEvalHonoursExplicitEnvironmentOverrides()
  {
    Environment.SetEnvironmentVariable("AUTO_TRADE_PROFILE", "demo_eval");
    Environment.SetEnvironmentVariable("AUTO_TRADE_RANGE_FLIP_ENABLED", "false");
    Environment.SetEnvironmentVariable(
      "AUTO_TRADE_ALLOW_CONCURRENT_STRATEGIES", "false"
    );
    try
    {
      var options = AutoTradeOptions.FromEnvironment();

      Assert.False(options.RangeFlipEnabled);
      Assert.False(options.AllowConcurrentStrategies);
      Assert.Equal(ExposurePolicy.FlatOnly, options.ExposurePolicy);
    }
    finally
    {
      Environment.SetEnvironmentVariable("AUTO_TRADE_PROFILE", null);
      Environment.SetEnvironmentVariable("AUTO_TRADE_RANGE_FLIP_ENABLED", null);
      Environment.SetEnvironmentVariable(
        "AUTO_TRADE_ALLOW_CONCURRENT_STRATEGIES", null
      );
    }
  }

  [Fact]
  public void RangeTargetsDefaultToSharedTwentyToSeventyLadder()
  {
    var options = AutoTradeOptions.FromEnvironment();

    // Same default and env var (AUTO_TRADE_RANGE_TARGETS_PIPS) as
    // app/autotrade/range_targets.py on the Python side.
    Assert.Equal(new[] { 20, 30, 40, 50, 70 }, options.EffectiveRangeTargetsPips);
    Assert.Equal(3m, options.RangeTpBufferPips);
  }

  [Fact]
  public void RangeTargetsFallBackToDefaultWhenUnset()
  {
    var options = Options();

    Assert.Null(options.RangeTargetsPips);
    Assert.Equal(new[] { 20, 30, 40, 50, 70 }, options.EffectiveRangeTargetsPips);
  }

  [Fact]
  public void ReadsExplicitRangeTargetsFromEnvironment()
  {
    Environment.SetEnvironmentVariable("AUTO_TRADE_RANGE_TARGETS_PIPS", "20,35");
    Environment.SetEnvironmentVariable("AUTO_TRADE_RANGE_TP_BUFFER_PIPS", "3");
    try
    {
      var options = AutoTradeOptions.FromEnvironment();
      Assert.Equal(new[] { 20, 35 }, options.EffectiveRangeTargetsPips);
      Assert.Equal(3m, options.RangeTpBufferPips);
    }
    finally
    {
      Environment.SetEnvironmentVariable("AUTO_TRADE_RANGE_TARGETS_PIPS", null);
      Environment.SetEnvironmentVariable("AUTO_TRADE_RANGE_TP_BUFFER_PIPS", null);
    }
  }

  [Fact]
  public void ValidatesRangeTargetsAreNonEmptyPositiveWithNonNegativeBuffer()
  {
    Options().Validate();

    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { RangeTargetsPips = [] }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { RangeTargetsPips = [30, -5] }).Validate()
    );
    Assert.Throws<AutoTradeConfigurationException>(
      () => (Options() with { RangeTpBufferPips = -1m }).Validate()
    );
  }

  [Fact]
  public void CanonicalEnvironmentTakesPrecedenceOverDeprecatedAlias()
  {
    Environment.SetEnvironmentVariable(
      "AUTO_TRADE_CANDIDATE_STREAM", "canonical:candidates"
    );
    Environment.SetEnvironmentVariable(
      "AUTO_TRADE_STREAM", "legacy:candidates"
    );
    Environment.SetEnvironmentVariable(
      "AUTO_TRADE_TARGET_PLANS_PIPS", "30,60,90,120,200"
    );
    Environment.SetEnvironmentVariable(
      "AUTO_TRADE_TP_PIPS", "1,2,3,4,5"
    );
    try
    {
      var options = AutoTradeOptions.FromEnvironment();

      Assert.Equal("canonical:candidates", options.CandidateStream);
      Assert.Equal(
        new[] { 30, 60, 90, 120, 200 },
        options.TargetsPips
      );
      Assert.Equal(
        "explicit_env",
        options.ConfigSources!["AUTO_TRADE_CANDIDATE_STREAM"]
      );
      Assert.Contains("AUTO_TRADE_STREAM", options.DeprecatedVariables!);
      Assert.Contains("AUTO_TRADE_TP_PIPS", options.DeprecatedVariables!);
    }
    finally
    {
      Environment.SetEnvironmentVariable("AUTO_TRADE_CANDIDATE_STREAM", null);
      Environment.SetEnvironmentVariable("AUTO_TRADE_STREAM", null);
      Environment.SetEnvironmentVariable("AUTO_TRADE_TARGET_PLANS_PIPS", null);
      Environment.SetEnvironmentVariable("AUTO_TRADE_TP_PIPS", null);
    }
  }

  [Theory]
  [InlineData("true", true)]
  [InlineData("TRUE", true)]
  [InlineData("1", true)]
  [InlineData("yes", true)]
  [InlineData("false", false)]
  [InlineData("0", false)]
  [InlineData("NO", false)]
  public void StrictBooleanParserAcceptsDocumentedForms(
    string configured,
    bool expected
  )
  {
    Environment.SetEnvironmentVariable("AUTO_TRADE_ENABLED", configured);
    try
    {
      Assert.Equal(expected, AutoTradeOptions.FromEnvironment().Enabled);
    }
    finally
    {
      Environment.SetEnvironmentVariable("AUTO_TRADE_ENABLED", null);
    }
  }

  [Fact]
  public void StrictBooleanParserRejectsUnknownValue()
  {
    Environment.SetEnvironmentVariable("AUTO_TRADE_ENABLED", "enable-ish");
    try
    {
      var error = Assert.Throws<AutoTradeConfigurationException>(
        AutoTradeOptions.FromEnvironment
      );
      Assert.Contains("AUTO_TRADE_ENABLED", error.Message);
    }
    finally
    {
      Environment.SetEnvironmentVariable("AUTO_TRADE_ENABLED", null);
    }
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

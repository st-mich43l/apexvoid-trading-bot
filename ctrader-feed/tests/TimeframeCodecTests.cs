using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class TimeframeCodecTests
{
  [Fact]
  public void SupportsOneMinuteTrendbars()
  {
    Assert.Equal(60, TimeframeCodec.ToSeconds("M1"));
    Assert.Equal(ProtoOATrendbarPeriod.M1, TimeframeCodec.ToProto("M1"));
    Assert.Equal("M1", TimeframeCodec.FromProto(ProtoOATrendbarPeriod.M1));
  }
}

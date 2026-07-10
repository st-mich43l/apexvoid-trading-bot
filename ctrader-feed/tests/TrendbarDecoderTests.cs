using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class TrendbarDecoderTests
{
  [Fact]
  public void DecodeUsesOpenApiPriceScaleAndUtcOpenTimestamp()
  {
    var raw = new RawTrendbar(
      Timeframe: "M5",
      Low: 410000000,
      DeltaOpen: 123000,
      DeltaHigh: 567000,
      DeltaClose: 345000,
      Volume: 77,
      UtcTimestampInMinutes: 60
    );

    var bar = TrendbarDecoder.Decode(raw, digits: 2);

    Assert.Equal(3600, bar.Timestamp);
    Assert.Equal(4101.23m, bar.Open);
    Assert.Equal(4105.67m, bar.High);
    Assert.Equal(4100.00m, bar.Low);
    Assert.Equal(4103.45m, bar.Close);
    Assert.Equal(77, bar.Volume);
  }
}

using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class ClosedBarEmitterTests
{
  [Fact]
  public void LiveBarCloseUsesLastInPeriodSpotAndPreservesOpenHighLow()
  {
    var spots = new SpotHistory();
    var emitter = new ClosedBarEmitter(spots, "XAU");
    var forming = TrendbarDecoder.Decode(
      Raw(
        1_200,
        low: 800_000,
        deltaClose: 0,
        hasDeltaClose: false
      ),
      digits: 2
    );
    var updated = forming with { Volume = 2 };
    var nextPeriod = TrendbarDecoder.Decode(
      Raw(1_500, low: 1_000_000, deltaClose: 0, hasDeltaClose: false),
      digits: 2
    );
    spots.Observe(new SpotPrice("XAU", 10.25m, 10.30m, 1_300));
    spots.Observe(new SpotPrice("XAU", 11.25m, 11.30m, 1_499));
    spots.Observe(new SpotPrice("XAU", 10.75m, 10.80m, 1_500));

    Assert.Empty(emitter.Observe("M5", forming));
    Assert.Empty(emitter.Observe("M5", updated));

    var emitted = emitter.Observe("M5", nextPeriod);

    Assert.Single(emitted);
    Assert.False(emitted[0].RequiresHistoricalClose);
    Assert.Equal(10m, emitted[0].Bar.Open);
    Assert.Equal(12m, emitted[0].Bar.High);
    Assert.Equal(8m, emitted[0].Bar.Low);
    Assert.Equal(11.25m, emitted[0].Bar.Close);
    Assert.Empty(emitter.Observe("M5", updated));
    Assert.Empty(emitter.Observe("M5", nextPeriod with { Close = 11.5m }));
  }

  [Fact]
  public void SpotOutsideRangeIsClamped()
  {
    var spots = new SpotHistory();
    var emitter = new ClosedBarEmitter(spots, "XAU");
    spots.Observe(new SpotPrice("XAU", 15m, 15.1m, 1_299));

    Assert.Empty(emitter.Observe("M5", new OhlcBar(1_000, 10, 12, 8, 8, 1)));
    var emitted = emitter.Observe("M5", new OhlcBar(1_300, 12, 13, 11, 11, 1));

    Assert.Single(emitted);
    Assert.False(emitted[0].RequiresHistoricalClose);
    Assert.Equal(12m, emitted[0].Bar.Close);
  }

  [Fact]
  public async Task MissingInPeriodSpotUsesHistoricalCloseFallback()
  {
    var emitter = new ClosedBarEmitter(new SpotHistory(), "XAU");
    Assert.Empty(emitter.Observe("M5", new OhlcBar(1_200, 10, 12, 8, 8, 1)));
    var emission = Assert.Single(
      emitter.Observe("M5", new OhlcBar(1_500, 11, 13, 10, 10, 1))
    );
    var client = new FakeCTraderClient
    {
      Backfill = [Raw(1_200, low: 800_000, deltaClose: 250_000)]
    };

    var resolved = await ClosedBarCloseResolver.ResolveAsync(
      client,
      new SymbolInfo("XAU", "XAUUSD", 7, 2),
      "M5",
      emission,
      CancellationToken.None
    );

    Assert.True(emission.RequiresHistoricalClose);
    Assert.Equal(10.5m, resolved.Close);
    Assert.Equal(1, client.BackfillCount);
  }

  private static RawTrendbar Raw(
    long timestamp,
    long low,
    ulong deltaClose,
    bool hasDeltaClose = true
  ) =>
    new(
      "M5",
      Low: low,
      DeltaOpen: 200_000,
      DeltaHigh: 400_000,
      DeltaClose: deltaClose,
      Volume: 100,
      UtcTimestampInMinutes: checked((uint)(timestamp / 60)),
      HasDeltaClose: hasDeltaClose
    );
}

using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class LiveBarQualityMonitorTests
{
  [Fact]
  public void SixConsecutiveExtremeClosesWarnOnceForTheNewPeriod()
  {
    var warnings = new List<string>();
    var monitor = new LiveBarQualityMonitor(6, warnings.Add);

    for (var i = 0; i < 6; i++)
    {
      monitor.Observe("M5", new OhlcBar(i * 300, 11, 12, 10, 10, 100));
    }
    monitor.Observe("M5", new OhlcBar(1_500, 11, 12, 10, 10, 100));

    var warning = Assert.Single(warnings);
    Assert.Contains(
      "live bars closing at range extreme 6 in a row - close-source suspect",
      warning
    );
  }
}

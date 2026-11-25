using Xunit;

namespace ApexVoid.CTraderFeed.Tests;

// PR-FEED1: the feed backfills by BAR COUNT, stepping back window by window, and reports any shortfall.
public class TrendbarBackfillTests
{
  // Monday 2026-11-23 01:00:30 UTC: the market reopened Sunday 22:00, so only ~3 hours of M1 exist since the weekend.
  private static readonly DateTimeOffset MondayMorning = new(2026, 11, 23, 1, 0, 30, TimeSpan.Zero);
  private static readonly DateTimeOffset TuesdayNoon = new(2026, 11, 24, 12, 0, 30, TimeSpan.Zero);

  /// <summary>Synthetic broker history: a bar every period, closed from Friday 22:00 to Sunday 22:00 UTC.</summary>
  private sealed class FakeServer(string timeframe, int pageLimit = int.MaxValue, DateTimeOffset? historyStart = null)
  {
    public List<(DateTimeOffset From, DateTimeOffset To, int Count, int Returned)> Requests { get; } = [];

    public static bool Open(DateTimeOffset t) =>
      !((t.DayOfWeek == DayOfWeek.Friday && t.Hour >= 22)
        || t.DayOfWeek == DayOfWeek.Saturday
        || (t.DayOfWeek == DayOfWeek.Sunday && t.Hour < 22));

    public static IEnumerable<long> BarTimes(string timeframe, DateTimeOffset from, DateTimeOffset to)
    {
      var seconds = TimeframeCodec.ToSeconds(timeframe);
      var first = (from.ToUnixTimeSeconds() + seconds - 1) / seconds * seconds;
      for (var ts = first; ts <= to.ToUnixTimeSeconds(); ts += seconds)
      {
        if (Open(DateTimeOffset.FromUnixTimeSeconds(ts)))
        {
          yield return ts;
        }
      }
    }

    public Task<IReadOnlyList<RawTrendbar>> Fetch(DateTimeOffset from, DateTimeOffset to, int count, CancellationToken _)
    {
      var start = historyStart is { } h && h > from ? h : from;
      var times = BarTimes(timeframe, start, to).ToList();
      var limit = Math.Min(count > 0 ? count : int.MaxValue, pageLimit);
      if (times.Count > limit)
      {
        times = times.GetRange(times.Count - limit, limit); // "back from toTimestamp": the newest ones
      }
      Requests.Add((from, to, count, times.Count));
      IReadOnlyList<RawTrendbar> page = times.Select(Raw).ToArray();
      return Task.FromResult(page);
    }

    private RawTrendbar Raw(long ts) =>
      new(timeframe, 400_000_00 + ts % 1000, 10, 50, 20, 1, checked((uint)(ts / 60)));
  }

  private sealed class FakeSink : IBarSink
  {
    public SortedDictionary<long, OhlcBar> Bars { get; } = [];
    public List<(long Ts, bool Publish)> Writes { get; } = [];
    public List<BackfillReport> Reports { get; } = [];

    public Task WriteClosedBarAsync(string symbol, string timeframe, OhlcBar bar, CancellationToken cancellationToken, bool publish = true)
    {
      Bars[bar.Timestamp] = bar;
      Writes.Add((bar.Timestamp, publish));
      return Task.CompletedTask;
    }

    public Task<long?> GetLatestTimestampAsync(string symbol, string timeframe, CancellationToken cancellationToken) =>
      Task.FromResult(Bars.Count == 0 ? (long?)null : Bars.Keys.Max());

    public Task<IReadOnlyList<OhlcBar>> ReadLatestAsync(string symbol, string timeframe, int count, CancellationToken cancellationToken)
    {
      IReadOnlyList<OhlcBar> latest = Bars.Values.Reverse().Take(count).ToArray();
      return Task.FromResult(latest);
    }

    public Task WriteSpotAsync(SpotPrice spot, CancellationToken cancellationToken) => Task.CompletedTask;

    public Task WriteBackfillStatusAsync(BackfillReport report, CancellationToken cancellationToken)
    {
      Reports.Add(report);
      return Task.CompletedTask;
    }

    public void Preload(string timeframe, DateTimeOffset to, int count)
    {
      var seconds = TimeframeCodec.ToSeconds(timeframe);
      foreach (var ts in FakeServer.BarTimes(timeframe, to.AddDays(-30), to).Where(ts => ts + seconds <= to.ToUnixTimeSeconds()).TakeLast(count))
      {
        Bars[ts] = new OhlcBar(ts, 1, 2, 0.5m, 1.5m, 1);
      }
    }
  }

  private static (TrendbarBackfill Backfill, List<string> Warnings, List<TimeSpan> Delays) Make(
    FakeSink sink, DateTimeOffset now, int maxRequests = 20, int backfillBars = 2000)
  {
    var warnings = new List<string>();
    var delays = new List<TimeSpan>();
    var backfill = new TrendbarBackfill(
      backfillBars, 2000, maxRequests, sink, () => now,
      (wait, _) => { delays.Add(wait); return Task.CompletedTask; },
      _ => { }, warnings.Add
    );
    return (backfill, warnings, delays);
  }

  private static void AssertStrictlyChronological(IEnumerable<long> timestamps)
  {
    var list = timestamps.ToList();
    for (var i = 1; i < list.Count; i++)
    {
      Assert.True(list[i] > list[i - 1], $"write {i} at {list[i]} is not after {list[i - 1]}");
    }
  }

  [Fact]
  public async Task MondayMorningRestartCollectsTheFullM1WindowAcrossTheWeekend()
  {
    // What the v1 calendar window (2,000 x 1 minute back from now) would have held: only Sunday night and Monday.
    var calendarWindow = FakeServer.BarTimes("M1", MondayMorning.AddMinutes(-2000), MondayMorning).Count();
    Assert.True(calendarWindow < 200, $"calendar window held {calendarWindow}");

    var server = new FakeServer("M1");
    var sink = new FakeSink();
    var (backfill, warnings, _) = Make(sink, MondayMorning);
    var report = await backfill.RunAsync("XAU", "M1", startup: true, server.Fetch, CancellationToken.None);

    Assert.Equal(2000, report.Written);
    Assert.Equal(2000, sink.Bars.Count);
    Assert.Equal(BackfillStop.Target, report.Stop);
    Assert.Empty(warnings);
    Assert.All(sink.Writes, write => Assert.False(write.Publish)); // startup stays silent
    AssertStrictlyChronological(sink.Writes.Select(write => write.Ts));
    // the forming 01:00 bar is excluded; the newest written bar is 00:59
    Assert.Equal(new DateTimeOffset(2026, 11, 23, 0, 59, 0, TimeSpan.Zero).ToUnixTimeSeconds(), report.LatestTs);
    // the window reaches back into the previous week, before Friday's close
    Assert.True(DateTimeOffset.FromUnixTimeSeconds(report.EarliestTs!.Value) < new DateTimeOffset(2026, 11, 20, 22, 0, 0, TimeSpan.Zero));
    Assert.Equal(1, report.Requests); // one 4-day window: Thursday 01:00 to Monday 01:00 holds 2,880 bars
    Assert.Equal(report, sink.Reports.Single());
    Assert.False(report.Short);
  }

  [Fact]
  public async Task M5WindowSpanningAWeekendCollectsTheFullCount()
  {
    var server = new FakeServer("M5");
    var sink = new FakeSink();
    var (backfill, warnings, _) = Make(sink, TuesdayNoon);
    var report = await backfill.RunAsync("XAU", "M5", startup: true, server.Fetch, CancellationToken.None);
    Assert.Equal(2000, report.Written);
    Assert.Empty(warnings);
    var earliest = DateTimeOffset.FromUnixTimeSeconds(report.EarliestTs!.Value);
    Assert.True(earliest < new DateTimeOffset(2026, 11, 20, 22, 0, 0, TimeSpan.Zero), $"earliest {earliest:O}");
  }

  [Fact]
  public async Task FullPagesAreFollowedFromTheirEarliestBarUntilTheWindowIsFull()
  {
    // 1.4.4 has no hasMore: a page as long as the requested count means "more", so the next request continues just
    // before its earliest bar, and no bar is lost or duplicated between pages.
    // the server caps pages at 300 bars, below the requested count: the walk must still lose nothing
    var server = new FakeServer("M15", pageLimit: 300);
    var sink = new FakeSink();
    var (backfill, _, delays) = Make(sink, TuesdayNoon);
    var report = await backfill.RunAsync("XAU", "M15", startup: true, server.Fetch, CancellationToken.None);
    Assert.Equal(2000, report.Written);
    Assert.True(report.Requests >= 7, $"requests {report.Requests}");
    var expected = FakeServer.BarTimes("M15", TuesdayNoon.AddDays(-60), TuesdayNoon)
      .Where(ts => ts + 900 <= TuesdayNoon.ToUnixTimeSeconds()).TakeLast(2000);
    Assert.Equal(expected, sink.Bars.Keys);
    for (var i = 1; i < server.Requests.Count; i++)
    {
      Assert.True(server.Requests[i].To < server.Requests[i - 1].To);
    }
    // historical requests are spaced (5 per second per connection)
    Assert.Equal(report.Requests - 1, delays.Count);
    Assert.All(delays, wait => Assert.Equal(TrendbarBackfill.MinRequestSpacing, wait));
  }

  [Fact]
  public async Task StopsAtMaxRequestsAndWarns()
  {
    var server = new FakeServer("M1", pageLimit: 100);
    var sink = new FakeSink();
    var (backfill, warnings, _) = Make(sink, TuesdayNoon, maxRequests: 3);
    var report = await backfill.RunAsync("XAU", "M1", startup: true, server.Fetch, CancellationToken.None);
    Assert.Equal(3, server.Requests.Count);
    Assert.Equal(299, report.Written); // the first page's forming bar is dropped
    Assert.Equal(BackfillStop.MaxRequests, report.Stop);
    Assert.True(report.Short);
    Assert.Contains(warnings, w => w.StartsWith("backfill_short XAU M1 written=299 target=2000"));
    Assert.True(sink.Reports.Single().Short);
  }

  [Fact]
  public async Task TwoEmptyWindowsMeanNoMoreHistory()
  {
    var server = new FakeServer("M1", historyStart: TuesdayNoon.AddHours(-6));
    var sink = new FakeSink();
    var (backfill, warnings, _) = Make(sink, TuesdayNoon);
    var report = await backfill.RunAsync("XAU", "M1", startup: true, server.Fetch, CancellationToken.None);
    Assert.Equal(BackfillStop.NoHistory, report.Stop);
    Assert.Equal(3, report.Requests); // the window with data, then two empty 4-day windows
    Assert.Equal(359, report.Written); // 6 hours of M1 minus the forming bar
    Assert.Contains(warnings, w => w.StartsWith("backfill_short XAU M1 written=359 target=2000"));
  }

  [Fact]
  public async Task ReconnectFillsOnlyTheGapAndPublishesEachBar()
  {
    var lastSeen = TuesdayNoon.AddMinutes(-30);
    var sink = new FakeSink();
    sink.Preload("M1", lastSeen, 2000);
    var latestBefore = sink.Bars.Keys.Max();
    var server = new FakeServer("M1");
    var (backfill, warnings, _) = Make(sink, TuesdayNoon);
    var report = await backfill.RunAsync("XAU", "M1", startup: false, server.Fetch, CancellationToken.None);
    Assert.Equal("reconnect-gap", report.Mode);
    Assert.Equal(BackfillStop.GapFilled, report.Stop);
    Assert.Equal(1, report.Requests);
    Assert.Equal(30, report.Written); // 11:30 .. 11:59 closed; 12:00 is forming
    Assert.All(sink.Writes, write => Assert.True(write.Publish && write.Ts > latestBefore));
    AssertStrictlyChronological(sink.Writes.Select(write => write.Ts));
    Assert.Empty(warnings);
    Assert.Equal(latestBefore + 60, server.Requests.Single().From.ToUnixTimeSeconds());
  }

  [Fact]
  public async Task ReconnectTopsUpAShortWindowSilentlyAndPublishesOnlyTheGap()
  {
    var lastSeen = TuesdayNoon.AddMinutes(-10);
    var sink = new FakeSink();
    sink.Preload("M1", lastSeen, 100);
    var latestBefore = sink.Bars.Keys.Max();
    var oldestBefore = sink.Bars.Keys.Min();
    var server = new FakeServer("M1");
    var (backfill, warnings, _) = Make(sink, TuesdayNoon);
    var report = await backfill.RunAsync("XAU", "M1", startup: false, server.Fetch, CancellationToken.None);
    Assert.Equal("reconnect-topup", report.Mode);
    Assert.Equal(2000, sink.Bars.Count);
    Assert.Equal(2000, report.Held);
    Assert.Empty(warnings);
    var published = sink.Writes.Where(write => write.Publish).Select(write => write.Ts).ToList();
    Assert.Equal(10, published.Count);
    Assert.All(published, ts => Assert.True(ts > latestBefore));
    var silent = sink.Writes.Where(write => !write.Publish).Select(write => write.Ts).ToList();
    Assert.Equal(1890, silent.Count); // 2,000 - 10 gap bars - 100 already stored
    Assert.All(silent, ts => Assert.True(ts < oldestBefore)); // stored bars are not rewritten
    AssertStrictlyChronological(sink.Writes.Select(write => write.Ts));
  }

  [Theory]
  [InlineData("M1")]
  [InlineData("M5")]
  [InlineData("M15")]
  [InlineData("M30")]
  [InlineData("H1")]
  public void HistoryWindowIsWiderThanAWeekendAndBelowThePageCap(string timeframe)
  {
    var window = TimeframeCodec.HistoryWindow(timeframe);
    Assert.True(window >= TimeSpan.FromDays(4));
    Assert.True(window <= TimeSpan.FromDays(35));
    Assert.True(window.TotalSeconds / TimeframeCodec.ToSeconds(timeframe) <= TrendbarBackfill.MaxBarsPerRequest);
  }
}

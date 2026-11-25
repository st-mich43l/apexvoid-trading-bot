using System.Globalization;

namespace ApexVoid.CTraderFeed;

/// <summary>One historical trendbar request: [from, to], at most <c>count</c> bars back from <c>to</c> (0 = no limit).</summary>
public delegate Task<IReadOnlyList<RawTrendbar>> TrendbarFetch(
  DateTimeOffset from,
  DateTimeOffset to,
  int count,
  CancellationToken cancellationToken
);

/// <summary>What one timeframe's backfill did; logged and written to <c>feed:backfill:{SYMBOL}:{TF}</c>.</summary>
public sealed record BackfillReport(
  string Symbol,
  string Timeframe,
  string Mode,
  int Target,
  int Received,
  int Written,
  int Held,
  long? EarliestTs,
  long? LatestTs,
  int Requests,
  string Stop,
  long At
)
{
  public bool Short => Stop == BackfillStop.MaxRequests || Held < Target;
}

public static class BackfillStop
{
  public const string Target = "target";
  public const string GapFilled = "gap_filled";
  public const string MaxRequests = "max_requests";
  public const string NoHistory = "no_history";
}

/// <summary>
/// PR-FEED1: backfill by BAR COUNT, not calendar time. Steps back window by window (TimeframeCodec.HistoryWindow) from
/// now until the target number of closed bars is collected, so a Monday-morning restart or a holiday does not leave
/// Redis with a few hours of M1. cTrader.OpenAPI.Net 1.4.4 has no ProtoOAGetTrendbarsRes.hasMore: a response that is
/// as long as the requested count is treated as a page, and the next request continues from its earliest bar.
/// </summary>
public sealed class TrendbarBackfill(
  int backfillBars,
  int barsWindowMax,
  int maxRequestsPerTimeframe,
  IBarSink sink,
  Func<DateTimeOffset> clock,
  Func<TimeSpan, CancellationToken, Task> delay,
  Action<string> log,
  Action<string> warn
)
{
  /// <summary>Spotware: at most 5 historical-data requests per second per connection.</summary>
  public static readonly TimeSpan MinRequestSpacing = TimeSpan.FromMilliseconds(250);

  /// <summary>Upper bound on one request's count (the largest window, M1 over 4 days, is 5,760 bars).</summary>
  public const int MaxBarsPerRequest = 14_000;

  private DateTimeOffset? _lastRequestAt;

  /// <summary>Bars a timeframe should hold after backfill: the configured count, never more than Redis keeps.</summary>
  public int WindowTarget => Math.Min(backfillBars, barsWindowMax);

  /// <summary>
  /// Startup (<paramref name="startup"/> true): collect the newest <c>backfill_bars</c> closed bars and write them
  /// silently. Reconnect: fetch the gap after the last stored bar and publish each bar; when Redis then holds fewer than
  /// the window, collect the full window and write the missing older bars silently.
  /// </summary>
  public async Task<BackfillReport> RunAsync(
    string redisSymbol,
    string timeframe,
    bool startup,
    TrendbarFetch fetch,
    CancellationToken cancellationToken
  )
  {
    var now = clock();
    var seconds = TimeframeCodec.ToSeconds(timeframe);
    var target = WindowTarget;
    var latest = startup
      ? null
      : await sink.GetLatestTimestampAsync(redisSymbol, timeframe, cancellationToken);

    if (latest is null)
    {
      // Startup, or a reconnect with an empty series: full window. Startup stays silent (Redis is the source of truth
      // and consumers read the ZSET); a reconnect into an empty series publishes per bar, exactly as before.
      var collected = await CollectAsync(fetch, timeframe, now, target, floorTs: null, cancellationToken);
      var written = 0;
      foreach (var bar in collected.Bars)
      {
        await sink.WriteClosedBarAsync(redisSymbol, timeframe, bar, cancellationToken, publish: !startup);
        written++;
      }
      return await FinishAsync(
        redisSymbol, timeframe, startup ? "startup" : "reconnect-empty", target, collected, written,
        held: Math.Min(written, barsWindowMax), now, cancellationToken
      );
    }

    var stored = await sink.ReadLatestAsync(redisSymbol, timeframe, barsWindowMax, cancellationToken);
    var floor = latest.Value + seconds;
    if (stored.Count >= target)
    {
      // The window is full: fill only the gap after the last stored bar, paged, publishing each bar.
      var gap = await CollectAsync(fetch, timeframe, now, int.MaxValue, floor, cancellationToken);
      foreach (var bar in gap.Bars)
      {
        await sink.WriteClosedBarAsync(redisSymbol, timeframe, bar, cancellationToken, publish: true);
      }
      return await FinishAsync(
        redisSymbol, timeframe, "reconnect-gap", target, gap, gap.Bars.Count,
        held: Math.Min(stored.Count + gap.Bars.Count, barsWindowMax), now, cancellationToken
      );
    }

    // The window is short: collect the full window by count. Bars after the last stored one are the reconnect gap and
    // are published; bars missing at or before it (older history, holes) are written silently; stored bars are kept.
    var full = await CollectAsync(fetch, timeframe, now, target, floorTs: null, cancellationToken);
    var have = stored.Select(bar => bar.Timestamp).ToHashSet();
    var writes = 0;
    foreach (var bar in full.Bars)
    {
      if (bar.Timestamp > latest.Value)
      {
        await sink.WriteClosedBarAsync(redisSymbol, timeframe, bar, cancellationToken, publish: true);
        writes++;
      }
      else if (have.Add(bar.Timestamp))
      {
        await sink.WriteClosedBarAsync(redisSymbol, timeframe, bar, cancellationToken, publish: false);
        writes++;
      }
    }
    return await FinishAsync(
      redisSymbol, timeframe, "reconnect-topup", target, full, writes,
      held: Math.Min(stored.Count + writes, barsWindowMax), now, cancellationToken
    );
  }

  public sealed record Collected(IReadOnlyList<OhlcBar> Bars, int Received, int Requests, string Stop);

  /// <summary>
  /// Newest-first window walk. Stops at the first of: <paramref name="target"/> closed bars collected, the
  /// <paramref name="floorTs"/> reached (gap mode), <c>max_backfill_requests_per_tf</c> requests, or two consecutive empty
  /// windows (each wider than a weekend: no more history). Returns the newest <paramref name="target"/> bars, oldest
  /// first, without duplicates and without the forming bar.
  /// </summary>
  public async Task<Collected> CollectAsync(
    TrendbarFetch fetch,
    string timeframe,
    DateTimeOffset now,
    int target,
    long? floorTs,
    CancellationToken cancellationToken
  )
  {
    var span = TimeframeCodec.HistoryWindow(timeframe);
    var nowSeconds = now.ToUnixTimeSeconds();
    var floor = floorTs is null ? (DateTimeOffset?)null : DateTimeOffset.FromUnixTimeSeconds(floorTs.Value);
    var bars = new SortedDictionary<long, OhlcBar>();
    var to = now;
    var requests = 0;
    var received = 0;
    var emptyWindows = 0;
    string stop;
    while (true)
    {
      if (bars.Count >= target)
      {
        stop = BackfillStop.Target;
        break;
      }
      var from = to - span;
      if (from.ToUnixTimeSeconds() < 0)
      {
        from = DateTimeOffset.UnixEpoch;
      }
      if (floor is not null && from < floor.Value)
      {
        from = floor.Value;
      }
      if (from >= to)
      {
        stop = floor is null ? BackfillStop.NoHistory : BackfillStop.GapFilled;
        break;
      }
      if (requests >= maxRequestsPerTimeframe)
      {
        stop = BackfillStop.MaxRequests;
        break;
      }
      // One more than still needed: the first window can contain the forming bar, which is dropped below.
      var count = target == int.MaxValue
        ? MaxBarsPerRequest
        : (int)Math.Min(MaxBarsPerRequest, (long)target - bars.Count + 1);
      await ThrottleAsync(cancellationToken);
      var page = await fetch(from, to, count, cancellationToken);
      requests++;
      received += page.Count;
      long? earliest = null;
      foreach (var raw in page)
      {
        var bar = TrendbarDecoder.Decode(raw, 0);
        earliest = earliest is null ? bar.Timestamp : Math.Min(earliest.Value, bar.Timestamp);
        if (bar.CloseTimestamp(timeframe) > nowSeconds || (floorTs is not null && bar.Timestamp < floorTs.Value))
        {
          continue;
        }
        bars[bar.Timestamp] = bar;
      }
      if (earliest is null)
      {
        emptyWindows++;
        to = from;
        if (floor is null && emptyWindows >= 2)
        {
          stop = BackfillStop.NoHistory;
          break;
        }
        continue;
      }
      emptyWindows = 0;
      // Always continue just before the earliest bar returned. 1.4.4 has no hasMore, and the server may cap a page
      // below the requested count (its chunk size is undocumented), so a short page does not prove the window is done.
      // An exhausted window costs nothing extra: the next request simply covers it and the span before it.
      var next = DateTimeOffset.FromUnixTimeSeconds(earliest.Value - 1);
      to = next < to ? next : from;
    }
    var ordered = bars.Values.ToList();
    if (target != int.MaxValue && ordered.Count > target)
    {
      ordered = ordered.GetRange(ordered.Count - target, target);
    }
    return new Collected(ordered, received, requests, stop);
  }

  private async Task<BackfillReport> FinishAsync(
    string symbol,
    string timeframe,
    string mode,
    int target,
    Collected collected,
    int written,
    int held,
    DateTimeOffset now,
    CancellationToken cancellationToken
  )
  {
    var report = new BackfillReport(
      symbol.ToUpperInvariant(),
      timeframe.ToUpperInvariant(),
      mode,
      target,
      collected.Received,
      written,
      held,
      collected.Bars.Count == 0 ? null : collected.Bars[0].Timestamp,
      collected.Bars.Count == 0 ? null : collected.Bars[^1].Timestamp,
      collected.Requests,
      collected.Stop,
      now.ToUnixTimeSeconds()
    );
    log(
      $"backfill {report.Symbol} {report.Timeframe} mode={mode} target={target} requests={report.Requests} "
      + $"received={report.Received} written={written} held={held} "
      + $"earliest={Format(report.EarliestTs)} latest={Format(report.LatestTs)} stop={report.Stop}"
    );
    if (report.Short)
    {
      warn(
        $"backfill_short {report.Symbol} {report.Timeframe} written={written} target={target} "
        + $"held={held} stop={report.Stop} requests={report.Requests}"
      );
    }
    try
    {
      await sink.WriteBackfillStatusAsync(report, cancellationToken);
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      // Observability only: a failed status write never fails the backfill.
      warn($"backfill status write failed {report.Symbol} {report.Timeframe}: {exception.GetType().Name}: {exception.Message}");
    }
    return report;
  }

  private async Task ThrottleAsync(CancellationToken cancellationToken)
  {
    var now = clock();
    if (_lastRequestAt is { } last && now - last < MinRequestSpacing)
    {
      await delay(MinRequestSpacing - (now - last), cancellationToken);
    }
    _lastRequestAt = clock();
  }

  private static string Format(long? ts) =>
    ts is null ? "-" : DateTimeOffset.FromUnixTimeSeconds(ts.Value).ToString("O", CultureInfo.InvariantCulture);
}

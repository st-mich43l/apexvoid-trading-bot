using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class RedisBarSinkTests
{
  [Fact]
  public async Task WriteClosedBarIsIdempotentPublishesAndKeepsNewestWindow()
  {
    var redis = new InMemoryRedisSeriesCommands();
    var sink = new RedisBarSink(redis, windowMax: 2, channel: "bars:new");

    await sink.WriteClosedBarAsync("XAU", "M5", Bar(100, 4100), CancellationToken.None);
    await sink.WriteClosedBarAsync("XAU", "M5", Bar(200, 4101), CancellationToken.None);
    await sink.WriteClosedBarAsync("XAU", "M5", Bar(200, 4102), CancellationToken.None);
    await sink.WriteClosedBarAsync("XAU", "M5", Bar(300, 4103), CancellationToken.None);

    Assert.Equal(300, await sink.GetLatestTimestampAsync("XAU", "M5", CancellationToken.None));
    var latest = await sink.ReadLatestAsync("XAU", "M5", 2, CancellationToken.None);

    Assert.Equal(new long[] { 300, 200 }, latest.Select(bar => bar.Timestamp));
    Assert.Equal(4103m, latest[0].Close);
    Assert.Equal(4102m, latest[1].Close);
    Assert.DoesNotContain(
      redis.Series[RedisBarSink.Key("XAU", "M5")],
      entry => entry.Timestamp == 100
    );
    Assert.Equal(4, redis.Published.Count);
    Assert.Equal(("bars:new", "XAU:M5:300"), redis.Published[^1]);
  }

  [Fact]
  public async Task WriteSpotThrottlesAndStoresPayloadShape()
  {
    var redis = new InMemoryRedisSeriesCommands();
    var sink = new RedisBarSink(redis, windowMax: 2, channel: "bars:new");

    await sink.WriteSpotAsync(new SpotPrice("XAU", 4082.10m, 4082.30m, 100), CancellationToken.None);
    await sink.WriteSpotAsync(new SpotPrice("XAU", 4082.20m, 4082.40m, 100), CancellationToken.None);
    await sink.WriteSpotAsync(new SpotPrice("XAU", 4082.50m, 4082.70m, 101), CancellationToken.None);

    Assert.Equal(2, redis.StringWrites.Count);
    Assert.Equal(
      """{"bid":4082.50,"ask":4082.70,"ts":101}""",
      redis.Strings[RedisBarSink.SpotKey("XAU")]
    );
  }

  [Fact]
  public async Task HistoricalRepairUpsertDoesNotPublishReplayEvent()
  {
    var redis = new InMemoryRedisSeriesCommands();
    var sink = new RedisBarSink(redis, windowMax: 2, channel: "bars:new");

    await sink.WriteClosedBarAsync(
      "XAU",
      "M5",
      Bar(100, 4100),
      CancellationToken.None,
      publish: false
    );

    Assert.Empty(redis.Published);
    Assert.Equal(
      4100m,
      Assert.Single(await sink.ReadLatestAsync("XAU", "M5", 1, CancellationToken.None)).Close
    );
  }

  private static OhlcBar Bar(long ts, decimal close) =>
    new(ts, close - 1, close + 1, close - 2, close, 100);
}

internal sealed class InMemoryRedisSeriesCommands : IRedisSeriesCommands
  , IRedisStringCommands
{
  public Dictionary<string, List<RedisBarEntry>> Series { get; } = [];
  public List<(string Channel, string Payload)> Published { get; } = [];
  public Dictionary<string, string> Strings { get; } = [];
  public List<(string Key, string Value)> StringWrites { get; } = [];

  public Task RemoveByScoreAsync(
    string key,
    long score,
    CancellationToken cancellationToken
  )
  {
    if (Series.TryGetValue(key, out var entries))
    {
      entries.RemoveAll(entry => entry.Timestamp == score);
    }
    return Task.CompletedTask;
  }

  public Task AddAsync(
    string key,
    string member,
    long score,
    CancellationToken cancellationToken
  )
  {
    if (!Series.TryGetValue(key, out var entries))
    {
      entries = [];
      Series[key] = entries;
    }
    entries.Add(new RedisBarEntry(score, member));
    return Task.CompletedTask;
  }

  public Task TrimToNewestAsync(
    string key,
    int keep,
    CancellationToken cancellationToken
  )
  {
    if (!Series.TryGetValue(key, out var entries))
    {
      return Task.CompletedTask;
    }
    var newest = entries
      .OrderByDescending(entry => entry.Timestamp)
      .Take(keep)
      .OrderBy(entry => entry.Timestamp)
      .ToList();
    Series[key] = newest;
    return Task.CompletedTask;
  }

  public Task PublishAsync(
    string channel,
    string payload,
    CancellationToken cancellationToken
  )
  {
    Published.Add((channel, payload));
    return Task.CompletedTask;
  }

  public Task<long?> LatestScoreAsync(string key, CancellationToken cancellationToken)
  {
    if (!Series.TryGetValue(key, out var entries) || entries.Count == 0)
    {
      return Task.FromResult<long?>(null);
    }
    return Task.FromResult<long?>(entries.Max(entry => entry.Timestamp));
  }

  public Task<IReadOnlyList<RedisBarEntry>> ReadLatestAsync(
    string key,
    int count,
    CancellationToken cancellationToken
  )
  {
    IReadOnlyList<RedisBarEntry> result = Series.TryGetValue(key, out var entries)
      ? entries.OrderByDescending(entry => entry.Timestamp).Take(count).ToArray()
      : [];
    return Task.FromResult(result);
  }

  public Task<string?> GetStringAsync(string key, CancellationToken cancellationToken)
  {
    Strings.TryGetValue(key, out var value);
    return Task.FromResult(value);
  }

  public Task SetStringAsync(
    string key,
    string value,
    CancellationToken cancellationToken
  )
  {
    Strings[key] = value;
    StringWrites.Add((key, value));
    return Task.CompletedTask;
  }
}

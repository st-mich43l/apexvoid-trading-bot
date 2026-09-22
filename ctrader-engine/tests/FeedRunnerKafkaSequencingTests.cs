using ApexVoid.CTraderFeed;
using ApexVoid.CTraderFeed.Transport.Kafka;

namespace CTraderFeed.Tests;

// Proves source task §5/§7/§8/§21/§40's sequencing rules directly against
// the real FeedRunner — not just KafkaMarketPublisher in isolation. Reuses
// ReconnectTests.cs's own RecordingSink/FakeCTraderClient/TempHeartbeat
// fakes (internal, same assembly) rather than duplicating them.
public sealed class FeedRunnerKafkaSequencingTests
{
  [Fact]
  public async Task LiveBar_PublishesToKafkaBeforeWritingToRedis()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var order = new List<string>();
    var sink = new OrderTrackingSink(order);
    var publisher = new OrderTrackingPublisher(order);
    var client = new FakeCTraderClient
    {
      // ClosedBarEmitter only emits a closed bar once a LATER live bar
      // arrives (it needs the next timestamp to know the first one
      // closed) — a single live bar never closes, matching
      // ReconnectTests.cs's own AutoTradeFaultDoesNotCancelFeedAndBarStillReachesSink
      // pattern. Bar 1_500 is what closes and gets published here.
      Live = [Raw(1_500), Raw(1_800)],
      CancelAfterLiveBars = () => cts.Cancel(),
    };
    var runner = new FeedRunner(
      TestOptions(temp.Path),
      () => client,
      sink,
      new HealthFile(temp.Path),
      _ => TimeSpan.Zero,
      marketPublisher: publisher
    );

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunOneSessionAsync(cts.Token)
    );

    Assert.Equal(["kafka", "redis"], order);
    Assert.Single(publisher.Published);
    Assert.False(publisher.Published[0].IsRecovery);
  }

  [Fact]
  public async Task LiveBar_PublishFailurePreventsTheRedisWriteForThatBar()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var sink = new RecordingSink();
    var publisher = new FailingPublisher();
    var client = new FakeCTraderClient { Live = [Raw(1_500), Raw(1_800)] };
    var runner = new FeedRunner(
      TestOptions(temp.Path),
      () => client,
      sink,
      new HealthFile(temp.Path),
      marketPublisher: publisher
    );

    // source task §16/§40: a Kafka failure must fault the session
    // (propagate) rather than being swallowed — proving the bar never
    // silently reaches Redis when Kafka rejected it.
    await Assert.ThrowsAsync<InvalidOperationException>(
      () => runner.RunOneSessionAsync(cts.Token)
    );
    Assert.Empty(sink.Writes);
    Assert.Equal(1, publisher.Attempts);
  }

  [Fact]
  public async Task StartupFullWindowBackfill_NeverCallsThePublisher()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var sink = new RecordingSink();
    var publisher = new FakeMarketEventPublisher();
    var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    var timestamp = now - (now % 300) - 600;
    var client = new FakeCTraderClient
    {
      Backfill = [Raw(timestamp)],
      CancelOnLiveStart = () => cts.Cancel(),
    };
    var runner = new FeedRunner(
      TestOptions(temp.Path) with { BackfillBars = 10 },
      () => client,
      sink,
      new HealthFile(temp.Path),
      _ => TimeSpan.Zero,
      marketPublisher: publisher
    );

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunOneSessionAsync(cts.Token)
    );

    // source task §7: "do NOT publish an entire historical window to the
    // live Kafka topic by default" — the startup full-window backfill
    // must never reach the publisher, only Redis.
    Assert.Empty(publisher.Published);
    Assert.Contains(sink.Writes, w => w.Bar.Timestamp == timestamp);
  }

  [Fact]
  public async Task ReconnectIncrementalCatchUp_PublishesToKafkaAsRecoveryAndNotifiesRedis()
  {
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var publisher = new FakeMarketEventPublisher();
    var notifyingSink = new NotifyTrackingSink();
    var first = new FakeCTraderClient { ThrowAfterLiveStart = true, Backfill = [Raw(900)] };
    var second = new FakeCTraderClient
    {
      CancelOnLiveStart = () => cts.Cancel(),
      Backfill = [Raw(1_200)],
    };
    var clients = new Queue<FakeCTraderClient>([first, second]);
    var runner = new FeedRunner(
      TestOptions(temp.Path),
      () => clients.Dequeue(),
      notifyingSink,
      new HealthFile(temp.Path),
      _ => TimeSpan.Zero,
      marketPublisher: publisher
    );

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunForeverAsync(cts.Token)
    );

    // The FIRST session's own startup backfill (bar 900) is Bootstrap
    // (fullWindow=true) — never published, never bars:new-notified. The
    // SECOND session's reconnect incremental backfill (bar 1200) is
    // RecoveryCatchUp — published to Kafka (source task §7's own "10:05,
    // 10:10... publish chronologically to Kafka" example) AND notified
    // via Redis bars:new (closing the gap for every consumer, not only
    // Kafka ones).
    Assert.Single(publisher.Published);
    Assert.Equal(1_200, publisher.Published[0].Bar.Timestamp);
    Assert.True(publisher.Published[0].IsRecovery);
    Assert.Contains(1_200L, notifyingSink.NotifiedTimestamps);
    Assert.DoesNotContain(900L, notifyingSink.NotifiedTimestamps);
  }

  [Fact]
  public async Task NullMarketPublisher_FeedRunnerStillWorksExactlyAsBefore()
  {
    // Explicit proof of the null-means-disabled default (mirrors the Go
    // side's transport.kafka.enabled=false symmetry) — a FeedRunner
    // built WITHOUT marketPublisher (every existing call site in this
    // test project) must behave identically to before this task.
    using var temp = new TempHeartbeat();
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var sink = new RecordingSink();
    var client = new FakeCTraderClient { Live = [Raw(1_500), Raw(1_800)], CancelAfterLiveBars = () => cts.Cancel() };
    var runner = new FeedRunner(
      TestOptions(temp.Path),
      () => client,
      sink,
      new HealthFile(temp.Path),
      _ => TimeSpan.Zero
      // marketPublisher omitted entirely
    );

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      () => runner.RunOneSessionAsync(cts.Token)
    );
    Assert.Contains(sink.Writes, w => w.Bar.Timestamp == 1_500);
  }

  private static FeedOptions TestOptions(string heartbeatPath) =>
    new(
      ClientId: "client",
      ClientSecret: "secret",
      AccessToken: "access",
      RefreshToken: "refresh",
      AccountId: 123,
      Host: "demo.ctraderapi.com",
      Port: 5035,
      CTraderSymbol: "XAUUSD",
      RedisSymbol: "XAU",
      Timeframes: ["M5"],
      BackfillBars: 1500,
      RedisUrl: "redis://redis:6379/0",
      BarsWindowMax: 1500,
      BarsChannel: "bars:new",
      BarQualityLookback: 6,
      HeartbeatFile: heartbeatPath,
      RefreshTokenKey: "ctrader:refresh_token",
      RefreshTokenFile: "/tmp/ctrader-token.json",
      RequestTimeout: TimeSpan.FromSeconds(1),
      TokenRefreshLead: TimeSpan.FromDays(5),
      TokenCheckInterval: TimeSpan.FromHours(6),
      ExpectedBroker: "Fusion"
    );

  private static RawTrendbar Raw(long timestamp) =>
    new(
      "M5",
      Low: 410000,
      DeltaOpen: 100,
      DeltaHigh: 200,
      DeltaClose: 150,
      Volume: 100,
      UtcTimestampInMinutes: checked((uint)(timestamp / 60))
    );
}

internal sealed class FakeMarketEventPublisher : IMarketEventPublisher
{
  public List<ClosedBarEvent> Published { get; } = [];

  public Task PublishClosedBarAsync(ClosedBarEvent bar, CancellationToken cancellationToken)
  {
    Published.Add(bar);
    return Task.CompletedTask;
  }
}

internal sealed class FailingPublisher : IMarketEventPublisher
{
  public int Attempts { get; private set; }

  public Task PublishClosedBarAsync(ClosedBarEvent bar, CancellationToken cancellationToken)
  {
    Attempts++;
    throw new InvalidOperationException("simulated broker rejection");
  }
}

// Logs "kafka"/"redis" into a shared, ordered list — proves Kafka-first
// sequencing directly (source task §5), not merely that both eventually
// happen.
internal sealed class OrderTrackingPublisher(List<string> order) : IMarketEventPublisher
{
  public List<ClosedBarEvent> Published { get; } = [];

  public Task PublishClosedBarAsync(ClosedBarEvent bar, CancellationToken cancellationToken)
  {
    order.Add("kafka");
    Published.Add(bar);
    return Task.CompletedTask;
  }
}

internal sealed class OrderTrackingSink(List<string> order) : IBarSink
{
  public Task WriteClosedBarAsync(
    string symbol, string timeframe, OhlcBar bar, CancellationToken cancellationToken, bool publish = true
  )
  {
    order.Add("redis");
    return Task.CompletedTask;
  }

  public Task<long?> GetLatestTimestampAsync(string symbol, string timeframe, CancellationToken cancellationToken) =>
    Task.FromResult<long?>(null);

  public Task<IReadOnlyList<OhlcBar>> ReadLatestAsync(string symbol, string timeframe, int count, CancellationToken cancellationToken) =>
    Task.FromResult<IReadOnlyList<OhlcBar>>([]);

  public Task WriteSpotAsync(SpotPrice spot, CancellationToken cancellationToken) => Task.CompletedTask;
}

// Tracks which bar timestamps were written with publish=true (a Redis
// bars:new notification) vs. publish=false (silent history-only write) —
// what distinguishes Bootstrap from RecoveryCatchUp at the sink layer.
internal sealed class NotifyTrackingSink : IBarSink
{
  public List<long> NotifiedTimestamps { get; } = [];
  private readonly Dictionary<(string, string), long> _latest = [];

  public Task WriteClosedBarAsync(
    string symbol, string timeframe, OhlcBar bar, CancellationToken cancellationToken, bool publish = true
  )
  {
    if (publish)
    {
      NotifiedTimestamps.Add(bar.Timestamp);
    }
    _latest[(symbol, timeframe)] = bar.Timestamp;
    return Task.CompletedTask;
  }

  public Task<long?> GetLatestTimestampAsync(string symbol, string timeframe, CancellationToken cancellationToken) =>
    Task.FromResult(_latest.TryGetValue((symbol, timeframe), out var ts) ? ts : (long?)null);

  public Task<IReadOnlyList<OhlcBar>> ReadLatestAsync(string symbol, string timeframe, int count, CancellationToken cancellationToken) =>
    Task.FromResult<IReadOnlyList<OhlcBar>>([]);

  public Task WriteSpotAsync(SpotPrice spot, CancellationToken cancellationToken) => Task.CompletedTask;
}

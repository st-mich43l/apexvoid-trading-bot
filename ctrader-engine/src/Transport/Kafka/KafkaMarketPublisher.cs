using System.Diagnostics;
using System.Text.Json;

namespace ApexVoid.CTraderFeed.Transport.Kafka;

/// <summary>
/// The event-publication boundary FeedRunner depends on (source task §4)
/// — deliberately its own small interface, not folded into
/// <see cref="IBarSink"/>: Kafka owns event publication, RedisBarSink
/// continues to own Redis bar persistence, and nothing turns into a
/// "RedisAndKafkaAndEverythingSink."
/// </summary>
public interface IMarketEventPublisher
{
  Task PublishClosedBarAsync(ClosedBarEvent bar, CancellationToken cancellationToken);
}

/// <summary>
/// Publishes <c>market.bar.closed.v1</c> via <see cref="KafkaProducer"/>.
/// Every delivery failure propagates to the caller unchanged (source
/// task §16) — FeedRunner's own Kafka-first sequencing (§5/§21) depends
/// on this: a failed publish must fault the live session so the existing
/// reconnect+incremental-backfill path naturally rediscovers and
/// republishes the missed bar (§40's "Kafka failure before Redis write"
/// case), with no bespoke retry/recovery logic needed here.
/// </summary>
public sealed class KafkaMarketPublisher(
  KafkaProducer producer,
  KafkaOptions options,
  KafkaConfigProvenance provenance,
  KafkaMetrics? metrics = null,
  KafkaHealth? health = null
) : IMarketEventPublisher, IAsyncDisposable
{
  private readonly KafkaMetrics _metrics = metrics ?? new KafkaMetrics();
  private readonly KafkaHealth _health = health ?? new KafkaHealth(options.Enabled);

  public static async Task<KafkaMarketPublisher> CreateAsync(
    KafkaOptions options,
    KafkaConfigProvenance provenance,
    KafkaMetrics? metrics,
    KafkaHealth? health,
    CancellationToken cancellationToken
  )
  {
    var producer = await KafkaProducer.CreateAsync(options, cancellationToken);
    var publisher = new KafkaMarketPublisher(producer, options, provenance, metrics, health);
    // Marked on publisher._health (the actual instance this publisher
    // will read from), not the possibly-null `health` parameter directly
    // — a caller that passed null gets a freshly-constructed KafkaHealth
    // here, which must ALSO learn it's connected/ready, or Ready() would
    // wrongly read false forever despite a real, working producer.
    publisher._health.SetConnected(true);
    publisher._health.SetProducerReady(true);
    return publisher;
  }

  /// <summary>
  /// <see cref="ClosedBarEvent.IsRecovery"/> marks a reconnect-gap-catchup
  /// publish distinctly in metrics
  /// (source task §46's <c>market_bar_recovery_publish_total</c>) — never
  /// a separate code PATH, just a label, since the actual publish
  /// behavior (Kafka-first, must-throw-on-failure) is identical for a
  /// live bar and a recovery-catchup bar; only a startup full-window
  /// bootstrap bar skips this call entirely (source task §7 —
  /// <see cref="FeedRunner"/> simply never calls this method for those).
  /// </summary>
  public async Task PublishClosedBarAsync(ClosedBarEvent bar, CancellationToken cancellationToken)
  {
    var payload = BarClosedPayload.From(bar);
    var now = DateTimeOffset.UtcNow;
    var envelope = new EventEnvelope<BarClosedPayload>(
      EventId: Uuid7.NewId(),
      EventType: options.MarketBarClosedTopic,
      EventVersion: 1,
      OccurredAt: payload.CloseTime,
      ProducedAt: now.ToUnixTimeSeconds(),
      Producer: "ctrader-engine",
      CorrelationId: bar.CorrelationId,
      CausationId: bar.CausationId,
      ConfigVersion: provenance.Version,
      ConfigFingerprint: provenance.Fingerprint,
      Payload: payload
    );
    var value = JsonSerializer.SerializeToUtf8Bytes(envelope, EventJsonContext.Default.EventEnvelopeBarClosedPayload);
    IReadOnlyList<(string, string)> headers =
    [
      ("event_type", options.MarketBarClosedTopic),
      ("event_version", "1"),
      ("content_type", "application/json"),
      ("producer", "ctrader-engine"),
    ];

    var stopwatch = Stopwatch.StartNew();
    try
    {
      await producer.ProduceAsync(options.MarketBarClosedTopic, bar.CanonicalSymbol, value, headers, cancellationToken);
    }
    catch (Exception error)
    {
      _metrics.RecordPublishError(bar.CanonicalSymbol, bar.Timeframe);
      _health.MarkError(error, now);
      throw; // source task §16: the caller (FeedRunner) must know
    }
    stopwatch.Stop();
    _metrics.RecordPublish(bar.CanonicalSymbol, bar.Timeframe, stopwatch.Elapsed, bar.IsRecovery);
    _health.MarkProduced(now);
  }

  public async Task FlushAsync(CancellationToken cancellationToken) => await producer.FlushAsync(cancellationToken);

  public async ValueTask DisposeAsync()
  {
    await producer.DisposeAsync();
    _health.SetProducerReady(false);
  }
}

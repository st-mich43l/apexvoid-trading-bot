using System.Text;
using System.Text.Json;
using ApexVoid.CTraderFeed;
using ApexVoid.CTraderFeed.Transport.Kafka;
using Dekaf;
using Dekaf.Consumer;

namespace CTraderFeed.Tests;

// Real-broker tests (source task §48/§49/§51's .NET analogue) — skip
// cleanly without KAFKA_TEST_BROKERS, so `dotnet test` never requires
// Docker. See docs/transport/kafka.md for the exact command to run these
// for real against an ephemeral Redpanda broker (the same one the Go
// side's test/kafka/ uses).
public sealed class KafkaProducerRealBrokerTests
{
  private static string[]? TestBrokers()
  {
    var raw = Environment.GetEnvironmentVariable("KAFKA_TEST_BROKERS");
    return string.IsNullOrWhiteSpace(raw) ? null : raw.Split(',');
  }

  [SkippableFact]
  public async Task KafkaMarketPublisher_ProducesCorrectTopicKeyEnvelopeAndPayload()
  {
    var brokers = TestBrokers();
    Skip.If(brokers is null, "KAFKA_TEST_BROKERS not set");

    // Auto-topic creation is deliberately disabled in every deployment. Use
    // the topic provisioned from Configuration V3 rather than manufacturing
    // a test-only topic that production would correctly reject.
    var topic = "market.bar.closed.v1";
    var options = new KafkaOptions(true, brokers!, "ctrader-engine-test", topic);
    var provenance = new KafkaConfigProvenance(3, "test-fingerprint");
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(20));

    await using var publisher = await KafkaMarketPublisher.CreateAsync(options, provenance, null, null, cts.Token);

    var bar = new OhlcBar(1_700_000_000, 2000m, 2005m, 1998m, 2003m, 120);
    var closedBar = new ClosedBarEvent("XAU", "XAUUSD", "M5", bar, "corr-real-1", "cause-1");
    await publisher.PublishClosedBarAsync(closedBar, cts.Token);
    await publisher.FlushAsync(cts.Token);

    var record = await ConsumeOneAsync(brokers!, topic, cts.Token);
    Assert.Equal(topic, record.Topic);
    Assert.Equal("XAU", record.Key);

    var envelope = JsonSerializer.Deserialize(record.Value, EventJsonContext.Default.EventEnvelopeBarClosedPayload)!;
    Assert.Equal(topic, envelope.EventType);
    Assert.Equal(1, envelope.EventVersion);
    Assert.Equal("corr-real-1", envelope.CorrelationId);
    Assert.Equal("cause-1", envelope.CausationId);
    Assert.Equal(3, envelope.ConfigVersion);
    Assert.Equal("test-fingerprint", envelope.ConfigFingerprint);
    Assert.Equal("ctrader-engine", envelope.Producer);
    Assert.NotEmpty(envelope.EventId);

    Assert.Equal("XAU", envelope.Payload.CanonicalSymbol);
    Assert.Equal("XAUUSD", envelope.Payload.BrokerSymbol);
    Assert.Equal("M5", envelope.Payload.Timeframe);
    Assert.Equal(1_700_000_000, envelope.Payload.OpenTime);
    Assert.Equal(1_700_000_300, envelope.Payload.CloseTime);
    Assert.Equal(2000m, envelope.Payload.Open);
  }

  [SkippableFact]
  public async Task KafkaMarketPublisher_PropagatesDeliveryErrorsForAnInvalidTopic()
  {
    var brokers = TestBrokers();
    Skip.If(brokers is null, "KAFKA_TEST_BROKERS not set");

    // Kafka topic names may only contain [a-zA-Z0-9._-] — forces a real
    // broker-side rejection, proving delivery errors reach the caller
    // (source task §16) rather than disappearing.
    var options = new KafkaOptions(true, brokers!, "ctrader-engine-test", "invalid topic name!!");
    var provenance = new KafkaConfigProvenance(3, "fp");
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(20));
    await using var publisher = await KafkaMarketPublisher.CreateAsync(options, provenance, null, null, cts.Token);

    var bar = new OhlcBar(100, 1m, 2m, 0.5m, 1.5m, 10);
    var closedBar = new ClosedBarEvent("XAU", null, "M5", bar, "corr-1");

    await Assert.ThrowsAnyAsync<Exception>(() => publisher.PublishClosedBarAsync(closedBar, cts.Token));
  }

  private static async Task<RecordResult> ConsumeOneAsync(string[] brokers, string topic, CancellationToken cancellationToken)
  {
    await using var consumer = await Kafka.CreateConsumer<string, byte[]>()
      .WithBootstrapServers(brokers)
      .WithGroupId($"test-group-{Guid.NewGuid()}")
      // Default is AutoOffsetReset.Latest ("new messages only") — the
      // producer call above already completed before this consumer even
      // exists, so without Earliest this would wait forever for a
      // message that already arrived.
      .WithAutoOffsetReset(AutoOffsetReset.Earliest)
      .SubscribeTo(topic)
      .BuildAsync(cancellationToken);

    await foreach (var message in consumer.ConsumeAsync(cancellationToken))
    {
      // The provisioned topic may contain records from earlier local smoke
      // runs. Select this test's correlation id rather than assuming a fresh
      // topic while auto-creation is disabled.
      if (Encoding.UTF8.GetString(message.Value).Contains("corr-real-1", StringComparison.Ordinal))
      {
        return new RecordResult(message.Key ?? "", topic, message.Value);
      }
    }
    throw new TimeoutException($"no record consumed from {topic} within the deadline");
  }

  private sealed record RecordResult(string Key, string Topic, byte[] Value);
}

using System.Text.Json;
using ApexVoid.CTraderFeed;
using ApexVoid.CTraderFeed.Transport.Kafka;

namespace CTraderFeed.Tests;

public sealed class KafkaOptionsTests
{
  private static ConfigDocument ResolveRealConfig() =>
    ConfigDocument.Resolve(RepoConfigPath("apexvoid.yml"));

  // Mirrors ConfigurationV3Tests.RepoConfigPath's exact approach.
  private static string RepoConfigPath(params string[] parts)
  {
    var root = Path.GetFullPath(
      Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..")
    );
    return Path.Combine([root, "config", .. parts]);
  }

  [Fact]
  public void FromConfig_ResolvesTheRealCheckedInConfig()
  {
    var doc = ResolveRealConfig();
    var options = KafkaOptions.FromConfig(doc);

    // Flipped true by this task — a real broker now exists in both
    // compose topologies (docs/transport/kafka.md).
    Assert.True(options.Enabled);
    Assert.NotEmpty(options.Brokers);
    Assert.Equal("apexvoid-ctrader-engine", options.ClientId);
    Assert.Equal("market.bar.closed.v1", options.MarketBarClosedTopic);
  }

  [Fact]
  public void Validate_AcceptsAWellFormedConfig()
  {
    var options = new KafkaOptions(true, ["kafka:9092"], "apexvoid-ctrader-engine", "market.bar.closed.v1");
    var exception = Record.Exception(options.Validate);
    Assert.Null(exception);
  }

  [Fact]
  public void Validate_DisabledConfigNeverValidatesFurther()
  {
    var options = new KafkaOptions(false, [], "", "");
    var exception = Record.Exception(options.Validate);
    Assert.Null(exception);
  }

  [Fact]
  public void Validate_RejectsNoBrokers()
  {
    var options = new KafkaOptions(true, [], "client", "topic");
    Assert.Throws<ConfigurationV3Error>(options.Validate);
  }

  [Fact]
  public void Validate_RejectsAnEmptyBrokerAddress()
  {
    var options = new KafkaOptions(true, ["kafka:9092", "  "], "client", "topic");
    Assert.Throws<ConfigurationV3Error>(options.Validate);
  }

  [Fact]
  public void Validate_RejectsAMissingClientId()
  {
    var options = new KafkaOptions(true, ["kafka:9092"], "", "topic");
    Assert.Throws<ConfigurationV3Error>(options.Validate);
  }

  [Fact]
  public void Validate_RejectsAnInvalidClientId()
  {
    var options = new KafkaOptions(true, ["kafka:9092"], "has a space", "topic");
    Assert.Throws<ConfigurationV3Error>(options.Validate);
  }

  [Fact]
  public void Validate_RejectsAMissingTopic()
  {
    var options = new KafkaOptions(true, ["kafka:9092"], "client", "");
    Assert.Throws<ConfigurationV3Error>(options.Validate);
  }

  [Fact]
  public void ConfigProvenance_FromConfig_ProducesADeterministicFingerprint()
  {
    var doc = ResolveRealConfig();
    var first = KafkaConfigProvenance.FromConfig(doc);
    var second = KafkaConfigProvenance.FromConfig(ResolveRealConfig());

    Assert.Equal(3, first.Version);
    Assert.NotEmpty(first.Fingerprint);
    Assert.Equal(first.Fingerprint, second.Fingerprint);
  }
}

public sealed class Uuid7Tests
{
  [Fact]
  public void NewId_IsAWellFormedUuidWithVersion7AndRfc4122Variant()
  {
    var id = Uuid7.NewId();
    var parts = id.Split('-');
    Assert.Equal(5, parts.Length);
    Assert.Equal([8, 4, 4, 4, 12], parts.Select(p => p.Length));
    Assert.Equal('7', parts[2][0]);
    Assert.Contains(parts[3][0], "89ab");
  }

  [Fact]
  public void NewId_ProducesUniqueValues()
  {
    var a = Uuid7.NewId();
    var b = Uuid7.NewId();
    Assert.NotEqual(a, b);
  }

  [Fact]
  public void NewId_SortsByTimeAcrossAMillisecondBoundary()
  {
    var first = Uuid7.NewId();
    Thread.Sleep(2);
    var second = Uuid7.NewId();
    // Same rationale as the Go side's own test (kept in sync): only
    // different-millisecond ordering is a real guarantee here — no
    // monotonic counter is implemented, and event_id is never relied on
    // for ordering by a consumer anyway (source task §10/§20).
    Assert.True(string.CompareOrdinal(second, first) >= 0);
  }
}

public sealed class BarClosedPayloadTests
{
  [Fact]
  public void From_ComputesCloseTimeViaTheSameHelperFeedRunnerAlreadyUses()
  {
    var bar = new OhlcBar(1_700_000_000, 2000m, 2005m, 1998m, 2003m, 120);
    var closedBar = new ClosedBarEvent("XAU", "XAUUSD", "M5", bar, "corr-1");

    var payload = BarClosedPayload.From(closedBar);

    Assert.Equal("XAU", payload.CanonicalSymbol);
    Assert.Equal("XAUUSD", payload.BrokerSymbol);
    Assert.Equal("M5", payload.Timeframe);
    Assert.Equal(1_700_000_000, payload.OpenTime);
    Assert.Equal(bar.CloseTimestamp("M5"), payload.CloseTime);
    Assert.Equal(1_700_000_300, payload.CloseTime); // M5 = 300s
    Assert.Equal(2000m, payload.Open);
    Assert.Equal(120m, payload.Volume);
  }

  [Fact]
  public void SerializesToSnakeCaseFieldNamesMatchingTheSharedSchema()
  {
    var bar = new OhlcBar(100, 1m, 2m, 0.5m, 1.5m, 10);
    var payload = BarClosedPayload.From(new ClosedBarEvent("XAU", null, "M5", bar, "corr-1"));

    var json = JsonSerializer.SerializeToUtf8Bytes(payload, EventJsonContext.Default.BarClosedPayload);
    using var doc = JsonDocument.Parse(json);
    var root = doc.RootElement;

    Assert.True(root.TryGetProperty("canonical_symbol", out _));
    Assert.True(root.TryGetProperty("open_time", out _));
    Assert.True(root.TryGetProperty("close_time", out _));
    Assert.True(root.TryGetProperty("high", out _));
    // BrokerSymbol was null -> DefaultIgnoreCondition.WhenWritingNull
    // means the field is omitted entirely, matching the schema's own
    // "optional: omitted when broker symbol == canonical symbol" note.
    Assert.False(root.TryGetProperty("broker_symbol", out _));
  }
}

public sealed class EventEnvelopeTests
{
  [Fact]
  public void RoundTripsThroughTheSourceGeneratedJsonContext()
  {
    var bar = new OhlcBar(100, 1m, 2m, 0.5m, 1.5m, 10);
    var payload = BarClosedPayload.From(new ClosedBarEvent("XAU", "XAUUSD", "M5", bar, "corr-1"));
    var envelope = new EventEnvelope<BarClosedPayload>(
      EventId: Uuid7.NewId(), EventType: "market.bar.closed.v1", EventVersion: 1,
      OccurredAt: 400, ProducedAt: 401, Producer: "ctrader-engine",
      CorrelationId: "corr-1", CausationId: null, ConfigVersion: 3, ConfigFingerprint: "abc",
      Payload: payload
    );

    var json = JsonSerializer.SerializeToUtf8Bytes(envelope, EventJsonContext.Default.EventEnvelopeBarClosedPayload);
    var decoded = JsonSerializer.Deserialize(json, EventJsonContext.Default.EventEnvelopeBarClosedPayload);

    Assert.NotNull(decoded);
    Assert.Equal(envelope.EventId, decoded!.EventId);
    Assert.Equal(envelope.Payload.CanonicalSymbol, decoded.Payload.CanonicalSymbol);
    Assert.Equal(envelope.ConfigFingerprint, decoded.ConfigFingerprint);
  }

  [Fact]
  public void OmitsNullCausationIdFromTheSerializedEnvelope()
  {
    var bar = new OhlcBar(100, 1m, 2m, 0.5m, 1.5m, 10);
    var payload = BarClosedPayload.From(new ClosedBarEvent("XAU", null, "M5", bar, "corr-1"));
    var envelope = new EventEnvelope<BarClosedPayload>(
      Uuid7.NewId(), "market.bar.closed.v1", 1, 400, 401, "ctrader-engine", "corr-1",
      CausationId: null, ConfigVersion: 3, ConfigFingerprint: "abc", Payload: payload
    );

    var json = JsonSerializer.SerializeToUtf8Bytes(envelope, EventJsonContext.Default.EventEnvelopeBarClosedPayload);
    using var doc = JsonDocument.Parse(json);
    Assert.False(doc.RootElement.TryGetProperty("causation_id", out _));
  }
}
